"""Agent and routine browser calls start the runner without a viewer or paid model."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.agent_chat.store import AgentChatStore
from jarvis.society.browser import install
from jarvis.society.browser import live as live_module
from jarvis.society.browser.tool import BrowserTool
from jarvis.society.runtime import SocietyRuntime
from jarvis.society.surface import society_system_extra, society_tool_filter, society_tools
from tests.fakes import browser_start_runner


@pytest.fixture
async def cold_browser(tmp_path, monkeypatch):
    installs = []
    monkeypatch.setattr(install, "is_installed", lambda _: bool(installs))
    monkeypatch.setattr(install, "ensure_installed", lambda path: installs.append(path))
    monkeypatch.setattr(install, "venv_python", lambda _: Path(sys.executable))
    monkeypatch.setattr(install, "runner_path", lambda: Path(browser_start_runner.__file__))
    monkeypatch.setattr(install, "browser_executable", lambda _: Path("unused-browser"))
    monkeypatch.setattr("jarvis.core.config.get_jarvis_agent_secret", lambda _: None)
    chat_store = AgentChatStore(tmp_path / "chat.sqlite")
    runtime = SocietyRuntime(
        tmp_path, seed_starter_team=False,
        chat_service=lambda: SimpleNamespace(store=chat_store),
    )
    await runtime.ensure_started()
    await runtime.roster.create(name="Scout", provider="ollama", model="fixture")

    def unexpected_model_call(*args, **kwargs):
        raise AssertionError("The startup fixture must not call a model")

    runtime.browser.live.model_resolver = lambda _: SimpleNamespace(
        complete=unexpected_model_call, supports_vision=False
    )
    runtime.browser.live.executor = object()
    try:
        yield runtime, installs
    finally:
        await runtime.close()
        chat_store.close()


@pytest.mark.parametrize("session_id", ["society:scout", "society:scout:routine:task:run"])
async def test_cold_agent_and_routine_start_reuse_and_reopen_browser(
    cold_browser, tmp_path, session_id
):
    runtime, installs = cold_browser
    session = runtime.chat_service().store.create_session(
        session_id=session_id, surface="society", provider="ollama", model="fixture",
        effort="", cwd=str(tmp_path), permission_mode="bypass",
    )
    cfg = SimpleNamespace(wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")))
    briefing = await society_system_extra(cfg, None, session)
    assert "starts the browser automatically" in briefing
    assert "Not set up" not in briefing
    assert not installs and not runtime.browser.live.sessions
    tools = society_tools(cfg, None, session)
    tool = society_tool_filter(session)(tools)[BrowserTool.name]
    ctx = SimpleNamespace(
        trace_id=uuid4(),
        user_utterance="Read the page",
        config={"approval_ref": f"agent-chat:{session_id}"},
        memory_read=None,
    )
    args = {"task": "Read the page", "url": "https://example.com"}
    result = await tool.execute(args, ctx)
    assert result.success, result.error
    first = runtime.browser.live.sessions["scout"]
    assert not first.subscribers and first.control_owner is None
    assert installs == [tmp_path]
    assert (await tool.execute(args, ctx)).success
    assert runtime.browser.live.sessions["scout"] is first
    await first.close()
    assert (await tool.execute(args, ctx)).success
    assert runtime.browser.live.sessions["scout"] is not first
    assert installs == [tmp_path]
    calls = [
        json.loads(line)
        for line in (tmp_path / "society" / "scout" / "workspace" / "browser-start.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [call["op"] for call in calls] == ["ensure", "run", "run", "shutdown", "ensure", "run"]
    assert calls[0]["args"]["profile_dir"] == calls[4]["args"]["profile_dir"]


@pytest.mark.parametrize(
    "rules",
    [
        {"denies": ["core:browser"]},
        {"grant_mode": "allowlist", "grants": []},
    ],
)
async def test_cold_browser_still_respects_denied_capability(cold_browser, tmp_path, rules):
    runtime, installs = cold_browser
    await runtime.roster.update("scout", rules)
    session = SimpleNamespace(session_id="society:scout:routine:task:run", cwd=str(tmp_path))
    cfg = SimpleNamespace(wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")))
    await society_system_extra(cfg, None, session)
    tools = society_tools(cfg, None, session)
    result = await tools[BrowserTool.name].execute(
        {"task": "Read the page"}, SimpleNamespace(trace_id=uuid4())
    )
    assert not result.success and result.output["reason"] == "blocked_by_policy"
    assert not installs and not runtime.browser.live.sessions


async def test_simultaneous_starts_share_one_process(cold_browser):
    runtime, installs = cold_browser
    live = runtime.browser.live
    agent = await runtime.roster.get("scout")
    sessions = await asyncio.gather(*(live.ensure(agent) for _ in range(8)))
    assert all(session is sessions[0] for session in sessions)
    assert len(installs) == 1


async def test_shutdown_cancels_in_progress_start_and_rejects_new_starts(cold_browser, monkeypatch):
    runtime, _ = cold_browser
    live = runtime.browser.live
    agent = await runtime.roster.get("scout")
    entered = asyncio.Event()
    children = []
    original = live_module.LiveSession.command

    async def blocked_start(session, op, *args, **kwargs):
        if op == "ensure":
            children.append(session)
            entered.set()
            await asyncio.Event().wait()
        return await original(session, op, *args, **kwargs)

    monkeypatch.setattr(live_module.LiveSession, "command", blocked_start)
    starting = asyncio.create_task(live.ensure(agent))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        await asyncio.wait_for(live.close(), 5)
        assert starting.done()
        assert children[0].proc.returncode is not None
        assert all(task.done() for task in children[0].readers)
        with pytest.raises(RuntimeError, match="shut"):
            await live.ensure(agent)
    finally:
        starting.cancel()
        await asyncio.gather(starting, return_exceptions=True)


async def test_spawn_failure_releases_containment_and_next_call_can_start(
    cold_browser, monkeypatch
):
    runtime, _ = cold_browser
    live = runtime.browser.live
    agent = await runtime.roster.get("scout")
    closed = []
    tree_factory = live_module.make_process_tree

    def tree(name):
        owned = tree_factory(name)
        return SimpleNamespace(
            assign=owned.assign, close=lambda: (closed.append(True), owned.close())
        )

    async def fail_spawn(*args, **kwargs):
        raise OSError("Fixture process creation failed")

    with monkeypatch.context() as patch:
        patch.setattr(live_module, "make_process_tree", tree)
        patch.setattr(asyncio, "create_subprocess_exec", fail_spawn)
        with pytest.raises(OSError, match="creation failed"):
            await live.ensure(agent)
    assert closed
    assert not live.sessions
    assert not (await live.ensure(agent)).closed


@pytest.mark.parametrize("cancel_twice", [False, True])
async def test_cancellation_during_spawn_reaps_late_child(cold_browser, monkeypatch, cancel_twice):
    runtime, _ = cold_browser
    live = runtime.browser.live
    agent = await runtime.roster.get("scout")
    spawned, release = asyncio.Event(), asyncio.Event()
    children = []
    original = asyncio.create_subprocess_exec

    async def delayed_spawn(*args, **kwargs):
        child = await original(*args, **kwargs)
        children.append(child)
        spawned.set()
        await release.wait()
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    starting = asyncio.create_task(live.ensure(agent))
    try:
        await asyncio.wait_for(spawned.wait(), 3)
        starting.cancel()
        await asyncio.sleep(0)
        if cancel_twice:
            starting.cancel()
            await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(starting, 5)
        await asyncio.wait_for(asyncio.gather(*tuple(live._starts), return_exceptions=True), 5)
        assert children[0].returncode is not None
        assert not live.sessions
    finally:
        release.set()
        starting.cancel()
        await asyncio.gather(starting, return_exceptions=True)
        for child in children:
            if child.returncode is None:
                child.kill()
            await child.wait()


@pytest.mark.parametrize("mode", ["timeout", "invalid"])
async def test_bad_start_is_reaped_and_next_call_can_recover(
    cold_browser, tmp_path, monkeypatch, mode
):
    runtime, _ = cold_browser
    live = runtime.browser.live
    agent = await runtime.roster.get("scout")
    workspace = tmp_path / "society" / "scout" / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    marker = workspace / "browser-start-mode"
    marker.write_text(mode, encoding="utf-8")
    monkeypatch.setattr(live_module, "_START_TIMEOUT_S", 0.5)
    children = []
    original = live_module.LiveSession.command

    async def command(session, op, *args, **kwargs):
        if op == "ensure":
            children.append(session)
        return await original(session, op, *args, **kwargs)

    monkeypatch.setattr(live_module.LiveSession, "command", command)
    with pytest.raises(RuntimeError, match="timed out|invalid readiness"):
        await live.ensure(agent)
    assert not live.sessions and not live._starting_sessions
    assert children[0].proc.returncode is not None
    assert all(task.done() for task in children[0].readers)
    marker.unlink()
    monkeypatch.setattr(live_module, "_START_TIMEOUT_S", 5)
    assert not (await live.ensure(agent)).closed


async def test_failed_idle_cleanup_retains_handle_for_shutdown(cold_browser, monkeypatch):
    runtime, _ = cold_browser
    live = runtime.browser.live
    session = await live.ensure(await runtime.roster.get("scout"))
    original = session.close

    async def failed_close():
        raise OSError("Fixture close failure")

    monkeypatch.setattr(session, "close", failed_close)
    monkeypatch.setattr(live_module, "_IDLE_TIMEOUT_S", 0)
    live.release_when_idle(session)
    await live.idle_tasks["scout"]
    assert live.sessions["scout"] is session
    monkeypatch.setattr(session, "close", original)
    await live.close()
    assert session.proc.returncode is not None
    assert not live.sessions and not live._closing_tasks


async def test_manual_browser_is_not_closed_by_idle_timer(cold_browser, monkeypatch):
    runtime, _ = cold_browser
    live = runtime.browser.live
    session = await live.ensure(await runtime.roster.get("scout"))
    session.state["manual"] = True
    monkeypatch.setattr(live_module, "_IDLE_TIMEOUT_S", 0)
    live.release_when_idle(session)
    await live.idle_tasks["scout"]
    assert live.sessions["scout"] is session
    assert not session.closed


async def test_containment_assignment_failure_reaps_child(cold_browser, monkeypatch):
    runtime, _ = cold_browser
    live = runtime.browser.live
    agent = await runtime.roster.get("scout")
    children = []
    original = asyncio.create_subprocess_exec
    tree_factory = live_module.make_process_tree

    async def spawn(*args, **kwargs):
        child = await original(*args, **kwargs)
        children.append(child)
        return child

    def tree(name):
        owned = tree_factory(name)

        def fail_assign(pid):
            raise OSError("Fixture containment failure")

        return SimpleNamespace(assign=fail_assign, close=owned.close)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(live_module, "make_process_tree", tree)
    with pytest.raises(OSError, match="containment failure"):
        await live.ensure(agent)
    assert children[0].returncode is not None
    assert not live.sessions and not live._starting_sessions


async def test_dead_worker_is_replaced_before_reusing_stale_state(cold_browser):
    runtime, _ = cold_browser
    live = runtime.browser.live
    agent = await runtime.roster.get("scout")
    previous = await live.ensure(agent)
    previous.proc.kill()
    await previous.proc.wait()
    await asyncio.gather(*previous.readers)
    previous.closed = False  # The process watcher can win the race against the protocol reader.
    current = await live.ensure(agent)
    assert current is not previous
    assert previous.closed and not current.closed


async def test_request_during_idle_close_waits_for_old_process(cold_browser, monkeypatch):
    runtime, _ = cold_browser
    live = runtime.browser.live
    agent = await runtime.roster.get("scout")
    previous = await live.ensure(agent)
    entered, release = asyncio.Event(), asyncio.Event()
    original = previous.close

    async def closing():
        entered.set()
        await release.wait()
        await original()

    monkeypatch.setattr(previous, "close", closing)
    monkeypatch.setattr(live_module, "_IDLE_TIMEOUT_S", 0)
    live.release_when_idle(previous)
    await asyncio.wait_for(entered.wait(), 3)
    monkeypatch.setattr(live_module, "_IDLE_TIMEOUT_S", 300)
    starting = asyncio.create_task(live.ensure(agent))
    try:
        # Let both the cancelled idle owner and the new startup contend for the lock.
        await asyncio.sleep(0.02)
        assert not starting.done()
        release.set()
        current = await asyncio.wait_for(starting, 5)
        assert current is not previous
        assert previous.proc.returncode is not None
    finally:
        release.set()
        await asyncio.gather(starting, return_exceptions=True)
