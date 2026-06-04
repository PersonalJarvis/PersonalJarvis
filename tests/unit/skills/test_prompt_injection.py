"""Unit tests for ``jarvis.skills.prompt_injection.render_available_skills_section``.

Skills-Brain-Integration (Track B): the renderer turns a SkillRegistry
snapshot into a Markdown ``## AVAILABLE SKILLS`` block that the
BrainManager appends to the system prompt.

These tests use lightweight Fakes (no ``unittest.mock``, per CLAUDE.md
testing convention).
"""
from __future__ import annotations

from dataclasses import dataclass

from jarvis.skills.prompt_injection import render_available_skills_section


@dataclass
class _FakeFrontmatter:
    """Stand-in for ``SkillFrontmatter`` — only ``description`` is read."""
    description: str = ""


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
            description="Speichert einen Fakt im Long-Term-Memory.")),
        _FakeSkill(name="morning-routine", frontmatter=_FakeFrontmatter(
            description="Tagesueberblick: Mail, Kalender, Wetter.")),
        _FakeSkill(name="deep-work-mode", frontmatter=_FakeFrontmatter(
            description="DND, Slack stumm, Pomodoro starten.")),
    ])

    out = render_available_skills_section(registry)  # type: ignore[arg-type]

    assert out is not None
    assert "## AVAILABLE SKILLS" in out
    assert "`run_skill`" in out
    assert "- `memory-save` — Speichert einen Fakt im Long-Term-Memory." in out
    assert "- `morning-routine` — Tagesueberblick: Mail, Kalender, Wetter." in out
    assert "- `deep-work-mode` — DND, Slack stumm, Pomodoro starten." in out


def test_render_skills_section_truncates_at_max_skills() -> None:
    """With 25 skills and max_skills=20, output ends with ``… und 5 weitere``."""
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
    assert "- … und 5 weitere" in out


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
