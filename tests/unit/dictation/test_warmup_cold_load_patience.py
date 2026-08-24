"""A dictation warm-up that is still BUILDING its local engine must be waited
for, not replaced.

Live forensic 2026-08-24 (local models, first dictation after a restart): the
join gave up after 20 s with "warm-up did not finish; replacing the provider
instance", built a second instance, and that one restarted the same cold
whisper-large-v3 load from zero. Replacing an instance never shortens a load —
it repeats it. The user waited through both and the dictation still came back
with zero characters.

The rule pinned here: patience is granted only on evidence. A provider that
reports ``is_loading`` gets the long ceiling while it says so; a provider that
does not report it (every cloud one) keeps the short join it always had; and a
build that ENDS without the warm-up finishing falls back to the short join,
because a hang in the priming inference is a real wedge (AP-24).
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.speech import pipeline as pipe
from jarvis.speech.pipeline import SpeechPipeline


class _LocalProvider:
    """A local engine that reports its build honestly."""

    def __init__(self) -> None:
        self.is_loading = True


class _CloudProvider:
    """No ``is_loading`` at all — the contract every cloud provider meets."""


async def _join(task: asyncio.Task, provider: object) -> None:
    # The method only reads module constants, so an unbound call is enough and
    # keeps the test off the SpeechPipeline constructor's whole dependency tree.
    await SpeechPipeline._await_warmup_or_cold_load(object(), task, provider)  # noqa: SLF001


@pytest.fixture(autouse=True)
def _fast_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    # Same logic, test-sized clocks: a 20 s / 180 s pair would make this a
    # three-minute test.
    monkeypatch.setattr(pipe, "_DICTATION_WARMUP_JOIN_TIMEOUT_S", 0.1)
    monkeypatch.setattr(pipe, "_DICTATION_WARMUP_COLD_LOAD_TIMEOUT_S", 5.0)


async def test_a_still_loading_engine_is_waited_for() -> None:
    provider = _LocalProvider()

    async def _warm() -> None:
        await asyncio.sleep(0.5)  # 5x the short join — a cold load, in miniature
        provider.is_loading = False

    task = asyncio.create_task(_warm())
    await _join(task, provider)  # must NOT raise: the engine was making progress

    assert task.done() and not task.cancelled(), (
        "the warm-up was abandoned while its engine was still building — the "
        "replacement would restart the same cold load from zero"
    )


async def test_a_cloud_provider_keeps_the_short_join() -> None:
    async def _warm() -> None:
        await asyncio.sleep(5.0)

    task = asyncio.create_task(_warm())
    try:
        with pytest.raises(TimeoutError):
            await _join(task, _CloudProvider())
    finally:
        task.cancel()


async def test_a_hang_after_the_build_is_still_a_wedge() -> None:
    # The engine finished building, but the warm-up never returns: that is the
    # priming inference hanging — the wedge the caller must replace.
    provider = _LocalProvider()

    async def _warm() -> None:
        await asyncio.sleep(0.2)
        provider.is_loading = False  # build done...
        await asyncio.sleep(30.0)  # ...priming hangs forever

    task = asyncio.create_task(_warm())
    try:
        with pytest.raises(TimeoutError):
            await _join(task, provider)
    finally:
        task.cancel()


async def test_an_endless_build_eventually_gives_up() -> None:
    # is_loading stuck True forever is still a wedge, just a patient one: the
    # cold-load ceiling has to end the wait rather than hanging the dictation.
    provider = _LocalProvider()

    async def _warm() -> None:
        await asyncio.sleep(30.0)

    task = asyncio.create_task(_warm())
    try:
        with pytest.raises(TimeoutError):
            await _join(task, provider)
    finally:
        task.cancel()
