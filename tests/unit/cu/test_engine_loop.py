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
        typed_lands=True,
        region_changes_after_click=True,
        clicks_seen=0,
    )

    def fake_select_capture_target(
        policy, *, main_monitor="primary", scope="window",
    ):
        return MONITOR

    def fake_capture(monitor, *, max_dimension, blob_dir=None, **kw):
        return capture_stable_frame(
            monitor,
            grab=lambda bbox: _solid(state.screen_shade),
            sleep=lambda s: None,
            max_dimension=max_dimension,
            blob_dir=Path(tmp_path),
        )

    def fake_grab_region(bbox, *, grab=None):
        # After a click the "post" grab differs when the fake says so.
        if state.region_changes_after_click and state.clicks_seen % 2 == 1:
            state.clicks_seen += 1
            return _solid(state.screen_shade + 40)
        state.clicks_seen += 1
        return _solid(state.screen_shade)

    async def fake_snapshot(*a, **kw):
        return [], "", None

    async def fake_verify_typed(text):
        return state.typed_lands

    monkeypatch.setattr(
        engine_mod, "select_capture_target", fake_select_capture_target,
    )
    monkeypatch.setattr(engine_mod, "capture_stable_frame", fake_capture)
    monkeypatch.setattr(engine_mod, "grab_region", fake_grab_region)
    monkeypatch.setattr(engine_mod, "foreground_ui_snapshot", fake_snapshot)
    monkeypatch.setattr(engine_mod, "verify_typed_text", fake_verify_typed)
    monkeypatch.setattr(engine_mod, "_foreground_title", lambda: "Test Window")
    # The engine normalizes the target window via jarvis.platform.window_state;
    # tests must never maximize a real window on the dev machine.
    from jarvis.platform import window_state as ws

    monkeypatch.setattr(
        ws, "normalize_foreground_window", lambda: (False, "test"),
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


async def test_missed_click_no_visible_change_is_reported_as_failure(patched):
    patched.region_changes_after_click = False   # click voids: nothing changes
    brain = FakeBrain([
        '{"action": "click", "x": 500, "y": 500, "target": "button"}',
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


async def test_handoff_screen_fails_fast_with_speakable_reason(patched, monkeypatch):
    async def snapshot_with_captcha(*a, **kw):
        return [], "", "captcha challenge"

    monkeypatch.setattr(engine_mod, "foreground_ui_snapshot", snapshot_with_captcha)
    brain = FakeBrain([])
    chunks = await _run(_ctx(brain, FakeExecutor()))
    final = _final(chunks)
    assert final.exit_code == 5
    assert "captcha challenge" in final.stderr
    assert not brain.calls, "no model call may happen on a handoff screen"
