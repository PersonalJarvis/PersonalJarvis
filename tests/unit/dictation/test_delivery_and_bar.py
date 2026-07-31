"""Delivery of a finished dictation, and the bar's new coarse mode.

``_finish_dictation`` is the half of the dictation session that has nothing to
do with a microphone: clean, publish, insert, record. Testing it directly is
what makes the delivery contract verifiable without audio hardware.

The bar tests pin the promise made when the mode was added: the four existing
voice modes behave EXACTLY as before, and a click during dictation cannot start
a voice session.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.core.config import DictationConfig
from jarvis.core.events import DictationCompleted, DictationTranscript
from jarvis.dictation.insert import InsertResult
from jarvis.speech.pipeline import SpeechPipeline
from jarvis.ui.jarvisbar import interaction, renderer

# --------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------


def _pipeline(cfg: DictationConfig | None = None, *, insert: InsertResult | None = None):
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._dictation_cfg = cfg or DictationConfig(history_enabled=False)
    events: list[object] = []

    async def _publish(event: object) -> None:
        events.append(event)

    pipe._publish_event = _publish  # type: ignore[assignment]
    pipe._insert_dictation = lambda text: insert or InsertResult(  # type: ignore[assignment]
        status="inserted", detail="", clipboard_holds_text=False,
        method="clipboard+ctrl_v",
    )
    return pipe, events


@pytest.mark.asyncio
async def test_cleans_then_inserts() -> None:
    pipe, events = _pipeline()
    text = await pipe._finish_dictation(
        # i18n-allow: German fixture under test (§1 list #4)
        raw_text="Ähm, das ist äh wirklich gut.",  # i18n-allow
        language="de",
        duration_s=3.0,
        target="insert",
        hung_up=False,
    )
    assert text == "Das ist wirklich gut."  # i18n-allow: German fixture under test (§1 list #4)

    transcript = next(e for e in events if isinstance(e, DictationTranscript))
    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert transcript.is_final is True
    # i18n-allow: German fixture under test (§1 list #4)
    assert transcript.text == "Das ist wirklich gut."  # i18n-allow
    assert completed.outcome == "inserted"
    # i18n-allow: German fixture under test (§1 list #4)
    assert completed.raw_text == "Ähm, das ist äh wirklich gut."  # i18n-allow
    assert completed.removed_words == 2


@pytest.mark.asyncio
async def test_chat_target_never_inserts() -> None:
    """The chat composer's mic button must not type into other apps."""
    inserted: list[str] = []
    pipe, events = _pipeline()
    pipe._insert_dictation = lambda text: inserted.append(text)  # type: ignore[assignment]

    await pipe._finish_dictation(
        raw_text="hello there", language="en", duration_s=1.0,
        target="chat", hung_up=False,
    )
    assert inserted == []
    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert completed.outcome == "chat"


@pytest.mark.asyncio
async def test_blocked_insertion_surfaces_the_reason() -> None:
    blocked = InsertResult(
        status="clipboard_only",
        detail="The window in front is running as administrator.",
        clipboard_holds_text=True,
    )
    pipe, events = _pipeline(insert=blocked)
    await pipe._finish_dictation(
        raw_text="hello there", language="en", duration_s=1.0,
        target="insert", hung_up=False,
    )
    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert completed.outcome == "clipboard_only"
    assert "administrator" in completed.detail


@pytest.mark.asyncio
async def test_hangup_cancels_without_inserting() -> None:
    inserted: list[str] = []
    pipe, events = _pipeline()
    pipe._insert_dictation = lambda text: inserted.append(text)  # type: ignore[assignment]

    await pipe._finish_dictation(
        raw_text="", language="", duration_s=0.4, target="insert", hung_up=True,
    )
    assert inserted == []
    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert completed.outcome == "cancelled"


