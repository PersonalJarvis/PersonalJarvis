"""One-click install of the managed browser environment.

Modeled on ``jarvis/realtime/local_server/install.py``: preflight → venv →
pinned package → Chromium → smoke probe → marker, in a daemon thread, with a
poll-shaped :func:`snapshot`. Never on the boot path; readiness is
:func:`is_installed` (fail-closed: the marker is written last).

Interpreter choice: browser-use declares ``>=3.11`` and no 3.14 wheel
classifier, so the venv prefers a 3.11–3.13 interpreter — ``uv`` can fetch
one — and falls back to the app's own Python only when nothing else exists.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

__all__ = [
    "BROWSER_USE_VERSION",
    "install_root",
    "is_installed",
    "runner_path",
    "snapshot",
    "start_install",
    "venv_python",
]

BROWSER_USE_VERSION: Final[str] = "0.13.8"
_MARKER: Final[str] = "installed.json"
_PREFERRED_PYTHONS: Final[tuple[str, ...]] = ("3.13", "3.12", "3.11")
_STEP_TIMEOUT_S: Final[int] = 900


def install_root(data_dir: Path | None = None) -> Path:
    if data_dir is None:
        from jarvis.core import config as core_config

        data_dir = core_config.DATA_DIR
    return Path(data_dir) / "society" / "browser"


def _venv_dir(data_dir: Path | None = None) -> Path:
    return install_root(data_dir) / "venv"


def venv_python(data_dir: Path | None = None) -> Path:
    venv = _venv_dir(data_dir)
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def runner_path() -> Path:
    return Path(__file__).with_name("runner.py")


def _marker(data_dir: Path | None = None) -> Path:
    return install_root(data_dir) / _MARKER


def is_installed(data_dir: Path | None = None) -> bool:
    marker = _marker(data_dir)
    if not marker.is_file() or not venv_python(data_dir).is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return str(payload.get("browser_use")) == BROWSER_USE_VERSION


# ------------------------------------------------------------------ state


@dataclass
class _State:
    phase: str = "idle"
    percent: int = 0
    detail: str = ""
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    log_tail: deque[str] = field(default_factory=lambda: deque(maxlen=30))
    thread: threading.Thread | None = None


_STATE = _State()
_LOCK = threading.Lock()


def _set(phase: str, percent: int, detail: str = "") -> None:
    with _LOCK:
        _STATE.phase = phase
        _STATE.percent = percent
        if detail:
            _STATE.detail = detail
            _STATE.log_tail.append(detail)


def _fail(message: str) -> None:
    log.error("society browser install: %s", message)
    with _LOCK:
        _STATE.phase = "error"
        _STATE.error = message
        _STATE.finished_at = time.time()


def _reset_for_tests() -> None:
    with _LOCK:
        _STATE.phase = "idle"
        _STATE.percent = 0
        _STATE.detail = ""
        _STATE.error = ""
        _STATE.started_at = 0.0
        _STATE.finished_at = 0.0
        _STATE.log_tail.clear()
        _STATE.thread = None


def snapshot(data_dir: Path | None = None) -> dict[str, Any]:
    with _LOCK:
        return {
            "installed": is_installed(data_dir),
            "phase": _STATE.phase,
            "percent": _STATE.percent,
            "detail": _STATE.detail,
            "error": _STATE.error,
            "running": _STATE.thread is not None and _STATE.thread.is_alive(),
            "log_tail": list(_STATE.log_tail),
            "browser_use": BROWSER_USE_VERSION,
            "root": str(install_root(data_dir)),
        }


# ------------------------------------------------------------------ steps


def _run(cmd: list[str], *, timeout: int, cwd: Path | None = None) -> None:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    popen_kwargs: dict[str, Any] = {}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd) if cwd else None,
        env=env,
        creationflags=NO_WINDOW_CREATIONFLAGS,
        **popen_kwargs,
    )

    def _pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                with _LOCK:
                    _STATE.detail = line[:200]
                    _STATE.log_tail.append(line[:200])

    pump = threading.Thread(target=_pump, name="society-browser-install-pump", daemon=True)
    pump.start()
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise TimeoutError(f"step timed out after {timeout}s: {' '.join(cmd[:3])}…") from None
    pump.join(timeout=10)
    if code != 0:
        raise RuntimeError(f"step failed (exit {code}): {' '.join(cmd[:3])}…")


def _create_venv(venv: Path) -> str:
    """Create the venv; returns a one-line description of the interpreter used."""
    uv = shutil.which("uv")
    if uv:
        for version in _PREFERRED_PYTHONS:
            try:
                _run([uv, "venv", "--python", version, str(venv)], timeout=_STEP_TIMEOUT_S)
                return f"uv venv (python {version})"
            except (RuntimeError, TimeoutError) as exc:
                log.info("society browser install: uv venv %s failed: %s", version, exc)
                continue
    if sys.version_info >= (3, 14):
        log.warning(
            "society browser install: no 3.11-3.13 interpreter found; using the app's "
            "Python %s (browser-use declares no 3.14 wheel classifier)",
            sys.version.split()[0],
        )
    _run([sys.executable, "-m", "venv", str(venv)], timeout=_STEP_TIMEOUT_S)
    return f"venv on {sys.executable}"


def _run_install(data_dir: Path | None) -> None:
    try:
        root = install_root(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        venv = _venv_dir(data_dir)
        python = venv_python(data_dir)
        _set("venv", 5, "creating the browser environment")
        if not python.is_file():
            how = _create_venv(venv)
            _set("venv", 20, how)
        _set("package", 25, f"installing browser-use {BROWSER_USE_VERSION}")
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-q",
                f"browser-use[cli]=={BROWSER_USE_VERSION}",
            ],
            timeout=_STEP_TIMEOUT_S,
        )
        _set("chromium", 60, "downloading Chromium (once per machine)")
        _install_chromium(python, venv)
        _set("probe", 90, "checking the environment")
        _probe(python)
        _marker(data_dir).write_text(
            json.dumps(
                {"browser_use": BROWSER_USE_VERSION, "python": str(python), "at": time.time()}
            ),
            encoding="utf-8",
        )
        _set("done", 100, "browser environment ready")
        with _LOCK:
            _STATE.finished_at = time.time()
    except (RuntimeError, TimeoutError, OSError) as exc:
        _fail(str(exc))


def _install_chromium(python: Path, venv: Path) -> None:
    """``browser-use install`` fetches the bundled Chromium; older layouts
    fall back to the module form. A missing CLI is not fatal: the first
    headless run downloads on demand."""
    bindir = venv / ("Scripts" if os.name == "nt" else "bin")
    cli = bindir / ("browser-use.exe" if os.name == "nt" else "browser-use")
    if cli.is_file():
        _run([str(cli), "install"], timeout=_STEP_TIMEOUT_S)
        return
    try:
        _run([str(python), "-m", "browser_use", "install"], timeout=_STEP_TIMEOUT_S)
    except RuntimeError as exc:
        log.info(
            "society browser install: no install CLI, Chromium downloads on first run: %s",
            exc,
        )


def _probe(python: Path) -> None:
    proc = subprocess.run(  # noqa: S603 — fixed argv
        [str(python), str(runner_path())],
        input='{"mode": "probe"}\n',
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        creationflags=NO_WINDOW_CREATIONFLAGS,
        check=False,
    )
    last = (proc.stdout or "").strip().splitlines()
    if not last:
        raise RuntimeError(f"probe produced no answer: {(proc.stderr or '')[-300:]}")
    try:
        payload = json.loads(last[-1])
    except ValueError as exc:
        raise RuntimeError(f"probe answered garbage: {last[-1][:200]}") from exc
    if not payload.get("ok"):
        raise RuntimeError(f"probe failed: {payload.get('error', '?')}")
    _set("probe", 95, f"browser-use {payload.get('version', '?')} answers")


def start_install(data_dir: Path | None = None) -> tuple[bool, str]:
    with _LOCK:
        if _STATE.thread is not None and _STATE.thread.is_alive():
            return False, "an install is already running"
        _STATE.phase = "preflight"
        _STATE.percent = 0
        _STATE.error = ""
        _STATE.detail = ""
        _STATE.started_at = time.time()
        _STATE.finished_at = 0.0
        thread = threading.Thread(
            target=_run_install, name="society-browser-install", args=(data_dir,), daemon=True
        )
        _STATE.thread = thread
    thread.start()
    return True, "install started"
