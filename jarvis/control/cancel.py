"""CancelToken + CancelScope + KillSwitch (Phase 5 ADR-0004).

Three components:

1. **CancelToken** — the propagation primitive. `asyncio.Event`-based, plus
   a `reason` field (first-reason-wins) so that later kill events do not
   overwrite an earlier budget-exceeded cancellation.

2. **CancelScope** — async context manager. Registers its token with the
   KillSwitch for the duration of the `async with` block. The scope is the
   only place where long-running operations receive a token — this prevents
   token leaks.

3. **KillSwitch** — aggregator. Holds all active tokens, subscribed to
   `KillRequested` on one or more buses (multi-bus support for the
   two-bus problem described in CLAUDE.md).

Usage:

    async with CancelScope(kill_switch, holder="brain_stream") as token:
        async for chunk in stream:
            if token.is_cancelled():
                break
            yield chunk
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING

from jarvis.core.events import KillAcknowledged, KillRequested

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus


# ----------------------------------------------------------------------
# CancelToken
# ----------------------------------------------------------------------

class CancelToken:
    """Concrete implementation of the `jarvis.core.protocols.CancelToken` protocol.

    Thread-safe for a single asyncio event loop. Multiple loops cannot safely
    share the same token — this is intentional, because the event bus and all
    long-running operations should live in the same loop.

    `reason` is frozen after the first `cancel()` call — if a budget overrun
    cancels first and the kill switch fires afterwards, the reason
    'budget_task_exceeded' is preserved. This matters for error reporting and
    replay.
    """

    __slots__ = ("_event", "_reason")

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason: str | None = None

    def cancel(self, reason: str) -> None:
        if self._reason is None:
            self._reason = reason
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    async def wait_until_cancelled(self) -> None:
        await self._event.wait()


# ----------------------------------------------------------------------
# CancelScope
# ----------------------------------------------------------------------

class CancelScope:
    """Async context manager that manages a CancelToken for the duration of an
    `async with` block.

    Responsibilities:
    - Registers the token with the `KillSwitch` on entry.
    - Releases it on exit (including on exceptions).
    - The holder name is a pure logging hint; it appears in `KillAcknowledged` events.

    The design principle: nobody obtains a token except through a scope. This
    prevents tokens from sitting as class attributes indefinitely and never
    being cleaned up by the KillSwitch.
    """

    def __init__(
        self,
        kill_switch: KillSwitch | None,
        *,
        holder: str,
    ) -> None:
        self._kill_switch = kill_switch
        self._holder = holder
        self.token = CancelToken()

    async def __aenter__(self) -> CancelToken:
        if self._kill_switch is not None:
            self._kill_switch.register(self.token, holder=self._holder)
        return self.token

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._kill_switch is not None:
            self._kill_switch.release(self.token)


# ----------------------------------------------------------------------
# KillSwitch
# ----------------------------------------------------------------------

class KillSwitch:
    """Aggregator for active CancelTokens. Reacts to `KillRequested` and
    cancels all known tokens with `reason='kill_switch'`.

    Two-bus pattern (ADR-0004): `bind(bus)` may be called multiple times —
    each bus is subscribed. This addresses the case described in CLAUDE.md
    where the `DesktopApp` integration creates a second bus (the
    Brain-Factory bus).
    """

    def __init__(self) -> None:
        self._tokens: dict[int, tuple[CancelToken, str, int]] = {}
        # id(token) -> (token, holder, registered_at_ns)
        self._bound_buses: list[EventBus] = []
        self._lock = asyncio.Lock()

    # ---- Token-Registry ----

    def register(self, token: CancelToken, *, holder: str) -> None:
        self._tokens[id(token)] = (token, holder, time.time_ns())

    def release(self, token: CancelToken) -> None:
        self._tokens.pop(id(token), None)

    def active_tokens(self) -> Iterable[tuple[CancelToken, str]]:
        """Snapshot of all active tokens. For debugging/tests only."""
        return [(tok, holder) for (tok, holder, _) in self._tokens.values()]

    # ---- Event-Bus-Binding ----

    def bind(self, bus: EventBus) -> None:
        """Register the KillSwitch as a KillRequested subscriber on `bus`.

        Binding to multiple buses is allowed — each bus will invoke
        `_on_kill`, but `trip()` is idempotent (multiple calls cancel tokens
        only once).
        """
        if bus in self._bound_buses:
            return
        self._bound_buses.append(bus)
        bus.subscribe(KillRequested, self._on_kill)

    async def _on_kill(self, event: KillRequested) -> None:
        await self.trip(reason=f"kill_switch:{event.source}", source_bus=None,
                        ack_bus=event_bus_of(event, self._bound_buses))

    async def trip(
        self,
        reason: str = "kill_switch",
        *,
        source_bus: EventBus | None = None,
        ack_bus: EventBus | None = None,
    ) -> None:
        """Cancel all active tokens and optionally publish a
        `KillAcknowledged` event per holder on `ack_bus`.

        `trip()` may also be called without a preceding `KillRequested` event
        (budget-exceed path: CostMeter calls `trip(...)` directly).
        """
        started_ns = time.time_ns()
        snapshot = list(self._tokens.values())
        for token, _holder, _registered in snapshot:
            token.cancel(reason)
        if ack_bus is not None:
            for _token, holder, registered_at_ns in snapshot:
                took_ms = max(0, (started_ns - registered_at_ns) // 1_000_000)
                with contextlib.suppress(Exception):
                    await ack_bus.publish(
                        KillAcknowledged(holder=holder, took_ms=took_ms)
                    )

    # ---- Two-Bus Forwarding ----

    async def forward_kill(
        self,
        event: KillRequested,
        *,
        to_bus: EventBus,
    ) -> None:
        """Re-publish a `KillRequested` event onto another bus.

        Useful when `DesktopApp._run_backend` starts a second bus
        (Brain-Factory bus, see CLAUDE.md) and the KillSwitch is subscribed
        only on the UI bus. Call once at startup:

            async def _forward(ev):
                await kill_switch.forward_kill(ev, to_bus=brain_bus)
            ui_bus.subscribe(KillRequested, _forward)
        """
        await to_bus.publish(event)


def event_bus_of(_event: KillRequested, bound: list[EventBus]) -> EventBus | None:
    """Heuristic: we do not know which bus the event came from because the bus
    reference is not embedded in the event. We publish acks on the last bound
    bus (typically the one that subscribed first and still accepts events).
    With only one bound bus this is trivial.
    """
    if not bound:
        return None
    return bound[-1]
