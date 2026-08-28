"""Ollama runtime lifecycle: detect, install, and start — without a terminal.

The local-model path was one terminal away from plug-and-go: every blocker
ended in "install Ollama and run `ollama pull ...`" (maintainer complaint
2026-08-08). This module owns the runtime itself so the cards can offer a
button instead of a command line:

- :func:`runtime_status` — the honest three-state answer ("not installed" /
  "installed but not running" / "running"), which pure HTTP probes cannot
  distinguish.
- :func:`start_server` — spawn ``ollama serve`` detached and wait for its
  port; the child's pid is recorded beside the log so :func:`stop_server`
  can later stop THAT process and nothing else.
- :func:`stop_server` / :func:`tail_log` / :func:`probe_host` /
  :func:`env_guide` — the Server panel's read-and-control surface: stop
  only what Jarvis spawned, show the log tail, test any host address, and
  hand out per-OS environment recipes as copyable text (the app never edits
  the OS environment itself).
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


#: ``0.0.0.0`` is a bind address; a client that typed it means this machine.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", ""})  # noqa: S104


def host_kind(base_url: str) -> str:
    """``"local"`` when ``base_url`` points at this machine, else ``"remote"``.

    Loopback names, any ``127.x`` address, and this machine's own hostname
    count as local; everything else is a server somewhere on the network
    whose install/start/stop/log belong to that machine.
    """
    host = (urlsplit(base_url).hostname or "").strip().lower().strip("[]")
    if host in _LOOPBACK_HOSTS or host.startswith("127."):
        return "local"
    try:
        own = socket.gethostname().strip().lower()
    except OSError:
        # Without a resolvable own hostname the only honest answer is that
        # the address is not one of the loopback spellings.
        log.debug("ollama-runtime: gethostname failed", exc_info=True)
        return "remote"
    if own and (host == own or host == f"{own}.local" or host.split(".")[0] == own):
        return "local"
    return "remote"


def _current_os() -> str:
    """``"windows"`` / ``"macos"`` / ``"linux"`` for this process."""
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


_OS_ALIASES = {
    "nt": "windows",
    "win32": "windows",
    "windows": "windows",
    "darwin": "macos",
    "mac": "macos",
    "macos": "macos",
    "linux": "linux",
    "posix": "linux",
}


def _normalize_os(os_name: str | None) -> str:
    """Map any common OS spelling to ``windows`` / ``macos`` / ``linux``.

    Empty means "this process"; an unknown name is treated as Linux, the
    family every headless server belongs to.
    """
    key = (os_name or "").strip().lower()
    if not key:
        return _current_os()
    return _OS_ALIASES.get(key, "linux")


def models_dir(os_name: str | None = None) -> Path:
    """Where Ollama keeps its weights: ``OLLAMA_MODELS`` or the OS default.

    Defaults follow the vendor: the user's ``.ollama/models`` on Windows
    (under ``%USERPROFILE%``) and macOS (under ``~``); on Linux the service
    user's ``/usr/share/ollama/.ollama/models`` when that directory exists,
    else the current user's ``~/.ollama/models``. Pure pathlib — nothing is
    created.
    """
    env_dir = (os.environ.get("OLLAMA_MODELS") or "").strip()
    if env_dir:
        return Path(env_dir).expanduser()
    which = _normalize_os(os_name)
    if which == "linux":
        service_dir = Path("/usr/share/ollama/.ollama/models")
        try:
            if service_dir.is_dir():
                return service_dir
        except OSError:
            # An unreadable mount means "not this one"; fall through.
            log.debug("ollama-runtime: service models dir unreadable", exc_info=True)
    return Path.home() / ".ollama" / "models"


def _owned_process_alive() -> bool:
    """Whether the ``ollama serve`` THIS install spawned is still a live Ollama.

    The pid-reuse guard :func:`stop_server` uses, without the stopping: a
    recorded pid the OS has handed to some other program is not ours.
    """
    pid = _recorded_pid()
    if pid is None:
        return False
    import psutil  # lazy (AP-26)

    try:
        return _process_is_ollama(psutil.Process(pid))
    except psutil.Error:
        return False


def runtime_status() -> dict[str, object]:
    """The honest runtime picture.

    ``{installed, binary, running, starting, version, detail, base_url,
    host_kind, models_dir}``. A pure HTTP probe cannot tell "not installed"
    from "installed but stopped" — and those two states need OPPOSITE
    buttons (install vs start), so the distinction is the whole point of
    this function. ``host_kind`` tells the panel whether install/start/stop/
    log even apply here: a remote server is managed on its own machine.

    ``starting`` is the fourth state, and it is not cosmetic: a spawned
    server takes seconds to answer its first request, and reporting that
    window as "stopped" told the user their click did nothing — so they
    clicked again, or read a model as ready that was still loading
    (BUG-204). Our own process being alive while the port stays quiet IS
    the boot window; a server someone else started never shows it here.
    """
    binary = find_binary()
    version = _server_version()
    running = version is not None
    installed = bool(binary) or running
    starting = not running and _owned_process_alive()
    if running:
        detail = f"Ollama is running (version {version})."
    elif starting:
        detail = "Ollama is starting — it does not answer yet."
    elif installed:
        detail = "Ollama is installed but not running."
    else:
        detail = "Ollama is not installed on this machine."
    base_url = _server_root()
    return {
        "installed": installed,
        "binary": binary,
        "running": running,
        "starting": starting,
        "version": version or "",
        "detail": detail,
        "base_url": base_url,
        "host_kind": host_kind(base_url),
        "models_dir": str(models_dir()),
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


def _log_path() -> Path:
    return _data_dir() / "ollama_server.log"


def _pid_file() -> Path:
    return _data_dir() / "ollama_server.pid"


def _record_pid(pid: int, binary: str) -> None:
    """Remember which process Jarvis spawned so ``stop_server`` stops only that."""
    try:
        _pid_file().parent.mkdir(parents=True, exist_ok=True)
        _pid_file().write_text(
            json.dumps({"pid": int(pid), "binary": binary, "at": time.time()}),
            encoding="utf-8",
        )
    except OSError:
        # Bookkeeping only: the server still runs, stop just loses its handle.
        log.warning("ollama-runtime: could not record the server pid", exc_info=True)


def _recorded_pid() -> int | None:
    """The pid Jarvis spawned, or ``None`` when no record exists."""
    try:
        raw = _pid_file().read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        log.debug("ollama-runtime: pid file unreadable", exc_info=True)
        return None
    try:
        pid = int(json.loads(raw).get("pid", 0))
    except (ValueError, AttributeError, TypeError):
        log.debug("ollama-runtime: pid file malformed", exc_info=True)
        return None
    return pid or None


def _forget_pid() -> None:
    try:
        _pid_file().unlink()
    except FileNotFoundError:
        return
    except OSError:
        log.debug("ollama-runtime: pid file not removed", exc_info=True)


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
        log_path = _log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        sink = open(log_path, "ab")  # noqa: SIM115 — handed to the child
    except OSError:
        log.debug("ollama-runtime: server log unavailable", exc_info=True)
    popen_kwargs: dict[str, object] = {}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    try:
        child = subprocess.Popen(  # noqa: S603 — fixed argv, resolved binary
            [binary, "serve"],
            stdin=subprocess.DEVNULL,
            stdout=sink or subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            creationflags=NO_WINDOW_CREATIONFLAGS,
            **popen_kwargs,  # type: ignore[arg-type, call-overload]
        )
    except OSError as exc:
        # Not swallowed: the reason travels back as this function's own
        # return value and the card renders it verbatim.
        return False, f"Could not start Ollama ({exc})."
    finally:
        if sink is not None:
            sink.close()
    _record_pid(child.pid, binary)
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


# ── Stop / log / probe / environment guide ───────────────────────────────

_STOP_WAIT_S = 8.0
_PROBE_TIMEOUT_S = 3.0

_NOT_OURS = (
    "This Ollama was not started by Jarvis, so Jarvis will not stop it — "
    "stop it where you started it (the Ollama tray app, your terminal, or "
    "the system service)."
)


def _process_is_ollama(proc: object) -> bool:
    """True when ``proc`` still is an Ollama binary (guards pid reuse)."""
    try:
        name = str(proc.name() or "").lower()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — psutil raises its own family on a gone/denied process
        log.debug("ollama-runtime: process name unavailable", exc_info=True)
        return False
    return "ollama" in name


def stop_server() -> tuple[bool, str]:
    """Stop the ``ollama serve`` Jarvis itself spawned. ``(ok, detail)``.

    Only the recorded pid is ever touched: a server the user started from
    the tray app, a terminal, or systemd gets one honest sentence instead of
    a silent no-op or a foreign kill. A recorded pid that already died is
    forgotten so the next answer is honest again.
    """
    pid = _recorded_pid()
    if pid is None:
        return False, _NOT_OURS
    import psutil  # lazy (AP-26)

    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        _forget_pid()
        return False, (
            f"The Ollama that Jarvis started (pid {pid}) is no longer running. "
            "If a server is still answering, it was started elsewhere."
        )
    except psutil.Error as exc:
        return False, f"Could not inspect the Ollama process Jarvis started ({exc})."
    if not _process_is_ollama(proc):
        # The pid was recycled by the OS for an unrelated program.
        _forget_pid()
        return False, (
            f"Pid {pid} no longer belongs to Ollama; the server Jarvis started "
            "is gone. If a server is still answering, it was started elsewhere."
        )
    try:
        proc.terminate()
        try:
            proc.wait(timeout=_STOP_WAIT_S)
        except psutil.TimeoutExpired:
            log.warning("ollama-runtime: pid %s ignored terminate; killing", pid)
            proc.kill()
            proc.wait(timeout=_STOP_WAIT_S)
    except psutil.NoSuchProcess:
        # It exited between the check and the signal — that IS the goal.
        log.debug("ollama-runtime: pid %s exited before the signal", pid)
    except psutil.Error as exc:
        return False, f"Could not stop Ollama (pid {pid}): {exc}."
    _forget_pid()
    return True, "Ollama stopped."


def tail_log(lines: int = 40) -> list[str]:
    """The last ``lines`` of the server log Jarvis writes; ``[]`` when none."""
    if lines <= 0:
        return []
    try:
        with open(_log_path(), "rb") as handle:
            tail = deque(handle, maxlen=lines)
    except FileNotFoundError:
        return []
    except OSError:
        log.debug("ollama-runtime: server log unreadable", exc_info=True)
        return []
    return [raw.decode("utf-8", errors="replace").rstrip("\r\n") for raw in tail]


async def probe_host(base_url: str, *, transport: object | None = None) -> dict[str, object]:
    """Ask ``base_url`` for its version: ``{ok, version, latency_ms, detail}``.

    Used before a new server address is saved, so a typo is caught by one
    sentence instead of by every role failing later. Never raises.
    ``transport`` is the test seam (an ``httpx`` transport); production
    passes nothing.
    """
    from jarvis.plugins.brain.ollama import normalize_server_root  # lazy (AP-26)

    root = normalize_server_root(base_url)
    url = f"{root}/api/version"
    if not url.startswith(("http://", "https://")):
        return {
            "ok": False,
            "version": "",
            "latency_ms": 0,
            "detail": f"{base_url!r} is not an http(s) address.",
        }
    import httpx  # lazy (AP-26)

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(
            timeout=_PROBE_TIMEOUT_S,
            transport=transport,  # type: ignore[arg-type]
        ) as client:
            response = await client.get(url)
        latency_ms = int((time.monotonic() - started) * 1000)
        response.raise_for_status()
        version = str(response.json().get("version", "") or "") or "unknown"
    except httpx.HTTPStatusError as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "ok": False,
            "version": "",
            "latency_ms": latency_ms,
            "detail": (
                f"{root} answered HTTP {exc.response.status_code} on /api/version "
                "— that is not an Ollama server."
            ),
        }
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        reason = str(exc) or exc.__class__.__name__
        return {
            "ok": False,
            "version": "",
            "latency_ms": latency_ms,
            "detail": f"No Ollama answered at {root} ({reason}).",
        }
    return {
        "ok": True,
        "version": version,
        "latency_ms": latency_ms,
        "detail": f"Ollama {version} answered in {latency_ms} ms.",
    }


#: (key, purpose, example value) — one plain sentence each. The value is an
#: example the user edits, not a setting the app applies.
_ENV_KEYS: tuple[tuple[str, str, str], ...] = (
    (
        "OLLAMA_HOST",
        "The address the server listens on; 0.0.0.0 lets other machines reach it.",
        "0.0.0.0",  # noqa: S104 — the documented value the USER pastes, not a bind here
    ),
    (
        "OLLAMA_MODELS",
        "The folder where downloaded model weights are stored.",
        "/path/to/models",
    ),
    (
        "OLLAMA_KEEP_ALIVE",
        "How long a model stays loaded in memory after its last request.",
        "30m",
    ),
    (
        "OLLAMA_NUM_PARALLEL",
        "How many requests one loaded model serves at the same time.",
        "1",
    ),
    (
        "OLLAMA_MAX_LOADED_MODELS",
        "How many models may sit in memory at once.",
        "2",
    ),
    (
        "OLLAMA_FLASH_ATTENTION",
        "Turns the faster attention kernel on (1) or off (0) on supported GPUs.",
        "1",
    ),
    (
        "OLLAMA_KV_CACHE_TYPE",
        "The precision of the context cache; q8_0 halves its memory with little loss.",
        "q8_0",
    ),
)

_RESTART_HINTS = {
    "windows": "Quit Ollama from the tray icon and start it again.",
    "macos": "Quit the Ollama menu-bar app and start it again.",
    "linux": "Run: sudo systemctl restart ollama",
}


def _env_command(which: str, key: str, value: str) -> str:
    if which == "windows":
        return f"setx {key} {value}"
    if which == "macos":
        return f"launchctl setenv {key} {value}"
    return (
        f'sudo systemctl edit ollama.service   # add under [Service]: Environment="{key}={value}"'
    )


def env_guide(os_name: str | None = None) -> list[dict[str, str]]:
    """Copyable per-OS recipes for Ollama's server environment variables.

    Each row is ``{key, purpose, command, restart}``. The app never edits the
    OS environment: the user pastes the line, then restarts the server. The
    Linux recipe targets the systemd unit the official installer creates;
    Windows ``setx`` reaches processes started afterwards; macOS
    ``launchctl setenv`` reaches apps launched after it.
    """
    which = _normalize_os(os_name)
    restart = _RESTART_HINTS[which]
    return [
        {
            "key": key,
            "purpose": purpose,
            "command": _env_command(which, key, value),
            "restart": restart,
        }
        for key, purpose, value in _ENV_KEYS
    ]


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
        thread = threading.Thread(target=_run_install, name="ollama-runtime-install", daemon=True)
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
        raise RuntimeError(f"step failed (exit {result.returncode}): {' '.join(cmd[:2])}…")


def _download(url: str, target: Path) -> None:
    """Stream an official artifact to disk (atomic: temp name + rename)."""
    if not url.startswith("https://ollama.com/"):
        raise RuntimeError(f"refusing non-official download URL: {url}")
    import httpx  # lazy (AP-26)

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_suffix(target.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=_DOWNLOAD_TIMEOUT_S) as response:
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
                return False, ("the Ollama installer finished but no binary was found")
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
                    "the installer finished but no Ollama binary was found — see the log tail above"
                )
        _set("starting", 85, "starting Ollama")
        ok, detail = start_server()
        if not ok:
            raise RuntimeError(detail)
        _set("done", 100, "Ollama is installed and running")
        log.info("ollama-runtime: install completed")
    except Exception as exc:  # noqa: BLE001 — every failure must land in the state
        _fail(str(exc))
