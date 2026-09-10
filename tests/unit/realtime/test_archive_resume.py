"""An archived conversation reaches the real duplex handshake, not only the UI."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.realtime.session import RealtimeVoiceSession
from tests.unit.state.test_chats_routes import _client, _make_app

from .test_session import FakeProvider, _cfg


def desktop_facade(brain: BrainManager) -> Any:
    """Exercise the actual nested desktop facade without starting native UI/audio."""
    source = Path(__file__).resolve().parents[3] / "jarvis/ui/desktop_app.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    facade = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "_DeferredVoiceBrain"
    )
    module = ast.Module(
        body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            facade,
        ],
        type_ignores=[],
    )
    namespace: dict[str, Any] = {"brain_holder": {"brain": brain}}
    exec(compile(ast.fix_missing_locations(module), str(source), "exec"), namespace)  # noqa: S102 - compile only the repository's own facade
    return namespace["_DeferredVoiceBrain"]()


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["desktop", "browser"])
async def test_resume_route_seeds_provider_and_delegate_once(surface: str) -> None:
    config = JarvisConfig()
    config.brain.primary = "fake"
    brain = BrainManager(config=config, bus=EventBus(), tools={})
    app, _ = await _make_app(with_session=True)
    app.state.brain = brain
    async with _client(app) as client:
        response = await client.post("/api/chats/voice/v1/resume")
    assert response.status_code == 200
    expected = (
        {"role": "user", "text": "how is the weather"},
        {"role": "assistant", "text": "Sunny."},
    )
    callback = desktop_facade(brain) if surface == "desktop" else brain
    sessions: list[RealtimeVoiceSession] = []

    async def send(_value: Any) -> None:
        pass  # Capture is unnecessary: the test inspects the actual provider input.

    async def start(identifier: str) -> tuple[RealtimeVoiceSession, FakeProvider]:
        provider = FakeProvider([])
        session = RealtimeVoiceSession(
            session_id=identifier,
            send_binary=send,
            send_json=send,
            config=_cfg(),
            provider=provider,
            brain=callback,
            surface=surface,
        )
        sessions.append(session)
        await session.handle_control({"type": "audio_start", "sample_rate": 16000})
        return session, provider

    try:
        first, provider = await start("resumed")
        assert provider.opened_with.history == expected
        assert [(message.role, message.content) for message in first._delegate_history] == [
            (item["role"], item["text"]) for item in expected
        ]
        # A newer archive selection belongs to the next call. A duplicate start
        # or provider reconnect in this call must retain its existing context.
        brain.seed_history([("user", "The other topic is private jets.")])
        await first.handle_control({"type": "audio_start", "sample_rate": 16000})
        assert first._history_seed() == expected
        _, second_provider = await start("different-archive")
        assert second_provider.opened_with.history == (
            {"role": "user", "text": "The other topic is private jets."},
        )
        _, fresh_provider = await start("fresh")
        assert fresh_provider.opened_with.history == ()
    finally:
        for session in sessions:
            await session.end(reason="client_stop")


def test_clearing_history_discards_pending_voice_context() -> None:
    brain = BrainManager(config=JarvisConfig(), bus=EventBus(), tools={})
    brain.seed_history([("user", "Old archive")])
    brain.clear_history()
    assert brain.take_voice_history_seed() == ()
    brain.seed_history([("user", "Another archive")])
    brain.seed_history([])
    assert brain.take_voice_history_seed() == ()


def test_desktop_speak_seed_reaches_shared_brain() -> None:
    brain = BrainManager(config=JarvisConfig(), bus=EventBus(), tools={})
    facade = desktop_facade(brain)
    facade.seed_history([("user", "Resume this exact conversation")])
    assert [(message.role, message.content) for message in facade.take_voice_history_seed()] == [
        ("user", "Resume this exact conversation")
    ]
    assert facade.take_voice_history_seed() == ()
