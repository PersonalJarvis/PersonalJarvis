"""Process-local Claude Max quota-cooldown flag.

Live pattern (missions 019eb2fd + the repeated session-limit failures,
2026-06-10/11): with [brain.sub_jarvis].provider = claude-api the mission
workers run on the SAME Claude Max OAuth window as the interactive dev
session. A heavy dev session exhausts the 5-hour window, then every
mission wastes a ~16 s Claude attempt before the reactive codex fallback
recovers. This flag lets the worker factory route STRAIGHT to codex while
Claude is known-exhausted — the proactive complement to the reactive
fallback. Unlike codex_needs_reauth (a boolean cleared by `codex login`),
a quota window auto-resets, so the cooldown is TIME-based and self-expires.
"""
from __future__ import annotations

import jarvis.claude_quota_state as qs


def test_default_not_in_cooldown() -> None:
    qs.clear_claude_quota_cooldown()
    assert qs.claude_in_quota_cooldown() is False


def test_mark_sets_cooldown() -> None:
    clock = [1000.0]
    qs.mark_claude_quota_cooldown(now_fn=lambda: clock[0], cooldown_s=600.0)
    assert qs.claude_in_quota_cooldown(now_fn=lambda: clock[0]) is True
    qs.clear_claude_quota_cooldown()


def test_cooldown_expires_after_window() -> None:
    clock = [1000.0]
    qs.mark_claude_quota_cooldown(now_fn=lambda: clock[0], cooldown_s=600.0)
    # Still inside the window.
    assert qs.claude_in_quota_cooldown(now_fn=lambda: 1300.0) is True
    # Past the window -> auto-expires (no manual clear, window reset).
    assert qs.claude_in_quota_cooldown(now_fn=lambda: 1601.0) is False
    qs.clear_claude_quota_cooldown()


def test_clear_resets_immediately() -> None:
    clock = [1000.0]
    qs.mark_claude_quota_cooldown(now_fn=lambda: clock[0], cooldown_s=600.0)
    qs.clear_claude_quota_cooldown()
    assert qs.claude_in_quota_cooldown(now_fn=lambda: clock[0]) is False


def test_default_cooldown_is_twenty_minutes() -> None:
    assert qs._DEFAULT_COOLDOWN_S == 20 * 60.0
