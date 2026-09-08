"""The society voice tools: ack at once, veto read-back, status without a model."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.plugins.tool.delegate_to_agent import DelegateToAgentTool, SocietyStatusTool
from jarvis.society.events import MsgType
from jarvis.society.runtime import SocietyRuntime


class FakeChat:
    """Enough of AgentChatService for a dispatch: the turn never ends here."""

    def __init__(self, db: Path) -> None:
        from jarvis.agent_chat.store import AgentChatStore

        self.store = AgentChatStore(db)
        self.sent: list[tuple[str, str]] = []

    def is_running(self, session_id: str) -> bool:
        return False

    def subscribe(self, session_id: str):
        import asyncio

        return asyncio.Queue()

    def unsubscribe(self, session_id: str, q) -> None:
        pass

    async def send(self, session_id: str, text: str, attachments=None) -> str:
        self.sent.append((session_id, text))
        return "turn-1"


def _ctx(utterance: str = "lass Scout das machen") -> SimpleNamespace:
    return SimpleNamespace(trace_id=uuid4(), user_utterance=utterance, config={}, memory_read=None)


@pytest.fixture
async def runtime(tmp_path: Path):
    chat = FakeChat(tmp_path / "agent_chat.db")
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path / "data")))
    rt = SocietyRuntime(tmp_path, chat_service=lambda: chat, cfg=lambda: cfg)
    await rt.ensure_started()
    # Only Jarvis is seeded by default (maintainer, 2026-09-02): the two
    # teammates these tests address are created here.
    await rt.roster.create(name="Scout", provider="openai", model="gpt-5.2")
    await rt.roster.create(name="Archivist", provider="openai", model="gpt-5.2")
    try:
        yield rt, chat
    finally:
        await rt.close()


async def test_delegate_acks_and_assigns(runtime):
    rt, chat = runtime
    tool = DelegateToAgentTool(runtime_resolver=lambda: rt)
    res = await tool.execute(
        {"agent": "Scout", "task": "Find the best VPS.", "turn_language": "de"}, _ctx()
    )
    assert res.success, res.error
    assert res.output["acknowledgement"] == (
        "Scout ist dran, ich sage Bescheid."  # i18n-allow: spoken ack
    )
    assert res.output["state"] == "running" and res.output["agent_id"] == "scout"
    assert res.artifacts[0] == "agent:scout"
    assert res.artifacts[1].startswith("assignment:")
    assert res.artifacts[2].startswith("trace:voice:")
    # The assignment went through the scheduler into Scout's canonical chat.
    assert chat.sent and chat.sent[0][0] == "society:scout"
    assert "Find the best VPS." in chat.sent[0][1]
    events = await rt.store.events_since(0)
    assert [e.msg_type for e in events][-2:] == [MsgType.ASSIGN, MsgType.CLAIM]
    assert events[-2].from_agent == "jarvis"


async def test_delegate_speaks_english_when_the_turn_is_english(runtime):
    rt, _ = runtime
    tool = DelegateToAgentTool(runtime_resolver=lambda: rt)
    res = await tool.execute(
        {"agent": "scout", "task": "Find it.", "turn_language": "en"}, _ctx("let scout find it")
    )
    assert res.output["acknowledgement"] == "Scout is on it, I will let you know."


async def test_delegate_unknown_agent(runtime):
    rt, _ = runtime
    tool = DelegateToAgentTool(runtime_resolver=lambda: rt)
    res = await tool.execute(
        {"agent": "Nobody", "task": "x", "turn_language": "en"}, _ctx("let nobody do x")
    )
    assert res.success is False and res.error == "target_unknown"
    assert "Nobody" in res.output


async def test_delegate_reads_the_veto_back(runtime):
    rt, _ = runtime
    await rt.store.set_kill_switch(True)
    tool = DelegateToAgentTool(runtime_resolver=lambda: rt)
    res = await tool.execute(
        {"agent": "scout", "task": "x", "turn_language": "en"}, _ctx("let scout do x")
    )
    assert res.success is False and res.error == "kill_switch"
    assert res.output["acknowledgement"].startswith("Scout cannot take that right now")
    assert res.output["state"] == "refused" and res.output["assignment_id"]


async def test_not_ready_paths():
    tool = DelegateToAgentTool(runtime_resolver=lambda: None)
    res = await tool.execute(
        {"agent": "scout", "task": "x", "turn_language": "en"}, _ctx("let scout do x")
    )
    assert res.success is False and res.output == "The agents are not ready yet."
    empty = await tool.execute({"agent": "", "task": ""}, _ctx("x"))
    assert empty.success is False

    def _boom():
        raise RuntimeError("no app")

    status = SocietyStatusTool(runtime_resolver=_boom)
    res = await status.execute({}, _ctx("who is on the team"))
    assert res.success is False


async def test_status_answers_from_the_board(runtime):
    rt, _ = runtime
    status = SocietyStatusTool(runtime_resolver=lambda: rt)
    team = await status.execute({"turn_language": "en"}, _ctx("who is on the team"))
    assert team.output == "Your agents: Archivist, Scout."
    idle = await status.execute(
        {"agent": "archivist", "turn_language": "en"}, _ctx("what is archivist doing")
    )
    assert idle.output == "Archivist has nothing to do right now."
    delegate = DelegateToAgentTool(runtime_resolver=lambda: rt)
    await delegate.execute({"agent": "scout", "task": "Find the best VPS."}, _ctx("let scout"))
    working = await status.execute(
        {"agent": "Scout", "turn_language": "en"}, _ctx("what is scout doing")
    )
    assert working.output.startswith("Scout is working on:")
    unknown = await status.execute({"agent": "ghost"}, _ctx("what is ghost doing"))
    assert unknown.success is False


# --------------------------------------------------------------- the lead picks


def _tool(name: str, desc: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, description=desc, risk_tier="monitor", schema={})


@pytest.fixture
async def team(tmp_path: Path):
    """A society with a mail agent and a researcher, hands from a tiny catalog."""
    chat = FakeChat(tmp_path / "agent_chat.db")
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path / "data")))
    tools = {
        "gmail": _tool("gmail", "Read and send mail."),
        "search-web": _tool("search-web", "Search the web."),
    }
    rt = SocietyRuntime(
        tmp_path, chat_service=lambda: chat, cfg=lambda: cfg, brain_tools=lambda: tools
    )
    await rt.ensure_started()
    await rt.roster.create(
        name="Gmail agent", focus=["plugin:gmail"], provider="openai", model="gpt-5.2"
    )
    await rt.roster.create(
        name="Scout", focus=["core:search-web"], provider="openai", model="gpt-5.2"
    )
    try:
        yield rt, chat
    finally:
        await rt.close()


async def test_delegate_without_a_name_picks_by_the_task(team):
    """'Give that to the team' — the lead picks the agent whose hands fit (2026-09-03)."""
    rt, chat = team
    tool = DelegateToAgentTool(runtime_resolver=lambda: rt)
    res = await tool.execute(
        {"task": "Answer the invoice mail in gmail.", "turn_language": "en"},
        _ctx("give that to the team"),
    )
    assert res.success, res.error
    assert res.output["acknowledgement"] == "Gmail agent is on it, I will let you know."
    assert chat.sent and chat.sent[0][0] == "society:gmail-agent"


async def test_delegate_unknown_name_does_not_redirect_work(team):
    """A task fit must never override the user's explicit recipient."""
    rt, chat = team
    tool = DelegateToAgentTool(runtime_resolver=lambda: rt)
    res = await tool.execute(
        {"agent": "email agent", "task": "check my inbox in gmail", "turn_language": "en"},
        _ctx("email agent, check my inbox"),
    )
    assert not res.success and res.error == "target_unknown"
    assert not chat.sent


