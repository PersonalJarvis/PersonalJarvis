"""Teacher mode: explicit commands; during a lesson Jarvis records, not answers."""

from __future__ import annotations

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainProviderConfig, JarvisConfig
from jarvis.teacher import lesson as lessons
from jarvis.teacher.gate import match_teacher_command
from tests.fixtures.brain.fake_brain import FakeBrain

# Japanese classroom phrases (speech input vocabulary under test).
PLAN = "中学2年の音楽で春の模擬授業を作って。25分"  # i18n-allow
START = "授業を始めて"  # i18n-allow
SUMMARY = "ジャービス、まとめて"  # i18n-allow
END = "授業を終わって"  # i18n-allow
OPINION = "明るいリズムが春っぽいと思います"  # i18n-allow


def test_gate_needs_explicit_phrases() -> None:
    assert match_teacher_command(PLAN, lesson_active=False).kind == "plan"
    assert match_teacher_command(PLAN, lesson_active=False).minutes == 25
    assert match_teacher_command(START, lesson_active=False).kind == "start"
    # "summarize" is an ordinary request outside a lesson.
    assert match_teacher_command(SUMMARY, lesson_active=False) is None
    assert match_teacher_command(SUMMARY, lesson_active=True).kind == "summary"
    assert match_teacher_command(OPINION, lesson_active=True) is None


@pytest.mark.asyncio
async def test_lesson_flow_records_silently_and_writes_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(lessons, "lessons_dir", lambda: tmp_path)
    config = JarvisConfig()
    config.brain.primary = "local-openai"
    config.brain.providers["local-openai"] = BrainProviderConfig(model="m", base_url="http://x")
    manager = BrainManager(config=config, bus=EventBus(), tools={})
    manager._registry._loaded = True
    brain = FakeBrain(text_response="# generated")
    manager._brain_cache[("local-openai", "m")] = brain

    plan_reply = await manager.generate(PLAN, use_history=False)
    assert "# generated" in plan_reply
    assert (await manager.generate(START, use_history=False)) != ""
    assert manager._lesson is not None and manager._lesson.minutes == 25
    assert manager._lesson.is_music

    silent = await manager.generate(OPINION, use_history=False)
    assert silent == "" and manager._last_turn_suppressed is True
    assert manager._lesson.utterances[-1].text == OPINION

    summary = await manager.generate(SUMMARY, use_history=False)
    assert "# generated" in summary
    # The summary prompt carried the recorded opinion and the music clause.
    assert OPINION in str(brain.calls[-1].messages[-1].content)
    assert "MUSIC" in (brain.calls[-1].system or "")

    await manager.generate(END, use_history=False)
    assert manager._lesson is None
    names = sorted(p.name.split("-", 2)[-1] for p in tmp_path.glob("*.md"))
    assert names == ["plan.md", "report.md", "summary-1.md", "transcript.md"]
