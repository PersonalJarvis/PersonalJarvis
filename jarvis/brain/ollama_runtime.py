"""Ollama runtime lifecycle: detect, install, and start — without a terminal.

The local-model path was one terminal away from plug-and-go: every blocker
ended in "install Ollama and run `ollama pull ...`" (maintainer complaint
2026-08-08). This module owns the runtime itself so the cards can offer a
button instead of a command line:

- :func:`runtime_status` — the honest three-state answer ("not installed" /
  "installed but not running" / "running"), which pure HTTP probes cannot
  distinguish.
- :func:`start_server` — spawn ``ollama serve`` detached and wait for its
  port.
- :func:`start_install` / :func:`install_snapshot` — a poll-shaped installer
  (same skeleton as the managed realtime server install): winget or the
  official per-user installer on Windows, Homebrew on macOS, the official
  script on Linux when non-interactive sudo exists. Everything else fails
  honestly with the one action that would fix it (§3: honest degradation,
  never a hang on a hidden password prompt).

Nothing here runs without an explicit user action: the REST route that calls
:func:`start_install` is dangerous-flagged and sits behind a confirm button.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

_VERSION_TIMEOUT_S = 1.5
#: How long a freshly spawned ``ollama serve`` may take to bind its port.
_START_WAIT_S = 15.0
_START_POLL_S = 0.5

_WINGET_TIMEOUT_S = 1200
_INSTALLER_TIMEOUT_S = 900
_DOWNLOAD_TIMEOUT_S = 1800

#: Official artifacts only — never a mirror.
_WINDOWS_INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"
_LINUX_INSTALL_SCRIPT_URL = "https://ollama.com/install.sh"


# ── Detection ────────────────────────────────────────────────────────────


def _known_binaries() -> list[Path]:
    """Places the official installers put the binary, per platform."""
    candidates: list[Path] = []
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            candidates.append(Path(local) / "Programs" / "Ollama" / "ollama.exe")
    else:
        candidates.extend(
            [
                Path("/usr/local/bin/ollama"),
                Path("/opt/homebrew/bin/ollama"),
                Path("/usr/bin/ollama"),
                Path("/Applications/Ollama.app/Contents/Resources/ollama"),
            ]
        )
    return candidates


def find_binary() -> str:
    """Absolute path of the Ollama binary, or ``""`` when none exists.

    PATH first (after the well-known-dir refresh, so a binary installed a
    minute ago by this very process is visible), then the official install
    locations directly — a GUI-launched app misses registry-PATH updates.
    """
    try:
        from jarvis.core.path_augment import ensure_cli_paths  # lazy (AP-26)

        ensure_cli_paths()
    except Exception:  # noqa: BLE001 — a PATH refresh failure must not block detection
        log.debug("ollama-runtime: PATH refresh failed", exc_info=True)
    resolved = shutil.which("ollama")
    if resolved:
        return resolved
    for candidate in _known_binaries():
        try:
            if candidate.exists():
                return str(candidate)
        except OSError:  # pragma: no cover — unreadable mount
            continue
    return ""


def _server_root() -> str:
    from jarvis.brain.ollama_pull import server_root  # lazy (AP-26)

    return server_root()


def _server_version(timeout: float = _VERSION_TIMEOUT_S) -> str | None:
    """The running server's version string, or ``None`` when it is not up."""
    import urllib.error
    import urllib.request

    url = f"{_server_root()}/api/version"
    if not url.startswith(("http://", "https://")):
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            payload = json.load(resp)
    except (urllib.error.URLError, OSError, ValueError):
        # A server that is not up is the normal answer this probe asks for,
        # not a failure — "not running" is exactly what the caller renders.
        return None
    version = str(payload.get("version", "") or "")
    return version or "unknown"


def runtime_status() -> dict[str, object]:
    """The honest runtime picture: ``{installed, binary, running, version, detail}``.

    A pure HTTP probe cannot tell "not installed" from "installed but
    stopped" — and those two states need OPPOSITE buttons (install vs
    start), so the distinction is the whole point of this function.
    """
    binary = find_binary()
    version = _server_version()
    running = version is not None
    installed = bool(binary) or running
    if running:
        detail = f"Ollama is running (version {version})."
    elif installed:
        detail = "Ollama is installed but not running."
    else:
        detail = "Ollama is not installed on this machine."
    return {
        "installed": installed,
        "binary": binary,
        "running": running,
        "version": version or "",
        "detail": detail,
    }


# ── Start ────────────────────────────────────────────────────────────────


def _server_port() -> int:
    parsed = urlsplit(_server_root())
    try:
        return parsed.port or 11434
    except ValueError:
        # A malformed port in a user-typed OLLAMA_HOST falls back to the
        # vendor default rather than breaking every probe on a typo.
        return 11434


