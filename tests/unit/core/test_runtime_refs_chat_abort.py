"""The bar's X must abort an in-flight CHAT turn, not just a voice turn.

Live bug 2026-06-19: clicking the jarvis-bar X (``request_hangup``) while a
chat-originated brain turn was THINKING did nothing — the chat dispatcher
(``desktop_app._on_user_message``) ran a bare ``await generate(...)`` with no
task handle and never observed the voice pipeline's ``_hangup_event``. The
earlier fix only made the *voice* thinking phase abortable.

These tests cover the decoupled abort seam in ``runtime_refs``: the chat
dispatcher registers its running task here and the hangup chokepoint cancels it
edge-triggered (no stale-``Event`` problem), thread-safely via the owning loop.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.core import runtime_refs


@pytest.fixture(autouse=True)
def _clean_refs():
    runtime_refs._reset_for_tests()
    yield
    runtime_refs._reset_for_tests()


@pytest.mark.asyncio
async def test_cancel_active_chat_turn_cancels_registered_task():
    """A registered chat turn is cancelled when the X fires request_hangup."""
    loop = asyncio.get_running_loop()
    started = asyncio.Event()

    async def slow_turn():
        started.set()
        await asyncio.sleep(60)  # an in-flight brain turn the user wants to stop
        return "should never arrive"

    task = asyncio.create_task(slow_turn())
    await started.wait()
    runtime_refs.set_active_chat_turn(task, loop)

    assert runtime_refs.cancel_active_chat_turn() is True

    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


@pytest.mark.asyncio
async def test_cancel_is_noop_when_no_turn_active():
    """No active chat turn → the X is a harmless no-op (returns False)."""
    assert runtime_refs.cancel_active_chat_turn() is False


@pytest.mark.asyncio
async def test_clear_only_clears_the_matching_task():
    """A finished turn must not clear a newer turn's registration.

    Turns are serialized by the supervisor, but if an old turn's ``finally``
    runs late it must only retract its OWN task — otherwise it would disarm the
    X for the turn currently running.
    """
    loop = asyncio.get_running_loop()
    old = asyncio.create_task(asyncio.sleep(60))
    new = asyncio.create_task(asyncio.sleep(60))

    runtime_refs.set_active_chat_turn(new, loop)
    runtime_refs.clear_active_chat_turn(old)  # stale clear — must be ignored

    # ``new`` is still armed, so the X still cancels it.
    assert runtime_refs.cancel_active_chat_turn() is True

    for t in (old, new):
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t


@pytest.mark.asyncio
async def test_clear_disarms_so_a_later_x_is_a_noop():
    """After the turn finishes and clears itself, a stray X press does nothing."""
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(asyncio.sleep(60))
    runtime_refs.set_active_chat_turn(task, loop)
    runtime_refs.clear_active_chat_turn(task)

    assert runtime_refs.cancel_active_chat_turn() is False

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
