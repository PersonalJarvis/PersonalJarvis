"""Process-level Codex auth-validity flag (reactive ``needs_reauth``).

``codex status`` only checks token PRESENCE, not validity, so a dead ChatGPT
OAuth session still reports ``connected=True``. When a real codex subprocess
(worker or critic) proves the token dead (HTTP 400/401 / "log in again"), we
flag it here. The rest of the session then routes codex sub-agents straight to
the Claude Max fallback — ONE path, like grok — instead of hammering the dead
provider every mission and falling back twice (worker + critic), which doubled
the Claude Max load and threw the critic into flaky ``critic_loop_exhausted``
under sustained use (forensic: 2026-06-09 codex verify run, 1/3 approved).

Cleared on a codex success (``turn.completed``) or an explicit ``codex login``,
so a re-authenticated codex runs natively again. Process-local + in-memory: the
live app and a verify run are each one process, so a module global is enough;
it resets on restart (then re-detected on the next dead-codex mission). The
file-backed + UI-surfaced version is the fuller Wave-C1 follow-up.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_needs_reauth = False


def mark_codex_needs_reauth() -> None:
    """Record that the Codex ChatGPT OAuth session is dead (proven by a 400/401)."""
    global _needs_reauth
    with _lock:
        _needs_reauth = True


def clear_codex_needs_reauth() -> None:
    """Clear the flag after a codex success or a fresh ``codex login``."""
    global _needs_reauth
    with _lock:
        _needs_reauth = False


def codex_needs_reauth() -> bool:
    """True when a codex subprocess proved the ChatGPT login dead this session."""
    with _lock:
        return _needs_reauth


__all__ = [
    "mark_codex_needs_reauth",
    "clear_codex_needs_reauth",
    "codex_needs_reauth",
]
