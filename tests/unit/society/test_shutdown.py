"""Shutdown receipts must track actual owner completion through cancellation."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.society import shutdown
from tests.fakes.society_shutdown import PausedOwner


@pytest.fixture(autouse=True)
def bounded_quiesce_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutdown, "QUIESCE_TIMEOUT_S", 0.05)


async def test_stalled_admission_is_cancelled_and_joined_before_quiesce_returns() -> None:
    gate = shutdown.AdmissionGate("review hooks")
    hook = PausedOwner(hold_cleanup=True)
    owner = asyncio.create_task(hook.run_admitted(gate))
    tasks = [owner]
    try:
        await asyncio.wait_for(hook.entered.wait(), timeout=1)
        receipt = next(iter(gate._active))
        quiescing = asyncio.create_task(gate.quiesce())
        tasks.append(quiescing)
        await asyncio.wait_for(hook.cancelled.wait(), timeout=1)

        assert hook.admitted is True
        assert not quiescing.done()
        assert not owner.done()
        assert not hook.finished.is_set()
        assert not receipt.done()
        async with gate.admit() as admitted:
            assert admitted is False

        hook.cleanup_release.set()
        await asyncio.wait_for(quiescing, timeout=1)
        assert owner.cancelled()
        assert hook.finished.is_set()
        assert receipt.done() and not receipt.cancelled()
        assert not gate._active
    finally:
        await hook.cleanup(*tasks)


@pytest.mark.parametrize("wait_phase", ["drain", "join"])
async def test_cancelled_quiesce_preserves_receipt_and_retry_waits_for_real_owner(
    wait_phase: str,
) -> None:
    gate = shutdown.AdmissionGate("review hooks")
    hook = PausedOwner(resist_cancel=True)
    owner = asyncio.create_task(hook.run_admitted(gate))
    tasks = [owner]
    try:
        await asyncio.wait_for(hook.entered.wait(), timeout=1)
        receipt = next(iter(gate._active))
        quiescing = asyncio.create_task(gate.quiesce())
        tasks.append(quiescing)
        if wait_phase == "join":
            await asyncio.wait_for(hook.cancelled.wait(), timeout=1)
        else:
            # Run the waiter into its first receipt wait without advancing a timer.
            await asyncio.sleep(0)
        assert gate.accepting is False

        quiescing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await quiescing
        assert not receipt.done()
        assert gate._active[receipt] is owner
        assert not owner.done()
        assert not hook.finished.is_set()
        assert hook.cancel_requests == (1 if wait_phase == "join" else 0)

        retry = asyncio.create_task(gate.quiesce())
        tasks.append(retry)
        next_cancellation = hook.cancelled_again if wait_phase == "join" else hook.cancelled
        await asyncio.wait_for(next_cancellation.wait(), timeout=1)
        assert not retry.done()
        assert not receipt.done()
        assert not hook.finished.is_set()

        hook.release.set()
        await asyncio.wait_for(retry, timeout=1)
        await asyncio.wait_for(owner, timeout=1)
        assert hook.finished.is_set()
        assert receipt.done() and not receipt.cancelled()
        assert not gate._active
    finally:
        await hook.cleanup(*tasks)


async def test_cancellation_resistant_admission_keeps_live_receipt_after_quiesce_failure() -> None:
    gate = shutdown.AdmissionGate("review hooks")
    hook = PausedOwner(resist_cancel=True)
    owner = asyncio.create_task(hook.run_admitted(gate))
    try:
        await asyncio.wait_for(hook.entered.wait(), timeout=1)
        receipt = next(iter(gate._active))

        with pytest.raises(
            RuntimeError, match="review hooks did not stop; its storage must remain open"
        ):
            await asyncio.wait_for(gate.quiesce(), timeout=1)

        assert hook.cancel_requests == 1
        assert not hook.finished.is_set()
        assert not owner.done()
        assert not receipt.done()
        assert gate._active[receipt] is owner
        assert gate.accepting is False

        hook.release.set()
        await asyncio.wait_for(owner, timeout=1)
        await asyncio.wait_for(gate.quiesce(), timeout=1)
        assert hook.finished.is_set()
        assert receipt.done() and not receipt.cancelled()
        assert not gate._active
    finally:
        await hook.cleanup(owner)


@pytest.mark.parametrize("label", ["Society producer", "Society startup"])
async def test_cancel_and_join_refuses_success_while_owner_ignores_cancel(label: str) -> None:
    producer = PausedOwner(resist_cancel=True)
    owner = asyncio.create_task(producer.run())
    try:
        await asyncio.wait_for(producer.entered.wait(), timeout=1)
        with pytest.raises(
            RuntimeError, match=f"{label} did not stop; its storage must remain open"
        ):
            await asyncio.wait_for(shutdown.cancel_and_join([owner], label), timeout=1)

        assert producer.cancel_requests == 1
        assert not owner.done()
        assert not producer.finished.is_set()

        producer.release.set()
        await asyncio.wait_for(owner, timeout=1)
        await shutdown.cancel_and_join([owner], label)
        assert producer.finished.is_set()
    finally:
        await producer.cleanup(owner)
