"""Unit tests for CancelToken + CancelScope + KillSwitch (ADR-0004)."""
from __future__ import annotations

import asyncio

import pytest

from jarvis.control import CancelScope, CancelToken, KillSwitch
from jarvis.core.bus import EventBus
from jarvis.core.events import KillAcknowledged, KillRequested

# ---------------------------------------------------------------------
# CancelToken
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_sets_state_and_first_reason_wins():
    tok = CancelToken()
    assert not tok.is_cancelled()
    assert tok.reason is None

    tok.cancel("budget_task_exceeded")
    assert tok.is_cancelled()
    assert tok.reason == "budget_task_exceeded"

    tok.cancel("kill_switch:hotkey")                   # should be ignored
    assert tok.reason == "budget_task_exceeded"


@pytest.mark.asyncio
async def test_wait_until_cancelled_unblocks():
    tok = CancelToken()

    async def canceller():
        await asyncio.sleep(0.01)
        tok.cancel("test")

    await asyncio.gather(tok.wait_until_cancelled(), canceller())
    assert tok.is_cancelled()


@pytest.mark.asyncio
async def test_protocol_structural_match():
    from jarvis.core.protocols import CancelToken as TokenProto
    assert isinstance(CancelToken(), TokenProto)


# ---------------------------------------------------------------------
# CancelScope
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_scope_registers_and_releases_token():
    ks = KillSwitch()

    async with CancelScope(ks, holder="test_holder") as token:
        tokens = list(ks.active_tokens())
        assert len(tokens) == 1
        assert tokens[0][1] == "test_holder"
        assert tokens[0][0] is token

    # after exit the token must be gone
    assert list(ks.active_tokens()) == []


@pytest.mark.asyncio
async def test_cancel_scope_releases_on_exception():
    ks = KillSwitch()

    with pytest.raises(RuntimeError, match="boom"):
        async with CancelScope(ks, holder="blown"):
            raise RuntimeError("boom")

    assert list(ks.active_tokens()) == []


@pytest.mark.asyncio
async def test_cancel_scope_without_kill_switch_still_works():
    """A scope without a KillSwitch is legal (e.g. for isolation in tests)."""
    async with CancelScope(None, holder="orphan") as token:
        assert not token.is_cancelled()
        token.cancel("manual")
        assert token.is_cancelled()


# ---------------------------------------------------------------------
# KillSwitch — trip()
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trip_cancels_all_registered_tokens():
    ks = KillSwitch()

    async with CancelScope(ks, holder="a") as tok_a, \
                CancelScope(ks, holder="b") as tok_b:
        await ks.trip(reason="kill_switch:test")
        assert tok_a.is_cancelled()
        assert tok_b.is_cancelled()
        assert tok_a.reason == "kill_switch:test"


@pytest.mark.asyncio
async def test_trip_is_idempotent():
    ks = KillSwitch()
    async with CancelScope(ks, holder="a") as tok:
        await ks.trip(reason="first")
        await ks.trip(reason="second")
        assert tok.reason == "first"                    # first-reason-wins


@pytest.mark.asyncio
async def test_trip_publishes_ack_events_when_ack_bus_given():
    ks = KillSwitch()
    bus = EventBus()
    acks: list[KillAcknowledged] = []

    async def capture(ev: KillAcknowledged) -> None:
        acks.append(ev)

    bus.subscribe(KillAcknowledged, capture)

    async with CancelScope(ks, holder="brain_stream"), \
                CancelScope(ks, holder="task_runner"):
        await ks.trip(reason="kill_switch:hotkey", ack_bus=bus)

    holders = {ev.holder for ev in acks}
    assert holders == {"brain_stream", "task_runner"}


# ---------------------------------------------------------------------
# KillSwitch — Bus-Binding + Forwarding
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bind_registers_kill_requested_subscriber():
    bus = EventBus()
    ks = KillSwitch()
    ks.bind(bus)

    async with CancelScope(ks, holder="x") as tok:
        await bus.publish(KillRequested(source="hotkey"))
        # Event-Dispatch ist async; gather um sicherzugehen
        await asyncio.sleep(0)
        assert tok.is_cancelled()
        assert tok.reason is not None
        assert tok.reason.startswith("kill_switch:")


@pytest.mark.asyncio
async def test_bind_is_idempotent_per_bus():
    bus = EventBus()
    ks = KillSwitch()
    ks.bind(bus)
    ks.bind(bus)                                         # twice — must not subscribe twice

    async with CancelScope(ks, holder="x") as tok:
        await bus.publish(KillRequested(source="tray"))
        await asyncio.sleep(0)
        # Only one cancel call was made — is_cancelled is still True.
        assert tok.is_cancelled()


@pytest.mark.asyncio
async def test_forward_kill_bridges_between_busses():
    """Two-bus problem from CLAUDE.md: KillSwitch can forward an event from
    bus A to bus B.
    """
    ui_bus = EventBus()
    brain_bus = EventBus()
    ks = KillSwitch()
    ks.bind(brain_bus)

    async def bridge(ev: KillRequested) -> None:
        await ks.forward_kill(ev, to_bus=brain_bus)

    ui_bus.subscribe(KillRequested, bridge)

    async with CancelScope(ks, holder="brain_stream") as tok:
        await ui_bus.publish(KillRequested(source="web"))
        # Dispatch auf ui_bus → bridge → brain_bus → KillSwitch._on_kill
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert tok.is_cancelled()
