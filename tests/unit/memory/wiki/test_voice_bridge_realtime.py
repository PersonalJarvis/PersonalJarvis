"""Realtime turns feed the wiki journal via ``VoiceTurnCompleted``.

The realtime engine never emits ``TranscriptFinal``/``MessageSent`` — the
pairing path in :class:`VoiceFactBridge` stays silent for it. The one event
that carries BOTH final texts of a realtime turn is ``VoiceTurnCompleted``
(``jarvis/realtime/session.py::_publish_turn_completed``). These tests pin
the bridge's realtime ingest contract:

1. A realtime turn (tier="realtime") flows extractor -> journal via the
   aggressive path (salience filter downstream, AP-9 fire-and-forget).
2. An ack-keyword reply uses the ack path even below the aggressive
   min-chars threshold.
3. Pipeline-tier ``VoiceTurnCompleted`` events are IGNORED here — those
   turns are already ingested via the TranscriptFinal/ResponseGenerated
   pairing; reacting twice would double-extract every pipeline turn.
4. The same realtime turn delivered twice is journaled once (hash gate).
5. Empty user transcript -> no ingest; empty reply does NOT block the
   aggressive path (the fact lives in the user text).
6. Aggressive ingests stay rate-limited across realtime turns.
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
from jarvis.core.events import VoiceTurnCompleted
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.memory.wiki.extractor import ConversationFactExtractor
from jarvis.memory.wiki.journal import CandidateJournal
from jarvis.memory.wiki.voice_bridge import VoiceFactBridge

FACT_SENTENCE = "Remember that my friend Lena moved to Hamburg last month."
SHORT_FACT = "Lena lives in Hamburg."  # >= 12 ack chars, < 30 aggressive chars


class FakeBrain:
    def __init__(self) -> None:
        self.call_count = 0

    name = "fake-brain"
    context_window = 100_000
    supports_tools = False
    supports_vision = False

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.call_count += 1
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

    def available(self) -> set[str]:
        return {"gemini"}

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


def _stack(tmp_path: Path):
    bus = EventBus()
    journal = CandidateJournal(tmp_path / "jarvis.db")
    brain = FakeBrain()
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )
    curator = FakeCurator()
    bridge = VoiceFactBridge(bus=bus, curator=curator, config=None, extractor=extractor)
    bridge.start()
    return bus, journal, curator, bridge, brain


def _realtime_turn(
    user_text: str,
    jarvis_text: str,
    *,
    tier: str = "realtime",
) -> VoiceTurnCompleted:
    return VoiceTurnCompleted(
        session_id="rt-session",
        turn_id="rt-turn-1",
        user_text=user_text,
        jarvis_text=jarvis_text,
        tier=tier,
        provider="gemini-live",
        model="gemini-3.1-flash-live-preview",
    )


async def _drain(journal: CandidateJournal, *, timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if journal.backlog_count() > 0:
            return
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_realtime_turn_feeds_journal_via_aggressive_path(tmp_path: Path) -> None:
    bus, journal, curator, bridge, _brain = _stack(tmp_path)
    try:
        await bus.publish(_realtime_turn(FACT_SENTENCE, "Okay, will do!"))
        await _drain(journal)
    finally:
        bridge.stop()

    rows = journal.pending()
    assert rows and rows[0].fact == "Lena moved to Hamburg."
    assert rows[0].source_label.startswith("realtime-aggressive:")
    assert curator.ingested == [], "extractor mode must not call curator.ingest directly"


@pytest.mark.asyncio
async def test_realtime_ack_reply_uses_ack_path(tmp_path: Path) -> None:
    """An acked short fact bypasses the aggressive min-chars threshold."""
    bus, journal, _curator, bridge, _brain = _stack(tmp_path)
    try:
        await bus.publish(_realtime_turn(SHORT_FACT, "Noted."))
        await _drain(journal)
    finally:
        bridge.stop()

    rows = journal.pending()
    assert rows, "acked realtime turn must be journaled"
    assert rows[0].source_label.startswith("realtime-fact:")


@pytest.mark.asyncio
async def test_pipeline_tier_turn_is_ignored(tmp_path: Path) -> None:
    """Pipeline turns already ingest via TranscriptFinal pairing — no double feed."""
    bus, journal, _curator, bridge, brain = _stack(tmp_path)
    try:
        await bus.publish(_realtime_turn(FACT_SENTENCE, "Noted.", tier="flash"))
        await asyncio.sleep(0.3)
    finally:
        bridge.stop()

    assert journal.backlog_count() == 0
    assert brain.call_count == 0


@pytest.mark.asyncio
async def test_same_realtime_turn_delivered_twice_journaled_once(tmp_path: Path) -> None:
    bus, journal, _curator, bridge, brain = _stack(tmp_path)
    try:
        # Ack path both times — the ack path has no rate limit, so the
        # second delivery is stopped by the turn-hash gate alone.
        await bus.publish(_realtime_turn(FACT_SENTENCE, "Noted."))
        await _drain(journal)
        await bus.publish(_realtime_turn(FACT_SENTENCE, "Noted."))
        await asyncio.sleep(0.2)
    finally:
        bridge.stop()

    assert journal.backlog_count() == 1, "duplicate realtime turn must be deduped by hash"
    assert brain.call_count == 1


@pytest.mark.asyncio
async def test_empty_user_text_is_ignored(tmp_path: Path) -> None:
    bus, journal, _curator, bridge, brain = _stack(tmp_path)
    try:
        await bus.publish(_realtime_turn("", "Noted."))
        await asyncio.sleep(0.2)
    finally:
        bridge.stop()

    assert journal.backlog_count() == 0
    assert brain.call_count == 0


@pytest.mark.asyncio
async def test_empty_reply_does_not_block_aggressive_path(tmp_path: Path) -> None:
    """A realtime turn can end with an empty output transcript (e.g. barge-in
    right after the fact was spoken) — the user fact must still be captured."""
    bus, journal, _curator, bridge, _brain = _stack(tmp_path)
    try:
        await bus.publish(_realtime_turn(FACT_SENTENCE, ""))
        await _drain(journal)
    finally:
        bridge.stop()

    rows = journal.pending()
    assert rows, "fact-shaped user text must be journaled even without a reply"
    assert rows[0].source_label.startswith("realtime-aggressive:")


@pytest.mark.asyncio
async def test_aggressive_path_is_rate_limited_across_realtime_turns(tmp_path: Path) -> None:
    """Chatty realtime sessions must not burn one LLM call per turn."""
    bus, journal, _curator, bridge, brain = _stack(tmp_path)
    try:
        await bus.publish(_realtime_turn(FACT_SENTENCE, "Okay!"))
        await _drain(journal)
        await bus.publish(
            _realtime_turn(
                "My favourite restaurant is the little place at the harbour.",
                "Sounds lovely!",
            )
        )
        await asyncio.sleep(0.2)
    finally:
        bridge.stop()

    assert journal.backlog_count() == 1, "second aggressive ingest within 60s must be skipped"
    assert brain.call_count == 1
