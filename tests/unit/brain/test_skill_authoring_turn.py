"""A request to CREATE a skill is never captured by a skill it merely mentions.

Live bug (voice session 2026-08-18 17:51): the German request for a new
"morning routine" skill that ends "… and then a song on YouTube Music" was
captured by ``plugin-youtube_music`` — its brand-only trigger matched the words
INSIDE the description of the skill the user wanted built. The music skill's
instructions were injected, the model burned the tool budget on
``jarvisctl --help`` and the user heard the spoken "that did not work".

The fix is a deterministic channel ahead of the trigger channel
(``jarvis.skills.authoring_request``): an authoring request resolves to the
``skill-creator`` builtin when it is active, and to NO skill otherwise —
never to a connector named inside the request. The same channel recognises
LIFECYCLE requests ("deaktiviere den YouTube-Music-Skill") and lets nobody
capture those either. Force-spawn and the evidence gate stand down on such a
turn; the ``create-skill`` router tool / the skill app-commands own it.
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

LIVE_UTTERANCE = (
    "Ich möchte, dass du mich bitte einen neuen Skill erstellst und zwar morgen "  # i18n-allow: transcript
    "routine, der soll immer unter früh um 6 Uhr ähm ähm getriggert werden, "  # i18n-allow: transcript
    "gesetzt wird jeden Morgen um 6 Uhr, wo all meine E-Mails, Lenya Tickets und "  # i18n-allow: transcript
    "ähm alle wichtigen Kalendereinträge ähm abgespielt werden und dann ein "  # i18n-allow: transcript
    "schönes Lied abgespielt wird mit YouTube Music zum Aufstehen und zwar ein "  # i18n-allow: transcript
    "Klassiker aus den 80er, wie Country Road oder so ETC immer was neues."  # i18n-allow: transcript
)


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.schema: dict[str, Any] = {}


class _RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, dict[str, Any], str]] = []

    async def execute(self, tool: Any, args: dict[str, Any], *, user_utterance: str = "", trace_id: Any = None, **_: Any) -> ToolResult:
        self.calls.append((tool, args, user_utterance))
        return ToolResult(success=True, output="ok")


class _StubRunner:
    def render_instructions(self, skill: Any, *, args: dict | None = None) -> str:
        return f"# {skill.name}\nDo the thing."


def _write_skill(root: Path, name: str, pattern: str, *, category: str = "general") -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\n"
        'schema_version: "1"\n'
        f"name: {name}\n"
        f"description: Demo skill {name}.\n"
        f"category: {category}\n"
        "triggers:\n"
        "  - type: voice\n"
        f"    pattern: '{pattern}'\n"
        "    language: [de, en]\n"
        "---\n"
        "# Demo\nFollow the steps.\n",
        encoding="utf-8",
    )


def _make_manager() -> BrainManager:
    config = JarvisConfig()
    config.brain.routing.force_spawn_mode = "permissive"
    return BrainManager(
        config=config,
        bus=EventBus(),
        tools={
            "spawn_worker": _FakeTool("spawn_worker"),
            "run-skill": _FakeTool("run-skill"),
            "create-skill": _FakeTool("create-skill"),
            "youtube_music": _FakeTool("youtube_music"),
        },
        tool_executor=_RecordingExecutor(),  # type: ignore[arg-type]
    )


def _context(root: Path, *, with_creator: bool) -> None:
    # The music connector's real trigger is brand-only and un-anchored, so it
    # fires on ANY mention of the brand — that is what captured the live turn.
    _write_skill(root, "plugin-youtube_music", "(youtube ?music|yt ?music|ytmusic)", category="media")
    if with_creator:
        # A stand-in for the builtin: name is what the resolver looks up.
        _write_skill(root, "skill-creator", "(cre[a]te|b[u]ild)\\s+a\\s+skill", category="meta")
    registry = SkillRegistry(root=root)
    registry.reload_sync()
    set_skill_context(SkillContext(registry=registry, runner=_StubRunner()))  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _clean_ctx():
    set_skill_context(None)
    yield
    set_skill_context(None)


# ----------------------------------------------------------------------
# Premise control: the brand trigger DOES capture a plain music command
# ----------------------------------------------------------------------


def test_control_music_command_is_captured_by_the_music_skill(tmp_path: Path) -> None:
    _context(tmp_path, with_creator=True)
    m = _make_manager()
    matched = m._match_skill_for_turn("spiel country roads auf youtube music")  # i18n-allow: test input
    assert matched is not None
    assert matched.name == "plugin-youtube_music"
    assert m._skill_meta_turn == ""


# ----------------------------------------------------------------------
# The live turn: authoring beats the brand trigger
# ----------------------------------------------------------------------


def test_live_utterance_resolves_to_skill_creator_not_youtube_music(tmp_path: Path) -> None:
    _context(tmp_path, with_creator=True)
    m = _make_manager()
    matched = m._match_skill_for_turn(LIVE_UTTERANCE)
    assert matched is not None
    assert matched.name == "skill-creator"
    assert m._skill_meta_turn == "authoring"


def test_live_utterance_captures_nobody_when_skill_creator_is_disabled(tmp_path: Path) -> None:
    """The protection does not hinge on the builtin being on: with it gone the
    music skill still may not take the turn — the create-skill tool owns it."""
    _context(tmp_path, with_creator=False)
    m = _make_manager()
    assert m._match_skill_for_turn(LIVE_UTTERANCE) is None
    assert m._skill_meta_turn == "authoring"


def test_authoring_turn_is_flagged_even_without_a_skill_context() -> None:
    m = _make_manager()
    assert m._match_skill_for_turn(LIVE_UTTERANCE) is None
    assert m._skill_meta_turn == "authoring"


# ----------------------------------------------------------------------
# Stand-downs: force-spawn and the evidence gate
# ----------------------------------------------------------------------


def test_force_spawn_stands_down_on_an_authoring_turn(tmp_path: Path) -> None:
    _context(tmp_path, with_creator=False)
    m = _make_manager()
    assert m._should_force_spawn(LIVE_UTTERANCE) is False


def test_evidence_gate_passes_on_an_authoring_turn(tmp_path: Path) -> None:
    _context(tmp_path, with_creator=False)
    m = _make_manager()
    m._skill_turn_match = m._match_skill_for_turn(LIVE_UTTERANCE)
    verdict = m._run_evidence_gate(LIVE_UTTERANCE)
    assert verdict.kind == "pass"


def test_flag_resets_on_the_next_probe(tmp_path: Path) -> None:
    _context(tmp_path, with_creator=True)
    m = _make_manager()
    m._match_skill_for_turn(LIVE_UTTERANCE)
    assert m._skill_meta_turn == "authoring"
    m._match_skill_for_turn("spiel country roads auf youtube music")  # i18n-allow: test input
    assert m._skill_meta_turn == ""


# ----------------------------------------------------------------------
# Lifecycle requests: the brand skill named inside must not RUN
# ----------------------------------------------------------------------


def test_disable_request_is_not_captured_by_the_named_brand_skill(tmp_path: Path) -> None:
    """"deaktiviere den YouTube-Music-Skill" mentions the brand — the music
    skill must not run; skill-disable (app-command) owns the turn."""
    _context(tmp_path, with_creator=True)
    m = _make_manager()
    assert m._match_skill_for_turn("deaktiviere den youtube music skill") is None  # i18n-allow: test input
    assert m._skill_meta_turn == "lifecycle"
    assert m._should_force_spawn("deaktiviere den youtube music skill") is False  # i18n-allow: test input


def test_a_routine_or_automation_request_is_authoring_too(tmp_path: Path) -> None:
    """One synonym away from the live bug: "build me an automation that plays
    YouTube Music on Mondays" must not hand the turn to the music skill."""
    _context(tmp_path, with_creator=True)
    m = _make_manager()
    matched = m._match_skill_for_turn(
        "bau mir eine automatisierung die montags um 9 ein lied auf youtube music spielt"  # i18n-allow: test input
    )
    assert matched is not None
    assert matched.name == "skill-creator"
    assert m._skill_meta_turn == "authoring"
