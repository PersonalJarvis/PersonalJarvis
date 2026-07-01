"""End-to-end test for the "Oops" protocol — the spec's canonical scenario.

User: "Schreib Max eine Mail ..." -> worker discovers there is no address for
Max -> emits an invisible WorkerCorrectionNeeded -> the Talker injects it into
context but does NOT speak mid-utterance -> at the next VAD turn-boundary it
surfaces an organic, scrubbed correction.
"""
from __future__ import annotations

import asyncio

from optimistic.bus import EventBus
from optimistic.events import CorrectionReason, WorkerCorrectionNeeded
from optimistic.oops import OopsProtocol
from optimistic.talker import Talker
from optimistic.worker import HeavyDutyWorker
from proto_testkit import FlightLog


def test_missing_email_triggers_organic_correction_at_turn_boundary() -> None:
    async def scenario() -> None:
        bus = EventBus()
        flight = FlightLog(bus)
        worker = HeavyDutyWorker(bus)
        oops = OopsProtocol(bus)
        talker = Talker(bus, worker=worker, oops=oops)

        # The user is still talking when the background failure arrives.
        oops.set_user_speaking(True)

        reply = await talker.handle_utterance(
            "Schreib Max eine Mail, dass sich das Projekt verschiebt"  # i18n-allow: test content — user voice utterance DE
        )
        assert reply.strip(), "optimistic ACK must still fire even though it will later fail"

        await worker.drain()  # worker finds no email address for Max

        # 1) The failure surfaced as an invisible correction event.
        assert flight.has(WorkerCorrectionNeeded)
        ev = flight.of(WorkerCorrectionNeeded)[0]
        assert ev.reason is CorrectionReason.MISSING_INFO

        # 2) It was injected into the Talker context, NOT spoken mid-utterance.
        assert len(oops.pending) == 1, "correction must be injected into the Talker context"
        assert oops.is_user_speaking() is True

        # 3) At the turn-boundary, an organic, scrubbed correction surfaces.
        spoken = talker.vad_turn_boundary()
        assert len(spoken) == 1
        phrase = spoken[0].lower()
        assert "max" in phrase, "correction should organically name the missing recipient"
        assert "gmail" not in phrase and "`" not in phrase, "must be scrubbed for voice"
        assert oops.pending == [], "buffer cleared after speaking"

    asyncio.run(scenario())
