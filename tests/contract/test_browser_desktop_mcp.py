"""Desktop bootstrap and subscription agents must discover the owned browser."""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
from jarvis.core.protocols import SupervisorToolRequest, ToolResult


async def test_desktop_bootstrap_publishes_the_mcp_endpoint(monkeypatch):
    from jarvis.agent_chat import jarvis_harness
    from jarvis.core import runtime_refs
    from jarvis.ui.web.server import WebServer

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


async def test_plan_session_gets_readonly_browser_but_not_coding_control(tmp_path):
    from jarvis.society.runtime import SocietyRuntime
    from jarvis.society.surface import browser_tool_for_session, coding_tool_for_session

    rt = SocietyRuntime(tmp_path, seed_starter_team=False)
    await rt.ensure_started()
    try:
        await rt.roster.create(name="Nala", permission_ceiling="safe")
        rt.chat_service = lambda: SimpleNamespace(
            store=SimpleNamespace(get_session=lambda _: SimpleNamespace(permission_mode="plan"))
        )
        browser = await browser_tool_for_session("society:nala")
        assert browser is not None and browser.risk_tier == "safe"
        assert browser.is_action_tool is False
        assert await coding_tool_for_session("society:nala") is None
        await rt.roster.update("nala", {"denies": ["core:browser"]})
        assert await browser_tool_for_session("society:nala") is None
    finally:
        await rt.close()


@pytest.mark.parametrize("action_name", ["navigate", "input", "click", "upload_file"])
async def test_readonly_browser_enforces_action_boundary(tmp_path, monkeypatch, action_name):
    from jarvis.society.browser.bridge import execute_live
    from jarvis.society.runtime import SocietyRuntime

    monkeypatch.setattr("jarvis.core.config.get_jarvis_agent_secret", lambda _: None)
    rt = SocietyRuntime(tmp_path, seed_starter_team=False)
    await rt.ensure_started()
    applied = []

    async def apply():
        applied.append(action_name)
        return {}

    async def execute(tool, args, **kwargs):
        assert tool.risk_tier == "safe"
        return await tool.execute(args, None)

    async def run(agent, *, action, **kwargs):
        verdict = await action({"action": {action_name: {}}, "apply": apply})
        return {"ok": verdict["ok"], "error": verdict.get("error"), "artifacts": []}

    rt.browser.live.model_resolver = lambda _: SimpleNamespace(complete=lambda *_: None)
    rt.browser.live.executor = SimpleNamespace(execute=execute)
    rt.browser.live.run = run
    try:
        agent, _ = await rt.roster.create(
            name="Reader", provider="openai", permission_ceiling="safe"
        )
        result = await execute_live(
            rt,
            agent,
            rt.browser,
            {"task": "Read"},
            SimpleNamespace(trace_id="one", config={}),
            read_only=True,
        )
        assert result.success == (action_name == "navigate")
        assert applied == (["navigate"] if action_name == "navigate" else [])
    finally:
        await rt.close()


async def test_root_subscription_browser_uses_chat_model_without_changing_roster(
    tmp_path, monkeypatch
):
    from jarvis.society.runtime import SocietyRuntime
    from jarvis.society.surface import browser_tool_for_session

    rt = SocietyRuntime(tmp_path, seed_starter_team=False)
    await rt.ensure_started()
    seen = []
    session = SimpleNamespace(
        surface="jarvis", provider="openai-codex", model="picked-model", permission_mode="ask"
    )
    rt.chat_service = lambda: SimpleNamespace(store=SimpleNamespace(get_session=lambda _: session))

    async def execute(runtime, caller, jobs, args, ctx, *, read_only=False):
        seen.append((caller.agent_id, caller.provider, caller.model))
        return ToolResult(True, {}, None)

    monkeypatch.setattr("jarvis.society.browser.bridge.execute_live", execute)
    try:
        original = await rt.roster.get(rt.lead_id)
        browser = await browser_tool_for_session("root-chat")
        assert browser is not None
        result = await browser.execute({"task": "Read the page"}, SimpleNamespace())
        assert result.success
        assert seen == [(rt.lead_id, "openai-codex", "picked-model")]
        current = await rt.roster.get(rt.lead_id)
        assert (current.provider, current.model) == (original.provider, original.model)
    finally:
        await rt.close()


