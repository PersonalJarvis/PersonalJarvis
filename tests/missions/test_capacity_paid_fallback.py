"""Paid-API fallback for missions: consent, caps and the one routing rule.

The maintainer's decisions (2026-10-07, jarvis/missions/capacity.py):

- subscriptions first; another connected subscription is free;
- ``[missions] paid_api_fallback`` (default OFF): ON continues on the user's
  key when no subscription has capacity, within $2 per mission (cumulative,
  shared by parallel steps, worker and critic, reserved before every call)
  and $10 per rolling 24 h of automatic use;
- consent is checked before EVERY paid call: switching the setting off parks
  the mission (``paid_consent_revoked``) at the next call;
- a manual approval grants up to $2 more, covers critic calls, shows what the
  mission already spent, and is recorded but never blocked by the daily cap;
- the setting is written only through config_writer, read fresh, behind the
  global web guard, flagged dangerous, and unreachable for voice/chat/agents.
"""
from __future__ import annotations

import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from jarvis.missions.capacity import (
    PAID_DAILY_CAP_USD,
    PAID_MISSION_CAP_USD,
    CriticCapacityUnavailable,
    DailyPaidLedger,
    MissionPaidLedger,
    PaidCallGate,
    PaidCallRefused,
    PaidOffer,
    RouteDecision,
    WorkerCapacityUnavailable,
    decide_route,
    paid_api_fallback_enabled,
)
from jarvis.missions.critic.verdict import REQUIRED_AXES, CriticAxis, CriticVerdict
from jarvis.missions.events import (
    EventEnvelope,
    MissionPaidUsage,
    MissionWaitingCapacity,
    now_ms,
)
from jarvis.missions.kontrollierer.decomposer import MissionPlan, Step
from jarvis.missions.manager import MissionManager
from jarvis.missions.state_machine import MissionState
from tests.fakes.fake_mission_runtime import (
    FakeMissionWorker,
    FakePaidOption,
    ResultEvent,
    make_kontrollierer,
    make_policy,
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


class SpentSubscriptions:
    """The worker factory of a subscription install whose subscriptions are
    all out of capacity until ``capacity`` is set."""

    def __init__(self) -> None:
        self.capacity = False
        self.built = 0

    def __call__(self, _step: Step) -> FakeMissionWorker:
        if not self.capacity:
            raise WorkerCapacityUnavailable("provider_quota", "claude")
        self.built += 1
        return FakeMissionWorker(family="claude")


def _approve() -> CriticVerdict:
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


class GateAwareCritic:
    """A critic whose subscription is spent: it grades only through the
    paid gate it is handed (paying ``cost`` per review), else parks."""

    def __init__(self, cost: float = 0.0) -> None:
        self.cost = cost
        self.calls: list[dict[str, Any]] = []
        self.paid: list[float] = []

    async def run(self, **kwargs: Any) -> CriticVerdict:
        self.calls.append(kwargs)
        gate: PaidCallGate | None = kwargs.get("paid_gate")
        if gate is None:
            raise CriticCapacityUnavailable("provider_quota", "claude")
        try:
            reservation = gate.reserve(self.cost)
        except PaidCallRefused as exc:
            raise CriticCapacityUnavailable(exc.reason, exc.provider) from None
        self.paid.append(gate.commit(reservation, self.cost))
        return _approve()


async def _payloads(manager: MissionManager, mission_id: str) -> list[Any]:
    return [e.payload for e in await manager.store.events_for_mission(mission_id)]


async def _state(manager: MissionManager, mission_id: str) -> MissionState:
    view = await manager.mission(mission_id)
    assert view is not None
    return view.state


def _usage(payloads: list[Any]) -> list[MissionPaidUsage]:
    return [p for p in payloads if isinstance(p, MissionPaidUsage)]


def _waits(payloads: list[Any]) -> list[MissionWaitingCapacity]:
    return [p for p in payloads if isinstance(p, MissionWaitingCapacity)]


def _one_step() -> MissionPlan:
    return MissionPlan(steps=[Step(slug="task", prompt="write it")], n_workers=1)


# --- The routing rule ---------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"subscription_ok": True}, RouteDecision("subscription")),
        ({}, RouteDecision("park", "provider_quota")),  # setting OFF, no approval
        ({"paid_fallback_enabled": True}, RouteDecision("paid", automatic=True)),
        ({"paid_fallback_enabled": True, "paid_option_available": False},
         RouteDecision("park", "provider_quota")),
        ({"paid_fallback_enabled": True, "mission_remaining_usd": 0.0},
         RouteDecision("park", "paid_cap_reached")),
        ({"paid_fallback_enabled": True, "daily_remaining_usd": 0.0},
         RouteDecision("park", "paid_daily_cap_reached")),
        ({"manual_approval": True}, RouteDecision("paid", automatic=False)),
        # A manual approval is never blocked by the daily cap ...
        ({"manual_approval": True, "daily_remaining_usd": 0.0},
         RouteDecision("paid", automatic=False)),
        # ... but by what it granted.
        ({"manual_approval": True, "mission_remaining_usd": 0.0},
         RouteDecision("park", "paid_cap_reached")),
    ],
)
def test_one_routing_rule(kwargs: dict[str, Any], expected: RouteDecision) -> None:
    base: dict[str, Any] = {
        "subscription_ok": False,
        "unavailable_reason": "provider_quota",
        "manual_approval": False,
        "paid_fallback_enabled": False,
        "paid_option_available": True,
        "mission_remaining_usd": 1.0,
        "daily_remaining_usd": 5.0,
    }
    assert decide_route(**{**base, **kwargs}) == expected


