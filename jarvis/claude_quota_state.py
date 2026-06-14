"""Process-local Claude Max quota-cooldown flag (time-based, self-expiring).

Why this exists: with ``[brain.sub_jarvis].provider = claude-api`` the mission
workers run on the SAME Claude Max OAuth window as the interactive dev session.
A heavy dev session exhausts the 5-hour window ("You've hit your session limit
· resets 11:10pm"), and then every mission wastes a ~16 s Claude attempt before
the reactive codex fallback (ClaudeDirectWorker -> CodexDirectWorker) recovers.

This flag is the PROACTIVE complement: when a Claude worker proves the window
exhausted, it arms a cooldown; while armed the worker factory routes straight to
codex (a separate ChatGPT subscription that does not compete with the dev
session). Unlike ``codex_needs_reauth`` — a boolean cleared only by a manual
``codex login`` — a quota window auto-resets on a clock, so this cooldown is
TIME-based: it self-expires after ``_DEFAULT_COOLDOWN_S`` and re-probes Claude.
A Claude success clears it immediately.

Process-local + in-memory, like ``codex_auth_state``: the live app is one
process, a module global suffices, and it resets on restart (then re-detected on
the next quota-limited mission). Monotonic clock so it is immune to wall-clock
jumps; ``now_fn`` is injectable for deterministic tests.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

# A Claude Max window is 5 hours, but partial/rolling resets happen, so a full
# 5-hour lock-out would keep using codex long after Claude recovered. 20 minutes
# is the middle ground: at most one wasted Claude re-probe per ~20 min of
# continuous mission activity, and Claude is back in rotation soon after a reset.
_DEFAULT_COOLDOWN_S: float = 20 * 60.0

_lock = threading.Lock()
_cooldown_until: float = 0.0  # monotonic deadline; 0.0 == not armed


def mark_claude_quota_cooldown(
    *,
    now_fn: Callable[[], float] = time.monotonic,
    cooldown_s: float = _DEFAULT_COOLDOWN_S,
) -> None:
    """Arm the cooldown after a Claude worker proved the quota window exhausted."""
    global _cooldown_until
    with _lock:
        _cooldown_until = now_fn() + cooldown_s


def clear_claude_quota_cooldown() -> None:
    """Clear the cooldown immediately (a Claude worker just succeeded)."""
    global _cooldown_until
    with _lock:
        _cooldown_until = 0.0


def claude_in_quota_cooldown(
    *, now_fn: Callable[[], float] = time.monotonic
) -> bool:
    """True while the Claude Max window is presumed exhausted (cooldown armed)."""
    with _lock:
        return now_fn() < _cooldown_until


__all__ = [
    "clear_claude_quota_cooldown",
    "claude_in_quota_cooldown",
    "mark_claude_quota_cooldown",
]
