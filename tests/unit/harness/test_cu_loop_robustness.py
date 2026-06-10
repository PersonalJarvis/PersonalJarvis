"""Loop-level robustness tests for the screenshot-only Computer-Use loop.

Covers the 2026-06-09 "not shippable" fix waves:

* Wave A — mid-task aborts: the empty-window-title false regression
  (Chrome & friends run in screenshot mode where ``window_title`` is empty,
  which the loop used to read as "the app window is gone"), single
  brain/parse failures killing the whole mission instead of retrying, and
  the ``_call_brain`` fallthrough that silently returned ``None``.

All tests drive ``_run_screenshot_loop`` directly with fakes — no real
screenshots, no real UIA, no real model calls.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import pytest

import jarvis.harness.screenshot_only_loop as loop_mod
from jarvis.core.protocols import HarnessResult, HarnessTask, Observation
from jarvis.harness.computer_use_context import ComputerUseContext
from jarvis.harness.screenshot_only_loop import (
    CULoopError,
    _call_brain,
    _run_screenshot_loop,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeVisionEngine:
    """Yields a fresh observation per call (unique hash so the no-progress
    guard never trips) with a configurable window title sequence."""

    def __init__(self, window_titles: list[str] | None = None) -> None:
        self.calls = 0
        self._titles = window_titles

    async def observe(self, *, mode: str = "auto", cancel_token: Any = None,
                      window_title_filter: str | None = None) -> Observation:
        idx = self.calls
        self.calls += 1
        title = ""
        if self._titles is not None:
            title = self._titles[min(idx, len(self._titles) - 1)]
        return Observation(
            trace_id=uuid4(),
            timestamp_ns=time.time_ns(),
            screenshot_path=None,
            screenshot_hash=f"hash-{idx}",
            nodes=(),
            window_title=title,
            active_pid=0,
            source="screenshot_only",
            pruning_stats={},
        )


class FakeBrain:
    """``complete_text`` shim — answers from a script or handler.

    Records every (system, user) pair so tests can assert on the prompt the
    executor actually saw (e.g. that no false REGRESSION note was injected).
    """

    def __init__(
        self,
        script: list[str | Exception] | None = None,
        handler: Callable[[str, str], str] | None = None,
    ) -> None:
        self.script = list(script or [])
        self.handler = handler
        self.requests: list[tuple[str, str]] = []

    async def complete_text(self, *, system: str, user: str) -> str:
        self.requests.append((system, user))
        if self.handler is not None:
            return self.handler(system, user)
        item = self.script.pop(0) if self.script else '{"action": "done"}'
        if isinstance(item, Exception):
            raise item
        return item


class FakeToolResult:
    def __init__(self, success: bool = True, output: str = "ok") -> None:
        self.success = success
        self.output = output
        self.error = ""


class FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, tool: Any, args: dict[str, Any], *,
                      user_utterance: str = "", trace_id: Any = None) -> FakeToolResult:
        self.calls.append((tool.name, dict(args)))
        return FakeToolResult()


def make_ctx(brain: FakeBrain, *, titles: list[str] | None = None,
             verify: bool = False, step_budget: int = 10) -> ComputerUseContext:
    tools = {
        name: FakeTool(name)
        for name in ("open_app", "click", "type_text", "hotkey",
                     "click_element", "scroll")
    }
    return ComputerUseContext(
        vision_engine=FakeVisionEngine(window_titles=titles),
        brain_manager=brain,
        tool_executor=FakeExecutor(),
        tools=tools,
        bus=None,
        step_budget=step_budget,
        per_step_timeout_s=5.0,
        verify_after_each_step=verify,
    )


async def run_loop(ctx: ComputerUseContext, goal: str) -> list[HarnessResult]:
    task = HarnessTask(prompt=goal, timeout_s=30)
    chunks: list[HarnessResult] = []
    async for chunk in _run_screenshot_loop(task, ctx):
        chunks.append(chunk)
    return chunks


@pytest.fixture(autouse=True)
def _isolate_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touch the real desktop from these tests: no UIA enumeration,
    no win32 monitor probing."""
    async def _no_labels(timeout_s: float, max_n: int = 28) -> list[str]:
        return []

    monkeypatch.setattr(loop_mod, "_foreground_clickable_labels", _no_labels)
    monkeypatch.setattr(
        loop_mod, "_capture_monitor_geometry", lambda: (0, 0, 1920, 1080),
    )
    # Keep the UI-tree-source singleton hermetic between tests.
    monkeypatch.setattr(loop_mod, "_UI_TREE_SOURCE", None, raising=False)


