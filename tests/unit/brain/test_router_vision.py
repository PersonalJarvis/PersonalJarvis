"""Unit-Tests fuer RouterBrain Permanent-Vision-Inject (B5, Wave-2).

Deckt ab:
- Vision-Inject wenn Provider aktiv liefert genau einen ImageBlock
- Ohne Provider oder bei Pause: keine Images
- Exception im Provider -> Text-Only-Fallback, kein Crash
- `VisionInjected`-Event wird mit korrektem Hash/Bytes emittiert
- `SYSTEM_PROMPT` enthaelt die SCREEN-CONTEXT-Klausel
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import tempfile
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest

from jarvis.brain import router as router_mod
from jarvis.brain.router import SYSTEM_PROMPT, RouterBrain
from jarvis.brain.streaming import StreamingAggregate
from jarvis.core.bus import EventBus
from jarvis.core.config import (
    BrainProviderConfig,
    BrainRouterPolicyConfig,
    BrainTierConfig,
    JarvisConfig,
)
from jarvis.core.events import VisionInjected
from jarvis.core.protocols import (
    BrainDelta,
    BrainRequest,
    ExecutionContext,
    ImageBlock,
    Observation,
    ToolResult,
)


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------


class _FakeTool:
    name = "bash"
    description = "Run shell commands."
    risk_tier = "monitor"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"command": {"type": "string"}},
    }

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        return ToolResult(success=True, output="ok")


class _FakeBrain:
    name = "fake"
    context_window = 8192
    supports_tools = True
    supports_vision = True
    model = "fake-model"

    def __init__(self) -> None:
        self.requests: list[BrainRequest] = []

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.requests.append(req)
        yield BrainDelta(content="ok")
        yield BrainDelta(finish_reason="stop")


class _NoopToolExecutor:
    async def execute(self, tool: Any, args: dict[str, Any], **_: Any) -> ToolResult:
        return await tool.execute(args, ctx=None)  # type: ignore[arg-type]


class _RecordingDispatcher:
    """Ersetzt den echten BrainDispatcher waehrend Tests."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def tools_payload(self) -> list[dict[str, Any]]:
        return []

    async def dispatch(
        self,
        user_text: str,
        *,
        images: tuple[ImageBlock, ...] = (),
        history: Any = None,
        trace_id: UUID | None = None,
        ack_emitter: Any = None,
        **_kwargs: Any,
    ) -> StreamingAggregate:
        self.calls.append({
            "user_text": user_text,
            "images": images,
            "history": history,
            "trace_id": trace_id,
            "ack_emitter": ack_emitter,
        })
        agg = StreamingAggregate()
        agg.text = "ok"
        agg.finish_reason = "stop"
        return agg


class _FakeVisionProvider:
    def __init__(
        self,
        *,
        obs: Observation | None = None,
        raise_on_current: Exception | None = None,
    ) -> None:
        self._obs = obs
        self._paused = False
        self._raise = raise_on_current

    @property
    def is_paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    async def current(self, *, force_refresh: bool = False) -> Observation:
        if self._raise is not None:
            raise self._raise
        assert self._obs is not None
        return self._obs


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _build_router_config() -> JarvisConfig:
    cfg = JarvisConfig()
    cfg.brain.providers["fake"] = BrainProviderConfig(
        model="fake-model", deep_model="fake-model"
    )
    cfg.brain.router = BrainTierConfig(
        provider="fake",
        model="fake-model",
        fallback_provider="fake",
        fallback_model="fake-model",
        policy=BrainRouterPolicyConfig(
            escalate_on_uncertainty=True,
            default_intent_on_low_confidence="spawn_worker",
        ),
    )
    cfg.brain.sub_jarvis = BrainTierConfig(provider="fake", model="fake-model")
    return cfg


def _make_png_file() -> tuple[str, bytes]:
    data = b"\x89PNG\r\n\x1a\n"
    fh = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    try:
        fh.write(data)
        fh.flush()
    finally:
        fh.close()
    return fh.name, data


def _make_jpeg_file() -> tuple[str, bytes]:
    data = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9"
    fh = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    try:
        fh.write(data)
        fh.flush()
    finally:
        fh.close()
    return fh.name, data


def _make_obs(path: str | None = "/tmp/fake.png", sha: str = "abc123") -> Observation:
    return Observation(
        trace_id=uuid4(),
        timestamp_ns=time.time_ns(),
        screenshot_path=path,
        screenshot_hash=sha,
        nodes=(),
        window_title="Test",
        active_pid=0,
        source="screenshot_only",
    )


def _build_router(
    *,
    vision_provider: Any | None = None,
    bus: EventBus | None = None,
) -> tuple[RouterBrain, _RecordingDispatcher]:
    cfg = _build_router_config()
    bus = bus or EventBus()
    tools = {"bash": _FakeTool()}
    router = RouterBrain(
        cfg,
        bus,
        tools=tools,
        tool_executor=_NoopToolExecutor(),
        vision_provider=vision_provider,
    )
    fb = _FakeBrain()
    router.manager._brain_cache[("fake", "fake-model")] = fb
    recorder = _RecordingDispatcher()
    router.manager._build_dispatcher = lambda _brain: recorder  # type: ignore[method-assign]
    return router, recorder


