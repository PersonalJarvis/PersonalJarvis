"""Acceptance tests — the Definition of Done, as executable assertions.

The goal: a local entry point where a prompt comes in, the system replies
INSTANTLY ("Erledigt"-style optimistic ACK) and the Heavy-Duty Worker processes
the task ASYNCHRONOUSLY in the background.

These tests stay RED until all three sub-agent modules (bus/router, tools/worker,
oops) plus the orchestrator's talker.py exist and integrate.
"""
from __future__ import annotations

import asyncio
import time

from proto_testkit import FlightLog

from optimistic.bus import EventBus
from optimistic.events import (
    AckEmitted,
    MissionSpawn,
    WorkerCompleted,
    WorkerStarted,
)
from optimistic.oops import OopsProtocol
from optimistic.talker import Talker
from optimistic.worker import HeavyDutyWorker


def _build():
    bus = EventBus()
    flight = FlightLog(bus)
    worker = HeavyDutyWorker(bus)
    oops = OopsProtocol(bus)
    talker = Talker(bus, worker=worker, oops=oops)
    return bus, flight, worker, oops, talker


def test_definition_of_done_instant_ack_then_async_worker() -> None:
    """Prompt -> instant ACK, worker completes the task in the background AFTER."""

    async def scenario() -> None:
        _bus, flight, worker, _oops, talker = _build()

        t0 = time.perf_counter()
        reply = await talker.handle_utterance(
            "Trag mir morgen 15 Uhr einen Termin mit dem Steuerberater ein"  # i18n-allow: test content — user voice utterance DE
        )
        ack_latency = time.perf_counter() - t0

        # 1) Instant, optimistic acknowledgement.
        assert reply.strip(), "Talker must reply with an optimistic ACK"
        assert ack_latency < 3.0, f"ACK took {ack_latency:.3f}s (budget 3.0s)"

        # 2) Truly optimistic: the ACK fired BEFORE the heavy work finished.
        assert flight.has(AckEmitted)
        assert not flight.has(
            WorkerCompleted
        ), "the worker must still be running in the background at ACK time"

        # 3) The worker processes the task asynchronously.
        await worker.drain()
        assert flight.has(WorkerStarted)
        assert flight.has(WorkerCompleted)
        assert flight.index(AckEmitted) < flight.index(WorkerCompleted)

        await talker.aclose()

    asyncio.run(scenario())


def test_smalltalk_never_spawns_worker() -> None:
    async def scenario() -> None:
        _bus, flight, worker, _oops, talker = _build()
        reply = await talker.handle_utterance("Hallo, wie geht es dir heute?")
        await asyncio.sleep(0)  # let any stray scheduled task run
        assert reply.strip()
        assert not flight.has(MissionSpawn), "smalltalk must never wake the worker"
        assert worker.in_flight == 0

    asyncio.run(scenario())


def test_dumb_tool_fires_in_process_without_worker() -> None:
    async def scenario() -> None:
        _bus, flight, worker, _oops, talker = _build()
        reply = await talker.handle_utterance("spiel mal etwas Musik ab")
        await asyncio.sleep(0)
        assert reply.strip()
        assert not flight.has(
            MissionSpawn
        ), "a dumb (local) tool must never wake the worker (AD-OE3)"
        assert worker.in_flight == 0

    asyncio.run(scenario())