# ---------------------------------------------------------------------------
# Wave A1/A2 — empty window title must not read as "app window gone"
# ---------------------------------------------------------------------------


async def test_empty_window_title_is_not_a_desktop_regression() -> None:
    """Chrome (and every _TEXT_HEAVY_HINTS app) runs in screenshot mode where
    the observation's window_title is EMPTY. The regression detector used to
    treat an empty title as "foreground fell to the desktop" and told the
    model to re-open the app it had just opened."""
    brain = FakeBrain(script=[
        '{"action": "open_app", "name": "chrome"}',
        '{"action": "done"}',
    ])
    ctx = make_ctx(brain, titles=[""])  # title stays empty like live Chrome
    # Single-verb goal: stays on the planless path so the script order is
    # deterministic (compound goals now trigger the planner — Wave C).
    chunks = await run_loop(ctx, "oeffne chrome")

    assert chunks[-1].exit_code == 0
    # The second executor turn must NOT contain a false regression note.
    assert len(brain.requests) == 2
    _system, user = brain.requests[1]
    assert "REGRESSION" not in user


async def test_real_desktop_title_still_detected_as_regression() -> None:
    """A genuine fall to the desktop (Program Manager foreground) must still
    inject the regression note so recover-after-close keeps working."""
    brain = FakeBrain(script=[
        '{"action": "open_app", "name": "chrome"}',
        '{"action": "done"}',
    ])
    ctx = make_ctx(brain, titles=["Program Manager"])
    chunks = await run_loop(ctx, "oeffne chrome")

    assert chunks[-1].exit_code == 0
    assert len(brain.requests) == 2
    _system, user = brain.requests[1]
    assert "REGRESSION" in user


# ---------------------------------------------------------------------------
# Wave A3 — single model failures retry instead of killing the mission
# ---------------------------------------------------------------------------


async def test_garbage_model_response_is_retried() -> None:
    brain = FakeBrain(script=[
        "THIS IS NOT JSON AT ALL",
        '{"action": "done"}',
    ])
    ctx = make_ctx(brain)
    chunks = await run_loop(ctx, "mach das fenster groesser bitte")

    assert chunks[-1].exit_code == 0
    assert len(brain.requests) == 2


async def test_brain_exception_is_retried() -> None:
    brain = FakeBrain(script=[
        RuntimeError("provider hiccup"),
        '{"action": "done"}',
    ])
    ctx = make_ctx(brain)
    chunks = await run_loop(ctx, "mach das fenster groesser bitte")

    assert chunks[-1].exit_code == 0
    assert len(brain.requests) == 2


async def test_repeated_model_failures_end_mission_cleanly() -> None:
    brain = FakeBrain(script=[
        "garbage one",
        "garbage two",
        "garbage three",
        '{"action": "done"}',  # must never be reached
    ])
    ctx = make_ctx(brain)
    chunks = await run_loop(ctx, "mach das fenster groesser bitte")

    final = chunks[-1]
    assert final.is_final
    assert final.exit_code != 0
    assert final.stderr  # clean, explicit failure message
    assert len(brain.requests) == 3


# ---------------------------------------------------------------------------
# Wave A4 — _call_brain must raise on an unusable manager, never return None
# ---------------------------------------------------------------------------


async def test_call_brain_raises_on_unusable_manager() -> None:
    ctx = make_ctx(FakeBrain())
    ctx.brain_manager = object()  # no complete_text, no _get_brain, not callable
    obs = Observation(
        trace_id=uuid4(), timestamp_ns=time.time_ns(),
        screenshot_path=None, screenshot_hash="x",
    )
    with pytest.raises(CULoopError):
        await _call_brain(ctx, observation=obs, user_goal="g", history_text="")


def test_decide_native_batch_defined_only_once() -> None:
    """Guard against the duplicated-function merge accident: the module must
    define _decide_native_batch exactly once."""
    import inspect

    src = inspect.getsource(loop_mod)
    assert src.count("async def _decide_native_batch(") == 1


# ---------------------------------------------------------------------------
# Wave B — generic done-verification gated by verify_after_each_step
#
# "done" used to be taken on faith for every goal except calculators
# (compute) and play/submit (media) — so "open Chrome" could end with
# exit 0 after merely TYPING "chrome" into a search box. Now every "done"
# goes through a strict screenshot judge when verify_after_each_step is on.
# ---------------------------------------------------------------------------


