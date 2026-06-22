"""Discover a locally running Jarvis instance from its single-instance session file.

The desktop app's ``SingleInstance`` writes ``{port, token, pid}`` to
``<user-app-dir>/session.json`` while it runs (authoritative writer:
``jarvis/ui/shell/single_instance.py``). A CLI on the same machine can read it to
reach the live server with zero configuration — even when the server bound a
non-default ``admin_api_port``.

This module is intentionally decoupled from the UI shell at runtime: it only
imports ``jarvis.core.paths`` (a base-install module) to locate the per-user app
directory, and never pulls in any desktop/UI dependency. A parity test
(``tests/unit/cli_ctl/test_discovery.py``) guards the filename and directory
against drift from the authoritative writer.

Liveness: a clean shutdown removes the session file, so a leftover file means the
process crashed. ``discover()`` best-effort verifies the recorded PID is still
alive and ignores a stale file, so the CLI does not point at a recycled port.
When the PID check cannot reach a verdict it errs toward "alive" — the worst case
is then a clean ``unreachable`` error from the HTTP client, never a silent wrong
target.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Authoritative writer constant lives in jarvis/ui/shell/single_instance.py
# (SESSION_FILENAME). Kept as a local literal to avoid importing the UI layer;
# test_discovery.py asserts the two never drift.
SESSION_FILENAME = "session.json"


@dataclass(frozen=True)
class SessionInfo:
    """A discovered running instance: where it listens and how to authenticate."""

    base_url: str
    token: str | None
    pid: int | None


def session_file() -> Path | None:
    """Return the path to the running instance's session file, or None.

    A ``JARVIS_CLI_SESSION_FILE`` override pins the path directly (tests point it
    at a temp file; an empty value disables discovery). Otherwise the canonical
    per-user app directory is used; None when it cannot be resolved (e.g. the
    server package is unavailable in a minimal install)."""
    override = os.environ.get("JARVIS_CLI_SESSION_FILE")
    if override is not None:
        return Path(override) if override else None
    try:
        from jarvis.core.paths import ensure_user_dirs

        return ensure_user_dirs() / SESSION_FILENAME
    except Exception:  # pragma: no cover - defensive: never break the CLI
        return None


def _pid_alive(pid: int) -> bool:
    """Best-effort cross-platform liveness check for ``pid``.

    Errs toward True when a definitive verdict is unavailable, because the only
    cost of a false "alive" is a clean transport error downstream, whereas a
    false "dead" would needlessly discard a valid live target.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, int(pid))
            if handle:
                kernel32.CloseHandle(handle)
                return True
            # ERROR_INVALID_PARAMETER (87) => no such process; anything else
            # (e.g. ERROR_ACCESS_DENIED 5) means the process exists.
            return kernel32.GetLastError() != 87
        except Exception:  # pragma: no cover - probe must never raise
            return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def discover(*, check_pid: bool = True) -> SessionInfo | None:
    """Read the single-instance session file and return a ``SessionInfo``.

    Returns None when no readable, well-formed, live session file exists.
    """
    path = session_file()
    if path is None:
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    port = data.get("port")
    token = data.get("token")
    pid = data.get("pid")
    if not isinstance(port, int) or isinstance(port, bool) or port <= 0:
        return None
    if check_pid and isinstance(pid, int) and not _pid_alive(pid):
        return None
    return SessionInfo(
        base_url=f"http://127.0.0.1:{port}",
        token=token if isinstance(token, str) and token else None,
        pid=pid if isinstance(pid, int) else None,
    )
