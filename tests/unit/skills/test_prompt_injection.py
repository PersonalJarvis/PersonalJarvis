"""Unit tests for ``jarvis.skills.prompt_injection.render_available_skills_section``.

Instruction-skill model (2026-06-09 rebuild, AD-S2 L1): the renderer turns a
SkillRegistry snapshot into a Markdown ``## AVAILABLE SKILLS`` block that the
BrainManager appends to the system prompt. Bullets carry description +
when_to_use, capped at 1536 chars per entry.

These tests use lightweight Fakes (no ``unittest.mock``, per CLAUDE.md
testing convention).
"""
from __future__ import annotations

from dataclasses import dataclass

from jarvis.skills.prompt_injection import (
    render_available_skills_section,
    render_realtime_skills_directive,
    render_skill_candidate_hint,
)


@dataclass
class _FakeFrontmatter:
    """Stand-in for ``SkillFrontmatter`` — description + when_to_use are read."""
    description: str = ""
    when_to_use: str | None = None


@dataclass
class _FakeSkill:
    """Stand-in for ``Skill`` — only ``name`` + ``frontmatter`` are read."""
    name: str
    frontmatter: _FakeFrontmatter | None


class _FakeRegistry:
    """Records which lookup method was called and returns canned skills.

    Mirrors the public surface that the renderer touches:
    ``list_active()``. ``list()`` exists too — assertion #4 verifies the
    renderer does NOT call it (active-only contract).
    """

    def __init__(self, skills: list[_FakeSkill]) -> None:
        self._skills = skills
        self.calls: list[str] = []

    def list_active(self) -> list[_FakeSkill]:
        self.calls.append("list_active")
        return list(self._skills)

    def list(self) -> list[_FakeSkill]:
        self.calls.append("list")
        return list(self._skills)


def test_render_skills_section_empty_registry_returns_none() -> None:
    """Empty ``list_active()`` → renderer returns ``None`` (no empty block)."""
    registry = _FakeRegistry(skills=[])
    assert render_available_skills_section(registry) is None  # type: ignore[arg-type]


def test_render_skills_section_basic_three_skills() -> None:
    """Three active skills produce one bullet each with name + description."""
    registry = _FakeRegistry(skills=[
        _FakeSkill(name="memory-save", frontmatter=_FakeFrontmatter(
            description="Saves a fact to long-term memory.")),
        _FakeSkill(name="morning-routine", frontmatter=_FakeFrontmatter(
            description="Day overview: mail, calendar, weather.")),
        _FakeSkill(name="deep-work-mode", frontmatter=_FakeFrontmatter(
            description="DND, mute Slack, start pomodoro.")),
    ])

    out = render_available_skills_section(registry)  # type: ignore[arg-type]

    assert out is not None
    assert "## AVAILABLE SKILLS" in out
    assert "`run-skill`" in out
    assert "- `memory-save` — Saves a fact to long-term memory." in out
    assert "- `morning-routine` — Day overview: mail, calendar, weather." in out
    assert "- `deep-work-mode` — DND, mute Slack, start pomodoro." in out


def test_render_skills_section_truncates_at_max_skills() -> None:
    """With 25 skills and max_skills=20, output ends with ``… and 5 more``."""
    skills = [
        _FakeSkill(
            name=f"skill-{i:02d}",
            frontmatter=_FakeFrontmatter(description=f"description {i}"),
        )
        for i in range(25)
    ]
    registry = _FakeRegistry(skills=skills)

    out = render_available_skills_section(registry, max_skills=20)  # type: ignore[arg-type]

    assert out is not None
    # First 20 are present, last 5 are NOT enumerated individually.
    assert "- `skill-00` — description 0" in out
    assert "- `skill-19` — description 19" in out
    assert "- `skill-20` — description 20" not in out
    assert "- `skill-24` — description 24" not in out
    # Tail bullet shows the overflow count.
    assert "- … and 5 more" in out


