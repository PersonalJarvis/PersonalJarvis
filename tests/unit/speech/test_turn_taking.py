from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import TranscriptFinal, TranscriptionUpdate
from jarvis.core.protocols import AudioChunk, Transcript
from jarvis.speech.pipeline import (
    _TIMEOUT_NO_ANSWER_PHRASE,
    SpeechPipeline,
    TurnTakingState,
)


@dataclass
class FakeSTT:
    delay_s: float = 0.0
    text: str = "Mach das Licht an"

    async def transcribe_pcm(self, _pcm: bytes) -> Transcript:
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return Transcript(
            text=self.text,
            language="de",
            confidence=0.9,
            is_partial=False,
        )


class FakeTTS:
    async def synthesize(
        self,
        text: str,
        language_code: str | None = None,
    ):
        if False:  # pragma: no cover
            yield AudioChunk(pcm=b"", sample_rate=24_000, timestamp_ns=0)


class SlowPlayer:
    def __init__(self) -> None:
        self.completed = False
        self.cancelled = False
        self.stop_calls = 0

    async def play_chunks(self, _chunks) -> None:
        try:
            await asyncio.sleep(0.05)
            self.completed = True
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    def stop(self) -> None:
        self.stop_calls += 1


def _make_pipeline(
    stt: FakeSTT,
    bus: EventBus | None = None,
    brain_response: str = "",
    speak_barged: bool = False,
    continue_listening_after_response: bool = False,
) -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._stt = stt
    pipe._utterance_stt = stt
    pipe._stt_final_timeout_s = 0.05
    pipe._brain_timeout_s = 5.0
    pipe._pending_context_flush_s = 4.0
    pipe._pending_flush_task = None
    pipe._hangup_event = asyncio.Event()
    pipe._bus = bus
    pipe._supervisor = None
    pipe._vision_provider = None
    pipe._config = None
    pipe._turn_state = TurnTakingState.LISTENING
    pipe._pending_user_context = []
    pipe._last_endpoint_reason = None
    pipe._session_has_assistant_spoken = False
    pipe._player = None  # no audio in unit tests; _emit_completeness_signal is safe with None
    pipe._continue_listening_after_response = continue_listening_after_response
    pipe._session_end_reason = None
    pipe._spawn_watchdog_tasks = []
    pipe._spoken: list[tuple[str, str | None]] = []

    async def _brain(_text: str, _lang: str) -> str:
        return brain_response

    async def _speak(
        text: str, language: str | None = None, *, kind: str = "reply"
    ) -> bool:
        pipe._spoken.append((text, language))
        return speak_barged

    pipe._brain_with_ack = _brain  # type: ignore[method-assign]
    pipe._speak = _speak  # type: ignore[method-assign]
    return pipe


@pytest.mark.asyncio
async def test_speak_does_not_cancel_playback_when_barge_monitor_returns_false() -> None:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    player = SlowPlayer()
    pipe._tts = FakeTTS()
    pipe._player = player
    pipe._post_tts_listen_suppression_s = 0.0
    pipe._input_suppressed_until_ns = 0

    async def _barge_monitor() -> bool:
        return False

    pipe._barge_monitor = _barge_monitor  # type: ignore[method-assign]

    barged = await pipe._speak("Hallo Ruben.", language="de")

    assert barged is False
    assert player.completed is True
    assert player.cancelled is False
    assert player.stop_calls == 0


@pytest.mark.asyncio
async def test_missing_final_transcript_timeout_resets_to_listening() -> None:
    pipe = _make_pipeline(FakeSTT(delay_s=1.0))

    keep_session = await pipe._handle_utterance(b"\x00\x00" * 1024)

    assert keep_session is True
    assert pipe._turn_state == TurnTakingState.LISTENING


@pytest.mark.asyncio
async def test_final_transcript_is_published_before_processing() -> None:
    bus = EventBus()
    events: list[TranscriptFinal] = []

    async def _record(event: TranscriptFinal) -> None:
        events.append(event)

    bus.subscribe(TranscriptFinal, _record)
    pipe = _make_pipeline(FakeSTT(text="Oeffne Chrome"), bus)

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is True
    assert len(events) == 1
    assert events[0].transcript is not None
    assert events[0].transcript.text == "Oeffne Chrome"
    assert pipe._turn_state == TurnTakingState.LISTENING


