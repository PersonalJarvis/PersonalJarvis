from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

import pytest

from jarvis.vision.uia_tree import UIATreeSource


async def test_busy_traversal_gate_falls_back_without_submitting_another_worker() -> None:
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def traverser(depth, title_filter):
        nonlocal calls
        calls += 1
        started.set()
        release.wait(timeout=2.0)
        return ("Window", 1, [])

    source = UIATreeSource(
        traverser=traverser,
        monitor_bounds=(0, 0, 1920, 1080),
    )
    first = asyncio.create_task(source.observe())
    assert await asyncio.to_thread(started.wait, 1.0)

    second = await asyncio.wait_for(source.observe(), timeout=0.2)
    assert second.source == "screenshot_only"
    assert calls == 1

    release.set()
    assert (await first).source == "ui_tree_only"


async def test_cancelled_observe_keeps_gate_closed_until_worker_finishes() -> None:
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def traverser(depth, title_filter):
        nonlocal calls
        calls += 1
        started.set()
        release.wait(timeout=2.0)
        return ("Window", 1, [])

    source = UIATreeSource(
        traverser=traverser,
        monitor_bounds=(0, 0, 1920, 1080),
    )
    first = asyncio.create_task(source.observe())
    assert await asyncio.to_thread(started.wait, 1.0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    second = await asyncio.wait_for(source.observe(), timeout=0.2)
    assert second.source == "screenshot_only"
    assert calls == 1
    release.set()


async def test_queued_traversal_releases_gate_after_caller_is_cancelled(
    monkeypatch,
) -> None:
    source = UIATreeSource(
        traverser=lambda depth, title_filter: ("Window", 1, []),
        monitor_bounds=(0, 0, 1920, 1080),
    )
    loop = asyncio.get_running_loop()
    scheduled: list[
        tuple[asyncio.Future, Callable[..., object], tuple[object, ...]]
    ] = []

    def queue_only(_executor, function, *args):
        future = loop.create_future()
        scheduled.append((future, function, args))
        return future

    monkeypatch.setattr(loop, "run_in_executor", queue_only)
    first = asyncio.create_task(source.observe())
    await asyncio.sleep(0)
    assert len(scheduled) == 1
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    busy = await source.observe()
    assert busy.source == "screenshot_only"
    assert len(scheduled) == 1

    queued, function, args = scheduled[0]
    queued.set_result(function(*args))
    await asyncio.sleep(0)

    third = asyncio.create_task(source.observe())
    await asyncio.sleep(0)
    assert len(scheduled) == 2
    queued, function, args = scheduled[1]
    queued.set_result(function(*args))
    assert (await third).source == "ui_tree_only"
