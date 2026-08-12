"""The relevance layer wired into BrainManager — additive, guarded, reversible.

The whole claim of this change is that it ADDS a channel without touching the
one that already works, and that a match inferred by a scorer has strictly
fewer rights than a match an author asked for. These tests are that claim,
written as assertions.

The prompt-caching test at the bottom is the quiet one that matters most: the
narrowed candidate hint must ride the per-turn context and never the cached
system prefix, or the change would break caching on every turn and cost far
more than it saves.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import ToolResult
from jarvis.skills import match_log, relevance
from jarvis.skills.registry import SkillRegistry
from jarvis.skills.skill_context import SkillContext, set_skill_context


class _FakeSpawnTool:
    name = "spawn_worker"
    schema: dict[str, Any] = {}


class _FakeRunSkillTool:
    name = "run-skill"
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


def _write_skill(
    root: Path,
    name: str,
    *,
    pattern: str | None = None,
    description: str = "Demo skill for relevance-routing tests.",
    tags: list[str] | None = None,
    state: str | None = None,
    tier: str | None = None,
    execution: str | None = None,
    requires_tools: list[str] | None = None,
    auto_fire: str | None = None,
) -> None:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        'schema_version: "1"',
        f"name: {name}",
        f"description: {description}",
    ]
    if tags:
        lines.append(f"tags: [{', '.join(tags)}]")
    if pattern:
        lines += ["triggers:", "  - type: voice", f'    pattern: "{pattern}"']
    if state:
        lines.append(f"state: {state}")
    if tier:
        lines += ["risk_policy:", f"  default_tier: {tier}"]
    if execution:
        lines.append(f"execution: {execution}")
    if requires_tools is not None:
        lines.append(f"requires_tools: [{', '.join(requires_tools)}]")
    if auto_fire:
        lines.append(f"auto_fire: {auto_fire}")
    lines += ["---", "", f"# {name}", "", "Follow the steps.", ""]
    (folder / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")


#: Utterances MEASURED against the small fixture corpus below, not guessed.
#: With seven skills the FIRE cut-off lands near 0.98: a bare
#: "aktiviere den konzentrationsmodus" scores 0.74 (NARROW), while naming
#: both indexed terms scores 1.05 (FIRE). Re-measure with
#: scripts/skill_relevance_calibrate.py if the fixture changes.
FIRE_UTTERANCE = "aktiviere den fokus und den konzentrationsmodus"  # i18n-allow

#: Both terms the FIRE utterance names must be indexed, or it scores as a
#: single-term NARROW hit instead.
FOCUS_TAGS = ["konzentrationsmodus", "fokus"]
NARROW_UTTERANCE = "aktiviere den konzentrationsmodus"  # i18n-allow
#: Matches no skill at all — the "nothing happened" case.
UNRELATED_UTTERANCE = "wie ist das wetter in berlin"  # i18n-allow


def _make_manager(
    *, shadow: bool = False, enabled: bool = True, min_band: str = "fire"
) -> BrainManager:
    config = JarvisConfig()
    config.skills.relevance_enabled = enabled
    config.skills.relevance_shadow = shadow
    config.skills.auto_fire_min_band = min_band
    return BrainManager(
        config=config,
        bus=EventBus(),
        tools={
            "spawn_worker": _FakeSpawnTool(),
            "run-skill": _FakeRunSkillTool(),
        },
        tool_executor=_RecordingExecutor(),  # type: ignore[arg-type]
    )


def _install(root: Path) -> None:
    registry = SkillRegistry(root=root)
    registry.reload_sync()
    set_skill_context(SkillContext(registry=registry, runner=_StubRunner()))  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _clean() -> None:
    relevance.clear_index_cache()
    match_log.clear()
    set_skill_context(None)
    yield
    set_skill_context(None)
    relevance.clear_index_cache()
    match_log.clear()


# ---------------------------------------------------------------------------
# The trigger channel keeps absolute precedence
# ---------------------------------------------------------------------------


def test_a_trigger_hit_wins_over_a_stronger_relevance_candidate(
    tmp_path: Path,
) -> None:
    """Zero behaviour change for anything that already worked.

    ``fokusmodus`` is the deep-work trigger AND a strong relevance term for the
    decoy, which stuffs it into name, tags and description. The author's regex
    must still win.
    """
    _write_skill(tmp_path, "deep-work-mode", pattern="(fokusmodus)")
    _write_skill(
        tmp_path,
        "fokusmodus-decoy",
        description="fokusmodus fokusmodus fokusmodus",
        tags=["fokusmodus"],
    )
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager()
    matched = manager._match_skill_for_turn("fokusmodus")
    assert matched is not None
    assert matched.name == "deep-work-mode"
    assert manager._skill_relevance is None, "trigger path must not consult the scorer"


def test_the_kill_switch_restores_pre_relevance_behaviour(tmp_path: Path) -> None:
    _write_skill(tmp_path, "focus", tags=FOCUS_TAGS)
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    assert (
        _make_manager(enabled=True)._match_skill_for_turn(FIRE_UTTERANCE)
        is not None
    )
    assert _make_manager(enabled=False)._match_skill_for_turn(FIRE_UTTERANCE) is None


# ---------------------------------------------------------------------------
# The relevance channel captures a paraphrase the anchored trigger cannot
# ---------------------------------------------------------------------------


def test_a_paraphrase_reaches_an_anchored_trigger_skill(tmp_path: Path) -> None:
    """The maintainer's actual case.

    deep-work-mode ships an ANCHORED trigger (``^...$``), so any extra word
    breaks it — "aktiviere bitte den konzentrationsmodus" could never fire.
    """
    _write_skill(
        tmp_path,
        "deep-work-mode",
        pattern="^(fokusmodus|konzentrationsmodus)$",
        tags=FOCUS_TAGS,
    )
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager()

    # The anchored trigger only ever fires on the bare word.
    assert manager._match_skill_for_turn("konzentrationsmodus") is not None
    assert manager._skill_relevance is None, "bare word is a trigger hit"

    # One extra word breaks the anchor — the relevance layer catches it.
    matched = manager._match_skill_for_turn(FIRE_UTTERANCE)
    assert matched is not None
    assert matched.name == "deep-work-mode"
    assert manager._skill_relevance is not None
    assert manager._skill_relevance.source == "relevance"


# ---------------------------------------------------------------------------
# Guards are inherited, not re-implemented
# ---------------------------------------------------------------------------


def test_a_draft_skill_never_fires_however_strongly_it_scores(tmp_path: Path) -> None:
    """AP-15, enforced structurally: a draft is not in the voice index at all."""
    _write_skill(
        tmp_path,
        "secret-draft",
        description="konzentrationsmodus fokus",
        tags=FOCUS_TAGS,
        state="draft",
    )
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager(min_band="narrow")
    assert manager._match_skill_for_turn(NARROW_UTTERANCE) is None


def test_a_block_tier_skill_never_captures_via_relevance(tmp_path: Path) -> None:
    _write_skill(
        tmp_path, "locked", tags=FOCUS_TAGS, tier="block"
    )
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager(min_band="narrow")
    assert manager._match_skill_for_turn(NARROW_UTTERANCE) is None
    assert match_log.recent(1)[0].vetoed_by == "block_tier"


def test_a_definitional_question_is_still_suppressed(tmp_path: Path) -> None:
    """The guard needs the RAW span, which is why evidence is never normalized."""
    _write_skill(tmp_path, "plugin-github", tags=["github"])
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager(min_band="narrow")
    assert manager._match_skill_for_turn("was ist GitHub?") is None
    assert match_log.recent(1)[0].vetoed_by == "definitional_question"


def test_a_mission_skill_never_captures_via_relevance(tmp_path: Path) -> None:
    """cloud-debug's shape: a paraphrase must not start a background worker."""
    _write_skill(
        tmp_path,
        "cloud-debug",
        description="Dispatch a bug hunt to the background worker.",
        tags=["debugsprint"],
        execution="mission",
    )
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager(min_band="narrow")
    assert manager._match_skill_for_turn("mach mal einen debugsprint") is None
    assert match_log.recent(1)[0].vetoed_by == "class_dispatching"


