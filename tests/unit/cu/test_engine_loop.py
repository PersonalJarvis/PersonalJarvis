"""CU v2 engine state-machine tests — scripted FakeBrain, fake tool layer.

Each test encodes one of the observed live failure classes the rebuild must
kill: duplicate actions on an unchanged screen, blind typing after a missed
focus click, a second stale pointer action in one batch, and unverified
"done" claims.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("PIL", reason="pillow required for frame handling")

import jarvis.cu.engine as engine_mod
from jarvis.cu.capture import capture_stable_frame
from jarvis.cu.geometry import MonitorInfo

MONITOR = MonitorInfo(left=0, top=0, width=192, height=108)


class FakeBrain:
    """Scripted ``complete_text`` manager: pops one reply per call."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, str]] = []

    async def complete_text(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self.replies:
            return '{"action": "wait", "ms": 1}'
        return self.replies.pop(0)


class FakeExecutor:
    """Records every dispatched tool call; scriptable per-tool success."""

    def __init__(self, failures: set[str] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.failures = failures or set()

    async def execute(self, tool, args, *, user_utterance, trace_id):
        name = tool["name"]
        self.calls.append((name, dict(args)))
        if name in self.failures:
            return SimpleNamespace(success=False, output=None, error=f"{name} broke")
        return SimpleNamespace(success=True, output=f"{name} ok")


def _tools() -> dict[str, Any]:
    return {
        name: {"name": name}
        for name in (
            "click", "type_text", "hotkey", "scroll", "drag",
            "open_app", "switch_window", "click_element",
        )
    }


def _ctx(brain: FakeBrain, executor: FakeExecutor) -> SimpleNamespace:
    return SimpleNamespace(
        brain_manager=brain,
        tool_executor=executor,
        tools=_tools(),
        bus=None,
        step_budget=30,
        monitor="primary",
        main_monitor="primary",
        settle_scale=0.0,          # no real sleeps in tests
        strict_verify=True,
        image_max_dimension=192,
        coordinate_space="auto",
    )


def _solid(shade: int = 30) -> tuple[tuple[int, int], bytes]:
    return ((192, 108), bytes((shade, shade, shade)) * (192 * 108))


@pytest.fixture
def patched(monkeypatch, tmp_path):
    """Patch every OS touchpoint of the engine to deterministic fakes."""
    state = SimpleNamespace(
        screen_shade=30,
        typed_lands=True,          # bool, or a list popped per verify call
        region_changes_after_click=True,
        clicks_seen=0,
        clickables=[],
        focus_hit=None,            # verify_click_focus_point verdict
        capture_calls=0,           # capture_stable_frame invocations
        foreground_handle=11,
        foreground_rect=(0, 0, 192, 108),
    )

    def fake_select_capture_target(
        policy, *, main_monitor="primary", scope="window",
    ):
        return MONITOR

    def fake_capture(monitor, *, max_dimension, blob_dir=None, **kw):
        state.capture_calls += 1
        return capture_stable_frame(
            monitor,
            grab=lambda bbox: _solid(state.screen_shade),
            sleep=lambda s: None,
            max_dimension=max_dimension,
            blob_dir=Path(tmp_path),
            capture_guard=kw.get("capture_guard"),
        )

    def fake_grab_region(bbox, *, grab=None):
        # After a click the "post" grab differs when the fake says so.
        if state.region_changes_after_click and state.clicks_seen % 2 == 1:
            state.clicks_seen += 1
            return _solid(state.screen_shade + 40)
        state.clicks_seen += 1
        return _solid(state.screen_shade)

    async def fake_snapshot(*a, **kw):
        return [], "", None, state.clickables

    async def fake_verify_typed(text):
        if isinstance(state.typed_lands, list):
            return state.typed_lands.pop(0) if state.typed_lands else None
        return state.typed_lands

    async def fake_verify_click_focus_point(x, y, **kwargs):
        return state.focus_hit

    monkeypatch.setattr(
        engine_mod, "select_capture_target", fake_select_capture_target,
    )
    monkeypatch.setattr(engine_mod, "list_monitors", lambda: [MONITOR])
    monkeypatch.setattr(engine_mod, "capture_stable_frame", fake_capture)
    monkeypatch.setattr(engine_mod, "grab_region", fake_grab_region)
    monkeypatch.setattr(engine_mod, "foreground_ui_snapshot", fake_snapshot)
    monkeypatch.setattr(engine_mod, "verify_typed_text", fake_verify_typed)
    monkeypatch.setattr(
        engine_mod, "verify_click_focus_point", fake_verify_click_focus_point,
    )
    monkeypatch.setattr(engine_mod, "_foreground_title", lambda: "Test Window")
    # The engine normalizes the target window via jarvis.platform.window_state;
    # tests must never maximize a real window on the dev machine.
    from jarvis.platform import window_state as ws

    monkeypatch.setattr(
        ws, "normalize_foreground_window", lambda: (False, "test"),
    )
    monkeypatch.setattr(
        ws,
        "foreground_window",
        lambda: ws.WindowInfo("Test Window", handle=state.foreground_handle),
    )
    monkeypatch.setattr(
        ws, "window_frame_rect", lambda _window: state.foreground_rect,
    )
    return state


async def _run(ctx) -> list:
    task = SimpleNamespace(prompt="open the thing", env={}, timeout_s=60)
    return [
        chunk
        async for chunk in engine_mod.run_cu_loop(task, ctx, cancel_token=None)
    ]


def _final(chunks):
    finals = [c for c in chunks if c.is_final]
    assert len(finals) == 1
    return finals[0]


# ---------------------------------------------------------------------------


async def test_done_is_verified_and_proof_flows_to_stdout(patched):
    brain = FakeBrain([
        '{"action": "done", "reason": "it is visible"}',       # decide
        '{"done": true, "proof": "the calculator shows 8"}',    # judge
    ])
    chunks = await _run(_ctx(brain, FakeExecutor()))
    final = _final(chunks)
    assert final.exit_code == 0
    assert "[cu] done (verified: the calculator shows 8)" in final.stdout


async def test_dispatch_refuses_action_after_screen_permission_revocation(
    monkeypatch,
):
    executor = FakeExecutor()
    ctx = _ctx(FakeBrain([]), executor)
    monkeypatch.setattr(
        "jarvis.cu.capture._require_macos_screen_recording_permission",
        lambda: (_ for _ in ()).throw(
            RuntimeError("Screen Recording permission was revoked"),
        ),
    )

    ok, detail = await engine_mod._dispatch_tool(
        ctx, "click", {"x": 10, "y": 10}, trace_id=None,
    )

    assert ok is False
    assert "Screen Recording" in detail
    assert executor.calls == []


async def test_rejected_done_feeds_history_and_eventually_fails(patched):
    brain = FakeBrain(
        ['{"action": "done", "reason": "sure"}',
         '{"done": false, "proof": "the window is not open"}'] * 3,
    )
    chunks = await _run(_ctx(brain, FakeExecutor()))
    final = _final(chunks)
    assert final.exit_code == 5
    assert "could not be verified" in final.stderr
    # The judge's rejection reached the model as history.
    assert any("done REJECTED" in user for (_, user) in brain.calls)


async def test_duplicate_click_on_unchanged_screen_is_refused(patched):
    patched.region_changes_after_click = True
    brain = FakeBrain([
        '{"action": "click", "x": 500, "y": 500, "target": "play"}',
        '{"action": "click", "x": 502, "y": 498, "target": "play"}',  # same spot
        '{"action": "done", "reason": "toggled"}',
        '{"done": true, "proof": "playback is running"}',
    ])
    executor = FakeExecutor()
    chunks = await _run(_ctx(brain, executor))
    final = _final(chunks)
    assert final.exit_code == 0
    clicks = [c for c in executor.calls if c[0] == "click"]
    assert len(clicks) == 1, "the near-identical re-click must be refused"
    assert any("REFUSED" in user for (_, user) in brain.calls)


async def test_type_that_does_not_land_blocks_the_rest_of_the_batch(patched):
    patched.typed_lands = False
    brain = FakeBrain([
        # Blind batch: type + enter. The type read-back fails -> enter must NOT run.
        '[{"action":"type","text":"https://example.com"},'
        '{"action":"key","keys":["enter"]}]',
        '{"action": "fail", "reason": "cannot focus the address bar"}',
    ])
    executor = FakeExecutor()
    chunks = await _run(_ctx(brain, executor))
    final = _final(chunks)
    assert final.exit_code == 5
    tool_names = [name for (name, _) in executor.calls]
    assert "type_text" in tool_names
    assert "hotkey" not in tool_names, "enter after a failed type is the blind-action bug"
    assert any("did NOT land" in user for (_, user) in brain.calls)


async def test_second_pointer_action_in_one_batch_is_skipped(patched):
    brain = FakeBrain([
        '[{"action":"click","x":100,"y":100},'
        '{"action":"click","x":900,"y":900}]',
        '{"action": "done", "reason": "clicked"}',
        '{"done": true, "proof": "both dialogs handled"}',
    ])
    executor = FakeExecutor()
    chunks = await _run(_ctx(brain, executor))
    assert _final(chunks).exit_code == 0
    clicks = [c for c in executor.calls if c[0] == "click"]
    assert len(clicks) == 1, "a second stale-frame pointer action must not execute"
    assert any("only one pointer action" in user for (_, user) in brain.calls)


async def test_click_then_click_element_in_one_batch_is_skipped(patched):
    brain = FakeBrain([
        '[{"action":"click","x":100,"y":100},'
        '{"action":"click_element","name":"Continue"}]',
        '{"action": "done", "reason": "clicked"}',
        '{"done": true, "proof": "the dialog advanced"}',
    ])
    executor = FakeExecutor()

    chunks = await _run(_ctx(brain, executor))

    assert _final(chunks).exit_code == 0
    assert [name for name, _args in executor.calls].count("click") == 1
    assert not any(name == "click_element" for name, _args in executor.calls)
    assert any("only one pointer action" in user for _system, user in brain.calls)


async def test_foreground_change_after_model_decision_refuses_action(patched):
    class _FocusChangingBrain(FakeBrain):
        async def complete_text(self, *, system: str, user: str) -> str:
            reply = await super().complete_text(system=system, user=user)
            if len(self.calls) == 1:
                patched.foreground_handle = 22
            return reply

    brain = _FocusChangingBrain([
        '{"action":"click","x":500,"y":500}',
        '{"action":"fail","reason":"the target window changed"}',
    ])
    executor = FakeExecutor()

    chunks = await _run(_ctx(brain, executor))

    assert _final(chunks).exit_code == 5
    assert executor.calls == []
    assert any("foreground window changed" in user for _system, user in brain.calls)


async def test_foreground_change_during_capture_discards_stale_frame(
    patched,
    monkeypatch,
):
    from jarvis.platform import window_state as ws

    handles = iter((11, 22, 22, 22))
    monkeypatch.setattr(
        ws,
        "foreground_window",
        lambda: ws.WindowInfo("Test Window", handle=next(handles, 22)),
    )
    brain = FakeBrain([
        '{"action":"fail","reason":"fresh frame reached the model"}',
    ])
    executor = FakeExecutor()

    chunks = await _run(_ctx(brain, executor))

    assert _final(chunks).exit_code == 5
    assert patched.capture_calls == 2
    assert len(brain.calls) == 1
    assert executor.calls == []


async def test_coordinate_less_scroll_targets_capture_center_and_stales_batch(patched):
    brain = FakeBrain([
        '[{"action":"scroll","direction":"down","amount":2},'
        '{"action":"click","x":900,"y":900}]',
        '{"action": "done", "reason": "scrolled"}',
        '{"done": true, "proof": "the target is visible"}',
    ])
    executor = FakeExecutor()

    chunks = await _run(_ctx(brain, executor))

    assert _final(chunks).exit_code == 0
    scrolls = [args for name, args in executor.calls if name == "scroll"]
    assert scrolls == [
        {
            "direction": "down",
            "amount": 2,
            "x": 96,
            "y": 54,
            "_expected_window_signature": ("handle", 11, (0, 0, 192, 108)),
        }
    ]
    assert not any(name == "click" for name, _args in executor.calls)
    assert any("only one pointer action" in user for _system, user in brain.calls)


async def test_done_judge_reuses_the_step_frame_when_no_action_ran(patched):
    # "Say you are done FAST": when `done` arrives before any action of the
    # batch executed, the screen is exactly the frame the model saw — the
    # judge must verify against THAT frame instead of paying a second
    # stability capture (live complaint 2026-07-02: the completion
    # confirmation took too long).
    brain = FakeBrain([
        '{"action": "done", "reason": "goal visible"}',
        '{"done": true, "proof": "the channel is open"}',
    ])
    executor = FakeExecutor()
    chunks = await _run(_ctx(brain, executor))
    assert _final(chunks).exit_code == 0
    assert patched.capture_calls == 1, (
        "the done-judge must reuse the perception frame, not recapture"
    )


async def test_done_judge_recaptures_after_batch_actions(patched):
    # After any executed action the screen may differ from the perception
    # frame — the judge must then verify against a FRESH capture.
    brain = FakeBrain([
        '[{"action":"key","keys":["enter"]},'
        '{"action":"done","reason":"submitted"}]',
        '{"done": true, "proof": "the form is submitted"}',
    ])
    executor = FakeExecutor()
    chunks = await _run(_ctx(brain, executor))
    assert _final(chunks).exit_code == 0
    assert patched.capture_calls == 2, (
        "a done after executed actions needs a fresh judge frame"
    )


async def test_click_on_already_focused_target_passes_and_type_proceeds(patched):
    # Live incident 2026-07-02 19:06 (Chrome guest new-tab): the address bar
    # is focused BY DEFAULT, so clicking it changes zero pixels. The pixel
    # effect-check alone judged that a miss, truncated the batched type, and
    # the mission stalled AT its goal. Focus evidence must rescue the click.
    patched.region_changes_after_click = False
    patched.focus_hit = True     # the click point sits in the focused control
    brain = FakeBrain([
        '[{"action":"click","x":300,"y":88,"target":"address bar"},'
        '{"action":"type","text":"weather berlin"}]',
        '{"action": "done", "reason": "typed the search"}',
        '{"done": true, "proof": "the address bar shows weather berlin"}',
    ])
    executor = FakeExecutor()
    chunks = await _run(_ctx(brain, executor))
    assert _final(chunks).exit_code == 0
    tool_names = [name for (name, _) in executor.calls]
    assert "click" in tool_names
    assert "type_text" in tool_names, (
        "a click on an already-focused target must not behead the batch"
    )
    assert not any("NO visible change" in user for (_, user) in brain.calls)


async def test_type_false_verdict_is_rechecked_once_before_failing(patched):
    # Async UI surfaces (UWP flyouts, start menu) commit the typed value
    # LATER than the injection returns — the first read-back sees stale
    # state (live incident 2026-07-02 18:00). One re-check absorbs that.
    patched.typed_lands = [False, True]
    brain = FakeBrain([
        '{"action":"type","text":"spotify"}',
        '{"action": "done", "reason": "typed"}',
        '{"done": true, "proof": "the search shows spotify"}',
    ])
    executor = FakeExecutor()
    chunks = await _run(_ctx(brain, executor))
    assert _final(chunks).exit_code == 0
    assert not any("did NOT land" in user for (_, user) in brain.calls)


async def test_missed_click_no_visible_change_is_reported_as_failure(patched):
    patched.region_changes_after_click = False   # click voids: nothing changes
    brain = FakeBrain([
        '{"action": "click", "x": 500, "y": 500, "target": "button"}',
        '{"found": false}',   # the zoom-refine probe finds nothing either
        '{"action": "fail", "reason": "the button does not react"}',
    ])
    executor = FakeExecutor()
    chunks = await _run(_ctx(brain, executor))
    final = _final(chunks)
    assert final.exit_code == 5
    assert any("NO visible change" in user for (_, user) in brain.calls)


async def test_clear_first_sends_platform_select_all(patched):
    brain = FakeBrain([
        '{"action":"type","text":"example.com","clear_first":true}',
        '{"action": "done", "reason": "typed"}',
        '{"done": true, "proof": "the address bar shows example.com"}',
    ])
    executor = FakeExecutor()
    chunks = await _run(_ctx(brain, executor))
    assert _final(chunks).exit_code == 0
    hotkeys = [args for (name, args) in executor.calls if name == "hotkey"]
    assert hotkeys and hotkeys[0]["keys"][-1] == "a"
    assert hotkeys[0]["_expected_window_signature"] == (
        "handle",
        11,
        (0, 0, 192, 108),
    )


async def test_clear_first_aborts_typing_when_select_all_fails(patched):
    brain = FakeBrain([
        '{"action":"type","text":"replacement","clear_first":true}',
        '{"action":"fail","reason":"keyboard shortcut unavailable"}',
    ])
    executor = FakeExecutor(failures={"hotkey"})

    chunks = await _run(_ctx(brain, executor))

    assert _final(chunks).exit_code == 5
    assert not any(name == "type_text" for name, _args in executor.calls)
    assert any("refusing to append" in user for _system, user in brain.calls)


async def test_cancelled_token_stops_immediately(patched):
    token = SimpleNamespace(is_cancelled=lambda: True, reason="hangup")
    brain = FakeBrain([])
    task = SimpleNamespace(prompt="anything", env={}, timeout_s=60)
    chunks = [
        c async for c in engine_mod.run_cu_loop(
            task, _ctx(brain, FakeExecutor()), cancel_token=token,
        )
    ]
    final = _final(chunks)
    assert final.exit_code == 130
    assert "[cu] cancelled" in final.stderr
    assert not brain.calls


async def test_unparseable_replies_exhaust_llm_budget(patched):
    brain = FakeBrain(["gibberish", "more gibberish", "still gibberish"])
    chunks = await _run(_ctx(brain, FakeExecutor()))
    final = _final(chunks)
    assert final.exit_code == 2


async def test_tool_failures_exhaust_consecutive_budget(patched):
    brain = FakeBrain(
        ['{"action":"open_app","name":"spotify"}'] * 8,
    )
    executor = FakeExecutor(failures={"open_app"})
    chunks = await _run(_ctx(brain, executor))
    final = _final(chunks)
    # Either the consecutive-failure cap (8) or the duplicate guard (5) ends
    # it — both are honest failures, never silent grinding.
    assert final.exit_code in (5, 8)


async def test_click_is_anchored_to_containing_element_center(patched):
    # Model points a few px off inside a small button whose center is
    # (145, 87): the dispatched click must hit the CENTER, not the estimate.
    patched.clickables = [("Send", "Button", (130, 80, 30, 14))]
    brain = FakeBrain([
        # norm (740, 787) on the 192x108 monitor -> raw point (142, 85).
        '{"action": "click", "x": 740, "y": 787, "target": "Send"}',
        '{"action": "done", "reason": "sent"}',
        '{"done": true, "proof": "the message shows as sent"}',
    ])
    executor = FakeExecutor()
    chunks = await _run(_ctx(brain, executor))
    assert _final(chunks).exit_code == 0
    clicks = [args for (name, args) in executor.calls if name == "click"]
    assert clicks and (clicks[0]["x"], clicks[0]["y"]) == (145, 87)


async def test_container_sized_elements_never_snap(patched):
    # A rect covering most of the capture is a container — the raw point
    # must be kept.
    patched.clickables = [("Body", "Text", (0, 0, 190, 100))]
    brain = FakeBrain([
        '{"action": "click", "x": 500, "y": 500, "target": "middle"}',
        '{"action": "done", "reason": "ok"}',
        '{"done": true, "proof": "clicked"}',
    ])
    executor = FakeExecutor()
    chunks = await _run(_ctx(brain, executor))
    assert _final(chunks).exit_code == 0
    clicks = [args for (name, args) in executor.calls if name == "click"]
    assert clicks and (clicks[0]["x"], clicks[0]["y"]) == (96, 54)


async def test_verified_miss_triggers_one_zoom_refined_retry(patched):
    patched.region_changes_after_click = False  # every click: no visible change
    brain = FakeBrain([
        '{"action": "click", "x": 500, "y": 500, "target": "tiny icon"}',
        # The zoom-refine call answers with a corrected in-crop position.
        '{"found": true, "x": 900, "y": 900}',
        '{"action": "fail", "reason": "the icon does not react"}',
    ])
    executor = FakeExecutor()
    chunks = await _run(_ctx(brain, executor))
    final = _final(chunks)
    assert final.exit_code == 5
    clicks = [args for (name, args) in executor.calls if name == "click"]
    assert len(clicks) == 2, "exactly one refined retry after the verified miss"
    assert (clicks[1]["x"], clicks[1]["y"]) != (clicks[0]["x"], clicks[0]["y"])
    assert all(
        args["_expected_window_signature"][0:2] == ("handle", 11)
        for args in clicks
    )


async def test_zoom_refine_refuses_click_after_foreground_switch(patched):
    patched.region_changes_after_click = False

    class _SwitchDuringRefineBrain(FakeBrain):
        async def complete_text(self, *, system: str, user: str) -> str:
            reply = await super().complete_text(system=system, user=user)
            if len(self.calls) == 2:
                patched.foreground_handle = 22
            return reply

    brain = _SwitchDuringRefineBrain([
        '{"action": "click", "x": 500, "y": 500, "target": "tiny icon"}',
        '{"found": true, "x": 900, "y": 900}',
        '{"action": "fail", "reason": "the original window lost focus"}',
    ])
    executor = FakeExecutor()

    chunks = await _run(_ctx(brain, executor))

    assert _final(chunks).exit_code == 5
    clicks = [args for name, args in executor.calls if name == "click"]
    assert len(clicks) == 1, "the stale zoom-refined click must not execute"


async def test_handoff_screen_fails_fast_with_speakable_reason(patched, monkeypatch):
    async def snapshot_with_captcha(*a, **kw):
        return [], "", "captcha challenge", []

    monkeypatch.setattr(engine_mod, "foreground_ui_snapshot", snapshot_with_captcha)
    brain = FakeBrain([])
    chunks = await _run(_ctx(brain, FakeExecutor()))
    final = _final(chunks)
    assert final.exit_code == 5
    assert "captcha challenge" in final.stderr
    assert not brain.calls, "no model call may happen on a handoff screen"
