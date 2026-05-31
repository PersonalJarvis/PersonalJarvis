"""Latency SLO tests (the "validate latency first" mandate from the goal).

M1: p95 intent -> ACK complete < 3.0 s (in-process proxy budget: < 1.2 s).
M2: router decision latency < 150 ms, never awaiting a network call.
AD-OE1: the optimistic ACK is emitted BEFORE the worker dispatch completes.
"""
from __future__ import annotations

import asyncio
import time

from optimistic.bus import EventBus
from optimistic.events import AckEmitted, WorkerCompleted
from optimistic.oops import OopsProtocol
from optimistic.router import classify
from optimistic.talker import Talker
from optimistic.worker import HeavyDutyWorker
from proto_testkit import FlightLog, percentile

ROUTER_SAMPLES = [
    "Hallo, wie geht's?",
    "Danke dir!",
    "spiel etwas Musik ab",
    "mach mal lauter",
    "mach die Adjusties",
    "Schreib Max eine E-Mail",
    "Maile dem Team den Status",
    "Trag morgen einen Termin ein",
    "Lad das Dokument auf Drive hoch",
    "Erzähl mir einen Witz",
    "Buche mir einen Flug nach Berlin",
    "öffne die Projektseite",
    "Wie spät ist es?",
    "spiel Spotify ab",
    "leiser bitte",
    "schreib eine Notiz",
    "such mir das Quartalsergebnis",
    "installier das Update",
    "zeig mir den Kalender",
    "Guten Morgen!",
]


def test_router_decision_under_150ms() -> None:
    for c in ROUTER_SAMPLES:  # warm up
        classify(c)
    worst = 0.0
    for c in ROUTER_SAMPLES:
        t0 = time.perf_counter()
        classify(c)
        worst = max(worst, time.perf_counter() - t0)
    assert worst < 0.150, f"router worst-case {worst * 1000:.3f}ms exceeds the 150ms budget"


def test_ack_emitted_before_worker_completes() -> None:
    async def scenario() -> None:
        bus = EventBus()
        flight = FlightLog(bus)
        worker = HeavyDutyWorker(bus)
        oops = OopsProtocol(bus)
        talker = Talker(bus, worker=worker, oops=oops)

        await talker.handle_utterance("Schreib eine Mail an das Team über den Launch")
        assert flight.has(AckEmitted), "ACK must exist the instant handle_utterance returns"
        assert not flight.has(WorkerCompleted), "worker must not have completed yet (AD-OE1)"
        await worker.drain()

    asyncio.run(scenario())


def test_p95_ack_latency_under_budget() -> None:
    async def scenario() -> None:
        latencies: list[float] = []
        for i in range(30):
            bus = EventBus()
            worker = HeavyDutyWorker(bus)
            oops = OopsProtocol(bus)
            talker = Talker(bus, worker=worker, oops=oops)
            t0 = time.perf_counter()
            await talker.handle_utterance(f"Schreib eine Mail an das Team ueber Thema {i}")
            latencies.append(time.perf_counter() - t0)
            await worker.drain()
        p95 = percentile(latencies, 95)
        assert p95 < 1.2, f"p95 ACK latency {p95 * 1000:.1f}ms exceeds the 1.2s in-process budget"

    asyncio.run(scenario())