def test_an_explicit_opt_out_is_honoured(tmp_path: Path) -> None:
    _write_skill(
        tmp_path, "focus", tags=FOCUS_TAGS, auto_fire="never"
    )
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager(min_band="narrow")
    assert manager._match_skill_for_turn(NARROW_UTTERANCE) is None
    assert match_log.recent(1)[0].vetoed_by == "auto_fire_never"


# ---------------------------------------------------------------------------
# Shadow mode
# ---------------------------------------------------------------------------


def test_shadow_mode_records_but_does_not_capture(tmp_path: Path) -> None:
    _write_skill(tmp_path, "focus", tags=FOCUS_TAGS)
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager(shadow=True)
    assert manager._match_skill_for_turn(FIRE_UTTERANCE) is None

    entry = match_log.recent(1)[0]
    assert entry.shadow is True
    assert entry.vetoed_by == "shadow_mode"
    assert entry.winner == "focus", "the decision itself is still recorded"
    # The narrowed hint must still ship — that is what makes shadow mode useful
    # rather than merely inert.
    assert manager._skill_relevance is not None


# ---------------------------------------------------------------------------
# Stand-down rights
# ---------------------------------------------------------------------------


def test_a_tool_backed_relevance_match_keeps_run_skill_visible(
    tmp_path: Path,
) -> None:
    """Dropping run-skill removes the model's only way to say "wrong skill".

    It may only be dropped where an author asked for the match, or where the
    skill can do nothing but add words.
    """
    _write_skill(
        tmp_path,
        "plugin-gmail",
        tags=["posteingangscheck"],
        requires_tools=["gmail"],
    )
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager(min_band="narrow")
    manager._skill_turn_match = manager._match_skill_for_turn(
        "mach mal einen posteingangscheck"
    )
    assert manager._skill_turn_match is not None
    assert manager._skill_stand_downs_allowed() is False

    manager._skill_injected_inline = True
    tools = manager._drop_run_skill_when_inline_injected(dict(manager._tools))
    assert "run-skill" in tools


