"""Realtime turns reach the wiki journal only when the user asked to keep them.

The realtime engine never emits ``TranscriptFinal``/``MessageSent`` — the one
event that carries BOTH final texts of a realtime turn is
``VoiceTurnCompleted``. These tests pin the bridge's realtime contract since
the automatic review was removed (2026-09-30):

1. A realtime turn whose reply acknowledges a save ("Noted.") is reviewed at
   once (fire-and-forget, AP-9) and its facts are journaled.
2. A turn without an acknowledgement is never reviewed — no per-turn review,
   no end-of-call sweep, no held-back extraction at hangup.
3. Pipeline-tier ``VoiceTurnCompleted`` events are ignored (they are paired
   via TranscriptFinal/ResponseGenerated instead).
4. The same realtime turn delivered twice is reviewed once.
5. Earlier turns of the same call are bounded reference context.
6. When the wiki must wait for the subscription, the explicit save is held,
   never billed to an API key, and retried once a subscription answers.
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
from jarvis.core.events import VoiceSessionEnded, VoiceTurnCompleted
from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.memory.wiki.background_guard import guard
from jarvis.memory.wiki.extractor import ConversationFactExtractor
from jarvis.memory.wiki.journal import CandidateJournal
from jarvis.memory.wiki.voice_bridge import VoiceFactBridge

FACT_SENTENCE = "Remember that my friend Lena moved to Hamburg last month."
SHORT_FACT = "Lena lives in Hamburg."


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
    """Records every provider it is asked to instantiate."""

    def __init__(self, brain: Any, *, providers: set[str] | None = None) -> None:
        self._brain = brain
        self._providers = providers or {"gemini"}
        self.instantiated: list[str] = []

    def available(self) -> set[str]:
        return set(self._providers)

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        self.instantiated.append(name)
        return self._brain


def _config() -> JarvisConfig:
    return JarvisConfig(
        brain=BrainConfig(
            primary="gemini",
            providers={"gemini": BrainProviderConfig(model="gemini-3.1-pro-preview")},
        ),
        memory=MemoryConfig(wiki=WikiMemoryConfig()),
    )


def _stack(tmp_path: Path, *, providers: set[str] | None = None):
    bus = EventBus()
    journal = CandidateJournal(tmp_path / "jarvis.db")
    brain = FakeBrain()
    registry = FakeRegistry(brain, providers=providers)
    extractor = ConversationFactExtractor(
        config=_config(), journal=journal, registry=registry,
    )
    bridge = VoiceFactBridge(bus=bus, extractor=extractor)
    bridge.start()
    return bus, journal, registry, bridge, brain


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


# Hang guard for background work, never a measured latency: a loaded CI runner
# runs this file several times slower than a laptop.
_HANG_GUARD_S = 30.0


async def _drain(bridge: VoiceFactBridge) -> None:
    """Wait for every review the bridge started, however slow the host is.

    Awaiting the bridge's own tasks replaces polling with a short deadline: a
    review that should run is always seen, and one that must not run is caught
    whenever it would have finished.
    """
    async with asyncio.timeout(_HANG_GUARD_S):
        while bridge._inflight:  # noqa: SLF001 - test seam: the bridge's own task set
            await asyncio.gather(*bridge._inflight, return_exceptions=True)  # noqa: SLF001


async def _end_session(bus: EventBus, session_id: str = "rt-session") -> None:
    await bus.publish(
        VoiceSessionEnded(session_id=session_id, hangup_reason="hotkey")
    )


@pytest.mark.asyncio
async def test_acknowledged_realtime_turn_is_reviewed_during_the_call(tmp_path: Path) -> None:
    bus, journal, _registry, bridge, brain = _stack(tmp_path)
    try:
        await bus.publish(_realtime_turn(SHORT_FACT, "Noted."))
        await _drain(bridge)
    finally:
        bridge.stop()

    rows = journal.pending()
    assert rows and rows[0].fact == "Lena moved to Hamburg."
    assert rows[0].source_label.startswith("realtime-fact:")
    assert brain.call_count == 1


@pytest.mark.asyncio
async def test_unacknowledged_realtime_turns_are_never_reviewed(tmp_path: Path) -> None:
    """No per-turn review, no end-of-call sweep, nothing held until hangup."""
    bus, journal, registry, bridge, brain = _stack(tmp_path)
    try:
        await bus.publish(_realtime_turn(FACT_SENTENCE, "Okay, will do!"))
        await bus.publish(_realtime_turn("I own a yacht.", "", turn_id="rt-turn-2"))
        await _end_session(bus)
        await _drain(bridge)
    finally:
        bridge.stop()

    assert brain.call_count == 0
    assert registry.instantiated == []
    assert journal.backlog_count() == 0
    assert journal.capture_summary()["sessions_swept"] == 0


@pytest.mark.asyncio
async def test_session_end_runs_nothing_after_an_acknowledged_turn(tmp_path: Path) -> None:
    bus, journal, _registry, bridge, brain = _stack(tmp_path)
    try:
        await bus.publish(_realtime_turn(FACT_SENTENCE, "Noted.", turn_id="turn-a"))
        await _drain(bridge)
        await _end_session(bus)
        await _end_session(bus)
        await _drain(bridge)
    finally:
        bridge.stop()

    assert brain.call_count == 1, "only the acknowledged turn is reviewed"
    assert journal.capture_summary()["sessions_swept"] == 0


@pytest.mark.asyncio
async def test_pipeline_tier_turn_is_ignored(tmp_path: Path) -> None:
    """Pipeline turns are paired via TranscriptFinal — no double feed."""
    bus, journal, _registry, bridge, brain = _stack(tmp_path)
    try:
        await bus.publish(_realtime_turn(FACT_SENTENCE, "Noted.", tier="flash"))
        await _drain(bridge)
    finally:
        bridge.stop()

    assert journal.backlog_count() == 0
    assert brain.call_count == 0


@pytest.mark.asyncio
async def test_same_realtime_turn_delivered_twice_is_reviewed_once(tmp_path: Path) -> None:
    bus, journal, _registry, bridge, brain = _stack(tmp_path)
    try:
        await bus.publish(_realtime_turn(FACT_SENTENCE, "Noted."))
        await bus.publish(_realtime_turn(FACT_SENTENCE, "Noted."))
        await _drain(bridge)
    finally:
        bridge.stop()

    assert journal.backlog_count() == 1
    assert brain.call_count == 1


@pytest.mark.asyncio
async def test_empty_user_text_is_ignored(tmp_path: Path) -> None:
    bus, journal, _registry, bridge, brain = _stack(tmp_path)
    try:
        await bus.publish(_realtime_turn("", "Noted."))
        await _drain(bridge)
    finally:
        bridge.stop()

    assert journal.backlog_count() == 0
    assert brain.call_count == 0


@pytest.mark.asyncio
async def test_acknowledged_turn_receives_bounded_same_session_context(tmp_path: Path) -> None:
    bus, _journal, _registry, bridge, brain = _stack(tmp_path)
    try:
        await bus.publish(
            _realtime_turn(
                "I own a yacht called Aurora.",
                "Aurora is a beautiful name.",
                turn_id="turn-a",
            )
        )
        await bus.publish(
            _realtime_turn(
                "It is currently moored in Kiel, please remember that.",
                "Noted.",
                turn_id="turn-b",
            )
        )
        await _drain(bridge)
    finally:
        bridge.stop()

    assert brain.call_count == 1
    prompt = brain.received_requests[0].messages[0].content
    assert "USER TURN [turn-a]" in prompt
    assert "I own a yacht called Aurora." in prompt
    assert "FOCUS USER TURN [turn-b]" in prompt
    assert "ASSISTANT CONTEXT (never evidence)" in prompt


@pytest.mark.asyncio
async def test_subscription_wait_holds_the_save_and_never_bills_a_key(
    tmp_path: Path, background_billing,  # noqa: ANN001 - fixture from conftest
) -> None:
    """Subscription mode, subscription signed out: wait, never the key."""
    background_billing.remember_subscription("claude-cli")
    background_billing.sign_out("claude-cli")
    bus, journal, registry, bridge, brain = _stack(
        tmp_path, providers={"gemini", "openrouter", "claude-cli"}
    )
    try:
        await bus.publish(_realtime_turn(SHORT_FACT, "Noted."))
        await _drain(bridge)
        assert bridge.waiting_count == 1
        assert registry.instantiated == [], "no keyed provider may be touched"
        assert brain.call_count == 0

        # A retry while the subscription is still out keeps waiting.
        assert await bridge.retry_waiting() == 0
        assert bridge.waiting_count == 1

        # The subscription answers again and the backoff window has passed.
        background_billing.sign_in("claude-cli")
        guard.note_progress()
        assert await bridge.retry_waiting() == 1
        assert bridge.waiting_count == 0
    finally:
        bridge.stop()

    assert registry.instantiated == ["claude-cli"]
    assert journal.backlog_count() == 1
