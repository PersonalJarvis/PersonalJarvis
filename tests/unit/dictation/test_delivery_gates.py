"""What a dictation is allowed to put into a document, and how long it lasted.

Four gates guard the last step of a dictation, and every one of them exists
because the live history of 2026-07-28 shows what happens without it:

* **Silence boilerplate.** 12 of the first 26 rows are "Thank you." or "Thank
  you for watching!" — a Whisper-family model answering a near-silent
  microphone. The repo has owned the marker list for months and the voice lane
  filters on it in four places; the dictation lane did not.
* **A transcript with no words.** A bare "." was pasted into a live document
  and the clipboard was restored over it, costing the user both the stray
  character and whatever they had copied.
* **The language a transcript is cleaned in.** A provider tag of "English" on
  German speech used to win over the text itself, and the cleanup then ran the
  English rules over German words.
* **How long the user spoke.** The wall clock ran from before the microphone
  opened until after the last provider retry, so every stored duration — and
  every words-per-minute figure derived from it — was inflated by whatever the
  network did that day.

The delivery half is tested directly through ``_finish_dictation``, which needs
no microphone; the duration is tested through a whole ``_dictation_session``
with a fake microphone and a deliberately slow provider, because the point of
that fix is precisely that provider latency no longer reaches the number.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.core.config import DictationConfig
from jarvis.core.events import DictationCompleted, DictationTranscript
from jarvis.dictation.insert import InsertResult, insert_text
from jarvis.speech.pipeline import SpeechPipeline, _is_silence_hallucination

# 16 kHz mono int16 — the capture contract every dictation records under.
BYTES_PER_SECOND = 16_000 * 2

# German fixtures. A transcript reproduces the speaker's own words, so the
# dictated German IS the thing under test here (CLAUDE.md §1, list #4).
DE_SENTENCE = "Er hat gesagt, dass er das Dokument gleich schickt."  # i18n-allow: fixture
DE_THANKS = "Vielen Dank für das Update"  # i18n-allow: fixture


def _pipeline(cfg: DictationConfig | None = None):
    """A pipeline with only the delivery half wired, and no disk anywhere."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._dictation_cfg = cfg or DictationConfig(history_enabled=False)
    events: list[object] = []
    inserted: list[str] = []

    async def _publish(event: object) -> None:
        events.append(event)

    pipe._publish_event = _publish  # type: ignore[assignment]
    pipe._insert_dictation = lambda text: (  # type: ignore[assignment]
        inserted.append(text)
        or InsertResult(
            status="inserted", detail="", clipboard_holds_text=False,
            method="clipboard+ctrl_v",
        )
    )
    return pipe, events, inserted


def _completed(events: list[object]) -> DictationCompleted:
    return next(e for e in events if isinstance(e, DictationCompleted))


def _final_transcript(events: list[object]) -> DictationTranscript:
    return next(
        e for e in events if isinstance(e, DictationTranscript) and e.is_final
    )


# --------------------------------------------------------------------------
# The silence hallucination — the whole utterance, and only on short audio
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_short_thank_you_for_watching_is_empty_not_inserted() -> None:
    """The exact shape that filled the live history: a second and a half of
    near-silence, and a video outro nobody said."""
    pipe, events, inserted = _pipeline()
    text = await pipe._finish_dictation(
        raw_text="Thank you for watching!",
        language="en",
        duration_s=1.4,
        target="insert",
        hung_up=False,
    )
    assert text == ""
    assert inserted == []
    completed = _completed(events)
    assert completed.outcome == "empty"
    # The raw transcript is kept: the history row must still show what the
    # provider actually returned, or the rejection is unauditable.
    assert completed.raw_text == "Thank you for watching!"
    assert completed.detail.strip(), "an unexplained empty result is the old bug"


@pytest.mark.asyncio
async def test_the_rejected_text_never_reaches_the_composer_either() -> None:
    """Skipping only the INSERTION would leave the same words sitting in the
    chat composer and on the bar — the identical defect in another window."""
    pipe, events, _inserted = _pipeline()
    await pipe._finish_dictation(
        raw_text="Thank you.", language="en", duration_s=0.9,
        target="chat", hung_up=False,
    )
    assert _final_transcript(events).text == ""
    assert _completed(events).outcome == "empty"


@pytest.mark.asyncio
async def test_a_genuinely_spoken_thank_you_is_delivered() -> None:
    """Five seconds of audio is a person speaking. The filter must stand down,
    or it becomes a new way of losing text."""
    pipe, events, inserted = _pipeline()
    text = await pipe._finish_dictation(
        raw_text="thank you very much for the update",
        language="en",
        duration_s=5.0,
        target="insert",
        hung_up=False,
    )
    assert text == "thank you very much for the update"
    assert inserted == ["thank you very much for the update"]
    assert _completed(events).outcome == "inserted"