def test_an_instruction_only_relevance_match_keeps_the_full_stand_down(
    tmp_path: Path,
) -> None:
    _write_skill(tmp_path, "focus", tags=FOCUS_TAGS, requires_tools=[])
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager()
    manager._skill_turn_match = manager._match_skill_for_turn(FIRE_UTTERANCE)
    assert manager._skill_turn_match is not None
    assert manager._skill_stand_downs_allowed() is True

    manager._skill_injected_inline = True
    tools = manager._drop_run_skill_when_inline_injected(dict(manager._tools))
    assert "run-skill" not in tools


def test_a_trigger_match_keeps_its_historical_rights(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "plugin-gmail",
        pattern="(posteingang)",
        requires_tools=["gmail"],
    )
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager()
    manager._skill_turn_match = manager._match_skill_for_turn(
        "schau mal in den posteingang"
    )
    assert manager._skill_turn_match is not None
    assert manager._skill_stand_downs_allowed() is True


# ---------------------------------------------------------------------------
# NARROW: a suggestion, not a capture
# ---------------------------------------------------------------------------


def test_narrow_does_not_capture_but_injects_conditional_instructions(
    tmp_path: Path,
) -> None:
    """NARROW_UTTERANCE is measured to land in the NARROW band on this corpus.

    2026-08-12 escalation: a clear, non-dispatching top candidate carries its
    FULL rendered instructions in a conditional frame — 60 name-only hints
    shipped over 14 live days converted to zero run-skill calls, so the fast
    router model needs the instructions in hand, not a tool round trip. The
    decision (follow vs ignore) explicitly stays with the model.
    """
    _write_skill(tmp_path, "focus", tags=FOCUS_TAGS)
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager()
    manager._skill_turn_match = manager._match_skill_for_turn(NARROW_UTTERANCE)

    assert manager._skill_relevance is not None
    assert manager._skill_relevance.band == "narrow"
    assert manager._skill_turn_match is None, "NARROW must never capture by default"

    hint = manager._render_skill_candidate_hint(NARROW_UTTERANCE)
    assert hint is not None
    assert "focus" in hint
    # The stub runner's rendered body rides inline, conditionally framed.
    assert "Do the thing." in hint
    assert "--- skill instructions" in hint
    assert "ignore this entire block" in hint, (
        "the model must know it may ignore this"
    )
    # No capture side effects: run-skill stays visible for this turn.
    assert manager._skill_injected_inline is False


