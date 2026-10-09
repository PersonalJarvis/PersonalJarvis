"""Warm picker lifecycle without a GUI, hooks, pixels or child processes."""

from __future__ import annotations

import asyncio
import queue
import subprocess
import threading

import pytest

from jarvis.appshot import picker as wire
from jarvis.appshot import picker_host, region


class Output:
    def __init__(self) -> None:
        self.lines: queue.Queue[str | None] = queue.Queue()
        self.closed = False

    def emit(self, payload: dict) -> None:
        self.lines.put(wire.encode(payload))

    def __iter__(self):
        while (line := self.lines.get()) is not None:
            yield line

    def close(self) -> None:
        self.closed = True


class Process:
    def __init__(self, *, finish: bool = True, standby: bool = True) -> None:
        self.stdout = Output()
        self.stdin = self
        self.returncode = None
        self.commands: list[dict] = []
        self.finish = finish
        self.closed = False
        self.started = threading.Event()
        if standby:
            self.stdout.emit({"event": wire.EVENT_STANDBY})

    def write(self, line: str) -> None:
        command = wire.decode(line)
        self.commands.append(command)
        if command["cmd"] == wire.CMD_START:
            self.started.set()
            self.stdout.emit({"event": wire.EVENT_READY})
            if self.finish:
                self.stdout.emit({"event": wire.EVENT_SELECTION, "cancelled": True})
        elif command["cmd"] == wire.CMD_QUIT:
            self.exit(0)

    def flush(self) -> None:
        pass  # In-memory pipe writes are immediate.

    def exit(self, code: int) -> None:
        self.returncode = code
        self.stdout.lines.put(None)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("picker", timeout)
        return self.returncode

    def kill(self) -> None:
        self.exit(-9)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def host(monkeypatch):
    children: list[Process] = []

    def spawn(*, resident):
        assert resident
        proc = Process()
        children.append(proc)
        return proc

    async def escape(_proc):
        await asyncio.Future()

    monkeypatch.setattr(region, "_spawn", spawn)
    monkeypatch.setattr(region, "_escape_cancels", escape)
    monkeypatch.setattr(region, "picker_capability", lambda: (True, ""))
    monkeypatch.setattr(region, "snap_layout", lambda: {"monitors": [], "windows": []})
    return picker_host.PickerHost(), children


async def test_standby_never_requests_pixels_and_repeated_picks_reuse_process(host):
    owner, children = host
    try:
        await owner.prewarm()
        assert children[0].commands == []
        for language in ("en", "de", "es"):
            payload, code, timed_out = await owner.pick(1, language)
            assert payload["cancelled"] and code is None and not timed_out
            assert children[0].commands[-1]["language"] == language
        assert len(children) == 1
    finally:
        await owner.close()
    assert children[0].closed and children[0].stdout.closed
    assert children[0].returncode == 0


async def test_each_pick_refreshes_window_layout(host, monkeypatch):
    owner, children = host
    layouts = iter(([1], [2]))
    monkeypatch.setattr(region, "snap_layout", lambda: {"windows": next(layouts)})
    try:
        await owner.pick(1, "en")
        await owner.pick(1, "en")
        assert [c["windows"] for c in children[0].commands] == [[1], [2]]
    finally:
        await owner.close()


async def test_dead_idle_process_is_reaped_and_replaced(host):
    owner, children = host
    try:
        await owner.prewarm()
        children[0].exit(7)
        await owner.pick(1, "en")
        assert len(children) == 2 and children[0].closed
    finally:
        await owner.close()


async def test_timeout_reaps_reader_before_next_request(host):
    owner, children = host
    try:
        await owner.prewarm()
        children[0].finish = False
        payload, _, timed_out = await owner.pick(0.02, "en")
        assert payload is None and timed_out and children[0].closed
        await owner.pick(1, "en")
        assert len(children) == 2
    finally:
        await owner.close()


async def test_shutdown_cancels_visible_selection_and_joins_reader(host):
    owner, children = host
    await owner.prewarm()
    children[0].finish = False
    task = asyncio.create_task(owner.pick(30, "en"))
    assert await asyncio.to_thread(children[0].started.wait, 1)
    await asyncio.wait_for(owner.close(), 1)
    assert task.cancelled() and children[0].closed


async def test_cancel_during_spawn_reaps_late_process(host, monkeypatch):
    owner, _ = host
    spawned = threading.Event()
    release = threading.Event()
    child = Process()

    def spawn(**_kwargs):
        spawned.set()
        assert release.wait(2)
        return child

    monkeypatch.setattr(region, "_spawn", spawn)
    warm = asyncio.create_task(owner.prewarm())
    assert await asyncio.to_thread(spawned.wait, 1)
    warm.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await warm
    assert child.returncode == 0 and child.closed


async def test_startup_timeout_closes_pipe_and_joins_reader(host, monkeypatch):
    owner, _ = host
    child = Process(standby=False)
    monkeypatch.setattr(region, "_spawn", lambda **_kwargs: child)
    monkeypatch.setattr(picker_host, "_START_TIMEOUT_S", 0.02)
    with pytest.raises(TimeoutError):
        await owner.prewarm()
    assert child.returncode == 0 and child.stdout.closed


async def test_no_display_never_spawns(host, monkeypatch):
    owner, children = host
    monkeypatch.setattr(region, "picker_capability", lambda: (False, "no display"))
    await owner.prewarm()
    assert children == []
