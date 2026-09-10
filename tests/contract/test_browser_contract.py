"""Deterministic tests for browser contracts and policy boundaries."""

from types import SimpleNamespace
from pathlib import Path
import pytest
from jarvis.society.browser.bridge import brain_messages
from jarvis.society.capabilities import (
    capability_id_for_tool,
    tool_name_for_capability,
    build_catalog,
)
from jarvis.ui.web.society_browser_routes import validate_control


def test_browser_capability_roundtrip():
    assert capability_id_for_tool("society_browser") == "core:browser"
    assert tool_name_for_capability("core:browser") == "society_browser"
    rows = build_catalog(
        {"society_browser": SimpleNamespace(description="Browse", risk_tier="monitor")}
    )
    assert "browser-use" in rows[0].aliases


async def test_stopped_browser_turn_cannot_retry_but_new_user_turn_can(tmp_path):
    from jarvis.society.browser.live import LiveSessions
    from jarvis.society.browser.bridge import execute_live

    live = LiveSessions(tmp_path)
    live.stop_turn("agent", "denied-turn")
    jobs = SimpleNamespace(live=live)
    caller = SimpleNamespace(agent_id="agent")
    denied = await execute_live(None, caller, jobs, {}, SimpleNamespace(trace_id="denied-turn"))
    assert not denied.success and "new user request" in denied.error
    fresh = await execute_live(None, caller, jobs, {}, SimpleNamespace(trace_id="new-turn"))
    assert "not ready" in fresh.error  # A fresh turn reaches normal readiness checks.


@pytest.mark.parametrize("chat_id", ["", "jarvis-root-test"])
async def test_stop_button_also_stops_the_owning_chat(monkeypatch, chat_id):
    import asyncio
    from jarvis.ui.web import society_browser_routes as routes

    seen = []
    lock = asyncio.Lock()
    await lock.acquire()
    session = SimpleNamespace(closed=False, run_lock=lock, active_chat=chat_id)

    async def cancel_browser(value):
        assert value is session
        seen.append("browser")

    async def cancel_chat(value):
        seen.append(value)

    async def resolve(_):
        return SimpleNamespace(agent_id="test")

    async def runtime(_):
        return SimpleNamespace(
            roster=SimpleNamespace(resolve=resolve),
            browser=SimpleNamespace(
                live=SimpleNamespace(sessions={"test": session}, cancel=cancel_browser)
            ),
        )

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(agent_chat=SimpleNamespace(cancel=cancel_chat)))
    )
    monkeypatch.setattr(routes, "_runtime", runtime)
    assert await routes.cancel_agent_browser("test", request) == {"cancelled": True}
    assert seen == ["browser", chat_id or "society:test"]


async def test_jarvis_chat_uses_the_lead_browser_with_its_selected_model(tmp_path, monkeypatch):
    from jarvis.agent_chat.surface_kits import kit_for
    from jarvis.agent_chat.tool_catalog import build_catalog, resolve_choices
    from jarvis.society.runtime import SocietyRuntime
    from jarvis.core.protocols import ToolResult

    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    await runtime.ensure_started()
    seen = []

    async def execute(rt, caller, jobs, args, ctx):
        seen.append((caller.agent_id, caller.provider, caller.model))
        return ToolResult(True, {"ok": True}, None)

    monkeypatch.setattr("jarvis.society.browser.bridge.execute_live", execute)
    try:
        original = await runtime.roster.get(runtime.lead_id)
        session = SimpleNamespace(
            provider="openrouter",
            model="selected-model",
            cwd=str(tmp_path / "chat"),
            permission_mode="ask",
        )
        tools = kit_for("jarvis").session_tools(None, None, session)
        assert "Read" in tools
        choice = resolve_choices(["tool:society_browser"], build_catalog(tools))[0]
        assert choice.tool_names == ("society_browser",)
        result = await tools["society_browser"].execute(
            {"task": "Read the page"}, SimpleNamespace()
        )
        assert result.success
        assert seen == [(runtime.lead_id, "openrouter", "selected-model")]
        current = await runtime.roster.get(runtime.lead_id)
        assert (current.provider, current.model) == (original.provider, original.model)
    finally:
        await runtime.close()


async def test_upload_rejects_files_outside_the_agent_workspace(tmp_path, monkeypatch):
    from jarvis.society.browser.live import LiveSessions
    from jarvis.society.browser.bridge import execute_live

    monkeypatch.setattr("jarvis.core.config.get_jarvis_agent_secret", lambda _: None)
    live = LiveSessions(tmp_path)
    live.executor = object()
    live.model_resolver = lambda _: SimpleNamespace(complete=lambda *_: None)
    outside = tmp_path / "outside.txt"
    outside.write_text("private fixture", encoding="utf-8")
    result = await execute_live(
        SimpleNamespace(data_dir=tmp_path),
        SimpleNamespace(agent_id="test", provider="openai"),
        SimpleNamespace(live=live),
        {"task": "Upload", "files": [str(outside)]},
        SimpleNamespace(trace_id="test"),
    )
    assert not result.success and "inside this agent's workspace" in result.error
    assert not live.sessions


