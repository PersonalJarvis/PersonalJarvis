"""Pin the per-sentence contract of the streaming TTS path (OF-11 + OF-12).

Two defects lived in ``_brain_streaming`` (jarvis/speech/pipeline.py):

OF-11 — ``scrub_for_voice`` is a WHOLE-TURN filter. Several of its guards
throw the input away and return a canned error phrase for ALL of it. Called
once per sentence, a single short sentence that lost its only noun turned into
"Es trat ein Fehler auf." spoken in the MIDDLE of an otherwise healthy answer.
The sibling failure was silent: a sentence that scrubbed to nothing vanished
with no log line at all, so the answer lost a clause and nothing downstream
knew.

OF-12 — the sentence splitter cut at every period followed by whitespace, so
"Am 1. Januar" became the utterance "Am eins." plus "Januar …", and "z. B.",
"Dr. Meier", "Mr. Smith", "Sr. Lopez" fragmented the same way.

The OF-11 tests drive the scrub through a FAKE so they pin how the pipeline
USES the filter, not what the filter currently does — the filter's own guards
are pinned in tests/unit/brain/test_output_filter.py.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.brain.output_filter import FALLBACK_PHRASES, ScrubResult
from jarvis.core.bus import EventBus
from jarvis.core.protocols import AudioChunk
from jarvis.speech import pipeline as pipeline_module
from jarvis.speech.pipeline import (
    SpeechPipeline,
    _is_whole_text_fallback,
    _next_stream_sentence_break,
    _stream_opens_sentence,
)

# ---------------------------------------------------------------------------
# OF-12: sentence splitting
# ---------------------------------------------------------------------------


def _split(text: str) -> list[str]:
    """Split ``text`` exactly the way the streaming producer does."""
    out: list[str] = []
    rest = text
    while True:
        cut = _next_stream_sentence_break(rest)
        if cut is None:
            break
        sentence = rest[:cut].strip()
        rest = rest[cut:]
        if sentence:
            out.append(sentence)
    tail = rest.strip()
    if tail:
        out.append(tail)
    return out


@pytest.mark.parametrize(
    "text",
    [
        # German ordinals — the reported bug ("Am eins." as its own utterance).
        "Am 1. Januar 2026 ist es soweit.",
        "Am 31. Dezember kam die Rechnung.",
        # Spaced abbreviations: the single-letter token before the period.
        "Das ist z. B. wichtig.",
        "Das gilt u. a. für dich.",
        # Titles.
        "Dr. Meier hat angerufen.",
        "Prof. Schmidt kommt später.",
        # English.
        "Mr. Smith called at 5 p.m. sharp.",
        "Bring water, e.g. a bottle.",
        # Spanish.
        "El Sr. Lopez llamo.",
        "El 1. de enero empieza todo.",
        "Ud. puede traer algo, p. ej. agua.",
    ],
)
def test_mid_sentence_period_is_not_a_boundary(text: str) -> None:
    """None of these is more than ONE utterance."""
    assert _split(text) == [text]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Das ist gut. Das ist besser. Fertig!", 3),
        # A four-digit year is not an ordinal — the boundary survives.
        ("Das war 2026. Danach kam Ruhe.", 2),
        ("Wirklich? Ja! Sicher.", 3),
        ('Er sagte das. "Dann gehen wir", meinte sie.', 2),
        ("Das war knapp. 2026 wird besser.", 2),
        ("Rechnung Nr. 5 kam am 31. Dezember an. Alles bezahlt.", 2),
        ("Prof. Lee wrote about A vs. B. It was good.", 1),
    ],
)
def test_real_boundaries_still_split(text: str, expected: int) -> None:
    assert len(_split(text)) == expected


def test_finished_sentence_is_not_held_back_for_the_next_token() -> None:
    """A sentence must go to TTS the moment it is complete.

    The next character would refine the decision, but waiting for it hands the
    turn to the brain: a tool-use turn streams "Ich schaue nach. " and then
    goes quiet for seconds — exactly when the user must hear something.
    """
    assert _next_stream_sentence_break("Ich schaue nach. ") == len(
        "Ich schaue nach. "
    )


@pytest.mark.parametrize(
    ("rest", "expected"),
    [
        ("Dann kam er.", True),      # uppercase
        ("2026 war gut.", True),     # digit
        ('"Dann kam er."', True),    # opening quote, then uppercase
        ("wichtig.", False),         # lowercase continuation
        ("", True),                  # not streamed yet — never block on it
    ],
)
def test_stream_opens_sentence(rest: str, expected: bool) -> None:
    assert _stream_opens_sentence(rest) is expected


# ---------------------------------------------------------------------------
# OF-11: whole-text fallback recognition
# ---------------------------------------------------------------------------


def test_fallback_flag_is_recognised() -> None:
    result = ScrubResult(
        cleaned=FALLBACK_PHRASES["de"],
        actions=["replaced_stacktrace"],
        fallback_used=True,
    )
    assert _is_whole_text_fallback("Traceback (most recent call last):", result)


def test_fallback_phrase_without_the_flag_is_recognised() -> None:
    """Belt and braces: a guard that forgets ``fallback_used`` still counts."""
    result = ScrubResult(
        cleaned=FALLBACK_PHRASES["en"], actions=["stripped_markdown"]
    )
    assert _is_whole_text_fallback("## Ab.", result)


def test_answer_that_genuinely_says_the_phrase_is_not_a_fallback() -> None:
    phrase = FALLBACK_PHRASES["de"]
    result = ScrubResult(cleaned=phrase, actions=["stripped_markdown"])
    assert not _is_whole_text_fallback(f"**{phrase}**", result)
    assert not _is_whole_text_fallback(phrase, ScrubResult(cleaned=phrase))


def test_healthy_sentence_is_not_a_fallback() -> None:
    result = ScrubResult(cleaned="Der Termin steht.", actions=[])
    assert not _is_whole_text_fallback("Der Termin steht.", result)


# ---------------------------------------------------------------------------
# OF-11: the streaming turn
# ---------------------------------------------------------------------------

_POISON = "POISON"   # the fake scrub answers this with the whole-text fallback
_VANISH = "VANISH"   # ... and this with an empty result


def _fake_scrub(
    text: str, *, language: str = "de", ack_mode: bool = False
) -> ScrubResult:
    """Stand-in for ``scrub_for_voice`` with deterministic verdicts."""
    stripped = text.strip()
    if _POISON in stripped:
        return ScrubResult(
            cleaned=FALLBACK_PHRASES.get(language, FALLBACK_PHRASES["de"]),
            actions=["replaced_stacktrace"],
            fallback_used=True,
        )
    if _VANISH in stripped:
        return ScrubResult(cleaned="", actions=["stripped_end_signal"])
    return ScrubResult(cleaned=stripped, actions=[])


@dataclass
class _RecordingTTS:
    name: str = "recording-tts"
    supports_streaming: bool = True
    synth_started: list[str] = field(default_factory=list)

    async def synthesize(
        self, text: str, voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        self.synth_started.append(text)
        yield AudioChunk(
            pcm=b"\x00\x00", sample_rate=24_000, timestamp_ns=0, channels=1
        )


@dataclass
class _NullPlayer:
    stop_calls: int = 0

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        async for _chunk in chunks:
            pass

    def stop(self) -> None:
        self.stop_calls += 1


class _ScriptedBrain:
    """Streams a fixed reply token by token."""

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens

    async def __call__(self, text: str) -> str:  # pragma: no cover - unused
        return "".join(self._tokens)

    async def generate_stream(self, text: str) -> AsyncIterator[str]:
        for token in self._tokens:
            yield token


async def _spoken_sentences(tokens: list[str], lang: str) -> list[str]:
    tts = _RecordingTTS()
    pipeline = SpeechPipeline(
        tts=tts, bus=EventBus(), enable_whisper_wake=False
    )
    pipeline._player = _NullPlayer()  # type: ignore[assignment]
    pipeline._brain = _ScriptedBrain(tokens)  # type: ignore[assignment]
    pipeline._latency_tracker = None
    pipeline._tts_lookahead_sentences = 2

    async def _never_barge(**_kwargs) -> bool:
        await asyncio.sleep(3600)
        return False

    pipeline._barge_monitor = _never_barge  # type: ignore[assignment]
    await asyncio.wait_for(pipeline._brain_streaming("egal", lang), timeout=10)
    return tts.synth_started


@pytest.mark.asyncio
async def test_filtered_sentence_never_interrupts_a_healthy_answer(
    monkeypatch, caplog
) -> None:
    """The canned phrase must NOT be spliced between two good sentences."""
    monkeypatch.setattr(pipeline_module, "scrub_for_voice", _fake_scrub)
    with caplog.at_level(logging.WARNING, logger="jarvis.speech.pipeline"):
        spoken = await _spoken_sentences(
            ["Der Bericht ist fertig. ", f"{_POISON} Meldung. ",
             "Sonst noch was?"],
            "de",
        )

    assert spoken == ["Der Bericht ist fertig.", "Sonst noch was?"]
    assert FALLBACK_PHRASES["de"] not in spoken
    # ... and the drop is on the record (CLAUDE.md §7: nothing swallowed).
    dropped = [r for r in caplog.records if "DROPPED" in r.getMessage()]
    assert len(dropped) == 1
    assert _POISON in dropped[0].getMessage()


@pytest.mark.asyncio
async def test_sentence_that_scrubs_to_nothing_is_logged(
    monkeypatch, caplog
) -> None:
    """The silent sibling: an empty scrub result loses a clause — say so."""
    monkeypatch.setattr(pipeline_module, "scrub_for_voice", _fake_scrub)
    with caplog.at_level(logging.WARNING, logger="jarvis.speech.pipeline"):
        spoken = await _spoken_sentences(
            ["Der Bericht ist fertig. ", f"{_VANISH} Rest. ",
             "Sonst noch was?"],
            "de",
        )

    assert spoken == ["Der Bericht ist fertig.", "Sonst noch was?"]
    dropped = [r for r in caplog.records if "DROPPED" in r.getMessage()]
    assert len(dropped) == 1
    assert "empty after scrub" in dropped[0].getMessage()


@pytest.mark.parametrize("lang", ["de", "en", "es"])
@pytest.mark.asyncio
async def test_fully_filtered_turn_still_speaks_the_fallback_once(
    monkeypatch, lang: str
) -> None:
    """Dropping per sentence must not turn a filtered turn into silence.

    Every locale gets ITS phrase — the runtime-output-language doctrine holds
    for the fallback too.
    """
    monkeypatch.setattr(pipeline_module, "scrub_for_voice", _fake_scrub)
    spoken = await _spoken_sentences(
        [f"{_POISON} eins. ", f"{_POISON} zwei."], lang
    )
    assert spoken == [FALLBACK_PHRASES[lang]]


@pytest.mark.asyncio
async def test_healthy_turn_never_gets_a_fallback_appended(monkeypatch) -> None:
    monkeypatch.setattr(pipeline_module, "scrub_for_voice", _fake_scrub)
    spoken = await _spoken_sentences(
        ["Das ist gut. ", "Das ist besser. ", "Fertig!"], "de"
    )
    assert spoken == ["Das ist gut.", "Das ist besser.", "Fertig!"]


@pytest.mark.asyncio
async def test_ordinal_answer_reaches_tts_as_one_utterance(monkeypatch) -> None:
    """OF-12 through the real producer, not just the splitter helper."""
    monkeypatch.setattr(pipeline_module, "scrub_for_voice", _fake_scrub)
    spoken = await _spoken_sentences(
        ["Am ", "1. ", "Jan", "uar ", "geht ", "es ", "los. ", "Dann ", "ruhe."],
        "de",
    )
    assert spoken == ["Am 1. Januar geht es los.", "Dann ruhe."]
