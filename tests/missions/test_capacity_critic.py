"""A critic without capacity parks the mission; it never fails delivered work,
re-runs a worker, or grades on another family or a paid key (2026-10-06).

- The pinned critic stays on its subscription: no codex/claude cross-over,
  no per-token API critic — a worker's paid approval never covers critic calls.
- A critic that says "no capacity" parks the mission with the review pending.
- A resume runs ONLY the missing review on the saved worker result.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.claude_quota_state import claude_in_quota_cooldown, clear_claude_quota_cooldown
from jarvis.missions.capacity import (
    PAID_MISSION_CAP_USD,
    CriticCapacityUnavailable,
    PaidOffer,
    WorkerCapacityUnavailable,
    read_checkpoint,
)
from jarvis.missions.critic.runner import CriticRunner
from jarvis.missions.critic.verdict import REQUIRED_AXES, CriticAxis, CriticVerdict
from jarvis.missions.events import MissionFailed, MissionPaidUsage, MissionWaitingCapacity
from jarvis.missions.kontrollierer.decomposer import MissionPlan, Step
from jarvis.missions.kontrollierer.orchestrator import Kontrollierer
from jarvis.missions.manager import MissionManager
from jarvis.missions.state_machine import MissionState
from tests.fakes.fake_mission_runtime import (
    FakeMissionWorker,
    FakePaidOption,
    make_kontrollierer,
)
from tests.missions.critic.test_runner_claude_direct import _patch_direct

SESSION_LIMIT = "You've hit your session limit · resets 11:10pm"


@pytest.fixture(autouse=True)
def _fresh_cooldowns():
    clear_claude_quota_cooldown()
    yield
    clear_claude_quota_cooldown()


def _pin(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Claude-subscription install: the claude CLI is present."""
    monkeypatch.setattr(
        "jarvis.missions.workers.claude_direct_worker._resolve_claude_binary",
        lambda: "/usr/local/bin/claude",
    )