@pytest.mark.parametrize(
    "value",
    [
        {"op": "evaluate", "args": {}},
        {"op": "click", "args": {"x": float("nan"), "y": 1}},
        {"op": "click", "args": {"x": 1}},
        {"op": "text", "args": {"text": "x" * 8193}},
    ],
)
def test_invalid_remote_controls_are_rejected(value):
    with pytest.raises(ValueError):
        validate_control(value)


def test_browser_multimodal_messages_preserve_images():
    messages = brain_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at this"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}},
                ],
            }
        ]
    )
    assert messages[0].content == "Look at this"
    assert messages[0].images[0].data_b64 == "YQ=="


async def test_slow_viewer_keeps_approval_and_only_latest_pixels():
    from jarvis.society.browser.live import LiveUpdates

    buffer = LiveUpdates()
    buffer.put_nowait({"kind": "approval", "id": "one"})
    for sequence in range(100):
        buffer.put_nowait({"kind": "frame", "sequence": sequence})
    assert (await buffer.get())["id"] == "one"
    assert (await buffer.get())["sequence"] == 99


async def test_slow_viewer_does_not_clear_a_newer_approval():
    from jarvis.society.browser.live import LiveUpdates

    buffer = LiveUpdates()
    buffer.put_nowait({"kind": "approval", "id": "old"})
    buffer.put_nowait({"kind": "approval_cleared", "id": "old"})
    buffer.put_nowait({"kind": "approval", "id": "new"})
    assert await buffer.get() == {"kind": "approval_cleared", "id": "old"}
    assert await buffer.get() == {"kind": "approval", "id": "new"}


def test_browser_children_do_not_inherit_provider_credentials(monkeypatch, tmp_path):
    from jarvis.society.browser.install import worker_env

    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    monkeypatch.setenv("EXAMPLE_ACCESS_TOKEN", "test-placeholder")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-placeholder")
    monkeypatch.setenv("DATABASE_PASSWORD", "test-placeholder")
    monkeypatch.setenv("PIP_INDEX_URL", "https://example.invalid/simple")
    env = worker_env(tmp_path)
    assert "OPENAI_API_KEY" not in env
    assert "EXAMPLE_ACCESS_TOKEN" not in env
    assert "AWS_ACCESS_KEY_ID" not in env
    assert "DATABASE_PASSWORD" not in env
    assert env["PYTHON_DOTENV_DISABLED"] == "1"
    assert "PIP_INDEX_URL" not in env
    assert env["ANONYMIZED_TELEMETRY"] == "false"


def test_managed_python_uses_windows_emulation_only_where_needed():
    from jarvis.society.browser.install import managed_python_request

    assert managed_python_request("win32", "ARM64") == "cpython-3.12-windows-x86_64-none"
    assert managed_python_request("win32", "AMD64") == "3.12"
    assert managed_python_request("linux", "aarch64") == "3.12"
    assert managed_python_request("darwin", "arm64") == "3.12"


@pytest.mark.parametrize("burst", [False, True])
async def test_viewer_disconnect_cancels_pending_takeover(monkeypatch, burst):
    import asyncio
    from starlette.websockets import WebSocketDisconnect
    from jarvis.ui.web import society_browser_routes as routes

    started = asyncio.Event()
    cancelled = asyncio.Event()
    released = asyncio.Event()
    completed = asyncio.Event()
    seen = []

    class Live:
        async def subscribe(self, agent):
            return object(), asyncio.Queue()

        async def control(self, *args):
            started.set()
            if burst:
                await asyncio.sleep(0.01)
                seen.append(args[-1]["text"])
                if len(seen) == 20:
                    completed.set()
                return {}
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def unsubscribe(self, *args):
            released.set()

    class Socket:
        scope = {}
        reads = 0

        async def accept(self):
            pass  # Test socket has no transport to accept.

        async def close(self, **kwargs):
            pass  # Test socket has no transport to close.

        async def receive_json(self):
            self.reads += 1
            if burst:
                if self.reads <= 20:
                    return {"op": "text", "args": {"text": str(self.reads)}}
                await completed.wait()
                raise WebSocketDisconnect()
            if self.reads == 1:
                return {"op": "takeover", "args": {"enabled": True}}
            await started.wait()
            raise WebSocketDisconnect()

        async def send_json(self, value):
            pass  # No response is expected before the disconnect.

    async def resolve(_):
        return SimpleNamespace(agent_id="test")

    async def runtime(_):
        return SimpleNamespace(
            roster=SimpleNamespace(resolve=resolve), browser=SimpleNamespace(live=Live())
        )

    monkeypatch.setattr(routes, "_runtime", runtime)
    monkeypatch.setattr(routes, "credentials_valid", lambda _: True)
    await asyncio.wait_for(routes.agent_browser_live(Socket(), "test"), 2)
    if burst:
        assert seen == [str(n) for n in range(1, 21)]
    else:
        assert cancelled.is_set()
    assert released.is_set()