def _port_open(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        # A closed port IS the answer this probe asks for.
        return False


def _data_dir() -> Path:
    env_dir = os.environ.get("JARVIS_DATA_DIR")
    if env_dir and env_dir.strip():
        return Path(env_dir.strip()).resolve()
    from jarvis.core.config import DATA_DIR  # lazy (AP-26)

    return DATA_DIR


def _install_marker() -> Path:
    return _data_dir() / "ollama_installed_by_jarvis.json"


def start_server() -> tuple[bool, str]:
    """Spawn ``ollama serve`` detached and wait for its port. ``(ok, detail)``.

    Detached + window-less (AP-1), log into the data dir so the NEXT failure
    leaves forensics. On POSIX the child gets its own session so it survives
    the app exactly like the managed realtime server does.
    """
    if _server_version() is not None:
        return True, "Ollama is already running."
    binary = find_binary()
    if not binary:
        return False, "Ollama is not installed — install it first."
    sink = None
    try:
        log_path = _data_dir() / "ollama_server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        sink = open(log_path, "ab")  # noqa: SIM115 — handed to the child
    except OSError:
        log.debug("ollama-runtime: server log unavailable", exc_info=True)
    popen_kwargs: dict[str, object] = {}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    try:
        subprocess.Popen(  # noqa: S603 — fixed argv, resolved binary
            [binary, "serve"],
            stdin=subprocess.DEVNULL,
            stdout=sink or subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            creationflags=NO_WINDOW_CREATIONFLAGS,
            **popen_kwargs,  # type: ignore[arg-type]
        )
    except OSError as exc:
        # Not swallowed: the reason travels back as this function's own
        # return value and the card renders it verbatim.
        return False, f"Could not start Ollama ({exc})."
    finally:
        if sink is not None:
            sink.close()
    port = _server_port()
    deadline = time.monotonic() + _START_WAIT_S
    while time.monotonic() < deadline:
        if _port_open(port):
            return True, "Ollama started."
        time.sleep(_START_POLL_S)
    return False, (
        f"Ollama did not come up within {_START_WAIT_S:.0f} seconds — "
        "see ollama_server.log in the Jarvis data folder."
    )


# ── Poll-shaped installer ────────────────────────────────────────────────

_PHASES = ("idle", "downloading", "installing", "starting", "done", "error")


@dataclass
class _State:
    phase: str = "idle"
    percent: int = 0
    detail: str = ""
    error: str = ""
    log_tail: deque[str] = field(default_factory=lambda: deque(maxlen=20))
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
    log.error("ollama-runtime install: %s", message)
    with _LOCK:
        _STATE.phase = "error"
        _STATE.error = message


def _reset_for_tests() -> None:
    with _LOCK:
        _STATE.phase = "idle"
        _STATE.percent = 0
        _STATE.detail = ""
        _STATE.error = ""
        _STATE.log_tail.clear()
        _STATE.thread = None


def install_snapshot() -> dict[str, object]:
    """Poll-shaped view of the running (or last) install."""
    with _LOCK:
        return {
            "phase": _STATE.phase,
            "percent": _STATE.percent,
            "detail": _STATE.detail,
            "error": _STATE.error,
            "running": _STATE.thread is not None and _STATE.thread.is_alive(),
            "log_tail": list(_STATE.log_tail),
        }


def start_install() -> tuple[bool, str]:
    """Kick off the platform-appropriate Ollama install. Returns immediately."""
    with _LOCK:
        if _STATE.thread is not None and _STATE.thread.is_alive():
            return False, "an install is already running"
        _STATE.phase = "downloading"
        _STATE.percent = 0
        _STATE.error = ""
        _STATE.detail = ""
        thread = threading.Thread(
            target=_run_install, name="ollama-runtime-install", daemon=True
        )
        _STATE.thread = thread
    thread.start()
    return True, "install started"


def _record_marker(method: str) -> None:
    """Remember that JARVIS put Ollama here (enables a clean uninstall later)."""
    try:
        _install_marker().parent.mkdir(parents=True, exist_ok=True)
        _install_marker().write_text(
            json.dumps({"at": time.time(), "method": method}, indent=2),
            encoding="utf-8",
        )
    except OSError:  # pragma: no cover — bookkeeping only
        log.debug("ollama-runtime: marker write failed", exc_info=True)


def _run_command(cmd: list[str], *, timeout: int) -> None:
    """Run one install step; stdout tail lands in the snapshot."""
    result = subprocess.run(  # noqa: S603 — fixed argv assembled above
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    tail = (result.stdout or "") + (result.stderr or "")
    for line in tail.strip().splitlines()[-5:]:
        with _LOCK:
            _STATE.log_tail.append(line[:200])
    if result.returncode != 0:
        raise RuntimeError(
            f"step failed (exit {result.returncode}): {' '.join(cmd[:2])}…"
        )


def _download(url: str, target: Path) -> None:
    """Stream an official artifact to disk (atomic: temp name + rename)."""
    if not url.startswith("https://ollama.com/"):
        raise RuntimeError(f"refusing non-official download URL: {url}")
    import httpx  # lazy (AP-26)

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_suffix(target.suffix + ".part")
    with httpx.stream(
        "GET", url, follow_redirects=True, timeout=_DOWNLOAD_TIMEOUT_S
    ) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length", 0) or 0)
        done = 0
        with open(staging, "wb") as sink:
            for chunk in response.iter_bytes(1024 * 1024):
                sink.write(chunk)
                done += len(chunk)
                if total > 0:
                    _set(
                        "downloading",
                        min(40, int(40 * done / total)),
                        f"downloading Ollama ({done // (1024 * 1024)} MB)",
                    )
    os.replace(staging, target)


def _install_windows() -> str:
    """winget when present (per-user, no UAC), else the official installer."""
    winget = shutil.which("winget")
    if winget:
        _set("installing", 45, "installing Ollama via winget")
        _run_command(
            [
                winget,
                "install",
                "--id",
                "Ollama.Ollama",
                "--silent",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "--disable-interactivity",
            ],
            timeout=_WINGET_TIMEOUT_S,
        )
        return "winget"
    _set("downloading", 5, "downloading the official Ollama installer")
    installer = _data_dir() / "downloads" / "OllamaSetup.exe"
    _download(_WINDOWS_INSTALLER_URL, installer)
    _set("installing", 55, "running the Ollama installer (silent)")
    # Inno Setup switches; the Ollama installer is per-user, so no UAC.
    _run_command(
        [str(installer), "/VERYSILENT", "/NORESTART", "/SP-"],
        timeout=_INSTALLER_TIMEOUT_S,
    )
    return "installer-exe"


def _install_macos() -> str:
    """Homebrew is the one automatable path; a dmg drag cannot be scripted honestly."""
    brew = shutil.which("brew")
    if not brew:
        # Both default prefixes: /opt/homebrew (Apple Silicon) and /usr/local
        # (Intel). A GUI-launched app can miss either on PATH; probing only
        # the Silicon prefix told Intel Macs WITH Homebrew "No Homebrew".
        for candidate in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"):
            if Path(candidate).exists():
                brew = candidate
                break
    if not brew:
        raise RuntimeError(
            "No Homebrew on this Mac, and the Ollama.dmg needs a manual "
            "drag-install — download it from ollama.com/download, open it "
            "once, then come back here."
        )
    _set("installing", 45, "installing Ollama via Homebrew")
    _run_command([brew, "install", "ollama"], timeout=_WINGET_TIMEOUT_S)
    return "homebrew"


def _install_linux() -> str:
    """The official script — but only when sudo works WITHOUT a prompt.

    The script escalates internally; running it without usable sudo would
    hang this daemon thread on an invisible password prompt forever, which
    is worse than an honest refusal.
    """
    sudo = shutil.which("sudo")
    if sudo:
        probe = subprocess.run(  # noqa: S603 — fixed argv
            [sudo, "-n", "true"], capture_output=True, timeout=15
        )
        sudo_ok = probe.returncode == 0
    else:
        sudo_ok = os.geteuid() == 0 if hasattr(os, "geteuid") else False
    if not sudo_ok:
        raise RuntimeError(
            "Installing Ollama on Linux needs administrator rights, and "
            "passwordless sudo is not available here. Run once in a "
            "terminal: curl -fsSL https://ollama.com/install.sh | sh"
        )
    _set("downloading", 5, "downloading the official install script")
    script = _data_dir() / "downloads" / "ollama_install.sh"
    _download(_LINUX_INSTALL_SCRIPT_URL, script)
    _set("installing", 45, "running the official Ollama install script")
    shell = shutil.which("sh") or "/bin/sh"
    _run_command([shell, str(script)], timeout=_WINGET_TIMEOUT_S)
    return "install-script"


def ensure_runtime_blocking() -> tuple[bool, str]:
    """Install (when absent) and start Ollama, synchronously. ``(ok, detail)``.

    For callers that already run on their own worker thread with their own
    progress surface (the managed realtime install engine): same steps as
    the poll-shaped installer, but inline and never raising — the caller
    owns the phase reporting.
    """
    try:
        status = runtime_status()
        if status["running"]:
            return True, "Ollama is already running."
        if not status["installed"]:
            if os.name == "nt":
                method = _install_windows()
            elif sys.platform == "darwin":
                method = _install_macos()
            else:
                method = _install_linux()
            _record_marker(method)
            if not find_binary():
                return False, (
                    "the Ollama installer finished but no binary was found"
                )
        return start_server()
    except Exception as exc:  # noqa: BLE001 — honest sentence, never a raise
        return False, str(exc)


def _run_install() -> None:
    try:
        status = runtime_status()
        if status["running"]:
            _set("done", 100, "Ollama is already installed and running")
            return
        if not status["installed"]:
            if os.name == "nt":
                method = _install_windows()
            elif sys.platform == "darwin":
                method = _install_macos()
            else:
                method = _install_linux()
            _record_marker(method)
            if not find_binary():
                raise RuntimeError(
                    "the installer finished but no Ollama binary was found — "
                    "see the log tail above"
                )
        _set("starting", 85, "starting Ollama")
        ok, detail = start_server()
        if not ok:
            raise RuntimeError(detail)
        _set("done", 100, "Ollama is installed and running")
        log.info("ollama-runtime: install completed")
    except Exception as exc:  # noqa: BLE001 — every failure must land in the state
        _fail(str(exc))