def test_render_skills_section_uses_active_only_via_registry_contract() -> None:
    """Renderer must call ``list_active``, never ``list`` (active-only contract).

    Disabled / draft skills are NOT advertised to the LLM — the registry
    contract guards that, and the renderer must respect it.
    """
    registry = _FakeRegistry(skills=[
        _FakeSkill(name="x", frontmatter=_FakeFrontmatter(description="d")),
    ])

    render_available_skills_section(registry)  # type: ignore[arg-type]

    assert registry.calls == ["list_active"]
    assert "list" not in registry.calls


def test_render_skills_section_handles_missing_description() -> None:
    """A skill with empty/whitespace description gets a ``(no description)`` fallback."""
    registry = _FakeRegistry(skills=[
        _FakeSkill(name="silent", frontmatter=_FakeFrontmatter(description="")),
        _FakeSkill(name="whitespace-only", frontmatter=_FakeFrontmatter(description="   ")),
    ])

    out = render_available_skills_section(registry)  # type: ignore[arg-type]

    assert out is not None
    assert "- `silent` — (no description)" in out
    assert "- `whitespace-only` — (no description)" in out


def test_render_skills_section_skips_skills_with_no_frontmatter() -> None:
    """A skill whose ``frontmatter is None`` (broken/draft) is silently skipped.

    Loader parks parse-error skills in DRAFT with ``frontmatter=None``.
    They must never appear in the prompt — the LLM has no way to call
    them anyway, and presenting them invites hallucinated tool calls.
    """
    registry = _FakeRegistry(skills=[
        _FakeSkill(name="ok-skill", frontmatter=_FakeFrontmatter(description="works")),
        _FakeSkill(name="broken-skill", frontmatter=None),
        _FakeSkill(name="another-ok", frontmatter=_FakeFrontmatter(description="also works")),
    ])

    out = render_available_skills_section(registry)  # type: ignore[arg-type]

    assert out is not None
    assert "ok-skill" in out
    assert "another-ok" in out
    assert "broken-skill" not in out


# ----------------------------------------------------------------------
# Instruction-skill rebuild (AD-S2 L1)
# ----------------------------------------------------------------------


def test_when_to_use_appended() -> None:
    registry = _FakeRegistry(skills=[
        _FakeSkill(name="demo", frontmatter=_FakeFrontmatter(
            description="Does X.", when_to_use="Use when Y.")),
    ])

    out = render_available_skills_section(registry)  # type: ignore[arg-type]

    assert out is not None
    assert "- `demo` — Does X. Use when Y." in out


def test_per_entry_char_cap() -> None:
    registry = _FakeRegistry(skills=[
        _FakeSkill(name="huge", frontmatter=_FakeFrontmatter(description="A" * 3000)),
    ])

    out = render_available_skills_section(registry)  # type: ignore[arg-type]

    assert out is not None
    line = next(ln for ln in out.splitlines() if ln.startswith("- `huge`"))
    # 1536-char cap on description+when_to_use, plus bullet/name overhead.
    assert len(line) <= 1600
    assert line.endswith("…")


def test_framing_mentions_instruction_loading() -> None:
    registry = _FakeRegistry(skills=[
        _FakeSkill(name="demo", frontmatter=_FakeFrontmatter(description="Does X.")),
    ])

    out = render_available_skills_section(registry)  # type: ignore[arg-type]

    assert out is not None
    assert "run-skill" in out
    assert "instructions" in out


