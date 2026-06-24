"""Click-accuracy fix for the screenshot-only Computer-Use loop (2026-06-10).

Root cause of the "agent consistently misses its click targets" bug: for
label-less surfaces (Spotify transport bar, custom-painted UIs) the loop
executes the vision model's SINGLE coarse 0-1000 estimate directly. Vision
LLMs cannot reliably ground small controls on a full-screen frame (live
evidence 2026-05-27 in screenshot_only_loop.py), so the click lands near —
but not on — the target.

The fix is a verify-then-refine pass on the pixel-click path (trust-first
since 2026-06-10, latency plan Task 3 — the up-front refine corrected live
clicks by <=5 px while costing a full LLM round-trip per click):

1. TRUST — attempt 1 clicks the model's own coarse estimate directly; the
   executor and the refiner see the same frame, so a pre-click refine call
   is redundant cost on the happy path.
2. VERIFY — compare a small region around the clicked point before/after
   the click; if nothing changed, NOW refine with a zoomed live crop and
   retry at the corrected position (max attempts capped, never re-clicking
   within tolerance of an already-clicked point — toggle safety).

All collaborators are module-level and monkeypatchable; no real screen,
brain, or mouse is touched here.
"""
from __future__ import annotations

from typing import Any

import pytest

import jarvis.harness.screenshot_only_loop as loop_mod
from jarvis.harness.screenshot_only_loop import (
    _SYSTEM_PROMPT,
    _crop_norm_to_abs,
    _execute_action,
    _parse_action,
    _parse_refine_verdict,
    _refine_crop_bbox,
)

# ---------------------------------------------------------------------------
# Schema: optional "target" description on pixel clicks
# ---------------------------------------------------------------------------


def test_parse_click_accepts_optional_target() -> None:
    obj = _parse_action(
        '{"action": "click", "x": 500, "y": 970, "target": "skip button"}'
    )
    assert obj["target"] == "skip button"
    assert (obj["x"], obj["y"]) == (500, 970)


def test_parse_click_without_target_stays_without_target() -> None:
    obj = _parse_action('{"action": "click", "x": 10, "y": 20}')
    assert "target" not in obj


def test_parse_click_drops_non_string_target() -> None:
    # The refinement is best-effort — a malformed target must not fail the
    # whole action, just be dropped.
    obj = _parse_action('{"action": "click", "x": 10, "y": 20, "target": 42}')
    assert "target" not in obj


def test_system_prompt_asks_for_click_target() -> None:
    # The model must be told to describe what it is aiming at, so the
    # refinement stage knows what to look for in the zoomed crop.
    assert '"target"' in _SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Pure helpers: refine-verdict parsing + crop-norm -> absolute mapping
# ---------------------------------------------------------------------------


def test_parse_refine_verdict_found() -> None:
    assert _parse_refine_verdict('{"found": true, "x": 250, "y": 750}') == (
        True, 250, 750,
    )


def test_parse_refine_verdict_found_clamps_overshoot() -> None:
    assert _parse_refine_verdict('{"found": true, "x": 1400, "y": -3}') == (
        True, 1000, 0,
    )


def test_parse_refine_verdict_not_found() -> None:
    assert _parse_refine_verdict('{"found": false}') == (False, 0, 0)


def test_parse_refine_verdict_garbage_returns_none() -> None:
    assert _parse_refine_verdict("sorry, I cannot help") is None
    assert _parse_refine_verdict('{"found": true}') is None  # missing coords
    assert _parse_refine_verdict("") is None


def test_parse_refine_verdict_tolerates_fences() -> None:
    raw = '```json\n{"found": true, "x": 500, "y": 500}\n```'
    assert _parse_refine_verdict(raw) == (True, 500, 500)


def test_crop_norm_to_abs_maps_within_bbox() -> None:
    bbox = {"left": 100, "top": 200, "width": 400, "height": 300}
    assert _crop_norm_to_abs(bbox, 500, 500) == (300, 350)
    # Edges stay inside the crop (never click outside the refined region).
    ax, ay = _crop_norm_to_abs(bbox, 1000, 1000)
    assert ax <= 100 + 400 - 1
    assert ay <= 200 + 300 - 1


def test_refine_crop_bbox_scales_with_monitor_and_clamps() -> None:
    bbox = _refine_crop_bbox(500, 500, (0, 0, 1000, 1000))
    # radius = max(180, round(0.14 * 1000)) = 180 -> 360px square at 320/320.
    assert bbox == {"left": 320, "top": 320, "width": 360, "height": 360}
    # Near the screen edge the crop is clamped to the monitor bounds.
    edge = _refine_crop_bbox(5, 5, (0, 0, 1000, 1000))
    assert edge["left"] == 0
    assert edge["top"] == 0


# ---------------------------------------------------------------------------
# Fakes for the executor-level tests
# ---------------------------------------------------------------------------


