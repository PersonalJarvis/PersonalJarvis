"""Unit tests for BrainManager._build_tool_ack_emitter — the GROUNDED per-tool
acknowledgment on the voice (generate/dispatch) path.

Regression guard for the "plugin call feels like it takes ages" fix: the
deterministic, LLM-free per-tool ack was wired into ``BrainManager`` so a slow
tool turn (e.g. a cold email fetch) is no longer silent from the tool selection
through the readback. Before the fix the ack emitter was only ever built in the
unused ``RouterBrain.handle``; ``BrainManager.generate`` dispatched with none.

The emitter is exercised through a light stub carrying only the attributes it
reads, so we avoid the heavy ``BrainManager`` constructor (mirrors the approach
in tests/integration/test_ack_flow.py).
"""

from __future__ import annotations

import asyncio
import time
import types
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain.ack_generator import _GMAIL_READ_ACK
from jarvis.brain.manager import BrainManager
from jarvis.core.events import AnnouncementRequested


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


def _make_stub(
    *,
    bus: Any | None,
    grounded_tool_ack: bool = True,
    reply_language: str = "auto",
    conversation_language: str = "",
    grounded_ack_min_gap_s: int = 20,
) -> SimpleNamespace:
    """Minimal object exposing exactly what _build_tool_ack_emitter reads."""
    ack_brain = SimpleNamespace(
        grounded_tool_ack=grounded_tool_ack,
        grounded_ack_min_gap_s=grounded_ack_min_gap_s,
    )
    stub = SimpleNamespace(
        _bus=bus,
        _config=SimpleNamespace(ack_brain=ack_brain),
        _reply_language=reply_language,
        _conversation_language=conversation_language,
    )
    stub._build_tool_ack_emitter = types.MethodType(BrainManager._build_tool_ack_emitter, stub)
    return stub


def _preambles(bus: _RecordingBus) -> list[AnnouncementRequested]:
    return [e for e in bus.events if isinstance(e, AnnouncementRequested) and e.kind == "preamble"]


@pytest.mark.asyncio
async def test_grounded_gmail_ack_is_published() -> None:
    """A gmail tool selection publishes the specific grounded ack on the
    ``brain.router.ack`` layer as a preamble (so the pipeline staleness guards
    apply)."""
    bus = _RecordingBus()
    stub = _make_stub(bus=bus)
    emit = stub._build_tool_ack_emitter("Schau mal in meine Mails")
    assert emit is not None

    await emit("gmail", {"action": "list_messages"})

    preambles = _preambles(bus)
    assert len(preambles) == 1
    ann = preambles[0]
    assert ann.text in _GMAIL_READ_ACK["de"]
    assert ann.source_layer == "brain.router.ack"
    assert ann.language == "de"


@pytest.mark.asyncio
async def test_toggle_off_returns_none() -> None:
    """`[ack_brain].grounded_tool_ack = false` disables the emitter entirely."""
    bus = _RecordingBus()
    stub = _make_stub(bus=bus, grounded_tool_ack=False)
    assert stub._build_tool_ack_emitter("Schau in meine Mails") is None


@pytest.mark.asyncio
async def test_no_bus_returns_none() -> None:
    stub = _make_stub(bus=None)
    assert stub._build_tool_ack_emitter("Schau in meine Mails") is None


@pytest.mark.asyncio
async def test_voice_control_utterance_returns_none() -> None:
    """ "sei still" is a voice-control command — the action is the confirmation,
    so no ack emitter is built."""
    bus = _RecordingBus()
    stub = _make_stub(bus=bus)
    assert stub._build_tool_ack_emitter("sei still bitte") is None


@pytest.mark.asyncio
async def test_skip_list_tool_publishes_nothing() -> None:
    """A passive-read / UI-microevent tool (ACK_SKIP_TOOLS) yields no ack even
    though the emitter exists."""
    bus = _RecordingBus()
    stub = _make_stub(bus=bus)
    emit = stub._build_tool_ack_emitter("Klick da drauf")
    assert emit is not None

    await emit("click", {})

    assert _preambles(bus) == []


@pytest.mark.asyncio
async def test_fires_at_most_once_across_retries() -> None:
    """The emitter is built once per turn; a provider-chain retry that re-runs
    the tool loop must not double-announce."""
    bus = _RecordingBus()
    stub = _make_stub(bus=bus)
    emit = stub._build_tool_ack_emitter("Schau mal in meine Mails")
    assert emit is not None

    await emit("gmail", {"action": "list_messages"})
    await emit("gmail", {"action": "list_messages"})

    assert len(_preambles(bus)) == 1


