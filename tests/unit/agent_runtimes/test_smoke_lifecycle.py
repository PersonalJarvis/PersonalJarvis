"""A failed or cancelled live probe must release its turn and reap its process."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

from jarvis.agent_runtimes.base import RuntimeLaunch, RuntimeTurn
from jarvis.agent_runtimes.model_map import ModelRoute
from scripts.spikes import agent_runtimes_e2e as smoke


def setup_probe(monkeypatch, tmp_path, *, missing=False):
    released = []
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    route = ModelRoute("ollama", "fake", "http://localhost", "chat_completions", None)
    turn = RuntimeTurn("test", "Test", "society:test", workspace, route, None, False)
    fake = Path(__file__).resolve().parents[2] / "fakes" / "fake_acp_agent.py"

    class Runtime:
        async def launch(self, _turn):
            return RuntimeLaunch(
                argv=[str(tmp_path / "nonexistent")] if missing else [sys.executable, str(fake)],
                env={**os.environ, "FAKE_ACP_STORE": str(tmp_path / "store.json")},
                cwd=workspace,
                release=lambda: released.append(True),
            )

    monkeypatch.setattr(smoke, "driver", lambda _name: Runtime())
    return turn, released


async def test_spawn_failure_releases_slot(monkeypatch, tmp_path):
    turn, released = setup_probe(monkeypatch, tmp_path, missing=True)
    with pytest.raises(OSError):
        await smoke._run("fake", turn, "hello")
    assert released == [True]


async def test_cancellation_reaps_the_child_and_releases_slot(monkeypatch, tmp_path):
    turn, released = setup_probe(monkeypatch, tmp_path)
    spawned = asyncio.Event()
    processes = []
    create = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        proc = await create(*args, **kwargs)
        processes.append(proc)
        spawned.set()
        return proc

    monkeypatch.setattr(smoke.asyncio, "create_subprocess_exec", capture)
    task = asyncio.create_task(smoke._run("fake", turn, "HANG"))
    try:
        await asyncio.wait_for(spawned.wait(), 5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert released == [True]
    assert processes[0].returncode is not None


async def test_success_releases_slot(monkeypatch, tmp_path):
    turn, released = setup_probe(monkeypatch, tmp_path)
    result, _io = await smoke._run("fake", turn, "hello")
    assert result.status == "done"
    assert result.result_text == "echo: hello"
    assert released == [True]
