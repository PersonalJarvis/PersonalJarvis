"""Cancellation must release real owned children without a browser installation."""

from __future__ import annotations

import asyncio
import sys

import pytest

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.society.browser.live import LiveSession, LiveUpdates
from jarvis.society.runtime import SocietyRuntime, current_runtime
from tests.fakes.fake_society_shutdown import RecordingProcessTree, society_shutdown_server


async def owned_child(*, respond=True):
    script = """
import json
import sys
import time
print('READY', flush=True)
for line in sys.stdin:
    message = json.loads(line)
    print(json.dumps({'kind': 'state', 'phase': 'shutdown-request'}), flush=True)
    if sys.argv[1] == 'respond':
        print(json.dumps({'kind': 'response', 'id': message['id'], 'ok': True}), flush=True)
    time.sleep(60)
"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        "-c",
        script,
        "respond" if respond else "stall",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    try:
        assert (await asyncio.wait_for(proc.stdout.readline(), timeout=3)).strip() == b"READY"
    except BaseException:
        await reap(proc)
        raise
    return proc


async def live_session(*, respond=True):
    proc = await owned_child(respond=respond)
    tree = RecordingProcessTree()
    session = LiveSession("fixture", proc, tree)
    updates = LiveUpdates()
    session.subscribers.add(updates)
    session.readers = [
        asyncio.create_task(session.read()),
        asyncio.create_task(session.drain_stderr()),
    ]
    return session, updates


async def reap(proc):
    if proc.returncode is None:
        proc.kill()
    await asyncio.wait_for(proc.wait(), timeout=3)


async def test_cancel_during_shutdown_command_reaps_owned_process():
    session, updates = await live_session(respond=False)
    closing = asyncio.create_task(session.close())
    try:
        assert (await asyncio.wait_for(updates.get(), timeout=2))["phase"] == "shutdown-request"
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closing, timeout=3)
        assert session.proc.returncode is not None
        assert session.tree.closes >= 1
        assert session.closed
        assert all(task.done() for task in session.readers)
    finally:
        await reap(session.proc)
        await asyncio.gather(closing, *session.readers, return_exceptions=True)


async def test_server_deadline_reaps_live_and_one_shot_browser_children(tmp_path):
    runtime = SocietyRuntime(tmp_path)
    await runtime.ensure_started()
    server, cleaned = society_shutdown_server(runtime)
    session = None
    job = None
    try:
        session, _updates = await live_session()
        runtime.browser.live.sessions[session.agent_id] = session
        job = await owned_child()
        runtime.browser._procs["one-shot"] = job
        # The child acknowledges shutdown but never exits voluntarily. Exercise
        # the production five-second server deadline, not a replaced close call.
        with pytest.raises(RuntimeError, match="society_runtime_shutdown_incomplete"):
            await asyncio.wait_for(server.stop(), timeout=10)
        assert session.proc.returncode is not None and job.returncode is not None
        assert session.tree.closes >= 1
        assert session.closed and all(task.done() for task in session.readers)
        assert not runtime.browser.live.sessions
        assert not runtime.browser._procs
        assert runtime.store._conn is None
        assert current_runtime() is None
        assert {"chat", "pty", "mission-approvals"} <= set(cleaned)
    finally:
        if session is not None:
            await reap(session.proc)
        if job is not None:
            await reap(job)
        await runtime.close()


async def test_lingering_rpc_keeps_session_handle_for_cleanup_retry(tmp_path, monkeypatch):
    from jarvis.society.browser import live as live_module

    session, _updates = await live_session()
    await reap(session.proc)
    await asyncio.gather(*session.readers, return_exceptions=True)
    live = live_module.LiveSessions(tmp_path)
    live.sessions[session.agent_id] = session
    entered = asyncio.Event()
    release = asyncio.Event()

    async def reluctant_rpc():
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue  # Deliberately resist cleanup until this fixture releases it.

    task = asyncio.create_task(reluctant_rpc())
    session.tasks.add(task)
    await entered.wait()
    monkeypatch.setattr(live_module, "_FORCED_CLOSE_TIMEOUT_S", 0.02)
    try:
        with pytest.raises(TimeoutError, match="browser process cleanup incomplete"):
            await live.close()
        assert not task.done()
        assert live.sessions[session.agent_id] is session
        release.set()
        await task
        await live.close()
        assert not live.sessions and not live._closing_tasks
    finally:
        release.set()
        await task
        await live.close()