def test_framing_is_imperative_and_skill_first() -> None:
    """Claude-Code-parity stance (2026-06-24): the listing must push the brain
    to check skills BEFORE answering/spawning and to prefer calling a skill on
    a loose match — not a passive "when it matches" note. Locks the framing so a
    future edit cannot silently revert to the weak wording that made skills
    never fire.
    """
    registry = _FakeRegistry(skills=[
        _FakeSkill(name="demo", frontmatter=_FakeFrontmatter(description="Does X.")),
    ])

    out = render_available_skills_section(registry)  # type: ignore[arg-type]

    assert out is not None
    lowered = out.lower()
    # Imperative "must call run-skill first", and the before-answer/spawn check.
    assert "must call" in lowered
    assert "before you answer" in lowered
    assert "spawn a worker" in lowered
    # Loose-match stance ("even loosely" / prefer calling when unsure).
    assert "even loosely" in lowered
    assert "prefer calling" in lowered
    # Over-fire guard is present (a topic mention is not a skill match).
    assert "what is gmail" in lowered


# ----------------------------------------------------------------------
# AD-S2 L1: total char budget with least-recently-modified eviction
# ----------------------------------------------------------------------


@dataclass
class _FakeSkillWithMtime(_FakeSkill):
    mtime: float = 0.0


def test_total_budget_drops_least_recently_modified_first() -> None:
    skills = [
        _FakeSkillWithMtime(
            name=f"skill-{i}",
            frontmatter=_FakeFrontmatter(description="D" * 200),
            mtime=float(i),  # skill-0 is the oldest
        )
        for i in range(5)
    ]
    registry = _FakeRegistry(skills=skills)  # type: ignore[arg-type]

    out = render_available_skills_section(
        registry, total_char_budget=700,  # type: ignore[arg-type]
    )

    assert out is not None
    # Newest survive, oldest evicted, overflow tail counts the dropped.
    assert "- `skill-4`" in out
    assert "- `skill-0`" not in out
    assert "more" in out.splitlines()[-4] or "more" in out  # overflow bullet


def test_total_budget_keeps_all_when_under_budget() -> None:
    registry = _FakeRegistry(skills=[
        _FakeSkill(name="a", frontmatter=_FakeFrontmatter(description="short")),
        _FakeSkill(name="b", frontmatter=_FakeFrontmatter(description="short")),
    ])

    out = render_available_skills_section(registry)  # type: ignore[arg-type]

    assert out is not None
    assert "- `a`" in out and "- `b`" in out
    assert "more" not in out


# ----------------------------------------------------------------------
# 2026-08-12 "skills never fire" rework: complete-by-default listing,
# user skills before builtins, and a NAMED overflow tail.
# ----------------------------------------------------------------------


def test_default_cap_covers_full_builtin_install() -> None:
    """With the default cap, a realistic install (25+ skills) renders every
    bullet — no skill is folded into the tail. Live forensic 2026-08-12: the
    old default of 20 hid five of the user's 25 active skills (including
    ``skill-creator``) and the measured model-initiated run-skill rate was 0.
    """
    skills = [
        _FakeSkill(
            name=f"skill-{i:02d}",
            frontmatter=_FakeFrontmatter(description=f"description {i}"),
        )
        for i in range(30)
    ]
    registry = _FakeRegistry(skills=skills)

    out = render_available_skills_section(registry)  # type: ignore[arg-type]

    assert out is not None
    assert "- `skill-29` — description 29" in out
    assert "more" not in out


def test_overflow_tail_names_folded_skills() -> None:
    """A folded skill must stay CALLABLE: the tail bullet enumerates the
    folded names, because run-skill resolves by exact name and an anonymous
    "… and 5 more" hides exactly the string the model would need.
    """
    skills = [
        _FakeSkill(
            name=f"skill-{i:02d}",
            frontmatter=_FakeFrontmatter(description=f"description {i}"),
        )
        for i in range(25)
    ]
    registry = _FakeRegistry(skills=skills)

    out = render_available_skills_section(registry, max_skills=20)  # type: ignore[arg-type]

    assert out is not None
    tail = next(ln for ln in out.splitlines() if ln.startswith("- …"))
    for i in range(20, 25):
        assert f"`skill-{i:02d}`" in tail
    # Folded skills carry no description in the tail — names only.
    assert "description 24" not in out