@pytest.mark.asyncio
async def test_empty_transcript_is_reported_as_empty() -> None:
    pipe, events = _pipeline()
    await pipe._finish_dictation(
        raw_text="", language="en", duration_s=0.2, target="insert", hung_up=False,
    )
    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert completed.outcome == "empty"


@pytest.mark.asyncio
async def test_cleanup_can_be_switched_off() -> None:
    pipe, _events = _pipeline(
        DictationConfig(remove_fillers=False, history_enabled=False)
    )
    text = await pipe._finish_dictation(
        raw_text="Um, hello there friend.", language="en", duration_s=1.0,
        target="chat", hung_up=False,
    )
    assert text == "Um, hello there friend."


@pytest.mark.asyncio
async def test_auto_target_is_resolved_at_DELIVERY_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Start in the app, switch to the target, speak — the switch must count.

    Resolving "auto" when recording STARTS would send that text to the chat box,
    because Jarvis was in front at the moment the button was clicked.
    """
    import jarvis.dictation.insert as insert_mod

    inserted: list[str] = []
    pipe, events = _pipeline()
    pipe._insert_dictation = lambda text: inserted.append(text) or InsertResult(  # type: ignore[assignment]
        status="inserted", detail="", clipboard_holds_text=False, method="clipboard+ctrl_v",
    )

    monkeypatch.setattr(insert_mod, "foreground_is_this_app", lambda: False)
    await pipe._finish_dictation(
        raw_text="into the other app", language="en", duration_s=1.0,
        target="auto", hung_up=False,
    )
    assert inserted == ["into the other app"]

    inserted.clear()
    monkeypatch.setattr(insert_mod, "foreground_is_this_app", lambda: True)
    await pipe._finish_dictation(
        raw_text="into our own box", language="en", duration_s=1.0,
        target="auto", hung_up=False,
    )
    assert inserted == []
    assert events[-1].outcome == "chat"


@pytest.mark.asyncio
async def test_final_transcript_says_which_route_it_took(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The UI cannot work this out for itself, and gets it wrong if it guesses.

    The same event fires on BOTH routes. A UI that inserted on every final
    transcript would take a dictation the backend is already pasting into
    another program and write it into whatever Jarvis field last had focus —
    invisibly, in a section nobody is looking at. So the resolved target rides
    along, and "auto" must never be what arrives: the UI cannot read a
    foreground window.
    """
    import jarvis.dictation.insert as insert_mod

    def _final(events: list[object]) -> DictationTranscript:
        return [
            e
            for e in events
            if isinstance(e, DictationTranscript) and e.is_final
        ][-1]

    monkeypatch.setattr(insert_mod, "foreground_is_this_app", lambda: False)
    pipe, events = _pipeline()
    await pipe._finish_dictation(
        raw_text="into the other app", language="en", duration_s=1.0,
        target="auto", hung_up=False,
    )
    assert _final(events).target == "insert"

    monkeypatch.setattr(insert_mod, "foreground_is_this_app", lambda: True)
    pipe, events = _pipeline()
    await pipe._finish_dictation(
        raw_text="into our own window", language="en", duration_s=1.0,
        target="auto", hung_up=False,
    )
    assert _final(events).target == "chat"


# --------------------------------------------------------------------------
# The failure signal — "the provider refused us" vs "you said nothing"
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_transcription_failure_is_reported_as_failed_not_empty() -> None:
    """Before this, a provider 401 and plain silence produced the identical
    ``empty`` outcome, so the one failure a user can actually fix was the one
    the app never mentioned."""
    pipe, events = _pipeline()
    await pipe._finish_dictation(
        raw_text="", language="", duration_s=1.0, target="insert", hung_up=False,
        stt_error="AuthenticationError: 401 invalid api key",
    )
    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert completed.outcome == "failed"
    # Normalised at the store: a caller handing over the provider's raw text
    # must not be able to put a stack-trace fragment in front of the user.
    assert completed.error == "bad_key"
    assert "401" not in (completed.error or ""), "raw provider text is back"
    # ``detail`` is what surfaces without a locale of their own (the bar, the
    # CLI) show verbatim, so a failed dictation must not leave it empty.
    assert completed.detail.strip()


