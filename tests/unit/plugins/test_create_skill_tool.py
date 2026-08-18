"""The ``create-skill`` router tool — the brain writes a new skill by voice.

Locks what the 2026-08-18 17:51 voice turn was missing: a registered,
router-visible, loader-constructible tool that turns "erstell mir einen
neuen Skill …" into a brain-authored SKILL.md draft in ONE call — and that
refuses honestly, writing nothing, when no brain can author it.

Fakes, no ``unittest.mock``: a fake brain manager whose active provider
answers a fixed JSON draft, a real ``SkillRegistry`` on ``tmp_path``.
"""
from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain.factory import ROUTER_TOOLS
from jarvis.core import runtime_refs
from jarvis.core.protocols import BrainDelta, ExecutionContext
from jarvis.plugins.tool.create_skill import CreateSkillTool
from jarvis.skills import prefs
from jarvis.skills.registry import SkillRegistry
from jarvis.skills.schema import SkillLifecycleState
from jarvis.skills.skill_context import SkillContext, set_skill_context

_DRAFT_JSON = """{
  "name": "Morgenroutine",
  "description": "Reads mail, tickets and calendar in the morning, then plays a song on YouTube Music.",
  "category": "productivity",
  "tags": ["morning", "routine", "music"],
  "triggers": [
    {"type": "voice", "pattern": "(morgenroutine|morning routine)", "language": ["de", "en"]},
    {"type": "schedule", "cron": "0 6 * * *"}
  ],
  "requires_tools": [],
  "risk_policy": {"default_tier": "monitor"},
  "body": "Short morning briefing, then music.\\n\\n## Steps\\n\\n1. gmail: unread mail, name at most three.\\n2. linear: summarise the open tickets.\\n3. google_calendar: the important events of the day.\\n4. youtube_music: play an 80s classic like Country Roads, a different one each time.\\n\\n## Answer format\\n\\nOne flowing spoken reply.\\n",
  "assumptions": ["Lenya Tickets = Linear"],
  "test_prompts": ["starte die morgenroutine"]
}"""


class _FakeBrain:
    def __init__(self, text: str | None) -> None:
        self._text = text
        self.calls = 0

    def complete(self, request):  # noqa: ANN001
        self.calls += 1
        if self._text is None:
            raise RuntimeError("No API key found")

        async def _gen():
            yield BrainDelta(content=self._text, finish_reason="stop")

        return _gen()


class _FakeManager:
    """Looks like the live BrainManager to the tool: active provider + tools."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider
        self.active_provider = "vertex"
        self._tools = {
            "gmail": SimpleNamespace(description="Read Gmail."),
            "youtube_music": SimpleNamespace(description="Play music on YouTube Music."),
        }
        self._config = None

    def _get_or_create(self, name: str):  # noqa: ANN202
        return self._provider


class _StubRunner:
    def render_instructions(self, skill: Any, *, args: dict | None = None) -> str:
        return skill.body


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    root.mkdir()
    return root


@pytest.fixture
def registry(skills_root: Path) -> SkillRegistry:
    reg = SkillRegistry(skills_root, bus=None, state_prefs_loader=prefs.load_state_overrides)
    reg.reload_sync()
    set_skill_context(SkillContext(registry=reg, runner=_StubRunner()))  # type: ignore[arg-type]
    yield reg
    set_skill_context(None)


@pytest.fixture(autouse=True)
def _clean_refs():
    runtime_refs._reset_for_tests()
    set_skill_context(None)
    yield
    runtime_refs._reset_for_tests()
    set_skill_context(None)


def _ctx(language: str = "de") -> ExecutionContext:
    from uuid import uuid4

    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="erstell mir einen neuen skill",  # i18n-allow: test input
        config={"output_language": language},
        memory_read=None,
    )


# ----------------------------------------------------------------------
# Wiring: registered, router-visible, loader-constructible
# ----------------------------------------------------------------------


def test_registered_and_router_visible() -> None:
    names = {e.name for e in entry_points(group="jarvis.tool")}
    assert "create-skill" in names
    assert "create-skill" in ROUTER_TOOLS


def test_loader_can_construct_it_without_arguments() -> None:
    """The phantom spawn-skill-author needed a runner the loader could not give;
    this one takes only what factory.py passes (bus, config) — or nothing."""
    tool = CreateSkillTool()
    assert tool.name == "create-skill"
    assert tool.risk_tier == "monitor"
    assert set(tool.schema["required"]) == {"intent", "name", "trigger_phrase", "schedule"}


def test_description_is_disjoint_from_run_skill() -> None:
    desc = CreateSkillTool.description.lower()
    assert "new skill" in desc
    assert "run-skill" in desc  # names the sibling so the model can tell them apart


# ----------------------------------------------------------------------
# The happy path: one call → a draft skill on disk
# ----------------------------------------------------------------------


async def test_writes_a_brain_authored_draft_skill(registry, skills_root) -> None:
    provider = _FakeBrain(_DRAFT_JSON)
    runtime_refs.set_brain_manager(_FakeManager(provider))
    tool = CreateSkillTool()
    result = await tool.execute(
        {
            "intent": "jeden morgen um 6 mails, tickets, kalender vorlesen und dann ein lied auf youtube music",  # i18n-allow: test input
            "name": "Morgenroutine",
            "trigger_phrase": "",
            "schedule": "0 6 * * *",
        },
        _ctx("de"),
    )
    assert result.success, result.error
    out = result.output
    assert out["skill_name"] == "Morgenroutine"
    assert out["state"] == "draft"
    assert out["schedule_crons"] == ["0 6 * * *"]
    assert out["voice_triggers"] == ["(morgenroutine|morning routine)"]
    assert out["ui_section"] == "skills"
    assert any("youtube_music" in step for step in out["steps_preview"])
    assert provider.calls == 1

    skill = registry.get("Morgenroutine")
    assert skill.state == SkillLifecycleState.DRAFT  # AP-15: never auto-active
    on_disk = (skills_root / "morgenroutine" / "SKILL.md").read_text(encoding="utf-8")
    assert "state: draft" in on_disk
    assert "cron: 0 6 * * *" in on_disk


# ----------------------------------------------------------------------
# Honest failures: nothing is written
# ----------------------------------------------------------------------


async def test_no_brain_means_no_skill_and_an_honest_error(registry, skills_root) -> None:
    runtime_refs.set_brain_manager(_FakeManager(_FakeBrain(None)))
    result = await CreateSkillTool().execute(
        {"intent": "pause spotify when I talk", "name": "", "trigger_phrase": "", "schedule": ""},
        _ctx(),
    )
    assert result.success is False
    assert result.error is not None and result.error.startswith("author_unavailable")
    assert not any(skills_root.iterdir())


async def test_missing_intent_is_rejected(registry) -> None:
    result = await CreateSkillTool().execute({"intent": "  ", "name": "", "trigger_phrase": "", "schedule": ""}, _ctx())
    assert result.success is False
    assert result.error is not None and result.error.startswith("invalid_input")


async def test_without_a_skill_context_it_reports_and_writes_nothing() -> None:
    runtime_refs.set_brain_manager(_FakeManager(_FakeBrain(_DRAFT_JSON)))
    result = await CreateSkillTool().execute(
        {"intent": "something", "name": "", "trigger_phrase": "", "schedule": ""}, _ctx()
    )
    assert result.success is False
    assert result.error is not None and result.error.startswith("skills_unavailable")