class FakeBrain:
    def __init__(self, script: list[str | Exception] | None = None) -> None:
        self.script = list(script or [])
        self.requests: list[tuple[str, str]] = []

    async def complete_text(self, *, system: str, user: str) -> str:
        self.requests.append((system, user))
        item = self.script.pop(0) if self.script else '{"found": false}'
        if isinstance(item, Exception):
            raise item
        return item


class FakeToolResult:
    def __init__(self) -> None:
        self.success = True
        self.output = "clicked"
        self.error = ""


class FakeTool:
    name = "click"


class FakeExecutor:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []

    async def execute(self, tool: Any, args: dict[str, Any], *,
                      user_utterance: str = "", trace_id: Any = None) -> FakeToolResult:
        self.clicks.append((args["x"], args["y"]))
        return FakeToolResult()


class FakeCtx:
    def __init__(self, brain: FakeBrain, executor: FakeExecutor, *,
                 verify: bool = True) -> None:
        self.brain_manager = brain
        self.tool_executor = executor
        self.tools = {"click": FakeTool()}
        self.bus = None
        self.per_step_timeout_s = 5.0
        self.verify_after_each_step = verify
        # These tests isolate the LLM refine path; the Phase-2 UIA snap (which
        # would otherwise fire first on a verified miss and query the real host
        # accessibility tree) is turned off so the refine behaviour is tested in
        # isolation. The snap itself is covered by tests/unit/harness/test_cu_uia_snap.py.
        self.uia_click_fallback = False


class FakeObservation:
    """Structural stand-in: only screenshot_path is read on this path."""

    screenshot_path = "X:/fake/blobs/shot.jpg"
    screenshot_hash = "fakehash"
    trace_id = None


class GrabQueue:
    """Scripted _grab_region_jpeg replacement; returns None when exhausted."""

    def __init__(self, items: list[bytes]) -> None:
        self.items = list(items)
        self.calls = 0

    def __call__(self, bbox: dict[str, int]) -> bytes | None:
        self.calls += 1
        return self.items.pop(0) if self.items else None


_GEOM = (0, 0, 1000, 1000)
_OBSERVATION = FakeObservation()


def _patch(monkeypatch: pytest.MonkeyPatch, grab: GrabQueue) -> None:
    monkeypatch.setattr(loop_mod, "_grab_region_jpeg", grab)
    monkeypatch.setattr(loop_mod, "_CLICK_VERIFY_SETTLE_S", 0.0)


async def _click(ctx: FakeCtx, obj: dict[str, Any],
                 observation: Any = _OBSERVATION) -> tuple[bool, str]:
    return await _execute_action(
        obj, ctx, trace_id=None, user_goal="open spotify and skip the song",
        monitor_geom=_GEOM, observation=observation,
    )


# ---------------------------------------------------------------------------
# Trust-first: attempt 1 never pays the refine LLM call
# ---------------------------------------------------------------------------


async def test_first_click_attempt_skips_the_refine_llm_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The up-front refine pass cost one LLM round-trip per click while
    # correcting by <=5 px in live runs (2026-06-10 20:46). The first attempt
    # trusts the model's coordinate; a visibly reacting click ends the pass
    # with ZERO refine calls.
    brain = FakeBrain()
    executor = FakeExecutor()
    _patch(monkeypatch, GrabQueue([b"pre", b"post-different"]))

    ok, _msg = await _click(
        FakeCtx(brain, executor),
        {"action": "click", "x": 500, "y": 500, "target": "skip button"},
    )

    assert ok is True
    assert executor.clicks == [(500, 500)]
    assert brain.requests == []  # no refine call on the happy path


async def test_refine_corrects_click_coordinates_on_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Click 1 at the coarse estimate (500,500) produces NO local change ->
    # the refiner re-locates the target at crop-norm (250,250) inside the
    # 360px crop at (320,320) -> abs (410,410). The RETRY must land on the
    # refined point.
    brain = FakeBrain(['{"found": true, "x": 250, "y": 250}'])
    executor = FakeExecutor()
    same = b"unchanged-region"
    grab = GrabQueue([same, same, b"refine-crop", b"pre2", b"post-different"])
    _patch(monkeypatch, grab)

    ok, _msg = await _click(
        FakeCtx(brain, executor),
        {"action": "click", "x": 500, "y": 500, "target": "skip button"},
    )

    assert ok is True
    assert executor.clicks == [(500, 500), (410, 410)]
    assert len(brain.requests) == 1  # exactly one refine call, on the retry


async def test_retry_refine_not_found_accepts_first_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # After a no-change click the refiner cannot see the target in the fresh
    # crop — likely the click DID work and the UI moved on. Accept click #1
    # and let the next screenshot judge the semantics.
    brain = FakeBrain(['{"found": false}'])
    executor = FakeExecutor()
    same = b"unchanged-region"
    _patch(monkeypatch, GrabQueue([same, same, b"refine-crop"]))

    ok, msg = await _click(
        FakeCtx(brain, executor),
        {"action": "click", "x": 500, "y": 500, "target": "skip button"},
    )

    assert ok is True
    assert executor.clicks == [(500, 500)]
    assert "no longer" in msg