async def test_delegate_without_a_fit_says_so(team):
    rt, chat = team
    tool = DelegateToAgentTool(runtime_resolver=lambda: rt)
    res = await tool.execute(
        {"task": "Water the plants.", "turn_language": "en"}, _ctx("give that to the team")
    )
    assert res.success is False and res.error == "no_agent_fits"
    assert res.output == "None of your agents fits this task."
    assert not chat.sent


async def test_assignment_preserves_context_criteria_and_tracking(runtime):
    rt, chat = runtime
    result = await DelegateToAgentTool(runtime_resolver=lambda: rt).execute(
        {
            "agent": "Scout",
            "task": "Compare suppliers.",
            "turn_language": "en",
            "context": "Only European hosting; budget under 20 per month.",
            "completion_criteria": "Return three cited quotes and a comparison table.",
            "refs": ["wiki:requirements"],
        },
        _ctx(),
    )
    assert result.success
    assert "Only European hosting" in chat.sent[0][1]
    assert "three cited quotes" in chat.sent[0][1]
    assert "wiki:requirements" in chat.sent[0][1]
    assignment_id = result.output["assignment_id"]
    trace_id = result.output["trace_id"]
    assert result.artifacts[1] == f"assignment:{assignment_id}"
    assert result.artifacts[2] == f"trace:{trace_id}"
    status = await SocietyStatusTool(runtime_resolver=lambda: rt).execute(
        {
            "assignment_id": assignment_id,
            "trace_id": trace_id,
        },
        _ctx(),
    )
    assert status.output["state"] == "running"
    assert status.output["events"][0]["parent_event_id"] == assignment_id
    await rt.say(
        from_agent="scout",
        to_agent="jarvis",
        text="Waiting for supplier access.",
        trace_id=trace_id,
        msg_type=MsgType.RESULT,
        parent_event_id=assignment_id,
        payload={
            "status": "blocked",
            "done": "Compared two suppliers.",
            "output": ["wiki:comparison"],
            "evidence": ["wiki:quotes"],
            "open": ["Third supplier requires access."],
            "next_owner": None,
        },
    )
    blocked = await SocietyStatusTool(runtime_resolver=lambda: rt).execute(
        {"assignment_id": assignment_id, "trace_id": trace_id},
        _ctx(),
    )
    assert blocked.output["state"] == "blocked"
    assert blocked.output["events"][-1]["payload"]["evidence"] == ["wiki:quotes"]


