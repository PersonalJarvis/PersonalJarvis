"""Plan 4.4 fixes in ``jarvis/live/native.py``, each on Gemini- and local-voice-like wires.

``NativeLiveVoiceSession`` serves Gemini Live and the Jarvis-owned local voice
engine, so every behaviour here is pinned on both connection shapes from
``tests/fakes/fake_native_voice.py``.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from jarvis.live import native, runtime
from jarvis.live.native import NativeLiveVoiceSession
from jarvis.live.state import LiveLedger
from jarvis.live.tools import LiveTools
from jarvis.realtime.protocol import RealtimeEvent, RealtimeUnavailableError
from tests.fakes.fake_native_voice import (
    FakeNativeConnection,
    FakeToolGateway,
    catalog_of,
    gemini_like_provider,
    local_voice_like_provider,
)

PROVIDERS = {
    "gemini-like": gemini_like_provider,
    "local-voice-like": local_voice_like_provider,
}
both_providers = pytest.mark.parametrize("make_provider", PROVIDERS.values(), ids=PROVIDERS)
_LOCAL_REASON = "The local voice is still loading (stt, 40 %)."


def _config(*, providers=None, realtime=None, budget=0, reply_language="auto"):
    return SimpleNamespace(
        brain=SimpleNamespace(
            reply_language=reply_language,
            providers=providers or {},
            realtime=realtime,
        ),
        voice=SimpleNamespace(realtime_tool_declaration_budget_tokens=budget),
    )


class _Call:
    """A session driven through the real ``_start`` with fake provider and tools."""

    def __init__(self, provider, config, gateway):
        self.provider = provider
        self.frames: list[dict] = []

        async def send_json(frame):
            self.frames.append(frame)

        async def send_binary(_data):
            return None

        self.session = NativeLiveVoiceSession(
            session_id="native-test",
            send_json=send_json,
            send_binary=send_binary,
            providers=[provider],
            config=config,
        )

    async def start(self) -> None:
        await self.session.handle_control({"type": "audio_start", "sample_rate": 48000})

    def sent(self, kind: str) -> list[dict]:
        return [frame for frame in self.frames if frame.get("type") == kind]


@pytest.fixture
def call(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "_active", {})
    monkeypatch.setattr(runtime, "_opening", set())
    monkeypatch.setattr(native, "user_data_dir", lambda: tmp_path)

    def _build(provider, config=None, gateway=None):
        gateway = gateway or FakeToolGateway()
        monkeypatch.setattr(native, "get_supervisor_tool_gateway", lambda: gateway)
        return _Call(provider, config or _config(), gateway)

    return _build


def _in_call(provider, tmp_path, *, gateway=None):
    """A session already in a call: connection, ledger and tools in place."""
    frames: list[dict] = []

    async def send_json(frame):
        frames.append(frame)

    async def send_binary(_data):
        return None

    session = NativeLiveVoiceSession(
        session_id="native-live",
        send_json=send_json,
        send_binary=send_binary,
        providers=[provider],
        config=_config(),
    )
    connection = FakeNativeConnection(creates_responses_automatically=provider._creates, model="m")
    ledger = LiveLedger(tmp_path / f"{provider.name}.sqlite3")
    session._connection = connection
    session._ledger = ledger
    session._tools = LiveTools(
        gateway or FakeToolGateway(), ledger, "native-live", language="en", backend_model=""
    )
    return session, connection, frames


async def _settle(session) -> None:
    """Let the response/language tasks the session spawned run to completion."""
    for _ in range(5):
        await asyncio.sleep(0)
    await asyncio.gather(*session._control_tasks, return_exceptions=True)


# 1. Readiness probe before open_session ---------------------------------------

# Hang guard only, never the measured quantity: a loaded CI runner can stretch
# the session setup before the probe (data dir, ledger, prompt) past a second.
_HANG_GUARD_S = 30.0


def test_the_refusal_budget_keeps_the_one_second_release_slo():
    """The SLO is pinned on the budget constant; the tests below prove it is used."""
    assert 0 < native._DUPLEX_PROBE_BUDGET_S <= 1.0


@both_providers
@pytest.mark.asyncio
async def test_not_ready_provider_is_refused_in_its_own_words(call, make_provider):
    live = call(make_provider(ready=False))
    with pytest.raises(RealtimeUnavailableError) as refused:
        await asyncio.wait_for(live.start(), _HANG_GUARD_S)
    assert live.provider.probes == 1
    assert live.provider.opened_with == []  # nothing opened, nothing billed
    expected = (
        _LOCAL_REASON if live.provider.name == "local-voice" else native._DUPLEX_REFUSAL_FALLBACK
    )
    assert str(refused.value) == expected
    assert live.session.failure_detail == expected
    spoken = live.sent("error_spoken")
    assert [frame["text"] for frame in spoken] == [expected]
    assert spoken[0]["provider"] == live.provider.name
    assert live.sent("audio_ready") == []


@both_providers
@pytest.mark.asyncio
async def test_slow_readiness_probe_is_refused_within_a_second(call, make_provider, monkeypatch):
    # A probe that never answers within the test is refused by the budget alone:
    # without the budget ``start`` would wait the full hour and hit the guard.
    # The budget is shrunk so the proof does not race a wall clock on a loaded
    # runner; its production value is pinned by the SLO test above.
    monkeypatch.setattr(native, "_DUPLEX_PROBE_BUDGET_S", 0.05)
    live = call(make_provider(probe_delay_s=3600.0))
    with pytest.raises(RealtimeUnavailableError):
        await asyncio.wait_for(live.start(), _HANG_GUARD_S)
    assert live.provider.probes == 1
    assert live.provider.opened_with == []
    assert len(live.sent("error_spoken")) == 1


@both_providers
@pytest.mark.asyncio
async def test_ready_provider_opens_after_one_probe(call, make_provider):
    live = call(make_provider())
    await live.start()
    try:
        assert live.provider.probes == 1
        assert len(live.provider.opened_with) == 1
        assert live.sent("error_spoken") == []
        assert len(live.sent("audio_ready")) == 1
        assert live.sent("audio_ready")[0]["sound_effects"] is True
    finally:
        await live.session.end()


@both_providers
@pytest.mark.asyncio
async def test_readiness_cue_respects_the_shared_sound_effects_switch(call, make_provider):
    config = _config()
    config.ui = SimpleNamespace(sound_effects=False)
    live = call(make_provider(), config)
    await live.start()
    try:
        assert live.sent("audio_ready")[0]["sound_effects"] is False
    finally:
        await live.session.end()


# 2. The active provider's own model --------------------------------------------


@both_providers
@pytest.mark.asyncio
async def test_model_comes_from_the_active_provider_never_the_realtime_tier(call, make_provider):
    provider = make_provider()
    # The OpenAI Live card wrote [brain.realtime]; a switch kept its model.
    tier = SimpleNamespace(provider=provider.name, model="gpt-realtime")
    own = SimpleNamespace(model="own-model", voice="")
    live = call(provider, _config(providers={provider.name: own}, realtime=tier))
    await live.start()
    try:
        assert provider.opened_with[0].model == "own-model"
        assert live.sent("audio_ready")[0]["model"] == "own-model"
    finally:
        await live.session.end()


@both_providers
@pytest.mark.asyncio
async def test_no_own_model_means_the_adapter_default_not_another_providers(call, make_provider):
    provider = make_provider()
    tier = SimpleNamespace(provider="openai-live", model="gpt-realtime")
    live = call(provider, _config(realtime=tier))
    await live.start()
    try:
        assert provider.opened_with[0].model == ""
    finally:
        await live.session.end()


# 3. Barge-in -------------------------------------------------------------------


@both_providers
@pytest.mark.asyncio
async def test_speech_while_jarvis_speaks_interrupts_and_flushes_playback(tmp_path, make_provider):
    session, connection, frames = _in_call(make_provider(), tmp_path)
    try:
        session._speaking = True
        await session._native_event(RealtimeEvent(type="speech_started"))
        assert connection.names() == ["interrupt"]
        kinds = [frame["type"] for frame in frames]
        assert "audio_clear" in kinds and "tts_cancel" in kinds
        assert kinds.index("audio_clear") < kinds.index("tts_cancel")
        assert not session._speaking and not session.playback_active
    finally:
        session._ledger.close()


@both_providers
@pytest.mark.asyncio
async def test_browser_still_playing_counts_as_speaking(tmp_path, make_provider):
    session, connection, frames = _in_call(make_provider(), tmp_path)
    try:
        session.playback_active = True  # model finished, browser still plays
        await session._native_event(RealtimeEvent(type="speech_started"))
        assert connection.names() == ["interrupt"]
        assert any(frame["type"] == "audio_clear" for frame in frames)
    finally:
        session._ledger.close()


@both_providers
@pytest.mark.asyncio
async def test_speech_while_silent_does_not_interrupt(tmp_path, make_provider):
    session, connection, frames = _in_call(make_provider(), tmp_path)
    try:
        await session._native_event(RealtimeEvent(type="speech_started"))
        await session._native_event(RealtimeEvent(type="interrupted"))
        assert "interrupt" not in connection.names()
        assert all(frame["type"] != "audio_clear" for frame in frames)
    finally:
        session._ledger.close()


# 4. Tool deadline ----------------------------------------------------------------


@both_providers
@pytest.mark.asyncio
async def test_slow_tool_releases_the_model_with_an_honest_pending_result(
    tmp_path, monkeypatch, make_provider
):
    monkeypatch.setattr(native, "_TOOL_DEADLINE_S", 0.05)
    gateway = FakeToolGateway()
    release = gateway.hold("search_web")
    session, connection, _frames = _in_call(make_provider(), tmp_path, gateway=gateway)
    try:
        await session._native_event(
            RealtimeEvent(
                type="tool_call",
                call_id="c1",
                tool_name="call_tool",
                tool_args={"name": "search_web", "arguments_json": "{}"},
            )
        )
        await asyncio.wait_for(connection.tool_result_sent.wait(), _HANG_GUARD_S)
        _, _, result = connection.tool_results[0]
        assert result["pending"] is True and result["success"] is False
        assert "still running" in result["error"]
        assert gateway.finished == []  # the tool itself was not cancelled
        assert session._has_pending_work()  # its receipt is still owed
        release.set()
        # Await the released work itself instead of polling a wall clock: its
        # receipt lands after the tool body returns, which a loaded Windows CI
        # runner can stretch past half a second.
        await asyncio.wait_for(
            asyncio.gather(*[job for job in session._jobs if not job.done()]), _HANG_GUARD_S
        )
        assert gateway.finished == ["search_web"]
        assert not session._has_pending_work()
        assert len(connection.tool_results) == 1  # the late result is not re-sent
    finally:
        session._ledger.close()


@both_providers
@pytest.mark.asyncio
async def test_fast_tool_answers_with_its_real_result(tmp_path, monkeypatch, make_provider):
    # "Fast" means "finishes before the deadline": the deadline is raised so a
    # loaded runner, where the receipt write alone can take seconds, cannot
    # turn the real result into a pending one.
    monkeypatch.setattr(native, "_TOOL_DEADLINE_S", _HANG_GUARD_S)
    session, connection, _frames = _in_call(make_provider(), tmp_path)
    try:
        await session._native_event(
            RealtimeEvent(
                type="tool_call",
                call_id="c1",
                tool_name="call_tool",
                tool_args={"name": "search_web", "arguments_json": "{}"},
            )
        )
        await asyncio.wait_for(connection.tool_result_sent.wait(), _HANG_GUARD_S)
        _, _, result = connection.tool_results[0]
        assert result["success"] is True and "pending" not in result
    finally:
        session._ledger.close()


# 5. Declaration budget -------------------------------------------------------------


@both_providers
@pytest.mark.asyncio
async def test_declarations_respect_the_providers_budget(call, make_provider):
    provider = make_provider()
    gateway = FakeToolGateway(catalog_of(30))
    live = call(provider, gateway=gateway)
    await live.start()
    try:
        tools = provider.opened_with[0].tools
        names = [tool["name"] for tool in tools]
        for builtin in ("end_call", "discover_tools", "call_tool", "confirm_action"):
            assert builtin in names
        described = [tool["description"].split(": ", 1)[0] for tool in tools[4:]]
        if provider.tool_declaration_budget_tokens:
            assert len(json.dumps(tools)) <= provider.tool_declaration_budget_tokens * 4
            assert len(described) <= native._DIRECT_TOOL_LIMIT
            # The curated frequent tools come first, in preference order.
            assert described[:4] == [
                "workspace-orchestrate",
                "search_web",
                "google_calendar",
                "youtube_music",
            ]
        else:
            # Gemini keeps the whole catalog LiveTools declares.
            assert len(described) == 34
    finally:
        await live.session.end()


@both_providers
@pytest.mark.asyncio
async def test_config_budget_bounds_a_provider_without_its_own(call, make_provider):
    provider = make_provider(tool_declaration_budget_tokens=0)
    live = call(provider, _config(budget=1_000), gateway=FakeToolGateway(catalog_of(30)))
    await live.start()
    try:
        assert len(json.dumps(provider.opened_with[0].tools)) <= 4_000
    finally:
        await live.session.end()


# 6. Per-turn language ----------------------------------------------------------------


@both_providers
@pytest.mark.asyncio
async def test_turn_language_reaches_the_provider_before_the_response(tmp_path, make_provider):
    provider = make_provider()
    session, connection, _frames = _in_call(provider, tmp_path)
    try:
        await session._native_event(
            RealtimeEvent(
                type="input_transcript",
                text="Wie wird das Wetter morgen in Berlin?",  # i18n-allow: German speech fixture
                is_final=True,
            )
        )
        await _settle(session)
        assert session._language == "de"
        if connection.creates_responses_automatically:
            # Gemini answers on its own; no extra steering turn is injected.
            assert connection.names() == []
        else:
            assert connection.calls == [
                ("update_session", {"language": "de"}),
                ("request_response", None),
            ]
    finally:
        session._ledger.close()
