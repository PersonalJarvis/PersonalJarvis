"""Managed browser installer with a real render check and atomic readiness."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)
BROWSER_USE_VERSION = "0.13.10"
PLAYWRIGHT_VERSION = "1.62.0"
PROTOCOL_VERSION = 2
_LOCK = threading.RLock()
_STATES: dict[str, dict[str, Any]] = {}
_THREADS: dict[str, threading.Thread] = {}


def install_root(data_dir: Path | None = None) -> Path:
    if data_dir is None:
        from jarvis.core.config import DATA_DIR

        data_dir = DATA_DIR
    return (Path(data_dir) / "society" / "browser").resolve()


def runner_path() -> Path:
    return Path(__file__).with_name("live_runner.py")


def requirements_path() -> Path:
    return Path(__file__).resolve().parents[2] / "assets" / "browser" / "requirements.lock"


def managed_python_request(system: str, machine: str) -> str:
    # Windows supports x64 emulation on ARM. Some crypto dependencies have no
    # Windows ARM wheels, so keep that ABI boundary in the isolated helper.
    if system == "win32" and machine.lower() in {"arm64", "aarch64"}:
        return "cpython-3.12-windows-x86_64-none"
    return "3.12"


def _manifest(data_dir: Path | None = None) -> dict[str, Any]:
    try:
        row = json.loads((install_root(data_dir) / "installed.json").read_text("utf-8"))
        return row if isinstance(row, dict) else {}
    except (OSError, ValueError):
        # Missing or incomplete manifests mean not installed and trigger repair.
        return {}


def _path(data_dir: Path | None, field: str, default: str) -> Path:
    root = install_root(data_dir)
    path = (root / str(_manifest(data_dir).get(field, default))).resolve()
    return path if path.is_relative_to(root) else root / default


def venv_python(data_dir: Path | None = None) -> Path:
    return _path(data_dir, "runtime", "venv") / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )


def browser_executable(data_dir: Path | None = None) -> Path:
    return _path(data_dir, "executable", "missing-browser")


def _lock_digests() -> tuple[str, str]:
    """Dependency identity is independent of Git's platform line endings."""
    content = requirements_path().read_bytes().replace(b"\r\n", b"\n")
    return (
        hashlib.sha256(content).hexdigest(),
        hashlib.sha256(content.replace(b"\n", b"\r\n")).hexdigest(),
    )


def is_installed(data_dir: Path | None = None) -> bool:
    row = _manifest(data_dir)
    try:
        digests = _lock_digests()
    except OSError:
        # A missing lock cannot attest a usable installation.
        return False
    return bool(
        row.get("browser_use") == BROWSER_USE_VERSION
        and row.get("playwright") == PLAYWRIGHT_VERSION
        and row.get("protocol") == PROTOCOL_VERSION
        and row.get("verified")
        and row.get("lock_sha256") in digests
        and venv_python(data_dir).is_file()
        and browser_executable(data_dir).is_file()
    )


def worker_env(data_dir: Path | None = None, *, for_installer: bool = False) -> dict[str, str]:
    # Inference credentials stay in the parent. The browser and its children
    # do not need account tokens, SSH agents or package-index credentials.
    inherited = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
        "LD_LIBRARY_PATH",
        "DYLD_LIBRARY_PATH",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
    env = {name: value for name, value in os.environ.items() if name.upper() in inherited}
    if for_installer:
        env.update(
            {
                name: value
                for name, value in os.environ.items()
                if name.upper().startswith(("PIP_", "UV_"))
                or name.upper() in {"HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY"}
            }
        )
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHON_DOTENV_DISABLED": "1",
            "ANONYMIZED_TELEMETRY": "false",
            "BROWSER_USE_DISABLE_EXTENSIONS": "1",
            "BROWSER_USE_LOGGING_LEVEL": "error",
            "BROWSER_USE_SETUP_LOGGING": "false",
            "PLAYWRIGHT_BROWSERS_PATH": str(install_root(data_dir) / "browsers"),
        }
    )
    return env


def _set(data_dir: Path | None, **values: Any) -> None:
    with _LOCK:
        _STATES.setdefault(str(install_root(data_dir)), {}).update(values)


def snapshot(data_dir: Path | None = None) -> dict[str, Any]:
    root = install_root(data_dir)
    with _LOCK:
        row = dict(_STATES.get(str(root), {}))
        thread = _THREADS.get(str(root))
    return {
        "installed": is_installed(data_dir),
        "phase": row.get("phase", "idle"),
        "percent": row.get("percent", 0),
        "detail": row.get("detail", ""),
        "error": row.get("error", ""),
        "running": bool(thread and thread.is_alive()),
        "browser_use": BROWSER_USE_VERSION,
        "root": str(root),
        "log_tail": [],
        "retry_at": row.get("retry_at", 0),
    }


