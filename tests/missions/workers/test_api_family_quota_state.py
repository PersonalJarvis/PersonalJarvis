"""Process-local per-API-family quota/auth cooldown.

Live pattern (mission 019f3d0f, 2026-07-07 — the verify run of the BUG-042
fixes): with both subscription workers down, the API-key family walk picked
gemini, whose prepaid credits were DEPLETED (429 RESOURCE_EXHAUSTED). Nothing
remembered that, so every retry re-picked gemini — the healthy openrouter key
one slot further in the SAME loop was never reached. This is the generic
API-family mirror of ``claude_quota_state`` / ``codex_quota_state``, keyed by
provider slug and FINGERPRINTED: saving a NEW key in the API-Keys view lifts
the cooldown instantly (in-app recoverability, CLAUDE.md §3), while the same
dead key stays skipped until the cooldown self-expires.
"""
from __future__ import annotations

import pytest

import jarvis.api_family_quota_state as qs


@pytest.fixture(autouse=True)
def _reset() -> None:
    qs.clear_api_family_cooldown("gemini")
    qs.clear_api_family_cooldown("openrouter")
    yield
    qs.clear_api_family_cooldown("gemini")
    qs.clear_api_family_cooldown("openrouter")


def test_default_not_in_cooldown() -> None:
    assert qs.api_family_in_cooldown("gemini") is False


def test_mark_sets_cooldown_per_family() -> None:
    clock = [1000.0]
    qs.mark_api_family_cooldown("gemini", now_fn=lambda: clock[0], cooldown_s=600.0)
    assert qs.api_family_in_cooldown("gemini", now_fn=lambda: clock[0]) is True
    # Only the marked family is affected.
    assert qs.api_family_in_cooldown("openrouter", now_fn=lambda: clock[0]) is False


def test_cooldown_expires_after_window() -> None:
    qs.mark_api_family_cooldown("gemini", now_fn=lambda: 1000.0, cooldown_s=600.0)
    assert qs.api_family_in_cooldown("gemini", now_fn=lambda: 1300.0) is True
    assert qs.api_family_in_cooldown("gemini", now_fn=lambda: 1601.0) is False


def test_clear_resets_immediately() -> None:
    qs.mark_api_family_cooldown("gemini", now_fn=lambda: 1000.0, cooldown_s=600.0)
    qs.clear_api_family_cooldown("gemini")
    assert qs.api_family_in_cooldown("gemini", now_fn=lambda: 1000.0) is False


def test_new_key_lifts_cooldown_instantly() -> None:
    """In-app recovery: the cooldown is bound to the credential that failed.
    A DIFFERENT current fingerprint (user saved a fresh key) is not blocked."""
    qs.mark_api_family_cooldown(
        "gemini", fingerprint="dead-key-fp", now_fn=lambda: 1000.0, cooldown_s=600.0
    )
    assert (
        qs.api_family_in_cooldown(
            "gemini", current_fingerprint="dead-key-fp", now_fn=lambda: 1000.0
        )
        is True
    )
    assert (
        qs.api_family_in_cooldown(
            "gemini", current_fingerprint="FRESH-key-fp", now_fn=lambda: 1000.0
        )
        is False
    )


def test_unknown_current_fingerprint_stays_blocked() -> None:
    """No fingerprint available at check time → conservative: cooldown holds."""
    qs.mark_api_family_cooldown(
        "gemini", fingerprint="dead-key-fp", now_fn=lambda: 1000.0, cooldown_s=600.0
    )
    assert qs.api_family_in_cooldown("gemini", now_fn=lambda: 1000.0) is True


def test_default_cooldown_is_twenty_minutes() -> None:
    assert qs._DEFAULT_COOLDOWN_S == 20 * 60.0
