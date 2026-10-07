"""Conversation observer: explicitly saved voice + chat turns feed the journal.

Pins five contracts:

1. A voice turn the brain acknowledged ("Noted.") flows extractor -> journal.
2. A chat turn (``MessageSent(role="user")`` + ``ResponseGenerated``)
   feeds the same journal.
3. The same turn text delivered via BOTH event paths is journaled once
   (turn-hash dedupe — voice turns surface as TranscriptFinal AND as the
   server's MessageSent mirror).
4. AP-9: the bus handlers return immediately; extraction happens in a
   fire-and-forget background task, never awaited on the voice path.
5. A turn the brain did NOT acknowledge is never reviewed: the automatic
   per-turn review was removed on 2026-09-30.
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.config import (
    BrainConfig,
    BrainProviderConfig,
    JarvisConfig,
    MemoryConfig,
    WikiMemoryConfig,
)
from jarvis.core.events import MessageSent, ResponseGenerated, TranscriptFinal
from jarvis.core.protocols import BrainDelta, BrainRequest, Transcript
from jarvis.memory.wiki.extractor import ConversationFactExtractor
from jarvis.memory.wiki.journal import CandidateJournal
from jarvis.memory.wiki.voice_bridge import VoiceFactBridge

FACT_SENTENCE = "Remember that my friend Lena moved to Hamburg last month."


# Hang guard for background work, never a measured latency: a loaded CI runner
# runs this file several times slower than a laptop.
_HANG_GUARD_S = 30.0


class FakeBrain:
    def __init__(self, *, gate: asyncio.Event | None = None) -> None:
        self.gate = gate
        self.call_count = 0
        self.completed = asyncio.Event()

    name = "fake-brain"
    context_window = 100_000
    supports_tools = False
    supports_vision = False

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.call_count += 1
        if self.gate is not None:
            await self.gate.wait()
        prompt = req.messages[-1].content
        assert isinstance(prompt, str)
        focus_match = re.search(r"FOCUS USER TURN \[([^\]]+)]", prompt)
        assert focus_match is not None
        evidence_turn_id = focus_match.group(1)
        yield BrainDelta(
            content=json.dumps(
                [
                    {
                        "fact": "Lena moved to Hamburg.",
                        "kind": "person",
                        "subjects": ["lena"],
                        "evidence_turn_id": evidence_turn_id,
                    }
                ]
            )
        )
        yield BrainDelta(finish_reason="stop")
        self.completed.set()

    def estimate_cost(self, req: BrainRequest) -> float:  # pragma: no cover
        return 0.0


class FakeRegistry:
    def __init__(self, brain: Any) -> None:
        self._brain = brain

    def available(self) -> set[str]:
        # The configured primary is reachable, so the key-aware fallback chain
        # is a single hop to it.
        return {"gemini"}

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        return self._brain


def _config() -> JarvisConfig:
    return JarvisConfig(
        brain=BrainConfig(
            primary="gemini",
            providers={"gemini": BrainProviderConfig(model="gemini-3.1-pro-preview")},
        ),
        memory=MemoryConfig(wiki=WikiMemoryConfig()),
    )


def _stack(tmp_path: Path, *, brain_gate: asyncio.Event | None = None):
    bus = EventBus()
    journal = CandidateJournal(tmp_path / "jarvis.db")
    brain = FakeBrain(gate=brain_gate)
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )
    bridge = VoiceFactBridge(bus=bus, extractor=extractor)
    bridge.start()
    return bus, journal, None, bridge, brain


async def _drain(bridge: VoiceFactBridge) -> None:
    """Wait for every review the bridge started, however slow the host is."""
    async with asyncio.timeout(_HANG_GUARD_S):
        while bridge._inflight:  # noqa: SLF001 - test seam: the bridge's own task set
            await asyncio.gather(*bridge._inflight, return_exceptions=True)  # noqa: SLF001


@pytest.mark.asyncio
async def test_voice_turn_feeds_journal_not_direct_ingest(tmp_path: Path) -> None:
    bus, journal, _unused, bridge, _brain = _stack(tmp_path)
    try:
        await bus.publish(TranscriptFinal(
            transcript=Transcript(text=FACT_SENTENCE, language="en", confidence=0.95),
        ))
        await bus.publish(ResponseGenerated(text="Noted.", language="en"))
        await _drain(bridge)
    finally:
        bridge.stop()

    rows = journal.pending()
    assert rows and rows[0].fact == "Lena moved to Hamburg."
    assert rows[0].source_label.startswith("voice-fact:")


@pytest.mark.asyncio
async def test_chat_turn_feeds_same_journal(tmp_path: Path) -> None:
    bus, journal, _unused, bridge, _brain = _stack(tmp_path)
    try:
        await bus.publish(MessageSent(thread_id="t1", role="user", text=FACT_SENTENCE))
        await bus.publish(ResponseGenerated(text="Noted.", language="en"))
        await _drain(bridge)
    finally:
        bridge.stop()

    rows = journal.pending()
    assert rows and rows[0].fact == "Lena moved to Hamburg."
    assert rows[0].source_label.startswith("chat-fact:")


@pytest.mark.asyncio
async def test_same_text_via_both_paths_journaled_once(tmp_path: Path) -> None:
    """Voice turns surface as TranscriptFinal AND MessageSent — one journal entry."""
    bus, journal, _curator, bridge, brain = _stack(tmp_path)
    try:
        # Both transport signals arrive before the one response that closes
        # the turn. The MessageSent mirror must not replace the pending voice
        # identity or start a second extraction.
        await bus.publish(TranscriptFinal(
            transcript=Transcript(text=FACT_SENTENCE, language="en", confidence=0.95),
        ))
        await bus.publish(MessageSent(thread_id="t1", role="user", text=FACT_SENTENCE))
        await bus.publish(ResponseGenerated(text="Noted.", language="en"))
        await _drain(bridge)
    finally:
        bridge.stop()

    assert journal.backlog_count() == 1, "one transport-mirrored turn is one review"
    assert brain.call_count == 1


@pytest.mark.asyncio
async def test_same_text_in_a_later_turn_is_reviewed_again(tmp_path: Path) -> None:
    """Transport dedupe must not suppress a genuinely repeated later turn."""
    bus, journal, _curator, bridge, brain = _stack(tmp_path)
    try:
        for index in range(2):
            brain.completed.clear()
            await bus.publish(
                MessageSent(thread_id="t1", role="user", text=FACT_SENTENCE)
            )
            await bus.publish(ResponseGenerated(text="Noted.", language="en"))
            await asyncio.wait_for(brain.completed.wait(), timeout=_HANG_GUARD_S)
            await _drain(bridge)
            assert journal.backlog_count() == index + 1
            assert brain.call_count == index + 1
    finally:
        bridge.stop()

    assert journal.backlog_count() == 2
    assert brain.call_count == 2


@pytest.mark.asyncio
async def test_ap9_handlers_return_before_extraction_completes(tmp_path: Path) -> None:
    """AP-9: publishing the turn never blocks on the LLM extraction."""
    # The extraction blocks until the test releases it, so a handler that
    # awaited it would never return: the publishes below would hit the guard.
    release = asyncio.Event()
    bus, journal, _curator, bridge, brain = _stack(tmp_path, brain_gate=release)
    try:
        async with asyncio.timeout(_HANG_GUARD_S):
            await bus.publish(TranscriptFinal(
                transcript=Transcript(text=FACT_SENTENCE, language="en", confidence=0.95),
            ))
            await bus.publish(ResponseGenerated(text="Noted.", language="en"))
        # Extraction has not finished yet — the journal is still empty.
        assert not brain.completed.is_set()
        assert journal.backlog_count() == 0
        # ... and completes later in the background.
        release.set()
        await _drain(bridge)
        assert journal.backlog_count() == 1
    finally:
        bridge.stop()


@pytest.mark.asyncio
async def test_unacknowledged_turns_are_never_reviewed(tmp_path: Path) -> None:
    """No automatic per-turn review: without an acknowledgement nothing bills."""
    bus, journal, _unused, bridge, brain = _stack(tmp_path)
    try:
        await bus.publish(TranscriptFinal(
            transcript=Transcript(text=FACT_SENTENCE, language="en", confidence=0.95),
        ))
        await bus.publish(ResponseGenerated(text="Hamburg is lovely.", language="en"))
        await bus.publish(MessageSent(thread_id="t1", role="user", text=FACT_SENTENCE * 2))
        await bus.publish(ResponseGenerated(text="Tell me more!", language="en"))
        # Any review the handlers started is awaited, so a wrong dispatch is
        # caught however slowly it would have run.
        await _drain(bridge)
    finally:
        bridge.stop()

    assert brain.call_count == 0
    assert journal.backlog_count() == 0
