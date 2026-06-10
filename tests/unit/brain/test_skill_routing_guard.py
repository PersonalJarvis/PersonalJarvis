"""Skill-aware routing guard (AD-S3): a matching active skill wins over the
force-spawn heuristic, keeps run-skill visible on smalltalk turns, and steers
the brain via a deterministic hint.

Root-cause fix for "Jarvis never calls a skill": action-verb utterances like
"starte die Morgenroutine" used to be force-spawned to a worker before the
brain ever saw the AVAILABLE SKILLS section.

See docs/superpowers/specs/2026-06-09-skill-system-rebuild-design.md.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import ToolResult
from jarvis.skills.registry import SkillRegistry
from jarvis.skills.skill_context import SkillContext, set_skill_context


class _FakeSpawnTool:
    name = "spawn_worker"
    schema: dict[str, Any] = {}


class _FakeRunSkillTool:
    name = "run-skill"
    schema: dict[str, Any] = {}


class _FakeScreenshotTool:
    name = "screenshot"
    schema: dict[str, Any] = {}


class _RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any], str]] = []

    async def execute(
        self,
        tool: Any,
        args: dict[str, Any],
        *,
        user_utterance: str = "",
        trace_id: Any = None,
        **_: Any,
    ) -> ToolResult:
        self.calls.append((tool, args, user_utterance))
        return ToolResult(success=True, output="ok")


class _StubRunner:
    def render_instructions(self, skill: Any, *, args: dict | None = None) -> str:
        return f"# {skill.name}\nDo the thing."


def _write_skill(root: Path, name: str, pattern: str) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\n"
        'schema_version: "1"\n'
        f"name: {name}\n"
        "description: Demo skill for routing-guard tests.\n"
        "triggers:\n"
        "  - type: voice\n"
        f'    pattern: "{pattern}"\n'
        "    language: [de, en]\n"
        "---\n"
        "# Demo\nFollow the steps.\n",
        encoding="utf-8",
    )


def _make_manager() -> BrainManager:
    executor = _RecordingExecutor()
    config = JarvisConfig()
    config.brain.routing.force_spawn_mode = "permissive"
    return BrainManager(
        config=config,
        bus=EventBus(),
        tools={
            "spawn_worker": _FakeSpawnTool(),
            "run-skill": _FakeRunSkillTool(),
            "screenshot": _FakeScreenshotTool(),
        },
        tool_executor=executor,  # type: ignore[arg-type]
    )


@pytest.fixture()
def skill_ctx(tmp_path: Path):
    _write_skill(tmp_path, "morning-routine", "(morgenroutine|morning routine)")
    registry = SkillRegistry(root=tmp_path)
    registry.reload_sync()
    set_skill_context(SkillContext(registry=registry, runner=_StubRunner()))  # type: ignore[arg-type]
    yield
    set_skill_context(None)


@pytest.fixture(autouse=True)
def _clean_ctx():
    set_skill_context(None)
    yield
    set_skill_context(None)


# ----------------------------------------------------------------------
# Premise control: WITHOUT a matching skill the verb heuristic spawns
# ----------------------------------------------------------------------


def test_control_action_verb_spawns_without_skill_match() -> None:
    m = _make_manager()
    # "lies …" is a plain spawn verb that no other fast path intercepts —
    # ("starte X" is grabbed by is_open_app_intent before force-spawn,
    # which is exactly why the skill guard must run early in generate()).
    assert m._should_force_spawn("lies die README und fasse sie zusammen") is True


# ----------------------------------------------------------------------
# AD-S3: skill match blocks force-spawn
# ----------------------------------------------------------------------


def test_skill_match_blocks_force_spawn(tmp_path: Path) -> None:
    _write_skill(tmp_path, "repo-reader", "(lies die readme)")
    registry = SkillRegistry(root=tmp_path)
    registry.reload_sync()
    set_skill_context(SkillContext(registry=registry, runner=_StubRunner()))  # type: ignore[arg-type]
    m = _make_manager()
    assert m._should_force_spawn("lies die README und fasse sie zusammen") is False


def test_non_skill_action_still_spawns(skill_ctx) -> None:
    m = _make_manager()
    assert m._should_force_spawn("baue mir ein neues Feature ins Repo") is True


def test_match_skill_for_turn_returns_skill(skill_ctx) -> None:
    m = _make_manager()
    matched = m._match_skill_for_turn("starte die morgenroutine")
    assert matched is not None
    assert matched.name == "morning-routine"


def test_match_skill_for_turn_none_without_context() -> None:
    m = _make_manager()
    assert m._match_skill_for_turn("starte die morgenroutine") is None


# ----------------------------------------------------------------------
# AD-S3: smalltalk tool override keeps run-skill on a skill-matched turn
# ----------------------------------------------------------------------


def test_smalltalk_override_keeps_run_skill_on_skill_turn(skill_ctx) -> None:
    m = _make_manager()
    m._skill_turn_match = m._match_skill_for_turn("morgenroutine")
    assert m._skill_turn_match is not None
    tools = m._smalltalk_tool_override()
    assert "run-skill" in tools


def test_smalltalk_override_hides_run_skill_without_match() -> None:
    m = _make_manager()
    tools = m._smalltalk_tool_override()
    assert "run-skill" not in tools


# ----------------------------------------------------------------------
# AD-S3: steering hint
# ----------------------------------------------------------------------


def test_turn_hint_names_the_skill(skill_ctx) -> None:
    m = _make_manager()
    m._skill_turn_match = m._match_skill_for_turn("morgenroutine")
    hint = m._render_skill_turn_hint()
    assert hint is not None
    assert "morning-routine" in hint
    assert "run-skill" in hint


def test_turn_hint_none_without_match() -> None:
    m = _make_manager()
    assert m._render_skill_turn_hint() is None


# ----------------------------------------------------------------------
# AD-S3 ordering: a skill match must bypass the local-action fast path
# (is_open_app_intent grabs "starte die Morgenroutine" otherwise)
# ----------------------------------------------------------------------


class _OrderProbeManager(BrainManager):
    """Records whether the local-action fast path ran on this turn."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.local_action_calls: list[str] = []

    async def _run_local_action_fast_path(
        self, user_text: str, *, trace_id: Any = None
    ) -> str | None:
        self.local_action_calls.append(user_text)
        return "LOCAL_ACTION_SENTINEL"

    def _build_fallback_chain(self, level: Any) -> list:
        return []  # force the provider-down exit — no LLM in unit tests


def _make_probe_manager() -> _OrderProbeManager:
    executor = _RecordingExecutor()
    config = JarvisConfig()
    config.brain.routing.force_spawn_mode = "permissive"
    return _OrderProbeManager(
        config=config,
        bus=EventBus(),
        tools={
            "spawn_worker": _FakeSpawnTool(),
            "run-skill": _FakeRunSkillTool(),
        },
        tool_executor=executor,  # type: ignore[arg-type]
    )


async def test_skill_match_skips_local_action_fast_path(skill_ctx) -> None:
    m = _make_probe_manager()
    reply = await m.generate("starte die Morgenroutine")
    assert reply != "LOCAL_ACTION_SENTINEL"
    assert m.local_action_calls == []


async def test_no_skill_match_keeps_local_action_fast_path() -> None:
    m = _make_probe_manager()
    reply = await m.generate("starte die Morgenroutine")
    assert reply == "LOCAL_ACTION_SENTINEL"
    assert m.local_action_calls == ["starte die Morgenroutine"]
