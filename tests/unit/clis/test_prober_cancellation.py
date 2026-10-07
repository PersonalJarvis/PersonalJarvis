"""Cancelling registry discovery also owns its queued probes and processes."""

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.clis import prober as module
from jarvis.clis.prober import CliStatusProber


@pytest.mark.asyncio
async def test_cancelled_sweep_joins_active_and_queued_probes(monkeypatch):
    monkeypatch.setattr(module, "PROBE_CONCURRENCY", 1)
    entered = asyncio.Event()
    settled = asyncio.Event()
    started = []

    class Prober(CliStatusProber):
        async def probe(self, spec):
            started.append(spec.name)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                settled.set()

    before = asyncio.all_tasks()
    task = asyncio.create_task(Prober().probe_all([
        SimpleNamespace(name="active"), SimpleNamespace(name="queued"),
    ]))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert settled.is_set()
        assert started == ["active"]
        assert not (asyncio.all_tasks() - before)
    finally:
        leaked = asyncio.all_tasks() - before
        for child in leaked:
            child.cancel()
        await asyncio.gather(*leaked, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["_probe_binary", "_probe_auth"])
async def test_cancelled_probe_kills_and_reaps_its_child(monkeypatch, method):
    entered = asyncio.Event()
    events = []

    class Process:
        returncode = None

        async def communicate(self):
            entered.set()
            await asyncio.Event().wait()

        def kill(self):
            events.append("kill")
            self.returncode = -1

        async def wait(self):
            await asyncio.sleep(0)
            events.append("reaped")
            return self.returncode

    async def spawn(*_args, **_kwargs):
        return Process()

    monkeypatch.setattr(module.shutil, "which", lambda _: "fake-cli")
    monkeypatch.setattr(module, "resolve_executable", lambda _: "fake-cli")
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    spec = SimpleNamespace(
        name="fake", binary_name="fake", check_command=("fake", "--version"),
        auth=SimpleNamespace(type="oauth_cli", status_command=("fake", "status")),
    )
    task = asyncio.create_task(getattr(CliStatusProber(), method)(spec))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert events == ["kill", "reaped"]


@pytest.mark.asyncio
async def test_cancelling_sweep_during_timeout_cleanup_still_joins_child(monkeypatch):
    monkeypatch.setattr(module, "PROBE_ALL_TIMEOUT_S", 0.001)
    monkeypatch.setattr(module, "KILL_WAIT_TIMEOUT_S", 0.5)
    cleaning = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    class Prober(CliStatusProber):
        async def probe(self, _spec):
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                finished.set()

    before = asyncio.all_tasks()
    task = asyncio.create_task(Prober().probe_all([SimpleNamespace(name="slow")]))
    try:
        await asyncio.wait_for(cleaning.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert finished.is_set()
        assert not (asyncio.all_tasks() - before)
    finally:
        release.set()
        children = asyncio.all_tasks() - before
        for child in children:
            child.cancel()
        await asyncio.gather(*children, return_exceptions=True)
