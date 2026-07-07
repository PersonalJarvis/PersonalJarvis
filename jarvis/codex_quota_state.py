"""Process-local codex (ChatGPT) quota-cooldown flag (time-based, self-expiring).

Why this exists: the codex ChatGPT plan can be usage-capped ("You've hit your
usage limit … try again at Jul 31st") while ``codex status`` still reports
connected=True — the login is fine, the plan just can't run right now. Without
a memory of that cap, the worker factory re-picked codex on EVERY mission and
retry iteration (2026-07-07 incident, mission_019f3cd8-1dd4): each spawn burned
~28 s until the cap error returned, the in-worker fallback then died on a dead
Claude login, and after three identical iterations the mission failed — while a
healthy OpenRouter key sat unused (AP-22).

This is the codex mirror of ``claude_quota_state``: when a codex worker proves
the plan capped, it arms a cooldown; while armed the worker factory and the
cross-family last resort skip codex and cross to the next reachable family.
Unlike ``codex_needs_reauth`` — a boolean cleared only by a codex success after
``codex login`` — a usage cap resets on the provider's clock, so this cooldown
is TIME-based: it self-expires after ``_DEFAULT_COOLDOWN_S`` and codex is
re-probed. A codex success clears it immediately.

Process-local + in-memory, like ``codex_auth_state``: the live app is one
process, a module global suffices, and it resets on restart (then re-detected on
the next capped mission). Monotonic clock so it is immune to wall-clock jumps;
``now_fn`` is injectable for deterministic tests.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

# Caps range from a rolling few-hour window to a hard until-next-billing-cycle
# date, and the error copy is not reliably parseable — so mirror the Claude
# cooldown's middle ground: at most one wasted codex re-probe per ~20 min of
# continuous mission activity, and codex is back in rotation soon after an
# early reset.
_DEFAULT_COOLDOWN_S: float = 20 * 60.0

_lock = threading.Lock()
_cooldown_until: float = 0.0  # monotonic deadline; 0.0 == not armed


def mark_codex_quota_cooldown(
    *,
    now_fn: Callable[[], float] = time.monotonic,
    cooldown_s: float = _DEFAULT_COOLDOWN_S,
) -> None:
    """Arm the cooldown after a codex worker proved the ChatGPT plan capped."""
    global _cooldown_until
    with _lock:
        _cooldown_until = now_fn() + cooldown_s


def clear_codex_quota_cooldown() -> None:
    """Clear the cooldown immediately (a codex worker just succeeded)."""
    global _cooldown_until
    with _lock:
        _cooldown_until = 0.0


def codex_in_quota_cooldown(
    *, now_fn: Callable[[], float] = time.monotonic
) -> bool:
    """True while the codex plan is presumed capped (cooldown armed)."""
    with _lock:
        return now_fn() < _cooldown_until


__all__ = [
    "clear_codex_quota_cooldown",
    "codex_in_quota_cooldown",
    "mark_codex_quota_cooldown",
]