class JudgingBrain(FakeBrain):
    """Routes executor calls and judge calls to separate scripts.

    Judge calls are recognised by the system prompt (every verifier prompt
    self-describes as a strict ... judge)."""

    def __init__(self, executor_script: list[str],
                 judge_script: list[str]) -> None:
        super().__init__()
        self.executor_script = list(executor_script)
        self.judge_script = list(judge_script)
        self.judge_calls: list[tuple[str, str]] = []
        self.executor_calls: list[tuple[str, str]] = []

    async def complete_text(self, *, system: str, user: str) -> str:
        self.requests.append((system, user))
        if "judge" in system.lower():
            self.judge_calls.append((system, user))
            return self.judge_script.pop(0) if self.judge_script else (
                '{"done": false, "proof": ""}'
            )
        self.executor_calls.append((system, user))
        return self.executor_script.pop(0) if self.executor_script else (
            '{"action": "fail", "reason": "script exhausted"}'
        )


async def test_premature_done_is_rejected_and_mission_continues() -> None:
    """The live 'Öffne Chrome' failure: the model declares done without
    having opened anything. The judge must reject it, the loop must keep
    working, and only a verified done may end the mission."""
    brain = JudgingBrain(
        executor_script=[
            '{"action": "done"}',                       # premature
            '{"action": "open_app", "name": "chrome"}',  # real work
            '{"action": "done"}',                       # now verifiable
        ],
        judge_script=[
            '{"done": false, "proof": "no chrome window visible"}',
            '{"done": true, "proof": "chrome window in foreground"}',
        ],
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "oeffne chrome")

    final = chunks[-1]
    assert final.exit_code == 0
    assert "verified" in final.stdout
    assert len(brain.judge_calls) == 2
    # The rejection must be taught back to the model.
    assert any("no chrome window visible" in user
               for _s, user in brain.executor_calls[1:])


async def test_done_accepted_directly_when_verification_disabled() -> None:
    brain = JudgingBrain(
        executor_script=['{"action": "done"}'],
        judge_script=[],
    )
    ctx = make_ctx(brain, verify=False)
    chunks = await run_loop(ctx, "oeffne chrome")

    assert chunks[-1].exit_code == 0
    assert len(brain.judge_calls) == 0


async def test_repeated_done_rejections_fail_cleanly() -> None:
    """A model that insists on a wrong done must not loop forever: after the
    reject budget the mission ends with an explicit failure."""
    brain = JudgingBrain(
        executor_script=['{"action": "done"}'] * 10,
        judge_script=['{"done": false, "proof": "goal not visible"}'] * 10,
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "oeffne chrome")

    final = chunks[-1]
    assert final.is_final
    assert final.exit_code != 0
    assert "not" in final.stderr.lower()  # explicit "not achieved" reason
    assert len(brain.judge_calls) == 3    # reject budget, then clean fail


# ---------------------------------------------------------------------------
# Wave C — plan-first for ALL multi-step goals, not just music playback
#
# The planner existed but was gated on play/search goals only, so
# "open Chrome and navigate to X" ran purely reactive and lost the thread.
# The music-specific SEARCH DISCIPLINE block also leaked into every planned
# turn regardless of the goal.
# ---------------------------------------------------------------------------


class PlanningBrain(FakeBrain):
    """Routes planner / judge / executor calls to separate scripts."""

    def __init__(self, executor_script: list[str],
                 plan_script: list[str] | None = None,
                 judge_script: list[str] | None = None) -> None:
        super().__init__()
        self.executor_script = list(executor_script)
        self.plan_script = list(plan_script or [])
        self.judge_script = list(judge_script or [])
        self.planner_calls: list[tuple[str, str]] = []
        self.judge_calls: list[tuple[str, str]] = []
        self.executor_calls: list[tuple[str, str]] = []

    async def complete_text(self, *, system: str, user: str) -> str:
        self.requests.append((system, user))
        low = system.lower()
        if "planner" in low:
            self.planner_calls.append((system, user))
            return self.plan_script.pop(0) if self.plan_script else '{"plan": []}'
        if "judge" in low:
            self.judge_calls.append((system, user))
            return self.judge_script.pop(0) if self.judge_script else (
                '{"done": true, "proof": "ok"}'
            )
        self.executor_calls.append((system, user))
        return self.executor_script.pop(0) if self.executor_script else (
            '{"action": "fail", "reason": "script exhausted"}'
        )


_CHROME_PLAN = (
    '{"plan": ['
    '{"intent": "open chrome", "success": "a chrome window is open"},'
    '{"intent": "open the settings menu", "success": "the menu is visible"},'
    '{"intent": "click settings", "success": "the settings page is shown"}'
    ']}'
)


