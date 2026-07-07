"""Process-local per-API-family quota/auth cooldown (time-based, fingerprinted).

Why this exists: the mission worker's API-key family walk
(``claude-api → gemini → openrouter → openai``) picked the FIRST family with a
stored key — with no memory of that family failing. Mission 019f3d0f
(2026-07-07, the verify run of the BUG-042 fixes): gemini's prepaid credits
were DEPLETED (429 RESOURCE_EXHAUSTED), yet every retry re-picked gemini; the
healthy openrouter key one slot further in the SAME loop was never reached
(AP-22). Same shape as the stale claude-api bearer that 401'd every retry in
mission 019f3d01.

This is the generic API-family mirror of ``claude_quota_state`` /
``codex_quota_state``, keyed by provider slug. When an ``ApiAgentWorker`` run
dies on a quota or auth provider error, it arms this cooldown; while armed the
family walk (``_api_key_family_viable``) skips the family. A successful run
clears it immediately, and the cooldown self-expires so a reset cap is
re-probed.

Fingerprint binding (in-app recoverability, CLAUDE.md §3): the cooldown is
bound to the credential that failed. Saving a NEW key in the API-Keys view
changes the fingerprint and lifts the block instantly — no restart, no wait.
A check without a current fingerprint is conservative: the cooldown holds.

Process-local + in-memory like its siblings: the live app is one process, a
module global suffices, and it resets on restart (then re-detected on the next
failing spawn). Monotonic clock; ``now_fn`` injectable for deterministic tests.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

# Same middle ground as the claude/codex cooldowns: at most one wasted re-probe
# per ~20 min of continuous mission activity, back in rotation soon after a
# top-up / cap reset.
_DEFAULT_COOLDOWN_S: float = 20 * 60.0

_lock = threading.Lock()
# provider slug -> (monotonic deadline, fingerprint-at-mark-time or None)
_cooldowns: dict[str, tuple[float, str | None]] = {}


def _norm(provider: str | None) -> str:
    return (provider or "").strip().lower()


def mark_api_family_cooldown(
    provider: str,
    *,
    fingerprint: str | None = None,
    now_fn: Callable[[], float] = time.monotonic,
    cooldown_s: float = _DEFAULT_COOLDOWN_S,
) -> None:
    """Arm the cooldown after a worker proved *provider*'s key unusable
    (quota depleted / rate-capped / auth-dead)."""
    key = _norm(provider)
    if not key:
        return
    with _lock:
        _cooldowns[key] = (now_fn() + cooldown_s, fingerprint)


def clear_api_family_cooldown(provider: str) -> None:
    """Clear the cooldown immediately (a worker on *provider* just succeeded)."""
    with _lock:
        _cooldowns.pop(_norm(provider), None)


def api_family_in_cooldown(
    provider: str,
    *,
    current_fingerprint: str | None = None,
    now_fn: Callable[[], float] = time.monotonic,
) -> bool:
    """True while *provider*'s key is presumed unusable.

    A ``current_fingerprint`` differing from the one recorded at mark time
    means the user saved a NEW credential — the cooldown does not apply to it.
    """
    key = _norm(provider)
    with _lock:
        entry = _cooldowns.get(key)
        if entry is None:
            return False
        deadline, marked_fp = entry
        if now_fn() >= deadline:
            _cooldowns.pop(key, None)
            return False
        if (
            marked_fp is not None
            and current_fingerprint is not None
            and current_fingerprint != marked_fp
        ):
            return False
        return True


__all__ = [
    "api_family_in_cooldown",
    "clear_api_family_cooldown",
    "mark_api_family_cooldown",
]
