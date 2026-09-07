"""RUB-19: short corrections, sticky follow-ups, and language-safe voice output."""

import asyncio

import pytest

from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession
from tests.unit.realtime.test_session import FakeProvider, FakeSession, _cfg


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial", "correction", "expected", "name"),
    [
        ("es", "auf Deutsch", "de", "German"),  # i18n-allow: incident correction
        ("en", "sprich Deutsch", "de", "German"),  # i18n-allow
        ("de", "in English", "en", "English"),
        ("en", "en español", "es", "Spanish"),
        ("es", "in English", "en", "English"),
    ],
)
async def test_short_correction_updates_provider_voice_and_stays_for_followup(
    initial: str, correction: str, expected: str, name: str,
) -> None:
    # A short first correction must establish the conversation, even when the
    # measured audio duration is below the usual first-fragment threshold.
    provider = FakeProvider([
        RealtimeEvent(type="input_transcript", text=correction, is_final=True, voiced_ms=200),
        RealtimeEvent(type="turn_complete"),
        RealtimeEvent(type="input_transcript", text="Okay", is_final=True),
        RealtimeEvent(type="turn_complete"),
    ])
    session = RealtimeVoiceSession(
        session_id="language-correction",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(reply_language="auto", stt_language=initial),
        bus=None,
    )
    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await session.wait_finished()
    await session.end(reason="test")

    assert session._conversation_established
    assert session._language == expected
    assert len(provider.session.session_updates) >= 2
    for update in provider.session.session_updates[-2:]:
        assert update["language"] == expected  # Provider TTS voice language.
        assert f"Reply only in {name} for this turn" in update["instructions"]
    assert session._output_language_failure_phrase() == (
        session._output_language_failure_phrase(expected)
    )


@pytest.mark.asyncio
async def test_incident_turns_five_to_seven_do_not_trap_conversation_in_spanish() -> None:
    # Establish German, then repeat the incident's weak Spanish evidence,
    # two-word correction, and longer correction under a conflicting STT tag.
    provider = FakeProvider([
        event
        for transcript in (
            "Wie ist das Wetter heute?",  # i18n-allow
            "Álvaro García Madrid",
            "auf Deutsch",  # i18n-allow
            "Sollst auf deutsch antworten.",  # i18n-allow
        )
        for event in (
            RealtimeEvent(type="input_transcript", text=transcript, is_final=True),
            RealtimeEvent(type="turn_complete"),
        )
    ])
    session = RealtimeVoiceSession(
        session_id="incident-language-sequence",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(reply_language="auto", stt_language="es"),
        bus=None,
    )
    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await session.wait_finished()
    await session.end(reason="test")

    assert len(provider.session.session_updates) == 4
    assert all(update["language"] == "de" for update in provider.session.session_updates)
    assert session._language == "de"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("correction", "expected", "name"),
    [
        ("auf Deutsch", "de", "German"),  # i18n-allow: incident correction
        ("in English", "en", "English"),
        ("en español", "es", "Spanish"),
    ],
)
async def test_mismatched_output_retries_and_speaks_fallback_in_corrected_language(
    correction: str, expected: str, name: str,
) -> None:
    wrong_text = "这是一个完整的中文回答，包含足够多的文字来可靠地识别语言。"

    class MismatchingSession(FakeSession):
        supports_prompted_response_retry = True
        renders_surface_fallback = True

        async def receive(self):
            yield RealtimeEvent(type="input_transcript", text=correction, is_final=True)
            for attempt in range(2):
                yield RealtimeEvent(type="output_transcript_delta", text=wrong_text)
                for _ in range(100):
                    if len(self.text_inputs) > attempt:
                        break
                    await asyncio.sleep(0.01)
            yield RealtimeEvent(type="turn_complete")

    class MismatchingProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = MismatchingSession([])
            return self.session

    provider = MismatchingProvider([])
    session = RealtimeVoiceSession(
        session_id="corrected-fallback-language",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(reply_language="auto", stt_language="es" if expected != "es" else "de"),
        bus=None,
    )
    # Reproduce a conversation that already got stuck in the wrong language.
    session._conversation_established = True
    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    await session.wait_finished()
    await session.end(reason="test")

    assert session._language == expected
    assert session._output_language_failures == 1
    assert len(provider.session.text_inputs) == 2
    assert name in provider.session.text_inputs[0]
    assert session._output_language_failure_phrase(expected) in provider.session.text_inputs[1]
    assert provider.session.session_updates[-1]["language"] == expected