@pytest.mark.asyncio
async def test_silence_without_an_error_is_still_empty() -> None:
    pipe, events = _pipeline()
    await pipe._finish_dictation(
        raw_text="", language="en", duration_s=0.2, target="insert", hung_up=False,
        stt_error=None,
    )
    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert completed.outcome == "empty"
    assert completed.error is None


@pytest.mark.asyncio
async def test_a_hangup_outranks_a_late_transcription_error() -> None:
    """The user cancelled; telling them it "failed" would be a lie."""
    pipe, events = _pipeline()
    await pipe._finish_dictation(
        raw_text="", language="", duration_s=0.4, target="insert", hung_up=True,
        stt_error="TimeoutError: provider timed out",
    )
    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert completed.outcome == "cancelled"


@pytest.mark.asyncio
async def test_text_that_did_arrive_is_still_delivered_after_a_flaky_segment() -> None:
    """One failed segment in an otherwise working dictation is not a failed
    dictation — the words the user got must still reach their text field."""
    inserted: list[str] = []
    pipe, events = _pipeline()
    pipe._insert_dictation = lambda text: inserted.append(text) or InsertResult(  # type: ignore[assignment]
        status="inserted", detail="", clipboard_holds_text=False,
        method="clipboard+ctrl_v",
    )
    await pipe._finish_dictation(
        raw_text="the part that came through", language="en", duration_s=2.0,
        target="insert", hung_up=False, stt_error="TimeoutError: one segment",
    )
    assert inserted == ["the part that came through"]
    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert completed.outcome == "inserted"


# --------------------------------------------------------------------------
# The pinned dictation language
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_pinned_language_outranks_the_provider_guess() -> None:
    """A provider that reports nothing (or the wrong thing) leaves the cleanup
    with reason="no_rules" — no cleanup at all — despite an explicit pin."""
    pipe, events = _pipeline(
        DictationConfig(language="de", history_enabled=False)
    )
    text = await pipe._finish_dictation(
        raw_text="Ähm, das ist äh wirklich gut.",  # i18n-allow: fixture (§1 #4)
        language="",  # the provider could not tell
        duration_s=3.0,
        target="chat",
        hung_up=False,
    )
    assert text == "Das ist wirklich gut."  # i18n-allow: German fixture under test (§1 list #4)
    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert completed.language == "de"


@pytest.mark.asyncio
async def test_auto_leaves_the_detected_language_alone() -> None:
    pipe, events = _pipeline(DictationConfig(history_enabled=False))
    await pipe._finish_dictation(
        raw_text="hello there", language="en", duration_s=1.0,
        target="chat", hung_up=False,
    )
    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert completed.language == "en"


# --------------------------------------------------------------------------
# The audio sidecar — written only when it buys back something lost
# --------------------------------------------------------------------------


class _FakeHistory:
    """Stands in for DictationHistory without touching the user's data dir."""

    instances: list = []

    def __init__(self, *_a, **_k) -> None:
        self.added: list[dict] = []
        self.updated: list[tuple[str, dict]] = []
        _FakeHistory.instances.append(self)

    @property
    def audio_dir(self):
        from pathlib import Path

        return Path("audio-dir-sentinel")

    def add(self, **fields):
        self.added.append(fields)
        return SimpleNamespace(id="entry-1")

    def update(self, entry_id: str, **fields):
        self.updated.append((entry_id, fields))
        return SimpleNamespace(id=entry_id)


