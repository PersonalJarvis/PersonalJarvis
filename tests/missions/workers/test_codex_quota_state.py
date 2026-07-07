"""Process-local codex (ChatGPT) quota-cooldown flag.

Live pattern (mission_019f3cd8 + the wall of failed missions, 2026-07-07):
the codex ChatGPT plan hit its usage cap ("You've hit your usage limit …
try again at Jul 31st"), but `codex status` still reported connected=True.
The worker factory therefore re-picked codex on EVERY mission and retry,
each spawn burning ~28 s before the cap error came back — and the in-worker
fallback then died on a dead Claude login, so no mission ever reached the
healthy OpenRouter key (AP-22). This flag is the codex mirror of
``claude_quota_state``: a usage-capped codex arms a TIME-based, self-expiring
cooldown; while armed the factory skips codex and crosses families. A codex
success clears it immediately.
"""
from __future__ import annotations

import jarvis.codex_quota_state as qs


def test_default_not_in_cooldown() -> None:
    qs.clear_codex_quota_cooldown()
    assert qs.codex_in_quota_cooldown() is False


def test_mark_sets_cooldown() -> None:
    clock = [1000.0]
    qs.mark_codex_quota_cooldown(now_fn=lambda: clock[0], cooldown_s=600.0)
    assert qs.codex_in_quota_cooldown(now_fn=lambda: clock[0]) is True
    qs.clear_codex_quota_cooldown()


def test_cooldown_expires_after_window() -> None:
    clock = [1000.0]
    qs.mark_codex_quota_cooldown(now_fn=lambda: clock[0], cooldown_s=600.0)
    # Still inside the window.
    assert qs.codex_in_quota_cooldown(now_fn=lambda: 1300.0) is True
    # Past the window -> auto-expires (no manual clear, cap reset re-probes).
    assert qs.codex_in_quota_cooldown(now_fn=lambda: 1601.0) is False
    qs.clear_codex_quota_cooldown()


def test_clear_resets_immediately() -> None:
    clock = [1000.0]
    qs.mark_codex_quota_cooldown(now_fn=lambda: clock[0], cooldown_s=600.0)
    qs.clear_codex_quota_cooldown()
    assert qs.codex_in_quota_cooldown(now_fn=lambda: clock[0]) is False


def test_default_cooldown_is_twenty_minutes() -> None:
    assert qs._DEFAULT_COOLDOWN_S == 20 * 60.0