@pytest.mark.asyncio
async def test_zdf_subtitle_hallucination_does_not_publish_final_transcription_update() -> None:
    bus = EventBus()
    transcription_updates: list[TranscriptionUpdate] = []

    async def _record_update(event: TranscriptionUpdate) -> None:
        transcription_updates.append(event)

    bus.subscribe(TranscriptionUpdate, _record_update)
    pipe = _make_pipeline(FakeSTT(text="Untertitelung des ZDF, 2020"), bus)

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is True
    assert transcription_updates == []
    assert pipe._turn_state == TurnTakingState.LISTENING


@pytest.mark.asyncio
async def test_turn_state_mapping_preserves_ui_supervisor_states() -> None:
    assert SpeechPipeline._supervisor_state_for_turn(TurnTakingState.IDLE) == "IDLE"
    assert SpeechPipeline._supervisor_state_for_turn(TurnTakingState.LISTENING) == "LISTENING"
    assert SpeechPipeline._supervisor_state_for_turn(TurnTakingState.USER_SPEAKING) == "LISTENING"
    assert (
        SpeechPipeline._supervisor_state_for_turn(
            TurnTakingState.WAITING_FOR_FINAL_TRANSCRIPT
        )
        == "LISTENING"
    )
    assert SpeechPipeline._supervisor_state_for_turn(TurnTakingState.PROCESSING) == "THINKING"
    assert (
        SpeechPipeline._supervisor_state_for_turn(TurnTakingState.JARVIS_SPEAKING)
        == "SPEAKING"
    )


@pytest.mark.asyncio
async def test_wellbeing_prompt_gets_voice_fallback_when_brain_returns_filler() -> None:
    # Production-Default since 2026-05-18 (single-turn-per-wake): the
    # wellbeing-fallback phrase still fires AND the session ends with
    # hangup_reason=turn_complete so the next turn requires a fresh
    # "Hey Jarvis" wake. ``_make_pipeline`` defaults to
    # ``continue_listening_after_response=False`` to match production.
    pipe = _make_pipeline(
        FakeSTT(text="Wie geht es dir Jarvis"),
        brain_response="Ich bin einsatzbereit.",
        continue_listening_after_response=False,
    )

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is False
    assert pipe._spoken == [("Mir geht's gut, Ruben. Was machen wir als Naechstes?", "de")]
    assert pipe._turn_state == TurnTakingState.IDLE
    assert pipe._session_end_reason == "turn_complete"


@pytest.mark.asyncio
async def test_single_turn_mode_ends_session_after_response() -> None:
    # User mandate 2026-05-18: every voice turn must end after the response
    # so the next turn requires a fresh "Hey Jarvis". If this test fails the
    # bug is back -- Jarvis stays in LISTENING and the open mic catches
    # background conversation without a wake word.
    pipe = _make_pipeline(
        FakeSTT(text="Wie spaet ist es"),
        brain_response="Es ist kurz nach drei.",  # i18n-allow
        continue_listening_after_response=False,
    )

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is False
    assert pipe._spoken == [("Es ist kurz nach drei.", "de")]  # i18n-allow
    assert pipe._turn_state == TurnTakingState.IDLE
    assert pipe._session_end_reason == "turn_complete"


@pytest.mark.asyncio
async def test_conversation_mode_keeps_session_listening_when_opted_in() -> None:
    # Opt-in legacy path: when [trigger].single_turn_mode = false in
    # jarvis.toml, the desktop launcher wires
    # ``continue_listening_after_response=True`` and the pipeline keeps the
    # mic open after the response. Idle-timeout / hangup-regex / hotkey are
    # then the only ways out -- same semantics as 2026-05-05 production.
    pipe = _make_pipeline(
        FakeSTT(text="Wie spaet ist es"),
        brain_response="Es ist kurz nach drei.",  # i18n-allow
        continue_listening_after_response=True,
    )

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is True
    assert pipe._spoken == [("Es ist kurz nach drei.", "de")]  # i18n-allow
    assert pipe._turn_state == TurnTakingState.LISTENING
    assert pipe._session_end_reason is None


@pytest.mark.asyncio
async def test_barge_in_keeps_session_listening() -> None:
    pipe = _make_pipeline(
        FakeSTT(text="Erklaer das"),
        brain_response="Das ist eine laengere Antwort.",  # i18n-allow
        speak_barged=True,
    )

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is True
    assert pipe._turn_state == TurnTakingState.LISTENING
    assert pipe._session_end_reason is None