@pytest.fixture
def audio_spy(monkeypatch: pytest.MonkeyPatch):
    """Capture every audio-sidecar call without writing a byte to disk."""
    import jarvis.dictation.audio as audio_mod
    import jarvis.dictation.history as history_mod

    saved: list[tuple[str, int]] = []
    pruned: list[dict] = []
    _FakeHistory.instances.clear()

    monkeypatch.setattr(history_mod, "DictationHistory", _FakeHistory)
    monkeypatch.setattr(
        audio_mod,
        "save_dictation_audio",
        lambda entry_id, pcm, **kw: (
            saved.append((entry_id, len(pcm))) or Path("saved.wav")
        ),
    )
    monkeypatch.setattr(
        audio_mod, "prune_audio", lambda **kw: pruned.append(kw) or 0
    )
    return SimpleNamespace(saved=saved, pruned=pruned)


@pytest.mark.asyncio
async def test_failed_audio_is_kept_and_linked_to_its_entry(audio_spy) -> None:
    pipe, _events = _pipeline(DictationConfig(keep_failed_audio=True))
    await pipe._finish_dictation(
        raw_text="", language="en", duration_s=2.0, target="insert", hung_up=False,
        stt_error="AuthenticationError: 401", audio=b"\x01\x02" * 100,
    )
    assert audio_spy.saved == [("entry-1", 200)]
    history = _FakeHistory.instances[-1]
    assert history.updated == [("entry-1", {"audio_path": "saved.wav"})]
    # Retention runs after the write, never before it.
    assert audio_spy.pruned and audio_spy.pruned[0]["max_files"] == 20


@pytest.mark.asyncio
async def test_a_successful_dictation_never_leaves_audio_behind(audio_spy) -> None:
    """Audio is the most sensitive thing this app stores. It is written on
    exactly one path: the user lost something AND allowed it."""
    pipe, _events = _pipeline(DictationConfig(keep_failed_audio=True))
    await pipe._finish_dictation(
        raw_text="this one worked", language="en", duration_s=2.0,
        target="chat", hung_up=False, audio=b"\x01\x02" * 100,
    )
    assert audio_spy.saved == []


@pytest.mark.asyncio
async def test_keep_failed_audio_off_writes_nothing(audio_spy) -> None:
    pipe, _events = _pipeline(DictationConfig(keep_failed_audio=False))
    await pipe._finish_dictation(
        raw_text="", language="en", duration_s=2.0, target="insert", hung_up=False,
        stt_error="AuthenticationError: 401", audio=b"\x01\x02" * 100,
    )
    assert audio_spy.saved == []
    # The history row is still written — that is what Restore needs a handle on.
    assert _FakeHistory.instances[-1].added


@pytest.mark.asyncio
async def test_a_wordless_failure_is_still_recorded(audio_spy) -> None:
    """The old guard skipped the history whenever there was no raw text, which
    is precisely when the worst failures happen."""
    pipe, _events = _pipeline(DictationConfig())
    await pipe._finish_dictation(
        raw_text="", language="en", duration_s=2.0, target="insert", hung_up=False,
        stt_error="RuntimeError: engine wedged",
    )
    added = _FakeHistory.instances[-1].added
    assert added and added[0]["outcome"] == "failed"
    # Stored as a reason code. This particular text matches no known shape, so
    # the honest answer is the catch-all rather than the raw exception string.
    assert added[0]["error"] == "unknown"


@pytest.mark.asyncio
async def test_history_disabled_writes_nothing_at_all(audio_spy) -> None:
    pipe, _events = _pipeline(DictationConfig(history_enabled=False))
    await pipe._finish_dictation(
        raw_text="", language="en", duration_s=2.0, target="insert", hung_up=False,
        stt_error="RuntimeError: engine wedged", audio=b"\x01\x02" * 100,
    )
    assert _FakeHistory.instances == []
    assert audio_spy.saved == []


@pytest.mark.asyncio
async def test_a_broken_history_write_never_costs_the_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import jarvis.dictation.history as history_mod

    class _BoomHistory:
        def __init__(self, *_a, **_k) -> None:
            raise RuntimeError("disk on fire")

    monkeypatch.setattr(history_mod, "DictationHistory", _BoomHistory)
    pipe, _events = _pipeline(DictationConfig())
    text = await pipe._finish_dictation(
        raw_text="the words survive", language="en", duration_s=1.0,
        target="chat", hung_up=False,
    )
    assert text == "the words survive"


