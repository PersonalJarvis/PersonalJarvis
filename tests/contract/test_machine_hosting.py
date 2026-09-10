"""Hosted loops and byte-preserving handoff without a provider key or real remote machine."""

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.machines.host_loop import run_host_loop
from jarvis.machines.hosting import HostedContext
from jarvis.machines.models import MachineCommand, MachineGrant
from jarvis.machines.runner import ConnectorRunner
from jarvis.machines.service import machine_service
from jarvis.machines.transfers import AgentTransfer, file_manifest, write_transfer_chunk
from jarvis.society.runtime import SocietyRuntime


@pytest.mark.asyncio
async def test_remote_loop_executes_only_model_calls_and_stops():
    requests = []

    async def rpc(job, method, payload):
        requests.append(method)
        if method == "tool":
            assert payload["name"] == "remote-machine"
            return {"success": True, "output": "hello"}
        if len(requests) == 1:
            return {
                "text": "",
                "calls": [{"id": "c", "name": "remote-machine", "input": {"operation": "read"}}],
            }
        assert payload["messages"][-1]["role"] == "tool"
        return {"text": "Done", "calls": []}

    command = MachineCommand(
        job_id="j",
        agent_id="a",
        trace_id="t",
        operation="agent_turn",
        args={"messages": []},
        grant=MachineGrant(agent_id="a", machine_id="m", workspace="/work"),
    )
    assert (await run_host_loop(command, rpc, lambda: None))["text"] == "Done"
    assert requests == ["model", "tool", "model"]


@pytest.mark.asyncio
async def test_host_cannot_invent_tool_calls():
    async def caller(_):
        return SimpleNamespace(state="active")

    async def kill_switch():
        return False

    runtime = SimpleNamespace(
        roster=SimpleNamespace(get=caller), store=SimpleNamespace(kill_switch=kill_switch)
    )
    handle = SimpleNamespace(
        session=SimpleNamespace(session_id="society:a"), cancel=asyncio.Event()
    )
    context = HostedContext(runtime, handle, "m", None, set(), {})
    with pytest.raises(PermissionError, match="not issued"):
        await context.answer(
            "tool", {"id": "invented", "name": "remote-machine", "input": {"command": "bad"}}
        )


class RunnerSocket:
    def __init__(self, hub, machine_id, runner):
        self.hub, self.machine_id, self.runner = hub, machine_id, runner

    async def send_json(self, message):
        if message["type"] == "command":
            self.runner.renew(30)
            result = await self.runner.run(MachineCommand.model_validate(message["command"]))
            await self.hub.complete(self.machine_id, result)


@pytest.mark.asyncio
async def test_workspace_move_to_host_and_back_preserves_binary_and_identity(tmp_path):
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path / "hub")))
    runtime = SocietyRuntime(tmp_path / "hub", cfg=lambda: cfg)
    await runtime.store.open()
    try:
        agent, _ = await runtime.roster.create(name="Writer", provider="openai", model="test")
        source = tmp_path / "hub" / agent.workspace_dir
        source.mkdir(parents=True)
        data = b"\x00\xff" * (1024 * 1024 + 1)
        (source / "binary.dat").write_bytes(data)
        target = tmp_path / "target"
        target.mkdir()
        hub = machine_service(runtime.data_dir)
        await hub.start()
        from jarvis.machines.connector import capabilities

        caps = capabilities()
        machine_id, _ = await hub.store.enroll(await hub.store.pair_code("target"), caps)
        await hub.store.put_grant(
            MachineGrant(
                agent_id=agent.agent_id,
                machine_id=machine_id,
                workspace=str(target),
                files=True,
                scope="account",
            )
        )
        runner = ConnectorRunner(tmp_path / "runner")
        await hub.attach(machine_id, RunnerSocket(hub, machine_id, runner), caps)
        moved = await AgentTransfer(runtime).move(agent.agent_id, machine_id)
        assert moved["state"] == "complete"
        assert (Path(moved["workspace"]) / "binary.dat").read_bytes() == data
        assert (source / "binary.dat").read_bytes() == data
        returned = await AgentTransfer(runtime).move(agent.agent_id, "local")
        assert returned["state"] == "complete"
        assert (Path(returned["workspace"]) / "binary.dat").read_bytes() == data
        assert (await runtime.roster.get(agent.agent_id)).agent_id == agent.agent_id
        assert (await hub.store.rows("SELECT host_id,moving FROM placements")) == [
            {"host_id": "local", "moving": 0}
        ]
    finally:
        await runtime.store.close()


