"""Unit tests for VisionEngine mode selection and cache integration.

Works against fake sources — we want to test the heuristic, not
capture real screenshots/UIA trees.
"""
from __future__ import annotations

import time
from typing import Literal
from unittest.mock import patch
from uuid import uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import ObservationCaptured
from jarvis.core.protocols import CancelToken, Observation, UIANode
from jarvis.vision.cache import VisionCache
from jarvis.vision.engine import VisionEngine


class _FakeScreenshotSource:
    name = "fake-screenshot"
    kind: Literal["screenshot", "ui_tree", "composite"] = "screenshot"

    def __init__(self, hash_: str = "shot-hash") -> None:
        self.hash = hash_
        self.calls = 0
        self.closed = False

    async def observe(
        self,
        *,
        cancel_token: CancelToken | None = None,
        window_title_filter: str | None = None,  # noqa: ARG002
    ) -> Observation:
        self.calls += 1
        if cancel_token is not None and cancel_token.is_cancelled():
            raise RuntimeError("cancelled")
        return Observation(
            trace_id=uuid4(),
            timestamp_ns=time.time_ns(),
            screenshot_path=f"blobs/{self.hash}.png",  # noqa: S108 — fake path for tests
            screenshot_hash=self.hash,
            nodes=(),
            window_title="",
            active_pid=0,
            source="screenshot_only",
            pruning_stats={},
        )

    async def close(self) -> None:
        self.closed = True


class _FakeUIATreeSource:
    name = "fake-uia"
    kind: Literal["screenshot", "ui_tree", "composite"] = "ui_tree"

    def __init__(
        self,
        *,
        nodes: tuple[UIANode, ...] = (),
        window_title: str = "Notepad",
        source: Literal["full", "screenshot_only", "ui_tree_only"] = "ui_tree_only",
    ) -> None:
        self.nodes = nodes
        self.window_title = window_title
        self.source = source
        self.calls = 0
        self.closed = False

    async def observe(
        self,
        *,
        cancel_token: CancelToken | None = None,
        window_title_filter: str | None = None,
    ) -> Observation:
        self.calls += 1
        if cancel_token is not None and cancel_token.is_cancelled():
            raise RuntimeError("cancelled")
        return Observation(
            trace_id=uuid4(),
            timestamp_ns=time.time_ns(),
            screenshot_path=None,
            screenshot_hash="tree-hash",
            nodes=self.nodes,
            window_title=window_title_filter or self.window_title,
            active_pid=123,
            source=self.source,
            pruning_stats={"nodes_before": 10, "nodes_after": len(self.nodes),
                           "depth_used": 6},
        )

    async def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Mode-Selection-Heuristik
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auto_mode_picks_screenshot_for_chrome():
    engine = VisionEngine(
        screenshot_source=_FakeScreenshotSource(),
        uia_source=_FakeUIATreeSource(),
    )
    shot = engine._screenshot_source
    uia = engine._uia_source
    obs = await engine.observe(mode="auto", window_title_filter="Chrome - GitHub")
    assert obs.source == "screenshot_only"
    assert shot.calls == 1
    assert uia.calls == 0


@pytest.mark.asyncio
async def test_auto_mode_picks_composite_for_unknown_title():
    engine = VisionEngine(
        screenshot_source=_FakeScreenshotSource(),
        uia_source=_FakeUIATreeSource(window_title="Notepad"),
    )
    obs = await engine.observe(mode="auto", window_title_filter="Notepad - Untitled")
    assert obs.source == "full"
    assert engine._screenshot_source.calls == 1
    assert engine._uia_source.calls == 1


@pytest.mark.asyncio
async def test_auto_mode_picks_screenshot_for_vscode():
    engine = VisionEngine(
        screenshot_source=_FakeScreenshotSource(),
        uia_source=_FakeUIATreeSource(),
    )
    obs = await engine.observe(mode="auto", window_title_filter="Visual Studio Code")
    assert obs.source == "screenshot_only"
    assert engine._uia_source.calls == 0


@pytest.mark.asyncio
async def test_auto_mode_picks_screenshot_for_slack():
    engine = VisionEngine(
        screenshot_source=_FakeScreenshotSource(),
        uia_source=_FakeUIATreeSource(),
    )
    obs = await engine.observe(mode="auto", window_title_filter="Slack | Channel")
    assert obs.source == "screenshot_only"


@pytest.mark.asyncio
async def test_explicit_ui_tree_mode_bypasses_heuristic():
    engine = VisionEngine(
        screenshot_source=_FakeScreenshotSource(),
        uia_source=_FakeUIATreeSource(),
    )
    obs = await engine.observe(mode="ui_tree", window_title_filter="Chrome")
    # Obwohl Chrome in Text-heavy-Hints ist, respektiert explicit mode.
    assert obs.source == "ui_tree_only"
    assert engine._screenshot_source.calls == 0
    assert engine._uia_source.calls == 1


@pytest.mark.asyncio
async def test_explicit_screenshot_mode_bypasses_heuristic():
    engine = VisionEngine(
        screenshot_source=_FakeScreenshotSource(),
        uia_source=_FakeUIATreeSource(),
    )
    obs = await engine.observe(mode="screenshot", window_title_filter="Notepad")
    assert obs.source == "screenshot_only"
    assert engine._uia_source.calls == 0


# ---------------------------------------------------------------------------
# Composite-Merge
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_composite_merges_screenshot_and_tree():
    nodes = (UIANode(role="Button", name="OK"),)
    engine = VisionEngine(
        screenshot_source=_FakeScreenshotSource(hash_="abc123"),
        uia_source=_FakeUIATreeSource(nodes=nodes, window_title="Notepad"),
    )
    obs = await engine.observe(mode="composite")
    assert obs.source == "full"
    assert obs.screenshot_hash == "abc123"
    assert obs.window_title == "Notepad"
    assert obs.nodes == nodes


