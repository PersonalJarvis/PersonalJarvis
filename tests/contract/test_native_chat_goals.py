"""Native goal transport contracts, without paid model calls or OS processes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.agent_chat.native_control import NativeClaudeGoal, NativeCodexGoal, disable_cli_tools
from jarvis.agent_chat.runner_api import TurnHandle
from jarvis.agent_chat.store import AgentChatStore


class RpcFixture:
    calls: list[tuple[str, dict]] = []
    closed = False
    status = "complete"

    def __init__(self, *_: Any) -> None:
        self.notifications = asyncio.Queue()
        self.objective = ""

    def start_events(self) -> None:
        for method, params in [
            ("turn/started", {"turn": {"id": "turn-1"}}),
            ("thread/goal/updated", {"goal": {"status": self.status, "objective": self.objective}}),
            (
                "item/completed",
                {"item": {"id": "a1", "type": "agentMessage", "text": "VERIFIED_OUTPUT"}},
            ),
            ("turn/completed", {"turn": {"id": "turn-1", "status": "completed"}}),
        ]:
            self.notifications.put_nowait(
                {"method": method, "params": {"threadId": "thread-1", **params}}
            )

    async def __aenter__(self) -> Any:
        return self

    async def __aexit__(self, *_: Any) -> None:
        RpcFixture.closed = True

    async def send(self, value: dict) -> None:
        pass  # Fixture records requests; this notification has no response.

    async def request(self, method: str, params: dict) -> dict:
        self.calls.append((method, params))
        if method in ("thread/start", "thread/resume"):
            return {"thread": {"id": "thread-1"}}
        if method == "thread/goal/set":
            self.objective = params["objective"]
        if method == "turn/start":
            self.start_events()
            return {"turn": {"id": "turn-1"}}
        if method == "thread/goal/get":
            return {"goal": {"status": self.status, "objective": self.objective}}
        return {}


@pytest.mark.parametrize("platform", ["Windows", "Linux", "Darwin"])
@pytest.mark.parametrize(
    "native_status, expected",
    [("complete", "complete"), ("usageLimited", "blocked"), ("budgetLimited", "blocked")],
)
def test_native_goal_preserves_final_output_and_maps_real_wire_statuses(
    monkeypatch: Any, tmp_path: Path, platform: str, native_status: str, expected: str
) -> None:
    from jarvis.agent_chat import runner_cli

    monkeypatch.setattr("platform.system", lambda: platform)
    monkeypatch.setattr("jarvis.agent_chat.native_control.GoalRpc", RpcFixture)
    monkeypatch.setattr(runner_cli, "codex_argv_prefix", lambda: ["codex"])
    monkeypatch.setattr(runner_cli, "_account_env", lambda _: {})

    async def identity(_: Any) -> None:
        return None

    monkeypatch.setattr(runner_cli, "_surface_identity", identity)
    RpcFixture.calls, RpcFixture.closed, RpcFixture.status = [], False, native_status

    async def scenario() -> None:
        session = AgentChatStore().create_session(
            provider="openai-codex",
            model="test",
            effort="low",
            cwd=str(tmp_path),
            surface="society",
        )
        events = []

        async def emit(event: dict) -> None:
            events.append(event)

        async def deny(*_: Any) -> str:
            return "deny"

        adapter = NativeCodexGoal()
        handle = TurnHandle(
            session=session,
            turn_id="local-turn",
            emit=emit,
            request_approval=deny,
            cancel=asyncio.Event(),
        )
        returned = await asyncio.wait_for(adapter._run(handle, "Verify output"), 2)
        assert returned == "thread-1"
        assert adapter.outcome["status"] == expected
        assert any(
            e["kind"] == "assistant_text" and e["payload"]["text"] == "VERIFIED_OUTPUT"
            for e in events
        )
        assert events[-1]["kind"] == "turn_finished"
        assert RpcFixture.closed and adapter.cleanup_done
        assert RpcFixture.calls[-1][0] == "thread/goal/clear"

    asyncio.run(scenario())


@pytest.mark.parametrize("runner", ["claude-cli", "glm-cli", "codex-cli"])
def test_verifier_removes_mcp_secrets_and_all_execution_tools(runner: str) -> None:
    plan = SimpleNamespace(
        argv=[
            "binary",
            "exec",
            "--mcp-config",
            "private",
            "-c",
            'mcp_servers.test.http_headers={Authorization="private"}',
        ]
    )
    disable_cli_tools(plan, runner)
    assert "private" not in " ".join(plan.argv)
    if runner == "codex-cli":
        assert "--ignore-user-config" in plan.argv and "shell_tool" in plan.argv
    else:
        at = plan.argv.index("--tools")
        assert plan.argv[at + 1] == ""
        assert "--strict-mcp-config" in plan.argv


def test_claude_native_goal_owns_the_loop_and_clears_native_state(monkeypatch: Any) -> None:
    from jarvis.agent_chat.control import ChatControls
    from jarvis.agent_chat.control_types import CommandRequest, GoalVerdict
    from tests.unit.agent_chat.test_controls import FakeService, seat

    cleared = []

    async def available(self: Any, session: Any) -> bool:
        return True

    async def verify(*_: Any) -> GoalVerdict:
        return GoalVerdict(
            status="complete", reason="Verified the output", evidence=["1"], progress=True
        )

    async def clear(self: Any, service: Any, sid: str) -> None:
        cleared.append(sid)

    monkeypatch.setattr(NativeClaudeGoal, "available", available)
    monkeypatch.setattr(NativeClaudeGoal, "_clear", clear)
    monkeypatch.setattr("jarvis.agent_chat.control_eval.evaluate_goal", verify)

    async def scenario() -> None:
        service = FakeService()
        sid = seat(service)
        controls = ChatControls(service, adapters=[NativeClaudeGoal()])
        service.controls = controls
        await controls.execute(
            sid, CommandRequest(command="goal", arguments="Produce the result", request_id="native")
        )
        await controls.jobs[sid]
        assert len(service.sent) == 1
        assert service.sent[0][0] == "/goal Produce the result"
        assert service.sent[0][1]["native_goal"] is True
        assert controls.state(sid).goal.engine == "claude-native"
        assert controls.state(sid).goal.status == "complete"
        assert not controls.state(sid).goal.native_pending
        assert cleared == [sid]

    asyncio.run(scenario())