@pytest.mark.asyncio
async def test_continuous_response_mode_can_keep_listening() -> None:
    pipe = _make_pipeline(
        FakeSTT(text="Erzaehl weiter"),  # i18n-allow
        brain_response="Gerne.",
        continue_listening_after_response=True,
    )

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is True
    assert pipe._turn_state == TurnTakingState.LISTENING
    assert pipe._session_end_reason is None


@pytest.mark.xfail(
    reason=(
        "Superseded by the user-approved 'incomplete-prompt completion buffer' "
        "design (docs/superpowers/specs/2026-05-25-incomplete-prompt-completion-"
        "design.md). The functional intent — incomplete fragment is buffered, "
        "brain not called, no TTS — is covered by test_pipeline_completion.py "
        "(::test_incomplete_text_buffers_and_returns_none and siblings). This "
        "test still asserts the parallel session's data model "
        "(_pending_user_context list, LISTENING after buffering); leaving it as "
        "xfail keeps the original parallel-design intent visible in git."
    ),
    strict=False,
)
@pytest.mark.asyncio
async def test_incomplete_context_is_buffered_instead_of_calling_brain() -> None:
    pipe = _make_pipeline(FakeSTT(text="Jarvis wenn"))

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is True
    assert pipe._pending_user_context == ["Jarvis wenn"]
    assert pipe._spoken == []
    assert pipe._turn_state == TurnTakingState.LISTENING


# Behavior reversal (2026-05-25, stay-on-when-unsure): a bare "Vielen Dank" used
# to be an instant regex hangup (a Whisper mis-hearing of short "auflegen"). It
# is now ambiguous and handed to the brain, which decides via the [[END_CALL]]
# sentinel — see test_polite_thanks_no_longer_auto_hangs_up below.


@pytest.mark.asyncio
async def test_hangup_accepts_split_auf_leg_transcript() -> None:
    pipe = _make_pipeline(FakeSTT(text="auf leg"))

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is False
    assert pipe._spoken == []


@pytest.mark.asyncio
async def test_hangup_accepts_lets_get_up_mistranscript() -> None:
    pipe = _make_pipeline(FakeSTT(text="Let's get up."))

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is False
    assert pipe._spoken == []


@pytest.mark.xfail(
    reason=(
        "Superseded by the user-approved 'incomplete-prompt completion buffer' "
        "design which FLUSHES the buffer to a short spoken follow-up cue on "
        "timeout (AD-OE6 zero-silent-drops) instead of silently discarding. "
        "The brain-not-called intent is preserved by the new design and pinned "
        "by test_pipeline_completion.py::test_timeout_fires_and_speaks_fallback_"
        "in_german (no _brain_with_ack call, but _speak IS called)."
    ),
    strict=False,
)
@pytest.mark.asyncio
async def test_pending_buffer_discard_after_timeout_does_not_flush_to_brain() -> None:
    """The discard timer must CLEAR the buffer — NOT send the half-command to the brain.

    This replaces the old auto-flush-to-brain test.  The old behaviour
    (flushing 'Jarvis wenn' to the brain after a timeout) was the core bug
    fixed by the utterance-completeness classifier (spec §2 /
    2026-05-25-utterance-completeness-design.md): a half-command must NEVER
    reach the brain.  The user-visible signal is now an earcon / spoken cue,
    not a brain call.  The buffer is simply discarded.

    See also: tests/unit/speech/test_pipeline_completeness.py::
    test_discard_timer_does_not_flush_to_brain (the authoritative regression guard).
    """
    pipe = _make_pipeline(FakeSTT(text="Jarvis wenn"), brain_response="Antwort.")
    pipe._pending_context_flush_s = 0.05  # speed up the test

    brain_calls: list[str] = []

    async def _fake_brain(text: str, lang: str) -> str:
        brain_calls.append(text)
        return "Antwort."

    pipe._brain_with_ack = _fake_brain  # type: ignore[method-assign]

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)
    assert keep_session is True
    assert pipe._pending_user_context == ["Jarvis wenn"]

    # Wait long enough for the discard timer to fire.
    await asyncio.sleep(0.2)

    # Buffer must be cleared …
    assert pipe._pending_user_context == []
    # … but the brain must NOT have been called with the half-command.
    assert brain_calls == [], (
        f"Brain was called with {brain_calls!r} — the discard timer flushed a half-command to the brain"
    )


