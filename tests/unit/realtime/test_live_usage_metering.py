"""Live-channel token metering + context hygiene (2026-07-28 cost audit).

Before these changes the Live/Realtime APIs' own spend — audio tokens at
4-40x text rates, full-context re-billing every turn — never reached the
recorder: providers discarded ``usage_metadata`` / ``response.usage``
entirely, and 57% of recorded realtime turns showed 0 tokens.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.plugins.realtime.gemini_live import (
    _COMPRESSION_TARGET_TOKENS,
    _COMPRESSION_TRIGGER_TOKENS,
    _compression_kwargs,
    _GeminiLiveSession,
    _usage_from_metadata,
)
from jarvis.plugins.realtime.openai_realtime import _usage_from_response
from jarvis.realtime.session import (
    _DELEGATE_RESULT_MAX_CHARS,
    RealtimeVoiceSession,
    _delegate_result_prompt,
)


def _modality(name: str, count: int) -> SimpleNamespace:
    return SimpleNamespace(modality=SimpleNamespace(name=name), token_count=count)


class TestGeminiUsageExtraction:
    def test_totals_and_modality_split(self) -> None:
        md = SimpleNamespace(
            prompt_token_count=1200,
            response_token_count=300,
            prompt_tokens_details=[
                _modality("TEXT", 400),
                _modality("AUDIO", 800),
            ],
            response_tokens_details=[_modality("AUDIO", 300)],
        )
        usage = _usage_from_metadata(md)
        assert usage == {
            "input_total": 1200,
            "output_total": 300,
            "input_text": 400,
            "input_audio": 800,
            "output_text": 0,
            "output_audio": 300,
        }

    def test_empty_metadata_returns_none(self) -> None:
        md = SimpleNamespace(
            prompt_token_count=0,
            response_token_count=None,
            prompt_tokens_details=None,
            response_tokens_details=None,
        )
        assert _usage_from_metadata(md) is None

    def test_missing_detail_lists_keep_totals(self) -> None:
        md = SimpleNamespace(
            prompt_token_count=50,
            response_token_count=10,
            prompt_tokens_details=None,
            response_tokens_details=None,
        )
        usage = _usage_from_metadata(md)
        assert usage is not None
        assert usage["input_total"] == 50
        assert usage["input_audio"] == 0


class TestCompressionConfig:
    def test_sdk_with_compression_gets_sliding_window(self) -> None:
        captured: dict = {}

        class Compression:
            def __init__(self, *, trigger_tokens, sliding_window):
                captured["trigger"] = trigger_tokens
                captured["window"] = sliding_window

        class Sliding:
            def __init__(self, *, target_tokens):
                captured["target"] = target_tokens

        types = SimpleNamespace(
            ContextWindowCompressionConfig=Compression, SlidingWindow=Sliding
        )
        kwargs = _compression_kwargs(types)
        assert "context_window_compression" in kwargs
        assert captured["trigger"] == _COMPRESSION_TRIGGER_TOKENS
        assert captured["target"] == _COMPRESSION_TARGET_TOKENS

    def test_sdk_without_compression_degrades_to_empty(self) -> None:
        assert _compression_kwargs(SimpleNamespace()) == {}


@pytest.mark.asyncio
async def test_gemini_receive_reports_usage_at_the_turn_boundary() -> None:
    messages = [
        SimpleNamespace(
            data=None,
            server_content=None,
            tool_call=None,
            go_away=None,
            usage_metadata=SimpleNamespace(
                prompt_token_count=1000,
                response_token_count=200,
                prompt_tokens_details=[_modality("AUDIO", 1000)],
                response_tokens_details=[_modality("AUDIO", 200)],
            ),
        ),
        SimpleNamespace(
            data=None,
            server_content=SimpleNamespace(
                output_transcription=None,
                input_transcription=None,
                interrupted=False,
                turn_complete=True,
                turn_complete_reason=None,
            ),
            tool_call=None,
            go_away=None,
            usage_metadata=None,
        ),
    ]
    calls = 0

    async def fake_receive():
        nonlocal calls
        calls += 1
        if calls == 1:
            for message in messages:
                yield message

    session = _GeminiLiveSession(
        session=SimpleNamespace(receive=fake_receive),
        connection_cm=SimpleNamespace(),
        client=SimpleNamespace(),
        session_id="s1",
    )
    events = [event async for event in session.receive()]
    kinds = [event.type for event in events]
    # Usage must be reported before the boundary event that closes the turn.
    assert kinds.index("usage") < kinds.index("turn_complete")
    usage_event = next(event for event in events if event.type == "usage")
    assert usage_event.usage is not None
    assert usage_event.usage["input_audio"] == 1000


class TestOpenAIUsageExtraction:
    def test_split_including_cached(self) -> None:
        response = SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=5000,
                output_tokens=400,
                input_token_details=SimpleNamespace(
                    text_tokens=3000, audio_tokens=2000, cached_tokens=2500
                ),
                output_token_details=SimpleNamespace(
                    text_tokens=100, audio_tokens=300
                ),
            )
        )
        usage = _usage_from_response(response)
        assert usage == {
            "input_total": 5000,
            "output_total": 400,
            "input_text": 3000,
            "input_audio": 2000,
            "input_cached": 2500,
            "output_text": 100,
            "output_audio": 300,
        }

    def test_no_usage_returns_none(self) -> None:
        assert _usage_from_response(SimpleNamespace(usage=None)) is None
        assert _usage_from_response(None) is None


class TestDelegateResultCap:
    def test_short_result_passes_verbatim(self) -> None:
        prompt = _delegate_result_prompt("All done.", language="en", success=True)
        assert "All done." in prompt
        assert "[result shortened]" not in prompt

    def test_runaway_result_is_capped(self) -> None:
        text = "A sentence about something. " * 1000
        prompt = _delegate_result_prompt(text, language="en", success=True)
        assert "[result shortened]" in prompt
        # Framing adds ~1.2k chars; the injected payload stays bounded.
        assert len(prompt) < _DELEGATE_RESULT_MAX_CHARS + 2000


class _FakeBus:
    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, event) -> None:
        self.published.append(event)


def _bare_session(usage: dict[str, int], model: str) -> RealtimeVoiceSession:
    session = object.__new__(RealtimeVoiceSession)
    session._turn_usage = dict(usage)
    session._bus = _FakeBus()
    session._active_model = model
    session._turn_trace_id = None
    session.session_id = "s-test"
    # active_provider is a read-only property over the provider's name.
    session._provider = SimpleNamespace(name="gemini-live")
    return session


@pytest.mark.asyncio
async def test_publish_live_usage_prices_audio_and_resets() -> None:
    session = _bare_session(
        {
            "input_total": 1_000_000,
            "output_total": 1_000_000,
            "input_audio": 1_000_000,
            "output_audio": 1_000_000,
        },
        "gemini-3.1-flash-live-preview",
    )
    await session._publish_live_usage()
    events = session._bus.published
    assert len(events) == 1
    event = events[0]
    assert event.tokens_in == 1_000_000
    assert event.tokens_out == 1_000_000
    # Audio rates for the live model: $3/M in + $12/M out.
    assert event.cost_usd == pytest.approx(15.0)
    assert event.provider == "gemini-live"
    assert session._turn_usage == {}


@pytest.mark.asyncio
async def test_publish_live_usage_without_usage_is_silent() -> None:
    session = _bare_session({}, "gemini-3.1-flash-live-preview")
    await session._publish_live_usage()
    assert session._bus.published == []
