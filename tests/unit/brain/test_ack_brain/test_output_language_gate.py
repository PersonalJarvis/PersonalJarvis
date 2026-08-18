"""The ack's language gate must not drop user-facing speech on a guess.

The generator used to run its own de/en-only top-100-word heuristic and drop
any ack whose guessed language differed from the turn's. Two consequences,
both of which left the user hearing NOTHING on a slow turn — the exact silence
the ack exists to bridge:

* "Moment, in Ordnung." scored en=1 / de=0, because the bare token "in" sat in
  the English set, so a perfectly good German ack was suppressed.
* Any Spanish ack containing "no" was read as English and suppressed too. The
  heuristic could not return "es" at all, so Spanish was structurally unable
  to pass.

The gate now validates against the turn's ALREADY-resolved output language
through ``jarvis.core.turn_language`` (CLAUDE.md §1: one resolver for every
layer), which fails open on anything indeterminate and covers de/en/es equally.
A genuine, high-confidence mismatch must still be blocked.
"""
from __future__ import annotations

import pytest

from tests.unit.brain.test_ack_brain.conftest import (
    FakeAckProvider,
    build_ack_generator_with_fake,
)


# Every phrase below is runtime voice output in the language it is testing.
# i18n-allow: de/en/es ack phrases under test
@pytest.mark.parametrize(
    ("ack_text", "language", "case"),
    [
        # The two live regressions.
        ("Moment, in Ordnung.", "de", "German ack with the token 'in'"),
        ("Un momento, no tardo nada.", "es", "Spanish ack with the token 'no'"),
        # All three locales are equal — an ordinary ack passes in each.
        ("Na klar, kein Problem und auch nicht schlimm.", "de", "German ack"),
        ("Alright, no problem at all for you.", "en", "English ack"),
        ("Hola, qué tal, y gracias por la pregunta.", "es", "Spanish ack"),
        # Names and near-empty output carry no language evidence at all.
        ("Spotify, Notepad, Chrome.", "es", "bare proper nouns"),
        ("Okay.", "de", "bare interjection"),
    ],
)
async def test_ack_survives_when_the_language_is_not_clearly_wrong(
    ack_text: str, language: str, case: str
) -> None:
    gen = build_ack_generator_with_fake(FakeAckProvider(response=ack_text))

    assert await gen.run("egal", language=language) is not None, (
        f"ack dropped for {case}: {ack_text!r}"
    )


async def test_ack_is_still_dropped_on_a_high_confidence_mismatch() -> None:
    """The guard is relaxed, not removed: real cross-language output blocks."""
    # A plainly German ack on an English turn — the one case worth suppressing.
    german_ack = "Ich schaue gleich einmal nach dem Wetter."  # i18n-allow
    gen = build_ack_generator_with_fake(FakeAckProvider(response=german_ack))

    assert await gen.run("what is the weather", language="en") is None


async def test_streaming_path_uses_the_same_relaxed_gate() -> None:
    """``_postprocess`` (the run_stream path) must not re-introduce the drop."""

    class _StreamingFake:
        async def run(self, u: str, lang: str, *, persona_prompt: str) -> str | None:
            return "Moment, in Ordnung."  # i18n-allow: German ack under test

        async def run_stream(self, u: str, lang: str, *, persona_prompt: str):
            yield "Moment, "  # i18n-allow: German ack under test
            yield "in Ordnung."  # i18n-allow: German ack under test

    gen = build_ack_generator_with_fake(_StreamingFake())  # type: ignore[arg-type]

    out = [s async for s in gen.run_stream("egal", language="de")]

    assert out, "the streaming ack was dropped on a language guess"
