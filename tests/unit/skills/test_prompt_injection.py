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

from jarvis.skills.prompt_injection import render_available_skills_section


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
    line = next(l for l in out.splitlines() if l.startswith("- `huge`"))
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