def test_a_dispatching_candidate_gets_a_named_hint_never_instructions(
    tmp_path: Path,
) -> None:
    """A mission skill's body is a process-start directive. Off an INFERRED
    match it may be named, never inlined — the same line may_capture draws.
    """
    _write_skill(
        tmp_path,
        "cloud-debug",
        description="Dispatch a bug hunt to the background worker.",
        tags=["debugsprint"],
        execution="mission",
    )
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager()
    manager._skill_turn_match = manager._match_skill_for_turn(
        "mach mal einen debugsprint"
    )
    assert manager._skill_turn_match is None

    hint = manager._render_skill_candidate_hint("mach mal einen debugsprint")
    assert hint is not None
    assert "cloud-debug" in hint
    assert "ranked suggestions" in hint
    assert "--- skill instructions" not in hint
    assert "Do the thing." not in hint


def test_a_near_tie_gets_a_short_list_not_instructions(tmp_path: Path) -> None:
    """Two near-tied candidates are genuine ambiguity — the model picks from a
    short list; the harness must not inline either one's instructions.
    """
    _write_skill(tmp_path, "focus-a", tags=["konzentrationsmodus"])
    _write_skill(tmp_path, "focus-b", tags=["konzentrationsmodus"])
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager()
    manager._skill_turn_match = manager._match_skill_for_turn(NARROW_UTTERANCE)
    assert manager._skill_turn_match is None

    hint = manager._render_skill_candidate_hint(NARROW_UTTERANCE)
    assert hint is not None
    assert "focus-a" in hint and "focus-b" in hint
    assert "--- skill instructions" not in hint


def test_a_render_failure_falls_back_to_the_named_hint(tmp_path: Path) -> None:
    """A broken Jinja body must degrade to the old hint, never to silence."""

    class _RaisingRunner:
        def render_instructions(
            self, skill: Any, *, args: dict | None = None
        ) -> str:
            raise RuntimeError("template exploded")

    _write_skill(tmp_path, "focus", tags=FOCUS_TAGS)
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    registry = SkillRegistry(root=tmp_path)
    registry.reload_sync()
    set_skill_context(SkillContext(registry=registry, runner=_RaisingRunner()))  # type: ignore[arg-type]

    manager = _make_manager()
    manager._skill_turn_match = manager._match_skill_for_turn(NARROW_UTTERANCE)
    assert manager._skill_turn_match is None

    hint = manager._render_skill_candidate_hint(NARROW_UTTERANCE)
    assert hint is not None
    assert "focus" in hint
    assert "ranked suggestions" in hint
    assert "--- skill instructions" not in hint


def test_the_hint_is_absent_on_a_captured_turn(tmp_path: Path) -> None:
    _write_skill(tmp_path, "focus", tags=FOCUS_TAGS)
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager()
    manager._skill_turn_match = manager._match_skill_for_turn(FIRE_UTTERANCE)
    assert manager._skill_turn_match is not None
    assert manager._render_skill_candidate_hint() is None