@pytest.mark.asyncio
async def test_complete_question_with_kannst_du_reaches_brain() -> None:
    """Regression guard: 'Kannst du das fixen' must hit the brain.

    Earlier the heuristic flagged any sentence ending on a 'kannst du' /
    'can you' starter as incomplete, trapping the pipeline in silent
    LISTENING and producing the user-reported "thinks but never replies".
    """
    pipe = _make_pipeline(
        FakeSTT(text="Kannst du das fixen"),
        brain_response="Ja, ich kümmere mich darum.",  # i18n-allow
        continue_listening_after_response=True,
    )

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is True
    assert pipe._pending_user_context == []
    assert pipe._spoken  # brain produced a reply that was spoken
    assert pipe._spoken[0][0] == "Ja, ich kümmere mich darum."  # i18n-allow


@pytest.mark.asyncio
async def test_brain_call_timeout_returns_to_listening_without_hanging() -> None:
    """A stalled brain provider must not freeze the turn-taking state, and must
    SPEAK a fallback instead of a silent hangup.

    Without the asyncio.wait_for guard around `_brain_with_ack`, an upstream
    stall (Gemini hang, OAuth refresh) leaves the pipeline forever in
    PROCESSING — exactly the user-reported "Jarvis stopped thinking" symptom.
    Live bug 2026-05-29 ("Claude Code öffnen" stalled → silent hangup) extended  # i18n-allow
    the AD-OE6 zero-silent-drop contract to the timeout path: the turn now
    returns to LISTENING AND speaks a short "took too long" fallback (was
    previously silent — see test_total_brain_failure for the same contract).
    """
    pipe = _make_pipeline(FakeSTT(text="Mach das Licht an"))
    pipe._brain_timeout_s = 0.05

    async def _slow_brain(_text: str, _lang: str) -> str:
        await asyncio.sleep(1.0)
        return "ignored"

    pipe._brain_with_ack = _slow_brain  # type: ignore[method-assign]

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is True
    assert pipe._turn_state == TurnTakingState.LISTENING
    # AD-OE6: a stall must be spoken, not a silent drop. Assert against the
    # actual phrase table (not a hardcoded word) so a future reword of the
    # timeout phrase can't silently re-break this contract test.
    assert pipe._spoken, "brain timeout stayed silent (AD-OE6 violation)"
    # A bare provider stall (non-stream total cap, no tool evidence) honestly
    # admits it couldn't find the answer — not the vague "took too long".
    assert pipe._spoken[0][0] in _TIMEOUT_NO_ANSWER_PHRASE.values(), (
        f"expected a no-answer timeout fallback phrase, got {pipe._spoken[0][0]!r}"
    )


# ---------------------------------------------------------------------------
# Total brain-provider failure must not be silent (AD-OE6 / BUG-020 4th
# recurrence, 2026-05-24). When the whole provider chain is exhausted
# (e.g. Gemini 429 "credits depleted" + grok 403 + claude-api 401 + openai
# no-key), the voice path used to drop back to LISTENING without a word.
# Jarvis must speak a short honest fallback -- but ONLY on a real failure,
# never on a legitimate suppress_response empty (fire-and-forget spawn).
# ---------------------------------------------------------------------------


class _FailedBrain:
    """Stand-in for BrainManager after a total provider-chain failure."""

    _last_turn_all_failed = True
    _last_turn_suppressed = False

    async def __call__(self, _text: str) -> str:  # pragma: no cover - unused here
        return ""


class _SuppressBrain:
    """Stand-in for BrainManager after a legitimate suppress_response turn.

    A fire-and-forget ``spawn_worker`` sets ``_last_turn_suppressed`` so the
    pipeline knows the empty text is intentional (bus reports back) and stays
    silent — instead of speaking a clarifying question on top of it.
    """

    _last_turn_all_failed = False
    _last_turn_suppressed = True

    async def __call__(self, _text: str) -> str:  # pragma: no cover - unused here
        return ""


