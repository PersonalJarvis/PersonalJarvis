"""Deterministic screenshot fast path: don't drive a GUI loop for a one-shot capture.

Root cause (live 2026-06-16 15:14 voice turn, 17 steps / 38 s): a bare
"take a screenshot" request is routed to the screenshot Computer-Use harness,
which then asks a weak fast-tier vision model to OPERATE the Snipping Tool. The
model fought the UI for 17 steps and clicked "Neuer Screenshot" three times
(steps 3, 10, 16) — each click DISCARDS the previous capture, which is exactly
the "deleted three screenshots" the user saw — before it finally emitted a
verified ``done``. No files were ever deleted; the over-execution is the bug.

Why it happens: the loop never asks "is the goal already satisfied?" — it only
stops when the MODEL volunteers ``done``. A one-shot capture has no business in
a GUI-exploration loop at all.

The fix: a goal that is ONLY "take a screenshot" (DE+EN) is satisfied
deterministically — capture the active monitor, save it to disk for the user,
and end in a SINGLE step without ever observing the screen or calling the model.
Any capture failure (headless VPS, missing desktop extras) returns None and the
loop falls through to its existing behaviour unchanged.

All tests drive ``_run_screenshot_loop`` directly with fakes — no real
screenshots, no real UIA, no real model calls. The capture itself is patched so
the behavioural tests never need a real display.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

import jarvis.harness.screenshot_only_loop as loop_mod
from jarvis.core.protocols import HarnessResult, HarnessTask, Observation
from jarvis.harness.computer_use_context import ComputerUseContext

# ---------------------------------------------------------------------------
# Fakes (compact mirror of tests/unit/harness/test_cu_fail_gate.py)
# ---------------------------------------------------------------------------


class FakeVisionEngine:
    def __init__(self) -> None:
        self.calls = 0

    def _guess_active_app_hint(self, window_title_filter: str | None = None) -> str:
        return ""

    async def observe(self, *, mode: str = "auto", cancel_token: Any = None,
                      window_title_filter: str | None = None) -> Observation:
        idx = self.calls
        self.calls += 1
        return Observation(
            trace_id=uuid4(),
            timestamp_ns=time.time_ns(),
            screenshot_path=None,
            screenshot_hash=f"hash-{idx}",
            nodes=(),
            window_title="",
            active_pid=0,
            source="screenshot_only",
            pruning_stats={},
        )


class FakeToolResult:
    def __init__(self) -> None:
        self.success = True
        self.output = "ok"
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


class ScriptBrain:
    """Routes planner / judge / executor calls. Records executor calls so a test
    can prove the GUI loop was (not) entered."""

    def __init__(self, executor_script: list[str] | None = None,
                 done_judge_script: list[str] | None = None) -> None:
        self.executor_script = list(executor_script or [])
        self.done_judge_script = list(done_judge_script or [])
        self.executor_calls: list[tuple[str, str]] = []

    async def complete_text(self, *, system: str, user: str) -> str:
        low = system.lower()
        if "planner" in low:
            return '{"plan": []}'
        if "judge" in low or "feasibility" in low:
            return (self.done_judge_script.pop(0) if self.done_judge_script
                    else '{"done": true, "proof": "ok"}')
        self.executor_calls.append((system, user))
        return (self.executor_script.pop(0) if self.executor_script
                else '{"action": "done"}')


def make_ctx(brain: ScriptBrain) -> ComputerUseContext:
    tools = {
        name: FakeTool(name)
        for name in ("open_app", "click", "type_text", "hotkey",
                     "click_element", "scroll")
    }
    return ComputerUseContext(
        vision_engine=FakeVisionEngine(),
        brain_manager=brain,
        tool_executor=FakeExecutor(),
        tools=tools,
        bus=None,
        step_budget=12,
        per_step_timeout_s=5.0,
        verify_after_each_step=True,
        announce_progress=False,
    )


async def run_loop(ctx: ComputerUseContext, goal: str) -> list[HarnessResult]:
    task = HarnessTask(prompt=goal, timeout_s=30)
    chunks: list[HarnessResult] = []
    async for chunk in _run_screenshot_loop(task, ctx):
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
# The classifier: only a bare "take a screenshot" goal qualifies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("goal", [
    "I do a screenshot from my screen right now.",   # the exact recorded turn
    "mach einen Screenshot",
    "Mach mal eben einen Screenshot",
    "nimm einen Screenshot auf",
    "knips einen Screenshot",
    "Screenshot machen",
    "take a screenshot",
    "do a screenshot",
    "kannst du einen Screenshot machen",
    "mach einen Screenshot von meinem Bildschirm",
])
def test_is_pure_screenshot_goal_accepts_bare_capture(goal: str) -> None:
    assert loop_mod._is_pure_screenshot_goal(goal) is True


@pytest.mark.parametrize("goal", [
    "schick mir den letzten Screenshot",             # send a previous one
    "zeig den letzten Screenshot",                   # show a previous one
    "ich habe einen Screenshot gemacht",             # informational / past
    "mach einen Screenshot und schick ihn an Mama",  # compound: capture + send
    "take a screenshot and open chrome",             # compound: capture + action
    "oeffne chrome",                                 # not a screenshot goal at all
    "spiel ein Lied ab",                             # unrelated
    "",                                              # empty
])
def test_is_pure_screenshot_goal_rejects_non_bare_capture(goal: str) -> None:
    assert loop_mod._is_pure_screenshot_goal(goal) is False


# ---------------------------------------------------------------------------
# The fast path: one deterministic step, no GUI loop, no model calls
# ---------------------------------------------------------------------------


async def test_bare_screenshot_is_satisfied_in_one_step_without_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE FIX: a bare screenshot goal captures+saves deterministically and ends
    with exit 0 WITHOUT observing the screen or calling the model — so it can
    never flail through the Snipping Tool or re-click "Neuer Screenshot"."""
    saved = Path("captured-Screenshot_2026-06-16_15-14-00.png")
    monkeypatch.setattr(loop_mod, "_save_user_screenshot", lambda: saved,
                        raising=False)

    brain = ScriptBrain()  # the loop must never reach the brain
    ctx = make_ctx(brain)
    chunks = await run_loop(ctx, "I do a screenshot from my screen right now.")

    final = chunks[-1]
    assert final.is_final
    assert final.exit_code == 0
    assert str(saved) in (final.stdout or "")
    # The whole point: zero GUI work — no observation, no model dispatch.
    assert ctx.vision_engine.calls == 0
    assert brain.executor_calls == []


