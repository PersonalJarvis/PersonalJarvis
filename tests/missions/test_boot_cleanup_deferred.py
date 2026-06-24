"""Boot-time cleanup sweeps must NOT sit on the desktop boot path.

2026-06-24 boot-speed work: the worktree prune/sweep (``prune_and_sweep_leaked``)
and the screenshot-blob retention sweep are pure cleanup whose result nothing
downstream consumes. Awaiting them delayed the desktop boot (mission_stack mark
1164 ms -> 33 ms once deferred). ``_spawn_boot_cleanup`` runs such a sweep
fire-and-forget with a tracked strong reference (so the event loop cannot GC it
mid-run) that self-discards on completion.

Regression guard: a future edit must keep these sweeps off the boot path.
"""
from __future__ import annotations

import asyncio

from jarvis.missions.init import _BOOT_BACKGROUND_TASKS, _spawn_boot_cleanup


async def test_spawn_runs_concurrently_tracked_then_self_discards() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def _work() -> None:
        started.set()
        await release.wait()

    task = _spawn_boot_cleanup(_work(), name="test-sweep")
    await started.wait()

    # The caller did NOT await it — it runs in the background...
    assert not task.done()
    # ...and is strongly referenced so the loop cannot GC it mid-run.
    assert task in _BOOT_BACKGROUND_TASKS
    assert task.get_name() == "test-sweep"

    release.set()
    await task

    # Reference self-discards once finished — no leak.
    assert task not in _BOOT_BACKGROUND_TASKS


async def test_spawn_failure_still_discards_reference() -> None:
    async def _boom() -> None:
        raise RuntimeError("sweep blew up")

    task = _spawn_boot_cleanup(_boom(), name="boom-sweep")
    # Awaiting surfaces the error here, but the done-callback still cleans up.
    with_error = False
    try:
        await task
    except RuntimeError:
        with_error = True

    assert with_error
    assert task not in _BOOT_BACKGROUND_TASKS