# ---------------------------------------------------------------------------
# Prompt caching — the quiet regression guard
# ---------------------------------------------------------------------------


def test_the_cached_system_prefix_is_identical_across_bands(tmp_path: Path) -> None:
    """The narrowed hint must ride the per-turn context, never the prefix.

    Rewriting the cached system prompt per turn would break prompt caching on
    EVERY turn — a far larger cost than anything narrowing could save. This test
    is the only thing standing between that mistake and production.
    """
    _write_skill(tmp_path, "focus", tags=FOCUS_TAGS)
    _write_skill(tmp_path, "plugin-gmail", tags=["inbox"])
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager()

    manager._match_skill_for_turn(UNRELATED_UTTERANCE)
    prefix_none = manager._build_system_prompt()

    manager._match_skill_for_turn("irgendwas mit inbox hier drin")
    prefix_narrow = manager._build_system_prompt()

    manager._match_skill_for_turn(FIRE_UTTERANCE)
    prefix_fire = manager._build_system_prompt()

    assert prefix_none == prefix_narrow == prefix_fire


# ---------------------------------------------------------------------------
# The decision log
# ---------------------------------------------------------------------------


def test_every_evaluation_is_recorded_including_a_total_miss(tmp_path: Path) -> None:
    """"Nothing happened" must leave a trace, or it stays undiagnosable."""
    _write_skill(tmp_path, "focus", tags=FOCUS_TAGS)
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager()
    manager._match_skill_for_turn(UNRELATED_UTTERANCE)
    entries = match_log.recent(5)
    assert entries, "a miss must still be recorded"
    assert entries[0].winner == ""


def test_the_log_keeps_a_readable_preview_but_hashes_for_the_bus(
    tmp_path: Path,
) -> None:
    _write_skill(tmp_path, "focus", tags=FOCUS_TAGS)
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager()
    manager._match_skill_for_turn(FIRE_UTTERANCE)
    entry = match_log.recent(1)[0]
    assert "konzentrationsmodus" in entry.utterance_preview
    assert entry.utterance_hash
    assert len(entry.utterance_hash) == 12


def test_matching_never_raises_without_a_skill_context() -> None:
    """Headless and mock boots have no skill subsystem at all."""
    manager = _make_manager()
    assert manager._match_skill_for_turn(FIRE_UTTERANCE) is None


# ---------------------------------------------------------------------------
# Channel 0: the user names the skill (2026-08-12)
# ---------------------------------------------------------------------------


def test_an_explicitly_named_mission_skill_captures(tmp_path: Path) -> None:
    """Naming a skill is the sanctioned human path to a dispatching skill.

    The relevance channel must never start a background worker
    (class_dispatching veto), but "nutz den Skill cloud-debug" states intent
    verbatim — it captures with trigger-grade rights.
    """
    _write_skill(
        tmp_path,
        "cloud-debug",
        description="Dispatch a bug hunt to the background worker.",
        execution="mission",
    )
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager()
    matched = manager._match_skill_for_turn("nutz den skill cloud-debug")  # i18n-allow
    assert matched is not None
    assert matched.name == "cloud-debug"
    assert manager._skill_relevance is None, "explicit naming has trigger rights"

    entry = match_log.recent(1)[0]
    assert entry.fired is True
    assert entry.source == "trigger"


def test_an_explicitly_named_block_tier_skill_stays_blocked(
    tmp_path: Path,
) -> None:
    """block > everything: even a spoken name does not unlock a blocked skill."""
    _write_skill(tmp_path, "locked", tier="block")
    for filler in range(6):
        _write_skill(tmp_path, f"filler-{filler}", description=f"topic{filler}")
    _install(tmp_path)

    manager = _make_manager()
    assert manager._match_skill_for_turn("use the skill locked") is None
    assert match_log.recent(1)[0].vetoed_by == "block_tier"