def _make_streaming_pipeline(
    stt: FakeSTT,
    *,
    stream_chunks: list[str],
    all_failed: bool,
    suppressed: bool = False,
) -> SpeechPipeline:
    """Like ``_make_pipeline`` but with the streaming-TTS path enabled.

    The live voice path uses ``_brain_streaming`` (performance.streaming_tts =
    true), so the silent-on-total-failure bug must be reproduced there too.
    """
    pipe = _make_pipeline(stt)
    pipe._latency_tracker = None

    class _Perf:
        streaming_tts = True

    class _Cfg:
        performance = _Perf()

    pipe._config = _Cfg()

    # The streaming play path is ``self._tts.synthesize(sentence)`` -> chunks ->
    # ``self._player.play_chunks(_merged_chunks())`` — NOT ``_speak``. A unit pipe
    # has ``_player = None`` and no ``_tts``, so that path raised AttributeError,
    # which derailed the total-failure fallback and hung the sentinel test.
    # Capturing fakes run the path deterministically: every synthesized sentence
    # is recorded on ``pipe._synthesized`` (assert spoken text there — the
    # streaming path bypasses ``_spoken``); the player just drains the chunks.
    synthesized: list[str] = []

    class _CapturingTTS:
        async def synthesize(self, text: str, language_code: str | None = None):
            synthesized.append(text)
            yield AudioChunk(pcm=b"\x00\x00", sample_rate=24_000, timestamp_ns=0)

    class _DrainPlayer:
        def __init__(self) -> None:
            self.stop_calls = 0

        async def play_chunks(self, chunks) -> None:
            async for _ in chunks:
                pass

        def stop(self) -> None:
            self.stop_calls += 1

    pipe._tts = _CapturingTTS()
    pipe._player = _DrainPlayer()
    pipe._synthesized = synthesized

    class _StreamBrain:
        _last_turn_all_failed = all_failed
        _last_turn_suppressed = suppressed

        async def generate_stream(self, _text: str):
            for chunk in stream_chunks:
                yield chunk

    pipe._brain = _StreamBrain()
    return pipe


@pytest.mark.asyncio
async def test_total_brain_failure_speaks_fallback_instead_of_silence() -> None:
    pipe = _make_pipeline(FakeSTT(text="Wie spaet ist es"), brain_response="")
    pipe._brain = _FailedBrain()

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is True
    assert pipe._spoken, "Jarvis stayed silent on a total brain-provider failure (AD-OE6)"
    spoken = pipe._spoken[0][0].lower()
    assert "sprachmodell" in spoken or "language model" in spoken
    assert pipe._turn_state == TurnTakingState.LISTENING


@pytest.mark.asyncio
async def test_suppressed_empty_response_stays_silent() -> None:
    # Fire-and-forget spawn_worker returns empty by design; speaking a
    # "providers are down" phrase here would be a false alarm on every spawn.
    pipe = _make_pipeline(FakeSTT(text="Wie spaet ist es"), brain_response="")
    pipe._brain = _SuppressBrain()

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is True
    assert pipe._spoken == []
    assert pipe._turn_state == TurnTakingState.LISTENING


@pytest.mark.asyncio
async def test_streaming_total_failure_speaks_fallback() -> None:
    # The production voice path is streaming. An exhausted provider chain
    # yields zero chunks -> the streamed answer is empty -> must still speak.
    pipe = _make_streaming_pipeline(
        FakeSTT(text="Wie spaet ist es"),
        stream_chunks=[],
        all_failed=True,
    )

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is True
    assert pipe._spoken, "Streaming voice path stayed silent on total brain failure"
    spoken = pipe._spoken[0][0].lower()
    assert "sprachmodell" in spoken or "language model" in spoken


@pytest.mark.asyncio
async def test_streaming_suppressed_empty_stays_silent() -> None:
    pipe = _make_streaming_pipeline(
        FakeSTT(text="Wie spaet ist es"),
        stream_chunks=[],
        all_failed=False,
        suppressed=True,  # fire-and-forget spawn → silence is correct
    )

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is True
    assert pipe._spoken == []


@pytest.mark.asyncio
async def test_streaming_empty_non_suppressed_speaks_clarifying_question() -> None:
    # The dominant live "Jarvis antwortet nie" case (logs 2026-06-08): a
    # conversational turn made the router brain emit a function_call / empty
    # content — NOT a spawn, NOT a total failure — and the turn ended mute.
    # The pipeline must now speak a short clarifying question instead of
    # dropping the user into silence (AD-OE6).
    pipe = _make_streaming_pipeline(
        FakeSTT(text="Das sind fuer mich die naechsten Plaene"),  # i18n-allow
        stream_chunks=[],
        all_failed=False,
        suppressed=False,
    )

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is True
    assert pipe._spoken, "conversational empty turn stayed silent (the bug)"
    assert pipe._spoken[0][0].strip().endswith("?")
    assert pipe._turn_state == TurnTakingState.LISTENING