async def test_unrelated_claim_does_not_confirm_assignment(runtime):
    from jarvis.society.events import SocietyEnvelope

    rt, chat = runtime
    rt.scheduler.detach()

    async def noisy_say(**kwargs):
        assignment = await rt.say(**kwargs)
        await rt.store.append_and_publish(
            SocietyEnvelope(
                msg_type=MsgType.CLAIM,
                from_agent="scout",
                to_agent="jarvis",
                trace_id=assignment.trace_id,
                parent_event_id="some-other-assignment",
            )
        )
        return assignment

    proxy = SimpleNamespace(
        ensure_started=rt.ensure_started,
        roster=rt.roster,
        store=rt.store,
        say=noisy_say,
        lead_id=rt.lead_id,
    )
    result = await DelegateToAgentTool(runtime_resolver=lambda: proxy).execute(
        {
            "agent": "scout",
            "task": "Find it.",
            "turn_language": "en",
        },
        _ctx(),
    )
    assert result.success
    assert "not confirmed" in result.output["acknowledgement"]
    assert result.output["state"] == "recorded"
    assert not chat.sent


async def test_status_details_exposes_roles_paused_state_and_observed_stats(runtime):
    rt, _ = runtime
    await rt.roster.update("scout", {"title": "Researcher", "state": "paused"})
    before = await rt.store.last_seq()
    result = await SocietyStatusTool(runtime_resolver=lambda: rt).execute(
        {
            "agent": "Scout",
            "details": True,
        },
        _ctx(),
    )
    row = result.output["agents"][0]
    assert row["agent"]["title"] == "Researcher"
    assert row["agent"]["state"] == "paused"
    assert row["stats"]["runs"] == 0
    assert "No independent quality rating" in result.output["measurement_note"]
    assert await rt.store.last_seq() == before


@pytest.mark.parametrize("tool_class", [DelegateToAgentTool, SocietyStatusTool])
async def test_runtime_start_failure_is_an_honest_tool_result(tool_class):
    async def fail_to_start():
        raise OSError("database unavailable")

    runtime = SimpleNamespace(ensure_started=fail_to_start)
    result = await tool_class(runtime_resolver=lambda: runtime).execute(
        {"agent": "Scout", "task": "Research it.", "turn_language": "en"},
        _ctx(),
    )
    assert not result.success
    assert result.error == "society unavailable"
    assert result.output == "The agents are not ready yet."


