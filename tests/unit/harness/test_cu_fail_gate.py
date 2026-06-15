"""Fail-gate: symmetric completion enforcement for the Computer-Use loop.

Root cause (2026-06-15 18:58 Snipping-Tool turn, exit 5): the loop's ``done``
action is gated by a strict completion judge (with a reject-and-keep-working
budget), but the ``fail`` action was honored immediately on the model's word.
Quitting was therefore free while succeeding was expensive — a weak/fast model
under friction took the free exit even with the goal nearly achieved (the
Snipping capture overlay was already on screen when it emitted ``fail``).

The fix makes quitting cost what succeeding costs: a ``fail`` must survive a
strict *feasibility* judge that agrees the goal is genuinely impossible/blocked
from the current screen, bounded by ``_MAX_FAIL_REJECTS`` so an honestly
impossible task still terminates.

All tests drive ``_run_screenshot_loop`` directly with fakes — no real
screenshots, no real UIA, no real model calls.

Design spec: docs/superpowers/specs/2026-06-15-cu-fail-gate-completion-enforcement-design.md
"""
from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

import pytest

import jarvis.harness.screenshot_only_loop as loop_mod
from jarvis.core.protocols import HarnessResult, HarnessTask, Observation
from jarvis.harness.computer_use_context import ComputerUseContext

# ---------------------------------------------------------------------------
# Fakes (compact, self-contained mirror of tests/unit/harness/test_cu_loop_robustness.py)
# ---------------------------------------------------------------------------


class FakeVisionEngine:
    """Fresh observation per call (unique hash so the no-progress guard never
    trips), configurable window-title sequence."""

    def __init__(self, window_titles: list[str] | None = None) -> None:
        self.calls = 0
        self._titles = window_titles

    def _guess_active_app_hint(self, window_title_filter: str | None = None) -> str:
        if self._titles is None:
            return ""
        return self._titles[min(self.calls, len(self._titles) - 1)]

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


def _resolve(item: Any) -> str:
    if isinstance(item, Exception):
        raise item
    return item


class FailGateBrain:
    """Routes executor / done-judge / fail-judge calls to separate scripts.

    * fail-judge: recognised by the *feasibility* prompt -> ``fail_judge_script``
    * done-judge: any other 'judge' prompt -> ``done_judge_script``
    * executor:   everything else -> ``executor_script``

    A scripted ``Exception`` is raised (to drive the judge-error path)."""

    def __init__(
        self,
        executor_script: list[Any],
        fail_judge_script: list[Any] | None = None,
        done_judge_script: list[Any] | None = None,
    ) -> None:
        self.executor_script = list(executor_script)
        self.fail_judge_script = list(fail_judge_script or [])
        self.done_judge_script = list(done_judge_script or [])
        self.requests: list[tuple[str, str]] = []
        self.fail_judge_calls: list[tuple[str, str]] = []
        self.done_judge_calls: list[tuple[str, str]] = []
        self.executor_calls: list[tuple[str, str]] = []

    async def complete_text(self, *, system: str, user: str) -> str:
        self.requests.append((system, user))
        low = system.lower()
        if "planner" in low:
            # Defensive: keep a (planner-triggering) goal from misrouting into
            # the executor script. The fail-gate tests use single-verb goals
            # that never plan, but this guard makes the fake robust to that.
            return '{"plan": []}'
        if "feasibility" in low:
            self.fail_judge_calls.append((system, user))
            return _resolve(
                self.fail_judge_script.pop(0) if self.fail_judge_script
                else '{"give_up": false, "reason": ""}'
            )
        if "judge" in low:
            self.done_judge_calls.append((system, user))
            return _resolve(
                self.done_judge_script.pop(0) if self.done_judge_script
                else '{"done": false, "proof": ""}'
            )
        self.executor_calls.append((system, user))
        return _resolve(
            self.executor_script.pop(0) if self.executor_script
            else '{"action": "fail", "reason": "script exhausted"}'
        )


def make_ctx(brain: FailGateBrain, *, verify: bool = True,
             titles: list[str] | None = None,
             step_budget: int = 12) -> ComputerUseContext:
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
        announce_progress=False,
    )


async def run_loop(ctx: ComputerUseContext, goal: str) -> list[HarnessResult]:
    task = HarnessTask(prompt=goal, timeout_s=30)
    chunks: list[HarnessResult] = []
    async for chunk in _run_screenshot_loop(task, ctx):  # noqa: F821 (imported below)
        chunks.append(chunk)
    return chunks