def test_user_skills_render_before_builtins() -> None:
    """User-authored skills sort before shipped builtins in the listing."""
    registry = _FakeRegistry(skills=[
        # Real builtin name → _is_builtin() classifies it as shipped.
        _FakeSkill(name="morning-routine", frontmatter=_FakeFrontmatter(
            description="builtin briefing")),
        _FakeSkill(name="my-own-skill", frontmatter=_FakeFrontmatter(
            description="user authored")),
    ])

    out = render_available_skills_section(registry)  # type: ignore[arg-type]

    assert out is not None
    assert out.index("- `my-own-skill`") < out.index("- `morning-routine`")


def test_builtins_fold_before_user_skills_on_overflow() -> None:
    """When the cap forces folding, shipped builtins fold first — the user's
    own skills keep their described bullets.
    """
    registry = _FakeRegistry(skills=[
        _FakeSkill(name="morning-routine", frontmatter=_FakeFrontmatter(
            description="builtin briefing")),
        _FakeSkill(name="deep-work-mode", frontmatter=_FakeFrontmatter(
            description="builtin focus")),
        _FakeSkill(name="my-own-skill", frontmatter=_FakeFrontmatter(
            description="user authored")),
    ])

    out = render_available_skills_section(registry, max_skills=2)  # type: ignore[arg-type]

    assert out is not None
    assert "- `my-own-skill` — user authored" in out
    # One builtin survives (cap 2), the other folds into the named tail.
    tail = next(ln for ln in out.splitlines() if ln.startswith("- …"))
    assert "`deep-work-mode`" in tail


def test_budget_eviction_prefers_builtins_over_user_skills() -> None:
    """Char-budget eviction drops a STALE builtin before a user skill, even
    when the user skill is older.
    """
    registry = _FakeRegistry(skills=[
        _FakeSkillWithMtime(
            name="morning-routine",
            frontmatter=_FakeFrontmatter(description="B" * 200),
            mtime=100.0,  # newer than the user skill
        ),
        _FakeSkillWithMtime(
            name="my-own-skill",
            frontmatter=_FakeFrontmatter(description="U" * 200),
            mtime=1.0,  # oldest overall
        ),
    ])

    out = render_available_skills_section(
        registry, total_char_budget=300,  # type: ignore[arg-type]
    )

    assert out is not None
    assert "- `my-own-skill`" in out
    assert "- `morning-routine` — " not in out
    tail = next(ln for ln in out.splitlines() if ln.startswith("- …"))
    assert "`morning-routine`" in tail


# ----------------------------------------------------------------------
# Realtime roster (2026-08-20 skill-blindness fix)
# ----------------------------------------------------------------------


def test_realtime_directive_names_every_installed_skill() -> None:
    """The roster's whole job: the live model can NAME what is installed.

    The realtime instructions carried the ``run-skill`` tool and not one skill
    name, so the model invented the argument from the user's words — "Morning
    Routine" against a registry keyed ``morning-routine`` — and the turn died
    on ``Unknown skill`` three times in a row.
    """
    registry = _FakeRegistry(skills=[
        _FakeSkill(name="morning-routine", frontmatter=_FakeFrontmatter(
            description="Day overview: mail, calendar, weather.")),
        _FakeSkill(name="deep-work-mode", frontmatter=_FakeFrontmatter(
            description="DND, mute Slack, start pomodoro.")),
    ])

    out = render_realtime_skills_directive(registry)  # type: ignore[arg-type]

    assert "morning-routine" in out
    assert "deep-work-mode" in out
    assert "run-skill" in out
    assert registry.calls == ["list_active"]


def test_realtime_directive_is_empty_without_skills() -> None:
    """No invocable skill → no block, so the caller drops it from the prompt."""
    assert render_realtime_skills_directive(_FakeRegistry(skills=[])) == ""  # type: ignore[arg-type]


