"""Regression tests for vision inject in the production BrainManager path."""
from __future__ import annotations

import base64
import time
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.brain.streaming import StreamingAggregate
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.events import VisionInjected
from jarvis.core.protocols import BrainDelta, BrainRequest, Observation

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
    b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT"
    b"\x08\xd7c\xf8\xcf\xc0\x00\x00\x03\x01\x01"
    b"\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _FakeBrain:
    name = "fake"
    context_window = 8192
    supports_tools = True
    supports_vision = True

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        yield BrainDelta(content="ok")
        yield BrainDelta(finish_reason="stop")


class _RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def dispatch(
        self,
        user_text,
        *,
        images=(),
        history=None,
        trace_id=None,
        intent_level=None,
        text_consumer=None,
        ack_emitter=None,
        **_kwargs,
    ):
        self.calls.append({
            "user_text": user_text,
            "images": images,
            "history": history,
            "trace_id": trace_id,
            "intent_level": intent_level,
            "text_consumer": text_consumer,
        })
        agg = StreamingAggregate()
        agg.text = "Ich sehe ein echtes Fenster."
        agg.finish_reason = "stop"
        return agg


class _FakeVisionProvider:
    def __init__(
        self,
        obs: Observation | None = None,
        *,
        paused: bool = False,
        exc: Exception | None = None,
    ) -> None:
        self._obs = obs
        self._paused = paused
        self._exc = exc

    @property
    def is_paused(self) -> bool:
        return self._paused

    async def current(self) -> Observation:
        if self._exc is not None:
            raise self._exc
        assert self._obs is not None
        return self._obs


def _manager_with_recorder() -> tuple[BrainManager, _RecordingDispatcher]:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    manager = BrainManager(config=cfg, bus=EventBus(), tools={})
    recorder = _RecordingDispatcher()
    manager._build_fallback_chain = lambda _level: [("fake", "fake-model")]  # type: ignore[method-assign]
    manager._get_brain = lambda _name, _model: _FakeBrain()  # type: ignore[method-assign]
    manager._build_dispatcher = lambda _brain, *, tools_override=None: recorder  # type: ignore[method-assign]
    return manager, recorder


def _obs(path, sha: str = "cafebabe1234567890") -> Observation:
    return Observation(
        trace_id=uuid4(),
        timestamp_ns=time.time_ns(),
        screenshot_path=path,
        screenshot_hash=sha,
        nodes=(),
        window_title="Chrome",
        active_pid=123,
        source="screenshot_only",
    )


@pytest.mark.asyncio
async def test_brain_manager_injects_vision_image(tmp_path) -> None:
    img = tmp_path / "screen.png"
    img.write_bytes(PNG_BYTES)
    manager, recorder = _manager_with_recorder()
    manager._vision_provider = _FakeVisionProvider(_obs(str(img)))

    result = await manager.generate("was siehst du", use_history=False)

    assert result == "Ich sehe ein echtes Fenster."
    assert len(recorder.calls) == 1
    images = recorder.calls[0]["images"]
    assert len(images) == 1
    assert images[0].mime == "image/png"
    assert images[0].source_hash == "cafebabe1234567890"
    assert base64.b64decode(images[0].data_b64) == PNG_BYTES


@pytest.mark.asyncio
async def test_brain_manager_emits_vision_injected_event(tmp_path) -> None:
    img = tmp_path / "screen.png"
    img.write_bytes(PNG_BYTES)
    manager, _ = _manager_with_recorder()
    events: list[VisionInjected] = []

    async def _collect(event: VisionInjected) -> None:
        events.append(event)

    manager._bus.subscribe(VisionInjected, _collect)
    manager._vision_provider = _FakeVisionProvider(_obs(str(img), sha="feedface12345678"))

    await manager.generate("was siehst du", use_history=False)

    assert len(events) == 1
    assert events[0].screenshot_hash == "feedface12345678"
    assert events[0].bytes_size >= len(PNG_BYTES) - 2


@pytest.mark.asyncio
async def test_brain_manager_skips_vision_when_paused() -> None:
    manager, recorder = _manager_with_recorder()
    manager._vision_provider = _FakeVisionProvider(paused=True)

    await manager.generate("hallo", use_history=False)

    assert recorder.calls[0]["images"] == ()


@pytest.mark.asyncio
async def test_brain_manager_continues_on_vision_failure(caplog) -> None:
    manager, recorder = _manager_with_recorder()
    manager._vision_provider = _FakeVisionProvider(exc=ValueError("no screenshot_path"))

    # Non-smalltalk utterance so the Wave-1 conditional-vision gate keeps the
    # screenshot and actually exercises the vision-failure path (smalltalk like
    # "hallo" is now skipped before the provider is ever called).
    with caplog.at_level("ERROR", logger="jarvis.brain.manager"):
        await manager.generate("was siehst du", use_history=False)

    assert recorder.calls[0]["images"] == ()
    assert any("Vision-Inject fehlgeschlagen" in rec.message for rec in caplog.records)  # i18n-allow
