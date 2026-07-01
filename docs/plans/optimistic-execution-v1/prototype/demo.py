#!/usr/bin/env python3
"""Runnable demo of the Optimistic Execution prototype.

    python demo.py                # scripted walkthrough (default)
    python demo.py --interactive  # type your own prompts

Definition of Done, made tangible: you enter a prompt, the system replies
INSTANTLY ("Geht klar"), and the Heavy-Duty Worker logs — asynchronously, in the
background — that it is processing the task. When a background task fails (the
"Schreib Max eine Mail" scenario), the system self-corrects organically at the
next conversational turn-boundary instead of going silent.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time

# Make `optimistic` importable no matter the current working directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from optimistic.bus import EventBus
from optimistic.events import (
    Event,
    MissionSpawn,
    WorkerCompleted,
    WorkerCorrectionNeeded,
)
from optimistic.oops import OopsProtocol
from optimistic.talker import Talker
from optimistic.worker import HeavyDutyWorker


class FlightLog:
    """Minimal wildcard recorder (the production flight recorder is a subscribe_all)."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[Event] = []
        bus.subscribe_all(self._record)

    async def _record(self, ev: Event) -> None:
        self.events.append(ev)


def _configure_logging() -> None:
    """Route the worker's background INFO logs to stdout, visually marked."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("        |bg| %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _build():
    bus = EventBus()
    flight = FlightLog(bus)
    worker = HeavyDutyWorker(bus)
    oops = OopsProtocol(bus)
    talker = Talker(bus, worker=worker, oops=oops)
    return bus, flight, worker, oops, talker


# --- scripted walkthrough -------------------------------------------------------

# (title, prompt, is_oops)
SCENARIOS = [
    (
        "Smart Tool — delegated to the background worker",
        "Trag mir morgen 15 Uhr einen Termin mit dem Steuerberater ein",  # i18n-allow: test content — user voice utterance DE
        False,
    ),
    (
        "Dumb Tool — local script, fires in-process",
        "spiel mir etwas 80er Synthwave",  # i18n-allow: test content — user voice utterance DE
        False,
    ),
    (
        "Smalltalk — answered directly, worker never wakes",
        "Hey, wie geht's dir?",  # i18n-allow: test content — user voice utterance DE
        False,
    ),
    (
        "Oops — background failure self-corrects at the turn-boundary",
        "Schreib Max eine Mail, dass sich das Projekt verschiebt",  # i18n-allow: test content — user voice utterance DE
        True,
    ),
]


async def scripted() -> None:
    bus, flight, worker, oops, talker = _build()
    print("=" * 70)
    print(" OPTIMISTIC EXECUTION — scripted walkthrough")
    print(" Talker answers instantly; the Heavy-Duty Worker runs async in the bg.")
    print("=" * 70)

    for n, (title, prompt, is_oops) in enumerate(SCENARIOS, 1):
        print(f"\n--- Scenario {n}: {title} ---")
        if is_oops:
            oops.set_user_speaking(True)
            print("    (the user is still talking — a correction must NOT interrupt)")

        mark = len(flight.events)
        t0 = time.perf_counter()
        reply = await talker.handle_utterance(prompt)
        dt_ms = (time.perf_counter() - t0) * 1000.0

        print(f"  [you]    {prompt}")
        print(f"  [jarvis] (instant, {dt_ms:.2f} ms)  {reply}")

        # Let the background settle — the worker's INFO log prints here.
        await worker.drain()

        turn = flight.events[mark:]
        spawned = any(isinstance(e, MissionSpawn) for e in turn)
        completed = [e for e in turn if isinstance(e, WorkerCompleted)]
        corrections = [e for e in turn if isinstance(e, WorkerCorrectionNeeded)]

        if not spawned:
            print("  [trace]  no MissionSpawn — the worker stayed asleep (0 false-spawns)")
        if completed:
            print(f"  [result] background mission done -> {completed[0].result}")
        if corrections:
            c = corrections[0]
            print(f"  [oops]   invisible background snag: {c.reason.value} — {c.detail}")
            print(
                "  [ctx]    injected into Talker context (unspoken): "
                f"{talker.injected_context()}"
            )
            print("    (... the user finishes their sentence — VAD turn-boundary ...)")
            for s in talker.vad_turn_boundary():
                print(f"  [jarvis] (organic correction at turn-boundary)  {s}")

    print("\n" + "=" * 70)
    print(" Done. Instant ACKs above; |bg| lines are the async worker; the final")
    print(" scenario shows the self-correcting 'Oops' loop. No blocking, ever.")
    print("=" * 70)


# --- interactive REPL -----------------------------------------------------------

INTERACTIVE_HELP = (
    "Type a prompt and press Enter. Jarvis answers instantly; the worker runs in the\n"
    "background (its log is the |bg| line). Special commands:\n"
    "    \\boundary   surface any pending background correction (simulates end-of-turn)\n"
    "    \\quit       exit\n"
    "Try:  'spiel etwas Musik'  ·  'trag morgen einen Termin ein'  ·  'Schreib Max eine Mail'\n"  # i18n-allow: test content — user voice utterance DE
)


def interactive() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _bus, _flight, worker, oops, talker = _build()
    print("=" * 70)
    print(" OPTIMISTIC EXECUTION — interactive mode")
    print("=" * 70)
    print(INTERACTIVE_HELP)
    try:
        while True:
            try:
                text = input("[you] ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not text:
                continue
            if text in ("\\quit", "\\exit", "quit", "exit"):
                break
            if text in ("\\boundary", "\\b"):
                spoken = talker.vad_turn_boundary()
                if spoken:
                    for s in spoken:
                        print(f"[jarvis] (turn-boundary)  {s}")
                else:
                    print("[jarvis] (nothing pending)")
                continue

            oops.set_user_speaking(True)
            t0 = time.perf_counter()
            reply = loop.run_until_complete(talker.handle_utterance(text))
            dt_ms = (time.perf_counter() - t0) * 1000.0
            print(f"[jarvis] (instant, {dt_ms:.2f} ms)  {reply}")
            loop.run_until_complete(worker.drain())
            if oops.pending:
                print("[hint]   a correction is waiting — type \\boundary to let Jarvis speak it")
    finally:
        loop.run_until_complete(worker.drain())
        loop.close()


def main() -> None:
    # Windows cp1252 stdout trap (CLAUDE.md): a CLI emitting German umlauts must
    # force UTF-8 or the console renders mojibake. Harmless no-op elsewhere.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: S110 - best-effort console UTF-8; safe to ignore
            pass

    parser = argparse.ArgumentParser(description="Optimistic Execution prototype demo")
    parser.add_argument("--interactive", action="store_true", help="type your own prompts")
    args = parser.parse_args()
    _configure_logging()
    if args.interactive:
        interactive()
    else:
        asyncio.run(scripted())


if __name__ == "__main__":
    main()
