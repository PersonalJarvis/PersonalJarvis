"""Browser recovery uses owned cleanup even when command handling is stuck."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.society.browser import recovery as recovery_module
from jarvis.society.browser.live import LiveSession, LiveSessions
from jarvis.society.browser.recovery import restart_browser
from tests.fakes.fake_browser_profiles import ProfileSession
from tests.fakes.fake_society_shutdown import RecordingProcessTree


@pytest.fixture
async def recovery(tmp_path, monkeypatch):
    live = LiveSessions(tmp_path)
    starts = []

    async def ensure(agent, binding, **kwargs):
        starts.append(kwargs)
        session = ProfileSession(agent.agent_id)
        live.sessions[agent.agent_id] = session
        return session

    monkeypatch.setattr(live, "_ensure_managed", ensure)
    agent = SimpleNamespace(agent_id="lead", browser_mode="own", browser_allowed_domains=[])
    session = await live.ensure(agent, window_view=True)
    yield live, agent, session, starts
    await live.close()


async def test_restart_preserves_profile_and_bypasses_stuck_control(recovery):
    live, agent, old, starts = recovery
    old.active_trace = "stopped-task"
    old.state.update(manual=True, login_mode=True)
    old.control_owner = "viewer"
    profile = old.profile_binding.path
    profile.mkdir(parents=True, exist_ok=True)
    marker = profile / "saved-session-fixture"
    marker.write_text("keep", encoding="utf-8")
    other = ProfileSession("other")
    live.sessions["other"] = other
    await old.control_lock.acquire()
    try:
        await asyncio.wait_for(restart_browser(live, agent), 2)
    finally:
        old.control_lock.release()
    assert old.closed
    assert not other.closed
    assert (agent.agent_id, "stopped-task") in live.stopped_turns
    assert live.sessions[agent.agent_id] is not old
    assert live.sessions[agent.agent_id].profile_binding.path == profile
    assert marker.read_text(encoding="utf-8") == "keep"
    assert starts[-1] == {"window_view": True}
    assert old.commands == [("event", {"kind": "disconnected"})]


@pytest.mark.parametrize("kind", ["chrome", "attach"])
async def test_restart_never_closes_an_external_browser(recovery, kind):
    live, agent, old, starts = recovery
    old.profile_binding = SimpleNamespace(kind=kind)
    with pytest.raises(ValueError, match="managed by Jarvis"):
        await restart_browser(live, agent)
    assert not old.closed and len(starts) == 1


async def test_concurrent_restart_requests_close_old_session_once(recovery, monkeypatch):
    live, agent, old, _starts = recovery
    entered, release = asyncio.Event(), asyncio.Event()
    original_close = old.close
    closes = []

    async def close():
        closes.append(True)
        entered.set()
        await release.wait()
        await original_close()

    monkeypatch.setattr(old, "close", close)
    first = asyncio.create_task(restart_browser(live, agent))
    await entered.wait()
    second = asyncio.create_task(restart_browser(live, agent))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)
    assert closes == [True]
    assert live.sessions[agent.agent_id] is not old


async def test_cancelled_request_waits_for_cleanup_without_reopening(recovery, monkeypatch):
    live, agent, old, starts = recovery
    entered, release = asyncio.Event(), asyncio.Event()
    original_close = old.close

    async def close():
        entered.set()
        await release.wait()
        await original_close()

    monkeypatch.setattr(old, "close", close)
    task = asyncio.create_task(restart_browser(live, agent))
    await entered.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert live.profile_start_lock.locked()
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert old.closed and agent.agent_id not in live.sessions
    assert len(starts) == 1


async def test_failed_cleanup_keeps_handle_and_does_not_spawn(recovery, monkeypatch):
    live, agent, old, starts = recovery
    async def fail():
        raise RuntimeError("cleanup unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(old, "close", fail)
        with pytest.raises(RuntimeError, match="cleanup unavailable"):
            await restart_browser(live, agent)
    assert live.sessions[agent.agent_id] is old
    assert len(starts) == 1


async def test_restart_refuses_after_shutdown_begins(recovery):
    live, agent, old, starts = recovery
    live._closed = True
    with pytest.raises(RuntimeError, match="shutting down"):
        await restart_browser(live, agent)
    assert not old.closed and len(starts) == 1


async def test_unresponsive_real_worker_is_reaped_before_profile_reopens(recovery, monkeypatch):
    live, agent, old, starts = recovery
    worker = Path(__file__).parents[2] / "fakes" / "stalled_browser_recovery_worker.py"
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-u", str(worker),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    session = LiveSession(agent.agent_id, proc, RecordingProcessTree())
    try:
        assert (await asyncio.wait_for(proc.stdout.readline(), 3)).strip() == b"READY"
        session.profile_binding, session.profile_lease = old.profile_binding, old.profile_lease
        old.profile_lease = None
        live.sessions[agent.agent_id] = session
        session.readers = [asyncio.create_task(session.read()),
                           asyncio.create_task(session.drain_stderr())]
        monkeypatch.setattr(recovery_module, "_RECOVERY_CLOSE_TIMEOUT_S", 0.05)
        await asyncio.wait_for(restart_browser(live, agent), 4)
        assert proc.returncode is not None
        assert all(task.done() for task in session.readers)
        assert session.profile_lease is None
        assert live.sessions[agent.agent_id] is not session
        assert len(starts) == 2
    finally:
        if proc.returncode is None:
            proc.kill()
        await asyncio.wait_for(proc.wait(), 3)
