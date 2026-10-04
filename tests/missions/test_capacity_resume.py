"""Automatic resume of a WAITING_CAPACITY mission from its checkpoint.

The user's rules (2026-10-04): resume once the SAME subscription has capacity
again, continue exactly from the checkpoint, never redo an approved step,
never switch to another provider or a paid key without approval.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from jarvis.missions.capacity import (
    CHECKPOINT_NAME,
    WorkerCapacityUnavailable,
    capacity_resume_loop,
    read_checkpoint,
    worker_family,
)
from jarvis.missions.events import MissionFailed, MissionWaitingCapacity
from jarvis.missions.kontrollierer.decomposer import MissionPlan, Step
from jarvis.missions.manager import MissionManager
from jarvis.missions.state_machine import MissionState
from jarvis.missions.workers.api_agent_worker import ApiAgentWorker
from jarvis.missions.workers.claude_direct_worker import ClaudeDirectWorker
from jarvis.missions.workers.codex_direct_worker import CodexDirectWorker
from tests.fakes.fake_mission_runtime import FakeMissionWorker, make_kontrollierer


@pytest.fixture
async def manager(tmp_missions_db: Path):
    m = MissionManager(tmp_missions_db)
    await m.start()
    yield m
    await m.stop()


class SwitchableFactory:
    """Worker factory whose capacity the test turns on and off.

    While ``capacity`` is False every call parks (as the real factory does
    during a quota cooldown). Records which step each worker was built for.
    """

    def __init__(self, *, capacity: bool = True, family: str = "claude") -> None:
        self.capacity = capacity
        self.family = family
        self.built_for: list[str] = []
        self.workers: list[FakeMissionWorker] = []
        self.writes: dict[str, dict[str, str]] = {}

    def __call__(self, step: Step) -> FakeMissionWorker:
        if not self.capacity:
            raise WorkerCapacityUnavailable("provider_quota", "claude")
        self.built_for.append(step.task_id)
        worker = FakeMissionWorker(family=self.family, writes=self.writes.get(step.task_id))
        self.workers.append(worker)
        return worker


def _mission_dir(tmp_path: Path, mission_id: str) -> Path:
    return tmp_path / "missions" / f"mission_{mission_id[:13]}"


def _waits(payloads: list[Any]) -> list[MissionWaitingCapacity]:
    return [p for p in payloads if isinstance(p, MissionWaitingCapacity)]


async def _payloads(manager: MissionManager, mission_id: str) -> list[Any]:
    return [e.payload for e in await manager.store.events_for_mission(mission_id)]


async def _state(manager: MissionManager, mission_id: str) -> MissionState:
    view = await manager.mission(mission_id)
    assert view is not None
    return view.state


async def _park_second_step(
    manager: MissionManager, tmp_path: Path, factory: SwitchableFactory
) -> tuple[Any, str, Step, Step]:
    """Two-step mission: step one is approved, step two hits the spent window."""
    first, second = Step(slug="first", prompt="one"), Step(slug="second", prompt="two")
    plan = MissionPlan(steps=[first, second], n_workers=1)

    def _first_ok_then_parked(step: Step) -> FakeMissionWorker:
        if step.task_id == second.task_id:
            raise WorkerCapacityUnavailable("provider_quota", "claude")
        return factory(step)

    k = make_kontrollierer(manager, tmp_path, plan, _first_ok_then_parked)
    mission_id = await manager.dispatch(prompt="two-part job")
    assert await k.run_mission(mission_id) == MissionState.WAITING_CAPACITY
    # From now on the test's factory decides capacity for every step.
    k._worker_factory = factory
    return k, mission_id, first, second


# --- Pause and checkpoint -----------------------------------------------------


async def test_checkpoint_holds_everything_a_resume_needs(
    manager: MissionManager, tmp_path: Path
) -> None:
    factory = SwitchableFactory()
    _k, mission_id, first, second = await _park_second_step(manager, tmp_path, factory)

    checkpoint = read_checkpoint(_mission_dir(tmp_path, mission_id))
    assert checkpoint is not None
    assert checkpoint["provider"] == "claude"
    assert checkpoint["resume_attempts"] == 0
    plan = MissionPlan.model_validate(checkpoint["plan"])
    assert [s.task_id for s in plan.steps] == [first.task_id, second.task_id]
    assert {s["task_id"]: s["done"] for s in checkpoint["steps"]} == {
        first.task_id: True,
        second.task_id: False,
    }


# --- Resume: exactly from the checkpoint, nothing twice -----------------------


async def test_resume_runs_only_the_open_step_and_approves(
    manager: MissionManager, tmp_path: Path
) -> None:
    factory = SwitchableFactory()
    k, mission_id, first, second = await _park_second_step(manager, tmp_path, factory)
    assert factory.built_for == [first.task_id]

    assert await k.resume_mission(mission_id) == MissionState.APPROVED

    # Step one ran once in the first run and never again.
    assert factory.built_for == [first.task_id, second.task_id]
    assert await _state(manager, mission_id) == MissionState.APPROVED
    # The plan came from the checkpoint, not from a second decomposition.
    assert k._decomposer.calls == 1  # type: ignore[attr-defined]


async def test_resume_restores_partial_work_and_tells_the_worker(
    manager: MissionManager, tmp_path: Path
) -> None:
    factory = SwitchableFactory()
    k, mission_id, _first, second = await _park_second_step(manager, tmp_path, factory)
    # The parked step's earlier attempt left a deliverable in its archive.
    task_dir = _mission_dir(tmp_path, mission_id) / "tasks" / second.task_id[:13]
    files = task_dir / "artifacts" / "files"
    files.mkdir(parents=True, exist_ok=True)
    (files / "draft.md").write_text("half done", encoding="utf-8")

    seen: dict[str, str] = {}

    def _record(_prompt: str, worktree: Path) -> None:
        draft = worktree / "draft.md"
        seen["draft"] = draft.read_text(encoding="utf-8") if draft.is_file() else ""

    def _factory(step: Step) -> FakeMissionWorker:
        worker = factory(step)
        worker.on_spawn = _record
        return worker

    k._worker_factory = _factory
    assert await k.resume_mission(mission_id) == MissionState.APPROVED

    assert seen["draft"] == "half done"
    assert "RESUMED TASK" in factory.workers[-1].prompts[0]


async def test_parked_mission_is_never_rerun_by_run_mission(
    manager: MissionManager, tmp_path: Path
) -> None:
    factory = SwitchableFactory()
    k, mission_id, first, _second = await _park_second_step(manager, tmp_path, factory)

    assert await k.run_mission(mission_id) == MissionState.WAITING_CAPACITY
    assert factory.built_for == [first.task_id]
    assert k._decomposer.calls == 1  # type: ignore[attr-defined]


async def test_concurrent_resumes_run_the_mission_once(
    manager: MissionManager, tmp_path: Path
) -> None:
    factory = SwitchableFactory()
    k, mission_id, first, second = await _park_second_step(manager, tmp_path, factory)

    results = await asyncio.gather(k.resume_mission(mission_id), k.resume_mission(mission_id))

    assert MissionState.APPROVED in results
    assert factory.built_for.count(second.task_id) == 1
    assert factory.built_for.count(first.task_id) == 1


async def test_resume_that_hits_the_limit_again_reparks_quietly(
    manager: MissionManager, tmp_path: Path
) -> None:
    factory = SwitchableFactory()
    k, mission_id, _first, _second = await _park_second_step(manager, tmp_path, factory)
    factory.capacity = False  # the window is still spent when the resume starts

    assert await k.resume_mission(mission_id) == MissionState.WAITING_CAPACITY

    waits = _waits(await _payloads(manager, mission_id))
    assert [(w.resume_attempt, w.repeat) for w in waits] == [(0, False), (1, True)]
    assert (waits[-1].steps_done, waits[-1].steps_total) == (1, 2)
    checkpoint = read_checkpoint(_mission_dir(tmp_path, mission_id))
    assert checkpoint is not None and checkpoint["resume_attempts"] == 1
    assert [s["done"] for s in checkpoint["steps"]] == [True, False]


async def test_resume_without_checkpoint_fails_honestly(
    manager: MissionManager, tmp_path: Path
) -> None:
    factory = SwitchableFactory()
    k, mission_id, _first, _second = await _park_second_step(manager, tmp_path, factory)
    (_mission_dir(tmp_path, mission_id) / CHECKPOINT_NAME).unlink()

    assert await k.resume_mission(mission_id) == MissionState.FAILED
    failed = [p for p in await _payloads(manager, mission_id) if isinstance(p, MissionFailed)]
    assert failed[-1].reason == "checkpoint_missing"


# --- When to resume: same subscription only -----------------------------------


async def test_resume_pass_waits_until_capacity_is_back(
    manager: MissionManager, tmp_path: Path
) -> None:
    factory = SwitchableFactory()
    k, mission_id, _first, _second = await _park_second_step(manager, tmp_path, factory)
    factory.capacity = False

    assert await k.resume_waiting_missions() == []
    assert await _state(manager, mission_id) == MissionState.WAITING_CAPACITY

    factory.capacity = True
    assert await k.resume_waiting_missions() == [mission_id]
    assert await _state(manager, mission_id) == MissionState.APPROVED


async def test_resume_pass_never_switches_provider(
    manager: MissionManager, tmp_path: Path
) -> None:
    """Capacity on another family (e.g. the paid Anthropic key) is not a
    reason to resume — that switch needs the user's approval."""
    factory = SwitchableFactory(family="claude-api")
    k, mission_id, first, _second = await _park_second_step(manager, tmp_path, factory)
    factory.built_for.clear()

    assert await k.resume_waiting_missions() == []
    assert await _state(manager, mission_id) == MissionState.WAITING_CAPACITY
    assert not any(w.prompts for w in factory.workers[1:])