async def test_compound_goal_activates_planner() -> None:
    """'open X and do Y' is a multi-step goal and must get an ordered plan,
    exactly like the music goals that were already plan-first."""
    brain = PlanningBrain(
        executor_script=[
            '{"action": "open_app", "name": "chrome"}',
            '{"action": "done"}',
        ],
        plan_script=[_CHROME_PLAN],
        judge_script=['{"done": true, "proof": "settings page visible"}'],
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "oeffne chrome und navigiere zu den einstellungen")

    assert chunks[-1].exit_code == 0
    assert len(brain.planner_calls) == 1


async def test_search_discipline_not_injected_for_non_music_goals() -> None:
    """The Spotify SEARCH DISCIPLINE block must only reach music/search
    goals — a navigation goal gets a clean plan prompt."""
    brain = PlanningBrain(
        executor_script=[
            '{"action": "open_app", "name": "chrome"}',
            '{"action": "done"}',
        ],
        plan_script=[_CHROME_PLAN],
        judge_script=['{"done": true, "proof": "ok"}'],
    )
    ctx = make_ctx(brain, verify=True)
    await run_loop(ctx, "oeffne chrome und navigiere zu den einstellungen")

    assert brain.executor_calls, "executor was never consulted"
    for _system, user in brain.executor_calls:
        assert "FORBIDDEN SHORTCUT" not in user
        assert "play a song" not in user


async def test_plan_step_advances_on_state_change_not_on_wait() -> None:
    """Step tracking must count successful STATE-CHANGING actions. The old
    ' ok '-substring heuristic also counted pure waits, so the >>> marker
    ran ahead of reality."""
    brain = PlanningBrain(
        executor_script=[
            # One real action + one pure wait in a single batch.
            '[{"action": "open_app", "name": "chrome"},'
            ' {"action": "wait", "ms": 1}]',
            '{"action": "done"}',
        ],
        plan_script=[_CHROME_PLAN],
        judge_script=['{"done": true, "proof": "ok"}'],
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "oeffne chrome und navigiere zu den einstellungen")

    assert chunks[-1].exit_code == 0
    assert len(brain.executor_calls) == 2
    _system, user = brain.executor_calls[1]
    # One state change happened -> the CURRENT step is plan step 2.
    assert "2. >>>" in user
    assert "3. >>>" not in user


async def test_done_judge_sees_fresh_screenshot_after_batch_action() -> None:
    """Review finding 2026-06-09: in a batch like [open_app, done] the judge
    used to be handed the screenshot from BEFORE open_app ran, spuriously
    rejecting a completed goal. The done-gate must re-observe when the batch
    already executed a state-changing action."""
    brain = JudgingBrain(
        executor_script=[
            '[{"action": "open_app", "name": "chrome"},'
            ' {"action": "done"}]',
        ],
        judge_script=['{"done": true, "proof": "chrome visible"}'],
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "oeffne chrome")

    assert chunks[-1].exit_code == 0
    # One observe for the step + one fresh observe for the verifier.
    assert ctx.vision_engine.calls == 2


async def test_verify_first_fallback_keeps_search_discipline_for_music() -> None:
    """Review finding 2026-06-09: when the planner fails on a music goal, the
    VERIFY FIRST fallback prompt must still carry the SEARCH DISCIPLINE
    anti-shortcut block."""
    brain = PlanningBrain(
        executor_script=[
            '{"action": "click", "x": 500, "y": 500}',
            '{"action": "fail", "reason": "test ends here"}',
        ],
        plan_script=['{"plan": []}'],  # planner returns nothing usable
    )
    ctx = make_ctx(brain, verify=True)
    await run_loop(ctx, "spiel shape of you")

    assert len(brain.executor_calls) == 2
    _system, user = brain.executor_calls[1]
    assert "VERIFY FIRST" in user
    assert "FORBIDDEN SHORTCUT" in user


def test_calculator_goals_never_pay_the_planner() -> None:
    """Review finding 2026-06-09: arithmetic phrased with 'und' ("rechne 7
    und 3 zusammen") must not trigger the planner round-trip — compute goals
    stay on the cheap stateless path."""
    assert loop_mod._goal_needs_plan("rechne 7 und 3 zusammen") is False
    assert loop_mod._goal_needs_plan("berechne 8 mal 8 und sag es mir") is False
    # Non-compute compound goals still plan.
    assert loop_mod._goal_needs_plan("oeffne chrome und geh zu github") is True


# ---------------------------------------------------------------------------
# Wave D — per-step latency: capped screenshots, no duplicate UIA work,
# screenshot + UIA label enumeration in parallel.
# ---------------------------------------------------------------------------


