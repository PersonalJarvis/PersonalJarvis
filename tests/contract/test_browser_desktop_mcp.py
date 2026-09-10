"""Desktop bootstrap and subscription agents must discover the owned browser."""

from types import SimpleNamespace
from uuid import uuid4
import pytest
from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
from jarvis.core.protocols import SupervisorToolRequest, ToolResult


async def test_desktop_bootstrap_publishes_the_mcp_endpoint(monkeypatch):
    from jarvis.ui.web.server import WebServer
    from jarvis.core import runtime_refs
    from jarvis.agent_chat import jarvis_harness

    urls = []
    monkeypatch.setattr(runtime_refs, "set_api_base_url", urls.append)
    monkeypatch.setattr(runtime_refs, "get_api_base_url", lambda: urls[-1] if urls else None)
    monkeypatch.setattr(jarvis_harness, "control_key", lambda: "test-placeholder")

    class ProbeFinished(Exception):
        pass

    def finish():
        raise ProbeFinished()

    probe = SimpleNamespace(
        cfg=SimpleNamespace(ui=SimpleNamespace(admin_api_port=48123)),
        _voice_ready=True,
        _schedule_anyio_pool_warm=finish,
    )
    with pytest.raises(ProbeFinished):
        await WebServer.start(probe, start_serving=False)
    entry = jarvis_harness.agy_mcp_server_entry("society:nala")
    assert entry["serverUrl"] == "http://127.0.0.1:48123/api/control/mcp/"
    assert entry["headers"]["X-Jarvis-Chat-Session"] == "society:nala"


async def test_browser_is_scoped_and_runs_through_the_executor():
    async def forbidden_direct_call(*args):
        raise AssertionError("The gateway must use ToolExecutor")

    tool = SimpleNamespace(
        name="society_browser",
        description="Owned browser",
        schema={"type": "object", "properties": {}},
        risk_tier="monitor",
        execute=forbidden_direct_call,
    )
    seen = []

    async def resolve(session_id):
        return tool if session_id == "society:nala" else None

    async def execute(actual, args, **context):
        seen.append((actual, context))
        return ToolResult(True, "used owned browser", None)

    gateway = BrainSupervisorToolGateway(
        SimpleNamespace(_tools={}, _tool_executor=SimpleNamespace(execute=execute)),
        browser_tool=resolve,
    )
    assert gateway.catalog() == ()
    assert [t.name for t in await gateway.session_catalog("society:nala")] == ["society_browser"]
    assert await gateway.session_catalog("unknown") == ()
    request = SupervisorToolRequest(
        trace_id=uuid4(),
        origin="agent-chat",
        user_utterance="Read example.com",
        config_snapshot={"approval_ref": "agent-chat:society:nala"},
    )
    assert (await gateway.execute("society_browser", {}, request)).success
    assert seen[0][0] is tool
    assert seen[0][1]["config_snapshot"]["approval_ref"] == "agent-chat:society:nala"
    unscoped = SupervisorToolRequest(trace_id=uuid4(), origin="worker", user_utterance="Read")
    assert not (await gateway.execute("society_browser", {}, unscoped)).success
    assert len(seen) == 1