@pytest.mark.asyncio
async def test_streaming_long_tool_loop_speaks_real_answer_not_timeout() -> None:
    """Integration regression for the 2026-06-14 "Jarvis hangs up / that took
    too long" bug (data/jarvis_desktop.log 14:21 + 14:24, "weather in Melbourne").

    A NON-computer-use brain tool-use loop (geocoding + DuckDuckGo + open-meteo,
    ~20 s of real work) produces NO spoken sentence for longer than the
    no-first-frame TTS ceiling, while pinging ``on_progress`` on every tool-use-
    loop round — exactly what ``ToolUseLoop`` does (tool_use_loop.py:400). Before
    the fix, ``_await_playback`` only honoured the computer_use heartbeat, so the
    20 s ceiling beheaded the working turn: the answer was discarded and the
    empty-turn handler spoke a canned timeout fallback ("…zu lange gedauert…").

    This drives the REAL streaming path end-to-end (``_handle_utterance`` →
    ``_run_brain_with_stall_guard`` → ``_brain_streaming`` → ``_await_playback``)
    and pins both halves: the real answer reaches TTS, and the "took too long"
    fallback is NEVER spoken. It is the production-altitude guard the
    ``_await_playback`` unit test (test_speak_playback_timeout.py) complements.
    """
    pipe = _make_streaming_pipeline(
        FakeSTT(text="What's the weather in Melbourne"),
        stream_chunks=[],  # replaced by the custom brain below
        all_failed=False,
        suppressed=False,
    )
    # Tiny no-first-frame ceiling; the simulated tool loop runs ~4x longer. The
    # brain stall window stays generous so it is provably NOT the trigger here.
    pipe._speak_playback_ceiling_s = 0.2  # type: ignore[attr-defined]
    pipe._brain_timeout_s = 5.0  # type: ignore[attr-defined]
    # Production runs conversation mode (log: TURN-MODE=conversation), so a
    # completed answer returns to LISTENING rather than the one-shot hang-up.
    pipe._continue_listening_after_response = True  # type: ignore[attr-defined]

    answer = "It is eighteen degrees and sunny in Melbourne."

    class _SlowToolLoopBrain:
        _last_turn_all_failed = False
        _last_turn_suppressed = False
        _last_turn_executed_action_tool = False

        async def generate_stream(self, _text, on_progress=None):
            # ~0.8 s of tool-use-loop rounds: each pings the brain heartbeat
            # (on_progress) but yields NO text — the brain is fetching weather.
            for _ in range(16):
                if on_progress is not None:
                    on_progress()
                await asyncio.sleep(0.05)
            yield answer  # data is in → the brain finally narrates

    pipe._brain = _SlowToolLoopBrain()

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is True
    assert any("Melbourne" in t for t in pipe._synthesized), (
        "the working tool-loop answer was beheaded before reaching TTS"
    )
    spoken_blob = " ".join(t.lower() for t, _ in pipe._spoken)
    assert "zu lange" not in spoken_blob and "too long" not in spoken_blob, (
        f"timeout fallback wrongly spoken on a working turn: {pipe._spoken}"
    )
    assert pipe._turn_state == TurnTakingState.LISTENING


# ---------------------------------------------------------------------------
# BUG-018 (2026-05-11): STT-probe truncated real speech on low Whisper
# confidence. The probe's "empty tail" signal originally accepted three
# disjunctive conditions including `confidence < 0.55` alone — so a
# 15-character real-speech tail like "spawnen welcher" (a German relative
# pronoun at a thinking pause) with Whisper confidence 0.45 was treated as
# "user finished" and the endpoint was forced after only 160 ms of silence
# instead of the 1200 ms silence_ms budget. Regression guards below pin the
# corrected logic: confidence alone never ends a turn; only empty / very
# short / known-hallucination tails do.
# ---------------------------------------------------------------------------


class _StubVAD:
    def __init__(self) -> None:
        self.endpoint_requested = False

    def request_endpoint(self) -> None:
        self.endpoint_requested = True


