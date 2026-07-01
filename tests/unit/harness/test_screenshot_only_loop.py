"""Unit tests for screenshot_only_loop — open_app action + dispatch.

Why open_app matters here:
    The screenshot-only loop's original vocabulary (click / type / done /
    fail) has no way to launch an app cleanly; "open Calc" used to fall
    through to ``type "calc"`` into whatever window had focus (often the
    Jarvis chat input — observed 2026-05-27). Restoring the open_app
    action makes the action vocabulary symmetric with the user's natural
    request pattern.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jarvis.harness.screenshot_only_loop import (
    _VALID_ACTIONS,
    CULoopError,
    _capture_monitor_geometry,
    _execute_action,
    _parse_action,
    _parse_actions,
    _resolve_click_pixel,
)

# ---------------------------------------------------------------------------
# _resolve_click_pixel — 0-1000 normalized -> absolute screen pixel
# (BUG-CU-NORMCOORD + BUG-CU-MULTIMON, live failure 2026-05-28: Gemini
# returns 0-1000 normalized coords; the loop used to treat them as raw
# pixels, so y=983 clicked 45% down (mid-screen, paused a YouTube video)
# instead of 98% down (Spotify player bar). These pin the conversion.)
# ---------------------------------------------------------------------------


def test_resolve_click_pixel_left_4k_monitor() -> None:
    # The exact live failure case: model said (550, 983) on a 4K monitor
    # whose virtual-desktop origin is (-3840, 0). Normalized -> pixel ->
    # +origin must land on the bottom player bar of the LEFT monitor.
    geom = (-3840, 0, 3840, 2160)  # left, top, width, height
    abs_x, abs_y = _resolve_click_pixel({"x": 550, "y": 983}, geom)
    assert abs_x == 550 / 1000 * 3840 + (-3840)  # == -1728
    assert abs_y == round(983 / 1000 * 2160)      # == 2123
    assert abs_x == -1728
    assert abs_y == 2123


def test_resolve_click_pixel_center_primary() -> None:
    # Dead center on a primary 1920x1080 monitor (origin 0,0).
    abs_x, abs_y = _resolve_click_pixel({"x": 500, "y": 500}, (0, 0, 1920, 1080))
    assert (abs_x, abs_y) == (960, 540)


def test_resolve_click_pixel_unknown_geometry_passthrough() -> None:
    # Headless / non-Windows: geometry unknown (0,0,0,0) -> never divide by
    # zero, pass the model coords through unscaled (no GUI to click anyway).
    abs_x, abs_y = _resolve_click_pixel({"x": 500, "y": 970}, (0, 0, 0, 0))
    assert (abs_x, abs_y) == (500, 970)


def test_resolve_click_pixel_clamps_overshoot() -> None:
    # A model that overshoots the 0-1000 grid must be clamped, never sent
    # off-screen.
    abs_x, abs_y = _resolve_click_pixel({"x": 1200, "y": -5}, (0, 0, 1000, 1000))
    assert abs_x == 1000   # clamped to 1000 -> 1000/1000*1000
    assert abs_y == 0      # clamped to 0


# ---------------------------------------------------------------------------
# _capture_monitor_geometry — cross-platform monitor geometry (B1,
# DEEP-DIVE-AUDIT-2026-06-19). On Windows it uses win32 (untouched, AD-7).
# On macOS/Linux the win32 import fails; it must fall back to the SAME mss
# monitor geometry the screenshot capture uses, so _resolve_click_pixel can
# scale the model's 0-1000 coords to real pixels. The pre-fix code returned
# (0,0,0,0) on every non-Windows host, so every pixel-click landed in the
# top-left 1000x1000 px square. These force the non-Windows path on this
# Windows host by making the win32 import raise.
# ---------------------------------------------------------------------------


class _FakeSct:
    """Minimal mss context-manager stand-in: one virtual bbox + one physical
    1920x1080 monitor at the origin."""

    monitors = [
        {"left": 0, "top": 0, "width": 1920, "height": 1080},
        {"left": 0, "top": 0, "width": 1920, "height": 1080, "is_primary": True},
    ]

    def __enter__(self) -> _FakeSct:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def test_capture_monitor_geometry_mss_fallback_off_windows(monkeypatch) -> None:
    import sys
    import types

    # Force the non-Windows path: make the win32 import inside
    # _capture_monitor_geometry raise (None in sys.modules -> ImportError).
    for mod in ("win32api", "win32con", "win32gui"):
        monkeypatch.setitem(sys.modules, mod, None)

    # Fake mss so the fallback reads a known monitor without a real display.
    monkeypatch.setitem(sys.modules, "mss", types.SimpleNamespace(mss=lambda: _FakeSct()))
    # Pin the selector to the physical monitor (avoid the Windows ctypes path
    # the real selector would take while this test runs on a Windows host).
    monkeypatch.setattr(
        "jarvis.vision.screenshot.select_capture_monitor",
        lambda monitors, **kw: monitors[1],
    )

    # Pre-fix: (0,0,0,0). Post-fix: the real mss geometry.
    assert _capture_monitor_geometry() == (0, 0, 1920, 1080)


def test_capture_monitor_geometry_headless_returns_zero(monkeypatch) -> None:
    # Genuinely headless (win32 absent AND mss/display absent): the geometry
    # is unknown and _resolve_click_pixel keeps its safe pass-through path.
    import sys

    for mod in ("win32api", "win32con", "win32gui", "mss"):
        monkeypatch.setitem(sys.modules, mod, None)

    assert _capture_monitor_geometry() == (0, 0, 0, 0)


# ---------------------------------------------------------------------------
# _parse_action — open_app schema validation
# ---------------------------------------------------------------------------


def test_parse_open_app_accepts_string_name() -> None:
    obj = _parse_action('{"action": "open_app", "name": "calc"}')
    assert obj == {"action": "open_app", "name": "calc"}


def test_parse_open_app_rejects_missing_name() -> None:
    with pytest.raises(CULoopError, match="name"):
        _parse_action('{"action": "open_app"}')


def test_parse_open_app_rejects_non_string_name() -> None:
    with pytest.raises(CULoopError, match="name"):
        _parse_action('{"action": "open_app", "name": 42}')


def test_parse_open_app_rejects_empty_name() -> None:
    with pytest.raises(CULoopError, match="name"):
        _parse_action('{"action": "open_app", "name": ""}')


# ---------------------------------------------------------------------------
# _execute_action — dispatch to open_app tool with proper arg name
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, *, success: bool = True, output: str = "", error: str = "") -> None:
        self.success = success
        self.output = output
        self.error = error


class _FakeOpenAppTool:
    name: str = "open_app"


class _FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any], str]] = []

    async def execute(self, tool, args, *, user_utterance: str = "", trace_id: Any = None):
        self.calls.append((tool, args, user_utterance))
        return _FakeResult(success=True, output=f"opened {args.get('app_name')}")


class _FakeCtx:
    def __init__(self, executor: _FakeExecutor, tools: dict) -> None:
        self.tool_executor = executor
        self.tools = tools
        self.bus = None
        self.per_step_timeout_s = 5.0


def test_execute_open_app_dispatches_to_open_app_tool_with_app_name_key() -> None:
    # The JSON the model emits uses the key "name" for ergonomics, but the
    # OpenAppTool's schema requires "app_name". The dispatcher must adapt.
    tool = _FakeOpenAppTool()
    executor = _FakeExecutor()
    ctx = _FakeCtx(executor=executor, tools={"open_app": tool})

    success, message = asyncio.run(
        _execute_action(
            {"action": "open_app", "name": "calc"},
            ctx,
            trace_id=None,
            user_goal="open calc",
        ),
    )

    assert success is True
    assert len(executor.calls) == 1
    sent_tool, sent_args, _utterance = executor.calls[0]
    assert sent_tool is tool
    assert sent_args == {"app_name": "calc"}


# ---------------------------------------------------------------------------
# _parse_actions — batch / plan-then-execute support
# ---------------------------------------------------------------------------


def test_parse_actions_wraps_single_object_in_list() -> None:
    # Backward compatibility: when the model returns ONE action object, the
    # plural parser treats it as a one-element batch so the executor can
    # iterate uniformly.
    actions = _parse_actions('{"action": "click", "x": 10, "y": 20}')
    # button/double are normalized onto every click (audit #21): default
    # left single-click — additive, the coordinates are unchanged.
    assert actions == [
        {"action": "click", "x": 10, "y": 20, "button": "left", "double": False}
    ]


def test_parse_actions_accepts_list_of_actions() -> None:
    actions = _parse_actions(
        '[{"action": "open_app", "name": "calc"},'
        ' {"action": "wait", "ms": 500},'
        ' {"action": "click", "x": 100, "y": 200}]'
    )
    assert len(actions) == 3
    assert actions[0] == {"action": "open_app", "name": "calc"}
    assert actions[1] == {"action": "wait", "ms": 500}
    assert actions[2] == {
        "action": "click", "x": 100, "y": 200, "button": "left", "double": False
    }


def test_parse_actions_rejects_empty_list() -> None:
    with pytest.raises(CULoopError, match="empty"):
        _parse_actions("[]")


def test_parse_actions_rejects_list_with_invalid_item() -> None:
    with pytest.raises(CULoopError):
        _parse_actions(
            '[{"action": "click", "x": 10, "y": 20},'
            ' {"action": "garbage"}]'
        )


def test_parse_actions_strips_json_fences() -> None:
    # The model occasionally ignores the no-fence rule; the parser should
    # still recover. Same rule as single _parse_action.
    actions = _parse_actions('```json\n[{"action": "done"}]\n```')
    assert actions == [{"action": "done"}]


# ---------------------------------------------------------------------------
# wait action — schema + dispatch
# ---------------------------------------------------------------------------


def test_parse_wait_accepts_positive_int_ms() -> None:
    obj = _parse_action('{"action": "wait", "ms": 800}')
    assert obj == {"action": "wait", "ms": 800}


def test_parse_wait_rejects_missing_ms() -> None:
    with pytest.raises(CULoopError, match="ms"):
        _parse_action('{"action": "wait"}')


def test_parse_wait_rejects_negative_ms() -> None:
    with pytest.raises(CULoopError, match="ms"):
        _parse_action('{"action": "wait", "ms": -10}')


def test_parse_wait_caps_ms_at_10000() -> None:
    # Defense: model could ask for a 1-hour wait. Cap at 10s.
    obj = _parse_action('{"action": "wait", "ms": 99999}')
    assert obj["ms"] == 10_000


def test_execute_wait_sleeps_the_requested_duration() -> None:
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    # Inject the fake sleep via monkeypatching the module's asyncio.sleep.
    import jarvis.harness.screenshot_only_loop as mod
    real = mod.asyncio.sleep
    mod.asyncio.sleep = fake_sleep
    try:
        executor = _FakeExecutor()
        ctx = _FakeCtx(executor=executor, tools={})

        success, message = asyncio.run(
            _execute_action(
                {"action": "wait", "ms": 750},
                ctx,
                trace_id=None,
                user_goal="x",
            ),
        )
        assert success is True
        assert sleeps == [0.75]
        assert executor.calls == []  # wait is in-loop, no tool dispatch
    finally:
        mod.asyncio.sleep = real


# ---------------------------------------------------------------------------
# click_element action — UIA-grounded clicks (no pixel guessing)
# ---------------------------------------------------------------------------


def test_parse_click_element_accepts_string_name() -> None:
    obj = _parse_action('{"action": "click_element", "name": "7"}')
    assert obj == {"action": "click_element", "name": "7"}


def test_parse_click_element_strips_whitespace_around_name() -> None:
    obj = _parse_action('{"action": "click_element", "name": "  Submit  "}')
    assert obj["name"] == "Submit"


def test_parse_click_element_rejects_missing_name() -> None:
    with pytest.raises(CULoopError, match="name"):
        _parse_action('{"action": "click_element"}')


def test_parse_click_element_rejects_empty_name() -> None:
    with pytest.raises(CULoopError, match="name"):
        _parse_action('{"action": "click_element", "name": ""}')


def test_parse_click_element_rejects_non_string_name() -> None:
    with pytest.raises(CULoopError, match="name"):
        _parse_action('{"action": "click_element", "name": 7}')


def test_execute_click_element_dispatches_to_click_element_tool() -> None:
    # ClickElementTool's schema takes args["name"] directly — no re-keying
    # required (contrast open_app, which the model emits as "name" but the
    # tool expects "app_name").
    class _FakeClickElementTool:
        name: str = "click_element"

    tool = _FakeClickElementTool()
    executor = _FakeExecutor()
    ctx = _FakeCtx(executor=executor, tools={"click_element": tool})

    success, message = asyncio.run(
        _execute_action(
            {"action": "click_element", "name": "7"},
            ctx,
            trace_id=None,
            user_goal="click 7",
        ),
    )

    assert success is True
    assert len(executor.calls) == 1
    sent_tool, sent_args, _utterance = executor.calls[0]
    assert sent_tool is tool
    assert sent_args == {"name": "7"}


def test_execute_click_element_reports_missing_tool_cleanly() -> None:
    executor = _FakeExecutor()
    ctx = _FakeCtx(executor=executor, tools={})  # click_element not wired

    success, message = asyncio.run(
        _execute_action(
            {"action": "click_element", "name": "7"},
            ctx,
            trace_id=None,
            user_goal="click 7",
        ),
    )

    assert success is False
    assert "click_element" in message
    assert executor.calls == []


# ---------------------------------------------------------------------------
# _execute_action — open_app dispatch (existing)
# ---------------------------------------------------------------------------


def test_execute_open_app_reports_missing_tool_cleanly() -> None:
    executor = _FakeExecutor()
    ctx = _FakeCtx(executor=executor, tools={})  # open_app not wired

    success, message = asyncio.run(
        _execute_action(
            {"action": "open_app", "name": "calc"},
            ctx,
            trace_id=None,
            user_goal="open calc",
        ),
    )

    assert success is False
    assert "open_app" in message
    assert executor.calls == []


# ---------------------------------------------------------------------------
# Hangup -> CU cancel registry (BUG-CU-HANGUP, 2026-05-28): "auflegen" must
# cancel ONLY the active CU mission, never raise, and be a no-op when idle.
# ---------------------------------------------------------------------------


def test_cancel_active_cu_registry() -> None:
    from jarvis.harness.computer_use_context import (
        cancel_active_cu,
        register_active_cu_token,
    )

    class _Tok:
        def __init__(self) -> None:
            self.reason = None

        def cancel(self, reason: str) -> None:
            self.reason = reason

    # No token registered -> no-op, returns False.
    register_active_cu_token(None)
    assert cancel_active_cu("voice_hangup") is False

    # Registered token gets cancelled with the reason.
    tok = _Tok()
    register_active_cu_token(tok)
    assert cancel_active_cu("voice_hangup") is True
    assert tok.reason == "voice_hangup"

    # A token whose cancel() raises must NOT propagate (hangup never crashes).
    class _BadTok:
        def cancel(self, reason: str) -> None:
            raise RuntimeError("boom")

    register_active_cu_token(None)
    register_active_cu_token(_BadTok())
    assert cancel_active_cu("voice_hangup") is False
    register_active_cu_token(None)


def test_cancel_active_cu_cancels_all_concurrent_missions() -> None:
    """A hangup must stop EVERY running Computer-Use mission, not just the
    most-recently-registered one.

    Live regression (2026-06-24, feat/fast-boot-bootstrap): two CU missions
    ran concurrently as overlapping background tasks. The single-slot active
    token only remembered the last registration, so ``cancel_active_cu``
    cancelled one mission while the other kept clicking the screen for ~22 s
    after the user hung up (data/jarvis_desktop.log 20:45:54 -> 20:46:16).
    """
    from jarvis.harness.computer_use_context import (
        cancel_active_cu,
        register_active_cu_token,
        unregister_active_cu_token,
    )

    class _Tok:
        def __init__(self) -> None:
            self.reason: str | None = None

        def cancel(self, reason: str) -> None:
            self.reason = reason

        def is_cancelled(self) -> bool:
            return self.reason is not None

    register_active_cu_token(None)
    mission_a = _Tok()
    mission_b = _Tok()
    register_active_cu_token(mission_a)
    register_active_cu_token(mission_b)

    # ONE hangup must cancel BOTH concurrent missions.
    assert cancel_active_cu("voice_hangup") is True
    assert mission_a.reason == "voice_hangup"
    assert mission_b.reason == "voice_hangup"

    # A finished mission removes only ITS OWN token; a sibling stays cancelable.
    register_active_cu_token(None)
    mission_c = _Tok()
    mission_d = _Tok()
    register_active_cu_token(mission_c)
    register_active_cu_token(mission_d)
    unregister_active_cu_token(mission_c)  # C's harness finally ran
    assert cancel_active_cu("voice_hangup") is True
    assert mission_c.reason is None        # C already gone — untouched
    assert mission_d.reason == "voice_hangup"  # D still cancelled
    register_active_cu_token(None)


# ---------------------------------------------------------------------------
# Plan-first planner (BUG-CU-NO-PLAN, 2026-05-28): a multi-step goal must be
# decomposed into an ordered plan instead of mashing one button.
# ---------------------------------------------------------------------------


def test_render_plan_marks_current_step() -> None:
    from jarvis.harness.screenshot_only_loop import _render_plan

    plan = [
        {"intent": "open Spotify", "success": "window visible"},
        {"intent": "click search", "success": "search box focused"},
        {"intent": "type song", "success": "query visible"},
    ]
    out = _render_plan(plan, 1)
    assert "1. [x] open Spotify" in out      # completed
    assert "2. >>> click search" in out      # current
    assert "3. [ ] type song" in out         # pending


class _PlanBrain:
    """Fake brain whose complete_text returns a fixed planner JSON."""

    def __init__(self, payload: str) -> None:
        self.payload = payload

    async def complete_text(self, *, system: str, user: str) -> str:
        return self.payload


class _PlanCtx:
    def __init__(self, brain) -> None:
        self.brain_manager = brain
        self.per_step_timeout_s = 5.0


def test_make_plan_parses_ordered_steps() -> None:
    payload = (
        '{"plan": ['
        '{"intent": "open Spotify", "success": "Spotify window visible"},'
        '{"intent": "click the search box", "success": "search box focused"},'
        '{"intent": "type the song name", "success": "query text visible"},'
        '{"intent": "press play", "success": "elapsed timer advancing"}]}'
    )
    from jarvis.harness.screenshot_only_loop import _make_plan

    obs = _DummyObs()
    plan = asyncio.run(
        _make_plan(_PlanCtx(_PlanBrain(payload)), observation=obs, user_goal="spiel ein lied ab"),
    )
    assert len(plan) == 4
    assert plan[0]["intent"] == "open Spotify"
    assert plan[3]["success"] == "elapsed timer advancing"


def test_make_plan_returns_empty_on_garbage() -> None:
    from jarvis.harness.screenshot_only_loop import _make_plan

    obs = _DummyObs()
    plan = asyncio.run(
        _make_plan(_PlanCtx(_PlanBrain("not json at all")), observation=obs, user_goal="x"),
    )
    assert plan == []


class _DummyObs:
    """Minimal Observation stand-in for planner tests (no screenshot)."""
    screenshot_path = None
    screenshot_hash = ""
    trace_id = None
    window_title = ""


# ---------------------------------------------------------------------------
# _call_brain early-stop wiring: the per-step action call must stop consuming
# the provider stream the moment a complete JSON action is parseable, while the
# default (verifier/plan) path still drains the whole stream.
# ---------------------------------------------------------------------------


class _StreamBrain:
    """Production-path fake: yields a BrainDelta stream and records how many
    deltas were actually consumed (so an early stop is observable)."""

    supports_vision = True

    def __init__(self, deltas, consumed) -> None:
        self._deltas = deltas
        self._consumed = consumed

    async def complete(self, req):  # noqa: ANN001
        for i, d in enumerate(self._deltas):
            self._consumed.append(i)
            yield d


class _StreamManager:
    """Minimal BrainManager stand-in driving the production (_get_brain) path —
    deliberately has NO complete_text, so _call_brain uses aggregate()."""

    def __init__(self, brain) -> None:
        self._brain = brain
        self._dead_providers: set = set()
        self._rate_tracker = None

    def _build_fallback_chain(self, tier):  # noqa: ANN001
        return [("fake", "m")]

    def _get_brain(self, provider, model):  # noqa: ANN001
        return self._brain

    def _fast_model(self, provider):  # noqa: ANN001
        return "m"


class _StreamCtx:
    def __init__(self, brain) -> None:
        self.brain_manager = brain
        self.per_step_timeout_s = 5.0


def _action_stream_deltas():
    from jarvis.core.protocols import BrainDelta

    return [
        BrainDelta(content='{"action": '),
        BrainDelta(content='"done"}'),
        BrainDelta(content=" rambling tail the model kept generating"),
        BrainDelta(finish_reason="stop"),
    ]


def test_call_brain_early_stops_on_complete_json_action() -> None:
    import json as _json

    from jarvis.harness.screenshot_only_loop import _call_brain

    consumed: list[int] = []
    brain = _StreamBrain(_action_stream_deltas(), consumed)
    ctx = _StreamCtx(_StreamManager(brain))
    text = asyncio.run(
        _call_brain(
            ctx,
            observation=_DummyObs(),
            user_goal="x",
            history_text="",
            early_stop_json=True,
        )
    )
    assert _json.loads(text)["action"] == "done"
    assert consumed == [0, 1]  # tail + finish delta never consumed


def test_call_brain_default_drains_whole_stream() -> None:
    from jarvis.harness.screenshot_only_loop import _call_brain

    consumed: list[int] = []
    brain = _StreamBrain(_action_stream_deltas(), consumed)
    ctx = _StreamCtx(_StreamManager(brain))
    text = asyncio.run(
        _call_brain(
            ctx,
            observation=_DummyObs(),
            user_goal="x",
            history_text="",
        )
    )
    # Default (verifier) path is unchanged: the entire stream is read.
    assert consumed == [0, 1, 2, 3]
    assert "rambling tail" in text


def test_make_plan_early_stops_at_complete_json() -> None:
    """The planner call (max 512 out tokens) also early-stops at the plan JSON."""
    from jarvis.core.protocols import BrainDelta
    from jarvis.harness.screenshot_only_loop import _make_plan

    consumed: list[int] = []
    deltas = [
        BrainDelta(content='{"plan": [{"intent":"open Spotify","success":"win"}]'),
        BrainDelta(content="}"),
        BrainDelta(content=" rambling tail after the plan object"),
        BrainDelta(finish_reason="stop"),
    ]
    brain = _StreamBrain(deltas, consumed)
    ctx = _StreamCtx(_StreamManager(brain))
    plan = asyncio.run(_make_plan(ctx, observation=_DummyObs(), user_goal="x"))
    assert plan and plan[0]["intent"] == "open Spotify"
    assert consumed == [0, 1]  # tail never consumed


# ---------------------------------------------------------------------------
# key action (BUG-CU-NO-PLAN): "press Enter" must dispatch to the hotkey tool
# with a keys list -- without it, search flows could never submit.
# ---------------------------------------------------------------------------


class _FakeHotkeyTool:
    name = "hotkey"


def test_execute_key_dispatches_to_hotkey_tool() -> None:
    tool = _FakeHotkeyTool()
    executor = _FakeExecutor()
    # _FakeExecutor.execute returns success for any tool; reuse it.
    ctx = _FakeCtx(executor=executor, tools={"hotkey": tool})

    success, _msg = asyncio.run(
        _execute_action(
            {"action": "key", "keys": ["enter"]},
            ctx,
            trace_id=None,
            user_goal="press enter",
        ),
    )
    assert success is True
    sent_tool, sent_args, _utt = executor.calls[0]
    assert sent_tool is tool
    assert sent_args == {"keys": ["enter"]}


def test_execute_key_missing_hotkey_tool_reports_cleanly() -> None:
    ctx = _FakeCtx(executor=_FakeExecutor(), tools={})  # hotkey not wired
    success, message = asyncio.run(
        _execute_action(
            {"action": "key", "keys": ["enter"]},
            ctx, trace_id=None, user_goal="x",
        ),
    )
    assert success is False
    assert "hotkey" in message


# ---------------------------------------------------------------------------
# UIA-first grounding + compute-result verification (BUG-CU-GROUNDING /
# BUG-CU-RESULT, 2026-05-29): native-app controls must be clicked by name, and
# "rechne" goals must verify the displayed RESULT, not just emit done.
# ---------------------------------------------------------------------------


def test_goal_needs_result_matches_compute_goals() -> None:
    from jarvis.harness.screenshot_only_loop import _goal_needs_result

    assert _goal_needs_result("rechne 8x8 plus 7") is True
    assert _goal_needs_result("berechne 12 geteilt durch 4") is True
    assert _goal_needs_result("8 mal 8") is True
    assert _goal_needs_result("spiel ein lied ab") is False
    assert _goal_needs_result("oeffne chrome") is False


def test_compute_goal_arms_verification() -> None:
    from jarvis.harness.screenshot_only_loop import _goal_needs_verification

    # Compute goals must arm the verifier so a wrong display isn't reported done.
    assert _goal_needs_verification("rechne 8x8 plus 7") is True
    # Play goals still arm it.
    assert _goal_needs_verification("spiel ein lied ab") is True
    # Pure navigation does not.
    assert _goal_needs_verification("oeffne den explorer") is False


def test_parse_verdict_tolerates_fences_and_garbage() -> None:
    from jarvis.harness.screenshot_only_loop import _parse_verdict

    assert _parse_verdict('{"done": true, "proof": "shows 71"}') == (True, "shows 71")
    assert _parse_verdict('```json\n{"done": false, "proof": "shows 130"}\n```') == (
        False, "shows 130",
    )
    assert _parse_verdict("not json") == (False, "")
    assert _parse_verdict("") == (False, "")


# ---------------------------------------------------------------------------
# scroll action (Wave 2) — the loop could not scroll before, so lists/pages
# (chats, file pickers, long web pages) were unreachable. The ScrollTool
# already existed (cross-platform: Win32 SendInput + pyautogui fallback) but
# was never in the loop's action vocabulary.
# ---------------------------------------------------------------------------


class _FakeScrollTool:
    name: str = "scroll"


def test_scroll_is_a_valid_action() -> None:
    assert "scroll" in _VALID_ACTIONS


def test_parse_scroll_accepts_direction_and_defaults_amount() -> None:
    action = _parse_action('{"action": "scroll", "direction": "down"}')
    assert action["action"] == "scroll"
    assert action["direction"] == "down"
    # A sensible default amount so the model can just say "scroll down".
    assert action["amount"] == 3


def test_parse_scroll_normalises_direction_case() -> None:
    action = _parse_action('{"action": "scroll", "direction": "Up", "amount": 5}')
    assert action["direction"] == "up"
    assert action["amount"] == 5


def test_parse_scroll_rejects_invalid_direction() -> None:
    with pytest.raises(CULoopError):
        _parse_action('{"action": "scroll", "direction": "sideways"}')


def test_parse_scroll_rejects_missing_direction() -> None:
    with pytest.raises(CULoopError):
        _parse_action('{"action": "scroll", "amount": 3}')


def test_execute_scroll_dispatches_to_scroll_tool() -> None:
    tool = _FakeScrollTool()
    executor = _FakeExecutor()
    ctx = _FakeCtx(executor=executor, tools={"scroll": tool})

    success, _message = asyncio.run(
        _execute_action(
            {"action": "scroll", "direction": "down", "amount": 4},
            ctx,
            trace_id=None,
            user_goal="scroll the chat list down",
        ),
    )

    assert success is True
    assert len(executor.calls) == 1
    sent_tool, sent_args, _utterance = executor.calls[0]
    assert sent_tool is tool
    assert sent_args["direction"] == "down"
    assert sent_args["amount"] == 4


def test_execute_scroll_reports_missing_tool_cleanly() -> None:
    executor = _FakeExecutor()
    ctx = _FakeCtx(executor=executor, tools={})  # scroll not wired

    success, message = asyncio.run(
        _execute_action(
            {"action": "scroll", "direction": "up", "amount": 3},
            ctx,
            trace_id=None,
            user_goal="scroll up",
        ),
    )

    assert success is False
    assert "scroll" in message.lower()


def test_execute_type_settles_before_dispatch(monkeypatch) -> None:
    """The CU loop must pause briefly before typing so a freshly-focused
    webview/Tauri input is listening -- otherwise leading characters drop
    (CU typo bug 2026-06-15). The settle is awaited BEFORE the type is sent."""
    import jarvis.harness.screenshot_only_loop as loop

    # One shared list records both events in execution order, so we can prove
    # the settle is awaited BEFORE the dispatch -- not merely that both happened.
    events: list[str] = []

    async def _fake_sleep(seconds):
        events.append(f"sleep:{seconds}")

    class _OrderRecordingExecutor:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, dict[str, Any], str]] = []

        async def execute(self, tool, args, *, user_utterance: str = "", trace_id: Any = None):
            events.append("dispatch")
            self.calls.append((tool, args, user_utterance))
            return _FakeResult(success=True, output="typed")

    monkeypatch.setattr(loop.asyncio, "sleep", _fake_sleep)

    # The type read-back gate (audit 🔴 #1) fetches the a11y tree after dispatch.
    # Isolate it with an empty tree so this settle-ORDERING test stays deterministic
    # and never depends on the live desktop's editable fields.
    class _EmptyTree:
        async def observe(self, **_kw):
            return type("_Obs", (), {"nodes": ()})()

    monkeypatch.setattr(loop, "_get_ui_tree_source", lambda: _EmptyTree())

    type_tool = object()
    executor = _OrderRecordingExecutor()
    ctx = _FakeCtx(executor=executor, tools={"type_text": type_tool})

    success, _message = asyncio.run(
        _execute_action(
            {"action": "type", "text": "hello hello hello"},
            ctx,
            trace_id=None,
            user_goal="type hello hello hello into the terminal",
        ),
    )

    assert success is True
    # Ordering proof: the settle is awaited BEFORE the dispatch.
    assert events == [f"sleep:{loop._PRE_TYPE_SETTLE_S}", "dispatch"]
    sent_tool, sent_args, _utterance = executor.calls[0]
    assert sent_tool is type_tool
    assert sent_args["text"] == "hello hello hello"


# ---------------------------------------------------------------------------
# Per-mission open_app launch cap (window-spam fix, 2026-05-29). A loop that
# never reaches "done" (a goal it cannot ground, or a model that keeps asking
# to open the app) used to re-launch the same app every few steps -> 4-7 real
# windows per mission, dozens across retried missions ("30 calculators for 7+7",
# "30 text editors for Hello World", live: 7 Spotify windows in one 51s mission).
# The cap launches each app AT MOST ONCE per mission; any further open_app for
# the same app is suppressed with a history note pointing the model at the
# already-open window. "one is enough" (user mandate 2026-05-29).
# ---------------------------------------------------------------------------


class _CapObs:
    """Fresh Observation per capture with a UNIQUE hash so the no-progress
    guard never trips and the loop runs its whole step budget."""

    def __init__(self, n: int) -> None:
        self.screenshot_hash = f"hash-{n}"
        self.screenshot_path = None
        self.trace_id = None
        self.window_title = "Rechner"
        self.source = "test"
        self.timestamp_ns = n


class _CapVision:
    def __init__(self) -> None:
        self._n = 0

    async def observe(self, *, mode: str = "auto", cancel_token: Any = None):
        self._n += 1
        return _CapObs(self._n)


class _OpenAppForeverBrain:
    """Pathological brain: ALWAYS asks to open the same app, never emits done.
    This is the exact never-terminating shape that produced the window spam."""

    async def complete_text(self, *, system: str, user: str) -> str:
        return '{"action": "open_app", "name": "calc"}'


class _CapCtx:
    def __init__(self, *, vision, brain, executor, tools) -> None:
        self.vision_engine = vision
        self.brain_manager = brain
        self.tool_executor = executor
        self.tools = tools
        self.bus = None
        self.per_step_timeout_s = 5.0
        self.step_budget = 25
        self.native_cu = None


class _CapTask:
    def __init__(self, prompt: str) -> None:
        self.prompt = prompt
        self.timeout_s = 60.0


async def _drain(agen):
    last = None
    async for chunk in agen:
        last = chunk
    return last


def test_open_app_launched_at_most_once_per_mission(monkeypatch) -> None:
    import jarvis.harness.screenshot_only_loop as loop

    # Fast + deterministic on Windows: no real UIA enumeration, no real
    # monitor probing (open_app needs neither).
    async def _no_labels(_timeout_s: float, max_n: int = 28):
        return []

    monkeypatch.setattr(loop, "_foreground_clickable_labels", _no_labels)
    monkeypatch.setattr(loop, "_capture_monitor_geometry", lambda: (0, 0, 0, 0))

    executor = _FakeExecutor()
    tool = _FakeOpenAppTool()
    ctx = _CapCtx(
        vision=_CapVision(),
        brain=_OpenAppForeverBrain(),
        executor=executor,
        tools={"open_app": tool},
    )

    final = asyncio.run(
        _drain(loop._run_screenshot_loop(_CapTask("open calc"), ctx, cancel_token=None)),
    )

    # The model asked to open calc on EVERY one of the ~25 steps, but the
    # per-mission cap must let it reach the launcher exactly ONCE. Pre-fix the
    # 2-step cooldown re-allowed a fresh launch every 3 steps -> ~9 windows.
    calc_launches = [c for c in executor.calls if c[1].get("app_name") == "calc"]
    assert len(calc_launches) == 1, (
        f"window-spam regression: calc was launched {len(calc_launches)}× in one "
        "mission, expected exactly 1 ('einer langt')"
    )
    # The loop still terminates (does not hang) — here via budget exhaustion,
    # since the pathological brain never emits done.
    assert final is not None and final.is_final
