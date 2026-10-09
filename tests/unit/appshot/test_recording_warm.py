"""Warm recording lifecycle, shutdown, and startup ownership regression coverage."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from jarvis.appshot import recording


class FakeInput:
    def __init__(self, process):
        self.process = process
        self.closed = False

    def write(self, data):
        self.process.commands.append(data)
        if data == b"stop\n":
            self.process.finish("saved")
        else:
            assert json.loads(data)["cmd"] == "start"

    async def drain(self):
        pass

    def close(self):
        self.closed = True
        if self.process.returncode is None:
            self.process.finish("cancelled")


class FakeProcess:
    def __init__(self):
        self.stdout = asyncio.StreamReader()
        self.stdin = FakeInput(self)
        self.returncode = None
        self.exited = asyncio.Event()
        self.commands = []
        self.stdout.feed_data(b'{"phase":"ready"}\n')

    def finish(self, phase, message=""):
        self.stdout.feed_data((json.dumps({"phase": phase, "message": message}) + "\n").encode())
        self.stdout.feed_eof()
        self.returncode = 0
        self.exited.set()

    async def wait(self):
        await self.exited.wait()
        return self.returncode

    def kill(self):
        self.finish("error")
        self.returncode = -9


@pytest.fixture
def setup_service(monkeypatch, tmp_path):
    from jarvis.core import config

    monkeypatch.setattr(recording, "recording_dir", lambda: tmp_path)
    monkeypatch.setattr(recording, "capability", lambda: {"available": True})
    monkeypatch.setattr(
        config,
        "load_config",
        lambda: SimpleNamespace(
            screen_context=SimpleNamespace(enabled=True),
            ui=SimpleNamespace(language="en"),
        ),
    )
    processes = []

    async def spawn(*args, **kwargs):
        assert "jarvis.appshot.recording_worker" in args
        assert "creationflags" in kwargs
        process = FakeProcess()
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return recording.RecordingService(), processes


async def test_standby_takes_no_pixels_or_file_and_start_reuses_it(setup_service, tmp_path):
    service, processes = setup_service
    await service.set_warm(True)
    await service._warm_task
    assert service.status()["phase"] == "idle"
    assert len(processes) == 1 and not processes[0].commands
    assert not list(tmp_path.iterdir())
    first = processes[0]
    try:
        started = await service.start()
        assert service._process is first and len(processes) == 1
        command = json.loads(first.commands[0])
        assert command["output"].endswith(started["id"] + ".mp4")
        assert command["language"] == "en"
        await service.stop()
        await service._warm_task
        assert len(processes) == 2 and not processes[1].commands
        assert service.status()["phase"] == "saved"
    finally:
        await service.close()
    assert all(process.stdin.closed and process.returncode is not None for process in processes)


async def test_disabling_warmup_preserves_active_recording_and_prevents_replacement(setup_service):
    service, processes = setup_service
    await service.set_warm(True)
    await service._warm_task
    await service.start()
    await service.set_warm(False)
    assert processes[0].returncode is None
    assert len(processes[0].commands) == 1
    await service.stop()
    assert len(processes) == 1 and service._warm_task is None


async def test_dead_standby_is_replaced_on_next_press(setup_service):
    service, processes = setup_service
    await service.set_warm(True)
    await service._warm_task
    processes[0].finish("error")
    try:
        await service.start()
        assert service._process is processes[1]
    finally:
        await service.close()


async def test_standby_does_not_bypass_permission_checks(setup_service, monkeypatch):
    service, processes = setup_service
    await service.set_warm(True)
    await service._warm_task
    monkeypatch.setattr(
        recording, "capability", lambda: {"available": False, "detail": "Permission revoked"}
    )
    try:
        with pytest.raises(ValueError, match="Permission revoked"):
            await service.start()
        assert not processes[0].commands
    finally:
        await service.close()


async def test_headless_prewarm_does_not_spawn(setup_service, monkeypatch):
    service, processes = setup_service
    monkeypatch.setattr(recording, "capability", lambda: {"available": False})
    await service.set_warm(True)
    await service._warm_task
    await service.close()
    assert not processes


async def test_slow_warmup_and_start_share_one_child(setup_service, monkeypatch):
    service, processes = setup_service
    original = asyncio.create_subprocess_exec
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def spawn(*args, **kwargs):
        entered.set()
        await proceed.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    await service.set_warm(True)
    await entered.wait()
    start = asyncio.create_task(service.start())
    proceed.set()
    try:
        await start
        await service._warm_task
        assert len(processes) == 1
    finally:
        await service.close()


async def test_cancelled_warmup_reaps_a_late_spawn(setup_service, monkeypatch):
    service, processes = setup_service
    original = asyncio.create_subprocess_exec
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def spawn(*args, **kwargs):
        entered.set()
        await proceed.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    await service.set_warm(True)
    await entered.wait()
    close = asyncio.create_task(service.close())
    await asyncio.sleep(0)
    proceed.set()
    await close
    assert len(processes) == 1 and processes[0].stdin.closed
    assert processes[0].returncode is not None


async def test_standby_timeout_reaps_worker_and_allows_retry(setup_service, monkeypatch):
    from jarvis.appshot import recording_runtime

    service, processes = setup_service
    original = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        process = await original(*args, **kwargs)
        if len(processes) == 1:
            await process.stdout.readline()  # Simulate a worker that never announces readiness.
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(recording_runtime, "_READY_TIMEOUT_S", 0.02)
    await service.set_warm(True)
    await service._warm_task
    assert processes[0].stdin.closed
    try:
        await service.start()
        assert service._process is processes[1]
    finally:
        await service.close()


