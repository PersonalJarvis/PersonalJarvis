"""Boot-race fix (AD-S6): the skill context exists from brain build time,
and a missing AVAILABLE SKILLS section warns exactly once instead of
disappearing silently (RC2 of "Jarvis never calls a skill").
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from jarvis.brain.factory import build_default_brain
from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.skills.skill_context import set_skill_context, try_get_skill_context


@pytest.fixture(autouse=True)
def _clean_ctx():
    set_skill_context(None)
    yield
    set_skill_context(None)


def test_factory_sets_skill_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Point the bootstrap at a temp skills dir with one skill.
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        'schema_version: "1"\n'
        "name: demo-skill\n"
        "description: Demo.\n"
        "---\n"
        "Body.\n",
        encoding="utf-8",
    )
    import jarvis.skills.bootstrap as bootstrap_mod

    monkeypatch.setattr(
        bootstrap_mod, "ensure_user_skills_dir", lambda: tmp_path
    )
    # Echo mode: no LLM, no providers — the skill-context block still runs.
    monkeypatch.setenv("JARVIS_BRAIN", "echo")

    assert try_get_skill_context() is None
    build_default_brain(bus=EventBus())

    ctx = try_get_skill_context()
    assert ctx is not None
    assert any(s.name == "demo-skill" for s in ctx.registry.list())


def test_factory_keeps_existing_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An already-set context (e.g. the web server's) is never replaced."""
    sentinel: Any = object()

    class _Ctx:
        registry = None
        runner = None

    ctx = _Ctx()
    ctx.sentinel = sentinel  # type: ignore[attr-defined]
    set_skill_context(ctx)  # type: ignore[arg-type]
    monkeypatch.setenv("JARVIS_BRAIN", "echo")

    build_default_brain(bus=EventBus())

    after = try_get_skill_context()
    assert getattr(after, "sentinel", None) is sentinel


def test_prompt_build_warns_once_when_ctx_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = BrainManager(config=JarvisConfig(), bus=EventBus(), tools={})
    with caplog.at_level(logging.WARNING, logger="jarvis.brain.manager"):
        manager._build_system_prompt()
        manager._build_system_prompt()
    warns = [
        r for r in caplog.records if "skills section omitted" in r.getMessage()
    ]
    assert len(warns) == 1