@pytest.mark.asyncio
async def test_composite_falls_back_to_screenshot_only_on_uia_overflow():
    engine = VisionEngine(
        screenshot_source=_FakeScreenshotSource(),
        uia_source=_FakeUIATreeSource(source="screenshot_only"),
    )
    obs = await engine.observe(mode="composite")
    assert obs.source == "screenshot_only"
    assert obs.nodes == ()


# ---------------------------------------------------------------------------
# Cache-Integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_hit_reuses_last_observation():
    shot = _FakeScreenshotSource(hash_="stable")
    uia = _FakeUIATreeSource(window_title="Notepad")
    engine = VisionEngine(
        screenshot_source=shot,
        uia_source=uia,
        cache=VisionCache(capacity=3),
    )
    obs_1 = await engine.observe(mode="composite")
    obs_2 = await engine.observe(mode="composite")
    # Second observation was pulled from the cache — trace_id is identical.
    assert obs_1.trace_id == obs_2.trace_id


@pytest.mark.asyncio
async def test_cache_invalidates_on_window_change():
    shot = _FakeScreenshotSource(hash_="stable")
    uia = _FakeUIATreeSource(window_title="Notepad")
    engine = VisionEngine(
        screenshot_source=shot,
        uia_source=uia,
        cache=VisionCache(capacity=3),
    )
    obs_1 = await engine.observe(mode="composite", window_title_filter="Notepad")
    obs_2 = await engine.observe(mode="composite", window_title_filter="Word")
    assert obs_1.trace_id != obs_2.trace_id


# ---------------------------------------------------------------------------
# CancelToken
# ---------------------------------------------------------------------------

class _Cancelled:
    """Minimal CancelToken stub for tests."""

    def __init__(self) -> None:
        self._cancelled = False
        self._reason: str | None = None

    def cancel(self, reason: str) -> None:
        self._cancelled = True
        self._reason = reason

    def is_cancelled(self) -> bool:
        return self._cancelled

    @property
    def reason(self) -> str | None:
        return self._reason

    async def wait_until_cancelled(self) -> None:  # pragma: no cover
        pass


@pytest.mark.asyncio
async def test_observe_raises_if_already_cancelled():
    engine = VisionEngine(
        screenshot_source=_FakeScreenshotSource(),
        uia_source=_FakeUIATreeSource(),
    )
    tok = _Cancelled()
    tok.cancel("pretest")
    with pytest.raises(RuntimeError, match="cancelled"):
        await engine.observe(mode="composite", cancel_token=tok)


# ---------------------------------------------------------------------------
# Event-Emission
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_engine_emits_observation_captured_to_bus():
    bus = EventBus()
    received: list[ObservationCaptured] = []

    async def capture(ev: ObservationCaptured) -> None:
        received.append(ev)

    bus.subscribe(ObservationCaptured, capture)

    engine = VisionEngine(
        screenshot_source=_FakeScreenshotSource(hash_="evt-hash"),
        uia_source=_FakeUIATreeSource(window_title="EventTest"),
        bus=bus,
    )
    await engine.observe(mode="composite")
    assert len(received) == 1
    assert received[0].source == "full"
    assert received[0].window_title == "EventTest"
    assert received[0].screenshot_hash == "evt-hash"


# ---------------------------------------------------------------------------
# close() delegiert
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_engine_close_closes_sources():
    shot = _FakeScreenshotSource()
    uia = _FakeUIATreeSource()
    engine = VisionEngine(screenshot_source=shot, uia_source=uia)
    await engine.close()
    assert shot.closed
    assert uia.closed


# ---------------------------------------------------------------------------
# Heuristik ohne explicit filter — nutzt GetForegroundWindow-Fallback
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Screenshot mode: window_title from the foreground hint (BUG-CU-EMPTYTITLE)
#
# Text-heavy apps (Chrome, VS Code, Slack, …) run in screenshot mode, where
# ScreenshotSource returns window_title="". The CU-loop regression
# detector read the empty title as "window gone / desktop in front" and
# instructed the model to reopen the app that was already open. The engine
# already knows the foreground title (mode heuristic) — it must carry it
# over into the observation.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auto_screenshot_mode_fills_window_title_from_filter_hint():
    engine = VisionEngine(
        screenshot_source=_FakeScreenshotSource(),
        uia_source=_FakeUIATreeSource(),
    )
    obs = await engine.observe(mode="auto", window_title_filter="Chrome - GitHub")
    assert obs.source == "screenshot_only"
    assert obs.window_title == "Chrome - GitHub"


@pytest.mark.asyncio
async def test_explicit_screenshot_mode_fills_window_title_from_probe():
    engine = VisionEngine(
        screenshot_source=_FakeScreenshotSource(),
        uia_source=_FakeUIATreeSource(),
    )
    with patch.object(
        VisionEngine, "_guess_active_app_hint",
        staticmethod(lambda _: "Google Chrome"),
    ):
        obs = await engine.observe(mode="screenshot")
    assert obs.window_title == "Google Chrome"


@pytest.mark.asyncio
async def test_auto_mode_without_filter_falls_back_to_composite():
    """When no filter and no Windows window is detectable,
    'composite' should be the default.
    """
    engine = VisionEngine(
        screenshot_source=_FakeScreenshotSource(),
        uia_source=_FakeUIATreeSource(),
    )
    # Forge GetForegroundWindow stub via patch auf _guess_active_app_hint.
    with patch.object(VisionEngine, "_guess_active_app_hint", staticmethod(lambda _: "")):
        obs = await engine.observe(mode="auto")
    assert obs.source == "full"
