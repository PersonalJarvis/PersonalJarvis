"""Wake confirmation stays independent of busy application workers and owns teardown."""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from jarvis.core.protocols import AudioChunk
from jarvis.plugins.wake import vosk_kws_provider as module


class FakeStreamingVerify:
    def __init__(self):
        self.closed = False

    def feed(self, audio):
        pass  # No native recognizer belongs to this scheduler-only fake.

    def close(self, *, wait=False):
        self.closed = True

    @property
    def ready_to_reap(self):
        return self.closed


def detector(monkeypatch, *, final=False, siblings=False):
    paths = ("fake-primary", "fake-sibling") if siblings else ("fake-primary",)
    monkeypatch.setattr(module, "_StreamingVerify", FakeStreamingVerify)
    provider = module.VoskKwsProvider("Hey Nova", model_path=paths[0], model_paths=paths)
    monkeypatch.setattr(provider, "_ensure_model", lambda path: None)
    monkeypatch.setattr(provider, "_fresh_recs", lambda: {path: object() for path in paths})
    monkeypatch.setattr(provider, "_models", {path: object() for path in paths})
    monkeypatch.setattr(provider, "_start_streaming_verify", lambda *args: FakeStreamingVerify())
    state = {"chunks": 0, "workers": []}

    def grammar(recs, pcm):
        state["chunks"] += 1
        return ((final, 1.0, paths[0]) if state["chunks"] == 1 else None), recs

    async def stage1(recs, pcm, run):
        return await run(grammar, recs, pcm)

    monkeypatch.setattr(provider, "_stage1", stage1)
    return provider, state


async def chunks(count=35):
    for index in range(count):
        await asyncio.sleep(0.005)
        yield AudioChunk(
            pcm=b"\x10\x00" * 512, sample_rate=16000,
            timestamp_ns=(index + 1) * 32_000_000,
        )


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():  # noqa: ASYNC110 - observe worker threads without the shared pool.
            await asyncio.sleep(0.002)


async def retired(provider):
    await until(lambda: provider._inference_reaper is not None
                and not provider._inference_reaper.is_alive())
    await asyncio.wait_for(asyncio.shield(provider._inference_cleanup), 2)
    assert not provider._detect_guard.locked()


@pytest.mark.parametrize("path", ["early", "stream_final", "final", "sibling"])
async def test_confirmation_uses_reserved_workers_when_default_executor_is_full(monkeypatch, path):
    provider, state = detector(monkeypatch, final=path in {"final", "sibling"},
                               siblings=path == "sibling")

    def verdict(*args):
        state["workers"].append(threading.current_thread().name)
        return True

    monkeypatch.setattr(provider, "_early_check",
                        verdict if path != "stream_final" else lambda *a: False)
    monkeypatch.setattr(provider, "_verify_candidate",
                        verdict if path != "sibling" else lambda *a: False)
    monkeypatch.setattr(provider, "_finish_streaming_verify", verdict)
    loop = asyncio.get_running_loop()
    previous = loop._default_executor
    default = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-busy-default")
    loop.set_default_executor(default)
    release, busy = threading.Event(), threading.Event()

    def unrelated_work():
        busy.set()
        release.wait(5)

    blocker = loop.run_in_executor(None, unrelated_work)
    stream = provider.detect(chunks())
    try:
        await until(busy.is_set)
        assert await asyncio.wait_for(anext(stream), 1) == "hey_nova"
        assert not release.is_set(), "Verification must finish while unrelated work remains blocked"
        assert state["workers"]
        assert all(name.startswith("vosk-wake-confirm") for name in state["workers"])
    finally:
        release.set()
        await stream.aclose()
        await retired(provider)
        await blocker
        loop._default_executor = previous
        default.shutdown(wait=True)


@pytest.mark.parametrize("termination", ["cancel", "stream_end"])
async def test_blocked_confirmation_preserves_stage1_and_retires_before_reentry(
    monkeypatch, termination,
):
    provider, state = detector(monkeypatch)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def blocked_verify(*args):
        entered.set()
        try:
            release.wait(5)
            return True
        finally:
            finished.set()

    monkeypatch.setattr(provider, "_early_check", blocked_verify)
    stream = provider.detect(chunks(5 if termination == "stream_end" else 100))
    pending = asyncio.create_task(anext(stream))
    try:
        await until(entered.is_set)
        await until(lambda: state["chunks"] >= 3)
        started = time.perf_counter()
        if termination == "cancel":
            pending.cancel()
        outcome = asyncio.CancelledError if termination == "cancel" else StopAsyncIteration
        with pytest.raises(outcome):
            await asyncio.wait_for(pending, 0.3)
        assert time.perf_counter() - started < 0.2
        assert not finished.is_set(), "Close must not wait for native inference on the wake path"
        assert provider._early_task is None
        assert provider._detect_guard.locked()
        newer = provider.detect(chunks())
        with pytest.raises(RuntimeError, match="retiring"):
            await anext(newer)
        await newer.aclose()
        release.set()
        await retired(provider)
        assert finished.is_set()
        newer = provider.detect(chunks(0))
        with pytest.raises(StopAsyncIteration):
            await anext(newer)
        await retired(provider)
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await stream.aclose()
        await retired(provider)


