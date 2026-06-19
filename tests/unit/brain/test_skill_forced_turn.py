"""Forced-skill turns (AD-S4) and mission-skill dispatch (AD-S5).

A trigger noted via ``note_skill_trigger`` makes the next generate() turn
carry the rendered skill instructions; ``execution: mission`` skills are
dispatched to spawn_worker with the instructions as the brief.
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
from jarvis.skills.runner import SkillRunner
from jarvis.skills.skill_context import SkillContext, set_skill_context


class _FakeSpawnTool:
    name = "spawn_worker"
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
        return ToolResult(success=True, output="Mission started.")


def _write_skill(
    root: Path, name: str, *, body: str, execution: str = "inline",
    pattern: str | None = None,
) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    trigger_block = ""
    if pattern:
        trigger_block = (
            "triggers:\n"
            "  - type: voice\n"
            f'    pattern: "{pattern}"\n'
            "    language: [de, en]\n"
        )
    (d / "SKILL.md").write_text(
        "---\n"
        'schema_version: "1"\n'
        f"name: {name}\n"
        "description: Forced-turn test skill.\n"
        f"execution: {execution}\n"
        f"{trigger_block}"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )


class _ProbeManager(BrainManager):
    """No-LLM manager: one fake chain entry whose brain init fails.

    The fake entry lets generate() run past the empty-chain early exit and
    through the turn-context build (where the skill injection happens, which
    these tests capture); the failing ``_get_brain`` then ends the provider
    loop without any real LLM call.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.captured_turn_contexts: list[str] = []

    def _build_fallback_chain(self, level: Any) -> list:
        return [("fake-provider", "fake-model")]

    def _get_brain(self, prov_name: str, model: str) -> Any:
        raise RuntimeError("no real brain in unit tests")

    def _build_turn_context(self) -> str:
        return ""

    def _render_skill_turn_injection(self, user_text: str) -> str | None:
        block = super()._render_skill_turn_injection(user_text)
        if block is not None:
            self.captured_turn_contexts.append(block)
        return block


def _make_manager(tools: dict[str, Any] | None = None) -> tuple[_ProbeManager, _RecordingExecutor]:
    executor = _RecordingExecutor()
    config = JarvisConfig()
    config.brain.routing.force_spawn_mode = "permissive"
    manager = _ProbeManager(
        config=config,
        bus=EventBus(),
        tools=tools if tools is not None else {"spawn_worker": _FakeSpawnTool()},
        tool_executor=executor,  # type: ignore[arg-type]
    )
    return manager, executor


@pytest.fixture(autouse=True)
def _clean_ctx():
    set_skill_context(None)
    yield
    set_skill_context(None)


def _wire_skills(root: Path) -> None:
    registry = SkillRegistry(root=root)
    registry.reload_sync()
    runner = SkillRunner(registry=registry)
    set_skill_context(SkillContext(registry=registry, runner=runner))


# ----------------------------------------------------------------------
# AD-S4: noted trigger → instructions injected into the turn
# ----------------------------------------------------------------------


async def test_noted_trigger_injects_instructions(tmp_path: Path) -> None:
    _write_skill(tmp_path, "note-skill", body="Note this: {{ content }}")
    _wire_skills(tmp_path)
    m, _executor = _make_manager()

    m.note_skill_trigger("note-skill", content="buy milk", source="trigger")
    await m.generate("notiere buy milk")

    assert len(m.captured_turn_contexts) == 1
    block = m.captured_turn_contexts[0]
    assert "note-skill" in block
    assert "Note this: buy milk" in block
    assert "Follow these skill instructions now" in block


async def test_probe_match_injects_instructions_without_noting(tmp_path: Path) -> None:
    _write_skill(
        tmp_path, "morning-routine",
        body="Do the morning briefing.",
        pattern="(morgenroutine|morning routine)",
    )
    _wire_skills(tmp_path)
    m, _executor = _make_manager()

    await m.generate("starte die morgenroutine")

    assert len(m.captured_turn_contexts) == 1
    assert "Do the morning briefing." in m.captured_turn_contexts[0]


async def test_unresolvable_noted_trigger_is_harmless(tmp_path: Path) -> None:
    _wire_skills(tmp_path)  # empty registry
    m, _executor = _make_manager()

    m.note_skill_trigger("ghost-skill", source="trigger")
    reply = await m.generate("hallo was geht")

    assert m.captured_turn_contexts == []
    assert isinstance(reply, str)


# ----------------------------------------------------------------------
# AD-S5: mission skills dispatch spawn_worker with the brief
# ----------------------------------------------------------------------


async def test_mission_skill_dispatches_worker(tmp_path: Path) -> None:
    _write_skill(
        tmp_path, "heavy-skill",
        body="Long multi-step background job.",
        execution="mission",
        pattern="(heavy job)",
    )
    _wire_skills(tmp_path)
    m, executor = _make_manager()

    reply = await m.generate("run the heavy job now")

    assert reply == "Mission started."
    assert len(executor.calls) == 1
    tool, args, _utt = executor.calls[0]
    assert tool.name == "spawn_worker"
    assert "heavy-skill" in args["utterance"]
    assert "Long multi-step background job." in args["utterance"]
    # No inline injection on a dispatched mission turn.
    assert m.captured_turn_contexts == []


async def test_mission_skill_falls_back_inline_without_spawn_tool(tmp_path: Path) -> None:
    _write_skill(
        tmp_path, "heavy-skill",
        body="Long multi-step background job.",
        execution="mission",
        pattern="(heavy job)",
    )
    _wire_skills(tmp_path)
    m, executor = _make_manager(tools={})  # no spawn_worker registered

    await m.generate("run the heavy job now")

    assert executor.calls == []
    # AD-OE6: no silent drop — inline injection carries the skill instead.
    assert len(m.captured_turn_contexts) == 1
    assert "Long multi-step background job." in m.captured_turn_contexts[0]
