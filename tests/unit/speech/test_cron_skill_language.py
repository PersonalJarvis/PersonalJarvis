"""A scheduled (cron) skill must compose AND announce its result in the
conversation language, not English.

Forensic 2026-06-23 (the screenshot): a German voice chat received an English
"ANNOUNCEMENT" ("Good morning, Chef. Your calendar is clear today...") because
``_handle_cron_skill`` drove the brain with a hardcoded ENGLISH instruction and
published the announcement with no language tag. The brain derives its reply
language from the prompt text, so the instruction itself must be localized; the
announcement is also tagged so the resolver speaks it in the same language.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.core.events import AnnouncementRequested
from jarvis.speech.pipeline import SpeechPipeline


class _FakeBrain:
    def __init__(self, reply_language: str, conversation_language: str) -> None:
        self.reply_language = reply_language
        self.conversation_language = conversation_language
        self.prompt: str | None = None

    def note_skill_trigger(self, name: str, source: str) -> None:  # noqa: D401
        pass

    async def __call__(self, prompt: str) -> str:
        self.prompt = prompt
        return "result text"


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[object] = []

    async def publish(self, event: object) -> None:
        self.published.append(event)


def _pipe(
    reply_language: str, conversation_language: str
) -> tuple[SpeechPipeline, _FakeBrain, _FakeBus]:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    brain = _FakeBrain(reply_language, conversation_language)
    pipe._brain = brain
    pipe._config = SimpleNamespace(brain=SimpleNamespace(reply_language=reply_language))
    bus = _FakeBus()
    pipe._bus = bus

    async def _noop(*a: object, **k: object) -> None:
        pass

    pipe._emit_skill_direct = _noop  # type: ignore[assignment]
    return pipe, brain, bus


def _skill():
    from jarvis.skills.schema import SkillLifecycleState

    return SimpleNamespace(
        name="morning-briefing", state=SkillLifecycleState.ACTIVE, frontmatter=None
    )


@pytest.mark.asyncio
async def test_cron_skill_german_conversation_prompts_and_announces_german() -> None:
    pipe, brain, bus = _pipe(reply_language="auto", conversation_language="de")
    await pipe._handle_cron_skill(_skill())
    # The brain instruction is German (so the model replies in German)...
    assert brain.prompt is not None and "morning-briefing" in brain.prompt
    assert "Anweisungen" in brain.prompt or "Geplanter" in brain.prompt
    # ...and the announcement carries the German tag.
    anns = [e for e in bus.published if isinstance(e, AnnouncementRequested)]
    assert anns and anns[-1].language == "de"


@pytest.mark.asyncio
async def test_cron_skill_english_conversation_prompts_and_announces_english() -> None:
    pipe, brain, bus = _pipe(reply_language="auto", conversation_language="en")
    await pipe._handle_cron_skill(_skill())
    assert brain.prompt is not None and "scheduled run" in brain.prompt.lower()
    anns = [e for e in bus.published if isinstance(e, AnnouncementRequested)]
    assert anns and anns[-1].language == "en"
