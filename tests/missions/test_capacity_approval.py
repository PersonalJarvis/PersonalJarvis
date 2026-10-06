"""Explicit, per-mission approval of paid API use for a parked mission.

The user's rules (2026-10-06): never switch to a paid API on its own; show
provider, model, estimated cost and reason first; options wait / approve for
this mission / cancel; an approval covers this one mission only and is never
stored or global; log the decision and the real cost; a declined mission
stays safely parked.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.missions import init as mi
from jarvis.missions.capacity import (
    PAID_MISSION_CAP_USD,
    CapacityDecisionRejected,
    PaidOffer,
    WorkerCapacityUnavailable,
    estimate_paid_cost_usd,
    read_checkpoint,
)
from jarvis.missions.events import (
    MissionCancelled,
    MissionCapacityDecision,
    MissionPaidUsage,
    MissionWaitingCapacity,
)
from jarvis.missions.kontrollierer.decomposer import MissionPlan, Step
from jarvis.missions.manager import MissionManager
from jarvis.missions.state_machine import MissionState
from jarvis.missions.workers.api_agent_worker import ApiAgentWorker
from jarvis.ui.web.missions_routes import router
from tests.fakes.fake_mission_runtime import (
    FakeMissionWorker,
    FakePaidOption,
    make_kontrollierer,
)

OFFER = PaidOffer(
    provider="claude-api",
    model="claude-sonnet-4-6",
    estimated_cost_usd=1.65,
    cost_cap_usd=PAID_MISSION_CAP_USD,
    reason="provider_quota",
    open_steps=1,
)


@pytest.fixture
async def manager(tmp_missions_db: Path):
    m = MissionManager(tmp_missions_db)
    await m.start()
    yield m
    await m.stop()


class SubscriptionFactory:
    """The subscription worker factory; ``capacity`` False parks every call."""

    def __init__(self) -> None:
        self.capacity = False
        self.built = 0

    def __call__(self, _step: Step) -> FakeMissionWorker:
        if not self.capacity:
            raise WorkerCapacityUnavailable("provider_quota", "claude")
        self.built += 1
        return FakeMissionWorker(family="claude")


async def _parked(
    manager: MissionManager,
    tmp_path: Path,
    *,
    paid: FakePaidOption | None,
) -> tuple[Any, str, SubscriptionFactory]:
    factory = SubscriptionFactory()
    plan = MissionPlan(steps=[Step(slug="task", prompt="write it")], n_workers=1)
    k = make_kontrollierer(manager, tmp_path, plan, factory, paid_option=paid)
    mission_id = await manager.dispatch(prompt="write the report")
    assert await k.run_mission(mission_id) == MissionState.WAITING_CAPACITY
    return k, mission_id, factory


async def _payloads(manager: MissionManager, mission_id: str) -> list[Any]:
    return [e.payload for e in await manager.store.events_for_mission(mission_id)]


async def _state(manager: MissionManager, mission_id: str) -> MissionState:
    view = await manager.mission(mission_id)
    assert view is not None
    return view.state


async def _wait_paid_runs(k: Any) -> None:
    for run in list(k._background_runs):
        await run


# --- The offer: shown before anything is billed -------------------------------


async def test_offer_names_provider_model_cost_cap_and_reason(
    manager: MissionManager, tmp_path: Path
) -> None:
    paid = FakePaidOption(OFFER)
    k, mission_id, _f = await _parked(manager, tmp_path, paid=paid)

    offer = await k.paid_offer(mission_id)

    assert offer == OFFER
    assert paid.offer_calls == [
        {"pinned_family": "claude", "open_steps": 1, "reason": "provider_quota"}
    ]
    assert paid.workers == []  # looking at an offer never builds a worker


async def test_no_offer_without_a_paid_option_or_when_not_parked(
    manager: MissionManager, tmp_path: Path
) -> None:
    k, mission_id, _f = await _parked(manager, tmp_path, paid=None)
    assert await k.paid_offer(mission_id) is None

    k2, mission_id2, _f2 = await _parked(manager, tmp_path / "b", paid=FakePaidOption(OFFER))
    await k2.decide_capacity(mission_id2, "cancel")
    assert await k2.paid_offer(mission_id2) is None


# --- Decisions -------------------------------------------------------------------


async def test_wait_keeps_the_mission_parked_and_logs_it(
    manager: MissionManager, tmp_path: Path
) -> None:
    paid = FakePaidOption(OFFER)
    k, mission_id, factory = await _parked(manager, tmp_path, paid=paid)

    assert await k.decide_capacity(mission_id, "wait") == MissionState.WAITING_CAPACITY

    assert await _state(manager, mission_id) == MissionState.WAITING_CAPACITY
    decisions = [
        p for p in await _payloads(manager, mission_id) if isinstance(p, MissionCapacityDecision)
    ]
    assert [d.decision for d in decisions] == ["wait"]
    assert decisions[0].provider == "claude-api"  # the offer that was declined
    assert paid.workers == [] and factory.built == 0
    assert k._paid_approval == {}


async def test_cancel_ends_the_mission_and_logs_it(
    manager: MissionManager, tmp_path: Path
) -> None:
    paid = FakePaidOption(OFFER)
    k, mission_id, _f = await _parked(manager, tmp_path, paid=paid)

    assert await k.decide_capacity(mission_id, "cancel") == MissionState.CANCELLED

    payloads = await _payloads(manager, mission_id)
    assert [p.decision for p in payloads if isinstance(p, MissionCapacityDecision)] == ["cancel"]
    assert any(isinstance(p, MissionCancelled) for p in payloads)
    assert paid.workers == []


async def test_approval_must_match_the_offer_that_was_shown(
    manager: MissionManager, tmp_path: Path
) -> None:
    paid = FakePaidOption(OFFER)
    k, mission_id, _f = await _parked(manager, tmp_path, paid=paid)

    with pytest.raises(CapacityDecisionRejected):
        await k.decide_capacity(
            mission_id, "approve_paid", provider="claude-api", model="claude-opus-5-5"
        )

    assert await _state(manager, mission_id) == MissionState.WAITING_CAPACITY
    assert not any(
        isinstance(p, MissionCapacityDecision) for p in await _payloads(manager, mission_id)
    )
    assert paid.workers == []


async def test_approval_needs_an_offer(manager: MissionManager, tmp_path: Path) -> None:
    k, mission_id, _f = await _parked(manager, tmp_path, paid=FakePaidOption(None))
    with pytest.raises(CapacityDecisionRejected):
        await k.decide_capacity(
            mission_id, "approve_paid", provider=OFFER.provider, model=OFFER.model
        )
    assert await _state(manager, mission_id) == MissionState.WAITING_CAPACITY


async def test_approved_run_uses_exactly_the_approved_offer_and_logs_real_cost(
    manager: MissionManager, tmp_path: Path
) -> None:
    paid = FakePaidOption(OFFER, cost_per_spawn=0.42)
    k, mission_id, factory = await _parked(manager, tmp_path, paid=paid)

    state = await k.decide_capacity(
        mission_id, "approve_paid", provider=OFFER.provider, model=OFFER.model
    )
    assert state == MissionState.RUNNING
    await _wait_paid_runs(k)

    assert await _state(manager, mission_id) == MissionState.APPROVED
    assert [w.family for w in paid.workers] == ["claude-api"]
    assert paid.remaining == [OFFER.cost_cap_usd]
    assert factory.built == 0  # no subscription worker, no other provider
    payloads = await _payloads(manager, mission_id)
    decision = next(p for p in payloads if isinstance(p, MissionCapacityDecision))
    assert (decision.decision, decision.provider, decision.model) == (
        "approve_paid", "claude-api", "claude-sonnet-4-6",
    )
    assert decision.estimated_cost_usd == OFFER.estimated_cost_usd
    usage = [p for p in payloads if isinstance(p, MissionPaidUsage)]
    assert len(usage) == 1
    assert usage[0].cost_usd == pytest.approx(0.42)
    assert usage[0].cost_cap_usd == OFFER.cost_cap_usd
    # The approval died with the run.
    assert k._paid_approval == {} and k._paid_spent == {}


async def test_cost_cap_parks_the_run_and_keeps_waiting_for_the_subscription(
    manager: MissionManager, tmp_path: Path
) -> None:
    paid = FakePaidOption(OFFER, cost_per_spawn=2.5)
    k, mission_id, factory = await _parked(manager, tmp_path, paid=paid)

    await k.decide_capacity(mission_id, "approve_paid", provider=OFFER.provider, model=OFFER.model)
    await _wait_paid_runs(k)

    assert await _state(manager, mission_id) == MissionState.WAITING_CAPACITY
    payloads = await _payloads(manager, mission_id)
    wait = [p for p in payloads if isinstance(p, MissionWaitingCapacity)][-1]
    assert (wait.reason, wait.provider) == ("paid_cap_reached", "claude-api")
    assert [p.cost_usd for p in payloads if isinstance(p, MissionPaidUsage)] == [2.5]
    # The checkpoint still waits for the ORIGINAL subscription, not the key.
    checkpoint = read_checkpoint(tmp_path / "missions" / f"mission_{mission_id[:13]}")
    assert checkpoint is not None and checkpoint["provider"] == "claude"


async def test_an_approval_is_never_reused(manager: MissionManager, tmp_path: Path) -> None:
    """After a paid run parks, the automatic resume path waits for the
    subscription again and never runs the paid worker without a new approval."""
    paid = FakePaidOption(OFFER, cost_per_spawn=2.5)
    k, mission_id, factory = await _parked(manager, tmp_path, paid=paid)
    await k.decide_capacity(mission_id, "approve_paid", provider=OFFER.provider, model=OFFER.model)
    await _wait_paid_runs(k)
    paid_workers_before = len(paid.workers)

    assert await k.resume_waiting_missions() == []  # subscription still spent
    assert len(paid.workers) == paid_workers_before
    assert await _state(manager, mission_id) == MissionState.WAITING_CAPACITY

    factory.capacity = True  # the subscription is back: resume on it, unpaid
    assert await k.resume_waiting_missions() == [mission_id]
    assert len(paid.workers) == paid_workers_before
    assert factory.built == 2  # one capacity probe, one real run
    assert await _state(manager, mission_id) == MissionState.APPROVED


async def test_approval_on_another_mission_does_not_apply(
    manager: MissionManager, tmp_path: Path
) -> None:
    paid = FakePaidOption(OFFER)
    k, first, _f = await _parked(manager, tmp_path, paid=paid)
    second = await manager.dispatch(prompt="second job")
    assert await k.run_mission(second) == MissionState.WAITING_CAPACITY

    await k.decide_capacity(first, "approve_paid", provider=OFFER.provider, model=OFFER.model)
    await _wait_paid_runs(k)

    assert await _state(manager, first) == MissionState.APPROVED
    assert await _state(manager, second) == MissionState.WAITING_CAPACITY
    assert len(paid.workers) == 1


async def test_decisions_need_a_parked_mission(manager: MissionManager, tmp_path: Path) -> None:
    paid = FakePaidOption(OFFER)
    k, mission_id, _f = await _parked(manager, tmp_path, paid=paid)
    await k.decide_capacity(mission_id, "cancel")
    with pytest.raises(CapacityDecisionRejected):
        await k.decide_capacity(mission_id, "wait")


# --- The real paid option (API keys) ----------------------------------------------


def _keys(monkeypatch: pytest.MonkeyPatch, viable: set[str], models: dict[str, str]) -> None:
    monkeypatch.setattr(mi, "_api_key_family_viable", lambda p: p in viable)
    monkeypatch.setattr(
        "jarvis.missions.workers.api_agent_worker._resolve_worker_model",
        lambda provider, _explicit: models.get(provider, ""),
    )


def test_same_vendor_key_is_offered_first(monkeypatch: pytest.MonkeyPatch) -> None:
    _keys(
        monkeypatch,
        {"claude-api", "openai"},
        {"claude-api": "claude-sonnet-4-6", "openai": "gpt-5.5"},
    )
    offer = mi.ApiKeyPaidOption().offer(
        pinned_family="claude", open_steps=2, reason="provider_quota"
    )
    assert offer is not None
    assert (offer.provider, offer.model) == ("claude-api", "claude-sonnet-4-6")
    assert offer.estimated_cost_usd == estimate_paid_cost_usd("claude-sonnet-4-6", 2)
    assert offer.cost_cap_usd == PAID_MISSION_CAP_USD


def test_unpriced_models_are_never_offered(monkeypatch: pytest.MonkeyPatch) -> None:
    _keys(monkeypatch, {"claude-api"}, {"claude-api": "no-such-model-xyz"})
    assert mi.ApiKeyPaidOption().offer(pinned_family="claude", open_steps=1, reason="r") is None


def test_no_viable_key_means_no_offer(monkeypatch: pytest.MonkeyPatch) -> None:
    _keys(monkeypatch, set(), {"claude-api": "claude-sonnet-4-6"})
    assert mi.ApiKeyPaidOption().offer(pinned_family="claude", open_steps=1, reason="r") is None


def test_a_spent_api_key_is_not_offered_again(monkeypatch: pytest.MonkeyPatch) -> None:
    _keys(
        monkeypatch,
        {"openrouter", "claude-api"},
        {"openrouter": "claude-sonnet-4-6", "claude-api": "claude-sonnet-4-6"},
    )
    offer = mi.ApiKeyPaidOption().offer(pinned_family="openrouter", open_steps=1, reason="r")
    assert offer is not None and offer.provider == "claude-api"


def test_paid_worker_is_pinned_to_the_approved_model_and_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mi, "_assemble_worker_mcp_servers", lambda **_k: ())
    worker = mi.ApiKeyPaidOption().worker(OFFER, remaining_usd=1.25, task_text="t")
    assert isinstance(worker, ApiAgentWorker)
    assert (worker.provider, worker.pinned_model, worker.cost_cap_usd) == (
        "claude-api", "claude-sonnet-4-6", 1.25,
    )


# --- The API worker stops at the cap and reports its spend --------------------------


class _UsageBrain:
    """Every turn asks for a tool and reports a fixed usage block."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, _req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.calls += 1
        yield BrainDelta(
            tool_call={"id": f"c{self.calls}", "name": "Write",
                       "input": {"file_path": f"f{self.calls}.txt", "content": "x"}}
        )
        yield BrainDelta(
            finish_reason="tool_use",
            usage={"input_tokens": 200_000, "output_tokens": 10_000, "cache_hit_tokens": 0},
        )


