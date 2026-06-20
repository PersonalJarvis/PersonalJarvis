"""The chat dispatcher must run its brain turn as a cancellable, X-armed task.

Extracted from ``desktop_app._on_user_message`` so the abort behaviour is unit
-testable. ``_await_cancellable_chat_turn`` distinguishes two kinds of
cancellation precisely (code-review 2026-06-19):

* the bar's X cancels the INNER brain task → the helper returns the
  ``_CHAT_TURN_ABORTED`` sentinel so the dispatcher drops to IDLE; and
* an OUTER cancellation (app shutdown / bus-gather teardown) is propagated
  unchanged (and the inner task is cancelled too, so it can't leak) — swallowing
  it would break Python's cooperative-cancellation contract.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.core import runtime_refs
from jarvis.ui.desktop_app import _CHAT_TURN_ABORTED, _await_cancellable_chat_turn


@pytest.fixture(autouse=True)
def _clean_refs():
    runtime_refs._reset_for_tests()
    yield
    runtime_refs._reset_for_tests()


@pytest.mark.asyncio
async def test_returns_reply_normally_and_disarms_the_x():
    async def generate():
        return "the answer"

    result = await _await_cancellable_chat_turn(
        generate(), asyncio.get_running_loop()
    )

    assert result == "the answer"
    # Turn done → the X is disarmed (a later press is a no-op).
    assert runtime_refs.cancel_active_chat_turn() is False


@pytest.mark.asyncio
async def test_x_press_returns_the_aborted_sentinel():
    started = asyncio.Event()

    async def generate():
        started.set()
        await asyncio.sleep(60)  # the brain is THINKING
        return "should never arrive"

    loop = asyncio.get_running_loop()
    turn = asyncio.create_task(_await_cancellable_chat_turn(generate(), loop))
    await started.wait()

    # The bar's X → request_hangup → cancel_active_chat_turn (inner task only).
    assert runtime_refs.cancel_active_chat_turn() is True

    # The X path is absorbed: a sentinel, NOT a raised CancelledError.
    assert (await turn) is _CHAT_TURN_ABORTED
    # And it disarmed itself, so a stray second press does nothing.
    assert runtime_refs.cancel_active_chat_turn() is False


@pytest.mark.asyncio
async def test_outer_cancellation_propagates_and_cancels_the_inner_turn():
    """App shutdown cancels the dispatcher coroutine itself — that must NOT be
    swallowed (it would break clean teardown), and the inner brain task must be
    cancelled too so it can't leak."""
    started = asyncio.Event()
    inner_cleaned = asyncio.Event()

    async def generate():
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            inner_cleaned.set()
            raise
        return "should never arrive"

    loop = asyncio.get_running_loop()
    outer = asyncio.create_task(_await_cancellable_chat_turn(generate(), loop))
    await started.wait()

    # Simulate shutdown: cancel the OUTER coroutine, NOT via the registry.
    outer.cancel()

    with pytest.raises(asyncio.CancelledError):
        await outer

    await asyncio.wait_for(inner_cleaned.wait(), timeout=1.0)  # inner not leaked
    assert runtime_refs.cancel_active_chat_turn() is False  # disarmed