# ----------------------------------------------------------------------
# Vision-Inject Tests
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_injects_image_when_provider_active() -> None:
    path, data = _make_png_file()
    try:
        obs = _make_obs(path=path, sha="deadbeef")
        provider = _FakeVisionProvider(obs=obs)
        router, recorder = _build_router(vision_provider=provider)

        [d async for d in router.handle("was ist das hier")]

        assert len(recorder.calls) == 1
        call = recorder.calls[0]
        assert call["user_text"] == "was ist das hier"
        images = call["images"]
        assert isinstance(images, tuple)
        assert len(images) == 1
        img = images[0]
        assert isinstance(img, ImageBlock)
        assert img.mime == "image/png"
        assert img.source_hash == "deadbeef"
        assert base64.b64decode(img.data_b64) == data
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_router_no_vision_when_provider_none() -> None:
    router, recorder = _build_router(vision_provider=None)
    [d async for d in router.handle("hallo")]
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["images"] == ()


@pytest.mark.asyncio
async def test_router_no_vision_when_paused() -> None:
    obs = _make_obs()
    provider = _FakeVisionProvider(obs=obs)
    provider.pause()
    router, recorder = _build_router(vision_provider=provider)
    [d async for d in router.handle("privacy test")]
    assert recorder.calls[0]["images"] == ()


@pytest.mark.asyncio
async def test_router_continues_on_vision_failure(caplog: pytest.LogCaptureFixture) -> None:
    provider = _FakeVisionProvider(raise_on_current=RuntimeError("engine kaputt"))
    router, recorder = _build_router(vision_provider=provider)

    with caplog.at_level(logging.WARNING, logger="jarvis.brain.router"):
        deltas = [d async for d in router.handle("trotzdem weiter")]

    assert len(recorder.calls) == 1
    assert recorder.calls[0]["images"] == ()
    assert any(d.content for d in deltas)
    assert any("Vision-Inject fehlgeschlagen" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_router_emits_vision_injected_event() -> None:
    path, data = _make_png_file()
    try:
        obs = _make_obs(path=path, sha="cafef00d")
        bus = EventBus()
        received: list[VisionInjected] = []

        async def _collector(ev: VisionInjected) -> None:
            received.append(ev)

        bus.subscribe(VisionInjected, _collector)

        provider = _FakeVisionProvider(obs=obs)
        router, _ = _build_router(vision_provider=provider, bus=bus)

        [d async for d in router.handle("was siehst du")]

        await asyncio.sleep(0.01)

        assert len(received) == 1
        ev = received[0]
        assert ev.screenshot_hash == "cafef00d"
        assert ev.bytes_size >= len(data) - 2
        assert ev.capture_age_ms >= 0
    finally:
        os.unlink(path)


# ----------------------------------------------------------------------
# SYSTEM_PROMPT (grep-basiert)
# ----------------------------------------------------------------------


def test_system_prompt_contains_screen_context_section() -> None:
    assert "SCREEN-CONTEXT" in SYSTEM_PROMPT


def test_system_prompt_contains_context_not_task_clause() -> None:
    assert "Bild ist Kontext, kein Auftrag" in SYSTEM_PROMPT


def test_system_prompt_screen_context_before_action_section() -> None:
    # Nach dem Pure-Delegator-Refactor (Commit e09780b7) heisst die Action-
    # Sektion jetzt "DELEGATOR-PRINZIP" / "ENTSCHEIDUNGSTABELLE" statt des
    # alten "Bei jeder Eingabe entscheidest"-Markers. Wichtig bleibt nur,
    # dass SCREEN-CONTEXT VOR der Entscheidungs-Anweisung steht, damit der
    # Router das Bild als Kontext kennt BEVOR er routet.
    idx_screen = SYSTEM_PROMPT.find("SCREEN-CONTEXT")
    idx_actions = SYSTEM_PROMPT.find("ENTSCHEIDUNGSTABELLE")
    assert idx_screen >= 0 and idx_actions >= 0
    assert idx_screen < idx_actions


# ----------------------------------------------------------------------
# Helper-Function
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_observation_png_b64_returns_matching_base64() -> None:
    path, data = _make_png_file()
    try:
        obs = _make_obs(path=path)
        b64 = await router_mod._read_observation_png_b64(obs)
        assert base64.b64decode(b64) == data
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_router_injects_jpeg_with_matching_mime() -> None:
    path, data = _make_jpeg_file()
    try:
        obs = _make_obs(path=path, sha="jpgbeef")
        provider = _FakeVisionProvider(obs=obs)
        router, recorder = _build_router(vision_provider=provider)

        [d async for d in router.handle("was ist das hier")]

        img = recorder.calls[0]["images"][0]
        assert img.mime == "image/jpeg"
        assert img.source_hash == "jpgbeef"
        assert base64.b64decode(img.data_b64) == data
    finally:
        os.unlink(path)


@pytest.mark.asyncio
async def test_read_observation_png_b64_raises_when_path_none() -> None:
    obs = _make_obs(path=None)
    with pytest.raises(ValueError):
        await router_mod._read_observation_png_b64(obs)
