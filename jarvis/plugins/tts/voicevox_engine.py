"""Managed VOICEVOX Engine — the keyless Japanese neural voice.

VOICEVOX Engine is a local HTTP server (default port 50021) that turns
Japanese text into speech with free-to-use fictional characters. This module
finds an engine unpacked under ``<user_data_dir>/voicevox`` (what
``scripts/install_voicevox.py`` produces) or already listening on the port, and
starts it as a child process when it is not running. It binds 127.0.0.1 only.

Start-up is never on the boot critical path (AP-26): the TTS plugin calls
:func:`ensure_running` from a worker thread on first use, and the web server
schedules one background start when the configured voice is VOICEVOX. The
engine is its own process, so no native inference state is shared (AP-24).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from jarvis.core.paths import user_data_dir
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

DEFAULT_PORT = 50021
BASE_URL = f"http://127.0.0.1:{DEFAULT_PORT}"
START_TIMEOUT_S = 90.0

_lock = threading.Lock()
_proc: subprocess.Popen[bytes] | None = None
_log_handle: Any = None


def engine_home() -> Path:
    return user_data_dir() / "voicevox"


def find_engine() -> Path | None:
    """The engine's ``run`` executable inside the managed install, if any."""
    home = engine_home()
    if not home.is_dir():
        return None
    names = ("run.exe",) if os.name == "nt" else ("run",)
    for name in names:
        for candidate in sorted(home.rglob(name)):
            if candidate.is_file():
                return candidate
    return None


def is_up(timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"{BASE_URL}/version", timeout=timeout) as resp:  # noqa: S310
            return resp.status == 200
    except (OSError, urllib.error.URLError, ValueError):
        return False


def installed() -> bool:
    return find_engine() is not None or is_up()


def ensure_running() -> bool:
    """Start the engine if needed and wait until it answers. Blocking; off-loop only."""
    global _proc, _log_handle
    if is_up():
        return True
    with _lock:
        if is_up():
            return True
        if _proc is None or _proc.poll() is not None:
            exe = find_engine()
            if exe is None:
                log.info("VOICEVOX engine not installed under %s.", engine_home())
                return False
            threads = max(1, min(4, (os.cpu_count() or 4) // 2))
            cmd = [
                str(exe),
                "--host", "127.0.0.1",
                "--port", str(DEFAULT_PORT),
                "--cpu_num_threads", str(threads),
            ]
            _log_handle = open(engine_home() / "engine.log", "ab")  # noqa: SIM115 - lives with the process
            log.info("VOICEVOX engine: starting %s", " ".join(cmd))
            _proc = subprocess.Popen(  # noqa: S603 - fixed argv, managed binary
                cmd,
                stdout=_log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=str(exe.parent),
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            if _proc is not None and _proc.poll() is not None:
                log.warning("VOICEVOX engine exited during start (code %s).", _proc.returncode)
                return False
            if is_up():
                log.info("VOICEVOX engine ready on %s.", BASE_URL)
                return True
            time.sleep(0.5)
        log.warning("VOICEVOX engine did not answer within %.0f s.", START_TIMEOUT_S)
        return False


def shutdown() -> None:
    """Stop the engine this process started (an adopted one is left alone)."""
    global _proc, _log_handle
    proc, _proc = _proc, None
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    if _log_handle is not None:
        with contextlib.suppress(OSError):
            _log_handle.close()
        _log_handle = None


def request_json(method: str, path: str, *, body: Any = None, timeout: float = 30.0) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 - loopback only
        f"{BASE_URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def request_bytes(path: str, *, body: Any, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(  # noqa: S310 - loopback only
        f"{BASE_URL}{path}",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return bytes(resp.read())


__all__ = [
    "BASE_URL",
    "DEFAULT_PORT",
    "engine_home",
    "ensure_running",
    "find_engine",
    "installed",
    "is_up",
    "request_bytes",
    "request_json",
    "shutdown",
]
