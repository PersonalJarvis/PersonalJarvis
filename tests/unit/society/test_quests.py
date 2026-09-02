"""The Quest Board: routing, forging, the board-driven lifecycle, the push."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.society.capabilities import CapabilityKind, CapabilityRow
from jarvis.society.events import MsgType, QuestState, SocietyEnvelope, Tier
from jarvis.society.quests import GENERALIST, choose_taker, quest_title
from jarvis.society.roster import AgentRecord
from jarvis.society.runtime import SocietyRuntime


def _cap(cap_id: str, label: str, *, connected: bool = True) -> CapabilityRow:
    kind, _, name = cap_id.partition(":")
    return CapabilityRow(
        id=cap_id,
        kind=CapabilityKind(kind),
        tool_name=name,
        label=label,
        one_liner="",
        risk_tier="safe",
        connected=connected,
        aliases=(),
    )


CATALOG = [
    _cap("plugin:gmail", "Gmail"),
    _cap("plugin:google-calendar", "Google Calendar"),
    _cap("core:search-web", "Search the web"),
]


class FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, target: AgentRecord, env: SocietyEnvelope) -> str:
        self.calls.append((target.agent_id, env.trace_id))
        return f"run-{len(self.calls)}"


@pytest.fixture
async def rt(tmp_path: Path):
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    runtime.catalog = lambda: CATALOG  # type: ignore[method-assign]
    await runtime.ensure_started()
    dispatcher = FakeDispatcher()
    runtime.scheduler._dispatch = dispatcher  # noqa: SLF001 — swap the hook for the fake
    runtime.dispatcher = dispatcher  # type: ignore[attr-defined]
    pushed: list = []
    runtime.quests._publish = pushed.append  # noqa: SLF001
    runtime.pushed = pushed  # type: ignore[attr-defined]
    try:
        yield runtime
    finally:
        await runtime.close()


# ------------------------------------------------------------------ routing


def test_title_is_the_first_line_trimmed():
    assert quest_title("Find the five most important mails\nfrom today") == (
        "Find the five most important mails"
    )
    assert quest_title("x" * 100).endswith("…")
    assert quest_title("   ", "Given") == "Given"
    assert quest_title("") == "Quest"


async def test_focus_match_picks_the_specialist(rt: SocietyRuntime):
    mailbox, _ = await rt.roster.create(
        name="Mailbox", title="Mail assistant", focus=["plugin:gmail"]
    )
    await rt.roster.create(
        name="Planner", title="Calendar assistant", focus=["plugin:google-calendar"]
    )
    agents = await rt.roster.list()
    choice = choose_taker("Summarize the five most important emails of today", agents, CATALOG)
    assert choice.agent_id == mailbox.agent_id
    assert choice.reason == "focus-match"
    assert choice.forge is None


async def test_name_in_the_quest_wins_without_focus(rt: SocietyRuntime):
    scout, _ = await rt.roster.create(
        name="Scout", title="Research coordinator", tier=Tier.ORCHESTRATOR
    )
    await rt.roster.create(name="Archivist", title="Knowledge curator")
    agents = await rt.roster.list()
    choice = choose_taker("Scout, what is new about solid-state batteries?", agents, CATALOG)
    assert choice.agent_id == scout.agent_id
    assert choice.reason == "keyword-match"


async def test_the_lead_never_takes_a_quest(rt: SocietyRuntime):
    agents = await rt.roster.list()  # only Jarvis
    choice = choose_taker("Jarvis, tidy the workshop", agents, CATALOG)
    assert choice.agent_id is None
    assert choice.forge is not None and choice.forge["name"] == GENERALIST["name"]
    assert choice.reason == "forged:generalist"


async def test_nobody_fits_forges_the_seed_for_the_capability(rt: SocietyRuntime):
    await rt.roster.create(name="Archivist", title="Knowledge curator")
    agents = await rt.roster.list()
    choice = choose_taker("Sort my inbox and draft replies", agents, CATALOG)
    assert choice.forge is not None and choice.forge["name"] == "Mailbox"
    assert choice.reason == "forged:plugin:gmail"
    assert "plugin:gmail" in choice.forge["focus"]


async def test_a_disconnected_capability_forges_nothing_specific(rt: SocietyRuntime):
    catalog = [_cap("plugin:gmail", "Gmail", connected=False)]
    agents = await rt.roster.list()
    choice = choose_taker("Sort my inbox", agents, catalog)
    assert choice.forge is not None and choice.forge["name"] == GENERALIST["name"]


async def test_busy_agents_lose_points_and_the_generalist_is_reused(rt: SocietyRuntime):
    runner, _ = await rt.roster.create(name="Runner", title="Errand runner")
    agents = await rt.roster.list()
    choice = choose_taker("Water the plants", agents, CATALOG)
    assert choice.agent_id == runner.agent_id and choice.reason == "generalist"
    a, _ = await rt.roster.create(name="Alpha", focus=["plugin:gmail"])
    b, _ = await rt.roster.create(name="Beta", focus=["plugin:gmail"])
    agents = await rt.roster.list()
    choice = choose_taker("mails", agents, CATALOG, busy={a.agent_id: 5})
    assert choice.agent_id == b.agent_id


# ---------------------------------------------------------------- lifecycle


async def test_create_routes_assigns_and_the_claim_makes_it_running(rt: SocietyRuntime):
    mailbox, _ = await rt.roster.create(name="Mailbox", focus=["plugin:gmail"])
    quest = await rt.quests.create("Find the five most important mails of today")
    assert quest.state is QuestState.RUNNING
    assert quest.agent_id == mailbox.agent_id
    assert quest.run_id == "run-1"
    assert quest.routing["reason"] == "focus-match"
    assert rt.dispatcher.calls == [(mailbox.agent_id, quest.trace_id)]  # type: ignore[attr-defined]
    events = await rt.store.events_for_trace(quest.trace_id)
    assert [e.msg_type for e in events] == [MsgType.ASSIGN, MsgType.CLAIM]
    assert events[0].payload["quest_id"] == quest.quest_id
    states = [(p.state, p.previous) for p in rt.pushed]  # type: ignore[attr-defined]
    assert states == [("open", ""), ("assigned", "open"), ("running", "assigned")]


async def test_result_closes_the_quest(rt: SocietyRuntime):
    mailbox, _ = await rt.roster.create(name="Mailbox", focus=["plugin:gmail"])
    quest = await rt.quests.create("Sort my mails")
    await rt.store.append_and_publish(
        SocietyEnvelope(
            msg_type=MsgType.RESULT,
            from_agent=mailbox.agent_id,
            to_agent=None,
            trace_id=quest.trace_id,
            payload={
                "run_id": quest.run_id,
                "status": "done",
                "done": "Five mails summarized.",
                "output": ["chat:society:mailbox"],
                "text": "Five mails summarized.",
            },
        )
    )
    fresh = await rt.quests.get(quest.quest_id)
    assert fresh is not None and fresh.state is QuestState.DONE
    assert fresh.result["done"] == "Five mails summarized."
    assert fresh.done_ms is not None
    assert rt.scheduler.active_runs(mailbox.agent_id) == 0


async def test_blocked_result_and_veto_fail_the_quest_and_retry_routes_again(rt: SocietyRuntime):
    mailbox, _ = await rt.roster.create(
        name="Mailbox", focus=["plugin:gmail"], max_concurrent_runs=1
    )
    first = await rt.quests.create("Sort my mails")
    # The one slot is taken: the next quest is vetoed by the concurrency cap.
    second = await rt.quests.create("Archive old mails")
    assert second.state is QuestState.FAILED
    assert second.result["status"] == "vetoed"
    assert second.result["reason"] == "concurrency_cap"
    # The first one ends blocked.
    await rt.store.append_and_publish(
        SocietyEnvelope(
            msg_type=MsgType.RESULT,
            from_agent=mailbox.agent_id,
            trace_id=first.trace_id,
            payload={
                "run_id": first.run_id,
                "status": "blocked",
                "done": "no login",
                "open": ["login"],
            },
        )
    )
    assert (await rt.quests.get(first.quest_id)).state is QuestState.FAILED  # type: ignore[union-attr]
    # Retry: the slot is free again, the second quest runs.
    retried = await rt.quests.retry(second.quest_id)
    assert retried.state is QuestState.RUNNING
    assert retried.result == {}


async def test_cancel_frees_the_slot_and_ignores_the_late_result(rt: SocietyRuntime):
    mailbox, _ = await rt.roster.create(name="Mailbox", focus=["plugin:gmail"])
    quest = await rt.quests.create("Sort my mails")
    cancelled = await rt.quests.cancel(quest.quest_id)
    assert cancelled.state is QuestState.CANCELLED
    assert rt.scheduler.active_runs(mailbox.agent_id) == 0
    await rt.store.append_and_publish(
        SocietyEnvelope(
            msg_type=MsgType.RESULT,
            from_agent=mailbox.agent_id,
            trace_id=quest.trace_id,
            payload={"run_id": quest.run_id, "status": "done", "done": "late", "output": ["x"]},
        )
    )
    assert (await rt.quests.get(quest.quest_id)).state is QuestState.CANCELLED  # type: ignore[union-attr]
    assert await rt.quests.cancel(quest.quest_id) is not None  # idempotent


async def test_forging_creates_the_teammate_on_the_roster(rt: SocietyRuntime):
    quest = await rt.quests.create("Summarize my inbox")
    assert quest.state is QuestState.RUNNING
    forged = await rt.roster.get("mailbox")
    assert forged is not None and forged.tier is Tier.SPECIALIST
    assert "plugin:gmail" in forged.focus
    assert quest.agent_id == "mailbox"
    assert quest.routing["forged"] is True
    # A second mail quest reuses it instead of forging again.
    again = await rt.quests.create("Reply to yesterday's mails", title="Replies")
    assert again.agent_id == "mailbox" and again.title == "Replies"
    assert again.routing["forged"] is False


async def test_status_counts_open_quests(rt: SocietyRuntime):
    await rt.quests.create("Water the plants")
    status = await rt.status()
    assert status["quests_open"] == 1
    listed = await rt.quests.list(state=QuestState.RUNNING)
    assert len(listed) == 1 and listed[0].agent_id == "runner"
