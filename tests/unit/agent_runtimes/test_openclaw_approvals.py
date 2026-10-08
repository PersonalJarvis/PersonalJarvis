"""Native OpenClaw approvals over real loopback WebSocket frames, without a model."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from websockets.asyncio.server import serve

from jarvis.agent_runtimes.acp import AcpTurn
from jarvis.agent_runtimes.openclaw_approvals import OpenClawApprovals

_TOKEN = "local-test-" + "token"
_SESSION = "agent:main:main"
_FAKE_AGENT = Path(__file__).resolve().parents[2] / "fakes" / "fake_acp_agent.py"


class Gateway:
    def __init__(self):
        self.socket = None
        self.connect = None
        self.requests = asyncio.Queue()
        self.closed = asyncio.Event()

    async def run(self, socket):
        self.socket = socket
        try:
            await socket.send(json.dumps({"type": "event", "event": "connect.challenge"}))
            self.connect = json.loads(await socket.recv())
            assert self.connect["method"] == "connect"
            assert self.connect["params"]["auth"] == {"token": _TOKEN}
            await socket.send(json.dumps({"type": "res", "id": self.connect["id"], "ok": True}))
            async for raw in socket:
                request = json.loads(raw)
                assert request["method"] == "plugin.approval.resolve"
                # Real Gateways can broadcast resolution before acknowledging
                # the RPC. Our own decision must still finish, including cancel.
                await socket.send(json.dumps({
                    "type": "event", "event": "plugin.approval.resolved",
                    "payload": {"id": request["params"]["id"]},
                }))
                await socket.send(json.dumps({"type": "res", "id": request["id"], "ok": True}))
                await self.requests.put(request)
        finally:
            self.closed.set()

    async def emit(self, payload):
        await self.socket.send(json.dumps({
            "type": "event", "event": "plugin.approval.requested", "payload": payload,
        }))


class IO:
    def __init__(self, decision="allow"):
        self.decision = decision
        self.frames = []
        self.events = []
        self.asked = []
        self.asking = asyncio.Event()
        self.card_closed = asyncio.Event()

    async def write(self, frame):
        self.frames.append(frame)

    async def emit(self, event):
        self.events.append(event)

    async def ask(self, call_id, name, args, summary):
        self.asked.append((call_id, name, args, summary))
        self.asking.set()
        try:
            if self.decision is None:
                await asyncio.Event().wait()
            return self.decision
        finally:
            self.card_closed.set()


@asynccontextmanager
async def _running(*, decision="allow", plan=False):
    gateway = Gateway()
    async with serve(gateway.run, "127.0.0.1", 0) as server:
        source = OpenClawApprovals(server.sockets[0].getsockname()[1], _TOKEN, _SESSION)
        turn = AcpTurn("turn-current", ".", "Read the fixture", auto_deny=plan,
                       approval_source=source)
        turn._acp_session = "acp-current"
        io = IO(decision)
        try:
            await turn._send_prompt(io)
            assert io.frames[-1]["method"] == "session/prompt"
            yield gateway, source, turn, io
        finally:
            await source.close()


async def _tool(turn, io, name="Read"):
    await turn._tool_start({
        "toolCallId": "tool-current", "_meta": {"toolName": name},
        "title": name, "rawInput": {"file_path": "fixture.txt"},
    }, io)


def _approval(name="Read", **overrides):
    now = int(time.time() * 1000)
    payload = {
        "id": "plugin:current", "createdAtMs": now + 1, "expiresAtMs": now + 5000,
        "request": {"sessionKey": _SESSION, "agentId": "main", "runId": "run-current",
                    "toolCallId": "tool-current", "toolName": name,
                    "allowedDecisions": ["allow-once", "allow-always", "deny"]},
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("decision,expected", [
    ("allow", "allow-once"), ("allow_always", "allow-always"),
    ("deny", "deny"), ("cancel", "deny"),
])
async def test_gateway_approval_uses_current_tool_card_and_returns_decision(decision, expected):
    async with _running(decision=decision) as (gateway, source, turn, io):
        await _tool(turn, io)
        payload = _approval()
        await gateway.emit(payload)
        await gateway.emit(payload)  # duplicate delivery must not ask twice
        reply = await asyncio.wait_for(gateway.requests.get(), 2)
        await asyncio.wait_for(asyncio.gather(*tuple(source._pending.values())), 2)
        assert reply["params"] == {"id": "plugin:current", "decision": expected}
        assert io.asked == [("tool-current", "Read", {"file_path": "fixture.txt"}, "Read")]
        assert (any(frame["method"] == "session/cancel" for frame in io.frames)) is (
            decision == "cancel"
        )
        assert gateway.connect["params"]["caps"] == ["approvals", "plugin-approvals"]
        assert _TOKEN not in str(io.events)


async def test_event_before_acp_tool_waits_for_exact_active_tool():
    async with _running() as (gateway, source, turn, io):
        await gateway.emit(_approval())
        await _tool(turn, io)
        reply = await asyncio.wait_for(gateway.requests.get(), 2)
        assert reply["params"]["decision"] == "allow-once"
        assert len(io.asked) == 1


@pytest.mark.parametrize("field,value", [("sessionKey", "agent:other:main"),
                                         ("agentId", "other"), ("toolCallId", "")])
async def test_foreign_or_unbound_requests_never_reach_the_card(field, value):
    async with _running() as (gateway, source, turn, io):
        await _tool(turn, io)
        wrong = _approval(id="plugin:foreign")
        wrong["request"][field] = value
        await gateway.emit(wrong)
        await gateway.emit(_approval(id="plugin:expired", expiresAtMs=0))
        await gateway.emit(_approval(id="plugin:old", createdAtMs=0))
        await gateway.emit(_approval())
        reply = await asyncio.wait_for(gateway.requests.get(), 2)
        assert reply["params"]["id"] == "plugin:current"
        assert len(io.asked) == 1


async def test_native_approval_without_run_id_is_bound_to_current_acp_tool():
    async with _running() as (gateway, source, turn, io):
        await _tool(turn, io)
        payload = _approval()
        payload["request"]["runId"] = None
        await gateway.emit(payload)
        reply = await asyncio.wait_for(gateway.requests.get(), 2)
        assert reply["params"]["decision"] == "allow-once"
        assert len(io.asked) == 1


@pytest.mark.parametrize("name,expected", [("Read", "allow-once"), ("Grep", "allow-once"),
                                          ("Edit", "deny"), ("Bash", "deny"),
                                          ("unknown_extension", "deny")])
async def test_plan_allows_only_known_read_only_native_tools(name, expected):
    async with _running(plan=True) as (gateway, source, turn, io):
        await _tool(turn, io, name)
        await gateway.emit(_approval(name))
        reply = await asyncio.wait_for(gateway.requests.get(), 2)
        assert reply["params"]["decision"] == expected
        assert io.asked == []


async def test_cancelling_turn_closes_card_denies_pending_and_closes_socket():
    async with _running(decision=None) as (gateway, source, turn, io):
        await _tool(turn, io)
        await gateway.emit(_approval())
        await asyncio.wait_for(io.asking.wait(), 2)
        await source.close()
        reply = await asyncio.wait_for(gateway.requests.get(), 2)
        await asyncio.wait_for(gateway.closed.wait(), 2)
        assert reply["params"]["decision"] == "deny"
        assert io.card_closed.is_set()
        assert source._reader.done() and not source._pending


async def test_connection_loss_visibly_fails_and_cancels_the_turn():
    async with _running() as (gateway, source, turn, io):
        await gateway.socket.close()
        await asyncio.wait_for(source._reader, 2)
        assert turn.status == "error" and turn.saw_result
        assert "approval connection was lost" in turn.error
        assert io.frames[-1]["method"] == "session/cancel"


async def test_unmatched_current_tool_fails_closed_after_bounded_correlation(monkeypatch):
    from jarvis.agent_runtimes import openclaw_approvals

    monkeypatch.setattr(openclaw_approvals, "_CORRELATION_TIMEOUT", 0.01)
    async with _running() as (gateway, source, turn, io):
        await gateway.emit(_approval())
        await asyncio.sleep(0.05)
        assert turn.status == "error" and io.asked == []
        assert io.frames[-1]["method"] == "session/cancel"


async def test_approval_disconnect_reaps_silent_goal_runtime_without_another_stdout_frame(
    monkeypatch, tmp_path
):
    from jarvis.agent_chat import runner_acp, runner_cli
    from jarvis.agent_chat.runner_api import TurnHandle
    from jarvis.agent_chat.store import AgentChatSession

    gateway = Gateway()
    events = []
    processes = []
    released = []
    create = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        proc = await create(*args, **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(runner_cli.asyncio, "create_subprocess_exec", capture)
    monkeypatch.setattr(runner_cli, "_ACP_EXIT_GRACE_S", 0.05)
    async with serve(gateway.run, "127.0.0.1", 0) as server:
        source = OpenClawApprovals(server.sockets[0].getsockname()[1], _TOKEN, _SESSION)

        async def fake_plan(handle, runner, *, prompt, cwd, resume, identity):
            return runner_cli.CliPlan(
                argv=[sys.executable, str(_FAKE_AGENT)],
                env={**os.environ, "FAKE_ACP_IGNORE_CANCEL": "1", "FAKE_ACP_IGNORE_EOF": "1"},
                stdin_text=None, shape="acp", vendor_session=None, keep_stdin=True,
                acp=AcpTurn(handle.turn_id, str(cwd), prompt, approval_source=source),
                after_turn=lambda: released.append(True),
            )

        async def emit(event):
            events.append(event)
            if event["kind"] == "tool_call":
                # TOOLWAIT sends no more stdout. The runtime also ignores
                # cancellation and EOF, so only bounded terminal cleanup wins.
                await gateway.socket.close()

        async def ask(*args):
            raise AssertionError("No approval request is part of this disconnect test")

        monkeypatch.setattr(runner_acp, "plan_runtime_turn", fake_plan)
        session = AgentChatSession(
            session_id="society:disconnect", title="Disconnect", provider="claude-api",
            model="fake-model", effort="", cwd=str(tmp_path), permission_mode="ask",
            vendor_session=None, created_ms=0, updated_ms=0, message_count=0, preview="",
            surface="society", runtime="openclaw",
        )
        handle = TurnHandle(
            session=session, turn_id="disconnect-goal", emit=emit, request_approval=ask,
            cancel=asyncio.Event(), goal_turn=True,
        )
        await asyncio.wait_for(runner_cli.run_cli_turn(handle, "TOOLWAIT", "openclaw-cli"), 10)
        finished = next(event["payload"] for event in events if event["kind"] == "turn_finished")
        assert finished["status"] == "error"
        assert "approval connection was lost" in finished["error"]
        assert processes and all(proc.returncode is not None for proc in processes)
        assert released == [True]
        assert source._reader.done()