async def test_observation_image_is_capped_for_the_model(tmp_path) -> None:
    """The loop used to ship the raw full-resolution screenshot (4K on the
    maintainer's box) to the vision model every step. It must be capped to
    the vision-friendly 2048px longest side / JPEG like the router path."""
    import base64
    import io
    import os

    from PIL import Image

    # Noise so the PNG actually exceeds the byte budget like a real
    # 4K desktop screenshot does (a flat color would compress to a no-op).
    big = Image.frombytes("RGB", (2600, 1600), os.urandom(2600 * 1600 * 3))
    png_path = tmp_path / "big.png"
    big.save(png_path, format="PNG")

    obs = Observation(
        trace_id=uuid4(), timestamp_ns=time.time_ns(),
        screenshot_path=str(png_path), screenshot_hash="big",
    )
    block = await loop_mod._load_observation_image(obs)

    assert block is not None
    img = Image.open(io.BytesIO(base64.b64decode(block.data_b64)))
    assert max(img.size) <= 2048
    assert block.mime == "image/jpeg"


async def test_observe_requests_screenshot_mode_not_composite() -> None:
    """The loop never reads observation.nodes — it enumerates clickable
    labels separately. Asking the engine for mode='auto' (composite for most
    apps) paid a full UIA enumeration per step for nothing."""
    seen_modes: list[str] = []

    class ModeRecordingEngine(FakeVisionEngine):
        async def observe(self, *, mode: str = "auto", cancel_token: Any = None,
                          window_title_filter: str | None = None) -> Observation:
            seen_modes.append(mode)
            return await super().observe(
                mode=mode, cancel_token=cancel_token,
                window_title_filter=window_title_filter,
            )

    brain = FakeBrain(script=['{"action": "done"}'])
    ctx = make_ctx(brain)
    ctx.vision_engine = ModeRecordingEngine()
    chunks = await run_loop(ctx, "mach das fenster zu bitte")

    assert chunks[-1].exit_code == 0
    assert seen_modes and all(m == "screenshot" for m in seen_modes)


async def test_screenshot_and_label_enumeration_run_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per step, the screenshot and the UIA label enumeration are independent
    I/O — they must start together instead of back-to-back."""
    import asyncio as aio

    starts: dict[str, float] = {}

    class SlowEngine(FakeVisionEngine):
        async def observe(self, *, mode: str = "auto", cancel_token: Any = None,
                          window_title_filter: str | None = None) -> Observation:
            starts.setdefault("observe", time.monotonic())
            await aio.sleep(0.15)
            return await super().observe(mode=mode, cancel_token=cancel_token)

    async def slow_labels(timeout_s: float, max_n: int = 28) -> list[str]:
        starts.setdefault("labels", time.monotonic())
        await aio.sleep(0.15)
        return []

    monkeypatch.setattr(loop_mod, "_foreground_clickable_labels", slow_labels)
    brain = FakeBrain(script=['{"action": "done"}'])
    ctx = make_ctx(brain)
    ctx.vision_engine = SlowEngine()
    chunks = await run_loop(ctx, "mach das fenster zu bitte")

    assert chunks[-1].exit_code == 0
    assert set(starts) == {"observe", "labels"}
    # Both started within the same step tick — not after each other's sleep.
    assert abs(starts["observe"] - starts["labels"]) < 0.1


def test_ui_tree_source_is_built_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """_foreground_clickable_labels used to construct a fresh UI tree source
    on every step; ``_get_ui_tree_source`` must build once and reuse."""
    import jarvis.vision.tree_factory as tree_factory

    built = {"count": 0}

    class _FakeTreeSource:
        pass

    def fake_factory() -> Any:
        built["count"] += 1
        return _FakeTreeSource()

    monkeypatch.setattr(tree_factory, "make_ui_tree_source", fake_factory)
    monkeypatch.setattr(loop_mod, "_UI_TREE_SOURCE", None, raising=False)

    s1 = loop_mod._get_ui_tree_source()
    s2 = loop_mod._get_ui_tree_source()

    assert built["count"] == 1
    assert s1 is s2


async def test_verify_first_directive_after_state_changing_action() -> None:
    """With verification on, the executor turn AFTER a state-changing action
    must carry an explicit verify-first directive naming that action, so the
    model checks the fresh screenshot before acting again."""
    brain = JudgingBrain(
        executor_script=[
            '{"action": "open_app", "name": "chrome"}',
            '{"action": "done"}',
        ],
        judge_script=['{"done": true, "proof": "chrome visible"}'],
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "oeffne chrome")

    assert chunks[-1].exit_code == 0
    assert len(brain.executor_calls) == 2
    _system, user = brain.executor_calls[1]
    assert "VERIFY FIRST" in user
    assert "open_app" in user
