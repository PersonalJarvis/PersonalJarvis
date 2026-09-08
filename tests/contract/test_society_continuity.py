"""Conversation continuity contracts: durable facts, recall, source isolation and completion."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from jarvis.agent_chat import runner_brain
from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.core.protocols import ChatTurn, current_chat_turn
from jarvis.society.agent_tools import ProposeChangeTool
from jarvis.society.conversation import ConversationArchive, prepare_history
from jarvis.society.conversation_tool import ConversationRecallTool
from jarvis.society.runtime import SocietyRuntime
from tests.fakes.fake_brain_manager import FakeBrainManager
from tests.fakes.fake_society_review import MemoryExecutor, SummaryProvider


@pytest.fixture
async def world(tmp_path, monkeypatch):
    svc = AgentChatService(AgentChatStore(tmp_path / "chat.db"))
    cfg = SimpleNamespace(
        memory=SimpleNamespace(data_dir=str(tmp_path)),
        wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")),
    )
    rt = SocietyRuntime(tmp_path, chat_service=lambda: svc, cfg=lambda: cfg)
    await rt.ensure_started()
    await rt.roster.create(
        name="Mailbox", title="Gmail agent", description="Prepare drafts only.", provider="openai"
    )
    from jarvis.society.chat_binding import ensure_session

    session = ensure_session(svc, cfg, await rt.roster.get("mailbox"))
    fake = FakeBrainManager()
    fake._config = cfg
    monkeypatch.setattr(runner_brain, "brain_manager", lambda: fake)
    monkeypatch.setattr(runner_brain, "_agent_secret", lambda *_: None)
    try:
        yield rt, svc, session, fake
    finally:
        await svc.cancel_all()
        await rt.close()
        svc.store.close()


def message(seq, text, kind="user_message"):
    return {"seq": seq, "kind": kind, "payload": {"text": text}}


async def test_new_memory_and_correction_survive_reopening(world):
    rt, _, _, _ = world
    agent = await rt.roster.get("mailbox")
    for i in range(65):
        await rt.memory.remember(
            agent,
            f"Incidental archived observation {i}. " + "This is ordinary prior context. " * 6,
            importance=1,
        )
    await rt.memory.remember(agent, "I am the Gmail agent and prepare drafts only.", importance=10)
    assert "Gmail agent" in rt.memory.head(agent)
    await rt.memory.remember(
        agent, "Prepare drafts in plain text.", operation="replace", old_text="I am the Gmail agent"
    )
    head = rt.memory.head(agent)
    assert "Prepare drafts in plain text." in head and "prepare drafts only" not in head
    from jarvis.society.memory import SocietyMemory

    reopened = SocietyMemory(rt)
    assert "Prepare drafts in plain text." in reopened.head(agent)
    await reopened.remember(agent, "", operation="remove", old_text="Prepare drafts in plain text.")
    assert "Prepare drafts in plain text." not in reopened.head(agent)


def test_archive_restart_and_exact_scope(tmp_path):
    path = tmp_path / "archive.db"
    archive = ConversationArchive(path)
    events = [message(i, "We discussed Gmail draft style " + str(i)) for i in range(1, 101)]
    archive.ingest("society:mailbox", events)
    archive.ingest("society:other", [message(1, "PRIVATE OTHER Gmail fact")])
    archive.close()
    archive = ConversationArchive(path)
    try:
        hits = archive.search("society:mailbox", "Gmail")
        assert hits and all("PRIVATE OTHER" not in h["text"] for h in hits)
        assert (
            archive.read("society:mailbox", after_seq=0, limit=1)[0]["payload"]["text"]
            == events[0]["payload"]["text"]
        )
        archive.fts_available = False
        assert archive.search("society:mailbox", "Gmail")
        assert not archive.search("society:missing", "' OR 1=1 -- secret")
    finally:
        archive.close()


async def test_long_chat_preserves_role_and_searchable_originals(world):
    rt, _, session, _ = world
    events = [message(1, "You are my Gmail agent; prepare drafts only.")]
    events += [
        message(
            i,
            f"Unrelated conversation {i}. " + "detail " * 120,
            "assistant_text" if i % 2 == 0 else "user_message",
        )
        for i in range(2, 81)
    ]
    provider = SummaryProvider()
    history = await prepare_history(rt, session, events, "What role did I give you?", provider)
    assert provider.calls and any("Gmail" in str(m.content) for m in history)
    assert rt.conversations.checkpoint(session.session_id)[0] > 1
    original = rt.conversations.search(session.session_id, "prepare drafts only")
    assert any(h["seq"] == 1 for h in original)
    before = len(provider.calls)
    await prepare_history(rt, session, events, "What role?", provider)
    assert len(provider.calls) == before


async def test_empty_summary_never_discards_archive(world):
    rt, _, session, _ = world
    events = [message(i, "important " * 1000) for i in range(1, 8)]
    with pytest.raises(ValueError, match="empty summary"):
        await prepare_history(rt, session, events, "recall", SummaryProvider(empty=True))
    assert rt.conversations.checkpoint(session.session_id) == (0, "")
    assert len(rt.conversations.read(session.session_id)) == len(events)


async def test_explicit_rule_applies_without_card_click_and_replaces(world):
    rt, _, session, _ = world
    tool = ProposeChangeTool(rt, "mailbox", session_id=session.session_id)
    quote = "Change my rule to prepare plain-text drafts."
    token = current_chat_turn.set(ChatTurn(session.session_id, "t1", quote, True, "trace"))
    try:
        result = await tool.execute(
            {
                "kind": "rule",
                "mode": "apply",
                "request_quote": quote,
                "payload": {
                    "operation": "replace",
                    "old_text": "Prepare drafts only.",
                    "text": "Prepare plain-text drafts.",
                },
            },
            None,
        )
    finally:
        current_chat_turn.reset(token)
    assert result.success
    assert (await rt.roster.get("mailbox")).description == "Prepare plain-text drafts."
    assert not await rt.approvals.items(state="pending", agent_id="mailbox")


@pytest.mark.parametrize(
    "direct,quote", [(False, "Remember this rule"), (True, "Forged instruction")]
)
async def test_background_or_forged_request_cannot_apply(world, direct, quote):
    rt, _, session, _ = world
    token = current_chat_turn.set(
        ChatTurn(session.session_id, "t", "Remember this rule", direct, "trace")
    )
    try:
        result = await ProposeChangeTool(rt, "mailbox").execute(
            {
                "kind": "rule",
                "mode": "apply",
                "request_quote": quote,
                "payload": {"text": "New role"},
            },
            None,
        )
    finally:
        current_chat_turn.reset(token)
    assert not result.success
    assert (await rt.roster.get("mailbox")).description == "Prepare drafts only."


async def test_direct_chat_completion_is_reviewed_once(world):
    rt, svc, session, _ = world
    calls = []
    reviewed = asyncio.Event()

    async def reviewer(runtime, agent, prompt):
        calls.append(json.loads(prompt))
        reviewed.set()
        return {"memories": [], "skill": None}

    rt.turn_reviewer = reviewer
    await svc.send(session.session_id, "Please use shorter explanations next time.")
    await asyncio.wait_for(reviewed.wait(), timeout=5)
    await rt.recover_reviews()
    assert len(calls) == 1
    assert "shorter explanations" in calls[0]["user"][0]
    result = await ConversationRecallTool(rt, "mailbox").execute(
        {"query": "shorter explanations"}, None
    )
    assert result.success and result.output["hits"]


async def test_review_memory_requires_grounded_evidence_and_executor(world):
    rt, svc, session, _ = world
    rt.memory_executor = MemoryExecutor()

    async def reviewer(runtime, agent, prompt):
        return {
            "memories": [
                {
                    "text": "The user prefers plain-text drafts.",
                    "evidence": "Please prepare plain-text drafts.",
                    "importance": 9,
                },
                {"text": "Invented preference", "evidence": "a fabricated source", "importance": 9},
            ],
            "skill": None,
        }

    rt.turn_reviewer = reviewer
    await svc.send(session.session_id, "Please prepare plain-text drafts.")
    await asyncio.wait_for(rt.memory_executor.completed.wait(), timeout=5)
    await rt.recover_reviews()
    assert len(rt.memory_executor.calls) == 1
    head = rt.memory.head(await rt.roster.get("mailbox"))
    assert "plain-text drafts" in head and "Invented" not in head


async def test_routine_uses_current_rules_and_memory(world):
    from jarvis.society.routine_runner import run_owned_routine
    from jarvis.society.routines import build_task_spec

    rt, svc, session, brain = world
    agent = await rt.roster.get("mailbox")
    spec = build_task_spec(
        agent,
        title="Digest",
        prompt="Read and summarize new mail.",
        schedule={"kind": "every", "interval_seconds": 3600},
    )
    await rt.roster.update(agent.agent_id, {"description": "Use plain text and never send mail."})
    await rt.memory.remember(agent, "The weekly project is Cedar.", importance=9)

    async def reviewer(*args):
        return {"memories": [], "skill": None}

    rt.turn_reviewer = reviewer
    output = await run_owned_routine(rt, str(spec.id), spec.tags, spec.action.prompt)
    assert output == brain.reply
    prompt, kwargs = brain.calls[-1]
    briefing = kwargs["turn_override"].system_extra
    assert "Use plain text and never send mail." in briefing and "Cedar" in briefing
    assert "Prepare drafts only." not in prompt
    assert kwargs["conversation_id"] == session.session_id


async def test_routine_update_preserves_id_and_pause_state(tmp_path):
    from jarvis.core.bus import EventBus
    from jarvis.tasks.runner import TaskRunner
    from jarvis.tasks.scheduler import TaskScheduler
    from jarvis.tasks.schema import AgentAction, TaskSpec, TriggerEvery
    from jarvis.tasks.store import TaskStore

    store = TaskStore(tmp_path / "tasks.db")
    await store.init()
    bus = EventBus()
    scheduler = TaskScheduler(store=store, bus=bus, runner=TaskRunner(store, bus))
    try:
        spec = TaskSpec(
            title="Digest",
            trigger=TriggerEvery(interval_seconds=3600),
            action=AgentAction(prompt="Read mail"),
        )
        tid = await scheduler.schedule(spec)
        updated = spec.model_copy(
            update={"title": "New digest", "trigger": TriggerEvery(interval_seconds=7200)}
        )
        await scheduler.update_task(tid, updated)
        assert (await store.get(tid))["state"] == "scheduled"
        assert (await store.get_spec(tid)).trigger.interval_seconds == 7200
        await scheduler.pause(tid)
        await scheduler.update_task(tid, spec)
        assert (await store.get(tid))["state"] == "paused"
        assert len(await store.list()) == 1
    finally:
        await scheduler.shutdown()
        await store.close()


async def test_learned_skill_is_revised_in_place_with_backup(world):
    from jarvis.society.learning import LearningPass
    from tests.unit.society.test_learning import DIGEST, FakeCreator

    rt, _, _, _ = world
    agent = await rt.roster.get("mailbox")

    class RevisingCreator(FakeCreator):
        async def draft(self, inp):
            old = (self.skills.root / "thumbnail-style" / "SKILL.md").read_text(encoding="utf-8")
            return SimpleNamespace(
                skill_md=old.replace("last five", "last seven").replace(
                    "schema_version: 1", "schema_version: '1'"
                ),
                brain_used=True,
            )

    rt.learning = LearningPass(rt, creator_factory=lambda a, s: RevisingCreator(s))
    slug = await rt.learning.run(agent, DIGEST)
    changed = await rt.learning.run(agent, DIGEST, existing_slug=slug, receipt="correction-1")
    assert changed == slug
    root = rt.skills_for(agent.agent_id).root
    assert "last seven" in (root / slug / "SKILL.md").read_text(encoding="utf-8")
    assert len(list(root.glob("*/SKILL.md"))) == 1
    assert list((root / slug / ".history").glob("*.md"))


@pytest.mark.parametrize(
    "utterance",
    [
        "Persist this standing instruction: You are my Gmail assistant. Do not access my inbox.",
        "Speichere diese Regel: Du bist mein Gmail-Agent "  # i18n-allow: input fixture
        "und liest meine Mails nur auf Anfrage.",  # i18n-allow: input fixture
        "Guarda esta regla: eres mi agente de correo y no lees mi inbox sin permiso.",
    ],
)
def test_role_configuration_does_not_require_inbox_access(utterance):
    from tests.unit.brain.test_evidence_gate import _gate

    assert _gate(utterance, live_tools=("society_propose_change",)).kind == "pass"


def test_configuration_capability_does_not_unlock_actual_data_lookup():
    from tests.unit.brain.test_evidence_gate import _gate

    assert (
        _gate("Read my inbox now", live_tools=("society_propose_change",)).kind == "honest_refusal"
    )


def test_live_capabilities_include_filtered_turn_tools():
    from jarvis.brain.manager import _TURN_OVERRIDE, BrainManager
    from jarvis.brain.turn_override import TurnOverride

    manager = BrainManager.__new__(BrainManager)
    manager._tools = {"gmail": SimpleNamespace(name="gmail")}
    override = TurnOverride(
        provider="openai",
        tools_extra={"society_propose_change": SimpleNamespace()},
        tool_filter=lambda tools: {k: v for k, v in tools.items() if k != "gmail"},
    )
    token = _TURN_OVERRIDE.set(override)
    try:
        assert manager._live_tool_names() == ("society_propose_change",)
    finally:
        _TURN_OVERRIDE.reset(token)


async def test_one_large_event_is_fully_summarized_in_bounded_requests(world):
    rt, _, session, _ = world
    events = [
        message(1, "Gmail role: prepare drafts only."),
        message(2, "Large prior result. " * 3000 + "LAST_SEGMENT", "assistant_text"),
        message(3, "Continue the existing task."),
    ]
    provider = SummaryProvider()
    await prepare_history(rt, session, events, "Gmail role", provider)
    evidence = [str(req.messages[0].content) for req in provider.calls]
    assert any("LAST_SEGMENT" in text for text in evidence)
    assert all(len(text) < provider.context_window * 3 for text in evidence)
    assert rt.conversations.checkpoint(session.session_id)[0] == 2
    assert rt.conversations.search(session.session_id, "LAST_SEGMENT")


async def test_smaller_model_can_recompact_an_existing_summary(world):
    rt, _, session, _ = world
    events = [message(1, "Gmail role"), message(2, "What is my role?")]
    rt.conversations.ingest(session.session_id, events)
    rt.conversations.save_checkpoint(session.session_id, 1, "Earlier archived facts. " * 1000)
    provider = SummaryProvider()
    provider.context_window = 2048
    history = await prepare_history(rt, session, events, "role", provider)
    assert provider.calls and "Gmail" in str(history[0].content)
    assert len(rt.conversations.checkpoint(session.session_id)[1]) < 1000
