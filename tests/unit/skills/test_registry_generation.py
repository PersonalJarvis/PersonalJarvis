"""The registry's monotonic reload counter.

Derived read-only caches (the relevance match index) key on
``(id(registry), generation)`` so a hot reload invalidates them lazily. That
indirection is deliberate: building the index *inside* the reload would put
CPU on the deferred boot scan (AP-26) and thrash while the watchdog fires on
every keystroke in an open SKILL.md.

The counter therefore has exactly one contract — it must change whenever the
skill set might have changed, and never go backwards. A reload that forgets to
bump it serves a stale index forever, which reads as "my edited skill still
doesn't match".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.skills.registry import SkillRegistry

_SKILL = """---
schema_version: "1"
name: {name}
version: "1.0.0"
description: A test skill for the generation counter.
category: general
---

# {name}

Do the thing.
"""


def _write_skill(root: Path, name: str) -> None:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(_SKILL.format(name=name), encoding="utf-8")


def test_generation_starts_at_zero(tmp_path: Path) -> None:
    assert SkillRegistry(tmp_path).generation == 0


def test_sync_reload_bumps_the_generation(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    registry = SkillRegistry(tmp_path)

    registry.reload_sync()
    first = registry.generation
    assert first == 1

    registry.reload_sync()
    assert registry.generation == first + 1


async def test_async_reload_bumps_the_generation(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha")
    registry = SkillRegistry(tmp_path)

    await registry.reload()
    first = registry.generation
    assert first == 1

    await registry.reload()
    assert registry.generation == first + 1


def test_generation_changes_when_a_skill_is_added(tmp_path: Path) -> None:
    """The case a cache actually depends on: new skill, new generation."""
    _write_skill(tmp_path, "alpha")
    registry = SkillRegistry(tmp_path)
    registry.reload_sync()
    before = registry.generation

    _write_skill(tmp_path, "beta")
    registry.reload_sync()

    assert registry.generation != before
    assert {s.name for s in registry.list()} == {"alpha", "beta"}


def test_generation_is_monotonic_and_read_only(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path)
    seen = [registry.generation]
    for _ in range(5):
        registry.reload_sync()
        seen.append(registry.generation)
    assert seen == sorted(seen)
    assert len(set(seen)) == len(seen)

    with pytest.raises(AttributeError):
        registry.generation = 99  # type: ignore[misc]
