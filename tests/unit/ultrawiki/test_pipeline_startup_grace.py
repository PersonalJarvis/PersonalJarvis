"""The pipeline worker's startup grace period — start fast, shut down fast.

Why this exists (2026-07-26): the ingest worker was started the instant the
backend came up. With a large backlog it holds ~1.3 CPU cores continuously, so
it competed with the app's own startup and made every launch feel sluggish
(AP-26). The store still opens immediately — reads, search and recall need it —
but the worker now waits out a grace period first.

The trap this pins shut: implementing the grace as a plain ``asyncio.sleep``
would turn a startup win into a shutdown stall, because ``stop()`` would then
block for the remainder of the window. The wait must therefore be a cancellable
wait ON the shutdown event, which is what the third test proves.

These exercise the scheduling helper directly with a recording double, so they
need no store, no config and no credentials.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from jarvis.ultrawiki.service import UltraWikiService


class RecordingPipeline:
    """Stands in for PipelineWorker, recording whether/when run() was entered."""

    def __init__(self) -> None:
        self.started = False
        self.started_at: float | None = None

    async def run(self, cancel_event: asyncio.Event) -> None:
        self.started = True
        self.started_at = time.perf_counter()
        await cancel_event.wait()


async def test_without_a_grace_period_the_worker_starts_straight_away() -> None:
    """An in-app enable passes no grace: the user is waiting for the work."""
    pipeline = RecordingPipeline()
    cancel = asyncio.Event()

    task = asyncio.create_task(
        UltraWikiService._run_pipeline_after_grace(pipeline, cancel, 0.0)
    )
    # One scheduler turn is all an ungraced start may need.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert pipeline.started, "the worker should run immediately without a grace"

    cancel.set()
    await asyncio.wait_for(task, timeout=1.0)


async def test_a_grace_period_holds_the_worker_back_then_runs_it() -> None:
    """The backlog is deferred, never skipped: it starts once the window ends."""
    pipeline = RecordingPipeline()
    cancel = asyncio.Event()
    grace = 0.15

    task = asyncio.create_task(
        UltraWikiService._run_pipeline_after_grace(pipeline, cancel, grace)
    )
    await asyncio.sleep(grace / 3)
    assert not pipeline.started, "the worker must not run during the grace window"

    # Comfortably past the window, but still far below any test timeout.
    await asyncio.sleep(grace)
    assert pipeline.started, "the worker must start once the grace window elapses"

    cancel.set()
    await asyncio.wait_for(task, timeout=1.0)


async def test_a_shutdown_inside_the_grace_window_returns_at_once() -> None:
    """The grace must not delay shutdown — the regression this guards.

    A blind sleep would keep stop() blocked for the rest of the window. With a
    cancellable wait the helper returns immediately and never starts the worker.
    """
    pipeline = RecordingPipeline()
    cancel = asyncio.Event()
    # Long enough that waiting it out would blow the assertion below by 20x.
    grace = 30.0

    started_at = time.perf_counter()
    task = asyncio.create_task(
        UltraWikiService._run_pipeline_after_grace(pipeline, cancel, grace)
    )
    await asyncio.sleep(0)
    cancel.set()

    await asyncio.wait_for(task, timeout=1.0)
    elapsed = time.perf_counter() - started_at

    assert not pipeline.started, "a worker cancelled mid-grace must never run"
    assert elapsed < 1.0, (
        f"shutdown waited {elapsed:.2f}s — the grace period is blocking stop(), "
        "which means it is a sleep rather than a wait on the cancel event"
    )


@pytest.mark.parametrize("grace", [0.0, 0.05])
async def test_the_helper_always_hands_control_to_the_worker_it_was_given(
    grace: float,
) -> None:
    """Whatever the grace, the worker object passed in is the one that runs."""
    pipeline = RecordingPipeline()
    cancel = asyncio.Event()

    task = asyncio.create_task(
        UltraWikiService._run_pipeline_after_grace(pipeline, cancel, grace)
    )
    await asyncio.sleep(grace + 0.05)

    assert pipeline.started
    cancel.set()
    await asyncio.wait_for(task, timeout=1.0)