def test_transfer_checksum_and_credential_refusal(tmp_path):
    (tmp_path / ".env").write_text("not a real secret")
    with pytest.raises(PermissionError, match="credentials"):
        file_manifest(tmp_path)
    path = tmp_path / ".jarvis-transfer-test" / "file"
    with pytest.raises(ValueError, match="checksum"):
        write_transfer_chunk(
            path,
            {
                "data": "",
                "offset": 0,
                "final": True,
                "sha256": hashlib.sha256(b"wrong").hexdigest(),
            },
        )


@pytest.mark.asyncio
async def test_clone_has_independent_knowledge_and_no_login(tmp_path):
    from jarvis.machines.cloning import clone_agent

    cfg = SimpleNamespace(
        memory=SimpleNamespace(data_dir=str(tmp_path), vault_root=str(tmp_path / "wiki"))
    )
    runtime = SocietyRuntime(tmp_path, cfg=lambda: cfg)
    await runtime.store.open()
    try:
        original, _ = await runtime.roster.create(
            name="Writer", provider="openai", account_id="private-seat"
        )
        memory = runtime.memory.namespace(runtime.memory.root(), original.agent_id)
        memory.mkdir(parents=True)
        (memory / "note.md").write_text("Useful knowledge")
        workspace = tmp_path / original.workspace_dir
        workspace.mkdir(parents=True)
        (workspace / "selected.bin").write_bytes(b"\x00\xffselected")
        (workspace / "excluded.txt").write_text("not selected")
        result = await clone_agent(runtime, original.agent_id, "Writer copy", ["selected.bin"])
        clone = result["agent"]
        assert clone["agent_id"] != original.agent_id
        assert clone["state"] == "paused"
        assert not clone["account_id"]
        assert (Path(clone["workspace_dir"]) / "selected.bin").read_bytes() == b"\x00\xffselected"
        assert not (Path(clone["workspace_dir"]) / "excluded.txt").exists()
        assert (
            runtime.memory.namespace(runtime.memory.root(), clone["agent_id"]) / "note.md"
        ).read_text() == "Useful knowledge"
        with pytest.raises(ValueError, match="unique"):
            await clone_agent(runtime, original.agent_id, "Writer copy")
    finally:
        await runtime.store.close()


@pytest.mark.asyncio
async def test_hosted_model_proxy_streams_and_authorizes_one_call(monkeypatch):
    from uuid import uuid4

    import jarvis.core.config as config
    from jarvis.core.protocols import BrainDelta, ToolResult

    monkeypatch.setattr(config, "get_jarvis_agent_secret", lambda provider: None)
    emitted = []

    async def emit(event):
        emitted.append(event)

    async def caller(_):
        return SimpleNamespace(state="active")

    async def kill_switch():
        return False

    class Brain:
        async def complete(self, request):
            assert request.system == "standing instructions"
            yield BrainDelta(content="Looking at the remote file.")
            yield BrainDelta(
                tool_call={"id": "call", "name": "remote-machine", "input": {"operation": "read"}}
            )

    class Executor:
        def __init__(self):
            self.calls = 0

        async def execute(self, tool, args, **kwargs):
            self.calls += 1
            return ToolResult(True, "file contents")

    handle = SimpleNamespace(
        session=SimpleNamespace(session_id="society:a", provider="openai"),
        cancel=asyncio.Event(),
        emit=emit,
        turn_id="turn",
        trace_id=uuid4(),
    )
    runtime = SimpleNamespace(
        roster=SimpleNamespace(get=caller), store=SimpleNamespace(kill_switch=kill_switch)
    )
    context = HostedContext(runtime, handle, "m", None, set(), {})
    context.system = "standing instructions"
    context.provider = Brain()
    context.executor = Executor()
    context.override = SimpleNamespace(reasoning_effort=None, tool_context={})
    context.tools = {
        "remote-machine": SimpleNamespace(name="remote-machine", description="Read", schema={})
    }
    result = await context.answer("model", {"messages": [{"role": "user", "content": "Read"}]})
    assert result["text"] == "Looking at the remote file."
    call = result["calls"][0]
    assert (await context.answer("tool", call))["success"]
    assert context.executor.calls == 1
    with pytest.raises(PermissionError, match="already executed"):
        await context.answer("tool", call)
    assert any(event["kind"] == "text_delta" for event in emitted)
