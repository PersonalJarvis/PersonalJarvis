"""Skill-Trigger End-to-End - Cron + Voice + Hotkey Konsistenz."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.core.bus import EventBus
from jarvis.skills.registry import SkillRegistry
from jarvis.skills.trigger_matcher import TriggerMatcher

pytestmark = pytest.mark.asyncio


@pytest.fixture
def builtin_root() -> Path:
    return Path(__file__).resolve().parents[2] / "jarvis" / "skills" / "builtin"


async def test_voice_match_de_en(builtin_root: Path) -> None:
    """AC12: named test covers both languages in one test (Review-Warning 3)."""
    bus = EventBus()
    reg = SkillRegistry(builtin_root, bus)
    reg.reload_sync()
    matcher = TriggerMatcher(reg)
    de = matcher.match_voice("guten morgen", lang="de")
    en = matcher.match_voice("good morning", lang="en")
    assert de is not None and de.frontmatter is not None
    assert en is not None and en.frontmatter is not None
    assert de.frontmatter.name == "morning-routine"
    assert en.frontmatter.name == "morning-routine"
    assert de.frontmatter.name == en.frontmatter.name


async def test_cron_scheduler_graceful_without_croniter(
    monkeypatch: pytest.MonkeyPatch, builtin_root: Path
) -> None:
    """AC9: Cron-Scheduler no-ops wenn croniter fehlt."""
    import jarvis.skills.trigger_matcher as tm

    monkeypatch.setattr(tm, "_HAVE_CRONITER", False)
    bus = EventBus()
    reg = SkillRegistry(builtin_root, bus)
    reg.reload_sync()
    matcher = TriggerMatcher(reg)
    stop_event = asyncio.Event()
    stop_event.set()  # sofort stop
    # Must NOT crash
    count = 0
    async for _ in matcher.run_cron_scheduler(stop_event):
        count += 1
    assert count == 0  # no-op wenn croniter fehlt