async def test_refine_failure_on_retry_never_reclicks_same_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A refine failure (provider error, malformed JSON) on the retry keeps
    # the coarse estimate — which was already clicked, so the tolerance
    # guard stops a duplicate click (toggle safety). Never worse than before.
    brain = FakeBrain([RuntimeError("provider down")])
    executor = FakeExecutor()
    same = b"unchanged-region"
    _patch(monkeypatch, GrabQueue([same, same, b"refine-crop"]))

    ok, _msg = await _click(
        FakeCtx(brain, executor),
        {"action": "click", "x": 500, "y": 500, "target": "skip button"},
    )

    assert ok is True
    assert executor.clicks == [(500, 500)]


# ---------------------------------------------------------------------------
# Post-click verification + corrected retry
# ---------------------------------------------------------------------------


async def test_verify_retries_with_corrected_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Click 1 at the coarse point produces NO local change -> refine round
    # finds the target elsewhere -> click 2 at the corrected point. A second
    # refine round returns the same point again -> the tolerance guard stops
    # further clicking (toggle safety).
    brain = FakeBrain([
        '{"found": true, "x": 800, "y": 800}',   # retry 1 -> (608,608)
        # Retry 2 crops around the last clicked point (608,608); its center
        # (crop-norm 500,500) IS that point again -> tolerance guard -> stop.
        '{"found": true, "x": 500, "y": 500}',
    ])
    executor = FakeExecutor()
    same = b"unchanged-region"
    grab = GrabQueue([
        same, same,             # pre 1, post 1 (identical -> miss)
        b"crop2", same, same,   # refine 2, pre 2, post 2 (identical -> miss)
        b"crop3",               # refine 3 -> same point -> no 3rd click
    ])
    _patch(monkeypatch, grab)

    ok, msg = await _click(
        FakeCtx(brain, executor),
        {"action": "click", "x": 500, "y": 500, "target": "skip button"},
    )

    assert executor.clicks == [(500, 500), (608, 608)]
    assert ok is True  # clicks were executed; semantic layer judges the rest
    assert "unchanged" in msg or "no visible" in msg


async def test_verify_accepts_changed_region_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The region around the click visibly changed -> the click reacted ->
    # exactly one click, no retry, no refine call.
    brain = FakeBrain()
    executor = FakeExecutor()
    _patch(monkeypatch, GrabQueue([b"before", b"after-different"]))

    ok, _msg = await _click(
        FakeCtx(brain, executor),
        {"action": "click", "x": 500, "y": 500, "target": "skip button"},
    )

    assert ok is True
    assert executor.clicks == [(500, 500)]
    assert brain.requests == []


async def test_verify_disabled_clicks_once_without_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ctx.verify_after_each_step=False turns off the post-click verify loop.
    # Without a miss signal there is nothing to refine against (trust-first):
    # exactly one click at the model's own point, zero grabs, zero LLM calls.
    brain = FakeBrain()
    executor = FakeExecutor()
    grab = GrabQueue([b"never-used"])
    _patch(monkeypatch, grab)

    ok, _msg = await _click(
        FakeCtx(brain, executor, verify=False),
        {"action": "click", "x": 500, "y": 500, "target": "skip button"},
    )

    assert ok is True
    assert executor.clicks == [(500, 500)]
    assert grab.calls == 0
    assert brain.requests == []


# ---------------------------------------------------------------------------
# Hermetic gates: tests/headless contexts keep the legacy single-click path
# ---------------------------------------------------------------------------


async def test_no_observation_keeps_legacy_click_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Without an observation (direct _execute_action callers, fakes with no
    # on-disk frame) the refine/verify machinery must stay off entirely.
    brain = FakeBrain()
    executor = FakeExecutor()
    grab = GrabQueue([b"never-used"])
    _patch(monkeypatch, grab)

    ok, _msg = await _click(
        FakeCtx(brain, executor),
        {"action": "click", "x": 500, "y": 500, "target": "skip button"},
        observation=None,
    )

    assert ok is True
    assert executor.clicks == [(500, 500)]
    assert brain.requests == []
    assert grab.calls == 0


async def test_unknown_monitor_geometry_keeps_legacy_click_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Headless / non-Windows: no geometry -> no meaningful crop -> legacy path.
    brain = FakeBrain()
    executor = FakeExecutor()
    grab = GrabQueue([b"never-used"])
    _patch(monkeypatch, grab)

    ok, _msg = await _execute_action(
        {"action": "click", "x": 500, "y": 500, "target": "skip button"},
        FakeCtx(brain, executor),
        trace_id=None, user_goal="g", monitor_geom=(0, 0, 0, 0),
        observation=FakeObservation(),
    )

    assert ok is True
    assert executor.clicks == [(500, 500)]
    assert brain.requests == []
    assert grab.calls == 0
