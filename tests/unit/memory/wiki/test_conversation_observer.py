"""Conversation observer (Wave-2 B3): voice + chat turns feed the journal.

Pins four contracts:

1. A completed voice turn flows extractor -> journal; the legacy direct
   ``curator.ingest`` path is NOT called when an extractor is attached.
2. A chat turn (``MessageSent(role="user")`` + ``ResponseGenerated``)
   feeds the same journal.
3. The same turn text delivered via BOTH event paths is journaled once
   (turn-hash dedupe — voice turns surface as TranscriptFinal AND as the
   server's MessageSent mirror).
4. AP-9: the bus handlers return immediately; extraction happens in a
   fire-and-forget background task, never awaited on the voice path.

Plus: without an extractor the bridge falls back to the legacy direct
``curator.ingest`` path unchanged.
"""
from __future__ import annotations

import asyncio
import json
import time
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


class FakeBrain:
    def __init__(self, *, sleep_s: float = 0.0) -> None:
        self.sleep_s = sleep_s
        self.call_count = 0

    name = "fake-brain"
    context_window = 100_000
    supports_tools = False
    supports_vision = False

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.call_count += 1
        if self.sleep_s:
            await asyncio.sleep(self.sleep_s)
        yield BrainDelta(
            content=json.dumps(
                [{"fact": "Lena moved to Hamburg.", "kind": "person", "subjects": ["lena"]}]
            )
        )
        yield BrainDelta(finish_reason="stop")

    def estimate_cost(self, req: BrainRequest) -> float:  # pragma: no cover
        return 0.0


class FakeRegistry:
    def __init__(self, brain: Any) -> None:
        self._brain = brain

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        return self._brain


class FakeCurator:
    def __init__(self) -> None:
        self.ingested: list[str] = []

    async def ingest(self, source_content: str, source_label: str) -> Any:
        self.ingested.append(source_content)

        class _R:
            applied: list = []
            skipped_due_to_recent_edit: list = []
            failed_validation: list = []

        return _R()


def _config() -> JarvisConfig:
    return JarvisConfig(
        brain=BrainConfig(
            primary="gemini",
            providers={"gemini": BrainProviderConfig(model="gemini-3.1-pro-preview")},
        ),
        memory=MemoryConfig(wiki=WikiMemoryConfig()),
    )


def _stack(tmp_path: Path, *, brain_sleep_s: float = 0.0):
    bus = EventBus()
    journal = CandidateJournal(tmp_path / "jarvis.db")
    brain = FakeBrain(sleep_s=brain_sleep_s)
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )
    curator = FakeCurator()
    bridge = VoiceFactBridge(bus=bus, curator=curator, config=None, extractor=extractor)
    bridge.start()
    return bus, journal, curator, bridge, brain


async def _drain(journal: CandidateJournal, *, timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if journal.backlog_count() > 0:
            return
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_voice_turn_feeds_journal_not_direct_ingest(tmp_path: Path) -> None:
    bus, journal, curator, bridge, _brain = _stack(tmp_path)
    try:
        await bus.publish(TranscriptFinal(
            transcript=Transcript(text=FACT_SENTENCE, language="en", confidence=0.95),
        ))
        await bus.publish(ResponseGenerated(text="Noted.", language="en"))
        await _drain(journal)
    finally:
        bridge.stop()

    rows = journal.pending()
    assert rows and rows[0].fact == "Lena moved to Hamburg."
    assert rows[0].source_label.startswith("voice-fact:")
    assert curator.ingested == [], "extractor mode must not call curator.ingest directly"


@pytest.mark.asyncio
async def test_chat_turn_feeds_same_journal(tmp_path: Path) -> None:
    bus, journal, curator, bridge, _brain = _stack(tmp_path)
    try:
        await bus.publish(MessageSent(thread_id="t1", role="user", text=FACT_SENTENCE))
        await bus.publish(ResponseGenerated(text="Noted.", language="en"))
        await _drain(journal)
    finally:
        bridge.stop()

    rows = journal.pending()
    assert rows and rows[0].fact == "Lena moved to Hamburg."
    assert rows[0].source_label.startswith("chat-fact:")
    assert curator.ingested == []


@pytest.mark.asyncio
async def test_same_text_via_both_paths_journaled_once(tmp_path: Path) -> None:
    """Voice turns surface as TranscriptFinal AND MessageSent — one journal entry."""
    bus, journal, _curator, bridge, brain = _stack(tmp_path)
    try:
        # First delivery: voice path.
        await bus.publish(TranscriptFinal(
            transcript=Transcript(text=FACT_SENTENCE, language="en", confidence=0.95),
        ))
        await bus.publish(ResponseGenerated(text="Noted.", language="en"))
        await _drain(journal)
        # Second delivery of the SAME text: the server's chat mirror.
        await bus.publish(MessageSent(thread_id="t1", role="user", text=FACT_SENTENCE))
        await bus.publish(ResponseGenerated(text="Noted.", language="en"))
        await asyncio.sleep(0.2)
    finally:
        bridge.stop()

    assert journal.backlog_count() == 1, "duplicate turn must be deduped by hash"
    assert brain.call_count == 1


@pytest.mark.asyncio
async def test_ap9_handlers_return_before_extraction_completes(tmp_path: Path) -> None:
    """AP-9: publishing the turn never blocks on the LLM extraction."""
    bus, journal, _curator, bridge, _brain = _stack(tmp_path, brain_sleep_s=0.5)
    try:
        started = time.monotonic()
        await bus.publish(TranscriptFinal(
            transcript=Transcript(text=FACT_SENTENCE, language="en", confidence=0.95),
        ))
        await bus.publish(ResponseGenerated(text="Noted.", language="en"))
        elapsed = time.monotonic() - started
        assert elapsed < 0.1, f"voice path blocked for {elapsed:.3f}s on extraction"
        # Extraction has not finished yet — the journal is still empty.
        assert journal.backlog_count() == 0
        # ... and completes later in the background.
        await _drain(journal, timeout_s=3.0)
        assert journal.backlog_count() == 1
    finally:
        bridge.stop()


@pytest.mark.asyncio
async def test_without_extractor_legacy_direct_ingest_still_fires(tmp_path: Path) -> None:
    bus = EventBus()
    curator = FakeCurator()
    bridge = VoiceFactBridge(bus=bus, curator=curator, config=None, extractor=None)
    bridge.start()
    try:
        await bus.publish(TranscriptFinal(
            transcript=Transcript(text=FACT_SENTENCE, language="en", confidence=0.95),
        ))
        await bus.publish(ResponseGenerated(text="Noted.", language="en"))
        for _ in range(100):  # up to ~2 s
            if curator.ingested:
                break
            await asyncio.sleep(0.02)
    finally:
        bridge.stop()

    assert curator.ingested, "fallback path must keep the legacy direct ingest alive"
