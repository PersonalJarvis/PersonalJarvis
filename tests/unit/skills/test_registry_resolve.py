"""Speech-tolerant skill lookup — ``SkillRegistry.resolve``.

Live forensic 2026-08-20: the realtime model called ``run-skill`` with
``skill_name="Morning Routine"`` while the registry key was
``morning-routine``. ``get`` is an exact dict read, so the tool answered
``Unknown skill`` and the user heard "I could not find that skill" on three
consecutive turns — for a skill that was installed, validated and matched
every trigger it declared.

Two naming conventions share one key space: builtins ship kebab-case slugs,
the skill creator writes Title Case display names. ``resolve`` is the bridge
for names that arrive from a model or a human; ``get`` stays exact for callers
that hold a real key.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.skills.registry import SkillRegistry

_SKILL = """\
---
schema_version: "1"
name: {name}
version: "1.0.0"
description: Test skill.
category: productivity
---

# {name}

Do the thing.
"""


def _write(root: Path, slug: str, name: str) -> None:
    folder = root / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(_SKILL.format(name=name), encoding="utf-8")


@pytest.fixture
def registry(tmp_path: Path) -> SkillRegistry:
    _write(tmp_path, "morning-routine", "morning-routine")
    _write(tmp_path, "plugin-gmail", "plugin-gmail")
    _write(tmp_path, "focus", "focus")
    reg = SkillRegistry(tmp_path)
    reg.reload_sync()
    return reg


@pytest.mark.parametrize(
    "spoken",
    [
        "morning-routine",   # the exact key
        "Morning Routine",   # what the realtime model actually sent
        "morning routine",
        "MORNING_ROUTINE",
        "  Morning-Routine  ",
    ],
)
def test_resolve_finds_the_skill_however_the_name_is_written(
    registry: SkillRegistry, spoken: str
) -> None:
    assert registry.resolve(spoken).name == "morning-routine"


def test_resolve_drops_a_namespace_prefix(registry: SkillRegistry) -> None:
    """Nobody asks for "the plugin-gmail skill"."""
    assert registry.resolve("Gmail").name == "plugin-gmail"
    assert registry.resolve("gmail").name == "plugin-gmail"


def test_resolve_raises_for_an_unknown_name(registry: SkillRegistry) -> None:
    with pytest.raises(KeyError):
        registry.resolve("a skill that was never installed")


def test_resolve_rejects_an_empty_name(registry: SkillRegistry) -> None:
    with pytest.raises(KeyError):
        registry.resolve("   ")


def test_resolve_does_not_match_a_mere_prefix(registry: SkillRegistry) -> None:
    """"focus pro" must not land on the skill called "focus"."""
    with pytest.raises(KeyError):
        registry.resolve("focus pro")


def test_get_stays_exact(registry: SkillRegistry) -> None:
    """``get`` must NOT gain the tolerance — a caller holding a real registry
    key has to fail loudly rather than land on a neighbouring skill."""
    assert registry.get("morning-routine").name == "morning-routine"
    with pytest.raises(KeyError):
        registry.get("Morning Routine")


def test_resolve_finds_a_skill_by_its_directory_slug(tmp_path: Path) -> None:
    """The CLI and the UI show the folder name, so that has to resolve too."""
    _write(tmp_path, "morning-routine-2", "Morning Routine 2")
    reg = SkillRegistry(tmp_path)
    reg.reload_sync()

    assert reg.resolve("morning-routine-2").name == "Morning Routine 2"
    assert reg.resolve("Morning Routine 2").name == "Morning Routine 2"


def test_resolve_prefers_the_live_user_skill_over_a_disabled_builtin(
    tmp_path: Path,
) -> None:
    """Voice-authored 'Morning Routine 2' is what the user asked for.

    create-skill suffixes the display name on a collision with the builtin.
    The model still sends 'Morning Routine'. A disabled bundled skill with
    the same tokens must not win, and a trailing 'skill' from the model
    must not miss.
    """
    _write(tmp_path, "morning-routine", "morning-routine")
    folder = tmp_path / "morning-routine"
    text = (folder / "SKILL.md").read_text(encoding="utf-8")
    (folder / "SKILL.md").write_text(
        text.replace(
            "name: morning-routine\n",
            "name: morning-routine\nstate: disabled\n",
        ),
        encoding="utf-8",
    )
    _write(tmp_path, "morning-routine-2", "Morning Routine 2")
    reg = SkillRegistry(tmp_path)
    reg.reload_sync()

    assert reg.resolve("Morning Routine").name == "Morning Routine 2"
    assert reg.resolve("morning routine skill").name == "Morning Routine 2"
    assert reg.resolve("Morning-Routine").name == "Morning Routine 2"


def test_resolve_is_deterministic_under_an_ambiguous_name(tmp_path: Path) -> None:
    """Two skills folding to the same tokens resolve to a NAMED one.

    Asserting only that twenty calls agree proves nothing: Python's dict order
    is stable within a process, so a resolver that iterated ``_skills`` with no
    ordering at all would pass that. The property is that the answer is decided
    by the documented sorted-name rule — "Focus Mode" sorts before "focus-mode"
    (uppercase first), so it wins regardless of insertion order.
    """
    # Written in the reverse of the expected winner, so insertion order and
    # sort order disagree and only the sort can produce the assertion below.
    _write(tmp_path, "b-thing", "focus-mode")
    _write(tmp_path, "a-thing", "Focus Mode")
    reg = SkillRegistry(tmp_path)
    reg.reload_sync()

    assert reg.resolve("focus mode").name == "Focus Mode"

    first = reg.resolve("focus mode").name
    assert all(reg.resolve("focus mode").name == first for _ in range(20))
