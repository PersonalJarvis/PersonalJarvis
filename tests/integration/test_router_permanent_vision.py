"""E2E-Integration-Tests fuer Permanent-Vision (Wave-3 B9).

Setup: echte VisionContextProvider + FakeVisionEngine + FakeBrain +
RouterBrain. Testet den kompletten Vision-Capture → Provider-Cache →
Router-Inject → BrainDispatcher.dispatch-Pfad.

Welle-4-Migration: der ehemals "kritische" Test
``test_sub_jarvis_isolated_from_vision`` testete dass der ``SubJarvisManager``
keine Vision-Images weiterreicht. Sub-Jarvis-Tier wurde durch die OpenClaw-
Bridge ersetzt (siehe docs/openclaw-bridge.md §11) — der OpenClaw-Worker
ist ein externer Subprocess ohne Image-Kanal, Vision-Isolation strukturell
garantiert. Dieser Test wurde entfernt.
"""
from __future__ import annotations

import asyncio
import base64
import io
import os
import tempfile
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest

pytestmark = pytest.mark.integration


# ======================================================================
# Gemeinsame Fakes
# ======================================================================


def _make_png_file(size_kb: int = 1) -> tuple[str, bytes]:
    """PNG-Datei mit Fake-Bytes. `size_kb` steuert die Byte-Groesse."""
    header = b"\x89PNG\r\n\x1a\n"
    filler = b"x" * max(0, size_kb * 1024 - len(header))
    data = header + filler
    fh = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    try:
        fh.write(data)
        fh.flush()
    finally:
        fh.close()
    return fh.name, data


class FakeVisionEngine:
    """Emuliert VisionEngine.observe() ohne mss/UIA.

    Liefert eine Observation mit einer echten (temp-)PNG-Datei, damit der
    Router-Helper `_read_observation_png_b64` File-I/O machen kann.
    """

    def __init__(
        self,
        *,
        png_path: str,
        png_hash: str = "h-engine",
        raise_once: bool = False,
    ) -> None:
        self._png_path = png_path
        self._png_hash = png_hash
        self._raise_once = raise_once
        self.calls = 0

    async def observe(self, *, mode: str = "screenshot", **_: Any):
        from jarvis.core.protocols import Observation
        self.calls += 1
        if self._raise_once and self.calls == 1:
            raise RuntimeError("engine boom")
        return Observation(
            trace_id=uuid4(),
            timestamp_ns=time.time_ns(),
            screenshot_path=self._png_path,
            screenshot_hash=self._png_hash,
            nodes=(),
            window_title="IntegrationTest",
            active_pid=os.getpid(),
            source="screenshot_only",
            pruning_stats={},
        )


# ======================================================================
# Test 1: Router injiziert Image bei aktivem Provider
# ======================================================================


