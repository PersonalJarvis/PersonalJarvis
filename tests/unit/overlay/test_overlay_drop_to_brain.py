"""End-to-end (GUI-free) proof of the OVERLAY (bar/mascot) drop → brain context.

The bar's tkdnd ``<<Drop>>`` handler calls ``drop_bridge.dispatch_drop(paths, text)``
(OS delivery of that event is proven separately on Windows + Linux). This test
proves the rest of the chain exactly as ``desktop_app`` wires it: dispatch_drop →
the registered ``_on_overlay_drop`` handler → ``ingest_drop`` → the brain's
SILENT context → and that the user's NEXT real turn actually uses it. No Tk, no
cursor — pure wiring.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from jarvis.brain.drop_context import ingest_drop, items_from_paths
from jarvis.brain.manager import BrainManager
from jarvis.brain.streaming import StreamingAggregate
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.overlay import drop_bridge


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

    async def dispatch(self, user_text, *, images=(), history=None, **_kwargs):
        self.calls.append({"history": history})
        agg = StreamingAggregate()
        agg.text = "reply"
        agg.finish_reason = "stop"
        return agg


def _manager() -> tuple[BrainManager, _RecordingDispatcher]:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    m = BrainManager(config=cfg, bus=EventBus(), tools={})
    rec = _RecordingDispatcher()
    m._build_fallback_chain = lambda _l: [("fake", "fake-model")]  # type: ignore[method-assign]
    m._get_brain = lambda _n, _mo: _FakeBrain()  # type: ignore[method-assign]
    m._build_dispatcher = lambda _b, *, tools_override=None: rec  # type: ignore[method-assign]
    return m, rec


def teardown_function() -> None:
    drop_bridge.set_drop_handler(None)


@pytest.mark.asyncio
async def test_bar_drop_reaches_brain_and_next_turn_uses_it(tmp_path) -> None:
    manager, rec = _manager()
    loop = asyncio.get_running_loop()
    done = asyncio.Event()

    # Wire the overlay drop EXACTLY as jarvis/ui/desktop_app.py does.
    def _on_overlay_drop(paths: list[str], text: str) -> None:
        items = items_from_paths(paths) if paths else []
        dragged = (text or "").strip() or None
        if not items and dragged is None:
            return

        async def _run() -> None:
            await ingest_drop(
                brain=manager, thread_id="default", items=items, dragged_text=dragged
            )
            done.set()

        asyncio.run_coroutine_threadsafe(_run(), loop)

    drop_bridge.set_drop_handler(_on_overlay_drop)

    # A real dropped file, as the bar would hand over a path.
    f = tmp_path / "report.txt"
    f.write_text("the bar marker is BAR_TOKEN_777, remember it")

    # This is precisely what the bar's tkdnd <<Drop>> handler calls.
    handled = drop_bridge.dispatch_drop([str(f)], "")
    assert handled is True
    await asyncio.wait_for(done.wait(), timeout=5)

    # The dropped content is now SILENT context on the brain (no turn happened).
    assert rec.calls == []
    assert any("BAR_TOKEN_777" in str(m.content) for m in manager._history)

    # The user's NEXT real turn (voice or chat → generate) USES it.
    await manager.generate("what is the marker?", trace_id=uuid4(), use_history=True)
    hist = " ".join(str(getattr(m, "content", "")) for m in (rec.calls[0]["history"] or []))
    assert "BAR_TOKEN_777" in hist
