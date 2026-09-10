"""Real owner chat/routine delivery with a deterministic model and fake PTY."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from jarvis.agent_chat import runner_brain
from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.agentic_ide import agent_transcript
from jarvis.agentic_ide.activity import Reading
from jarvis.agentic_ide.session import Terminal
from jarvis.core.bus import EventBus
from jarvis.core.chat_turn import current_chat_turn
from jarvis.society.routine_runner import run_owned_routine
from jarvis.society.routines import agent_tag
from tests.contract.test_society_coding_sessions import open_one
from tests.contract.test_society_coding_sessions import rig as rig
from tests.fakes.fake_brain_manager import FakeBrainManager


async def test_routine_question_and_result_stay_in_real_owner_chat(rig, tmp_path, monkeypatch):
    runtime, owner = rig[3], rig[5]
    owner = await runtime.roster.update(
        owner.agent_id, {"provider": "openai", "model": "fake-model"}
    )
    bus = EventBus()
    service = AgentChatService(AgentChatStore(str(tmp_path / "chat.db")), bus=lambda: bus)
    runtime._get_chat = lambda: service
    runtime._get_cfg = lambda: SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path)))
    monkeypatch.setattr(runtime.coding_supervision, "_ensure_loop", lambda: None)
    monkeypatch.setattr(Terminal, "reading", lambda self: Reading("waiting", 1))

    async def confirm(term, payload, manager, multiline):
        manager.write(term.pty_id, payload)
        return True

    monkeypatch.setattr(rig[0], "_write_and_confirm", confirm)
    opened = await open_one(rig, tmp_path)
    recorded = [{"kind": "assistant_text", "text": "Which branch should I inspect?"}]
    monkeypatch.setattr(agent_transcript, "can_read", lambda agent: True)
    monkeypatch.setattr(
        agent_transcript, "read_timeline", lambda *args, **kwargs: SimpleNamespace(events=recorded)
    )
    identities = []

    class WorkflowBrain(FakeBrainManager):
        async def generate(self, text, **kwargs):
            turn = current_chat_turn.get()
            identities.append((turn.session_id, turn.direct_user))
            override = kwargs["turn_override"]
            # The deterministic driver replaces model/tool selection only. The
            # real chat, routine binding, receipts, context and IDE registry run.
            tool = override.tool_filter(dict(override.tools_extra))["coding-session"]
            ctx = SimpleNamespace(trace_id=kwargs["trace_id"])
            if not self.calls:
                result = await tool.execute(
                    {
                        **opened,
                        "action": "assign",
                        "request_id": "routine-job",
                        "prompt": "Inspect the current branch and report evidence.",
                    },
                    ctx,
                )
                self.reply = "Assignment started under supervision."
            elif len(self.calls) == 1:
                assert "Which branch" in text
                status = await tool.execute({**opened, "action": "supervision"}, ctx)
                input_state = await tool.execute({**opened, "action": "input"}, ctx)
                result = await tool.execute(
                    {
                        **opened,
                        "action": "respond",
                        "request_id": "routine-answer",
                        "prompt": "Inspect the currently checked-out branch.",
                        "update_id": status.output["update_id"],
                        **input_state.output,
                    },
                    ctx,
                )
                self.reply = "Answered the coding agent."
            else:
                assert "Inspection complete" in text
                result = await tool.execute(
                    {
                        **opened,
                        "action": "finish",
                        "request_id": "routine-finish",
                        "summary": "The branch inspection evidence was returned.",
                    },
                    ctx,
                )
                self.reply = "Inspection complete with evidence."
            assert result.success, result.error
            return await super().generate(text, **kwargs)

    brain = WorkflowBrain(bus=bus)
    monkeypatch.setattr(runner_brain, "brain_manager", lambda: brain)
    monkeypatch.setattr(runner_brain, "_agent_secret", lambda *args: "synthetic-test-key")

    async def idle():
        running = service._running.get(owner.session_id)
        if running is not None and running.task is not None:
            await asyncio.wait_for(asyncio.shield(running.task), timeout=5)

    try:
        answer = await run_owned_routine(
            runtime, "routine-proof", ("society", agent_tag(owner.agent_id)), "Inspect the branch"
        )
        assert answer == "Assignment started under supervision."
        await idle()
        key = runtime.coding_supervision.key(opened)
        await runtime.coding_supervision.tick(key)
        await idle()
        recorded[:] = [
            {"kind": "assistant_text", "text": "Inspection complete; current branch recorded."}
        ]
        await runtime.coding_supervision.tick(key)
        await idle()
        assert runtime.coding_supervision.rows[key]["state"] == "finished"
        assert identities == [(owner.session_id, False)] * 3
        events = service.store.list_events(owner.session_id)
        assert len([e for e in events if e["kind"] == "agent_message"]) == 2
        assert len([e for e in events if e["kind"] == "turn_started"]) == 3
    finally:
        await service.cancel(owner.session_id)
        await idle()
        service.store.close()