async def test_cancelled_stream_constructor_joins_its_late_decoder_pools(monkeypatch):
    provider, state = detector(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    joined = threading.Event()

    class LateStream(FakeStreamingVerify):
        def close(self, *, wait=False):
            super().close(wait=wait)
            if wait:
                joined.set()

    monkeypatch.setattr(module, "_StreamingVerify", LateStream)

    def build(*args):
        entered.set()
        release.wait(5)
        return LateStream()

    monkeypatch.setattr(provider, "_start_streaming_verify", build)
    stream = provider.detect(chunks())
    pending = asyncio.create_task(anext(stream))
    try:
        await until(entered.is_set)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 0.3)
        assert provider._detect_guard.locked()
        release.set()
        await retired(provider)
        assert joined.is_set()
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await stream.aclose()
        await retired(provider)


async def test_early_fire_retains_acquired_decoder_workers_until_they_join(monkeypatch):
    provider, state = detector(monkeypatch)
    release = threading.Event()
    entered = [threading.Event(), threading.Event()]
    owned = []

    class HeldStream(FakeStreamingVerify):
        def __init__(self):
            super().__init__()
            self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-owned-decode")

            def decode(index):
                entered[index].set()
                release.wait(5)

            self.jobs = [self.pool.submit(decode, index) for index in range(2)]
            assert all(event.wait(2) for event in entered)
            owned.append(self)

        def close(self, *, wait=False):
            super().close(wait=wait)
            self.pool.shutdown(wait=wait, cancel_futures=True)

        @property
        def ready_to_reap(self):
            return self.closed and all(job.done() for job in self.jobs)

    monkeypatch.setattr(module, "_StreamingVerify", HeldStream)
    monkeypatch.setattr(provider, "_start_streaming_verify", lambda *args: HeldStream())
    monkeypatch.setattr(provider, "_early_check", lambda *args: True)
    stream = provider.detect(chunks())
    try:
        assert await asyncio.wait_for(anext(stream), 1) == "hey_nova"
        started = time.perf_counter()
        await asyncio.wait_for(stream.aclose(), 0.3)
        assert time.perf_counter() - started < 0.2
        assert provider._detect_guard.locked()
        assert any(thread.is_alive() for thread in owned[0].pool._threads)
        newer = provider.detect(chunks())
        with pytest.raises(RuntimeError, match="retiring"):
            await anext(newer)
        await newer.aclose()
        release.set()
        await retired(provider)
        assert all(not thread.is_alive() for thread in owned[0].pool._threads)
    finally:
        release.set()
        await stream.aclose()
        await retired(provider)


async def test_cancelled_rescue_drops_queued_jobs_and_observes_late_exceptions(monkeypatch):
    provider, state = detector(monkeypatch, final=True, siblings=True)
    paths = ("fake-primary", "fake-one", "fake-two", "fake-queued")
    provider._model_paths = list(paths)
    provider._models = {path: object() for path in paths}
    entered = [threading.Event(), threading.Event()]
    release, queued_ran = threading.Event(), threading.Event()
    monkeypatch.setattr(provider, "_verify_candidate", lambda *args: False)

    def verify(window, path):
        if path == "fake-queued":
            queued_ran.set()
            return True
        entered[0 if path == "fake-one" else 1].set()
        release.wait(5)
        raise RuntimeError("Expected late fake verification failure")

    monkeypatch.setattr(provider, "_early_check", verify)
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unobserved = []
    loop.set_exception_handler(lambda owner, context: unobserved.append(context))
    stream = provider.detect(chunks())
    pending = asyncio.create_task(anext(stream))
    try:
        await until(lambda: all(event.is_set() for event in entered))
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 0.3)
        release.set()
        await retired(provider)
        assert not queued_ran.is_set()
        assert not unobserved
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await stream.aclose()
        await retired(provider)
        loop.set_exception_handler(previous_handler)