async def test_capture_failure_falls_through_to_the_normal_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Graceful degrade (cloud-first): when the capture is unavailable (headless
    VPS / no mss / no PIL → None), the fast path stands down and the existing
    interactive loop runs unchanged — no crash, no regression."""
    monkeypatch.setattr(loop_mod, "_save_user_screenshot", lambda: None,
                        raising=False)

    brain = ScriptBrain(
        executor_script=['{"action": "done"}'],
        done_judge_script=['{"done": true, "proof": "screenshot visible"}'],
    )
    ctx = make_ctx(brain)
    chunks = await run_loop(ctx, "mach einen Screenshot")

    final = chunks[-1]
    assert final.exit_code == 0
    # Fell through to the real loop: the screen WAS observed and the model WAS
    # asked (the opposite of the fast-path test above).
    assert ctx.vision_engine.calls >= 1
    assert len(brain.executor_calls) >= 1


async def test_non_screenshot_goal_never_triggers_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fast path must not fire for an unrelated goal: the capture helper is
    never invoked and the normal loop handles the task."""
    called = {"n": 0}

    def _capture() -> Path | None:
        called["n"] += 1
        return Path("should-not-happen.png")

    monkeypatch.setattr(loop_mod, "_save_user_screenshot", _capture,
                        raising=False)

    brain = ScriptBrain(
        executor_script=['{"action": "open_app", "name": "chrome"}',
                         '{"action": "done"}'],
        done_judge_script=['{"done": true, "proof": "chrome window visible"}'],
    )
    ctx = make_ctx(brain)
    chunks = await run_loop(ctx, "oeffne chrome")

    assert chunks[-1].exit_code == 0
    assert called["n"] == 0
    assert len(brain.executor_calls) >= 1


async def test_compound_screenshot_goal_uses_the_loop_not_the_fast_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A compound "screenshot AND send it" still needs the loop (the second half
    is real work), so the deterministic capture must NOT short-circuit it."""
    called = {"n": 0}

    def _capture() -> Path | None:
        called["n"] += 1
        return Path("should-not-happen.png")

    monkeypatch.setattr(loop_mod, "_save_user_screenshot", _capture,
                        raising=False)

    brain = ScriptBrain(
        executor_script=['{"action": "done"}'],
        done_judge_script=['{"done": true, "proof": "done"}'],
    )
    ctx = make_ctx(brain)
    await run_loop(ctx, "mach einen Screenshot und schick ihn an Mama")

    assert called["n"] == 0
    assert ctx.vision_engine.calls >= 1
