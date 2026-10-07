"""Process-level Codex auth-validity flag (reactive ``needs_reauth``).

``codex status`` only checks token PRESENCE, not validity, so a dead ChatGPT
OAuth session still reports ``connected=True``. When a real codex subprocess
(worker or critic) proves the token dead (HTTP 400/401 / "log in again"), we
flag it here. The rest of the session then routes codex sub-agents straight to
the Claude Max fallback — ONE path, like grok — instead of hammering the dead
provider every mission and falling back twice (worker + critic), which doubled
the Claude Max load and threw the critic into flaky ``critic_loop_exhausted``
under sustained use (forensic: 2026-06-09 codex verify run, 1/3 approved).

FINGERPRINTED, like ``claude_auth_state``: the flag remembers a hash of the
codex login file (``<CODEX_HOME>/auth.json``) that produced the failure. As
soon as that file differs — the user ran ``codex login``, or codex itself
refreshed the session — the flag no longer applies and codex re-enters
rotation, without waiting for a codex success. That matters for a mission
parked on codex (jarvis/missions/capacity.py): no codex run happens while it
waits, so a success-only clear would keep it parked until the next restart.

Also cleared on a codex success (``turn.completed``). Process-local +
in-memory: the live app and a verify run are each one process, so a module
global is enough; it resets on restart (then re-detected on the next
dead-codex mission).
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_lock = threading.Lock()
_needs_reauth = False
_dead_fingerprint: str | None = None


def codex_auth_file() -> Path:
    """The codex login file the mission workers use (``$CODEX_HOME`` or
    ``~/.codex``) — the same home :class:`jarvis.codex_auth.CodexAuthService`
    reads."""
    override = os.environ.get("CODEX_HOME")
    home = Path(override) if override and override.strip() else Path.home() / ".codex"
    return home / "auth.json"


def codex_auth_fingerprint(path: Path | None = None) -> str | None:
    """A short, non-reversible fingerprint of the codex login file, or None
    when it is absent or unreadable. Never the token itself."""
    target = path or codex_auth_file()
    try:
        data = target.read_bytes()
    except FileNotFoundError:  # no login file is a normal answer
        return None
    except OSError:
        log.debug("codex auth file unreadable for fingerprinting", exc_info=True)
        return None
    if not data:
        return None
    return hashlib.sha256(data).hexdigest()[:16]


def mark_codex_needs_reauth(*, fingerprint: str | None = None) -> None:
    """Record that the Codex ChatGPT OAuth session is dead (proven by a 400/401).

    ``fingerprint`` identifies the login that failed; by default the current
    login file. ``None`` with no readable file flags codex until a success.
    """
    global _needs_reauth, _dead_fingerprint
    fp = fingerprint if fingerprint is not None else codex_auth_fingerprint()
    with _lock:
        _needs_reauth = True
        _dead_fingerprint = fp


def clear_codex_needs_reauth() -> None:
    """Clear the flag after a codex success or a fresh ``codex login``."""
    global _needs_reauth, _dead_fingerprint
    with _lock:
        _needs_reauth = False
        _dead_fingerprint = None


def codex_needs_reauth(*, current_fingerprint: str | None = None) -> bool:
    """True when a codex subprocess proved the CURRENT ChatGPT login dead.

    A login file that changed since the failure (a fresh ``codex login``)
    lifts the flag. ``current_fingerprint`` defaults to the file on disk.
    """
    with _lock:
        if not _needs_reauth:
            return False
        dead = _dead_fingerprint
    if dead is None:
        return True
    current = (
        current_fingerprint if current_fingerprint is not None else codex_auth_fingerprint()
    )
    if current is not None and current != dead:
        clear_codex_needs_reauth()
        return False
    return True


__all__ = [
    "clear_codex_needs_reauth",
    "codex_auth_file",
    "codex_auth_fingerprint",
    "codex_needs_reauth",
    "mark_codex_needs_reauth",
]