@pytest.mark.asyncio
async def test_router_handle_includes_image():
    """Full-Stack: VisionContextProvider + RouterBrain → Brain sieht Image."""
    from jarvis.brain.router import RouterBrain
    from jarvis.brain.streaming import StreamingAggregate
    from jarvis.core.bus import EventBus
    from jarvis.core.config import (
        BrainProviderConfig,
        BrainTierConfig,
        JarvisConfig,
    )
    from jarvis.core.protocols import (
        BrainDelta,
        BrainRequest,
        ExecutionContext,
        ImageBlock,
        ToolResult,
    )
    from jarvis.vision.context_provider import VisionContextProvider

    path, data = _make_png_file(size_kb=2)
    try:
        engine = FakeVisionEngine(png_path=path, png_hash="e2e-hash")
        provider = VisionContextProvider(engine, refresh_interval_s=0.05)
        await provider.start()
        # Warte auf erste Capture
        await asyncio.sleep(0.1)

        # Router-Setup mit Fake-Brain
        class _FakeTool:
            name = "bash"
            description = ""
            risk_tier = "monitor"
            schema = {"type": "object", "properties": {}}
            async def execute(self, args, ctx):
                return ToolResult(success=True, output="ok")

        class _FakeBrain:
            name = "fake"
            context_window = 8192
            supports_tools = True
            supports_vision = True
            def __init__(self):
                self.requests: list[BrainRequest] = []
            async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
                self.requests.append(req)
                yield BrainDelta(content="ok")
                yield BrainDelta(finish_reason="stop")

        class _NoopToolExecutor:
            async def execute(self, tool, args, **_):
                return await tool.execute(args, ctx=None)

        class _RecordingDispatcher:
            def __init__(self):
                self.calls = []
            def tools_payload(self):
                return []
            async def dispatch(self, user_text, *, images=(), history=None, trace_id=None, ack_emitter=None, **kwargs):
                self.calls.append({"user_text": user_text, "images": images})
                agg = StreamingAggregate()
                agg.text = "ok"
                agg.finish_reason = "stop"
                return agg

        cfg = JarvisConfig()
        cfg.brain.providers["fake"] = BrainProviderConfig(model="fake", deep_model="fake")
        cfg.brain.router = BrainTierConfig(
            provider="fake", model="fake",
            fallback_provider="fake", fallback_model="fake",
        )
        cfg.brain.worker = BrainTierConfig(provider="fake", model="fake")

        bus = EventBus()
        router = RouterBrain(
            cfg, bus,
            tools={"bash": _FakeTool()},
            tool_executor=_NoopToolExecutor(),
            vision_provider=provider,
        )
        router.manager._brain_cache[("fake", "fake")] = _FakeBrain()
        recorder = _RecordingDispatcher()
        router.manager._build_dispatcher = lambda _b: recorder  # type: ignore[method-assign]

        [_ async for _ in router.handle("was siehst du")]

        await provider.stop()

        assert len(recorder.calls) == 1
        images = recorder.calls[0]["images"]
        assert len(images) == 1
        img = images[0]
        assert isinstance(img, ImageBlock)
        assert img.mime == "image/png"
        assert img.source_hash == "e2e-hash"
        assert base64.b64decode(img.data_b64) == data
    finally:
        os.unlink(path)


# ======================================================================
# Test 2: OpenClaw-Worker-Isolation
#
# Welle-4-Migration: Der ursprueliche ``test_sub_jarvis_isolated_from_vision``-
# Test verifizierte dass ``SubJarvisManager.run(task)`` keine Vision-Images
# weiterreicht. ``SubJarvisManager`` wurde durch die OpenClaw-Bridge ersetzt
# (siehe docs/openclaw-bridge.md §11). Vision-Isolation ist beim OpenClaw-
# Worker strukturell garantiert: er ist ein externer Subprocess, der nur
# einen ``--message <prompt>``-String bekommt — keinen Image-Kanal. Der
# alte Test wird daher entfernt; ein vergleichbarer Smoke-Test fuer den
# OpenClaw-Subprocess-Stream lebt in ``tests/integration/test_openclaw_*``.
# ======================================================================


# ======================================================================
# Test 3: Privacy-Pause droppt Image
# ======================================================================


@pytest.mark.asyncio
async def test_privacy_pause_drops_image():
    """Provider.pause() → RouterBrain.handle() → images=()."""
    from jarvis.brain.router import RouterBrain
    from jarvis.brain.streaming import StreamingAggregate
    from jarvis.core.bus import EventBus
    from jarvis.core.config import (
        BrainProviderConfig, BrainTierConfig, JarvisConfig,
    )
    from jarvis.vision.context_provider import VisionContextProvider

    path, _ = _make_png_file(size_kb=1)
    try:
        engine = FakeVisionEngine(png_path=path)
        provider = VisionContextProvider(engine, refresh_interval_s=0.05)
        await provider.start()
        await asyncio.sleep(0.1)

        class _FakeBrain:
            name = "fake"; context_window = 8192
            supports_tools = True; supports_vision = True
            async def complete(self, req):
                yield __import__("jarvis.core.protocols", fromlist=["BrainDelta"]).BrainDelta(
                    content="ok")

        class _RecordingDispatcher:
            def __init__(self):
                self.calls = []
            def tools_payload(self): return []
            async def dispatch(self, user_text, *, images=(), history=None, trace_id=None, ack_emitter=None, **kwargs):
                self.calls.append({"images": images})
                agg = StreamingAggregate(); agg.text = "ok"; agg.finish_reason = "stop"
                return agg

        class _FakeTool:
            name = "bash"; description = ""; risk_tier = "monitor"
            schema = {"type": "object", "properties": {}}
            async def execute(self, args, ctx): ...

        cfg = JarvisConfig()
        cfg.brain.providers["fake"] = BrainProviderConfig(model="fake", deep_model="fake")
        cfg.brain.router = BrainTierConfig(provider="fake", model="fake",
                                           fallback_provider="fake", fallback_model="fake")
        cfg.brain.worker = BrainTierConfig(provider="fake", model="fake")
        bus = EventBus()
        router = RouterBrain(cfg, bus, tools={"bash": _FakeTool()},
                             tool_executor=object(), vision_provider=provider)
        router.manager._brain_cache[("fake", "fake")] = _FakeBrain()
        recorder = _RecordingDispatcher()
        router.manager._build_dispatcher = lambda _b: recorder  # type: ignore[method-assign]

        provider.pause()

        [_ async for _ in router.handle("hallo")]

        assert recorder.calls[0]["images"] == ()
        await provider.stop()
    finally:
        os.unlink(path)


