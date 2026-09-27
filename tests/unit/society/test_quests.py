"""The Quest Board: routing, forging, the board-driven lifecycle, the push."""

from __future__ import annotations

import asyncio
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

    async def lexical_focus(_runtime, text, catalog):
        from jarvis.society.focus import derive_focus

        return derive_focus("", text, catalog)

    runtime.quests._infer_focus = lexical_focus  # noqa: SLF001 - deterministic fixture
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


@pytest.mark.parametrize(
    "task_text",
    [
        "Summarize my inbox today.",
        "Fasse heute mein Postfach zusammen.",
        "Resume mi bandeja de entrada hoy.",
        "今日の受信トレイを要約して。",
    ],
)
async def test_semantic_capability_hint_routes_all_scripts_through_trusted_scheduler(
    rt: SocietyRuntime, task_text: str
):
    mailbox, _ = await rt.roster.create(name="Mailbox", focus=["plugin:gmail"])

    async def semantic_focus(_runtime, task, catalog):
        assert task == task_text
        assert any(row.id == "plugin:gmail" for row in catalog)
        return ["plugin:gmail"]

    rt.quests._infer_focus = semantic_focus  # noqa: SLF001 - fake model hint
    quest = await rt.quests.create(task_text)
    assert quest.state is QuestState.RUNNING
    assert quest.agent_id == mailbox.agent_id
    assert quest.routing["reason"] == "focus-match"


async def test_unavailable_multilingual_hint_still_starts_a_generalist(rt: SocietyRuntime):
    async def unavailable(_runtime, _task, _catalog):
        raise RuntimeError("provider unavailable")

    rt.quests._infer_focus = unavailable  # noqa: SLF001 - simulate a provider failure
    quest = await rt.quests.create("今日の受信トレイを要約して。")
    assert quest.state is QuestState.RUNNING
    assert quest.agent_id == "runner"
    assert quest.routing["reason"] == "forged:generalist"


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


async def test_deferred_create_returns_before_semantic_routing_finishes(rt: SocietyRuntime):
    await rt.roster.create(name="Mailbox", focus=["plugin:gmail"])
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_focus(_runtime, _task, _catalog):
        started.set()
        await release.wait()
        return ["plugin:gmail"]

    rt.quests._infer_focus = slow_focus  # noqa: SLF001 - controlled async classifier
    quest = await rt.quests.create("Summarize my inbox", defer=True)
    assert quest.state is QuestState.OPEN
    await asyncio.wait_for(started.wait(), timeout=1)
    assert rt.dispatcher.calls == []  # type: ignore[attr-defined]
    release.set()
    for _ in range(200):
        current = await rt.quests.get(quest.quest_id)
        if current is not None and current.state is QuestState.RUNNING:
            break
        await asyncio.sleep(0.01)
    assert current is not None and current.state is QuestState.RUNNING
    assert current.agent_id == "mailbox"


async def test_cancelling_while_routing_does_not_assign_or_forge(rt: SocietyRuntime):
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_focus(_runtime, _task, _catalog):
        started.set()
        await release.wait()
        return []

    rt.quests._infer_focus = slow_focus  # noqa: SLF001 - controlled async classifier
    quest = await rt.quests.create("Write a note", defer=True)
    await asyncio.wait_for(started.wait(), timeout=1)
    await rt.quests.cancel(quest.quest_id)
    release.set()
    for _ in range(20):
        if quest.quest_id not in rt.quests._routing:  # noqa: SLF001 - wait for route cleanup
            break
        await asyncio.sleep(0.01)
    assert (await rt.quests.get(quest.quest_id)).state is QuestState.CANCELLED  # type: ignore[union-attr]
    assert rt.dispatcher.calls == []  # type: ignore[attr-defined]
    assert await rt.roster.get("runner") is None


