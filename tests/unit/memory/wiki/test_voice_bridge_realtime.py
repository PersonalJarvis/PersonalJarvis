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
import re
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
    VoiceBridgeConfig,
    WikiMemoryConfig,
)
from jarvis.core.events import VoiceSessionEnded, VoiceTurnCompleted
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.memory.wiki.extractor import ConversationFactExtractor
from jarvis.memory.wiki.journal import CandidateJournal
from jarvis.memory.wiki.voice_bridge import VoiceFactBridge

FACT_SENTENCE = "Remember that my friend Lena moved to Hamburg last month."
SHORT_FACT = "Lena lives in Hamburg."  # >= 12 ack chars, < 30 aggressive chars


class FakeBrain:
    def __init__(self) -> None:
        self.call_count = 0
        self.received_requests: list[BrainRequest] = []

    name = "fake-brain"
    context_window = 100_000
    supports_tools = False
    supports_vision = False

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.call_count += 1
        self.received_requests.append(req)
        prompt = req.messages[0].content
        evidence_ids = re.findall(r"(?:FOCUS )?USER TURN \[([^\]]+)\]", prompt)
        evidence = evidence_ids[-1] if evidence_ids else ""
        yield BrainDelta(
            content=json.dumps(
                [
                    {
                        "fact": "Lena moved to Hamburg.",
                        "kind": "person",
                        "subjects": ["lena"],
                        "evidence_turn_id": evidence,
                    }
                ]
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


def _stack(tmp_path: Path, *, bridge_config: VoiceBridgeConfig | None = None):
    bus = EventBus()
    journal = CandidateJournal(tmp_path / "jarvis.db")
    brain = FakeBrain()
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=FakeRegistry(brain),
    )
    curator = FakeCurator()
    bridge = VoiceFactBridge(
        bus=bus,
        curator=curator,
        config=bridge_config,
        extractor=extractor,
    )
    bridge.start()
    return bus, journal, curator, bridge, brain


def _realtime_turn(
    user_text: str,
    jarvis_text: str,
    *,
    tier: str = "realtime",
    session_id: str = "rt-session",
    turn_id: str = "rt-turn-1",
) -> VoiceTurnCompleted:
    return VoiceTurnCompleted(
        session_id=session_id,
        turn_id=turn_id,
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


async def _wait_for_calls(brain: FakeBrain, count: int, *, timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while brain.call_count < count and time.monotonic() < deadline:  # noqa: ASYNC110
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
    """Operators can opt into rate limiting when call cost matters more."""
    bus, journal, _curator, bridge, brain = _stack(
        tmp_path,
        bridge_config=VoiceBridgeConfig(rate_limit_seconds=60),
    )
    try:
        await bus.publish(_realtime_turn(FACT_SENTENCE, "Okay!"))
        await _drain(journal)
        await bus.publish(
            _realtime_turn(
                "My favourite restaurant is the little place at the harbour.",
                "Sounds lovely!",
                turn_id="rt-turn-2",
            )
        )
        await asyncio.sleep(0.2)
    finally:
        bridge.stop()

    assert journal.backlog_count() == 1, "second aggressive ingest within 60s must be skipped"
    assert brain.call_count == 1


@pytest.mark.asyncio
async def test_default_reviews_consecutive_realtime_turns(tmp_path: Path) -> None:
    """The default must not silently discard a second durable fact."""
    bus, journal, _curator, bridge, brain = _stack(tmp_path)
    try:
        await bus.publish(_realtime_turn(FACT_SENTENCE, "Okay!"))
        await _drain(journal)
        await bus.publish(
            _realtime_turn(
                "My favourite restaurant is the little place at the harbour.",
                "Sounds lovely!",
                turn_id="rt-turn-2",
            )
        )
        deadline = time.monotonic() + 2.0
        while journal.backlog_count() < 2 and time.monotonic() < deadline:  # noqa: ASYNC110
            await asyncio.sleep(0.02)
    finally:
        bridge.stop()

    assert journal.backlog_count() == 2
    assert brain.call_count == 2


@pytest.mark.asyncio
async def test_short_yacht_ownership_reaches_extractor_without_ack(tmp_path: Path) -> None:
    bus, journal, _curator, bridge, brain = _stack(tmp_path)
    try:
        await bus.publish(_realtime_turn("I own a yacht.", "That sounds exciting."))
        await _wait_for_calls(brain, 1)
    finally:
        bridge.stop()

    assert brain.call_count == 1
    assert journal.capture_summary()["candidates"] == 1


@pytest.mark.asyncio
async def test_same_text_in_different_turns_is_reviewed_twice(tmp_path: Path) -> None:
    bus, journal, _curator, bridge, brain = _stack(tmp_path)
    try:
        await bus.publish(_realtime_turn(FACT_SENTENCE, "Okay.", turn_id="turn-a"))
        await bus.publish(_realtime_turn(FACT_SENTENCE, "Okay.", turn_id="turn-b"))
        await _wait_for_calls(brain, 2)
    finally:
        bridge.stop()

    assert brain.call_count == 2
    assert journal.backlog_count() == 2


@pytest.mark.asyncio
async def test_second_turn_receives_bounded_same_session_context(tmp_path: Path) -> None:
    bus, _journal, _curator, bridge, brain = _stack(tmp_path)
    try:
        await bus.publish(
            _realtime_turn(
                "I own a yacht called Aurora.",
                "Aurora is a beautiful name.",
                turn_id="turn-a",
            )
        )
        await _wait_for_calls(brain, 1)
        await bus.publish(
            _realtime_turn(
                "It is currently moored in Kiel.",
                "Understood.",
                turn_id="turn-b",
            )
        )
        await _wait_for_calls(brain, 2)
    finally:
        bridge.stop()

    prompt = brain.received_requests[1].messages[0].content
    assert "USER TURN [turn-a]" in prompt
    assert "I own a yacht called Aurora." in prompt
    assert "FOCUS USER TURN [turn-b]" in prompt
    assert "ASSISTANT CONTEXT (never evidence)" in prompt


@pytest.mark.asyncio
async def test_session_end_runs_one_full_realtime_sweep(tmp_path: Path) -> None:
    bus, journal, _curator, bridge, brain = _stack(tmp_path)
    try:
        await bus.publish(_realtime_turn(FACT_SENTENCE, "Okay.", turn_id="turn-a"))
        await _wait_for_calls(brain, 1)
        await bus.publish(
            VoiceSessionEnded(session_id="rt-session", hangup_reason="hotkey")
        )
        await _wait_for_calls(brain, 2)
        deadline = time.monotonic() + 2.0
        while (  # noqa: ASYNC110 - polls a persisted counter with a hard deadline
            journal.capture_summary()["sessions_swept"] < 1
            and time.monotonic() < deadline
        ):
            await asyncio.sleep(0.02)
    finally:
        bridge.stop()

    assert brain.call_count == 2
    assert journal.capture_summary()["sessions_swept"] == 1
