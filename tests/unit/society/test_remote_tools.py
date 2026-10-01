"""Real SSH forwarding, turn isolation, CLI wiring and durable routine proof."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shlex
from types import SimpleNamespace

import httpx
import pytest

from jarvis.agent_chat.remote_mcp import RemoteMcpBridge, RemoteToolsUnavailable, ScopedMcpApp
from jarvis.agent_chat.remote_mcp_config import SUPPORTED_RUNNERS, TOKEN_ENV, configure
from jarvis.agent_chat.tool_context import register_turn, unregister_turn
from jarvis.computers.remote_os import RemoteHost
from jarvis.computers.ssh import SshTarget, open_session
from jarvis.core.protocols import ChatTurn, current_chat_turn
from tests.fakes.fake_ssh_server import TEST_PASSWORD, FakeSshServer, FakeSshState
from tests.unit.society.test_cli_routines import world  # noqa: F401 — reusable world fixture


@pytest.fixture
def active_turn():
    turn = ChatTurn("society:scout", "remote-turn", "Check issues daily at 09:00", True, "trace")
    token = current_chat_turn.set(turn)
    registered = register_turn(turn.session_id)
    current_chat_turn.reset(token)
    try:
        yield turn
    finally:
        unregister_turn(turn.session_id, registered)


@pytest.fixture
async def connection(tmp_path):
    root = tmp_path / "remote"
    root.mkdir()
    server = FakeSshServer(FakeSshState(port_forwarding=True), sftp_root=root)
    await server.start()
    opened = await open_session(
        SshTarget(
            "127.0.0.1",
            server.port,
            "root",
            password=TEST_PASSWORD,
            host_key=server.host_key.export_public_key().decode(),
        )
    )
    try:
        yield SimpleNamespace(conn=opened.conn, server=server, root=root)
    finally:
        opened.conn.close()
        await opened.conn.wait_closed()
        await server.stop()


async def echo(scope, receive, send):
    headers = dict(scope["headers"])
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send(
        {
            "type": "http.response.body",
            "body": json.dumps(
                {
                    "session": headers[b"x-jarvis-chat-session"].decode(),
                    "path": scope["path"],
                }
            ).encode(),
        }
    )


@pytest.fixture
def scoped_app(active_turn, monkeypatch):
    monkeypatch.setattr("jarvis.core.control_key.get_control_key", lambda: "fixture-owner-key")
    return ScopedMcpApp(active_turn.session_id, active_turn.turn_id, echo)


@pytest.mark.parametrize("path", ["/api/control/config", "/mcp/agents", "/mcp?session=other"])
async def test_only_tools_path_is_exposed(scoped_app, path):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=scoped_app), base_url="http://test"
    ) as c:
        response = await c.post(path, headers={"Authorization": "Bearer " + scoped_app.token})
    assert response.status_code == 404


async def test_remote_headers_cannot_change_session(scoped_app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=scoped_app), base_url="http://test"
    ) as c:
        response = await c.post(
            "/mcp",
            headers={
                "Authorization": "Bearer " + scoped_app.token,
                "X-Jarvis-Chat-Session": "society:other",
            },
        )
    assert response.json() == {"session": "society:scout", "path": "/api/control/mcp/"}


@pytest.mark.parametrize("state", ["wrong-token", "revoked", "expired", "new-turn", "no-turn"])
async def test_stale_or_invalid_capability_is_rejected(scoped_app, active_turn, state):
    if state == "revoked":
        scoped_app.revoke()
    if state == "expired":
        scoped_app.expires = 0
    if state in {"new-turn", "no-turn"}:
        from jarvis.agent_chat import tool_context

        tool_context._active.pop(active_turn.session_id)
    context = None
    if state == "new-turn":
        token = current_chat_turn.set(ChatTurn(active_turn.session_id, "new", "yes", True, "trace"))
        context = register_turn(active_turn.session_id)
        current_chat_turn.reset(token)
    credential = "wrong" if state == "wrong-token" else scoped_app.token
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=scoped_app), base_url="http://test"
        ) as c:
            response = await c.post("/mcp", headers={"Authorization": "Bearer " + credential})
        assert response.status_code == 401
    finally:
        unregister_turn(active_turn.session_id, context)


async def test_real_reverse_ssh_forward_and_cleanup(connection, active_turn, monkeypatch):
    monkeypatch.setattr("jarvis.core.control_key.get_control_key", lambda: "fixture-owner-key")
    monkeypatch.setattr("jarvis.ui.web.mcp_server_routes.build_mcp_asgi_app", lambda: echo)
    bridge = await RemoteMcpBridge.open(connection.conn, active_turn.session_id)
    try:
        async with httpx.AsyncClient() as c:
            response = await c.post(
                bridge.url, headers={"Authorization": "Bearer " + bridge.app.token}
            )
            assert response.json()["session"] == active_turn.session_id
            assert (await c.post(bridge.url)).status_code == 401
    finally:
        await bridge.aclose()
    assert not bridge.app.active
    assert bridge._task.done()
    async with httpx.AsyncClient(timeout=1) as c:
        with pytest.raises(httpx.TransportError):
            await c.post(bridge.url)


async def test_forward_refusal_closes_listener(connection, active_turn):
    connection.server.state.port_forwarding = False
    before = {t for t in asyncio.all_tasks() if t.get_name() == "remote-jarvis-tools"}
    with pytest.raises(RemoteToolsUnavailable, match="TCP forwarding"):
        await RemoteMcpBridge.open(connection.conn, active_turn.session_id)
    assert {t for t in asyncio.all_tasks() if t.get_name() == "remote-jarvis-tools"} == before


async def test_bridge_requires_registered_turn(connection):
    with pytest.raises(RemoteToolsUnavailable, match="active chat turn"):
        await RemoteMcpBridge.open(connection.conn, "society:scout")


async def test_remote_cli_spawn_mounts_tools_and_reaps_without_caller_wait(
    connection,
    active_turn,
    monkeypatch,
):
    from jarvis.agent_chat import remote_cli

    @contextlib.asynccontextmanager
    async def session(_computer):
        yield connection

    async def host(_computer, _opened):
        return RemoteHost()

    monkeypatch.setattr(
        "jarvis.computers.service.get_service",
        lambda: SimpleNamespace(
            session=session,
            get=lambda _: SimpleNamespace(name="Test computer", facts=None),
        ),
    )
    monkeypatch.setattr("jarvis.computers.remote_os.remote_host", host)
    monkeypatch.setattr("jarvis.core.control_key.get_control_key", lambda: "fixture-owner-key")
    monkeypatch.setattr("jarvis.ui.web.mcp_server_routes.build_mcp_asgi_app", lambda: echo)

    async def handler(command, process):
        script = await process.stdin.read() if command.endswith(" -s") else command
        if "command -v" in script:
            process.stdout.write("/workspace\nfound\n")
        else:
            parts = shlex.split(command)
            if "--mcp-config" not in parts:
                launcher = (connection.root / parts[-1].lstrip("/")).read_text()
                parts = shlex.split(next(line[5:] for line in launcher.splitlines()
                                         if line.startswith("exec ")))
            config_path = parts[parts.index("--mcp-config") + 1]
            config = json.loads((connection.root / config_path.lstrip("/")).read_text())
            server = config["mcpServers"]["jarvis"]
            async with httpx.AsyncClient() as client:
                response = await client.post(server["url"], headers=server["headers"])
            process.stdout.write(response.text + "\n")
        process.exit(0)
        return True

    connection.server.state.handler = handler
    proc = await remote_cli.spawn(
        "fixture",
        agent_id="scout",
        runner="claude-cli",
        binary="claude",
        argv=["claude", "--print"],
        local_cwd="/local",
        env={"SECRET": "must-stay-local"},
        session_id=active_turn.session_id,
    )
    response = json.loads(await proc.stdout.readline())
    assert response["session"] == active_turn.session_id
    # Resource cleanup must not depend on the runner reaching proc.wait().
    await asyncio.wait_for(proc._reaper, timeout=10)
    assert proc._closed
    assert not list((connection.root / "workspace").glob(".jarvis-mcp-*.json"))
    assert not any("must-stay-local" in command for command in connection.server.state.commands)
    assert await proc.wait() == 0


async def test_disconnect_and_cancel_revoke_bridge(connection, active_turn):
    bridge = await RemoteMcpBridge.open(connection.conn, active_turn.session_id)
    connection.conn.close()
    await connection.conn.wait_closed()
    await bridge.aclose()
    assert not bridge.app.active
    assert bridge._task.done()


async def test_project_configs_cannot_cross_concurrent_sessions(connection):
    async with contextlib.AsyncExitStack() as first, contextlib.AsyncExitStack() as second:
        kwargs = dict(
            runner="grok-cli",
            cwd="/workspace",
            url="http://127.0.0.1:23456/mcp",
            token="fixture",  # noqa: S106 — test capability
        )
        await configure(connection, RemoteHost(), first, **kwargs)
        with pytest.raises(RemoteToolsUnavailable, match="Another turn"):
            await configure(connection, RemoteHost(), second, **kwargs)


@pytest.mark.parametrize("runner", sorted(SUPPORTED_RUNNERS))
async def test_runner_wiring_and_file_cleanup(connection, runner):
    root = connection.root
    workspace = root / "workspace"
    workspace.mkdir()
    old_files = {
        ".grok/config.toml": 'theme = "dark"\n',
        ".cursor/mcp.json": '{"mcpServers":{"other":{"url":"https://example.test"}}}',
    }
    for name, body in old_files.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    async with contextlib.AsyncExitStack() as stack:
        config = await configure(
            connection,
            RemoteHost(),
            stack,
            runner=runner,
            cwd="/workspace",
            url="http://127.0.0.1:23456/mcp",
            token="turn-only",  # noqa: S106 — isolated fake SSH server
        )
        assert config.apply(["cli", "--print"])[0] == "cli"
        assert config.env[TOKEN_ENV] == "turn-only"
        content = " ".join(p.read_text() for p in workspace.rglob("*") if p.is_file())
        encoded = json.dumps(config.env) + json.dumps(config.args) + content
        assert "http://127.0.0.1:23456/mcp" in encoded
        assert "fixture-owner-key" not in encoded
        if runner == "claude-cli":
            loaded = json.loads((connection.root / config.args[1].lstrip("/")).read_text())
            assert loaded["mcpServers"]["jarvis"]["type"] == "http"
        if runner == "codex-cli":
            assert "mcp_servers.jarvis.required=true" in config.args
            assert 'mcp_servers.jarvis.default_tools_approval_mode="approve"' in config.args
        if runner == "cursor-cli":
            assert '"other"' in content
        if runner == "grok-cli":
            assert 'theme = "dark"' in content
    remaining = {
        p.relative_to(workspace).as_posix(): p.read_text()
        for p in workspace.rglob("*")
        if p.is_file()
    }
    assert remaining == old_files


async def test_cleanup_preserves_concurrent_changes(connection):
    async with contextlib.AsyncExitStack() as stack:
        await configure(
            connection,
            RemoteHost(),
            stack,
            runner="cursor-cli",
            cwd="/workspace",
            url="http://127.0.0.1:23456/mcp",
            token="turn-only",  # noqa: S106 — isolated fake SSH server
        )
        path = connection.root / "workspace/.cursor/mcp.json"
        path.write_text('{"user":"changed"}')
    assert json.loads(path.read_text()) == {"user": "changed"}


async def test_routine_roundtrip_over_ssh_persists_after_reopen(
    connection,
    active_turn,
    world,  # noqa: F811 — imported pytest fixture
    monkeypatch,
    tmp_path,
):
    from jarvis.core.bus import EventBus
    from jarvis.tasks.scheduler import TaskScheduler
    from jarvis.tasks.store import TaskStore
    from jarvis.ui.web import mcp_server_routes as routes

    # Real persistence and scheduler; no runner or scheduling loop is started.
    store = TaskStore(tmp_path / "persistent-tasks.db")
    await store.init()
    scheduler = TaskScheduler(store, EventBus())
    world.rt.task_services = lambda: (store, scheduler)
    monkeypatch.setattr("jarvis.core.control_key.get_control_key", lambda: "fixture-owner-key")
    monkeypatch.setattr(
        "jarvis.core.control_key.verify_control_key", lambda key: key == "fixture-owner-key"
    )
    surface = routes._Surface("tools", routes._build_tools_server)
    monkeypatch.setattr(routes, "_TOOLS_SURFACE", surface)
    monkeypatch.setitem(routes._SUFFIX_SURFACES, "", surface)
    bridge = await RemoteMcpBridge.open(connection.conn, active_turn.session_id)
    headers = {
        "Authorization": "Bearer " + bridge.app.token,
        "Accept": "application/json, text/event-stream",
    }
    try:
        async with httpx.AsyncClient(headers=headers) as c:

            async def call(method, params):
                response = await c.post(
                    bridge.url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
                )
                assert response.status_code == 200, response.text
                return response.json()["result"]

            catalog = await call("tools/list", {})
            assert {"society_routines", "society_propose_change"} <= {
                t["name"] for t in catalog["tools"]
            }
            result = await call(
                "tools/call",
                {
                    "name": "society_propose_change",
                    "arguments": {
                        "kind": "routine",
                        "mode": "apply",
                        "request_quote": active_turn.user_text,
                        "payload": {
                            "title": "Issue report",
                            "prompt": "Read and rank repository issues.",
                            "schedule": {
                                "kind": "calendar",
                                "local_time": "09:00",
                                "timezone": "Europe/Berlin",
                            },
                        },
                    },
                },
            )
            assert not result["isError"], result
            assert json.loads(result["content"][0]["text"])["applied"] is True
            result = await call("tools/call", {"name": "society_routines", "arguments": {}})
            saved = json.loads(result["content"][0]["text"])["routines"]
            assert len(saved) == 1
            assert saved[0]["trigger"]["timezone"] == "Europe/Berlin"
            assert saved[0]["state"] == "scheduled"
            assert saved[0]["due_at_ns"] > 0
            await scheduler.shutdown()
            await store.close()
            await store.init()
            result = await call("tools/call", {"name": "society_routines", "arguments": {}})
            restored = json.loads(result["content"][0]["text"])["routines"]
            assert restored[0]["id"] == saved[0]["id"]
            assert restored[0]["trigger"]["local_time"] == "09:00"
    finally:
        await bridge.aclose()
        await scheduler.shutdown()
        if surface._task is not None:
            surface._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await surface._task
        await store.close()


async def test_expired_request_cannot_borrow_next_turn(world, active_turn):  # noqa: F811
    import mcp.types as types

    from jarvis.agent_chat.tool_context import required_turn_id
    from jarvis.mcp.jarvis_tools_server import CHAT_SESSION_REF

    entered, release = asyncio.Event(), asyncio.Event()
    original = world.gateway.session_catalog

    async def delayed(session_id):
        entered.set()
        await release.wait()
        return await original(session_id)

    world.gateway.session_catalog = delayed
    session = CHAT_SESSION_REF.set(active_turn.session_id)
    bound = required_turn_id.set(active_turn.turn_id)
    task = asyncio.create_task(
        world.server.request_handlers[types.CallToolRequest](
            types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(
                    name="society_routines",
                    arguments={},
                ),
            )
        )
    )
    required_turn_id.reset(bound)
    CHAT_SESSION_REF.reset(session)
    await entered.wait()
    token = current_chat_turn.set(ChatTurn(active_turn.session_id, "next", "yes", True, "trace"))
    registered = register_turn(active_turn.session_id)
    current_chat_turn.reset(token)
    release.set()
    try:
        result = (await task).root
        assert result.isError
        assert "turn has ended" in result.content[0].text
        assert world.executor.calls == []
    finally:
        unregister_turn(active_turn.session_id, registered)
