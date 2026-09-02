"""BUG-212 — a ``brain_prompt`` step is an isolated agent turn.

The step used to call ``BrainManager.__call__`` → ``generate()``: the LIVE
voice conversation's history and the full tool surface. A 07:30 routine then
answered inside whatever the user last talked about and had no allowlist.
With a brain that exposes ``run_task`` the step now runs that — empty
history, the step's own tools, its model tier. Brains without ``run_task``
(fakes, minimal providers) keep the callable path.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.bus import EventBus
from jarvis.workflows.runner import WorkflowRunner
from jarvis.workflows.schema import BrainPromptStep, ManualTrigger, WorkflowDef
from jarvis.workflows.store import WorkflowStore


@pytest.fixture
async def store(tmp_path: Path) -> WorkflowStore:
    s = WorkflowStore(tmp_path / "wf.sqlite")
    await s.init()
    yield s
    await s.close()


class _ManagerLikeBrain:
    """Both entry points, so the test can see which one the runner picked."""

    def __init__(self) -> None:
        self.task_calls: list[dict[str, Any]] = []
        self.chat_calls: list[str] = []

    async def run_task(self, *, prompt: str, allowed_tools: tuple[str, ...] = (),
                       model_tier: str = "auto", trace_id: Any = None) -> str:
        self.task_calls.append({
            "prompt": prompt, "allowed_tools": allowed_tools, "model_tier": model_tier,
        })
        return "isolated reply"

    async def __call__(self, prompt: str) -> str:
        self.chat_calls.append(prompt)
        return "chat reply"


class _CallableOnlyBrain:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        return "plain reply"


async def _run(store: WorkflowStore, runner: WorkflowRunner, wf: WorkflowDef) -> dict[str, Any]:
    wid = await store.upsert_workflow(wf)
    run_id = await runner.trigger(wid)
    for _ in range(100):
        await asyncio.sleep(0.01)
        run = await store.get_run(run_id)
        if run and run["state"] in ("completed", "failed"):
            return run
    pytest.fail("run did not finish")


async def test_the_step_runs_as_an_isolated_task_turn_with_its_tools(
    store: WorkflowStore,
) -> None:
    brain = _ManagerLikeBrain()
    runner = WorkflowRunner(store=store, bus=EventBus(), brain=brain)
    wf = WorkflowDef(
        name="Morning Briefing",
        trigger=ManualTrigger(),
        steps=(
            BrainPromptStep(
                prompt="brief me",
                tools=("search_web", "gmail"),
                model_tier="deep",
            ),
        ),
    )

    run = await _run(store, runner, wf)

    assert run["state"] == "completed"
    assert run["steps"][0]["output"] == "isolated reply"
    assert brain.chat_calls == [], "never the live conversation path"
    assert brain.task_calls == [{
        "prompt": "brief me",
        "allowed_tools": ("search_web", "gmail"),
        "model_tier": "deep",
    }]


async def test_a_step_without_tools_still_runs_isolated(store: WorkflowStore) -> None:
    brain = _ManagerLikeBrain()
    runner = WorkflowRunner(store=store, bus=EventBus(), brain=brain)
    wf = WorkflowDef(
        name="URL Summary",
        trigger=ManualTrigger(),
        steps=(BrainPromptStep(prompt="summarise"),),
    )

    run = await _run(store, runner, wf)

    assert run["state"] == "completed"
    assert brain.task_calls[0]["allowed_tools"] == ()
    assert brain.task_calls[0]["model_tier"] == "auto"
    assert brain.chat_calls == []


async def test_a_brain_without_run_task_keeps_the_callable_path(
    store: WorkflowStore,
) -> None:
    brain = _CallableOnlyBrain()
    runner = WorkflowRunner(store=store, bus=EventBus(), brain=brain)
    wf = WorkflowDef(
        name="Plain",
        trigger=ManualTrigger(),
        steps=(BrainPromptStep(prompt="hello"),),
    )

    run = await _run(store, runner, wf)

    assert run["state"] == "completed"
    assert run["steps"][0]["output"] == "plain reply"
    assert brain.calls == ["hello"]


def test_old_step_json_without_the_new_fields_still_loads() -> None:
    """Rows written before the fields existed carry neither ``tools`` nor
    ``model_tier``; ``extra="forbid"`` must not reject them."""
    step = BrainPromptStep.model_validate(
        {"kind": "brain_prompt", "prompt": "x", "max_output_chars": 500}
    )
    assert step.tools == ()
    assert step.model_tier == "auto"