def _make_probe_pipe(transcript: Transcript) -> tuple[SpeechPipeline, _StubVAD]:
    pipe = SpeechPipeline.__new__(SpeechPipeline)

    class _ConstSTT:
        async def transcribe_pcm(self, _pcm: bytes) -> Transcript:
            return transcript

    pipe._stt = _ConstSTT()
    pipe._probe_min_text_len = 4
    pipe._probe_min_confidence = 0.55
    pipe._probe_last_text = ""
    pipe._probe_stable_count = 0
    pipe._probe_required_stable = 1
    pipe._probe_empty_count = 0
    pipe._probe_required_empty = 2
    pipe._probe_in_flight = True  # mirrors real-world `_on_vad_probe` setup
    vad = _StubVAD()
    pipe._vad = vad  # type: ignore[assignment]
    return pipe, vad


@pytest.mark.asyncio
async def test_probe_does_not_force_endpoint_on_real_speech_with_low_confidence() -> None:
    """BUG-018 regression: real-speech tail with low Whisper confidence
    must not be classified as 'empty'.

    The exact production case: user says "Kannst du bitte einen Subagenten
    spawnen, welcher..." and pauses to think. Probe transcribes the last
    2 s as 'spawnen welcher' (15 chars, real speech, no hallucination
    pattern) with confidence 0.45 — naturally low because the relative
    pronoun ends without a follow-up clause. The probe must keep the turn
    open so the user can finish the sentence.
    """
    pipe, vad = _make_probe_pipe(
        Transcript(
            text="spawnen welcher",
            language="de",
            confidence=0.45,
            is_partial=False,
        )
    )

    await pipe._stt_probe_async(b"\x00\x00" * 512)

    assert vad.endpoint_requested is False
    assert pipe._probe_last_text == "spawnen welcher"


@pytest.mark.asyncio
async def test_probe_forces_endpoint_on_sustained_empty_tail() -> None:
    """Speaker-bleed protection still works: a SUSTAINED empty tail forces the
    endpoint. A single empty tail now defers (it is indistinguishable from a
    quiet mumble mid-speech — the "och ha..." cut, 2026-06-14); only the
    persistent empty run (real bleed) forces."""
    pipe, vad = _make_probe_pipe(
        Transcript(text="", language="de", confidence=0.0, is_partial=False)
    )

    # First empty probe defers (the user may still be speaking).
    await pipe._stt_probe_async(b"\x00\x00" * 512)
    assert vad.endpoint_requested is False
    # Sustained empty (real speaker bleed) → force.
    await pipe._stt_probe_async(b"\x00\x00" * 512)
    assert vad.endpoint_requested is True


@pytest.mark.asyncio
async def test_probe_forces_endpoint_on_known_hallucination_phrase() -> None:
    """Whisper-on-silence hallucinations ('Vielen Dank.', 'thanks for
    watching', …) match `_STT_HALLUCINATION_RE` and must still cut the
    turn — that was the original speaker-bleed motivation for the probe.

    Updated 2026-06-15: like the empty tail, a known-boilerplate tail now defers
    on the FIRST reading and forces only when it PERSISTS. A single such reading
    is indistinguishable from the user's live speech mis-decoded as boilerplate
    ('I would like you to' → 'i would like to thank you for your time.'), so the
    one-shot pre-speech force was removed. Sustained boilerplate (real bleed)
    still ends the turn.
    """
    pipe, vad = _make_probe_pipe(
        Transcript(
            text="vielen dank.",
            language="de",
            confidence=0.85,  # high confidence does not save it
            is_partial=False,
        )
    )

    # First boilerplate probe defers (could be hallucinated live speech).
    await pipe._stt_probe_async(b"\x00\x00" * 512)
    assert vad.endpoint_requested is False
    # Sustained boilerplate (real speaker bleed) → force.
    await pipe._stt_probe_async(b"\x00\x00" * 512)
    assert vad.endpoint_requested is True


@pytest.mark.asyncio
async def test_probe_forces_endpoint_on_stable_repeating_tail() -> None:
    """Signal 2 (stable repetition) is unchanged: when the same tail is
    transcribed twice in a row, force the endpoint. This is the safety
    net for genuine end-of-turn cases where the user has stopped talking
    but the regex did not match the tail content.
    """
    pipe, vad = _make_probe_pipe(
        Transcript(
            text="das war alles",
            language="de",
            confidence=0.9,
            is_partial=False,
        )
    )

    pipe._probe_in_flight = True
    await pipe._stt_probe_async(b"\x00\x00" * 512)
    assert vad.endpoint_requested is False  # first sighting, just remember

    pipe._probe_in_flight = True
    await pipe._stt_probe_async(b"\x00\x00" * 512)
    assert vad.endpoint_requested is True   # second identical hit → end


