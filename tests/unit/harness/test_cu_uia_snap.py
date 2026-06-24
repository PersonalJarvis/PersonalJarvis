"""Phase 2: UIA snap fallback on a missed pixel click.

When the post-click verification detects no visible change, the loop snaps to the
nearest clickable UIA element (by bounding box) and clicks its center BEFORE the
expensive LLM refine retry — fixing the "guessed a pixel, missed the button by a
few px" thrash. Falls through to the existing refine when no element is near or
no UI-tree backend is available (headless / Null source).

Unit-level: the UI-tree source and the raw click dispatch are monkeypatched, so
these are deterministic and exercise the pure picker + the snap helper directly.
"""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.harness import screenshot_only_loop as sol
from jarvis.harness.computer_use_context import ComputerUseContext


def _ctx() -> ComputerUseContext:
    return ComputerUseContext(
        vision_engine=None, brain_manager=None, tool_executor=object(), tools={}
    )


def _node(x, y, w, h, *, enabled=True, name="n"):
    return SimpleNamespace(bounds=(x, y, w, h), enabled=enabled, name=name)


# --- pure picker ------------------------------------------------------------


def test_pick_snap_node_prefers_smallest_containing():
    nodes = [_node(0, 0, 100, 100, name="big"), _node(40, 40, 20, 20, name="small")]
    picked = sol._pick_snap_node(nodes, 50, 50, 80)
    assert picked.name == "small"


def test_pick_snap_node_nearest_when_not_contained():
    nodes = [_node(100, 100, 20, 20, name="near")]  # center (110,110)
    picked = sol._pick_snap_node(nodes, 130, 130, 80)
    assert picked is not None
    assert picked.name == "near"


def test_pick_snap_node_none_when_too_far():
    nodes = [_node(0, 0, 10, 10, name="far")]  # center (5,5)
    assert sol._pick_snap_node(nodes, 500, 500, 80) is None


def test_pick_snap_node_skips_disabled():
    nodes = [_node(40, 40, 20, 20, enabled=False, name="disabled")]
    assert sol._pick_snap_node(nodes, 50, 50, 80) is None


def test_pick_snap_node_skips_zero_area():
    nodes = [_node(40, 40, 0, 0, name="zero")]
    assert sol._pick_snap_node(nodes, 40, 40, 80) is None


def test_pick_snap_node_empty():
    assert sol._pick_snap_node([], 10, 10, 80) is None


# --- snap helper ------------------------------------------------------------


class _FakeSource:
    def __init__(self, nodes):
        self._nodes = nodes

    async def observe(self, **_kw):
        return SimpleNamespace(nodes=self._nodes)


async def test_uia_snap_clicks_element_center(monkeypatch):
    monkeypatch.setattr(
        "jarvis.vision.tree_factory.make_ui_tree_source",
        lambda: _FakeSource([_node(40, 40, 20, 20, name="OK button")]),
    )
    clicks: list = []

    async def fake_dispatch(executor, tool, cx, cy, tid):
        clicks.append((cx, cy))
        return True, "clicked"

    monkeypatch.setattr(sol, "_dispatch_raw_click", fake_dispatch)
    res = await sol._uia_snap_click(
        _ctx(), executor=object(), tool=object(), x=50, y=50, trace_id=None
    )
    assert res is not None
    ok, msg = res
    assert ok is True
    assert clicks == [(50, 50)]   # center of (40,40,20,20)
    assert "OK button" in msg


async def test_uia_snap_none_when_no_node(monkeypatch):
    monkeypatch.setattr(
        "jarvis.vision.tree_factory.make_ui_tree_source", lambda: _FakeSource([])
    )
    res = await sol._uia_snap_click(
        _ctx(), executor=object(), tool=object(), x=5, y=5, trace_id=None
    )
    assert res is None


async def test_uia_snap_respects_disable_flag(monkeypatch):
    ctx = _ctx()
    ctx.uia_click_fallback = False
    called: list = []
    monkeypatch.setattr(
        "jarvis.vision.tree_factory.make_ui_tree_source",
        lambda: called.append(1) or _FakeSource([]),
    )
    res = await sol._uia_snap_click(
        ctx, executor=object(), tool=object(), x=5, y=5, trace_id=None
    )
    assert res is None
    assert called == []   # source never even built


async def test_uia_snap_none_when_observe_raises(monkeypatch):
    class _Boom:
        async def observe(self, **_kw):
            raise RuntimeError("COM dead")

    monkeypatch.setattr("jarvis.vision.tree_factory.make_ui_tree_source", lambda: _Boom())
    res = await sol._uia_snap_click(
        _ctx(), executor=object(), tool=object(), x=5, y=5, trace_id=None
    )
    assert res is None


# --- integration: snap fires inside _click_with_refine on a verified miss ----


class _RecordingExecutor:
    def __init__(self):
        self.clicks: list = []

    async def execute(self, tool, args, *, user_utterance="", trace_id=None):
        self.clicks.append((args["x"], args["y"]))
        return SimpleNamespace(success=True, output="clicked", error="")


class _FakeClickTool:
    name = "click"
    risk_tier = "monitor"
    schema = {"type": "object", "properties": {"x": {}, "y": {}}}


class _FakeObs:
    screenshot_path = "X:/fake/shot.jpg"
    screenshot_hash = "h"
    trace_id = None


async def test_snap_fires_inside_click_with_refine_on_miss(monkeypatch):
    # pre == post (same bytes) -> verified miss -> the snap path runs before the
    # LLM refine and clicks the nearest UIA element's center.
    monkeypatch.setattr(sol, "_grab_region_jpeg", lambda bbox: b"same")
    monkeypatch.setattr(sol, "_CLICK_VERIFY_SETTLE_S", 0.0)
    monkeypatch.setattr(
        "jarvis.vision.tree_factory.make_ui_tree_source",
        lambda: _FakeSource([_node(460, 460, 40, 40, name="Submit")]),  # center (480,480)
    )
    ctx = ComputerUseContext(
        vision_engine=None,
        brain_manager=None,
        tool_executor=_RecordingExecutor(),
        tools={"click": _FakeClickTool()},
        verify_after_each_step=True,
    )
    ok, msg = await sol._execute_action(
        {"action": "click", "x": 500, "y": 500, "target": "submit"},
        ctx,
        trace_id=None,
        user_goal="x",
        monitor_geom=(0, 0, 1000, 1000),
        observation=_FakeObs(),
    )
    assert ctx.tool_executor.clicks == [(500, 500), (480, 480)]
    assert "UIA-snapped" in msg
    assert "Submit" in msg
