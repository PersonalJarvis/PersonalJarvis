"""Process-local Claude auth-dead flag (fingerprinted, self-expiring).

Why this exists (2026-07-06 incident, missions 019f36e5 + 019f38b1): the
Claude Max OAuth access token in ``~/.claude/.credentials.json`` expired and
nothing on the host refreshed it anymore, yet ``claude status`` reported
connected=True (presence-only check) and the worker factory kept routing every
heavy mission to the ``claude`` CLI. Each spawn died in ~15 s with
"Failed to authenticate. API Error: 401 Invalid authentication credentials"
while a healthy codex ChatGPT login and a healthy OpenRouter key were sitting
right there — the AP-22 single-provider brick.

This is the Claude mirror of ``codex_auth_state`` (the 2026-06-08 codex
incident), with two twists:

- TIME-based like ``claude_quota_state``: dead auth does not fix itself, but
  the user can fix it any moment (``claude /login`` refreshes the OAuth file,
  or a fresh API key is saved in-app). A bounded cooldown re-probes Claude
  instead of locking it out until the next app restart.
- FINGERPRINTED: the flag remembers a hash of the credential that produced the
  401. A caller passing the fingerprint of the credential it WOULD use next
  gets ``False`` as soon as that credential differs — a fresh login/key
  re-enables Claude instantly, while the same dead credential stays flagged
  (no flip-flop inside a mission's retry loop).

Process-local + in-memory, like its two siblings: the live app is one process,
a module global suffices, and it resets on restart (then re-detected on the
next dead-auth mission). Monotonic clock so it is immune to wall-clock jumps;
``now_fn`` is injectable for deterministic tests.
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable

# 15 minutes: long enough that a mission's 3-iteration retry loop never
# re-probes the same dead credential, short enough that a fixed-out-of-band
# auth (e.g. a key rotated outside the app) costs at most one ~15 s re-probe
# per window.
_DEFAULT_COOLDOWN_S: float = 15 * 60.0

_lock = threading.Lock()
_dead_until: float = 0.0  # monotonic deadline; 0.0 == not armed
_dead_fingerprint: str | None = None


def credential_fingerprint(credential: str | None) -> str | None:
    """Return a short, non-reversible fingerprint of a credential, or ``None``.

    Never the raw secret — safe to keep in memory next to log statements.
    """
    if not credential:
        return None
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()[:16]


def mark_claude_auth_dead(
    *,
    fingerprint: str | None = None,
    now_fn: Callable[[], float] = time.monotonic,
    cooldown_s: float = _DEFAULT_COOLDOWN_S,
) -> None:
    """Record that Claude auth is proven dead (a worker just got a 401).

    ``fingerprint`` identifies the credential that produced the failure
    (see :func:`credential_fingerprint`); ``None`` means "unknown credential"
    and flags Claude dead for the whole cooldown regardless of what a caller
    would use next.
    """
    global _dead_until, _dead_fingerprint
    with _lock:
        _dead_until = now_fn() + cooldown_s
        _dead_fingerprint = fingerprint


def clear_claude_auth_dead() -> None:
    """Clear the flag immediately (a Claude worker just succeeded)."""
    global _dead_until, _dead_fingerprint
    with _lock:
        _dead_until = 0.0
        _dead_fingerprint = None


def claude_auth_dead(
    *,
    current_fingerprint: str | None = None,
    now_fn: Callable[[], float] = time.monotonic,
) -> bool:
    """True while Claude auth is presumed dead for the credential at hand.

    ``current_fingerprint`` is the fingerprint of the credential the caller
    WOULD use next. When it differs from the one that produced the 401, the
    user has re-authenticated — return ``False`` so Claude re-enters rotation
    instantly. ``None`` (caller cannot tell) trusts the mark.
    """
    with _lock:
        if now_fn() >= _dead_until:
            return False
        if (
            current_fingerprint is not None
            and _dead_fingerprint is not None
            and current_fingerprint != _dead_fingerprint
        ):
            return False
        return True


__all__ = [
    "claude_auth_dead",
    "clear_claude_auth_dead",
    "credential_fingerprint",
    "mark_claude_auth_dead",
]