# ---------------------------------------------------------------------------
# Semantic hang-up: brain-emitted [[END_CALL]] sentinel + stay-on-when-unsure.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_brain_end_call_sentinel_hangs_up_and_is_not_spoken() -> None:
    # Conservative-but-clear dismissal: STT text is NOT an explicit regex
    # command, so the brain decides — and signals end via the sentinel.
    pipe = _make_pipeline(
        FakeSTT(text="Ich glaube wir sind durch"),
        brain_response="Bis später, Ruben. [[END_CALL]]",  # i18n-allow
        continue_listening_after_response=True,  # prove hangup overrides stay-open
    )

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is False
    assert pipe._spoken == [("Bis später, Ruben.", "de")]  # sentinel stripped  # i18n-allow
    assert pipe._session_end_reason == "voice_pattern"
    assert pipe._hangup_event.is_set()


@pytest.mark.asyncio
async def test_polite_thanks_no_longer_auto_hangs_up() -> None:
    # Regression for the old over-eager regex: a bare "Vielen Dank" used to
    # match HANGUP_RE and end the call. Now it reaches the brain, which (no
    # sentinel) keeps the conversation open. Realizes "stay on when unsure".
    pipe = _make_pipeline(
        FakeSTT(text="Vielen Dank"),
        brain_response="Gern geschehen, Ruben.",
        continue_listening_after_response=True,
    )

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is True
    assert pipe._session_end_reason is None
    assert pipe._hangup_event.is_set() is False


@pytest.mark.asyncio
async def test_explicit_auflegen_still_hard_hangs_up_via_regex() -> None:
    pipe = _make_pipeline(FakeSTT(text="Auflegen bitte"))
    pipe._player = SlowPlayer()

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is False
    assert pipe._hangup_event.is_set()
    assert pipe._player.stop_calls == 1  # "auflegen" stays an absolute kill switch


@pytest.mark.asyncio
async def test_brain_streaming_strips_sentinel_but_keeps_it_in_full_text() -> None:
    pipe = _make_streaming_pipeline(
        FakeSTT(text="x"),
        stream_chunks=["Alles erledigt. ", "Bis später, Ruben. ", "[[END_CALL]]"],  # i18n-allow
        all_failed=False,
    )

    full, _barged = await pipe._brain_streaming("x", "de")

    # The sentinel survives in the returned full text (so _handle_utterance can
    # read the hang-up intent) but is scrubbed out of every synthesized sentence.
    # The streaming path speaks via self._tts.synthesize -> self._player, so the
    # spoken text is captured on pipe._synthesized, NOT on _spoken.
    assert "[[END_CALL]]" in full
    assert all("[[END_CALL]]" not in t for t in pipe._synthesized)  # never spoken
    assert any("Bis später" in t for t in pipe._synthesized)  # i18n-allow


@pytest.mark.asyncio
async def test_brain_timeout_speaks_fallback_instead_of_silent_hangup() -> None:
    """AD-OE6: when the brain stalls past brain_timeout_s, the turn must SPEAK a
    graceful fallback, not drop back to LISTENING in silence.

    Live bug 2026-05-29: "kannst du Claude Code öffnen" stalled the Gemini  # i18n-allow
    stream; idle_timeout (30s) pre-empted brain_timeout (40s) and the turn hung
    up with zero feedback. The fix lowers brain_timeout below idle_timeout AND
    speaks on timeout — this pins the spoken-fallback half.
    """
    pipe = _make_pipeline(FakeSTT(text="Kanzlerin Cloud Code starten"))
    pipe._tool_executor = None  # skip the local-action fast path -> go to brain
    pipe._streaming_enabled = lambda: False  # exercise the _brain_with_ack path
    pipe._brain_timeout_s = 0.05

    async def _stalling_brain(_text: str, _lang: str) -> str:
        await asyncio.sleep(0.5)  # exceeds the 0.05s brain timeout
        return "never reached"

    pipe._brain_with_ack = _stalling_brain  # type: ignore[method-assign]

    keep_session = await pipe._handle_utterance(b"\x01\x00" * 1024)

    assert keep_session is True
    assert pipe._turn_state == TurnTakingState.LISTENING
    assert pipe._spoken, "brain timeout produced NO spoken feedback (silent drop — AD-OE6)"
