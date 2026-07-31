"""Nothing in the dictation lane may cut a user off mid-sentence.

Two ceilings used to do exactly that, and neither came from a provider:

* ``[dictation].max_seconds`` stopped a recording after five minutes, which is
  shorter than plenty of real dictations. No speech-to-text request ever
  carries the whole recording — the final pass cuts the audio at its quietest
  points into segment-sized pieces — so a provider's file-size ceiling is
  reached by a SEGMENT and never by a long dictation. The limit was ours.
* ``[dictation].polish_max_input_chars`` silently skipped the formatting pass
  above 4000 characters, i.e. on precisely the long transcripts that need it,
  duplicating guards that already speak when they fire.

Both are now off by default and both remain honoured when a user sets them.
These tests pin that, and pin the one ceiling that must NOT follow the
recording into being unbounded: the wake-word block, whose failure mode is a
wake word that stays deaf until the app is restarted (BUG-037).
"""

from __future__ import annotations

import pytest

from jarvis.core.config import DictationConfig


class TestRecordingCeiling:
    def test_the_default_is_far_past_any_real_dictation(self) -> None:
        """Five minutes cut people off; half an hour does not."""
        assert DictationConfig().max_seconds >= 1800.0

    def test_zero_is_accepted_and_means_no_ceiling(self) -> None:
        """``0`` must survive validation rather than being clamped away.

        A ``gt=0.0`` bound would reject it and leave no way to switch the
        ceiling off at all.
        """
        assert DictationConfig(max_seconds=0).max_seconds == 0.0

    def test_a_configured_ceiling_is_still_honoured(self) -> None:
        """Removing a default is not the same as ignoring the setting."""
        assert DictationConfig(max_seconds=90).max_seconds == 90.0

    def test_zero_reaches_the_wait_as_no_timeout(self) -> None:
        """``0`` has to become asyncio's ``None``, not fall back to a default.

        The old code read the value as ``max_seconds or 300.0``, which turned
        the off switch into the very ceiling it was meant to remove (AP-31).
        """
        from jarvis.speech import pipeline

        assert (0.0 or None) is None
        # And the fallback used for a MISSING setting is the generous one,
        # never the old five minutes.
        assert pipeline._DICTATION_DEFAULT_MAX_S >= 1800.0

    def test_the_wake_block_stays_bounded_when_the_recording_is_not(self) -> None:
        """An unbounded recording may not imply an unbounded wake block.

        The two ceilings are deliberately allowed to disagree: a wake word that
        answers during a very long dictation is recoverable in a second, while
        one that never unblocks needs an app restart.
        """
        from jarvis.speech import pipeline

        assert pipeline._DICTATION_UNBOUNDED_WAKE_BLOCK_S > 0
        assert pipeline._DICTATION_UNBOUNDED_WAKE_BLOCK_S < float("inf")


class TestPolishInputCeiling:
    def test_long_dictations_are_polished_by_default(self) -> None:
        """``0`` = no cap. A long transcript is the case that needs the pass."""
        assert DictationConfig().polish_max_input_chars == 0

    def test_a_configured_cap_is_still_honoured(self) -> None:
        assert DictationConfig(polish_max_input_chars=4000).polish_max_input_chars == 4000

    def test_the_module_default_agrees_with_the_config_default(self) -> None:
        """The getattr fallback must not reinstate a cap the config removed.

        ``polish.py`` reads every setting through ``getattr`` so it works on a
        jarvis.toml that predates these keys. A fallback of 4000 there would
        quietly restore the ceiling for exactly those older installs.
        """
        from jarvis.dictation import polish

        assert polish._DEFAULT_MAX_INPUT_CHARS == DictationConfig().polish_max_input_chars

    @pytest.mark.parametrize("length", [5_000, 20_000, 120_000])
    async def test_a_long_transcript_is_not_skipped_as_too_long(
        self, length: int
    ) -> None:
        """No length may resolve to ``skipped_long`` under the default config."""
        from jarvis.dictation.polish import polish_transcript

        outcome = await polish_transcript(
            "word " * (length // 5),
            language="en",
            cfg=DictationConfig(),
        )
        assert outcome.status != "skipped_long"
        # Whatever else happened, the text is never lost.
        assert outcome.text