async def test_unrouted_receipt_survives_process_restart(rt: SocietyRuntime, monkeypatch):
    rt.quests._retry_delay = 0.2  # noqa: SLF001 - exercise the durable timer
    monkeypatch.setattr("jarvis.society.quests.secrets.randbelow", lambda _max: 0)
    started = asyncio.Event()
    release = asyncio.Event()

    async def interrupted_focus(_runtime, _task, _catalog):
        started.set()
        await release.wait()
        return []

    rt.quests._infer_focus = interrupted_focus  # noqa: SLF001 - simulate an interrupted hint
    quest = await rt.quests.create("Write a short note", defer=True)
    await asyncio.wait_for(started.wait(), timeout=1)
    rt.quests.detach()
    await asyncio.sleep(0)

    async def recovered_focus(_runtime, _task, _catalog):
        return []

    rt.quests._infer_focus = recovered_focus  # noqa: SLF001 - resumed process
    rt.quests.attach()
    for _ in range(200):
        current = await rt.quests.get(quest.quest_id)
        if current is not None and current.state is QuestState.RUNNING:
            break
        await asyncio.sleep(0.01)
    assert current is not None and current.state is QuestState.RUNNING
    assert current.agent_id == "runner"


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


async def test_brain_startup_retries_without_user_intervention(rt: SocietyRuntime, monkeypatch):
    rt.quests._retry_delay = 0.2  # noqa: SLF001 - bound the timer in this contract
    monkeypatch.setattr("jarvis.society.quests.secrets.randbelow", lambda _max: 0)
    quest = await rt.quests.create("Summarize my inbox")
    assert quest.state is QuestState.RUNNING
    rt.scheduler.note_run_ended(quest.run_id)
    await rt.store.append_and_publish(
        SocietyEnvelope(
            msg_type=MsgType.RESULT,
            from_agent=quest.agent_id,
            trace_id=quest.trace_id,
            parent_event_id=quest.assign_event_id,
            payload={
                "run_id": quest.run_id,
                "status": "blocked",
                "retry_reason": "brain_starting",
                "done": "The agent is still starting.",
                "output": ["chat:society:mailbox"],
            },
        )
    )
    waiting = await rt.quests.get(quest.quest_id)
    assert waiting is not None and waiting.state is QuestState.OPEN
    assert waiting.result["status"] == "waiting"
    assert waiting.result["reason"] == "brain_starting"
    for _ in range(200):
        current = await rt.quests.get(quest.quest_id)
        if len(rt.dispatcher.calls) == 2 and current.state is QuestState.RUNNING:  # type: ignore[attr-defined]
            break
        await asyncio.sleep(0.01)
    after_retry = await rt.quests.get(quest.quest_id)
    trace_events = await rt.store.events_for_trace(quest.trace_id)
    assert len(rt.dispatcher.calls) == 2, (  # type: ignore[attr-defined]
        after_retry.state if after_retry else None,
        after_retry.result if after_retry else None,
        [event.msg_type for event in trace_events],
    )
    retried = await rt.quests.get(quest.quest_id)
    assert retried is not None and retried.state is QuestState.RUNNING


async def test_waiting_startup_task_recovers_after_restart(rt: SocietyRuntime, monkeypatch):
    rt.quests._retry_delay = 0.2  # noqa: SLF001 - exercise a short durable wait
    monkeypatch.setattr("jarvis.society.quests.secrets.randbelow", lambda _max: 0)
    quest = await rt.quests.create("Summarize my inbox")
    rt.scheduler.note_run_ended(quest.run_id)
    await rt.store.append_and_publish(
        SocietyEnvelope(
            msg_type=MsgType.RESULT,
            from_agent=quest.agent_id,
            trace_id=quest.trace_id,
            parent_event_id=quest.assign_event_id,
            payload={
                "run_id": quest.run_id,
                "status": "blocked",
                "retry_reason": "brain_starting",
                "done": "The agent is still starting.",
                "output": ["chat:society:mailbox"],
            },
        )
    )
    rt.quests.detach()
    assert (await rt.quests.get(quest.quest_id)).state is QuestState.OPEN  # type: ignore[union-attr]
    rt.quests.attach()
    for _ in range(200):
        current = await rt.quests.get(quest.quest_id)
        if len(rt.dispatcher.calls) == 2 and current.state is QuestState.RUNNING:  # type: ignore[attr-defined]
            break
        await asyncio.sleep(0.01)
    assert len(rt.dispatcher.calls) == 2  # type: ignore[attr-defined]
    assert (await rt.quests.get(quest.quest_id)).state is QuestState.RUNNING  # type: ignore[union-attr]


