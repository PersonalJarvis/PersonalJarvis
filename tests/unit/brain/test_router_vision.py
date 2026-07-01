"""Unit tests for RouterBrain permanent vision inject (B5, wave 2).

Covers:
- Vision inject when the provider is active delivers exactly one ImageBlock
- No provider or paused: no images
- Exception in the provider -> text-only fallback, no crash
- The `VisionInjected` event is emitted with the correct hash/bytes
- `SYSTEM_PROMPT` contains the SCREEN-CONTEXT clause
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
    """Replaces the real BrainDispatcher during tests."""

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
    cfg.brain.worker = BrainTierConfig(provider="fake", model="fake-model")
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
# Silent desktop-action flag (manager side of the 2026-06-09 clarify fix)
# ----------------------------------------------------------------------


class _AggDispatcher:
    """Dispatcher stub that returns a pre-built aggregate (wordless turn whose
    only output is a single tool call). Signature mirrors the real
    BrainDispatcher so it slots into ``_build_dispatcher`` (which the manager
    calls with a keyword ``tools_override``).

    ``requested`` is the tool the model asked for (lands in ``tool_calls``);
    ``executed`` is the set that ACTUALLY ran (lands in ``executed_tool_names``).
    They differ when a guard blocks the call — the case the action-confirmation
    flag must NOT mistake for a real side effect."""

    def __init__(self, *, requested: str, executed: set[str] | None = None) -> None:
        self._requested = requested
        self._executed = executed if executed is not None else {requested}

    def tools_payload(self) -> list[dict[str, Any]]:
        return []

    async def dispatch(self, user_text: str, **_kw: Any) -> StreamingAggregate:
        agg = StreamingAggregate()
        agg.text = ""  # wordless — the exact bug condition (no narration)
        agg.finish_reason = "stop"
        agg.tool_calls = [
            {"name": self._requested, "input": {"goal": "open chrome"}, "id": "t1"}
        ]
        agg.executed_tool_names = set(self._executed)
        return agg


@pytest.mark.asyncio
async def test_generate_flags_executed_desktop_action_tool() -> None:
    """Manager side of the 2026-06-09 fix: when the winning turn SUCCESSFULLY
    executed a DESKTOP-ACTION tool (computer_use / open_app / …) but produced no
    narration text, ``generate`` (the method the live ``generate_stream`` path
    delegates to) sets ``_last_turn_executed_action_tool=True`` so the voice
    pipeline speaks a confirmation ("Erledigt.") instead of the clarifying
    question that made a successful Chrome-open look like incomprehension
    (data/jarvis_desktop.log 16:27)."""
    router, _recorder = _build_router()
    router.manager._build_dispatcher = (  # type: ignore[method-assign]
        lambda _brain, *, tools_override=None: _AggDispatcher(requested="computer_use")
    )

    await router.manager.generate("was ist das hier")  # i18n-allow
    assert router.manager._last_turn_executed_action_tool is True


@pytest.mark.asyncio
async def test_generate_does_not_flag_non_action_tool() -> None:
    """A wordless turn whose only tool is read-only (e.g. wiki_recall) is NOT a
    desktop action — the flag stays False so such a turn keeps the existing
    clarify-question behaviour."""
    router, _recorder = _build_router()
    router.manager._build_dispatcher = (  # type: ignore[method-assign]
        lambda _brain, *, tools_override=None: _AggDispatcher(requested="wiki_recall")
    )

    await router.manager.generate("was ist das hier")  # i18n-allow
    assert router.manager._last_turn_executed_action_tool is False


@pytest.mark.asyncio
async def test_generate_does_not_flag_requested_but_blocked_action_tool() -> None:
    """Regression for the 2026-06-09 review finding: a desktop-action tool the
    model REQUESTED but that a guard BLOCKED (e.g. computer_use refused on a
    how-to question) lands in ``tool_calls`` with success=False but NEVER in
    ``executed_tool_names``. The flag must stay False so the pipeline does NOT
    speak "Erledigt." for an action that never happened — it falls back to the
    clarifying question as before."""
    router, _recorder = _build_router()
    router.manager._build_dispatcher = (  # type: ignore[method-assign]
        lambda _brain, *, tools_override=None: _AggDispatcher(
            requested="computer_use", executed=set()
        )
    )

    await router.manager.generate("wie öffne ich Chrome?")  # i18n-allow
    assert router.manager._last_turn_executed_action_tool is False


# ----------------------------------------------------------------------
# Vision-inject tests
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_injects_image_when_provider_active() -> None:
    path, data = _make_png_file()
    try:
        obs = _make_obs(path=path, sha="deadbeef")
        provider = _FakeVisionProvider(obs=obs)
        router, recorder = _build_router(vision_provider=provider)

        [d async for d in router.handle("was ist das hier")]  # i18n-allow

        assert len(recorder.calls) == 1
        call = recorder.calls[0]
        assert call["user_text"] == "was ist das hier"  # i18n-allow
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

    # The utterance must carry a visual marker so the attach-on-reference gate
    # enters the vision path at all — otherwise the screenshot is skipped (by
    # design) and the failure under test never fires.
    with caplog.at_level(logging.WARNING, logger="jarvis.brain.router"):
        deltas = [d async for d in router.handle("was siehst du hier")]

    assert len(recorder.calls) == 1
    assert recorder.calls[0]["images"] == ()
    assert any(d.content for d in deltas)
    assert any("Vision-Inject fehlgeschlagen" in rec.message for rec in caplog.records)  # i18n-allow


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
# SYSTEM_PROMPT (grep-based)
# ----------------------------------------------------------------------


def test_system_prompt_contains_screen_context_section() -> None:
    assert "SCREEN-CONTEXT" in SYSTEM_PROMPT


def test_system_prompt_contains_context_not_task_clause() -> None:
    assert "Bild ist Kontext, kein Auftrag" in SYSTEM_PROMPT  # i18n-allow


def test_system_prompt_screen_context_before_action_section() -> None:
    # After the pure-delegator refactor (commit e09780b7) the action section
    # is now called "DELEGATOR-PRINZIP" / "ENTSCHEIDUNGSTABELLE" instead of
    # the old "Bei jeder Eingabe entscheidest" marker. What matters is only
    # that SCREEN-CONTEXT comes BEFORE the decision instruction, so the
    # router knows the image as context BEFORE it routes.
    idx_screen = SYSTEM_PROMPT.find("SCREEN-CONTEXT")
    idx_actions = SYSTEM_PROMPT.find("ENTSCHEIDUNGSTABELLE")
    assert idx_screen >= 0 and idx_actions >= 0
    assert idx_screen < idx_actions


# ----------------------------------------------------------------------
# Helper function
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

        [d async for d in router.handle("was ist das hier")]  # i18n-allow

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
