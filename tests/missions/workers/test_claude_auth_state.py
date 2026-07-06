"""Process-local Claude auth-dead flag (fingerprinted, self-expiring).

Mirror of ``codex_auth_state`` (the 2026-06-08 codex incident) for the Claude
side of the 2026-07-06 incident: the Claude Max OAuth token expired, every
``claude``-CLI worker died with a 401, and the worker factory kept picking the
dead Claude path because nothing recorded "Claude auth is proven dead".

Semantics under test:
- ``mark_claude_auth_dead(fingerprint=...)`` arms a TIME-based cooldown (auth
  does not fix itself, but the user can fix it any moment via ``claude /login``
  or a fresh API key — a bounded cooldown re-probes instead of locking Claude
  out until restart).
- ``claude_auth_dead(current_fingerprint=...)`` returns False as soon as the
  CURRENT credential differs from the one that produced the 401 — a fresh
  login/key re-enables Claude instantly, while the same dead credential stays
  flagged (no flip-flop inside a mission's retry loop).
"""
from __future__ import annotations

import jarvis.claude_auth_state as cas


def _reset() -> None:
    cas.clear_claude_auth_dead()


def test_default_not_dead() -> None:
    _reset()
    assert cas.claude_auth_dead() is False


def test_mark_sets_dead() -> None:
    _reset()
    cas.mark_claude_auth_dead(fingerprint="fp-1", now_fn=lambda: 1000.0)
    assert cas.claude_auth_dead(now_fn=lambda: 1000.0) is True
    _reset()


def test_clear_resets_immediately() -> None:
    _reset()
    cas.mark_claude_auth_dead(fingerprint="fp-1", now_fn=lambda: 1000.0)
    cas.clear_claude_auth_dead()
    assert cas.claude_auth_dead(now_fn=lambda: 1000.0) is False


def test_cooldown_self_expires() -> None:
    _reset()
    cas.mark_claude_auth_dead(
        fingerprint="fp-1", now_fn=lambda: 1000.0, cooldown_s=600.0
    )
    assert cas.claude_auth_dead(now_fn=lambda: 1300.0) is True
    assert cas.claude_auth_dead(now_fn=lambda: 1601.0) is False
    _reset()


def test_same_dead_credential_stays_flagged() -> None:
    """The credential that produced the 401 must not be retried this cooldown."""
    _reset()
    cas.mark_claude_auth_dead(fingerprint="fp-dead", now_fn=lambda: 1000.0)
    assert (
        cas.claude_auth_dead(current_fingerprint="fp-dead", now_fn=lambda: 1000.0)
        is True
    )
    _reset()


def test_fresh_credential_reenables_claude_instantly() -> None:
    """A NEW token/key (different fingerprint) means the user re-authed —
    Claude is viable again without waiting for the cooldown or a restart."""
    _reset()
    cas.mark_claude_auth_dead(fingerprint="fp-dead", now_fn=lambda: 1000.0)
    assert (
        cas.claude_auth_dead(current_fingerprint="fp-fresh", now_fn=lambda: 1000.0)
        is False
    )
    _reset()


def test_unknown_current_fingerprint_stays_flagged() -> None:
    """No current credential to compare (None) → trust the mark."""
    _reset()
    cas.mark_claude_auth_dead(fingerprint="fp-dead", now_fn=lambda: 1000.0)
    assert cas.claude_auth_dead(current_fingerprint=None, now_fn=lambda: 1000.0) is True
    _reset()


def test_fingerprint_helper_is_stable_and_none_safe() -> None:
    fp1 = cas.credential_fingerprint("sk-ant-oat01-abc")
    fp2 = cas.credential_fingerprint("sk-ant-oat01-abc")
    fp3 = cas.credential_fingerprint("sk-ant-oat01-XYZ")
    assert fp1 == fp2
    assert fp1 != fp3
    assert cas.credential_fingerprint(None) is None
    assert cas.credential_fingerprint("") is None
    # Never the raw secret.
    assert "sk-ant" not in (fp1 or "")