@pytest.mark.asyncio
async def test_spanish_pin_keeps_ack_spanish() -> None:
    """With the reply-language pinned to Spanish the ack stays Spanish — it must
    NOT collapse to German (runtime-output-language doctrine)."""
    bus = _RecordingBus()
    stub = _make_stub(bus=bus, reply_language="es")
    emit = stub._build_tool_ack_emitter("mira mi correo")
    assert emit is not None

    await emit("gmail", {"action": "list_messages"})

    preambles = _preambles(bus)
    assert len(preambles) == 1
    assert preambles[0].text in _GMAIL_READ_ACK["es"]
    assert preambles[0].language == "es"


@pytest.mark.asyncio
async def test_min_gap_suppresses_next_turns_ack() -> None:
    """2026-07-06 redesign: a SECOND turn's grounded ack within the min gap is
    suppressed — the forensic bug was the identical ack once per utterance,
    three utterances in a row."""
    bus = _RecordingBus()
    stub = _make_stub(bus=bus)

    emit_turn_1 = stub._build_tool_ack_emitter("Was steht in meinen Mails")
    assert emit_turn_1 is not None
    await emit_turn_1("gmail", {"action": "list_messages"})

    emit_turn_2 = stub._build_tool_ack_emitter("Und im Kalender?")
    assert emit_turn_2 is not None
    await emit_turn_2("google_calendar", {})

    assert len(_preambles(bus)) == 1


@pytest.mark.asyncio
async def test_min_gap_zero_restores_legacy_per_turn_acks() -> None:
    bus = _RecordingBus()
    stub = _make_stub(bus=bus, grounded_ack_min_gap_s=0)

    emit_turn_1 = stub._build_tool_ack_emitter("Was steht in meinen Mails")
    await emit_turn_1("gmail", {"action": "list_messages"})
    emit_turn_2 = stub._build_tool_ack_emitter("Und im Kalender?")
    await emit_turn_2("google_calendar", {})

    assert len(_preambles(bus)) == 2


@pytest.mark.asyncio
async def test_ack_returns_after_the_gap_elapses() -> None:
    bus = _RecordingBus()
    stub = _make_stub(bus=bus)

    emit_turn_1 = stub._build_tool_ack_emitter("Was steht in meinen Mails")
    await emit_turn_1("gmail", {"action": "list_messages"})
    # Simulate the gap having elapsed instead of sleeping 20 s.
    stub._last_grounded_ack_monotonic = time.monotonic() - 21.0

    emit_turn_2 = stub._build_tool_ack_emitter("Und im Kalender?")
    await emit_turn_2("google_calendar", {})

    assert len(_preambles(bus)) == 2


@pytest.mark.asyncio
async def test_skip_tool_does_not_arm_the_gap() -> None:
    """A skip-list tool publishes nothing — it must not start the cooldown
    (nothing was spoken, so there is nothing to avoid repeating)."""
    bus = _RecordingBus()
    stub = _make_stub(bus=bus)

    emit_turn_1 = stub._build_tool_ack_emitter("Klick da drauf")
    await emit_turn_1("click", {})
    assert _preambles(bus) == []

    emit_turn_2 = stub._build_tool_ack_emitter("Was steht in meinen Mails")
    await emit_turn_2("gmail", {"action": "list_messages"})
    assert len(_preambles(bus)) == 1


# ---------------------------------------------------------------------------
# Contextual Interim Voice (v2 spec) — LLM-composed line via ReadbackComposer
# ---------------------------------------------------------------------------

class _FakeComposer:
    """Duck-typed ReadbackComposer: records the compose() call, returns a
    scripted line (or the canned fallback when scripted to fail)."""

    def __init__(self, response: str | None) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []
        self.has_llm = True

    async def compose(
        self,
        *,
        instruction: str,
        language: str,
        canned: Any,
        facts: dict[str, Any] | None = None,
        in_progress: bool = False,
        honesty_bound: bool = False,
        latency_budget_ms: int | None = None,
    ) -> str:
        self.calls.append({
            "instruction": instruction,
            "language": language,
            "facts": facts or {},
            "in_progress": in_progress,
        })
        if self._response is None:
            return (canned() or "").strip()  # composer contract: fall back, never raise
        return self._response


