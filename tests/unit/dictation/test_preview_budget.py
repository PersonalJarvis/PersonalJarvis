"""The live preview must not spend the request budget the transcript needs.

Measured cause (2026-07-29): dictation spent ~40 provider requests per minute of
speech, only ~7.5 of them producing final text. The rest were preview calls
whose answers are discarded on the next tick — and they were what pushed a 137 s
dictation past the provider's per-minute limit, so the segment closes got the
429s and the user got 367 characters for two minutes of speech.
"""

from __future__ import annotations

from jarvis.dictation.preview_budget import (
    PREVIEW_CALLS_PER_MINUTE,
    PreviewBudget,
    preview_budget,
)


def test_the_budget_stops_at_its_limit():
    budget = PreviewBudget(calls_per_minute=3)
    assert [budget.try_spend() for _ in range(5)] == [True, True, True, False, False]


def test_remaining_reports_what_is_left():
    budget = PreviewBudget(calls_per_minute=3)
    assert budget.remaining() == 3
    budget.try_spend()
    assert budget.remaining() == 2


def test_the_window_rolls(monkeypatch):
    """A minute later the calls are free again — it is a window, not a quota."""
    import jarvis.dictation.preview_budget as mod

    now = [1_000.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
    budget = PreviewBudget(calls_per_minute=2)
    assert budget.try_spend() and budget.try_spend()
    assert not budget.try_spend()

    now[0] += 61.0
    assert budget.try_spend(), "the rolling window should have released the calls"


def test_a_zero_budget_disables_the_preview_entirely():
    """The escape hatch for a very tight tier: transcript only, no preview."""
    assert PreviewBudget(calls_per_minute=0).try_spend() is False


def test_the_budget_is_shared_across_dictations():
    """Provider limits are rolling windows; per-session budgets would reset.

    Three back-to-back 20 s dictations meet the same limit one 60 s dictation
    does. A budget that restarted with each session would rebuild exactly the
    failure it exists to prevent.
    """
    assert preview_budget() is preview_budget()


def test_the_default_leaves_room_for_the_segment_traffic():
    """Segments close ~7.5x/min at the 8 s default and are NEVER budgeted.

    20 RPM is Groq's published limit for both whisper models AND the limit its
    paid Developer plan carries — upgrading buys no relief, so the budget has to
    fit inside it. Preview plus segment traffic must stay under that or the
    budget would be decorative.
    """
    provider_rpm_limit = 20  # Groq whisper-large-v3, free AND developer plan
    segments_per_minute = 60 / 8.0
    assert PREVIEW_CALLS_PER_MINUTE + segments_per_minute < provider_rpm_limit