@pytest.mark.asyncio
async def test_a_short_sentence_that_merely_contains_a_marker_survives() -> None:
    """``DE_THANKS`` opens with a marker phrase and is still a real sentence.

    Two seconds is inside the duration gate, so the ONLY thing keeping this text
    is the whole-utterance rule — which is why it is pinned separately.
    """
    pipe, events, inserted = _pipeline()
    await pipe._finish_dictation(
        raw_text=DE_THANKS,
        language="de",
        duration_s=2.0,
        target="insert",
        hung_up=False,
    )
    assert inserted == [DE_THANKS]
    assert _completed(events).outcome == "inserted"


@pytest.mark.parametrize(
    "text",
    [
        "Thank you.",
        "Thank you for watching!",
        "thanks for watching",
        "please subscribe",
        "Untertitelung des ZDF, 2020",  # i18n-allow: Whisper-noise vocabulary
    ],
)
def test_known_boilerplate_is_recognised_on_short_audio(text: str) -> None:
    assert _is_silence_hallucination(text, 1.2) is True


@pytest.mark.parametrize(
    ("text", "duration_s"),
    [
        # Long enough to be speech, whatever it says.
        ("Thank you for watching!", 4.0),
        # Short, but the boilerplate is not the whole utterance.
        ("thank you very much for the update", 1.0),
        (DE_THANKS, 1.0),
        ("check www.example.com for the numbers", 1.0),
        ("", 1.0),
    ],
)
def test_everything_else_is_left_alone(text: str, duration_s: float) -> None:
    assert _is_silence_hallucination(text, duration_s) is False


# --------------------------------------------------------------------------
# Nothing without words gets delivered
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_bare_full_stop_is_empty_not_inserted() -> None:
    """History row 2026-07-28T18:10:30: ``raw_text "."``, ``word_count 0``,
    ``outcome "inserted"`` — pasted into a document, then buried under the
    restored clipboard."""
    pipe, events, inserted = _pipeline()
    text = await pipe._finish_dictation(
        raw_text=".", language="en", duration_s=3.0, target="insert",
        hung_up=False,
    )
    assert text == ""
    assert inserted == []
    completed = _completed(events)
    assert completed.outcome == "empty"
    assert completed.detail.strip()


@pytest.mark.asyncio
async def test_punctuation_only_output_is_refused_at_any_duration() -> None:
    """Unlike the hallucination filter this gate has no duration condition:
    a transcript with no words is worthless however long the recording was."""
    pipe, events, inserted = _pipeline()
    await pipe._finish_dictation(
        raw_text="... ,", language="en", duration_s=42.0, target="insert",
        hung_up=False,
    )
    assert inserted == []
    assert _completed(events).outcome == "empty"


@pytest.mark.asyncio
async def test_one_real_word_is_still_delivered() -> None:
    """The floor is "no words at all", not "not enough words"."""
    pipe, _events, inserted = _pipeline()
    await pipe._finish_dictation(
        raw_text="Yes.", language="en", duration_s=0.8, target="insert",
        hung_up=False,
    )
    assert inserted == ["Yes."]


def test_insert_text_refuses_a_wordless_string_on_its_own() -> None:
    """Defence in depth: the pipeline gates first, but every other caller of
    ``insert_text`` — including the ones that do not exist yet — gets the same
    floor, and it must refuse BEFORE touching the clipboard."""
    result = insert_text(".")
    assert result.status == "unavailable"
    assert result.clipboard_holds_text is False


# --------------------------------------------------------------------------
# Which language the transcript is cleaned in
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_german_text_tagged_english_resolves_as_german() -> None:
    """The worst text defect this feature had.

    Groq and OpenAI report a language NAME on every request and report it
    confidently when it is wrong. ``normalize_language_tag("English")`` is "en",
    never "unknown", so the old text-based rescue — gated on an UNPARSEABLE tag
    — never ran, and the English cleanup rules were applied to German speech.
    """
    pipe, events, _inserted = _pipeline()
    await pipe._finish_dictation(
        raw_text=DE_SENTENCE,
        language="English",
        duration_s=4.0,
        target="chat",
        hung_up=False,
    )
    completed = _completed(events)
    assert completed.language == "de"
    # And the German words survive intact — the wrong language is only a defect
    # because of what the cleanup then does with it.
    assert completed.text == DE_SENTENCE


@pytest.mark.asyncio
async def test_a_user_pin_outranks_the_text() -> None:
    """The pin is the one signal a person can set. Overruling it with a
    detector would leave them no way to be right."""
    pipe, events, _inserted = _pipeline(
        DictationConfig(language="en", history_enabled=False)
    )
    await pipe._finish_dictation(
        raw_text=DE_SENTENCE,
        language="English",
        duration_s=4.0,
        target="chat",
        hung_up=False,
    )
    assert _completed(events).language == "en"