@pytest.mark.asyncio
async def test_a_broken_cleanup_never_loses_the_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a, **_k):
        raise RuntimeError("rule exploded")

    monkeypatch.setattr("jarvis.dictation.cleanup.clean_transcript", _boom)
    pipe, _events = _pipeline()
    text = await pipe._finish_dictation(
        raw_text="the raw words", language="en", duration_s=1.0,
        target="chat", hung_up=False,
    )
    assert text == "the raw words"


# --------------------------------------------------------------------------
# The recording session — the half above the delivery contract
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


def _session_pipeline(stt, cfg: DictationConfig):
    """A pipeline wired for ``_dictation_session`` and nothing else."""
    import asyncio

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
async def test_a_language_pin_does_not_lock_a_code_switching_dictation(
    monkeypatch: pytest.MonkeyPatch, audio_spy
) -> None:
    import jarvis.speech.pipeline as pipeline_mod

    seen: list[dict] = []

    class _STT:
        async def transcribe_pcm(self, pcm: bytes, **kw):
            seen.append(kw)
            return SimpleNamespace(text="hola mundo", language="es")

    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda **_kw: _FakeMic(b"\x00\x01" * 16_000)
    )
    pipe, _events = _session_pipeline(
        _STT(), DictationConfig(language="es", partial_interval_s=0.0)
    )
    await pipe._dictation_session()
    assert seen == [{"language": "auto"}]


@pytest.mark.asyncio
async def test_completion_and_history_carry_stt_quality_telemetry(
    monkeypatch: pytest.MonkeyPatch, audio_spy
) -> None:
    import jarvis.speech.pipeline as pipeline_mod

    class _MeasuredSTT:
        name = "openai-api"
        _model = "gpt-4o-transcribe"

        async def transcribe_pcm(self, pcm: bytes, **_kw):
            return SimpleNamespace(
                text="hello 東京 equipo",  # i18n-allow: multilingual STT fixture
                language="en-US",
                segments=(
                    {"language": "ja-JP"},
                    {"language": "es-MX"},
                ),
            )

    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda **_kw: _FakeMic(b"\x00\x01" * 16_000)
    )
    pipe, events = _session_pipeline(
        _MeasuredSTT(), DictationConfig(partial_interval_s=0.0)
    )

    await pipe._dictation_session()

    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert completed.stt_providers == ("openai-api",)
    assert completed.stt_models == ("gpt-4o-transcribe",)
    assert completed.detected_languages == ("en", "ja", "es")
    assert completed.stt_calls == 1
    assert "final_pass:applied" in completed.stt_audit
    assert "audio_preprocessing:raw_pcm" in completed.stt_audit
    assert "acoustic_echo_cancellation:unavailable" in completed.stt_audit
    assert completed.audio_sample_rate_hz == 16_000
    assert completed.audio_rms == pytest.approx(1 / 128)
    assert completed.audio_clipping_ratio == 0.0
    assert completed.audio_dropouts == 0
    assert completed.audio_dropout_ms == 0
    stored = _FakeHistory.instances[-1].added[0]
    assert stored["stt_providers"] == ("openai-api",)
    assert stored["stt_models"] == ("gpt-4o-transcribe",)
    assert stored["detected_languages"] == ("en", "ja", "es")
    assert stored["audio_sample_rate_hz"] == 16_000
    assert stored["audio_rms"] == pytest.approx(1 / 128)
    assert stored["audio_clipping_ratio"] == 0.0
    assert stored["audio_dropouts"] == 0
    assert stored["audio_dropout_ms"] == 0