from jarvis.harness.screenshot_only_loop import _run_screenshot_loop  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_host(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_labels(timeout_s: float, max_n: int = 28) -> list[str]:
        return []

    monkeypatch.setattr(loop_mod, "_foreground_clickable_labels", _no_labels)
    monkeypatch.setattr(
        loop_mod, "_capture_monitor_geometry", lambda: (0, 0, 1920, 1080),
    )
    monkeypatch.setattr(loop_mod, "_UI_TREE_SOURCE", None, raising=False)
    monkeypatch.setattr(loop_mod, "_OPEN_APP_SETTLE_TIMEOUT_S", 0.0)


# ---------------------------------------------------------------------------
# The fail-gate
# ---------------------------------------------------------------------------


async def test_fail_on_achievable_goal_is_rejected_and_loop_continues() -> None:
    """A voluntary ``fail`` on a still-achievable goal must NOT end the mission.
    The feasibility judge rejects it, the loop is told to keep working, and only
    a verified ``done`` ends the mission. This is the recorded bug: the model
    gave up while the task was still doable."""
    brain = FailGateBrain(
        executor_script=[
            '{"action": "fail", "reason": "this is too hard"}',     # premature give-up
            '{"action": "open_app", "name": "snippingtool"}',       # real work after push-back
            '{"action": "done"}',                                   # now verifiable
        ],
        fail_judge_script=[
            '{"give_up": false, "reason": "the snipping tool button is visible"}',
        ],
        done_judge_script=[
            '{"done": true, "proof": "snipping overlay visible"}',
        ],
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "open the snipping tool")

    final = chunks[-1]
    assert final.exit_code == 0, "mission must end via verified done, not the rejected fail"
    assert len(brain.fail_judge_calls) == 1
    # The rejection must be taught back to the model so it keeps working.
    assert any("FAIL REJECTED" in user for _s, user in brain.executor_calls[1:])


async def test_fail_honored_after_max_rejects_backstop() -> None:
    """A model that insists on quitting must not loop forever: after the reject
    budget the fail is honored (verified-impossible-after-N-tries backstop)."""
    brain = FailGateBrain(
        executor_script=['{"action": "fail", "reason": "cannot do it"}'] * 10,
        fail_judge_script=['{"give_up": false, "reason": "still looks doable"}'] * 10,
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "open the snipping tool")

    final = chunks[-1]
    assert final.is_final
    assert final.exit_code == loop_mod._FAIL_EXIT_CODE
    # Bounded: exactly _MAX_FAIL_REJECTS judge calls, then honored.
    assert len(brain.fail_judge_calls) == loop_mod._MAX_FAIL_REJECTS


async def test_fail_on_impossible_goal_is_honored_immediately() -> None:
    """When the feasibility judge AGREES the goal is genuinely impossible from
    here, the fail is honored at once (no needless grinding) and the judge's
    verified reason is surfaced, not the model's unverified claim."""
    brain = FailGateBrain(
        executor_script=['{"action": "fail", "reason": "vague claim"}'],
        fail_judge_script=[
            '{"give_up": true, "reason": "a hard permission wall blocks the task"}',
        ],
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "open the snipping tool")

    final = chunks[-1]
    assert final.is_final
    assert final.exit_code == loop_mod._FAIL_EXIT_CODE
    assert len(brain.fail_judge_calls) == 1  # honored on the first judge, no re-plan
    assert "permission wall" in (final.stderr or "")


async def test_fail_judge_error_defaults_to_keep_working() -> None:
    """ANTI-REWARD-HACK GUARD: a feasibility-judge error/timeout must default to
    KEEP WORKING, never to a free quit. A broken judge cannot become the cheap
    escape hatch the whole fix exists to remove."""
    brain = FailGateBrain(
        executor_script=[
            '{"action": "fail", "reason": "giving up"}',
            '{"action": "open_app", "name": "snippingtool"}',
            '{"action": "done"}',
        ],
        fail_judge_script=[RuntimeError("judge provider down")],
        done_judge_script=['{"done": true, "proof": "overlay visible"}'],
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "open the snipping tool")

    final = chunks[-1]
    assert final.exit_code == 0, "judge error must not grant a free quit"
    assert len(brain.fail_judge_calls) == 1


async def test_fail_honored_at_face_value_when_verify_disabled() -> None:
    """Backward-compat / escape hatch: with verification off (same flag as the
    done-gate), a ``fail`` is honored at face value and the judge is never
    consulted."""
    brain = FailGateBrain(
        executor_script=['{"action": "fail", "reason": "nope"}'],
        fail_judge_script=['{"give_up": false, "reason": "must not be called"}'],
    )
    ctx = make_ctx(brain, verify=False)
    chunks = await run_loop(ctx, "open the snipping tool")

    final = chunks[-1]
    assert final.exit_code == loop_mod._FAIL_EXIT_CODE
    assert len(brain.fail_judge_calls) == 0


async def test_happy_path_done_never_invokes_fail_judge() -> None:
    """LATENCY GUARD: the fail-judge fires ONLY when the model emits ``fail``.
    A successful task never emits ``fail`` -> zero added latency on the happy
    path (and on every normal action step)."""
    brain = FailGateBrain(
        executor_script=[
            '{"action": "open_app", "name": "chrome"}',
            '{"action": "done"}',
        ],
        done_judge_script=['{"done": true, "proof": "chrome window visible"}'],
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "oeffne chrome")

    final = chunks[-1]
    assert final.exit_code == 0
    assert len(brain.fail_judge_calls) == 0


async def test_fail_re_observes_fresh_screen_after_batch_state_change() -> None:
    """When ``fail`` arrives in a batch AFTER a state-changing action
    ([open_app, fail]), the feasibility judge must see a FRESH screenshot, not
    the stale pre-batch frame — mirror of the done-gate's re-observe branch."""
    brain = FailGateBrain(
        executor_script=[
            '[{"action": "open_app", "name": "snippingtool"}, '
            '{"action": "fail", "reason": "cannot"}]',
        ],
        fail_judge_script=['{"give_up": true, "reason": "a blocking error dialog"}'],
    )
    ctx = make_ctx(brain, verify=True)
    chunks = await run_loop(ctx, "open the snipping tool")

    final = chunks[-1]
    assert final.exit_code == loop_mod._FAIL_EXIT_CODE
    assert len(brain.fail_judge_calls) == 1
    # step observe (1) + fresh re-observe for the fail-judge (1) = 2 observes.
    assert ctx.vision_engine.calls == 2
