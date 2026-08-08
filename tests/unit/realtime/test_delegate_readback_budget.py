"""The provider-declared delegate-readback budget (AP-21 capability).

Live 2026-08-08 15:24: the self-hosted card renders a delegate readback
through its own LLM + TTS (4-8 s), the shared 2.5 s window fired the surface
fallback first, that fallback is TEXT-ONLY on a card without a
realtime-scoped surface TTS — and the provider's real audio answer arriving
seconds later was then withheld as already-spoken. The user heard nothing.
"""

from __future__ import annotations

import asyncio

import pytest

from jarvis.realtime.session import _DELEGATE_READBACK_WAIT_S, RealtimeVoiceSession

from .test_session import FakeProvider, _cfg


class _SlowReadbackProvider(FakeProvider):
    name = "slow-readback-family"
    readback_render_budget_s = 12.0


def _session(provider: FakeProvider) -> RealtimeVoiceSession:
    return RealtimeVoiceSession(
        session_id="readback-budget",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda _message: asyncio.sleep(0),
        providers=[provider],
        config=_cfg(),
        bus=None,
    )


@pytest.mark.asyncio
async def test_declared_budget_wins_over_the_hosted_floor():
    provider = _SlowReadbackProvider([])
    sess = _session(provider)
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    try:
        assert sess._delegate_readback_budget_s() == 12.0
    finally:
        await sess.end(reason="test")


@pytest.mark.asyncio
async def test_hosted_providers_keep_the_measured_floor():
    provider = FakeProvider([])
    sess = _session(provider)
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    try:
        assert sess._delegate_readback_budget_s() == _DELEGATE_READBACK_WAIT_S
    finally:
        await sess.end(reason="test")


def test_local_card_declares_a_realistic_readback_budget() -> None:
    from jarvis.plugins.realtime.openai_realtime import LocalRealtimeProvider

    assert LocalRealtimeProvider.readback_render_budget_s > _DELEGATE_READBACK_WAIT_S