@pytest.mark.asyncio
async def test_a_tag_outside_de_en_es_is_kept_rather_than_coerced() -> None:
    """``detect_text_language`` only knows de/en/es, so letting it overrule a
    French tag would relabel that dictation as whichever of the three it scored
    highest on — and then run THAT language's filler rules over it. Keeping the
    tag makes the cleanup a documented no-op, which is the honest answer for
    ~95 of the 100 recognition languages.

    The fixture is chosen to make that concrete: this French sentence shares
    enough tokens with the Spanish vocabulary that the detector calls it "es",
    so a resolver that trusted the text here would clean French with Spanish
    rules.
    """
    french = "Il faut que je vous envoie le document avec les chiffres tout de suite."
    from jarvis.core.turn_language import detect_text_language

    assert detect_text_language(french) == "es", "fixture no longer bites"

    pipe, events, _inserted = _pipeline()
    await pipe._finish_dictation(
        raw_text=french,
        language="French",
        duration_s=4.0,
        target="chat",
        hung_up=False,
    )
    assert _completed(events).language == "French"


@pytest.mark.asyncio
async def test_a_silent_provider_still_gets_its_language_from_the_text() -> None:
    """The rescue that already shipped stays: with no tag at all the text is
    the only signal there is, and without it the cleanup answers every
    dictation with "no rules for this language"."""
    pipe, events, _inserted = _pipeline()
    await pipe._finish_dictation(
        raw_text=DE_SENTENCE,
        language="",
        duration_s=4.0,
        target="chat",
        hung_up=False,
    )
    assert _completed(events).language == "de"


@pytest.mark.asyncio
async def test_an_agreeing_provider_tag_is_left_untouched() -> None:
    pipe, events, _inserted = _pipeline()
    await pipe._finish_dictation(
        raw_text="hello there, this is the summary", language="en",
        duration_s=4.0, target="chat", hung_up=False,
    )
    assert _completed(events).language == "en"


# --------------------------------------------------------------------------
# duration_s comes from the audio, not from the clock
# --------------------------------------------------------------------------


class _FakeChunk:
    def __init__(self, pcm: bytes) -> None:
        self.pcm = pcm


class _FakeMic:
    """Yields one chunk and ends, which is what closes the session."""

    def __init__(self, pcm: bytes) -> None:
        self._pcm = pcm

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> bool:
        return False

    async def stream(self):
        yield _FakeChunk(self._pcm)


class _SlowSTT:
    """A provider that answers correctly, but takes its time doing it."""

    def __init__(self, delay_s: float) -> None:
        self._delay_s = delay_s

    async def transcribe_pcm(self, pcm: bytes, **_kw):
        await asyncio.sleep(self._delay_s)
        return SimpleNamespace(text="the recorded sentence", language="en")


def _session_pipeline(stt, cfg: DictationConfig):
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._utterance_stt = stt
    pipe._dictation_cfg = cfg
    pipe._dictation_target = "chat"
    pipe._ptt_partial_interval_s = 0.0
    pipe._dictation_max_s = 5.0
    pipe._input_device = None
    pipe._input_priority = None
    pipe._dictation_stop_event = asyncio.Event()
    pipe._hangup_event = asyncio.Event()
    events: list[object] = []

    async def _publish(event: object) -> None:
        events.append(event)

    pipe._publish_event = _publish  # type: ignore[assignment]
    return pipe, events


@pytest.mark.asyncio
async def test_duration_is_the_length_of_the_audio_not_of_the_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two seconds of speech stay two seconds however long the provider took.

    The clock used to start before the microphone opened and stop after the
    final transcription — retry sleeps included — so a slow or rate-limited
    provider inflated the stored duration by seconds. That number is what the
    statistics sidecar divides the word count by, so it silently under-reported
    the user's words per minute and stretched every streak built on it.
    """
    import jarvis.speech.pipeline as pipeline_mod

    pcm = b"\x00\x01" * 32_000  # 64 000 bytes = exactly 2.0 s
    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda **_kw: _FakeMic(pcm)
    )
    pipe, events = _session_pipeline(
        _SlowSTT(0.35),
        DictationConfig(partial_interval_s=0.0, history_enabled=False),
    )
    await pipe._dictation_session()

    completed = _completed(events)
    # The transcript really did travel the slow path — without this the
    # duration assertion could pass on a session that transcribed nothing.
    assert completed.text == "the recorded sentence"
    assert completed.duration_s == pytest.approx(len(pcm) / BYTES_PER_SECOND)
    assert completed.duration_s == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_a_recording_that_captured_nothing_reports_no_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No audio means no dictation. Reporting the seconds the machinery spent
    failing would put a duration on a row that holds no speech."""
    import jarvis.speech.pipeline as pipeline_mod

    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda **_kw: _FakeMic(b"")
    )
    pipe, events = _session_pipeline(
        _SlowSTT(0.05),
        DictationConfig(partial_interval_s=0.0, history_enabled=False),
    )
    await pipe._dictation_session()
    assert _completed(events).duration_s == pytest.approx(0.0)