# --- The ledgers ------------------------------------------------------------------


def test_concurrent_reservations_never_cross_the_mission_cap() -> None:
    ledger = MissionPaidLedger()
    granted: list[float] = []
    lock = threading.Lock()

    def caller() -> None:
        for _ in range(10):
            if ledger.try_reserve(0.3, automatic=True):
                time.sleep(0.001)
                ledger.settle(0.3, 0.3)
                with lock:
                    granted.append(0.3)

    threads = [threading.Thread(target=caller) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert ledger.spent_usd <= PAID_MISSION_CAP_USD + 1e-9
    assert sum(granted) == pytest.approx(ledger.spent_usd)
    assert len(granted) == 6  # 6 x $0.30 = $1.80; a seventh would cross $2


def test_manual_approval_grants_two_dollars_more() -> None:
    ledger = MissionPaidLedger(spent_usd=1.75)
    assert ledger.remaining(automatic=True) == pytest.approx(0.25)
    assert ledger.remaining(automatic=False) == 0.0  # no approval yet
    ledger.grant_manual()
    assert ledger.remaining(automatic=False) == pytest.approx(PAID_MISSION_CAP_USD)
    ledger.revoke_manual()
    assert ledger.remaining(automatic=False) == 0.0


def test_daily_ledger_is_rolling_and_counts_automatic_use_only(tmp_path: Path) -> None:
    clock = [1_000_000.0]
    ledger = DailyPaidLedger(tmp_path / "ledger.json", clock=lambda: clock[0])
    ledger.record(4.0, automatic=True, mission_id="a")
    ledger.record(3.0, automatic=False, mission_id="b")  # manual: reported, not capped
    assert ledger.spent_last_24h() == pytest.approx(4.0)
    assert ledger.spent_last_24h(automatic_only=False) == pytest.approx(7.0)
    assert ledger.remaining() == pytest.approx(PAID_DAILY_CAP_USD - 4.0)
    assert ledger.try_reserve(6.0) and not ledger.try_reserve(0.01)
    ledger.record(0.5, automatic=True, mission_id="a", reserved_usd=6.0)
    # A fresh reader on the same file sees the persisted entries.
    again = DailyPaidLedger(tmp_path / "ledger.json", clock=lambda: clock[0])
    assert again.spent_last_24h() == pytest.approx(4.5)
    clock[0] += 24 * 3600 + 1
    assert again.spent_last_24h() == 0.0  # rolled out of the window


def test_unreadable_daily_ledger_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text("{not json", encoding="utf-8")
    ledger = DailyPaidLedger(path)
    assert ledger.remaining() == 0.0
    assert ledger.try_reserve(0.01) is False


# --- Setting OFF / ON -----------------------------------------------------------------


async def test_setting_off_parks_and_never_pays(manager: MissionManager, tmp_path: Path) -> None:
    paid = FakePaidOption(OFFER, calls=[(0.2, 0.2)])
    k = make_kontrollierer(manager, tmp_path, _one_step(), SpentSubscriptions(), paid_option=paid)
    mission_id = await manager.dispatch(prompt="write the report")

    assert await k.run_mission(mission_id) == MissionState.WAITING_CAPACITY
    assert paid.workers == []
    payloads = await _payloads(manager, mission_id)
    assert _waits(payloads)[-1].reason == "provider_quota"
    assert _usage(payloads) == []


async def test_setting_on_continues_on_the_key_within_the_caps(
    manager: MissionManager, tmp_path: Path
) -> None:
    policy, _pinned, _paid = make_policy(tmp_path, paid_fallback=True)
    paid = FakePaidOption(OFFER, calls=[(0.3, 0.25), (0.3, 0.2)])
    k = make_kontrollierer(
        manager, tmp_path, _one_step(), SpentSubscriptions(), paid_option=paid,
        capacity_policy=policy,
    )
    mission_id = await manager.dispatch(prompt="write the report")

    assert await k.run_mission(mission_id) == MissionState.APPROVED
    assert [w.family for w in paid.workers] == ["claude-api"]
    usage = _usage(await _payloads(manager, mission_id))
    assert len(usage) == 1
    assert (usage[0].provider, usage[0].automatic) == ("claude-api", True)
    assert usage[0].cost_usd == pytest.approx(0.45)
    assert policy.daily.spent_last_24h() == pytest.approx(0.45)


async def test_a_key_only_install_ignores_the_setting(
    manager: MissionManager, tmp_path: Path
) -> None:
    """The switch has no effect without a subscription: such an install never
    parks for capacity in the first place (its factory crosses families)."""
    policy, _pinned, _paid = make_policy(tmp_path, pinned=False, paid_fallback=True)
    paid = FakePaidOption(OFFER, calls=[(0.3, 0.25)])
    k = make_kontrollierer(
        manager, tmp_path, _one_step(), lambda _s: FakeMissionWorker(family="openrouter"),
        paid_option=paid, capacity_policy=policy,
    )
    mission_id = await manager.dispatch(prompt="write the report")
    assert await k.run_mission(mission_id) == MissionState.APPROVED
    assert paid.workers == [] and paid.offer_calls == []


async def test_a_usage_less_call_counts_its_whole_reservation(
    manager: MissionManager, tmp_path: Path
) -> None:
    policy, _pinned, _paid = make_policy(tmp_path, paid_fallback=True)
    paid = FakePaidOption(OFFER, calls=[(0.7, None)])
    k = make_kontrollierer(
        manager, tmp_path, _one_step(), SpentSubscriptions(), paid_option=paid,
        capacity_policy=policy,
    )
    mission_id = await manager.dispatch(prompt="write the report")

    assert await k.run_mission(mission_id) == MissionState.APPROVED
    assert _usage(await _payloads(manager, mission_id))[0].cost_usd == pytest.approx(0.7)
    assert policy.daily.spent_last_24h() == pytest.approx(0.7)


async def test_parallel_workers_share_one_two_dollar_ledger(
    manager: MissionManager, tmp_path: Path
) -> None:
    """Finding 5: four parallel steps each want three $0.40 calls ($4.80).
    Every call reserves first, so together they never pass $2."""
    policy, _pinned, _paid = make_policy(tmp_path, paid_fallback=True)
    paid = FakePaidOption(OFFER, calls=[(0.4, 0.4)] * 3, call_delay_s=0.01)
    steps = [Step(slug=f"s{i}", prompt=f"part {i}") for i in range(4)]
    plan = MissionPlan(steps=steps, n_workers=4)
    k = make_kontrollierer(
        manager, tmp_path, plan, SpentSubscriptions(), paid_option=paid,
        capacity_policy=policy,
    )
    mission_id = await manager.dispatch(prompt="four parts")

    assert await k.run_mission(mission_id) == MissionState.WAITING_CAPACITY
    payloads = await _payloads(manager, mission_id)
    spent = sum(u.cost_usd for u in _usage(payloads))
    assert spent <= PAID_MISSION_CAP_USD + 1e-9
    assert spent == pytest.approx(2.0)  # five $0.40 calls fit, the sixth never
    assert len(paid.workers) >= 2  # really ran in parallel on one ledger
    assert _waits(payloads)[-1].reason == "paid_cap_reached"
    assert "paid_cap_reached" in {r for w in paid.workers for r in w.refused}


async def test_the_automatic_cap_is_cumulative_across_runs(
    manager: MissionManager, tmp_path: Path
) -> None:
    """The worker pays $1.50; its $0.60 critic call would cross $2 and parks
    the review. The resume starts from the recorded $1.50 — the cap never
    resets — so the review parks again instead of paying."""
    policy, _pinned, _paid = make_policy(tmp_path, paid_fallback=True)
    paid = FakePaidOption(OFFER, calls=[(0.5, 0.5)] * 3)
    critic = GateAwareCritic(cost=0.6)
    k = make_kontrollierer(
        manager, tmp_path, _one_step(), SpentSubscriptions(), critic=critic,
        paid_option=paid, capacity_policy=policy,
    )
    k._capture_diff = lambda wt: "diff --git a/r b/r\n+r\n"  # type: ignore[method-assign]
    k._restore_task_workspace = lambda wt, art: True  # type: ignore[method-assign]
    mission_id = await manager.dispatch(prompt="write the report")
    assert await k.run_mission(mission_id) == MissionState.WAITING_CAPACITY
    assert await k.paid_spent_usd(mission_id) == pytest.approx(1.5)

    assert await k.resume_mission(mission_id) == MissionState.WAITING_CAPACITY
    payloads = await _payloads(manager, mission_id)
    assert [w.reason for w in _waits(payloads)] == ["paid_cap_reached"] * 2
    assert critic.paid == []
    assert sum(len(w.prompts) for w in paid.workers) == 1  # the worker never re-ran
    assert await k.paid_spent_usd(mission_id) == pytest.approx(1.5)


# --- Daily cap ------------------------------------------------------------------------


async def test_a_spent_daily_cap_parks_without_a_worker(
    manager: MissionManager, tmp_path: Path
) -> None:
    policy, _pinned, _paid = make_policy(tmp_path, paid_fallback=True)
    policy.daily.record(PAID_DAILY_CAP_USD, automatic=True, mission_id="earlier")
    paid = FakePaidOption(OFFER, calls=[(0.2, 0.2)])
    k = make_kontrollierer(
        manager, tmp_path, _one_step(), SpentSubscriptions(), paid_option=paid,
        capacity_policy=policy,
    )
    mission_id = await manager.dispatch(prompt="write the report")

    assert await k.run_mission(mission_id) == MissionState.WAITING_CAPACITY
    assert paid.workers == []
    assert _waits(await _payloads(manager, mission_id))[-1].reason == "paid_daily_cap_reached"


async def test_the_daily_cap_stops_a_call_that_would_cross_it(
    manager: MissionManager, tmp_path: Path
) -> None:
    policy, _pinned, _paid = make_policy(tmp_path, paid_fallback=True)
    policy.daily.record(PAID_DAILY_CAP_USD - 0.1, automatic=True, mission_id="earlier")
    paid = FakePaidOption(OFFER, calls=[(0.4, 0.4)])
    k = make_kontrollierer(
        manager, tmp_path, _one_step(), SpentSubscriptions(), paid_option=paid,
        capacity_policy=policy,
    )
    mission_id = await manager.dispatch(prompt="write the report")

    assert await k.run_mission(mission_id) == MissionState.WAITING_CAPACITY
    assert paid.workers[0].refused == ["paid_daily_cap_reached"]
    assert policy.daily.spent_last_24h() == pytest.approx(PAID_DAILY_CAP_USD - 0.1)


# --- Consent per call ------------------------------------------------------------------


def _write_stream(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "stream.jsonl").write_text("{}\n", encoding="utf-8")


def _config_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class _TogglingWorker:
    """Pays one call, then the user switches the setting off, then tries again."""

    cli = "claude"
    family = "claude-api"
    last_pid = 1

    def __init__(self, gate: PaidCallGate, switch: Any) -> None:
        self.gate = gate
        self.switch = switch

    async def spawn(self, prompt: str, *, log_dir: Path, **_k: Any) -> AsyncIterator[Any]:
        _write_stream(log_dir)
        spent = self.gate.commit(self.gate.reserve(0.2), 0.2)
        self.switch.value = False  # the user turns the setting OFF mid-run
        try:
            self.gate.reserve(0.2)
        except PaidCallRefused as exc:
            yield ResultEvent(
                is_error=True, subtype="error_during_execution", result=str(exc),
                cost_usd=spent,
            )
            return
        yield ResultEvent(cost_usd=spent)


async def test_switching_the_setting_off_parks_at_the_next_call(
    manager: MissionManager, tmp_path: Path
) -> None:
    policy, _pinned, paid_switch = make_policy(tmp_path, paid_fallback=True)

    class _Option(FakePaidOption):
        def worker(self, offer: PaidOffer, *, gate: PaidCallGate, task_text: str) -> Any:
            self.gates.append(gate)
            return _TogglingWorker(gate, paid_switch)

    paid = _Option(OFFER)
    k = make_kontrollierer(
        manager, tmp_path, _one_step(), SpentSubscriptions(), paid_option=paid,
        capacity_policy=policy,
    )
    mission_id = await manager.dispatch(prompt="write the report")

    assert await k.run_mission(mission_id) == MissionState.WAITING_CAPACITY
    payloads = await _payloads(manager, mission_id)
    assert _waits(payloads)[-1].reason == "paid_consent_revoked"
    assert [u.cost_usd for u in _usage(payloads)] == [pytest.approx(0.2)]
    assert paid.gates[0].refusal == "paid_consent_revoked"


# --- Resume -----------------------------------------------------------------------------


async def test_resume_never_goes_metered_with_the_setting_off(
    manager: MissionManager, tmp_path: Path
) -> None:
    policy, _pinned, paid_switch = make_policy(tmp_path)
    paid = FakePaidOption(OFFER, calls=[(0.3, 0.3)])
    factory = SpentSubscriptions()
    k = make_kontrollierer(
        manager, tmp_path, _one_step(), factory, paid_option=paid, capacity_policy=policy
    )
    mission_id = await manager.dispatch(prompt="write the report")
    assert await k.run_mission(mission_id) == MissionState.WAITING_CAPACITY

    for _ in range(3):
        assert await k.resume_waiting_missions() == []
    assert paid.workers == []
    assert await _state(manager, mission_id) == MissionState.WAITING_CAPACITY

    # The user turns the setting ON: the next tick resumes on the key.
    paid_switch.value = True
    assert await k.resume_waiting_missions() == [mission_id]
    assert await _state(manager, mission_id) == MissionState.APPROVED
    usage = _usage(await _payloads(manager, mission_id))
    assert [(u.cost_usd, u.automatic) for u in usage] == [(pytest.approx(0.3), True)]


# --- Manual approval: covers the critic, shows what was spent -----------------------------


async def test_approval_covers_the_critic_and_shows_the_spend(
    manager: MissionManager, tmp_path: Path
) -> None:
    critic = GateAwareCritic(cost=0.3)
    paid = FakePaidOption(OFFER, calls=[(0.4, 0.4)])
    k = make_kontrollierer(
        manager, tmp_path, _one_step(), SpentSubscriptions(), critic=critic, paid_option=paid
    )
    mission_id = await manager.dispatch(prompt="write the report")
    assert await k.run_mission(mission_id) == MissionState.WAITING_CAPACITY
    # An earlier paid run of this mission already cost $0.50.
    await manager.store.append_and_publish(
        EventEnvelope(
            mission_id=mission_id,
            source_actor="kontrollierer",
            ts_ms=now_ms(),
            payload=MissionPaidUsage(
                provider="claude-api", model="claude-sonnet-4-6", cost_usd=0.5,
                cost_cap_usd=PAID_MISSION_CAP_USD, estimated_cost_usd=1.0, automatic=True,
            ),
        )
    )

    from jarvis.ui.web.missions_routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.mission_manager = manager
    app.state.kontrollierer = k
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        shown = (await client.get(f"/api/missions/{mission_id}/paid-offer")).json()["offer"]
    assert shown["spent_usd"] == pytest.approx(0.5)
    assert shown["covers_critic"] is True

    await k.decide_capacity(mission_id, "approve_paid", provider=OFFER.provider, model=OFFER.model)
    for run in list(k._background_runs):
        await run

    assert await _state(manager, mission_id) == MissionState.APPROVED
    assert critic.paid == [pytest.approx(0.3)]  # the critic call ran on the approval
    usage = _usage(await _payloads(manager, mission_id))
    assert [(u.cost_usd, u.automatic) for u in usage[1:]] == [(pytest.approx(0.7), False)]
    assert await k.paid_spent_usd(mission_id) == pytest.approx(1.2)


async def test_manual_approval_is_not_blocked_by_the_daily_cap(
    manager: MissionManager, tmp_path: Path
) -> None:
    policy, _pinned, _paid = make_policy(tmp_path)
    policy.daily.record(PAID_DAILY_CAP_USD, automatic=True, mission_id="earlier")
    paid = FakePaidOption(OFFER, calls=[(0.4, 0.4)])
    k = make_kontrollierer(
        manager, tmp_path, _one_step(), SpentSubscriptions(), paid_option=paid,
        capacity_policy=policy,
    )
    mission_id = await manager.dispatch(prompt="write the report")
    assert await k.run_mission(mission_id) == MissionState.WAITING_CAPACITY

    await k.decide_capacity(mission_id, "approve_paid", provider=OFFER.provider, model=OFFER.model)
    for run in list(k._background_runs):
        await run

    assert await _state(manager, mission_id) == MissionState.APPROVED
    # Recorded for reporting, never counted against the automatic cap.
    assert policy.daily.spent_last_24h() == pytest.approx(PAID_DAILY_CAP_USD)
    assert policy.daily.spent_last_24h(automatic_only=False) == pytest.approx(
        PAID_DAILY_CAP_USD + 0.4
    )


async def test_setting_on_lets_the_critic_pay_within_the_same_ledger(
    manager: MissionManager, tmp_path: Path
) -> None:
    policy, _pinned, _paid = make_policy(tmp_path, paid_fallback=True)
    critic = GateAwareCritic(cost=1.9)  # worker $0.40 + critic $1.90 > $2
    paid = FakePaidOption(OFFER, calls=[(0.4, 0.4)])
    k = make_kontrollierer(
        manager, tmp_path, _one_step(), SpentSubscriptions(), critic=critic,
        paid_option=paid, capacity_policy=policy,
    )
    mission_id = await manager.dispatch(prompt="write the report")

    assert await k.run_mission(mission_id) == MissionState.WAITING_CAPACITY
    payloads = await _payloads(manager, mission_id)
    assert _waits(payloads)[-1].reason == "paid_cap_reached"
    assert critic.paid == []  # the critic call that would cross $2 was refused
    assert sum(u.cost_usd for u in _usage(payloads)) == pytest.approx(0.4)


# --- The setting itself ---------------------------------------------------------------------


@pytest.fixture
def temp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "jarvis.toml"
    path.write_text('[ui]\ntheme = "dark"\n', encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", str(path))
    return path


def test_setting_defaults_off_and_is_read_fresh(temp_config: Path) -> None:
    from jarvis.core.config import JarvisConfig, MissionsConfig
    from jarvis.core.config_writer import set_missions_paid_api_fallback

    assert MissionsConfig().paid_api_fallback is False
    loaded = JarvisConfig(missions={"paid_api_fallback": True, "later_key": 1})
    assert loaded.missions.paid_api_fallback is True
    assert paid_api_fallback_enabled() is False
    set_missions_paid_api_fallback(True)
    assert paid_api_fallback_enabled() is True  # no restart, no cache
    text = _config_text(temp_config)
    assert "[missions]" in text and "paid_api_fallback = true" in text
    assert 'theme = "dark"' in text  # the rest of the file is untouched
    set_missions_paid_api_fallback(False)
    assert paid_api_fallback_enabled() is False
    with pytest.raises(TypeError):
        set_missions_paid_api_fallback("yes")  # type: ignore[arg-type]


def test_an_unreadable_config_never_enables_paid_use(
    temp_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temp_config.write_text("[missions]\npaid_api_fallback = true\n", encoding="utf-8")
    assert paid_api_fallback_enabled() is True

    def _broken(*_a: Any, **_k: Any) -> Any:
        raise OSError("config unreadable")

    monkeypatch.setattr("jarvis.core.config.load_config", _broken)
    assert paid_api_fallback_enabled() is False


def test_no_voice_chat_or_agent_surface_can_flip_it() -> None:
    from jarvis.app_actions.catalog import is_excluded
    from jarvis.core.self_mod.forbidden import is_forbidden
    from jarvis.core.self_mod.schema_introspect import introspect_mutable_specs

    assert is_forbidden("missions.paid_api_fallback")
    assert "missions.paid_api_fallback" not in {s.path for s in introspect_mutable_specs()}
    assert is_excluded("/api/mission-billing")
    assert is_excluded("/api/missions/{mission_id}/capacity-decision")


# --- Routes ------------------------------------------------------------------------------------


def _billing_app(k: Any | None = None) -> FastAPI:
    from jarvis.ui.web.mission_billing_routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.kontrollierer = k
    return app


async def test_routes_read_and_persist_the_switch(
    manager: MissionManager, tmp_path: Path, temp_config: Path
) -> None:
    policy, _pinned, _paid = make_policy(tmp_path)
    policy.daily.record(1.25, automatic=True, mission_id="m")
    k = make_kontrollierer(
        manager, tmp_path, _one_step(), SpentSubscriptions(),
        paid_option=FakePaidOption(OFFER), capacity_policy=policy,
    )
    transport = httpx.ASGITransport(app=_billing_app(k))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        before = await client.get("/api/mission-billing")
        turned_on = await client.put("/api/mission-billing", json={"paid_api_fallback": True})
        coerced = await client.put("/api/mission-billing", json={"paid_api_fallback": "yes"})
        extra = await client.put(
            "/api/mission-billing", json={"paid_api_fallback": False, "daily_cap_usd": 99}
        )
        after = await client.get("/api/mission-billing")

    assert before.status_code == 200
    assert before.json() == {
        "paid_api_fallback": False,
        "subscription_mode": True,
        "per_mission_cap_usd": PAID_MISSION_CAP_USD,
        "daily_cap_usd": PAID_DAILY_CAP_USD,
        "spent_last_24h_usd": 1.25,
        "paid_provider": {
            "provider": "claude-api", "model": "claude-sonnet-4-6", "price_known": True,
        },
    }
    assert turned_on.status_code == 200 and turned_on.json()["paid_api_fallback"] is True
    assert coerced.status_code == 422 and extra.status_code == 422
    assert after.json()["paid_api_fallback"] is True
    assert "paid_api_fallback = true" in _config_text(temp_config)
    # The next decision sees it at once (the policy reads the file).
    assert paid_api_fallback_enabled() is True


async def test_routes_without_a_runner_show_no_priced_key(
    temp_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.missions import init as mi

    monkeypatch.setattr(mi, "_api_key_family_viable", lambda _p: False)
    transport = httpx.ASGITransport(app=_billing_app(None))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get("/api/mission-billing")).json()
    assert body["paid_provider"] is None
    assert body["subscription_mode"] is False  # no subscription seen, no login
    assert body["spent_last_24h_usd"] == 0.0


def test_put_is_flagged_dangerous_for_the_cli() -> None:
    schema = _billing_app().openapi()
    put = schema["paths"]["/api/mission-billing"]["put"]
    assert put.get("x-jarvis-dangerous") is True
    assert put["tags"] == ["missions"]
    assert "x-jarvis-dangerous" not in schema["paths"]["/api/mission-billing"]["get"]


@pytest.mark.no_auto_web_auth
async def test_the_global_guard_protects_the_switch(temp_config: Path) -> None:
    from jarvis.ui.web.surface_security import SurfaceSecurity

    secured = SurfaceSecurity(
        _billing_app(),
        public_urls="https://jarvis.example",
        control_key_validator=lambda token: token == "test-control",  # noqa: S105 - fixture
        session_validator=lambda token: False,
    )
    transport = httpx.ASGITransport(app=secured, client=("198.51.100.10", 54321))
    body = {"paid_api_fallback": True}
    key = {"Authorization": "Bearer test-control"}
    async with httpx.AsyncClient(transport=transport, base_url="https://jarvis.example") as c:
        anonymous = await c.put("/api/mission-billing", json=body)
        foreign = await c.put(
            "/api/mission-billing", json=body,
            headers={**key, "Origin": "https://evil.example"},
        )
        assert "paid_api_fallback = true" not in _config_text(temp_config)
        allowed = await c.put("/api/mission-billing", json=body, headers=key)

    assert anonymous.status_code == 401
    assert foreign.status_code == 403
    assert allowed.status_code == 200
    assert "paid_api_fallback = true" in _config_text(temp_config)


def test_no_route_response_carries_a_secret(
    temp_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The billing state names a provider and model at most — never a key."""
    from fastapi.testclient import TestClient

    from jarvis.missions import init as mi

    fake_key = "sk-ant-api03-FAKE-NOT-A-SECRET"
    monkeypatch.setattr("jarvis.core.config.get_jarvis_agent_secret", lambda _p: fake_key)
    monkeypatch.setattr(mi, "_api_key_family_viable", lambda p: p == "claude-api")
    client = TestClient(_billing_app(None))
    response = client.get("/api/mission-billing")
    assert response.status_code == 200
    assert fake_key not in response.text


# --- codex: a fresh login lifts the dead-login flag -----------------------------------------


def test_a_fresh_codex_login_clears_needs_reauth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding 2: a mission parked on a dead codex login resumes after
    `codex login`, without needing a codex success first (none can happen
    while it is parked) and without a restart."""
    from jarvis import codex_auth_state as cas

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    auth = tmp_path / "auth.json"
    auth.write_text('{"tokens": {"refresh_token": "dead-FAKE"}}', encoding="utf-8")
    cas.clear_codex_needs_reauth()
    try:
        cas.mark_codex_needs_reauth()
        assert cas.codex_needs_reauth() is True  # same dead login: stays flagged
        auth.write_text('{"tokens": {"refresh_token": "fresh-FAKE"}}', encoding="utf-8")
        assert cas.codex_needs_reauth() is False  # `codex login` wrote a new one
        assert cas.codex_needs_reauth() is False

        # Without any login file the flag holds until a success clears it.
        auth.unlink()
        cas.mark_codex_needs_reauth()
        assert cas.codex_needs_reauth() is True
        assert cas.codex_auth_fingerprint() is None
    finally:
        cas.clear_codex_needs_reauth()


def test_codex_fingerprint_never_contains_the_token(tmp_path: Path) -> None:
    from jarvis.codex_auth_state import codex_auth_fingerprint

    auth = tmp_path / "auth.json"
    auth.write_text('{"tokens": {"access_token": "abc-FAKE-TOKEN"}}', encoding="utf-8")
    fp = codex_auth_fingerprint(auth)
    assert fp is not None and len(fp) == 16 and "FAKE" not in fp


# --- The paid critic call itself ---------------------------------------------------------------


class _FakeCriticBrain:
    """An in-process API brain that answers with one approve verdict."""

    usage: dict[str, int] | None = {"input_tokens": 1000, "output_tokens": 200}
    calls = 0

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def complete(self, _req: Any) -> AsyncIterator[Any]:
        from jarvis.core.protocols import BrainDelta

        type(self).calls += 1
        yield BrainDelta(content=_approve().model_dump_json())
        yield BrainDelta(finish_reason="end_turn", usage=type(self).usage)


def _subscriptions_spent(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.claude_quota_state import mark_claude_quota_cooldown

    monkeypatch.setattr("jarvis.missions.init.install_is_pinned", lambda: True)
    monkeypatch.setattr(
        "jarvis.missions.critic.runner._resolve_critic_provider_model",
        lambda: ("claude-api", "claude-sonnet-4-6"),
    )
    monkeypatch.setattr(
        "jarvis.missions.workers.claude_direct_worker._resolve_claude_binary",
        lambda: "/usr/local/bin/claude",
    )
    monkeypatch.setattr(
        "jarvis.missions.workers.codex_direct_worker._codex_oauth_available", lambda: False
    )
    mark_claude_quota_cooldown()
    monkeypatch.setattr(
        "jarvis.brain.provider_registry.BrainProviderRegistry.get_class",
        lambda self, _p: _FakeCriticBrain,
    )
    monkeypatch.setattr("jarvis.core.config.get_jarvis_agent_secret", lambda _p: "FAKE")


async def _critic_review(tmp_path: Path, gate: PaidCallGate | None) -> CriticVerdict:
    from jarvis.missions.critic.runner import CriticRunner

    return await CriticRunner().run(
        mission_prompt="Build X",
        worker_diff="diff --git a/x b/x\n+x\n",
        worker_log="log",
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={},
        paid_gate=gate,
    )


def _gate(
    tmp_path: Path, decision: list[RouteDecision], ledger: MissionPaidLedger
) -> PaidCallGate:
    return PaidCallGate(
        mission_id="m",
        offer=OFFER,
        ledger=ledger,
        daily=DailyPaidLedger(tmp_path / "ledger.json"),
        authorize=lambda: decision[0],
    )


async def test_the_paid_critic_reserves_and_books_each_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from jarvis.claude_quota_state import clear_claude_quota_cooldown

    _subscriptions_spent(monkeypatch)
    try:
        ledger = MissionPaidLedger()
        gate = _gate(tmp_path, [RouteDecision("paid", automatic=True)], ledger)
        verdict = await _critic_review(tmp_path, gate)
        assert verdict.verdict == "approve"
        assert gate.calls == 1
        assert 0 < ledger.spent_usd < 0.05  # 1000 in + 200 out on Sonnet
        assert ledger.remaining(automatic=True) == pytest.approx(
            PAID_MISSION_CAP_USD - ledger.spent_usd
        )
    finally:
        clear_claude_quota_cooldown()


async def test_the_paid_critic_stops_when_consent_is_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from jarvis.claude_quota_state import clear_claude_quota_cooldown

    _subscriptions_spent(monkeypatch)
    _FakeCriticBrain.calls = 0
    try:
        ledger = MissionPaidLedger()
        gate = _gate(tmp_path, [RouteDecision("park", "paid_consent_revoked")], ledger)
        with pytest.raises(CriticCapacityUnavailable) as exc:
            await _critic_review(tmp_path, gate)
        assert exc.value.reason == "paid_consent_revoked"
        assert _FakeCriticBrain.calls == 0 and ledger.spent_usd == 0.0
        # No gate at all (setting OFF, no approval): the critic parks.
        with pytest.raises(CriticCapacityUnavailable) as no_gate:
            await _critic_review(tmp_path, None)
        assert no_gate.value.reason == "provider_quota"
    finally:
        clear_claude_quota_cooldown()


async def test_a_usage_less_paid_critic_call_counts_its_reservation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from jarvis.claude_quota_state import clear_claude_quota_cooldown

    _subscriptions_spent(monkeypatch)
    monkeypatch.setattr(_FakeCriticBrain, "usage", None)
    try:
        ledger = MissionPaidLedger()
        gate = _gate(tmp_path, [RouteDecision("paid", automatic=True)], ledger)
        await _critic_review(tmp_path, gate)
        # Booked at the reservation: every prompt byte as a cache-write token
        # plus the full 2048-token output budget.
        assert ledger.spent_usd >= 2048 * 15 / 1_000_000
    finally:
        clear_claude_quota_cooldown()


def test_no_waits_are_lost_in_the_vocabulary() -> None:
    from typing import get_args

    from jarvis.missions.capacity import CapacityWaitReason
    from jarvis.missions.events import CAPACITY_WAIT_REASONS

    assert set(get_args(CapacityWaitReason)) == set(CAPACITY_WAIT_REASONS)
    assert {"paid_daily_cap_reached", "paid_consent_revoked"} <= set(CAPACITY_WAIT_REASONS)
