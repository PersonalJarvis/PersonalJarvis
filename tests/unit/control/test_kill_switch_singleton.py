"""Process-wide KillSwitch singleton + the Emergency-Stop chain (C-02).

Deep-dive 2026-07-15 found the advertised global Emergency Stop was never
wired: no KillSwitch instance existed at boot, ComputerUseContext.kill_switch
stayed None, and the tray "kill" command was swallowed. These tests pin the
repaired chain: singleton identity, factory-style bind, and the full
KillRequested -> registered-CU-token cancellation path (ADR-0004).
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.control import CancelScope, KillSwitch, get_kill_switch
from jarvis.control import cancel as cancel_mod
from jarvis.core.bus import EventBus
from jarvis.core.events import KillRequested


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Isolate the process-global singleton per test."""
    cancel_mod._PROCESS_KILL_SWITCH = None
    yield
    cancel_mod._PROCESS_KILL_SWITCH = None


def test_get_kill_switch_returns_one_shared_instance():
    first = get_kill_switch()
    second = get_kill_switch()
    assert isinstance(first, KillSwitch)
    assert first is second


@pytest.mark.asyncio
async def test_bind_is_idempotent_per_bus():
    bus = EventBus()
    ks = get_kill_switch()
    ks.bind(bus)
    ks.bind(bus)  # second bind must not double-subscribe
    assert ks._bound_buses.count(bus) == 1


@pytest.mark.asyncio
async def test_kill_requested_cancels_a_registered_cu_token():
    """The full Emergency-Stop chain: a KillRequested event on a bound bus
    cancels the token a CU mission registered via CancelScope."""
    bus = EventBus()
    ks = get_kill_switch()
    ks.bind(bus)

    async with CancelScope(ks, holder="cu_loop") as token:
        assert not token.is_cancelled()
        await bus.publish(KillRequested(source="tray"))
        # The bus dispatches subscribers asynchronously; give it a beat.
        for _ in range(50):
            if token.is_cancelled():
                break
            await asyncio.sleep(0.01)
        assert token.is_cancelled()
        assert (token.reason or "").startswith("kill_switch")


@pytest.mark.asyncio
async def test_factory_injects_the_singleton_into_the_cu_context():
    """The brain factory must hand the SAME process-wide switch to the CU
    context — the harness's CancelScope(ctx.kill_switch) only works then.

    Exercised structurally (no full factory boot): the context accepts the
    singleton and the harness-visible field is non-None.
    """
    from jarvis.harness.computer_use_context import ComputerUseContext

    ks = get_kill_switch()
    ctx = ComputerUseContext(
        vision_engine=object(),
        brain_manager=object(),
        tool_executor=object(),
        kill_switch=ks,
    )
    assert ctx.kill_switch is ks