# ======================================================================
# Test 4: IDLE-State pausiert Provider-Loop (via Pipeline-Helper)
# ======================================================================


def test_idle_state_pauses_provider_loop():
    """Pipeline._maybe_toggle_vision_on_state('IDLE') → provider.pause()."""
    from jarvis.speech.pipeline import SpeechPipeline
    from types import SimpleNamespace

    class _Prov:
        def __init__(self):
            self.paused_calls = 0
            self.resumed_calls = 0
            self._p = False
        def pause(self): self.paused_calls += 1; self._p = True
        def resume(self): self.resumed_calls += 1; self._p = False
        @property
        def is_paused(self): return self._p

    prov = _Prov()
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._vision_provider = prov
    pipe._config = SimpleNamespace(brain=SimpleNamespace(router=SimpleNamespace(vision=SimpleNamespace(
        pause_on_idle=True,
        voice_pause_phrase_de="privacy", voice_pause_phrase_en="privacy mode",
        voice_resume_phrase_de="du darfst wieder sehen", voice_resume_phrase_en="vision back on",
    ))))
    pipe._supervisor = None

    pipe._maybe_toggle_vision_on_state("IDLE")
    assert prov.paused_calls == 1

    pipe._maybe_toggle_vision_on_state("LISTENING")
    assert prov.resumed_calls == 1


# ======================================================================
# Test 5: Provider recovert nach Engine-Fehler
# ======================================================================


@pytest.mark.asyncio
async def test_provider_recovers_from_engine_error():
    """Engine wirft 1x, naechster Tick klappt — Loop stirbt nicht."""
    from jarvis.vision.context_provider import VisionContextProvider

    path, _ = _make_png_file(size_kb=1)
    try:
        engine = FakeVisionEngine(png_path=path, raise_once=True)
        provider = VisionContextProvider(engine, refresh_interval_s=0.03)
        await provider.start()

        # Warte mehrere Ticks — beim ersten wirft Engine, danach klappt's.
        await asyncio.sleep(0.2)
        obs = await provider.current()
        assert obs is not None
        assert engine.calls >= 2
        await provider.stop()
    finally:
        os.unlink(path)


# ======================================================================
# Test 6: Image-Block ≤ 500 KB
# ======================================================================


@pytest.mark.asyncio
async def test_image_block_size_under_budget():
    """Captured Image bleibt unter 500-KB-Budget.

    Mit einem 300-KB-PNG als Input: Der Provider-Pfad encodet es als
    base64 und reicht es durch — assertet dass die Rohdaten unter Budget
    bleiben (Base64-Overhead ~33% wird separat gemessen).
    """
    from jarvis.brain.router import _read_observation_png_b64
    from jarvis.core.protocols import Observation

    path, data = _make_png_file(size_kb=300)
    try:
        obs = Observation(
            trace_id=uuid4(),
            timestamp_ns=time.time_ns(),
            screenshot_path=path,
            screenshot_hash="h-budget",
            nodes=(),
            window_title="budget",
            active_pid=0,
            source="screenshot_only",
            pruning_stats={},
        )
        b64 = await _read_observation_png_b64(obs)
        raw_bytes = len(base64.b64decode(b64))
        assert raw_bytes <= 500 * 1024, f"Image ueber Budget: {raw_bytes} bytes"
        assert raw_bytes == len(data)
    finally:
        os.unlink(path)