async def _drain_interim_tasks(stub: Any) -> None:
    tasks = getattr(stub, "_interim_ack_tasks", None) or ()
    if tasks:
        await asyncio.gather(*list(tasks))


@pytest.mark.asyncio
async def test_contextual_line_is_composed_from_the_turn() -> None:
    """With a live composer the spoken text is the per-turn composition, and
    the composer receives the user's request + the concrete action as facts."""
    bus = _RecordingBus()
    stub = _make_stub(bus=bus)
    composer = _FakeComposer("Ich grabe gerade durch die GitHub-Profile.")  # i18n-allow: German TTS fixture
    stub._readback_composer = composer

    emit = stub._build_tool_ack_emitter("Wo liegt das PrimeRandat-Repo?")
    await emit("cli_gh", {"command": "gh repo view PrimeRandat"})
    # Fire-and-forget: the publish happens on the detached task, not inline.
    await _drain_interim_tasks(stub)

    preambles = _preambles(bus)
    assert len(preambles) == 1
    assert preambles[0].text == "Ich grabe gerade durch die GitHub-Profile."  # i18n-allow: German TTS fixture
    call = composer.calls[0]
    assert call["in_progress"] is True
    assert "PrimeRandat" in call["facts"]["user_request"]
    assert "GitHub" in call["facts"]["current_action"]


@pytest.mark.asyncio
async def test_emit_does_not_block_on_composition() -> None:
    """The tool-use loop awaits emit(); the flash call must run detached so
    tool execution is never delayed by composition latency."""
    bus = _RecordingBus()
    stub = _make_stub(bus=bus)

    release = asyncio.Event()

    class _SlowComposer(_FakeComposer):
        async def compose(self, **kwargs: Any) -> str:
            await release.wait()
            return "Ich schaue gerade in dein Postfach."  # i18n-allow: German TTS fixture

    stub._readback_composer = _SlowComposer(None)

    emit = stub._build_tool_ack_emitter("Schau mal in meine Mails")
    await emit("gmail", {"action": "list_messages"})  # returns without waiting

    assert _preambles(bus) == [], "emit() blocked on the flash composition"
    release.set()
    await _drain_interim_tasks(stub)
    assert len(_preambles(bus)) == 1


@pytest.mark.asyncio
async def test_composer_failure_falls_back_to_the_pool_line() -> None:
    """A rejected/failed composition degrades to the deterministic pool line
    (keyless-install contract) — never silence, never an exception."""
    bus = _RecordingBus()
    stub = _make_stub(bus=bus)
    stub._readback_composer = _FakeComposer(None)  # scripted: fall back to canned

    emit = stub._build_tool_ack_emitter("Schau mal in meine Mails")
    await emit("gmail", {"action": "list_messages"})
    await _drain_interim_tasks(stub)

    preambles = _preambles(bus)
    assert len(preambles) == 1
    assert preambles[0].text in _GMAIL_READ_ACK["de"]


@pytest.mark.asyncio
async def test_contextual_kill_switch_uses_pool_inline() -> None:
    """[ack_brain].contextual_interim = false: pools only, published inline."""
    bus = _RecordingBus()
    stub = _make_stub(bus=bus)
    stub._config.ack_brain.contextual_interim = False
    composer = _FakeComposer("Nie zu hoeren.")  # i18n-allow: German TTS fixture
    stub._readback_composer = composer

    emit = stub._build_tool_ack_emitter("Schau mal in meine Mails")
    await emit("gmail", {"action": "list_messages"})

    preambles = _preambles(bus)
    assert len(preambles) == 1  # inline — no task drain needed
    assert preambles[0].text in _GMAIL_READ_ACK["de"]
    assert composer.calls == []


@pytest.mark.asyncio
async def test_fallback_only_composer_uses_pool_inline() -> None:
    """A keyless install wires a fallback-only composer (has_llm=False) — the
    deterministic path stays synchronous, no detached task."""
    bus = _RecordingBus()
    stub = _make_stub(bus=bus)
    composer = _FakeComposer("Nie zu hoeren.")  # i18n-allow: German TTS fixture
    composer.has_llm = False
    stub._readback_composer = composer

    emit = stub._build_tool_ack_emitter("Schau mal in meine Mails")
    await emit("gmail", {"action": "list_messages"})

    preambles = _preambles(bus)
    assert len(preambles) == 1
    assert preambles[0].text in _GMAIL_READ_ACK["de"]
    assert composer.calls == []