async def test_resumed_run_parks_again_if_the_worker_family_changes(
    manager: MissionManager, tmp_path: Path
) -> None:
    factory = SwitchableFactory(family="codex")
    k, mission_id, _first, _second = await _park_second_step(manager, tmp_path, factory)

    assert await k.resume_mission(mission_id) == MissionState.WAITING_CAPACITY
    wait = _waits(await _payloads(manager, mission_id))[-1]
    assert (wait.reason, wait.provider) == ("provider_unavailable", "claude")
    assert not any(w.prompts for w in factory.workers[1:])


# --- Restoring work into a git workspace --------------------------------------


def _offline_kontrollierer(tmp_path: Path) -> Any:
    """A Kontrollierer for calling workspace helpers; its store is never opened."""
    plan = MissionPlan(steps=[Step(slug="s", prompt="p")], n_workers=1)
    return make_kontrollierer(
        MissionManager(tmp_path / "unused.db"), tmp_path, plan, SwitchableFactory()
    )


def _git(*args: str, cwd: Path) -> str:
    identity = ["-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    return subprocess.run(  # noqa: S603
        ["git", *identity, *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_restore_applies_the_archived_patch(tmp_path: Path) -> None:
    git = _git
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-q", cwd=repo)
    (repo / "notes.txt").write_text("line one\n", encoding="utf-8")
    git("add", "notes.txt", cwd=repo)
    git("commit", "-q", "-m", "notes", cwd=repo)

    # The earlier attempt edited a tracked file and added a new one.
    (repo / "notes.txt").write_text("line one\nline two\n", encoding="utf-8")
    (repo / "new.txt").write_text("fresh\n", encoding="utf-8")
    git("add", "-A", ".", cwd=repo)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    patch = git("diff", "--cached", "HEAD", cwd=repo)
    (artifacts / "diff.patch").write_text(patch, encoding="utf-8")
    git("reset", "-q", "--hard", "HEAD", cwd=repo)
    assert not (repo / "new.txt").exists()

    assert _offline_kontrollierer(tmp_path)._restore_task_workspace(repo, artifacts) is True
    assert (repo / "notes.txt").read_text(encoding="utf-8") == "line one\nline two\n"
    assert (repo / "new.txt").read_text(encoding="utf-8") == "fresh\n"


def test_restore_with_nothing_archived_is_a_clean_start(tmp_path: Path) -> None:
    k = _offline_kontrollierer(tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (tmp_path / "artifacts").mkdir()
    assert k._restore_task_workspace(workspace, tmp_path / "artifacts") is False
    assert list(workspace.iterdir()) == []


# --- The timer -----------------------------------------------------------------


async def test_resume_loop_keeps_running_after_a_failing_pass() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    async def _pass() -> list[str]:
        calls.append(len(calls))
        if len(calls) == 1:
            raise RuntimeError("store hiccup")
        return []

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) > 3:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await capacity_resume_loop(
            _pass, interval_s=300.0, jitter_s=60.0, sleep=_sleep, jitter=lambda _a, _b: 7.0
        )
    assert len(calls) == 3
    assert sleeps[0] == 307.0  # waits before the first pass: not on the boot path


# --- Worker identity --------------------------------------------------------------


def test_worker_families_tell_billing_apart() -> None:
    assert worker_family(ClaudeDirectWorker()) == "claude"
    assert worker_family(CodexDirectWorker()) == "codex"
    # The Anthropic API key is a different family from the Claude subscription.
    assert worker_family(ApiAgentWorker("claude-api")) == "claude-api"


def test_checkpoint_is_plain_json(tmp_path: Path) -> None:
    from jarvis.missions.capacity import write_checkpoint

    path = write_checkpoint(tmp_path, {"mission_id": "m", "steps": []})
    assert json.loads(path.read_text(encoding="utf-8"))["mission_id"] == "m"