async def test_roster_names_do_not_claim_paused_agents_are_active(runtime):
    rt, _ = runtime
    await rt.roster.update("scout", {"state": "paused"})
    tool = SocietyStatusTool(runtime_resolver=lambda: rt)
    result = await tool.execute({"turn_language": "en"}, _ctx())
    assert result.output == "Your agents: Archivist, Scout."
    await rt.roster.update("scout", {"state": "archived"})
    await rt.roster.update("archivist", {"state": "archived"})
    empty = await tool.execute({"turn_language": "en"}, _ctx())
    assert empty.output == "You do not have any team agents yet."


@pytest.mark.parametrize("tool_class", [DelegateToAgentTool, SocietyStatusTool])
async def test_runtime_still_starting_returns_not_ready(tool_class):
    async def prepare():
        return False

    runtime = SimpleNamespace(prepare_context=prepare)
    result = await tool_class(runtime_resolver=lambda: runtime).execute(
        {"agent": "Scout", "task": "Research it.", "turn_language": "en"},
        _ctx(),
    )
    assert not result.success and result.error == "society unavailable"


async def test_latest_assignment_survives_unrelated_history_without_previous_tool_ids(runtime):
    rt, _ = runtime
    rt.scheduler.detach()
    await rt.say(
        from_agent="jarvis",
        to_agent="scout",
        text="Old task.",
        msg_type=MsgType.ASSIGN,
    )
    newest = await rt.say(
        from_agent="jarvis",
        to_agent="scout",
        text="Compare supplier prices.",
        msg_type=MsgType.ASSIGN,
    )
    await rt.say(
        from_agent="scout",
        to_agent="jarvis",
        text="Compared three suppliers.",
        msg_type=MsgType.RESULT,
        trace_id=newest.trace_id,
        parent_event_id=newest.event_id,
        payload={"status": "done", "evidence": ["wiki:supplier-quotes"]},
    )
    # A later user assignment is not the latest assignment sent by the lead.
    await rt.say(
        from_agent="user",
        to_agent="scout",
        text="Separate user task.",
        msg_type=MsgType.ASSIGN,
    )
    for i in range(25):
        await rt.say(from_agent="scout", to_agent="jarvis", text=f"Unrelated message {i}.")
    status = await SocietyStatusTool(runtime_resolver=lambda: rt).execute(
        {"agent": "Scout", "latest_assignment": True},
        _ctx(),
    )
    assert status.success
    assert status.output["assignment_id"] == newest.event_id
    assert status.output["assignment"]["payload"]["text"] == "Compare supplier prices."
    assert status.output["state"] == "done"
    assert status.output["events"][-1]["payload"]["evidence"] == ["wiki:supplier-quotes"]


async def test_exact_assignment_id_works_without_trace_and_rejects_mismatched_trace(runtime):
    rt, _ = runtime
    result = await DelegateToAgentTool(runtime_resolver=lambda: rt).execute(
        {"agent": "Scout", "task": "Compare suppliers."},
        _ctx(),
    )
    assignment_id = result.output["assignment_id"]
    tool = SocietyStatusTool(runtime_resolver=lambda: rt)
    status = await tool.execute({"assignment_id": assignment_id}, _ctx())
    assert status.success and status.output["state"] == "running"
    assert status.output["trace_id"] == result.output["trace_id"]
    mismatch = await tool.execute(
        {
            "assignment_id": assignment_id,
            "trace_id": "wrong-trace",
        },
        _ctx(),
    )
    assert not mismatch.success and mismatch.error == "assignment_trace_mismatch"
    ambiguous = await tool.execute({"latest_assignment": True}, _ctx())
    assert not ambiguous.success and ambiguous.error == "agent required"
    empty = await tool.execute({"agent": "Archivist", "latest_assignment": True}, _ctx())
    assert not empty.success and empty.error == "assignment_unknown"