async def test_a_busy_taker_makes_the_quest_wait_and_a_freed_slot_starts_it(rt: SocietyRuntime):
    mailbox, _ = await rt.roster.create(
        name="Mailbox", focus=["plugin:gmail"], max_concurrent_runs=1
    )
    first = await rt.quests.create("Sort my mails")
    # The one slot is taken: the next quest waits on the board instead of failing.
    second = await rt.quests.create("Archive old mails")
    assert second.state is QuestState.OPEN
    assert second.result["status"] == "waiting"
    assert second.result["reason"] == "concurrency_cap"
    assert second.quest_id in rt.quests._timers  # noqa: SLF001 — it knocks again by timer
    # The first one ends blocked -> failed; the freed slot starts the waiting one at once.
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
    woken = await rt.quests.get(second.quest_id)
    assert woken is not None and woken.state is QuestState.RUNNING
    assert woken.result == {}
    # A hard refusal still fails: the kill switch is "never", not "not now".
    await rt.store.set_kill_switch(True)
    third = await rt.quests.create("Reply to the newsletter")
    assert third.state is QuestState.FAILED
    assert third.result["reason"] == "kill_switch"
    await rt.store.set_kill_switch(False)
    retried = await rt.quests.retry(third.quest_id)
    # Mailbox is still busy, so the retry lands on the forged generalist.
    assert retried.state is QuestState.RUNNING and retried.agent_id == "runner"


async def test_waiting_quests_knock_again_by_timer(tmp_path: Path):
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    runtime.catalog = lambda: CATALOG  # type: ignore[method-assign]
    await runtime.ensure_started()
    runtime.scheduler._dispatch = FakeDispatcher()  # noqa: SLF001
    runtime.quests._publish = lambda _e: None  # noqa: SLF001
    runtime.quests._retry_delay = 0.05  # noqa: SLF001
    try:
        mailbox, _ = await runtime.roster.create(
            name="Mailbox", focus=["plugin:gmail"], max_concurrent_runs=1
        )
        first = await runtime.quests.create("Sort my mails")
        second = await runtime.quests.create("Archive old mails")
        assert second.state is QuestState.OPEN
        # Free the slot silently (no RESULT): only the timer can notice.
        runtime.scheduler.note_run_ended(first.run_id)
        import asyncio

        await asyncio.sleep(0.3)
        woken = await runtime.quests.get(second.quest_id)
        assert woken is not None and woken.state is QuestState.RUNNING
        assert mailbox.agent_id == woken.agent_id
    finally:
        await runtime.close()


async def test_progress_lines_reach_the_card_while_running(rt: SocietyRuntime):
    await rt.roster.create(name="Mailbox", focus=["plugin:gmail"])
    quest = await rt.quests.create("Sort my mails")
    await rt.quests.note_progress(quest.trace_id, "gmail_list: 40 mails")
    await rt.quests.note_progress(quest.trace_id, "gmail_list: 40 mails")  # duplicate step
    await rt.quests.note_progress(quest.trace_id, "", live="Reading the newest ones…")
    fresh = await rt.quests.get(quest.quest_id)
    assert fresh is not None
    assert fresh.result["progress"] == ["gmail_list: 40 mails"]
    assert fresh.result["live"] == "Reading the newest ones…"
    assert rt.pushed[-1].state == "running"  # type: ignore[attr-defined]
    # A quest that is not running ignores late progress.
    await rt.quests.cancel(quest.quest_id)
    await rt.quests.note_progress(quest.trace_id, "late step")
    assert "late step" not in (await rt.quests.get(quest.quest_id)).result.get("progress", [])  # type: ignore[union-attr]


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
