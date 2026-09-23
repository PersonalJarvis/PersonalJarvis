"""Owned real subprocesses, bounded native cancellation and generation isolation."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from jarvis.realtime.local_runtime.events import NativeAudioError
from jarvis.realtime.local_runtime.process import NativeAudioProcess

WORKER = Path(__file__).parents[1] / "fakes" / "native_audio_worker.py"


def worker(root: Path, mode: str = "normal", **kwargs) -> NativeAudioProcess:
    return NativeAudioProcess(
        (sys.executable, "-u", str(WORKER), mode),
        root,
        expected_revision="test",
        start_timeout_s=5,
        event_timeout_s=2,
        cancel_timeout_s=0.5,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_worker_is_warmed_once_streams_and_releases_process_ownership(tmp_path: Path) -> None:
    runtime = worker(tmp_path)
    try:
        await runtime.start()
        first_pid = runtime.pid
        await runtime.start()
        assert runtime.pid == first_pid
        events = [event async for event in runtime.generate(text="hello")]
        assert [event.kind for event in events] == ["text", "audio", "done"]
        events = [event async for event in runtime.generate(text="followup", continue_context=True)]
        assert events[-1].kind == "done"
    finally:
        await runtime.close()
    assert not runtime.running
    assert runtime._reader is not None and not runtime._reader.is_alive()
    other = worker(tmp_path)
    try:
        await other.start()
        assert other.running
    finally:
        await other.close()


@pytest.mark.asyncio
async def test_another_owner_cannot_spawn_a_second_model(tmp_path: Path) -> None:
    first, second = worker(tmp_path), worker(tmp_path)
    try:
        await first.start()
        with pytest.raises(NativeAudioError, match="already owns"):
            await second.start()
        assert first.running
    finally:
        await second.close()
        await first.close()


@pytest.mark.asyncio
async def test_abandoning_a_turn_cancels_and_requires_new_context(tmp_path: Path) -> None:
    runtime = worker(tmp_path)
    try:
        await runtime.start()
        turn = runtime.generate(text="slow")
        assert (await anext(turn)).kind == "text"
        with pytest.raises(NativeAudioError, match="already handling"):
            _ = [event async for event in runtime.generate(text="competing")]
        await turn.aclose()
        assert runtime.running
        assert not runtime.busy
        with pytest.raises(NativeAudioError, match="reset"):
            _ = [event async for event in runtime.generate(text="next", continue_context=True)]
        assert [event.kind async for event in runtime.generate(text="new")] == [
            "text",
            "audio",
            "done",
        ]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_cancelling_an_await_does_not_lose_the_native_terminal_receipt(
    tmp_path: Path,
) -> None:
    runtime = worker(tmp_path)
    try:
        await runtime.start()
        turn = runtime.generate(text="slow")
        await anext(turn)
        waiting = asyncio.create_task(anext(turn))
        await asyncio.sleep(0.05)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert not runtime.busy
        assert runtime.running
        assert [event.kind async for event in runtime.generate(text="new")][-1] == "done"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_wedged_model_is_reaped_and_never_reused(tmp_path: Path) -> None:
    runtime = worker(tmp_path, "ignore_cancel")
    await runtime.start()
    turn = runtime.generate(text="wedged")
    await anext(turn)
    await turn.aclose()
    assert not runtime.running
    assert not runtime.busy
    with pytest.raises(NativeAudioError, match="not loaded"):
        _ = [event async for event in runtime.generate(text="new")]


@pytest.mark.asyncio
async def test_crashed_model_does_not_report_success(tmp_path: Path) -> None:
    runtime = worker(tmp_path)
    try:
        await runtime.start()
        with pytest.raises(NativeAudioError, match="exited"):
            _ = [event async for event in runtime.generate(text="crash")]
        assert not runtime.running
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_wrong_generation_never_reaches_the_caller(tmp_path: Path) -> None:
    runtime = worker(tmp_path)
    try:
        await runtime.start()
        with pytest.raises(NativeAudioError, match="mixed"):
            _ = [event async for event in runtime.generate(text="foreign")]
        assert not runtime.running
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_invalid_handshake_reaps_worker_and_releases_lease(tmp_path: Path) -> None:
    runtime = worker(tmp_path, "bad")
    with pytest.raises(NativeAudioError, match="incompatible"):
        await runtime.start()
    assert not runtime.running
    assert runtime._lease is None


@pytest.mark.asyncio
async def test_close_during_startup_never_leaves_a_late_child(tmp_path: Path) -> None:
    runtime = worker(tmp_path, "slow_start")
    starting = asyncio.create_task(runtime.start())
    await asyncio.sleep(0.05)
    await runtime.close()
    with pytest.raises(NativeAudioError):
        await starting
    assert not runtime.running
    assert runtime._lease is None


@pytest.mark.asyncio
async def test_cancel_crossing_completion_does_not_poison_the_next_turn(tmp_path: Path) -> None:
    runtime = worker(tmp_path)
    try:
        await runtime.start()
        turn = runtime.generate(text="fast")
        await anext(turn)
        # The engine finished before the caller stops consuming its audio.
        await asyncio.sleep(0.05)
        await turn.aclose()
        assert [event.kind async for event in runtime.generate(text="new")][-1] == "done"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_repeated_task_cancellation_still_reaps_a_wedged_worker(tmp_path: Path) -> None:
    runtime = worker(tmp_path, "ignore_cancel")
    try:
        await runtime.start()
        turn = runtime.generate(text="wedged")
        await anext(turn)
        waiting = asyncio.create_task(anext(turn))
        await asyncio.sleep(0.02)
        waiting.cancel()
        await asyncio.sleep(0.02)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        for _ in range(20):
            if not runtime.running and not runtime.busy:
                break
            await asyncio.sleep(0.1)
        assert not runtime.running
        assert not runtime.busy
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_closing_a_backpressured_worker_joins_reader_and_drops_queued_output(
    tmp_path: Path,
) -> None:
    runtime = worker(tmp_path)
    await runtime.start()
    turn = runtime.generate(text="flood")
    await anext(turn)
    await asyncio.sleep(0.1)
    try:
        await runtime.close()
        assert runtime._reader is not None and not runtime._reader.is_alive()
        with pytest.raises(NativeAudioError, match="closed"):
            await anext(turn)
    finally:
        await turn.aclose()
        await runtime.close()
