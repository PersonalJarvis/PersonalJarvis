"""Running agents follow a seat switch, and a spent seat hands its work on.

The registry here is a small stand-in with the four things the switch uses
(``sessions``, ``turn_in_progress``, ``move_to_seat``, ``send_prompt``); the
real restart path is covered by the Agentic-IDE suites. What these pin down is
the policy: who moves now, who waits for their turn, who never moves, and
where the work goes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis import agent_accounts
from jarvis.agent_accounts import AccountSnapshot, AgentAccount
from jarvis.agent_usage import AccountUsage, UsageWindow
from jarvis.agentic_ide import seat_switch


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_accounts, "_store_path", lambda: tmp_path / "accounts.json")
    monkeypatch.setattr(agent_accounts, "_accounts_root", lambda: tmp_path / "dirs")
    monkeypatch.setattr(
        agent_accounts, "_native_dir", lambda platform: tmp_path / "native" / platform
    )
    monkeypatch.setattr(seat_switch, "_handled_path", lambda: tmp_path / "handled.json")
    seat_switch.reset()
    yield
    seat_switch.reset()


@dataclass
class _Pane:
    name: str
    account: str
    agent: str = "claude"
    status: str = "live"
    pty_id: str | None = "pty"
    computer_id: str = ""
    history_id: str = ""
    pending_account: str | None = None
    last_input_at: float | None = None
    busy: str = ""
    resume: Any = None
    limit_handled_at: float = 0.0

    def __post_init__(self) -> None:
        if self.resume is None:
            self.resume = SimpleNamespace(id=f"conv-{self.name}", captured_at=0.0)


@dataclass
class _Workspace:
    id: str
    terminals: list[_Pane]


@dataclass
class _Registry:
    sessions: list[_Workspace]
    moves: list[tuple[str, str]] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)

    async def turn_in_progress(self, term: _Pane) -> str:
        return term.busy

    async def move_to_seat(self, term: _Pane, account_id: str, *, idle: Any = None) -> str:
        live = term.status == "live"
        if live and idle is not None and not await idle(term):
            term.pending_account = account_id
            return "deferred"
        self.moves.append((term.name, account_id))
        term.account = account_id
        term.pending_account = None
        return "moved" if live else "repointed"

    async def send_prompt(self, wanted: str, text: str, **_kwargs: Any) -> None:
        self.prompts.append(wanted)


def _seats(email_of: dict[str, str | None]) -> list[AgentAccount]:
    seats = [agent_accounts.active_account("claude")]
    for label in email_of:
        if label != "default":
            seats.append(agent_accounts.create_account("claude", label))
    return seats


def _fake_snapshots(
    monkeypatch: pytest.MonkeyPatch, seats: list[AgentAccount], emails: list[str | None]
) -> None:
    def snapshots(platform: str) -> list[AccountSnapshot]:
        return [
            AccountSnapshot(
                account=seat,
                connected=True,
                mode="subscription",
                message="",
                email=email,
            )
            for seat, email in zip(seats, emails, strict=True)
        ]

    monkeypatch.setattr(agent_accounts, "snapshots", snapshots)


def _usage(account: AgentAccount, percent: float) -> AccountUsage:
    return AccountUsage(
        account_id=account.id,
        platform="claude",
        status="ok",
        windows=(UsageWindow(kind="session", percent=percent, severity="normal"),),
    )


def test_one_click_moves_idle_agents_now_and_working_ones_after_their_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seats = _seats({"default": None, "Second": None, "Third": None})
    default, second, third = seats
    _fake_snapshots(monkeypatch, seats, ["a@x", "b@x", "c@x"])
    idle = _Pane("T1", default.id)
    working = _Pane("T2", default.id, busy="working")
    stopped = _Pane("T3", third.id, status="exited", pty_id=None)
    remote = _Pane("T4", default.id, computer_id="vps")
    registry = _Registry([_Workspace("w", [idle, working, stopped, remote])])

    event = asyncio.run(seat_switch.switch_seat(registry, "claude", second.id))

    assert agent_accounts.active_account("claude").id == second.id
    assert sorted(registry.moves) == [("T1", second.id), ("T3", second.id)]
    assert working.pending_account == second.id
    assert remote.account == default.id  # its CLI uses the server's own login
    assert (event.moved, event.queued) == (2, 1)
    assert registry.prompts == []  # nobody was cut off, nobody is told anything


def test_a_pane_being_typed_into_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    seats = _seats({"default": None, "Second": None})
    _fake_snapshots(monkeypatch, seats, ["a@x", "b@x"])
    typing = _Pane("T1", seats[0].id, last_input_at=seat_switch._now())
    registry = _Registry([_Workspace("w", [typing])])

    asyncio.run(seat_switch.switch_seat(registry, "claude", seats[1].id))

    assert registry.moves == []
    assert typing.pending_account == seats[1].id


def test_panes_on_the_same_login_as_the_target_stay_put(monkeypatch: pytest.MonkeyPatch) -> None:
    seats = _seats({"default": None, "Twin": None})
    default, twin = seats
    _fake_snapshots(monkeypatch, seats, ["same@x", "same@x"])
    pane = _Pane("T1", default.id)
    registry = _Registry([_Workspace("w", [pane])])

    asyncio.run(seat_switch.switch_seat(registry, "claude", twin.id))

    assert registry.moves == []  # one subscription under two names: nothing to gain


def test_the_next_seat_is_a_different_login_with_the_most_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seats = _seats({"default": None, "Twin": None, "Busy": None, "Fresh": None})
    default, twin, busy, fresh = seats
    _fake_snapshots(monkeypatch, seats, ["a@x", "a@x", "b@x", "c@x"])
    usage = {
        default.id: _usage(default, 99),
        twin.id: _usage(twin, 99),
        busy.id: _usage(busy, 60),
        fresh.id: _usage(fresh, 10),
    }
    assert seat_switch.choose_seat("claude", {default.id}, usage, 97).id == fresh.id
    # The active seat wins whenever it is eligible at all.
    assert seat_switch.choose_seat("claude", {default.id}, usage, 97, prefer=busy.id).id == busy.id
    # A seat past the threshold, or out of rotation, is never the answer.
    usage[fresh.id] = _usage(fresh, 98)
    seat_switch._STATE.exhausted[busy.id] = seat_switch._now() + 60
    assert seat_switch.choose_seat("claude", {default.id}, usage, 97) is None


def test_a_window_that_already_reset_counts_as_empty() -> None:
    account = agent_accounts.active_account("claude")
    reading = AccountUsage(
        account_id=account.id,
        platform="claude",
        status="ok",
        windows=(
            UsageWindow(
                kind="session", percent=100, severity="critical", resets_at="2020-01-01T00:00:00Z"
            ),
            UsageWindow(kind="weekly", percent=40, severity="normal"),
        ),
    )
    assert seat_switch.spent_percent(reading) == 40


def test_a_limit_stop_moves_the_work_and_tells_it_to_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seats = _seats({"default": None, "Second": None, "Other": None})
    default, second, other = seats
    _fake_snapshots(monkeypatch, seats, ["a@x", "b@x", "c@x"])
    agent_accounts.set_active("claude", second.id)
    cut_off = _Pane("T1", default.id, history_id="h1", busy="working")
    sibling = _Pane("T2", default.id, history_id="h2")
    elsewhere = _Pane("T3", other.id, history_id="h3")
    registry = _Registry([_Workspace("w", [cut_off, sibling, elsewhere])])
    monkeypatch.setattr(
        seat_switch, "_limit_stops", lambda _r: [(registry.sessions[0], cut_off, 1000.0)]
    )
    monkeypatch.setattr(seat_switch, "_cached_usage", lambda _p: {})

    async def run() -> None:
        await seat_switch._handle_limit_stops(registry, 97.0)
        await asyncio.sleep(0)  # let the continue prompt go out

    asyncio.run(run())

    # The active seat had budget, so the work went there — not to a third seat.
    assert sorted(registry.moves) == [("T1", second.id), ("T2", second.id)]
    assert elsewhere.account == other.id  # a healthy seat's work is not touched
    assert registry.prompts == ["pane:h1"]
    assert cut_off.limit_handled_at == 1000.0
    assert seat_switch.is_exhausted(default.id)


def test_with_no_seat_left_nothing_moves_and_it_is_said_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seats = _seats({"default": None, "Second": None})
    default, second = seats
    _fake_snapshots(monkeypatch, seats, ["a@x", "b@x"])
    pane = _Pane("T1", default.id, history_id="h1")
    registry = _Registry([_Workspace("w", [pane])])
    seat_switch._STATE.exhausted[second.id] = seat_switch._now() + 600
    monkeypatch.setattr(
        seat_switch, "_limit_stops", lambda _r: [(registry.sessions[0], pane, 1000.0)]
    )
    monkeypatch.setattr(seat_switch, "_cached_usage", lambda _p: {})

    asyncio.run(seat_switch._handle_limit_stops(registry, 97.0))
    asyncio.run(seat_switch._handle_limit_stops(registry, 97.0))

    assert registry.moves == []
    reports = [e for e in seat_switch.status()["events"] if e["reason"] == "no_seat"]
    assert len(reports) == 1


def test_a_nearly_spent_seat_hands_its_idle_work_on(monkeypatch: pytest.MonkeyPatch) -> None:
    seats = _seats({"default": None, "Second": None})
    default, second = seats
    _fake_snapshots(monkeypatch, seats, ["a@x", "b@x"])
    pane = _Pane("T1", default.id)
    working = _Pane("T2", default.id, busy="working")
    registry = _Registry([_Workspace("w", [pane, working])])
    readings = {default.id: _usage(default, 98), second.id: _usage(second, 20)}
    monkeypatch.setattr(seat_switch, "_cached_usage", lambda _p: readings)

    asyncio.run(seat_switch._check_thresholds(registry, 97.0))

    assert agent_accounts.active_account("claude").id == second.id
    assert registry.moves == [("T1", second.id)]
    assert working.pending_account == second.id
    assert registry.prompts == []


def test_a_thread_on_a_spent_seat_runs_on_the_active_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_accounts,
        "describe",
        lambda account: AccountSnapshot(
            account=account, connected=True, mode="subscription", message=""
        ),
    )
    default = agent_accounts.active_account("claude")
    second = agent_accounts.create_account("claude", "Second")
    agent_accounts.set_active("claude", second.id)
    assert seat_switch.preferred_seat("claude", default.id) is None
    seat_switch._STATE.exhausted[default.id] = seat_switch._now() + 60
    assert seat_switch.preferred_seat("claude", default.id) == second.id
    agent_accounts.set_auto_switch(enabled=False)
    assert seat_switch.preferred_seat("claude", default.id) is None


def test_auto_switch_setting_defaults_on_clamps_and_survives_other_writes() -> None:
    assert agent_accounts.auto_switch().enabled is True
    assert agent_accounts.auto_switch().at_percent == agent_accounts.DEFAULT_SWITCH_AT_PERCENT
    assert agent_accounts.set_auto_switch(at_percent=10).at_percent == 50.0
    agent_accounts.set_auto_switch(enabled=False, at_percent=95)
    agent_accounts.create_account("claude", "Later")  # another writer of the store
    choice = agent_accounts.auto_switch()
    assert (choice.enabled, choice.at_percent) == (False, 95.0)


def test_a_one_model_budget_does_not_move_all_work_ahead_of_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seats = _seats({"default": None, "Second": None})
    default, second = seats
    _fake_snapshots(monkeypatch, seats, ["a@x", "b@x"])
    pane = _Pane("T1", default.id)
    registry = _Registry([_Workspace("w", [pane])])
    scoped = AccountUsage(
        account_id=default.id,
        platform="claude",
        status="ok",
        windows=(
            UsageWindow(kind="session", percent=20, severity="normal"),
            UsageWindow(kind="weekly_scoped", percent=100, severity="critical", scope_label="M"),
        ),
    )
    readings = {default.id: scoped, second.id: _usage(second, 5)}
    monkeypatch.setattr(seat_switch, "_cached_usage", lambda _p: readings)

    asyncio.run(seat_switch._check_thresholds(registry, 97.0))

    # Agents on other models still have the whole plan; a turn that does run
    # into the one-model budget is refused and moved by the limit-stop path.
    assert registry.moves == []
    assert seat_switch.spent_percent(scoped) == 100
    assert seat_switch.spent_percent(scoped, scoped=False) == 20


def test_an_answered_stop_survives_a_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    seats = _seats({"default": None, "Second": None})
    default, second = seats
    _fake_snapshots(monkeypatch, seats, ["a@x", "b@x"])
    pane = _Pane("T1", default.id, history_id="h1")
    registry = _Registry([_Workspace("w", [pane])])
    monkeypatch.setattr(
        seat_switch, "_limit_stops", lambda _r: [(registry.sessions[0], pane, 1000.0)]
    )
    monkeypatch.setattr(seat_switch, "_cached_usage", lambda _p: {})
    asyncio.run(seat_switch._handle_limit_stops(registry, 97.0))
    assert pane.account == second.id

    # A restarted app has fresh panes and an empty memory, but the stop that
    # was answered must not move the work a second time.
    monkeypatch.setattr(seat_switch, "_HANDLED", None)
    assert seat_switch.handled_stop(pane.resume.id) == 1000.0


def test_a_pane_that_became_busy_is_not_cut_off(monkeypatch: pytest.MonkeyPatch) -> None:
    seats = _seats({"default": None, "Second": None})
    _fake_snapshots(monkeypatch, seats, ["a@x", "b@x"])
    pane = _Pane("T1", seats[0].id)
    registry = _Registry([_Workspace("w", [pane])])
    original = registry.move_to_seat

    async def prompt_lands_first(term: _Pane, account_id: str, *, idle: Any = None) -> str:
        term.busy = "working"  # a prompt arrived while the switch was on its way
        return await original(term, account_id, idle=idle)

    monkeypatch.setattr(registry, "move_to_seat", prompt_lands_first)
    event = asyncio.run(seat_switch.switch_seat(registry, "claude", seats[1].id))

    assert registry.moves == []
    assert pane.pending_account == seats[1].id
    assert (event.moved, event.queued) == (0, 1)


def test_a_manual_pick_is_not_undone_by_the_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    seats = _seats({"default": None, "Second": None})
    default, second = seats
    _fake_snapshots(monkeypatch, seats, ["a@x", "b@x"])
    pane = _Pane("T1", default.id)
    registry = _Registry([_Workspace("w", [pane])])
    asyncio.run(seat_switch.switch_seat(registry, "claude", default.id))
    readings = {default.id: _usage(default, 98), second.id: _usage(second, 5)}
    monkeypatch.setattr(seat_switch, "_cached_usage", lambda _p: readings)

    asyncio.run(seat_switch._check_thresholds(registry, 97.0))

    assert registry.moves == []
    assert agent_accounts.active_account("claude").id == default.id


def test_a_thread_is_never_rerouted_onto_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    default = agent_accounts.active_account("claude")
    keyed = agent_accounts.create_account("claude", "Key")
    agent_accounts.set_active("claude", keyed.id)
    seat_switch._STATE.exhausted[default.id] = seat_switch._now() + 60
    monkeypatch.setattr(
        agent_accounts,
        "describe",
        lambda account: AccountSnapshot(
            account=account, connected=True, mode="api_key", message=""
        ),
    )
    assert seat_switch.preferred_seat("claude", default.id) is None


def test_with_nowhere_to_go_a_refilled_seat_carries_on_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seats = _seats({"default": None, "Second": None})
    default, second = seats
    _fake_snapshots(monkeypatch, seats, ["a@x", "b@x"])
    pane = _Pane("T1", default.id, history_id="h1")
    registry = _Registry([_Workspace("w", [pane])])
    seat_switch._STATE.exhausted[second.id] = seat_switch._now() + 600
    monkeypatch.setattr(
        seat_switch, "_limit_stops", lambda _r: [(registry.sessions[0], pane, 1000.0)]
    )
    readings: dict[str, AccountUsage] = {default.id: _usage(default, 100)}
    monkeypatch.setattr(seat_switch, "_cached_usage", lambda _p: readings)

    async def run() -> None:
        await seat_switch._handle_limit_stops(registry, 97.0)  # still spent: waits
        assert registry.prompts == []
        seat_switch._STATE.exhausted[default.id] = seat_switch._now() - 1  # refill time passed
        readings[default.id] = _usage(default, 3)  # and the reading agrees
        await seat_switch._handle_limit_stops(registry, 97.0)
        await asyncio.sleep(0)

    asyncio.run(run())

    assert registry.moves == []
    assert registry.prompts == ["pane:h1"]
    assert seat_switch.handled_stop(pane.resume.id) == 1000.0
