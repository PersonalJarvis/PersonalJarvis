"""The local-voice provider adapter translates between the realtime contract and the engine."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.config import JarvisConfig, VoiceEngineConfig
from jarvis.plugins.realtime.local_voice import (
    EngineSettings,
    LocalVoiceProvider,
    _Engine,
)
from jarvis.realtime.protocol import RealtimeProvider
from jarvis.voice_engine import models
from jarvis.voice_engine.paths import venv_python
from jarvis.voice_engine.protocol import AudioFrame


@dataclass
class FakeClient:
    """Stands in for EngineClient: records what the adapter sends."""

    sent: list[dict[str, Any]] = field(default_factory=list)
    audio: list[tuple[int, int, bytes]] = field(default_factory=list)
    messages: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    audio_frames: asyncio.Queue[AudioFrame] = field(default_factory=asyncio.Queue)

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    async def send_audio(self, slot: int, seq: int, pcm: bytes) -> None:
        self.audio.append((slot, seq, pcm))

    async def close(self) -> int:
        return 0


def _settings() -> EngineSettings:
    return EngineSettings(python="python", package_root=None, languages=["de", "en"])


async def _running_engine() -> tuple[_Engine, FakeClient]:
    engine = _Engine(_settings())
    client = FakeClient()
    engine._client = client
    loop = asyncio.get_running_loop()
    engine._router = loop.create_task(engine._route_messages(client))
    engine._audio_router = loop.create_task(engine._route_audio(client))
    client.messages.put_nowait({"type": "state", "phase": "ready", "detail": {"voices": {}}})
    assert await engine.wait_ready(1.0)
    return engine, client


def test_provider_satisfies_the_realtime_contract() -> None:
    provider = LocalVoiceProvider(_settings())
    assert isinstance(provider, RealtimeProvider)
    assert LocalVoiceProvider.native_tool_orchestration and LocalVoiceProvider.browser_audio
    assert LocalVoiceProvider.implicit_usage_fallback_allowed is False
    assert LocalVoiceProvider.credential_candidates == ()


@pytest.mark.asyncio
async def test_a_session_round_trip_is_translated_both_ways() -> None:
    engine, client = await _running_engine()
    cfg = SimpleNamespace(language="de", instructions="Du bist Jarvis.",  # i18n-allow
                          tools=({"name": "set_timer", "description": "", "parameters": {}},),
                          history=(), turn_pause_ms=None)
    session = await engine.open(LocalVoiceProvider(_settings()), cfg)
    opened = client.sent[-1]
    assert opened["type"] == "session.open" and opened["language"] == "de"
    assert opened["tools"][0]["name"] == "set_timer"

    await session.send_audio(SimpleNamespace(pcm=b"\x00\x00" * 320, sample_rate=16_000))
    assert client.audio[-1][0] == session.slot

    for message in (
        {"type": "speech_started"},
        {"type": "transcript.input", "text": "Stell einen Timer", "final": True,  # i18n-allow
         "voiced_ms": 900},
        {"type": "tool.call", "call_id": "c1", "name": "set_timer", "arguments": {"minutes": 5}},
        {"type": "transcript.output", "delta": "Erledigt. "},  # i18n-allow
        {"type": "interrupted", "self_initiated": False},
        {"type": "response.done", "status": "completed"},
    ):
        client.messages.put_nowait({**message, "session": session.session_id})
    client.audio_frames.put_nowait(AudioFrame(slot=session.slot, seq=1, pcm=b"\x01\x00" * 480))

    seen = []
    async with asyncio.timeout(2):
        async for event in session.receive():
            seen.append(event)
            if len(seen) == 7:
                break
    kinds = [e.type for e in seen]
    assert kinds.count("audio_delta") == 1
    assert [k for k in kinds if k != "audio_delta"] == [
        "speech_started", "input_transcript", "tool_call", "output_transcript_delta",
        "interrupted", "turn_complete",
    ]
    final = next(e for e in seen if e.type == "input_transcript")
    assert final.is_final and final.voiced_ms == 900
    call = next(e for e in seen if e.type == "tool_call")
    assert (call.call_id, call.tool_name, call.tool_args) == ("c1", "set_timer", {"minutes": 5})
    audio = next(e for e in seen if e.type == "audio_delta")
    assert audio.audio.sample_rate == 24_000

    await session.request_response()
    await session.send_tool_result("c1", "set_timer", {"success": True})
    await session.interrupt()
    await session.truncate(1200)
    await session.update_session(language="en", turn_directive="Be brief.")
    await session.close()
    kinds_sent = [m["type"] for m in client.sent[1:]]
    assert kinds_sent == ["response.request", "tool.result", "interrupt", "truncate",
                          "session.update", "session.close"]
    assert client.sent[1]["language"] == "de"
    assert client.sent[5]["language"] == "en" and "Be brief." in client.sent[5]["instructions"]
    await engine.stop()


@pytest.mark.asyncio
async def test_an_engine_exit_ends_the_call_with_an_error() -> None:
    engine, client = await _running_engine()
    session = await engine.open(LocalVoiceProvider(_settings()),
                                SimpleNamespace(language="en", instructions="", tools=(),
                                                history=(), turn_pause_ms=None))
    client.messages.put_nowait({"type": "_exited"})
    events = []
    async with asyncio.timeout(2):
        async for event in session.receive():
            events.append(event)
    assert [e.type for e in events] == ["error"]
    assert events[0].recoverable is False
    assert engine._client is None


@pytest.mark.asyncio
async def test_a_loading_engine_refuses_fast_with_its_progress() -> None:
    provider = LocalVoiceProvider(_settings())
    engine = LocalVoiceProvider._shared(_settings())
    engine._client = FakeClient()
    engine.phase, engine.stage, engine.progress = "loading", "stt", 0.2
    try:
        assert await provider.can_open_duplex_session() is False
        assert "20 %" in provider.duplex_unavailable_reason
        assert "about 12 seconds" in provider.duplex_unavailable_reason
    finally:
        LocalVoiceProvider._engine = None


def _installed_home(home: Path) -> EngineSettings:
    """A fake engine home that passes the installed check (no real engine inside)."""
    python = venv_python(home)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("", encoding="utf-8")
    for name in ("silero-vad-v6", "smart-turn-v3.2", "parakeet-tdt-0.6b-v3-int8"):
        path = models.model_path(name, home / "models")
        if models.REGISTRY[name].is_archive:
            path.mkdir(parents=True, exist_ok=True)
            (path / "model.onnx").write_bytes(b"x")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
    return EngineSettings(python=str(python), package_root=None, home=str(home))


def test_the_tool_declaration_budget_is_declared() -> None:
    assert LocalVoiceProvider.tool_declaration_budget_tokens == 2000


@pytest.mark.asyncio
async def test_an_engine_that_was_never_set_up_refuses_with_the_next_step(
    tmp_path: Path,
) -> None:
    provider = LocalVoiceProvider(EngineSettings(python=str(tmp_path / "none"),
                                                 package_root=None, home=str(tmp_path)))
    try:
        assert await provider.can_open_duplex_session() is False
        assert "not set up" in provider.duplex_unavailable_reason
        assert LocalVoiceProvider._engine._client is None
        assert LocalVoiceProvider._engine._starting is None
    finally:
        LocalVoiceProvider._engine = None


@pytest.mark.asyncio
async def test_an_installed_engine_starts_in_the_background_and_answers_at_once(
    tmp_path: Path,
) -> None:
    settings = _installed_home(tmp_path)
    provider = LocalVoiceProvider(settings)
    engine = LocalVoiceProvider._shared(settings)
    starts: list[int] = []
    gate = asyncio.Event()

    async def slow_start() -> None:
        starts.append(1)
        engine.phase, engine.stage, engine.progress = "loading", "tts:de", 0.4
        await gate.wait()

    engine.ensure_started = slow_start
    try:
        async with asyncio.timeout(0.5):
            assert await provider.can_open_duplex_session() is False
        await asyncio.sleep(0)
        assert starts == [1]
        assert await provider.can_open_duplex_session() is False
        assert starts == [1]  # joined, not started twice
        assert "40 %" in provider.duplex_unavailable_reason
        assert "about 9 seconds" in provider.duplex_unavailable_reason
    finally:
        gate.set()
        LocalVoiceProvider._engine = None


@pytest.mark.asyncio
async def test_selecting_the_card_starts_an_installed_engine_and_never_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_VOICE_ENGINE_HOME", str(tmp_path / "missing"))
    try:
        await LocalVoiceProvider.verify_activation(JarvisConfig())
        assert LocalVoiceProvider._engine is None  # nothing set up: nothing starts

        _installed_home(tmp_path)
        monkeypatch.setenv("JARVIS_VOICE_ENGINE_HOME", str(tmp_path))
        engine = LocalVoiceProvider.shared_engine(JarvisConfig())
        starts: list[int] = []

        async def fake_start() -> None:
            starts.append(1)

        engine.ensure_started = fake_start
        await LocalVoiceProvider.verify_activation(JarvisConfig())
        await asyncio.sleep(0)
        assert starts == [1]
    finally:
        LocalVoiceProvider._engine = None


@pytest.mark.asyncio
async def test_a_selftest_result_is_routed_to_its_caller() -> None:
    engine, client = await _running_engine()
    task = asyncio.get_running_loop().create_task(engine.selftest(timeout_s=2))
    await asyncio.sleep(0)
    assert client.sent[-1] == {"type": "selftest"}
    client.messages.put_nowait({"type": "selftest.result", "ok": True, "llm": {"ms": 390}})
    result = await task
    assert result["ok"] is True and result["llm"]["ms"] == 390
    await engine.stop()


@pytest.mark.asyncio
async def test_new_settings_replace_and_stop_the_running_worker() -> None:
    engine, _client = await _running_engine()
    LocalVoiceProvider._engine = engine
    stopped: list[int] = []

    async def stop() -> None:
        stopped.append(1)

    engine.stop = stop
    try:
        other = EngineSettings(python="python", package_root=None, tts="piper")
        replacement = LocalVoiceProvider._shared(other)
        await asyncio.sleep(0)
        assert replacement is not engine and stopped == [1]
    finally:
        LocalVoiceProvider._engine = None
        engine._router.cancel()
        engine._audio_router.cancel()


@pytest.mark.asyncio
async def test_a_refusal_is_spoken_in_the_language_the_call_starts_in(tmp_path: Path) -> None:
    german = SimpleNamespace(
        brain=SimpleNamespace(reply_language="de"), stt=SimpleNamespace(language="auto"),
        ui=SimpleNamespace(language="en"),
        voice_engine=SimpleNamespace(home=str(tmp_path), python=str(tmp_path / "none")),
    )
    provider = LocalVoiceProvider.from_runtime_config(german)
    try:
        assert provider.language == "de"
        assert await provider.can_open_duplex_session() is False
        assert "noch nicht eingerichtet" in provider.duplex_unavailable_reason  # i18n-allow
    finally:
        LocalVoiceProvider._engine = None
    auto_ui_es = SimpleNamespace(brain=SimpleNamespace(reply_language="auto"),
                                 stt=SimpleNamespace(language="auto"),
                                 ui=SimpleNamespace(language="es"))
    assert LocalVoiceProvider.from_runtime_config(auto_ui_es).language == "es"
    assert LocalVoiceProvider.from_runtime_config(SimpleNamespace()).language == "en"


@pytest.mark.asyncio
async def test_a_failed_engine_refuses_in_words_not_a_traceback() -> None:
    provider = LocalVoiceProvider(_settings(), language="de")
    engine = LocalVoiceProvider._shared(_settings())
    engine._client = FakeClient()
    engine.phase, engine.reason = "failed", "The local voice could not load: CUDA error 700"
    try:
        assert await provider.can_open_duplex_session() is False
        assert "CUDA" not in provider.duplex_unavailable_reason
        assert "konnte nicht starten" in provider.duplex_unavailable_reason  # i18n-allow
    finally:
        LocalVoiceProvider._engine = None


def test_the_model_comes_from_the_card_then_setup_then_the_default(tmp_path: Path) -> None:
    home = tmp_path / "engine"
    cfg = SimpleNamespace(voice_engine=SimpleNamespace(home=str(home), llm_model="",
                                                       tts="pocket", languages=["de", "en"]))
    assert EngineSettings.from_config(cfg).llm_model == "qwen3.5:4b"
    home.mkdir()
    (home / "setup.json").write_text(json.dumps({"llm_model": "granite4.2:8b"}),
                                     encoding="utf-8")
    assert EngineSettings.from_config(cfg).llm_model == "granite4.2:8b"
    cfg.voice_engine.llm_model = "qwen3.5:2b"
    assert EngineSettings.from_config(cfg).llm_model == "qwen3.5:2b"
    typed = JarvisConfig(voice_engine=VoiceEngineConfig(tts="nonsense", languages=[]))
    assert (typed.voice_engine.tts, typed.voice_engine.languages) == ("pocket", ["de", "en"])
