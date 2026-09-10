"""Chat controls preserve history, serialize goals and never infer commands from text."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from jarvis.agent_chat.control import ChatControls
from jarvis.agent_chat.control_store import ControlStore
from jarvis.agent_chat.control_types import COMMANDS, CommandRequest, GoalVerdict
from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.store import AgentChatStore


class FakeService:
    def __init__(self, store: Any = None) -> None:
        self.store = store or AgentChatStore()
        self.sent: list[tuple[str, dict]] = []
        self.notices: list[dict] = []
        self.running: dict[str, asyncio.Task] = {}
        self.gate: asyncio.Event | None = None
        self.cancelled = 0

    async def post_notice(self, sid: str, data: dict) -> None:
        self.notices.append(data)
        self.store.append_event(sid, make_event("notice", data))

    async def _emit(self, sid: str, event: dict) -> None:
        self.store.append_event(sid, event)

    def is_running(self, sid: str) -> bool:
        return sid in self.running

    async def send(self, sid: str, text: str, **kwargs: Any) -> str:
        assert sid not in self.running
        self.sent.append((text, kwargs))
        tid = str(len(self.sent))

        async def work() -> None:
            try:
                if self.gate:
                    await self.gate.wait()
                await self._emit(
                    sid,
                    make_event("assistant_text", {"turn_id": tid, "text": "Actual work product"}),
                )
                await self._emit(
                    sid, make_event("turn_finished", {"turn_id": tid, "status": "done"})
                )
            finally:
                self.running.pop(sid, None)

        self.running[sid] = asyncio.create_task(work())
        return tid

    async def wait_turn(self, sid: str) -> None:
        task = self.running.get(sid)
        if task:
            await asyncio.shield(task)

    async def cancel(self, sid: str) -> bool:
        task = self.running.get(sid)
        if task:
            self.cancelled += 1
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return task is not None


def seat(service: FakeService, name: str = "society:test") -> str:
    service.store.create_session(
        session_id=name,
        provider="openai",
        model="test",
        effort="low",
        cwd=".",
        surface="society",
        permission_mode="ask",
    )
    return name


def request(command: str, arguments: str = "", rid: str = "r1") -> CommandRequest:
    return CommandRequest(command=command, arguments=arguments, request_id=rid)


def test_plan_build_and_review_preserve_original_permissions_and_history() -> None:
    async def scenario() -> None:
        svc = FakeService()
        sid = seat(svc)
        controls = ChatControls(svc, adapters=[])
        svc.store.append_event(sid, make_event("user_message", {"text": "Original context"}))
        result = await controls.execute(sid, request("plan", "Investigate first"))
        assert result.status == "started"
        await svc.wait_turn(sid)
        await controls.turn_completed(sid, "1", "Investigate first", True, False)
        assert controls.state(sid).plan == "Actual work product"
        assert controls.state(sid).previous_permission == "ask"
        result = await controls.execute(sid, request("message", "@Friend Hello", "r2"))
        assert result.status == "failed" and "Plan mode" in result.error
        await controls.execute(sid, request("build", rid="r3"))
        await svc.wait_turn(sid)
        assert svc.store.get_session(sid).permission_mode == "ask"
        await controls.execute(sid, request("review", rid="r4"))
        await svc.wait_turn(sid)
        assert svc.sent[-1][1]["read_only"] is True
        assert svc.store.get_session(sid).permission_mode == "ask"
        assert svc.store.list_events(sid)[0]["payload"]["text"] == "Original context"

    asyncio.run(scenario())


def test_receipts_deduplicate_start_and_reject_request_id_reuse() -> None:
    async def scenario() -> None:
        svc = FakeService()
        sid = seat(svc)
        controls = ChatControls(svc, adapters=[])
        first = await controls.execute(sid, request("recap"))
        second = await controls.execute(sid, request("recap"))
        assert first == second
        assert len(svc.sent) == 1
        with pytest.raises(ValueError, match="another command"):
            await controls.execute(sid, request("review"))
        await svc.wait_turn(sid)

    asyncio.run(scenario())


def test_goal_rechecks_results_and_stops_after_three_stalled_steps() -> None:
    async def verdict(*_: Any) -> GoalVerdict:
        return GoalVerdict(
            status="continue", reason="Missing the requested artifact", progress=False
        )

    async def scenario() -> None:
        svc = FakeService()
        sid = seat(svc)
        controls = ChatControls(svc, evaluator=verdict, adapters=[])
        await controls.execute(sid, request("goal", "Produce an artifact"))
        await controls.jobs[sid]
        goal = controls.state(sid).goal
        assert goal.status == "blocked"
        assert goal.steps == goal.stalled_steps == 3
        assert len(svc.sent) == 3
        assert all(not flags["direct_user"] for _, flags in svc.sent)

    asyncio.run(scenario())


def test_goal_completion_requires_evidence_and_stop_cannot_be_undone_by_late_verdict() -> None:
    async def scenario() -> None:
        svc = FakeService()
        sid = seat(svc)
        checking = asyncio.Event()
        release = asyncio.Event()

        async def verdict(*_: Any) -> GoalVerdict:
            checking.set()
            await release.wait()
            return GoalVerdict(status="complete", reason="Verified", evidence=["1"], progress=True)

        controls = ChatControls(svc, evaluator=verdict, adapters=[])
        await controls.execute(sid, request("goal", "Write the artifact"))
        await checking.wait()
        await controls.execute(sid, request("stop", rid="stop"))
        release.set()
        await asyncio.sleep(0)
        assert controls.state(sid).goal.status == "paused"
        assert sid not in controls.jobs
        await controls.execute(sid, request("continue", rid="resume"))
        await controls.jobs[sid]
        assert controls.state(sid).goal.status == "complete"

    asyncio.run(scenario())


def test_stop_interrupts_work_and_preserves_goal_for_resume() -> None:
    async def scenario() -> None:
        svc = FakeService()
        sid = seat(svc)
        svc.gate = asyncio.Event()
        controls = ChatControls(svc, adapters=[])
        await controls.execute(sid, request("goal", "Work"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await controls.execute(sid, request("stop", rid="stop"))
        assert svc.cancelled == 1
        assert controls.state(sid).goal.objective == "Work"
        assert controls.state(sid).goal.status == "paused"
        assert not svc.is_running(sid)

    asyncio.run(scenario())


def test_persisted_goals_are_paused_after_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "chat.db"
        svc = FakeService(AgentChatStore(path))
        sid = seat(svc)
        svc.gate = asyncio.Event()
        controls = ChatControls(svc, adapters=[])
        await controls.execute(sid, request("goal", "Keep the goal"))
        # Simulate recovery from a process that persisted active state and then died.
        restored = ControlStore(AgentChatStore(path))
        assert restored.get(sid).goal.status == "paused"
        assert restored.get(sid).goal.objective == "Keep the goal"
        await controls.close()

    asyncio.run(scenario())


def test_find_is_scoped_and_does_not_execute_old_commands() -> None:
    async def scenario() -> None:
        svc = FakeService()
        sid, other = seat(svc), seat(svc, "society:other")
        svc.store.append_event(sid, make_event("user_message", {"text": "/goal confidential"}))
        svc.store.append_event(
            other, make_event("user_message", {"text": "confidential in other chat"})
        )
        controls = ChatControls(svc, adapters=[])
        result = await controls.execute(sid, request("find", "confidential"))
        assert len(result.data["hits"]) == 1
        assert result.data["hits"][0]["session_id"] == sid
        assert not svc.sent and controls.state(sid).goal is None
        assert len(COMMANDS) == 16

    asyncio.run(scenario())


def test_build_never_elevates_an_initial_read_only_seat() -> None:
    async def scenario() -> None:
        svc = FakeService()
        sid = seat(svc)
        svc.store.update_session(sid, permission_mode="plan")
        controls = ChatControls(svc, adapters=[])
        result = await controls.execute(sid, request("build"))
        assert result.status == "failed"
        assert svc.store.get_session(sid).permission_mode == "plan"
        assert not svc.sent

    asyncio.run(scenario())


def test_goal_attachments_are_saved_and_read_before_work_starts() -> None:
    async def verify(*_: Any) -> GoalVerdict:
        return GoalVerdict(
            status="complete", reason="The result is present", evidence=["1"], progress=True
        )

    async def scenario() -> None:
        svc = FakeService()
        sid = seat(svc)
        controls = ChatControls(svc, adapters=[], evaluator=verify)
        files = [
            {
                "name": "brief.txt",
                "reference": "brief.txt",
                "kind": "text",
                "detail": "Source material",
                "described_by": "extraction",
                "note": "",
            }
        ]
        await controls.execute(
            sid,
            CommandRequest(
                command="goal", arguments="Use the brief", attachments=files, request_id="files"
            ),
        )
        assert controls.store.inputs(sid) == files
        await controls.jobs[sid]
        assert svc.sent[0][1]["attachments"] == files
        assert svc.sent[0][1]["read_only"] is True
        assert controls.store.inputs(sid) == []
        assert controls.state(sid).goal.status == "complete"

    asyncio.run(scenario())


def test_message_uses_executor_exact_text_sender_and_idempotency(monkeypatch: Any) -> None:
    from types import SimpleNamespace

    from jarvis.agent_chat.service import AgentChatService
    from jarvis.core.bus import EventBus
    from jarvis.core.config import SafetyConfig
    from jarvis.core.protocols import ToolResult
    from jarvis.safety.approval import ApprovalWorkflow
    from jarvis.safety.risk_tier import RiskTierEvaluator
    from jarvis.safety.tool_executor import ToolExecutor

    calls = []

    class MessageTool:
        name = "society_message_agent"
        risk_tier = "safe"
        is_action_tool = True
        schema = {}

        def __init__(self, runtime: Any, agent_id: str) -> None:
            self.agent_id = agent_id

        async def execute(self, args: dict, ctx: Any) -> ToolResult:
            calls.append((self.agent_id, args, ctx))
            return ToolResult(
                True, {"status": "queued", "target": args["target"], "text": args["text"]}
            )

    async def scenario() -> None:
        bus = EventBus()
        executor = ToolExecutor(bus, RiskTierEvaluator(SafetyConfig()), ApprovalWorkflow(bus))
        monkeypatch.setattr(
            "jarvis.agent_chat.runner_brain.brain_manager",
            lambda: SimpleNamespace(_tool_executor=executor),
        )

        async def completed(*_: Any) -> None:
            return None

        runtime = SimpleNamespace(
            conversations=SimpleNamespace(checkpoint=lambda _: (0, "")), turn_completed=completed
        )
        monkeypatch.setattr("jarvis.society.runtime.current_runtime", lambda: runtime)
        monkeypatch.setattr("jarvis.society.agent_tools.MessageAgentTool", MessageTool)
        svc = AgentChatService(AgentChatStore(), bus=lambda: bus)
        sid = svc.store.create_session(
            provider="openai",
            model="",
            effort="low",
            cwd=".",
            surface="society",
            session_id="society:mail",
        ).session_id
        request_data = request("message", '@"Drive Agent" First line\nSecond line')
        first = await svc.controls.execute(sid, request_data)
        second = await svc.controls.execute(sid, request_data)
        assert first.status == "done" and first == second
        assert len(calls) == 1
        assert calls[0][0] == "mail"
        assert calls[0][1]["target"] == "Drive Agent"
        assert calls[0][1]["text"] == "First line\nSecond line"
        assert first.data["result"]["status"] == "queued"
        assert any(e["kind"] == "tool_result" for e in svc.store.list_events(sid))

    asyncio.run(scenario())
