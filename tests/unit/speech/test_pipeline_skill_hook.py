"""Unit tests for the pipeline pre-brain hook (instruction-skill model).

``SpeechPipeline._try_skill_direct_trigger`` no longer macro-runs a matched
skill and reads raw Markdown aloud (AD-S4, 2026-06-09 rebuild). On a voice
trigger match it notes the skill on the brain (``note_skill_trigger``) and
returns ``False`` so the normal brain turn carries the skill instructions.
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
    """Minimal TTS fake — keeps pipeline init from crashing."""

    name = "fake-tts"
    supports_streaming = False

    def __init__(self) -> None:
        self.spoken: list[tuple[str, str]] = []

    async def synthesize(  # type: ignore[no-untyped-def]
        self, text: str, language_code=None
    ) -> AsyncIterator[bytes]:  # pragma: no cover — not called in these tests
        if False:
            yield b""


class FakeBrain:
    """Callable brain stub recording note_skill_trigger handoffs."""

    def __init__(self) -> None:
        self.noted: list[tuple[str, str, str]] = []

    async def __call__(self, text: str) -> str:  # pragma: no cover
        return "ok"

    def note_skill_trigger(
        self, skill_name: str, *, content: str = "", source: str = "trigger"
    ) -> None:
        self.noted.append((skill_name, content, source))


def _write_skill(root: Path, name: str, body: str) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")


VOICE_SKILL_DE = """---
schema_version: "1"
name: voice_test_skill
description: Test skill for the pipeline hook.
triggers:
  - type: voice
    pattern: "^starte das experiment$"
    language: ["de"]
---
Experiment instructions.
"""

CONTENT_SKILL_DE = """---
schema_version: "1"
name: note_skill
description: Captures trailing content.
triggers:
  - type: voice
    pattern: "^notiere (.+)$"
    language: ["de"]
---
Note: {{ content }}
"""


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    _write_skill(tmp_path, "voice_test_skill", VOICE_SKILL_DE)
    _write_skill(tmp_path, "note_skill", CONTENT_SKILL_DE)
    return tmp_path


@pytest.fixture
def skill_ctx_with_bus(skills_root: Path):
    """Real SkillContext with registry + runner; yields the bus.

    Cleanup after every test — no state leak between tests.
    """
    bus = EventBus()
    registry = SkillRegistry(skills_root, bus=bus)
    registry.reload_sync()
    runner = SkillRunner(registry=registry, bus=bus)
    set_skill_context(SkillContext(registry=registry, runner=runner))
    yield bus
    set_skill_context(None)


@pytest.fixture
def pipeline_with_brain(skill_ctx_with_bus: EventBus):
    bus = skill_ctx_with_bus
    brain = FakeBrain()
    pipeline = SpeechPipeline(
        tts=FakeTTS(), bus=bus, brain_callback=brain, enable_whisper_wake=False
    )

    speak_calls: list[tuple[str, str | None]] = []

    async def fake_speak(text: str, language: str | None = None) -> bool:
        speak_calls.append((text, language))
        return False

    pipeline._speak = fake_speak  # type: ignore[method-assign]
    return pipeline, brain, speak_calls, bus


@pytest.mark.asyncio
async def test_trigger_match_notes_brain_and_returns_false(pipeline_with_brain) -> None:
    """Voice match → brain noted, NO macro run, NO direct TTS, brain path continues."""
    pipeline, brain, speak_calls, bus = pipeline_with_brain

    received: list[SkillDirectTriggered] = []

    async def _capture(event: SkillDirectTriggered) -> None:
        received.append(event)

    bus.subscribe(SkillDirectTriggered, _capture)

    handled = await pipeline._try_skill_direct_trigger("starte das experiment", lang="de")

    assert handled is False  # brain path continues — it carries the skill
    assert brain.noted == [("voice_test_skill", "", "trigger")]
    assert speak_calls == []  # no raw-Markdown read-aloud anymore

    await asyncio.sleep(0.01)
    assert len(received) == 1
    assert received[0].skill_name == "voice_test_skill"
    assert received[0].trigger_type == "voice_direct"


@pytest.mark.asyncio
async def test_trigger_match_captures_content(pipeline_with_brain) -> None:
    pipeline, brain, _speak_calls, _bus = pipeline_with_brain

    await pipeline._try_skill_direct_trigger("notiere milch kaufen", lang="de")

    assert brain.noted == [("note_skill", "milch kaufen", "trigger")]


@pytest.mark.asyncio
async def test_no_match_returns_false_without_noting(pipeline_with_brain) -> None:
    pipeline, brain, speak_calls, _bus = pipeline_with_brain

    handled = await pipeline._try_skill_direct_trigger("komplett anderes zeug", lang="de")

    assert handled is False
    assert brain.noted == []
    assert speak_calls == []


@pytest.mark.asyncio
async def test_no_context_returns_false() -> None:
    set_skill_context(None)
    pipeline = SpeechPipeline(tts=FakeTTS(), bus=None, enable_whisper_wake=False)

    handled = await pipeline._try_skill_direct_trigger("egal was", lang="de")

    assert handled is False


@pytest.mark.asyncio
async def test_trigger_matcher_cached_after_first_match(pipeline_with_brain) -> None:
    """TriggerMatcher is instantiated on first use and cached afterwards."""
    pipeline, _brain, _speak, _bus = pipeline_with_brain

    assert pipeline._trigger_matcher is None
    await pipeline._try_skill_direct_trigger("starte das experiment", lang="de")
    assert pipeline._trigger_matcher is not None
    cached = pipeline._trigger_matcher
    await pipeline._try_skill_direct_trigger("starte das experiment", lang="de")
    assert pipeline._trigger_matcher is cached