@pytest.mark.asyncio
async def test_a_language_pin_reaches_the_provider_when_code_switching_is_off(
    monkeypatch: pytest.MonkeyPatch, audio_spy
) -> None:
    import jarvis.speech.pipeline as pipeline_mod

    seen: list[dict] = []

    class _STT:
        async def transcribe_pcm(self, pcm: bytes, **kw):
            seen.append(kw)
            return SimpleNamespace(text="hola mundo", language="es")

    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda **_kw: _FakeMic(b"\x00\x01" * 16_000)
    )
    pipe, _events = _session_pipeline(
        _STT(),
        DictationConfig(
            language="es",
            code_switching=False,
            partial_interval_s=0.0,
        ),
    )
    await pipe._dictation_session()
    assert seen == [{"language": "es"}]


@pytest.mark.asyncio
async def test_auto_asks_the_provider_to_detect_instead_of_staying_silent(
    monkeypatch: pytest.MonkeyPatch, audio_spy
) -> None:
    """``auto`` is REQUESTED, never left unsaid.

    This test used to assert the opposite — that auto sends no language at all —
    and that is exactly how the bug worked: an absent argument means "no opinion"
    to every provider, so the transcription fell back to whatever
    ``[stt].language`` was pinned to. With the recognition language on English, a
    user dictating German got English words back, and no setting in the dictation
    view could fix it because the dictation view was not the setting in charge
    (live bug 2026-07-28).
    """
    import jarvis.speech.pipeline as pipeline_mod

    seen: list[dict] = []

    class _STT:
        async def transcribe_pcm(self, pcm: bytes, **kw):
            seen.append(kw)
            return SimpleNamespace(text="hello", language="en")

    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda **_kw: _FakeMic(b"\x00\x01" * 16_000)
    )
    pipe, _events = _session_pipeline(
        _STT(), DictationConfig(language="auto", partial_interval_s=0.0)
    )
    await pipe._dictation_session()
    assert seen == [{"language": "auto"}]


@pytest.mark.asyncio
async def test_a_provider_that_cleans_its_own_text_cannot_override_the_filler_switch(
    monkeypatch: pytest.MonkeyPatch, audio_spy
) -> None:
    """A cleaned ``text`` is right for a voice command and wrong for dictation.

    Cloud providers may now filter their own transcript — hesitation sounds,
    decoder loops, stutters — before handing it over. This lane must keep
    transcribing from ``raw_text``: it owns the user's filler switch, runs its
    own cleanup in the language the USER pinned, and writes the untouched
    string to the history. Reading the pre-cleaned one would turn "keep my
    filler words" into a setting with no effect and nothing to observe.
    """
    import jarvis.speech.pipeline as pipeline_mod

    class _CleaningSTT:
        async def transcribe_pcm(self, pcm: bytes, **_kw):
            return SimpleNamespace(
                text="Das ist gut.",  # i18n-allow: German fixture under test (§1 list #4)
                raw_text="Ähm, das ist gut.",  # i18n-allow: German fixture (§1 list #4)
                language="de",
            )

    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda **_kw: _FakeMic(b"\x00\x01" * 16_000)
    )
    pipe, events = _session_pipeline(
        _CleaningSTT(),
        DictationConfig(
            language="de", partial_interval_s=0.0, remove_fillers=False
        ),
    )
    await pipe._dictation_session()

    final = [
        e for e in events if isinstance(e, DictationTranscript) and e.is_final
    ][-1]
    assert final.text == "Ähm, das ist gut."  # i18n-allow: German fixture (§1 list #4)


@pytest.mark.asyncio
async def test_a_provider_without_a_raw_text_field_is_unaffected(
    monkeypatch: pytest.MonkeyPatch, audio_spy
) -> None:
    """Every other provider keeps its single ``text`` field and behaves as before."""
    import jarvis.speech.pipeline as pipeline_mod

    class _PlainSTT:
        async def transcribe_pcm(self, pcm: bytes, **_kw):
            return SimpleNamespace(
                text="Ähm, das ist gut.",  # i18n-allow: German fixture (§1 list #4)
                language="de",
            )

    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda **_kw: _FakeMic(b"\x00\x01" * 16_000)
    )
    pipe, events = _session_pipeline(
        _PlainSTT(),
        DictationConfig(language="de", partial_interval_s=0.0, remove_fillers=True),
    )
    await pipe._dictation_session()

    final = [
        e for e in events if isinstance(e, DictationTranscript) and e.is_final
    ][-1]
    assert final.text == "Das ist gut."  # i18n-allow: German fixture (§1 list #4)