def test_realtime_directive_drops_descriptions_before_names() -> None:
    """Over budget, every NAME survives and the prose goes.

    A described skill missing from the roster is uncallable; a bare name still
    resolves. So the budget may never be paid for with a name.
    """
    skills = [
        _FakeSkill(name=f"skill-{i:03d}", frontmatter=_FakeFrontmatter(
            description="A deliberately long description " * 6))
        for i in range(40)
    ]
    out = render_realtime_skills_directive(
        _FakeRegistry(skills=skills), budget_chars=1200,  # type: ignore[arg-type]
    )

    for i in range(40):
        assert f"skill-{i:03d}" in out
    assert "A deliberately long description" not in out


def test_realtime_directive_compact_profile_is_names_only() -> None:
    """Small self-hosted brains get the names without paying for the prose."""
    registry = _FakeRegistry(skills=[
        _FakeSkill(name="morning-routine", frontmatter=_FakeFrontmatter(
            description="Day overview: mail, calendar, weather.")),
    ])

    out = render_realtime_skills_directive(registry, compact=True)  # type: ignore[arg-type]

    assert "morning-routine" in out
    assert "Day overview" not in out


def test_realtime_directive_skips_skills_without_frontmatter() -> None:
    """A broken entry is skipped, never crashes the instruction build."""
    registry = _FakeRegistry(skills=[
        _FakeSkill(name="broken", frontmatter=None),
        _FakeSkill(name="morning-routine", frontmatter=_FakeFrontmatter(
            description="Day overview.")),
    ])

    out = render_realtime_skills_directive(registry)  # type: ignore[arg-type]

    assert "morning-routine" in out
    assert "broken" not in out


def test_realtime_directive_carries_the_when_to_use_matching_rule() -> None:
    """``when_to_use`` is the field that says WHEN a skill applies, in the
    words people actually use. The first realtime roster shipped
    description-only at 70 chars and cut it off, leaving the live model a topic
    label where it needed a matching rule."""
    registry = _FakeRegistry(skills=[
        _FakeSkill(name="morning-routine", frontmatter=_FakeFrontmatter(
            description="Delivers the user's spoken morning briefing.",
            when_to_use='Use when the user asks "wie sieht mein Tag aus".')),
    ])

    out = render_realtime_skills_directive(registry)  # type: ignore[arg-type]

    assert "wie sieht mein Tag aus" in out
    assert "MEANING" in out  # the paraphrase instruction


def test_realtime_directive_degrades_rich_then_short_then_names() -> None:
    """Three tiers, richest first, and never below the names."""
    skills = [
        _FakeSkill(name=f"skill-{i:02d}", frontmatter=_FakeFrontmatter(
            description=f"Does thing {i}. " + "padding " * 12,
            when_to_use="Use when " + "more padding " * 12))
        for i in range(12)
    ]
    registry = _FakeRegistry(skills=skills)

    rich = render_realtime_skills_directive(registry, budget_chars=99_000)  # type: ignore[arg-type]
    short = render_realtime_skills_directive(registry, budget_chars=1_800)  # type: ignore[arg-type]
    names = render_realtime_skills_directive(registry, budget_chars=900)  # type: ignore[arg-type]

    assert "Use when" in rich
    assert "Use when" not in short and "Does thing 0" in short
    assert "Does thing 0" not in names
    # The one invariant every tier keeps: no skill loses its name.
    for out in (rich, short, names):
        for i in range(12):
            assert f"skill-{i:02d}" in out


# ----------------------------------------------------------------------
# The shared candidate hint
# ----------------------------------------------------------------------


def test_candidate_hint_names_the_skills_and_leaves_the_choice_open() -> None:
    out = render_skill_candidate_hint(
        [("morning-routine", "Your day at a glance."), ("deep-work-mode", "")]
    )

    assert "`morning-routine` — Your day at a glance." in out
    assert "`deep-work-mode`" in out
    assert "run-skill" in out
    # The safety argument for showing a merely plausible match at all.
    assert "not a verdict" in out


def test_candidate_hint_is_empty_without_candidates() -> None:
    assert render_skill_candidate_hint([]) == ""
