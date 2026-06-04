"""Unit-Tests fuer den Pipeline-Pre-Brain-Hook (Skills-Brain-Integration: Phase Skills-1).

Testet ``SpeechPipeline._try_skill_direct_trigger`` — die zentrale Methode
die zwischen STT-Hallucination-Guard und Brain-Call sitzt und bei einem
Voice-Pattern-Match den Skill direkt ausfuehrt (Brain-Bypass).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from jarvis.core.bus import EventBus
from jarvis.skills.registry import SkillRegistry
from jarvis.skills.runner import SkillRunner
from jarvis.skills.schema import SkillDirectTriggered
from jarvis.skills.skill_context import SkillContext, set_skill_context
from jarvis.speech.pipeline import SpeechPipeline


class FakeTTS:
    """Minimaler TTS-Fake — nur damit Pipeline-Init nicht crasht."""

    name = "fake-tts"
    supports_streaming = False

    def __init__(self) -> None:
        self.spoken: list[tuple[str, str]] = []

    async def synthesize(  # type: ignore[no-untyped-def]
        self, text: str, language_code=None
    ) -> AsyncIterator[bytes]:  # pragma: no cover — wird im Test nicht aufgerufen
        if False:
            yield b""


def _write_skill(root: Path, name: str, body: str) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")


VOICE_SKILL_DE = """---
schema_version: "1"
name: voice_test_skill
description: Test-Skill fuer Pipeline-Hook
triggers:
  - type: voice
    pattern: "^starte das experiment$"
    language: ["de"]
---
Experiment gestartet.
"""


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    _write_skill(tmp_path, "voice_test_skill", VOICE_SKILL_DE)
    return tmp_path


@pytest.fixture
def skill_ctx_with_bus(skills_root: Path):
    """Setzt einen echten SkillContext mit Registry + Runner und liefert den Bus.

    Cleanup nach jedem Test garantiert: kein State-Leak zwischen Tests.
    """
    bus = EventBus()
    registry = SkillRegistry(skills_root, bus=bus)
    registry.reload_sync()
    runner = SkillRunner(registry=registry, bus=bus)
    set_skill_context(SkillContext(registry=registry, runner=runner))
    yield bus
    set_skill_context(None)


@pytest.fixture
def pipeline_with_mocks(skill_ctx_with_bus: EventBus):
    """Pipeline mit gemockten _speak/_transition fuer Test-Schnelligkeit (kein Audio-I/O)."""
    bus = skill_ctx_with_bus
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=bus, enable_whisper_wake=False)

    speak_calls: list[tuple[str, str | None]] = []
    transition_calls: list[str] = []

    async def fake_speak(text: str, language: str | None = None) -> bool:
        speak_calls.append((text, language))
        return False

    async def fake_transition(state: str) -> None:
        transition_calls.append(state)

    pipeline._speak = fake_speak  # type: ignore[method-assign]
    pipeline._transition = fake_transition  # type: ignore[method-assign]

    return pipeline, speak_calls, transition_calls, bus


@pytest.mark.asyncio
async def test_skill_direct_trigger_matches(pipeline_with_mocks) -> None:
    """Voice-Pattern-Match → Skill laeuft, Speak gerufen, SkillDirectTriggered emittet."""
    pipeline, speak_calls, transition_calls, bus = pipeline_with_mocks

    received: list[SkillDirectTriggered] = []

    async def _capture(event: SkillDirectTriggered) -> None:
        received.append(event)

    bus.subscribe(SkillDirectTriggered, _capture)

    handled = await pipeline._try_skill_direct_trigger("starte das experiment", lang="de")

    assert handled is True
    assert len(speak_calls) == 1
    assert "Experiment gestartet" in speak_calls[0][0]
    assert speak_calls[0][1] == "de"
    # State-Transitions: THINKING (vor Run) → SPEAKING (Output) → LISTENING (Ende)
    assert transition_calls == ["THINKING", "SPEAKING", "LISTENING"]

    # Bus-Event Flush — publish ist async, der Subscribe-Handler ist es auch.
    await asyncio.sleep(0.01)
    assert len(received) == 1
    assert received[0].skill_name == "voice_test_skill"
    assert received[0].trigger_type == "voice_direct"
    assert received[0].source_layer == "speech.pipeline"


@pytest.mark.asyncio
async def test_skill_direct_trigger_no_match(pipeline_with_mocks) -> None:
    """Kein Pattern-Match → returns False, kein Skill-Run, kein Speak/Transition."""
    pipeline, speak_calls, transition_calls, _bus = pipeline_with_mocks

    handled = await pipeline._try_skill_direct_trigger("komplett anderes zeug", lang="de")

    assert handled is False
    assert speak_calls == []
    assert transition_calls == []


@pytest.mark.asyncio
async def test_skill_direct_trigger_no_context() -> None:
    """Kein SkillContext gesetzt → returns False sauber, kein Crash."""
    set_skill_context(None)
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=None, enable_whisper_wake=False)

    handled = await pipeline._try_skill_direct_trigger("egal was", lang="de")

    assert handled is False


@pytest.mark.asyncio
async def test_trigger_matcher_cached_after_first_match(pipeline_with_mocks) -> None:
    """TriggerMatcher wird beim ersten Match instanziiert und gecached.

    Pattern-Cache lebt so fuer die Pipeline-Lebenszeit — verhindert wiederholte
    Regex-Compilation auf jedem Voice-Turn.
    """
    pipeline, _, _, _ = pipeline_with_mocks

    assert pipeline._trigger_matcher is None
    await pipeline._try_skill_direct_trigger("starte das experiment", lang="de")
    assert pipeline._trigger_matcher is not None
    cached = pipeline._trigger_matcher
    await pipeline._try_skill_direct_trigger("starte das experiment", lang="de")
    assert pipeline._trigger_matcher is cached  # gleicher Cache, nicht neu erstellt