def _run(cmd: list[str], *, env: dict[str, str], timeout: float = 900) -> str:
    from jarvis.core.process_tree import make_process_tree

    tree = make_process_tree("browser-install")
    process_options: dict[str, Any] = {"start_new_session": True} if os.name != "nt" else {}
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=NO_WINDOW_CREATIONFLAGS,
        **process_options,
    )
    try:
        tree.assign(proc.pid)
        output, _ = proc.communicate(timeout=timeout)
        if proc.returncode:
            raise RuntimeError(f"Browser setup failed: {output[-1800:]}")
        return output
    finally:
        tree.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def ensure_installed(
    data_dir: Path | None = None, *, repair: bool = False, system_dependencies: bool = False
) -> dict[str, Any]:
    """Blocking installer for background threads and the normal installer."""
    from filelock import FileLock

    root = install_root(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(str(root / "install.lock"), timeout=960):
        if is_installed(data_dir) and not repair:
            return snapshot(data_dir)
        env = worker_env(data_dir, for_installer=True)
        runtime = root / "runtimes" / uuid.uuid4().hex
        runtime.parent.mkdir(exist_ok=True)
        _set(data_dir, phase="installing", percent=5, error="", detail="Preparing browser runtime")
        try:
            request = managed_python_request(sys.platform, platform.machine())
            if (
                not getattr(sys, "frozen", False)
                and (3, 11) <= sys.version_info[:2] < (3, 14)
                and request == "3.12"
            ):
                _run([sys.executable, "-m", "venv", str(runtime)], env=env)
            else:
                from .bootstrap import ensure_uv

                uv = ensure_uv(root / "bootstrap")
                _run([uv, "venv", "--python", request, str(runtime)], env=env)
            python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            _run([str(python), "-m", "ensurepip", "--upgrade"], env=env)
            # ensurepip may seed a pip whose marker parser treats kernel
            # releases as PEP-440 versions ("2025Server", "...-azure").
            _run(
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--upgrade",
                    "--require-hashes",
                    "-r",
                    str(requirements_path().with_name("bootstrap.lock")),
                ],
                env=env,
            )
            _set(data_dir, percent=20, detail="Installing Browser-Use and browser components")
            _run(
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--require-hashes",
                    "-r",
                    str(requirements_path()),
                ],
                env=env,
            )
            _set(data_dir, percent=60, detail="Downloading the managed browser")
            if system_dependencies and sys.platform.startswith("linux"):
                _run([str(python), "-m", "playwright", "install-deps", "chromium"], env=env)
            _run([str(python), "-m", "playwright", "install", "chromium", "--no-shell"], env=env)
            _set(
                data_dir,
                phase="verifying",
                percent=90,
                detail="Checking browser rendering and input",
            )
            output = _run(
                [str(python), str(runner_path()), "--probe"],
                env=worker_env(data_dir),
                timeout=120,
            )
            lines = [
                json.loads(line)
                for line in output.splitlines()
                if line.startswith('{"kind": "probe"')
            ]
            if not lines or not lines[-1].get("ok"):
                raise RuntimeError(f"Browser render check failed: {output[-1500:]}")
            probe = lines[-1]
            executable = Path(probe["executable"]).resolve()
            if not executable.is_relative_to(root):
                raise RuntimeError("Browser verification used an unmanaged executable")
            manifest = {
                "browser_use": BROWSER_USE_VERSION,
                "playwright": PLAYWRIGHT_VERSION,
                "protocol": PROTOCOL_VERSION,
                "runtime": runtime.relative_to(root).as_posix(),
                "executable": executable.relative_to(root).as_posix(),
                "verified": True,
                "lock_sha256": _lock_digests()[0],
                "verified_at": time.time(),
                "browser_version": probe["version"],
                "packages": probe.get("packages", []),
            }
            temporary = root / f"installed-{uuid.uuid4().hex}.json"
            temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            os.replace(temporary, root / "installed.json")
            _set(data_dir, phase="done", percent=100, detail="Browser ready", error="", retry_at=0)
        except Exception as exc:
            log.exception("Managed browser setup failed")
            _set(
                data_dir,
                phase="error",
                error=str(exc),
                detail="Browser setup needs repair",
                retry_at=time.time() + 60,
            )
            raise
    return snapshot(data_dir)


def start_install(data_dir: Path | None = None, *, repair: bool = False) -> tuple[bool, str]:
    key = str(install_root(data_dir))
    with _LOCK:
        thread = _THREADS.get(key)
        if thread and thread.is_alive():
            return False, "setup already running"
        if is_installed(data_dir) and not repair:
            return False, "browser ready"
        if _STATES.get(key, {}).get("retry_at", 0) > time.time() and not repair:
            return False, "waiting to retry browser setup"

        def work() -> None:
            try:
                ensure_installed(data_dir, repair=repair)
            except Exception:
                log.debug("Background setup failed; state carries the error", exc_info=True)

        thread = threading.Thread(target=work, name="browser-setup", daemon=True)
        _THREADS[key] = thread
        _set(data_dir, phase="checking", error="", percent=0)
        thread.start()
    return True, "setup started"


def _reset_for_tests() -> None:
    with _LOCK:
        _STATES.clear()
        _THREADS.clear()


if __name__ == "__main__":
    print(json.dumps(ensure_installed(system_dependencies="--system-deps" in sys.argv)))
