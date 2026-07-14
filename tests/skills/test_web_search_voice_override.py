"""Tests for ``skills.web_search._voice_override``."""

from __future__ import annotations

import pytest

pytest.importorskip(
    "skills.web_search", reason="top-level skills package absent (public snapshot / plain checkout)"
)
from skills.web_search._voice_override import (
    SearchSettings,
    TEXT_LATENCY_BUDGET_MS,
    TEXT_MAX_RESULTS,
    VOICE_LATENCY_BUDGET_MS,
    VOICE_MAX_RESULTS,
    VOICE_MAX_SUMMARY_CHARS,
    apply_voice_override,
    scrub_for_speech,
)


class TestApplyVoiceOverride:
    def test_voice_false_returns_input_unchanged(self) -> None:
        base = SearchSettings()
        out = apply_voice_override(base, voice=False)
        assert out == base

    def test_voice_true_caps_max_results(self) -> None:
        base = SearchSettings(max_results=20)
        out = apply_voice_override(base, voice=True)
        assert out.max_results == VOICE_MAX_RESULTS

    def test_voice_true_caps_summary_chars(self) -> None:
        base = SearchSettings(max_summary_chars=5_000)
        out = apply_voice_override(base, voice=True)
        assert out.max_summary_chars == VOICE_MAX_SUMMARY_CHARS

    def test_voice_true_caps_latency_budget(self) -> None:
        base = SearchSettings(latency_budget_ms=30_000)
        out = apply_voice_override(base, voice=True)
        assert out.latency_budget_ms == VOICE_LATENCY_BUDGET_MS

    def test_voice_true_sets_strip_markdown_and_urls(self) -> None:
        base = SearchSettings(strip_markdown=False, strip_urls_from_summary=False)
        out = apply_voice_override(base, voice=True)
        assert out.strip_markdown is True
        assert out.strip_urls_from_summary is True

    def test_voice_override_does_not_raise_caps(self) -> None:
        """Voice override is a one-way *tighten* — it must never relax
        already-strict settings."""
        already_strict = SearchSettings(
            max_results=1,
            max_summary_chars=50,
            latency_budget_ms=500,
            strip_markdown=True,
            strip_urls_from_summary=True,
        )
        out = apply_voice_override(already_strict, voice=True)
        assert out.max_results == 1
        assert out.max_summary_chars == 50
        assert out.latency_budget_ms == 500

    def test_voice_override_is_pure(self) -> None:
        """Calling twice with the same input yields equal output (no
        hidden state). Also covers the dataclass frozen contract."""
        base = SearchSettings()
        a = apply_voice_override(base, voice=True)
        b = apply_voice_override(base, voice=True)
        assert a == b
        # And the input must not have been mutated.
        assert base.max_results == TEXT_MAX_RESULTS
        assert base.latency_budget_ms == TEXT_LATENCY_BUDGET_MS


class TestScrubForSpeech:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("plain text", "plain text"),
            ("**bold** word", "bold word"),
            ("`code` block", "code block"),
            ("# header line", "header line"),
            ("see https://example.com here", "see here"),
            ("at www.acme.org now", "at now"),
        ],
    )
    def test_scrub_examples(self, raw: str, expected: str) -> None:
        assert scrub_for_speech(raw) == expected

    def test_scrub_collapses_double_spaces(self) -> None:
        assert "  " not in scrub_for_speech("**a**   **b**")

    def test_scrub_is_idempotent(self) -> None:
        once = scrub_for_speech("**foo** see https://x.y bar")
        twice = scrub_for_speech(once)
        assert once == twice