async def test_api_worker_stops_at_the_cost_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    brain = _UsageBrain()
    monkeypatch.setattr(
        "jarvis.missions.workers.api_agent_worker._build_brain", lambda _p, _m: brain
    )
    per_call = estimate_paid_cost_usd("claude-sonnet-4-6", 1) or 0.0
    assert per_call > 0
    worker = ApiAgentWorker(
        "claude-api", pinned_model="claude-sonnet-4-6", cost_cap_usd=1.0
    )
    events = [
        ev
        async for ev in worker.spawn(
            "go", worktree=tmp_path, env={}, job=None, worker_id="w",
            log_dir=tmp_path / "logs", model="ignored-model",
        )
    ]

    result = events[-1]
    assert result.is_error is True
    assert "cost cap" in result.result
    assert result.cost_usd is not None and result.cost_usd >= 1.0
    # 200k in + 10k out on Sonnet is about $0.75 a call: it stops on call two.
    assert brain.calls == 2


# --- REST -------------------------------------------------------------------------


def _app(manager: MissionManager, k: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.mission_manager = manager
    app.state.kontrollierer = k
    return app


async def test_rest_shows_the_offer_and_applies_decisions(
    manager: MissionManager, tmp_path: Path
) -> None:
    paid = FakePaidOption(OFFER)
    k, mission_id, _f = await _parked(manager, tmp_path, paid=paid)
    transport = httpx.ASGITransport(app=_app(manager, k))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        shown = await client.get(f"/api/missions/{mission_id}/paid-offer")
        mismatch = await client.post(
            f"/api/missions/{mission_id}/capacity-decision",
            json={"decision": "approve_paid", "provider": "openai", "model": "gpt-5.5"},
        )
        waited = await client.post(
            f"/api/missions/{mission_id}/capacity-decision", json={"decision": "wait"}
        )
        missing = await client.get("/api/missions/does-not-exist/paid-offer")

    assert shown.status_code == 200
    assert shown.json()["offer"] == {
        "provider": "claude-api",
        "model": "claude-sonnet-4-6",
        "estimated_cost_usd": 1.65,
        "cost_cap_usd": PAID_MISSION_CAP_USD,
        "reason": "provider_quota",
        "open_steps": 1,
    }
    assert mismatch.status_code == 409
    assert waited.status_code == 200 and waited.json()["state"] == "WAITING_CAPACITY"
    assert missing.status_code == 404
    assert paid.workers == []