@pytest.mark.parametrize("session_id", ["society:scout", "root-chat"])
@pytest.mark.parametrize("resume", [None, "existing-vendor-session"])
def test_jarvis_codex_seat_uses_owned_browser_not_inherited_plugins(
    tmp_path, monkeypatch, session_id, resume
):
    from jarvis.agent_chat import jarvis_harness, runner_cli

    monkeypatch.setattr(runner_cli, "codex_argv_prefix", lambda: ["codex"])
    monkeypatch.setattr(runner_cli, "_account_env", lambda _: {})
    monkeypatch.setattr(
        jarvis_harness, "endpoint", lambda: "http://127.0.0.1:47821/api/control/mcp/"
    )
    monkeypatch.setattr(jarvis_harness, "control_key", lambda: "test-placeholder")
    args = dict(
        prompt="Use the browser",
        cwd=tmp_path,
        model="",
        effort="low",
        permission_mode="ask",
        resume=resume,
    )
    identity = jarvis_harness.Identity(session_id, "identity", "identity")
    plan = runner_cli.plan_codex(**args, identity=identity)
    assert "--ignore-user-config" in plan.argv
    assert "--ignore-rules" in plan.argv
    assert "browser_use" in plan.argv and "plugins" in plan.argv
    assert 'mcp_servers.jarvis.tools.society_browser.approval_mode="approve"' in plan.argv
    assert "mcp_servers.jarvis.required=true" in plan.argv
    coding = runner_cli.plan_codex(**args)
    assert "--ignore-user-config" not in coding.argv
    assert "browser_use" not in coding.argv


async def test_failed_browser_mcp_preserves_partial_outcome_and_error_flag(monkeypatch):
    import json

    import mcp.types as types

    from jarvis.mcp import jarvis_tools_server as server

    tool = SimpleNamespace(
        name="society_browser",
        description="Browser",
        risk_tier="monitor",
        is_action_tool=True,
        input_schema={"type": "object"},
    )
    partial = {"ok": False, "urls": ["https://example.org/receipt"], "steps": 3}

    async def execute(name, args, request):
        return ToolResult(False, partial, "Model timed out after navigation")

    gateway = SimpleNamespace(catalog=lambda: [tool], execute=execute)
    monkeypatch.setattr(server, "_gateway", lambda: gateway)
    instance = server.build_server()
    request = types.CallToolRequest(
        params=types.CallToolRequestParams(name="society_browser", arguments={"task": "Read"})
    )
    response = await instance.request_handlers[types.CallToolRequest](request)
    assert response.root.isError
    body = json.loads(response.root.content[0].text)
    assert body["result"] == partial
    assert body["error"] == "Model timed out after navigation"
    assert "Inspect current state" in body["retry"]


async def test_cli_browser_retries_keep_the_user_turn_trace(monkeypatch):
    import mcp.types as types

    from jarvis.agent_chat.tool_context import register_turn, unregister_turn
    from jarvis.core.protocols import ChatTurn, current_chat_turn
    from jarvis.mcp import jarvis_tools_server as server

    seen = []
    tool = SimpleNamespace(
        name="society_browser",
        description="Browser",
        risk_tier="monitor",
        is_action_tool=True,
        input_schema={"type": "object"},
    )

    async def execute(name, args, request):
        seen.append(request.trace_id)
        return ToolResult(True, {}, None)

    monkeypatch.setattr(
        server, "_gateway", lambda: SimpleNamespace(catalog=lambda: [tool], execute=execute)
    )
    instance = server.build_server()
    request = types.CallToolRequest(
        params=types.CallToolRequestParams(name="society_browser", arguments={"task": "Read"})
    )
    first, second = uuid4(), uuid4()
    token = current_chat_turn.set(ChatTurn("chat", "first", "Read", True, str(first)))
    ref = server.CHAT_SESSION_REF.set("chat")
    registered = register_turn("chat")
    try:
        await instance.request_handlers[types.CallToolRequest](request)
        await instance.request_handlers[types.CallToolRequest](request)
        current_chat_turn.set(ChatTurn("chat", "second", "Try again", True, str(second)))
        registered = register_turn("chat")
        await instance.request_handlers[types.CallToolRequest](request)
    finally:
        unregister_turn("chat", registered)
        server.CHAT_SESSION_REF.reset(ref)
        current_chat_turn.reset(token)
    assert seen == [first, first, second]
