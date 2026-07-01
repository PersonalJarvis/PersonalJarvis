"""Unit tests for the single-slot pending-prompt buffer.

Target module: ``jarvis/speech/pending_buffer.py`` (not yet implemented — RED).

The buffer is intentionally minimal: it stores a dangling fragment, allows
concatenation of continuations, tracks the chain count and last-activity
timestamp, and yields the joined text on flush. It does NOT classify, NOT
schedule timeouts, NOT contact the brain — the orchestrator owns those concerns.
The clock is injected so the age tests stay deterministic.
"""

from __future__ import annotations

import pytest

from jarvis.speech.pending_buffer import PendingPromptBuffer


def _clock(*timestamps_ns: int):
    """Return a callable that yields the given ns timestamps in order."""
    it = iter(timestamps_ns)
    return lambda: next(it)


def test_buffer_starts_empty() -> None:
    buf = PendingPromptBuffer()
    assert buf.is_pending is False
    assert buf.fragment == ""
    assert buf.language == ""
    assert buf.chain_count == 0
    assert buf.age_ms() is None


def test_start_sets_fragment_and_initial_chain_count() -> None:
    buf = PendingPromptBuffer()
    buf.start("Erinnere mich daran, dass", language="de")
    assert buf.is_pending is True
    assert buf.fragment == "Erinnere mich daran, dass"
    assert buf.language == "de"
    assert buf.chain_count == 1


def test_extend_concatenates_with_single_space() -> None:
    buf = PendingPromptBuffer()
    buf.start("Erinnere mich daran, dass")
    buf.extend("ich morgen Brötchen kaufe")  # i18n-allow
    assert buf.fragment == "Erinnere mich daran, dass ich morgen Brötchen kaufe"  # i18n-allow
    assert buf.chain_count == 2


def test_extend_increments_chain_count_per_call() -> None:
    buf = PendingPromptBuffer()
    buf.start("eins")
    buf.extend("zwei")
    buf.extend("drei")
    assert buf.fragment == "eins zwei drei"
    assert buf.chain_count == 3


def test_extend_strips_redundant_whitespace_on_both_sides() -> None:
    buf = PendingPromptBuffer()
    buf.start("  Erinnere mich, dass  ")
    buf.extend("  ich anrufe  ")
    assert buf.fragment == "Erinnere mich, dass ich anrufe"


def test_extend_without_start_raises() -> None:
    buf = PendingPromptBuffer()
    with pytest.raises(RuntimeError):
        buf.extend("orphan continuation")


def test_flush_returns_joined_text_and_clears_state() -> None:
    buf = PendingPromptBuffer()
    buf.start("frag", language="de")
    buf.extend("ment")
    flushed = buf.flush()
    assert flushed == "frag ment"
    assert buf.is_pending is False
    assert buf.fragment == ""
    assert buf.language == ""
    assert buf.chain_count == 0
    assert buf.age_ms() is None


def test_flush_on_empty_returns_none() -> None:
    buf = PendingPromptBuffer()
    assert buf.flush() is None
    assert buf.is_pending is False


def test_clear_discards_without_returning() -> None:
    buf = PendingPromptBuffer()
    buf.start("frag")
    buf.clear()
    assert buf.is_pending is False
    assert buf.fragment == ""
    assert buf.chain_count == 0
    assert buf.age_ms() is None


def test_start_replaces_a_previously_pending_fragment() -> None:
    buf = PendingPromptBuffer()
    buf.start("old", language="de")
    buf.extend("more")
    buf.start("new", language="en")
    assert buf.fragment == "new"
    assert buf.language == "en"
    assert buf.chain_count == 1


def test_age_ms_measures_time_since_start_with_injected_clock() -> None:
    buf = PendingPromptBuffer(clock=_clock(1_000_000_000, 1_080_000_000))
    buf.start("frag")
    # second call hits the second timestamp = +80 ms
    assert buf.age_ms() == 80


def test_extend_resets_the_age_timer() -> None:
    # start @ 0ms, extend @ 4000ms (resets), age@ 4500ms → 500ms since last activity
    buf = PendingPromptBuffer(clock=_clock(0, 4_000_000_000, 4_500_000_000))
    buf.start("frag")
    buf.extend("more")
    assert buf.age_ms() == 500


def test_language_defaults_to_empty_when_unspecified() -> None:
    buf = PendingPromptBuffer()
    buf.start("text")
    assert buf.language == ""
