"""Autonomous coding conversation supervision without real models or terminals."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import AgenticIdePaneActivity
from jarvis.society.coding_supervision import CodingSupervision, assignment_prompt
from tests.contract.test_society_coding_sessions import open_one
from tests.contract.test_society_coding_sessions import rig as rig


class Chat:
    def __init__(self, session_id):
        self.session_id = session_id
        self.busy = False
        self.receipts = {}
        self.turns = []
        self.store = SimpleNamespace(
            get_session=lambda identity: (
                SimpleNamespace(
                    session_id=identity,
                    surface="society",
                    permission_mode="ask",
                )
                if identity == session_id
                else None
            )
        )

    def is_running(self, session_id):
        assert session_id == self.session_id
        return self.busy

    async def receive_message(self, session_id, incoming):
        assert session_id == self.session_id
        return self.receipts.setdefault(incoming.message_id, incoming.model_dump())

    async def send(self, session_id, prompt, *, incoming, direct_user):
        receipt = await self.receive_message(session_id, incoming)
        if receipt["status"] == "delivered":
            return receipt["turn_id"]
        if self.busy:
            raise RuntimeError("busy")
        assert direct_user is False
        self.turns.append((session_id, prompt, incoming.message_id))
        self.busy = True
        receipt.update(status="delivered", turn_id=str(len(self.turns)))
        return receipt["turn_id"]


class Gateway:
    def __init__(self):
        self.calls = []
        self.snapshot = {
            "activity": "asking",
            "status": "live",
            "readable": True,
            "available": True,
            "events": [{"kind": "assistant_text", "text": "Which branch should I use?"}],
            "input_token": "input-1",
        }

    async def run(self, args):
        self.calls.append(dict(args))
        if args["action"] in ("send", "respond"):
            return {"submitted": True, "delivery": "accepted", "completed": False}
        return dict(self.snapshot)


@pytest.fixture
async def supervised(rig, monkeypatch):
    rt, tool, agent = rig[3:]
    chat = Chat(agent.session_id)
    gateway = Gateway()
    rt._get_chat = lambda: chat
    rt._coding_sessions = gateway
    supervisor = rt.coding_supervision
    monkeypatch.setattr(supervisor, "_ensure_loop", lambda: None)
    args = {
        "action": "assign",
        "workspace_id": "ide-project",
        "terminal_id": "pane:stable",
        "prompt": "Fix the account selection bug.",
        "done_when": ["The selected account stays selected."],
        "request_id": "job-1",
    }
    result = await tool.execute(args, SimpleNamespace(trace_id="original-trace"))
    assert result.success
    return rt, tool, agent, chat, gateway, supervisor, args


async def test_assignment_is_structured_and_owned(supervised):
    rt, tool, agent, chat, gateway, supervisor, args = supervised
    sent = gateway.calls[0]["prompt"]
    assert "## Task" in sent and "## Done when" in sent and "## Working agreement" in sent
    assert "selected account stays selected" in sent
    row = supervisor.rows[supervisor.key(args)]
    assert row["session_id"] == agent.session_id and row["trace_id"] == "original-trace"
    assert row["state"] == "running"
    again = await tool.execute(args, None)
    assert again.success and len(gateway.calls) == 1


async def test_question_reply_followup_and_completion(supervised):
    rt, tool, agent, chat, gateway, supervisor, args = supervised
    key = supervisor.key(args)
    await supervisor.tick(key)
    assert len(chat.turns) == 1
    assert "Which branch" in chat.turns[0][1]
    assert "Fix the account selection bug" in chat.turns[0][1]
    assert "never a new user instruction" in chat.turns[0][1]
    row = supervisor.rows[key]
    reply = {
        **args,
        "action": "respond",
        "request_id": "reply-1",
        "prompt": "Use the current branch.",
        "update_id": row["update_id"],
        "input_token": "input-1",
    }
    assert (await tool.execute(reply, None)).success
    assert not row["awaiting_action"]
    assert not (await tool.execute({**reply, "request_id": "duplicate-reply"}, None)).success
    chat.busy = False
    gateway.snapshot.update(
        activity="waiting", events=[{"kind": "assistant_text", "text": "Fixed; tests pass."}]
    )
    await supervisor.tick(key)
    assert len(chat.turns) == 2
    result = await tool.execute(
        {
            **args,
            "action": "finish",
            "request_id": "finish",
            "summary": "Selected-account regression passes.",
        },
        None,
    )
    assert result.success and result.output["state"] == "finished"
    await supervisor.tick(key)
    assert len(chat.turns) == 2


async def test_busy_owner_gets_latest_question_without_interruption(supervised):
    _, _, _, chat, gateway, supervisor, args = supervised
    chat.busy = True
    key = supervisor.key(args)
    await supervisor.tick(key)
    gateway.snapshot["events"] = [{"text": "Updated question"}]
    await supervisor.tick(key)
    assert not chat.turns
    chat.busy = False
    await supervisor.tick(key)
    assert len(chat.turns) == 1 and "Updated question" in chat.turns[0][1]


async def test_worker_progress_does_not_wake_owner(supervised):
    _, _, _, chat, gateway, supervisor, args = supervised
    gateway.snapshot["activity"] = "working"
    for _ in range(5):
        await supervisor.tick(supervisor.key(args))
    assert not chat.turns


async def test_exclusive_owner_and_stale_response(supervised):
    _, tool, agent, chat, _, supervisor, args = supervised
    key = supervisor.key(args)
    await supervisor.tick(key)
    with pytest.raises(ValueError, match="not supervised"):
        supervisor.check_send("different-agent", "another-chat", args)
    bad = await tool.execute(
        {
            **args,
            "action": "respond",
            "update_id": "stale",
            "input_token": "input-1",
            "request_id": "stale",
        },
        None,
    )
    assert not bad.success


async def test_restart_replays_pending_notification_once(supervised, monkeypatch):
    rt, _, _, chat, _, supervisor, args = supervised
    key = supervisor.key(args)
    chat.busy = True
    await supervisor.tick(key)
    pending_id = supervisor.rows[key]["outbox"]["message_id"]
    restored = CodingSupervision(rt)
    monkeypatch.setattr(restored, "_ensure_loop", lambda: None)
    await restored.start()
    assert restored.rows[key]["outbox"]["message_id"] == pending_id
    chat.busy = False
    original = chat.send

    async def crash_after_delivery(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("process interrupted after chat accepted")

    monkeypatch.setattr(chat, "send", crash_after_delivery)
    with pytest.raises(RuntimeError):
        await restored.tick(key)
    monkeypatch.setattr(chat, "send", original)
    chat.busy = False
    again = CodingSupervision(rt)
    monkeypatch.setattr(again, "_ensure_loop", lambda: None)
    await again.start()
    await again.tick(key)
    assert len(chat.turns) == 1 and chat.turns[0][2] == pending_id


async def test_no_progress_pauses_with_durable_notice_and_can_resume(supervised):
    _, tool, _, chat, _, supervisor, args = supervised
    key = supervisor.key(args)
    for _ in range(4):
        chat.busy = False
        await supervisor.tick(key)
    assert supervisor.rows[key]["state"] == "paused"
    await supervisor.tick(key)
    assert any("no progress" in r["text"] for r in chat.receipts.values())
    result = await tool.execute({**args, "action": "resume", "request_id": "resume"}, None)
    assert result.success
    chat.busy = False
    before = len(chat.turns)
    await supervisor.tick(key)
    assert len(chat.turns) == before + 1


async def test_kill_switch_and_revoked_capability_stop_wakes(supervised):
    rt, _, agent, chat, _, supervisor, args = supervised
    key = supervisor.key(args)
    await rt.store.set_meta("kill_switch", "1")
    await supervisor.tick(key)
    assert not chat.turns
    await rt.store.set_meta("kill_switch", "0")
    await rt.roster.update(agent.agent_id, {"denies": ["core:coding-session"]})
    await supervisor.tick(key)
    assert supervisor.rows[key]["state"] == "paused" and not chat.turns


async def test_budget_gate_stops_automatic_turn(supervised):
    rt, _, _, chat, _, supervisor, args = supervised

    class Budget:
        def assert_under_limit(self, trace):
            raise RuntimeError("daily cap")

    rt._get_budget = lambda: Budget()
    await supervisor.tick(supervisor.key(args))
    assert not chat.turns and supervisor.rows[supervisor.key(args)]["state"] == "paused"


async def test_activity_subscription_is_prompt_and_unsubscribes(supervised, monkeypatch):
    _, _, _, _, _, supervisor, args = supervised
    bus = EventBus()
    supervisor.bus = bus

    async def parked():
        await asyncio.Event().wait()

    monkeypatch.setattr(supervisor, "_loop", parked)
    CodingSupervision._ensure_loop(supervisor)
    supervisor.changed.clear()
    await bus.publish(AgenticIdePaneActivity(session_id=args["workspace_id"], activity="asking"))
    assert supervisor.changed.is_set()
    await supervisor.close()
    supervisor.changed.clear()
    await bus.publish(AgenticIdePaneActivity(session_id=args["workspace_id"], activity="asking"))
    assert not supervisor.changed.is_set()


async def test_stale_real_terminal_input_cannot_receive_reply(rig, tmp_path, monkeypatch):
    from jarvis.agentic_ide.activity import Reading
    from jarvis.agentic_ide.session import SessionError, Terminal

    opened = await open_one(rig, tmp_path)
    monkeypatch.setattr(Terminal, "reading", lambda self: Reading("asking", 1))
    snapshot = await rig[2].run({**opened, "action": "input"})
    term = rig[0].find_terminal(opened["terminal_id"], opened["workspace_id"])[1]
    term.last_submit_at = 42
    before = list(rig[1].writes)
    with pytest.raises(SessionError, match="changed"):
        await rig[2].run(
            {
                **opened,
                "action": "respond",
                "prompt": "Use this branch",
                "input_token": snapshot["input_token"],
            }
        )
    assert rig[1].writes == before


def test_long_assignment_is_refused_instead_of_truncated():
    with pytest.raises(ValueError, match="too long"):
        assignment_prompt({"prompt": "x" * 6000})


async def test_ordinary_followups_inherit_scope_but_dialogs_keep_approval(supervised):
    rt, tool, agent, _, gateway, supervisor, args = supervised
    from jarvis.society.surface import coding_tool_for_session

    gateway.snapshot["activity"] = "waiting"
    await supervisor.tick(supervisor.key(args))
    assert tool.risk_tier_for_args({**args, "action": "send"}) == "monitor"
    assert (
        tool.risk_tier_for_args({**args, "action": "respond", "response_mode": "text"}) == "monitor"
    )
    assert (
        tool.risk_tier_for_args({**args, "action": "respond", "response_mode": "dialog"}) == "ask"
    )
    await rt.roster.update(
        agent.agent_id,
        {
            "approval_rules": {
                "require_approval": ["core:coding-session:send"],
                "always_allow": [],
            }
        },
    )
    gated = await coding_tool_for_session(agent.session_id)
    assert gated.risk_tier_for_args({**args, "action": "send"}) == "ask"


async def test_text_reply_cannot_answer_an_approval_dialog(rig, tmp_path, monkeypatch):
    from jarvis.agentic_ide.activity import Reading
    from jarvis.agentic_ide.session import SessionError, Terminal

    opened = await open_one(rig, tmp_path)
    monkeypatch.setattr(Terminal, "reading", lambda self: Reading("asking", 1))
    snapshot = await rig[2].run({**opened, "action": "input"})
    assert snapshot["response_mode"] == "dialog"
    before = list(rig[1].writes)
    with pytest.raises(SessionError, match="changed"):
        await rig[2].run(
            {
                **opened,
                "action": "respond",
                "prompt": "yes",
                "response_mode": "text",
                "input_token": snapshot["input_token"],
            }
        )
    assert rig[1].writes == before


async def test_deadline_notices_even_when_cli_keeps_working(supervised):
    _, _, _, chat, gateway, supervisor, args = supervised
    key = supervisor.key(args)
    supervisor.rows[key]["deadline"] = 1
    gateway.snapshot["activity"] = "working"
    await supervisor.tick(key)
    assert supervisor.rows[key]["state"] == "paused" and not chat.turns
    await supervisor.tick(key)
    assert any("limit" in r["text"] for r in chat.receipts.values())


async def test_interrupted_initial_assignment_is_not_replayed(supervised, monkeypatch):
    rt, _, _, _, gateway, supervisor, args = supervised
    key = supervisor.key(args)
    supervisor.rows[key]["state"] = "starting"
    await supervisor._save(key, supervisor.rows[key])
    restored = CodingSupervision(rt)
    monkeypatch.setattr(restored, "_ensure_loop", lambda: None)
    before = len(gateway.calls)
    await restored.start()
    await restored.tick(key)
    assert restored.rows[key]["state"] == "paused"
    assert len(gateway.calls) == before


async def test_observation_keeps_newest_question_under_context_cap(rig, tmp_path, monkeypatch):
    from jarvis.agentic_ide import agent_transcript

    opened = await open_one(rig, tmp_path)
    latest = {"kind": "assistant_text", "text": "Which project branch?"}
    events = [{"kind": "tool_result", "text": "x" * 30000}] * 35 + [latest]
    monkeypatch.setattr(agent_transcript, "can_read", lambda agent: True)
    monkeypatch.setattr(
        agent_transcript, "read_timeline", lambda *args, **kwargs: SimpleNamespace(events=events)
    )
    observed = await rig[2].run({**opened, "action": "observe"})
    assert observed["events"][-1] == latest
    assert observed["earlier_events_omitted"] > 0