@pytest.mark.asyncio
async def test_a_provider_without_the_language_keyword_still_transcribes(
    monkeypatch: pytest.MonkeyPatch, audio_spy
) -> None:
    """The STT contract allows a bare ``transcribe_pcm(pcm)``. A pin must
    degrade to "no pin", never to a failed dictation."""
    import jarvis.speech.pipeline as pipeline_mod

    class _LegacySTT:
        def __init__(self) -> None:
            self.calls = 0

        async def transcribe_pcm(self, pcm: bytes):
            self.calls += 1
            return SimpleNamespace(text="it still works", language="en")

    stt = _LegacySTT()
    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda **_kw: _FakeMic(b"\x00\x01" * 16_000)
    )
    pipe, events = _session_pipeline(
        stt, DictationConfig(language="de", partial_interval_s=0.0)
    )
    await pipe._dictation_session()
    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert completed.outcome == "chat"
    assert completed.text == "it still works"
    assert stt.calls == 1


@pytest.mark.asyncio
async def test_a_provider_error_ends_the_session_as_failed(
    monkeypatch: pytest.MonkeyPatch, audio_spy
) -> None:
    import jarvis.speech.pipeline as pipeline_mod

    class _BrokenSTT:
        async def transcribe_pcm(self, pcm: bytes, **_kw):
            raise RuntimeError("401 invalid api key")

    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda **_kw: _FakeMic(b"\x00\x01" * 16_000)
    )
    pipe, events = _session_pipeline(
        _BrokenSTT(), DictationConfig(partial_interval_s=0.0)
    )
    await pipe._dictation_session()
    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert completed.outcome == "failed"
    assert completed.error == "bad_key"


@pytest.mark.asyncio
async def test_a_crashed_session_is_recorded_instead_of_vanishing(
    monkeypatch: pytest.MonkeyPatch, audio_spy
) -> None:
    """The old handler logged a warning and returned. The worst failure this
    feature has must not also be its most invisible one."""
    import jarvis.speech.pipeline as pipeline_mod

    class _BoomMic:
        async def __aenter__(self):
            raise OSError("no input device")

        async def __aexit__(self, *_exc) -> bool:
            return False

    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kw: _BoomMic())

    class _STT:
        async def transcribe_pcm(self, pcm: bytes, **_kw):  # pragma: no cover
            raise AssertionError("never reached")

    pipe, events = _session_pipeline(_STT(), DictationConfig())
    await pipe._dictation_session()
    completed = next(e for e in events if isinstance(e, DictationCompleted))
    assert completed.outcome == "failed"
    # A microphone that never opened is not a classifiable STT failure, so the
    # honest answer is the catch-all — never the raw OSError text.
    assert completed.error == "unknown"
    assert "no input device" not in (completed.error or "")
    added = _FakeHistory.instances[-1].added
    assert added and added[0]["outcome"] == "failed"


@pytest.mark.asyncio
async def test_a_crash_inside_delivery_does_not_double_publish(
    monkeypatch: pytest.MonkeyPatch, audio_spy
) -> None:
    """Re-entering the delivery half would publish a second final transcript
    and could recurse straight back into the same fault."""
    import jarvis.speech.pipeline as pipeline_mod

    calls: list[int] = []

    class _STT:
        async def transcribe_pcm(self, pcm: bytes, **_kw):
            return SimpleNamespace(text="hello", language="en")

    monkeypatch.setattr(
        pipeline_mod, "MicrophoneCapture", lambda **_kw: _FakeMic(b"\x00\x01" * 16_000)
    )
    pipe, _events = _session_pipeline(_STT(), DictationConfig(partial_interval_s=0.0))

    async def _boom(**_kw):
        calls.append(1)
        raise RuntimeError("delivery exploded")

    pipe._finish_dictation = _boom  # type: ignore[assignment]
    await pipe._dictation_session()
    assert calls == [1]