def _no_paid_critic(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _forbidden(self: Any, **_k: Any) -> None:
        raise AssertionError("a pinned critic must never grade on a paid API key")

    monkeypatch.setattr(CriticRunner, "_invoke_via_api_critic", _forbidden)
    monkeypatch.setattr(
        "jarvis.missions.critic.runner._resolve_api_critic_provider",
        lambda *a, **k: ("openrouter", "some/model"),
    )


async def _review(tmp_path: Path) -> CriticVerdict:
    return await CriticRunner().run(
        mission_prompt="Build X",
        worker_diff="diff --git a/x b/x\n+x\n",
        worker_log="log",
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={},
    )


# --- The critic runner ------------------------------------------------------------


async def test_spent_window_on_the_critic_raises_capacity_not_a_paid_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_direct(monkeypatch, stdout=SESSION_LIMIT, returncode=1)
    _pin(monkeypatch)
    _no_paid_critic(monkeypatch)

    with pytest.raises(CriticCapacityUnavailable) as exc:
        await _review(tmp_path)

    assert (exc.value.reason, exc.value.provider) == ("provider_quota", "claude")
    # The resume waits for the window instead of retrying at once.
    assert claude_in_quota_cooldown()


async def test_dead_claude_login_parks_instead_of_crossing_families(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_direct(monkeypatch, stdout="")
    _pin(monkeypatch)
    _no_paid_critic(monkeypatch)
    monkeypatch.setattr("jarvis.missions.critic.runner._claude_cli_critic_viable", lambda: False)

    with pytest.raises(CriticCapacityUnavailable) as exc:
        await _review(tmp_path)
    assert (exc.value.reason, exc.value.provider) == ("provider_auth", "claude")


async def test_codex_critic_never_falls_back_to_claude(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "jarvis.missions.critic.runner._resolve_critic_provider_model",
        lambda: ("chatgpt", ""),
    )
    monkeypatch.setattr("jarvis.codex_auth_state.codex_needs_reauth", lambda: False)
    monkeypatch.setattr("jarvis.codex_quota_state.codex_in_quota_cooldown", lambda **_k: True)

    async def _no_claude(self: Any, **_k: Any) -> None:
        raise AssertionError("a codex-pinned critic must not run on Claude")

    monkeypatch.setattr(CriticRunner, "_invoke_via_claude_direct", _no_claude)
    _no_paid_critic(monkeypatch)

    with pytest.raises(CriticCapacityUnavailable) as exc:
        await _review(tmp_path)
    assert (exc.value.reason, exc.value.provider) == ("provider_quota", "codex")


async def test_a_long_review_that_mentions_quotas_is_not_a_capacity_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only a short limit notice counts; a review about rate limiting does not."""
    from jarvis.missions.critic.verdict import CriticSchemaInvalid

    review_prose = "The patch adds a rate limit and a quota check. " * 30
    _patch_direct(monkeypatch, stdout=review_prose)
    _pin(monkeypatch)
    _no_paid_critic(monkeypatch)

    with pytest.raises(CriticSchemaInvalid):
        await _review(tmp_path)
    assert not claude_in_quota_cooldown()


# --- The orchestrator: park, then review only -------------------------------------


class ScriptedCritic:
    """Raises the queued errors in order, then approves; records each call."""

    def __init__(self, *errors: Exception) -> None:
        self._errors = list(errors)
        self.calls: list[dict[str, Any]] = []

    def critic_family(self) -> str:
        return "claude"

    async def run(self, **kwargs: Any) -> CriticVerdict:
        self.calls.append(kwargs)
        if self._errors:
            raise self._errors.pop(0)
        return CriticVerdict(
            verdict="approve",
            axes={ax: CriticAxis(status="pass", evidence=["x:1"]) for ax in REQUIRED_AXES},
            issues=[],
            correction_instruction="",
            summary="ok",
            summary_de="ok",
            confidence=0.9,
            suggested_next_action="accept",
        )


DELIVERED_DIFF = "diff --git a/report.md b/report.md\n@@ -0,0 +1 @@\n+the report\n"


@pytest.fixture
async def manager(tmp_missions_db: Path):
    m = MissionManager(tmp_missions_db)
    await m.start()
    yield m
    await m.stop()


@pytest.fixture(autouse=False)
def delivered(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker's result is a real diff, restorable after a park."""
    monkeypatch.setattr(Kontrollierer, "_capture_diff", lambda self, wt: DELIVERED_DIFF)
    monkeypatch.setattr(Kontrollierer, "_restore_task_workspace", lambda self, wt, art: True)


class CountingFactory:
    def __init__(self) -> None:
        self.capacity = True
        self.workers: list[FakeMissionWorker] = []

    def __call__(self, _step: Step) -> FakeMissionWorker:
        if not self.capacity:
            raise WorkerCapacityUnavailable("provider_quota", "claude")
        worker = FakeMissionWorker(family="claude")
        self.workers.append(worker)
        return worker

    def spawned(self) -> int:
        return sum(len(w.prompts) for w in self.workers)


async def _payloads(manager: MissionManager, mission_id: str) -> list[Any]:
    return [e.payload for e in await manager.store.events_for_mission(mission_id)]


async def test_spent_critic_parks_delivered_work_and_resume_only_reviews(
    manager: MissionManager, tmp_path: Path, delivered: None
) -> None:
    factory = CountingFactory()
    critic = ScriptedCritic(CriticCapacityUnavailable("provider_quota", "claude"))
    plan = MissionPlan(steps=[Step(slug="report", prompt="write it")], n_workers=1)
    k = make_kontrollierer(manager, tmp_path, plan, factory, critic=critic)
    mission_id = await manager.dispatch(prompt="write the report")

    assert await k.run_mission(mission_id) == MissionState.WAITING_CAPACITY
    payloads = await _payloads(manager, mission_id)
    assert not any(isinstance(p, MissionFailed) for p in payloads)
    wait = [p for p in payloads if isinstance(p, MissionWaitingCapacity)][-1]
    assert (wait.reason, wait.provider) == ("provider_quota", "claude")
    checkpoint = read_checkpoint(tmp_path / "missions" / f"mission_{mission_id[:13]}")
    assert checkpoint is not None
    assert checkpoint["steps"][0]["pending_review"] is True
    assert factory.spawned() == 1

    # Capacity is back: the resume runs the critic on the saved result only.
    assert await k.resume_waiting_missions() == [mission_id]
    assert await manager.mission(mission_id) is not None
    view = await manager.mission(mission_id)
    assert view is not None and view.state == MissionState.APPROVED
    assert factory.spawned() == 1  # the worker never ran again
    assert len(critic.calls) == 2
    assert critic.calls[-1]["worker_diff"] == DELIVERED_DIFF


async def test_capacity_waits_never_count_toward_failing_the_review(
    manager: MissionManager, tmp_path: Path, delivered: None
) -> None:
    from jarvis.missions.capacity import MAX_REVIEW_RETRIES

    factory = CountingFactory()
    spent = [CriticCapacityUnavailable("provider_quota", "claude")] * (MAX_REVIEW_RETRIES + 2)
    critic = ScriptedCritic(*spent)
    plan = MissionPlan(steps=[Step(slug="report", prompt="write it")], n_workers=1)
    k = make_kontrollierer(manager, tmp_path, plan, factory, critic=critic)
    mission_id = await manager.dispatch(prompt="write the report")

    assert await k.run_mission(mission_id) == MissionState.WAITING_CAPACITY
    for _ in range(MAX_REVIEW_RETRIES + 1):
        assert await k.resume_mission(mission_id) == MissionState.WAITING_CAPACITY
    assert await k.resume_mission(mission_id) == MissionState.APPROVED
    assert factory.spawned() == 1


async def test_paid_run_with_spent_critic_parks_without_paying_again(
    manager: MissionManager, tmp_path: Path, delivered: None
) -> None:
    """The approved paid worker runs once; its critic is spent → park. The
    approval dies with the run: the review later resumes on the subscription,
    and neither the paid worker nor a paid critic runs again."""
    offer = PaidOffer(
        provider="claude-api",
        model="claude-sonnet-4-6",
        estimated_cost_usd=1.0,
        cost_cap_usd=PAID_MISSION_CAP_USD,
        reason="provider_quota",
        open_steps=1,
    )
    paid = FakePaidOption(offer, cost_per_spawn=0.4)
    factory = CountingFactory()
    factory.capacity = False  # Claude Pro is spent: the first run parks
    critic = ScriptedCritic(CriticCapacityUnavailable("provider_quota", "claude"))
    plan = MissionPlan(steps=[Step(slug="report", prompt="write it")], n_workers=1)
    k = make_kontrollierer(manager, tmp_path, plan, factory, critic=critic, paid_option=paid)
    mission_id = await manager.dispatch(prompt="write the report")
    assert await k.run_mission(mission_id) == MissionState.WAITING_CAPACITY
    assert len(critic.calls) == 0

    await k.decide_capacity(mission_id, "approve_paid", provider=offer.provider, model=offer.model)
    for run in list(k._background_runs):
        await run

    view = await manager.mission(mission_id)
    assert view is not None and view.state == MissionState.WAITING_CAPACITY
    assert sum(len(w.prompts) for w in paid.workers) == 1  # paid worker ran once
    assert len(critic.calls) == 1
    payloads = await _payloads(manager, mission_id)
    assert [p.cost_usd for p in payloads if isinstance(p, MissionPaidUsage)] == [0.4]
    checkpoint = read_checkpoint(tmp_path / "missions" / f"mission_{mission_id[:13]}")
    assert checkpoint is not None
    assert checkpoint["provider"] == "claude"  # waits for the subscription, not the key
    assert checkpoint["steps"][0]["pending_review"] is True
    assert k._paid_approval == {}

    # Subscription back: only the review runs — no paid worker, no new worker.
    factory.capacity = True
    assert await k.resume_waiting_missions() == [mission_id]
    view = await manager.mission(mission_id)
    assert view is not None and view.state == MissionState.APPROVED
    assert sum(len(w.prompts) for w in paid.workers) == 1
    assert factory.spawned() == 0
    assert len(critic.calls) == 2
