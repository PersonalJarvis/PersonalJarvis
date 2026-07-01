"""Unit tests for the kill-switch wiring (voice + tray + hotkey)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from jarvis.control import (
    CancelScope,
    KillSwitch,
    voice_matches_kill_intent,
    wire_tray_kill_switch,
    wire_voice_kill_switch,
)
from jarvis.core.bus import EventBus
from jarvis.core.events import KillRequested, TranscriptFinal
from jarvis.core.protocols import Transcript

# ---------------------------------------------------------------------
# Voice-Intent-Regex
# ---------------------------------------------------------------------

@pytest.mark.parametrize("phrase", [
    "Jarvis, stopp",
    "Stopp Jarvis!",
    "Notfall-Stopp",
    "Notstopp jetzt",
    "kill switch aktivieren",
    "emergency stop",
    "Alles stoppen",
    "Alles stopp",
    "abort all",
])
def test_voice_matches_kill_intent_positives(phrase):
    assert voice_matches_kill_intent(phrase), phrase


@pytest.mark.parametrize("phrase", [
    "Hallo Jarvis",
    "Ich schreibe eine Email",
    "Lies mir das vor",
    "stopp als Wort in einem normalen Satz klingt harmloser als es ist",  # i18n-allow: German speech-input test vocabulary
    "",
])
def test_voice_matches_kill_intent_negatives(phrase):
    # IMPORTANT: the 3rd sentence contains "stopp" — our regex only matches
    # `jarvis stopp` or `stopp jarvis`, not every "stopp".
    if "jarvis" in phrase.lower() and "stopp" in phrase.lower():
        return  # special case, intentionally overlaps
    assert not voice_matches_kill_intent(phrase), phrase


@pytest.mark.asyncio
async def test_voice_wiring_publishes_on_kill_phrase():
    bus = EventBus()
    wire_voice_kill_switch(bus)

    killed: list[KillRequested] = []
    async def capture(ev: KillRequested) -> None:
        killed.append(ev)
    bus.subscribe(KillRequested, capture)

    await bus.publish(TranscriptFinal(
        transcript=Transcript(text="Jarvis, stopp", language="de", confidence=0.9),
    ))
    await asyncio.sleep(0)

    assert len(killed) == 1
    assert killed[0].source == "voice"


@pytest.mark.asyncio
async def test_voice_wiring_ignores_normal_speech():
    bus = EventBus()
    wire_voice_kill_switch(bus)

    killed: list[KillRequested] = []
    async def capture(ev: KillRequested) -> None:
        killed.append(ev)
    bus.subscribe(KillRequested, capture)

    await bus.publish(TranscriptFinal(
        transcript=Transcript(text="Oeffne Outlook bitte",
                              language="de", confidence=0.9),
    ))
    await asyncio.sleep(0)

    assert killed == []


# ---------------------------------------------------------------------
# Tray-Wiring
# ---------------------------------------------------------------------

@dataclass
class _FakeTrayCommand:
    action: str


@pytest.mark.asyncio
async def test_tray_wiring_publishes_kill_on_kill_action():
    bus = EventBus()
    queue: asyncio.Queue[_FakeTrayCommand] = asyncio.Queue()
    stop = asyncio.Event()

    killed: list[KillRequested] = []
    async def capture(ev: KillRequested) -> None:
        killed.append(ev)
    bus.subscribe(KillRequested, capture)

    task = wire_tray_kill_switch(queue, bus, stop_event=stop)

    await queue.put(_FakeTrayCommand(action="kill"))
    await asyncio.sleep(0.25)           # Tray-Bridge hat timeout=0.2s
    stop.set()
    await task

    assert len(killed) == 1
    assert killed[0].source == "tray"


@pytest.mark.asyncio
async def test_tray_wiring_ignores_non_kill_actions():
    bus = EventBus()
    queue: asyncio.Queue[_FakeTrayCommand] = asyncio.Queue()
    stop = asyncio.Event()

    killed: list[KillRequested] = []
    async def capture(ev: KillRequested) -> None:
        killed.append(ev)
    bus.subscribe(KillRequested, capture)

    task = wire_tray_kill_switch(queue, bus, stop_event=stop)
    await queue.put(_FakeTrayCommand(action="pause"))
    await queue.put(_FakeTrayCommand(action="reload_config"))
    await asyncio.sleep(0.25)
    stop.set()
    await task

    assert killed == []


# ---------------------------------------------------------------------
# End-to-End: Voice → Bus → KillSwitch → Token
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_voice_trigger_cancels_registered_tokens():
    """This is the DoD replacement test: voice intent → CancelToken active."""
    bus = EventBus()
    ks = KillSwitch()
    ks.bind(bus)
    wire_voice_kill_switch(bus)

    async with CancelScope(ks, holder="brain_stream") as token:
        await bus.publish(TranscriptFinal(
            transcript=Transcript(text="Notfall-Stopp!",
                                  language="de", confidence=0.9),
        ))
        # Double await, so the event dispatch runs through:
        # TranscriptFinal → voice_handler → KillRequested → KillSwitch._on_kill → token.cancel
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert token.is_cancelled()
        assert token.reason and token.reason.startswith("kill_switch:")