# --------------------------------------------------------------------------
# The Jarvis Bar
# --------------------------------------------------------------------------


def test_dictate_renders_as_the_equalizer() -> None:
    """A SPEAKING dictation is the user talking — never the thinking indicator,
    not even during a silent pause mid-sentence."""
    assert renderer.visual_mode("dictate", 99.0, hold_s=0.4) == "speak"
    assert renderer.visual_mode("dictate", 0.0, hold_s=0.4) == "speak"
    # Not even a TTS playback flag may steal the recording look.
    assert renderer.visual_mode("dictate", 0.0, hold_s=0.4, playback_active=True) == "speak"


def test_the_transcribing_phase_renders_as_the_thinking_core() -> None:
    """Recording has stopped and the mic feed is gone — the equalizer would
    claim the bar is still listening. Decided by the mode alone, so a level
    sample still fresh from the last word cannot overrule it."""
    assert renderer.visual_mode("dictate_transcribing", 99.0, hold_s=0.4) == "think"
    assert renderer.visual_mode("dictate_transcribing", 0.0, hold_s=0.4) == "think"


def test_both_dictation_modes_open_the_pill_to_its_active_size() -> None:
    """The hover hit-box is computed from the COARSE mode while the pill is
    drawn from the effective one; if they disagree the user hovers a pill that
    is not where they see it."""
    active = renderer.target_pill_size("listen", hovered=False)
    for mode in renderer.DICTATION_MODES:
        assert renderer.target_pill_size(mode, hovered=False) == active


def test_the_dictation_modes_are_accepted_by_the_surfaces() -> None:
    """The gap that made the whole lane dead on arrival: the modes were handled
    by the pure helpers but missing from the tuple every surface validates
    against, so every ``show("dictate")`` was silently dropped."""
    for mode in renderer.DICTATION_MODES:
        assert mode in renderer.MODES


@pytest.mark.parametrize(
    ("mode", "seconds_since_audible", "playback", "expected"),
    [
        ("idle", 99.0, False, "idle"),
        ("listen", 99.0, False, "speak"),
        ("listen", 0.0, False, "speak"),
        ("think", 99.0, False, "think"),
        ("think", 0.0, False, "speak"),
        ("speak", 99.0, False, "speak"),
        ("speak", 0.0, True, "speak"),
    ],
)
def test_existing_visual_modes_are_unchanged(
    mode: str, seconds_since_audible: float, playback: bool, expected: str
) -> None:
    assert (
        renderer.visual_mode(
            mode, seconds_since_audible, hold_s=0.4, playback_active=playback
        )
        == expected
    )


@pytest.mark.parametrize("x", [10, 100, 400, 700])
@pytest.mark.parametrize("mode", renderer.DICTATION_MODES)
def test_clicking_the_bar_during_dictation_does_nothing(x: int, mode: str) -> None:
    """Without this, a stray click would start a voice session mid-dictation —
    and on the mute zone it would deafen Jarvis while the user is dictating.
    Both dictation phases are inert, not just the recording one."""
    assert interaction.resolve_click(x, 800, mode, hovered=True, pill_w=400) == "none"
    assert interaction.resolve_click(x, 800, mode, hovered=False) == "none"


def test_existing_click_zones_are_unchanged() -> None:
    assert interaction.resolve_click(100, 800, "idle") == "talk"
    assert interaction.resolve_click(700, 800, "idle") == "mute"
    assert interaction.resolve_click(700, 800, "listen") == "mute"
    assert interaction.resolve_click(400, 800, "listen", hovered=True, pill_w=400) == "none"
