"""The identity card must actually reach the CACHED system-prompt prefix.

Companion to test_identity_card.py (which locks the distillation and the
cache). This proves the wiring: ``BrainManager._build_system_prompt`` emits the
ambient block in BOTH prompt layouts — unlike the per-turn wiki snippets, which
move to the turn context in cache-optimized mode — and emits nothing at all
when there is no profile to speak of.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import jarvis.core.config as core_config
from jarvis.brain import identity_card as ic
from jarvis.brain.manager import BrainManager
from jarvis.core.config import load_config

PROFILE = """# Nova User

## Summary

Ships a voice assistant solo, mostly in the evenings.

## Preferences

- Short answers, no filler
"""


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(core_config, "DATA_DIR", tmp_path)
    ic.reset_identity_card_cache()
    yield tmp_path
    ic.reset_identity_card_cache()


def _manager(tmp_path: Path, *, cache_optimized: bool, profile: str | None) -> BrainManager:
    """A BrainManager with __init__ bypassed — only the attrs the prompt needs."""
    # An arbitrary slug, never the host's configured one — the wiring must not
    # depend on whose machine runs the suite.
    slug = "nova-user"
    vault = tmp_path / "vault"
    if profile is not None:
        page = vault / "entities" / f"{slug}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(profile, encoding="utf-8")

    m = BrainManager.__new__(BrainManager)
    m._soul = None
    m._user_profile = None
    m._people = None
    m._core_memory = None
    m._awareness_manager = None
    m._system_prompt_extra = ""
    m._wiki_context_suffix = ""
    m._reply_language = "auto"
    cfg = load_config()
    cfg.performance.cache_optimized_prompt = cache_optimized
    cfg.wiki_integration.vault_root = vault
    cfg.memory.wiki.session_rollup.user_entity_slug = slug
    m._config = cfg
    return m


@pytest.mark.parametrize("cache_optimized", [False, True])
def test_the_card_rides_the_cached_prefix_in_both_layouts(
    tmp_path: Path, cache_optimized: bool
) -> None:
    manager = _manager(tmp_path, cache_optimized=cache_optimized, profile=PROFILE)

    prompt = manager._build_system_prompt()

    assert "Nova User" in prompt
    assert "Short answers" in prompt
    assert "silence beats a personal fact nobody asked for" in prompt.lower()


def test_the_block_is_byte_stable_across_turns(tmp_path: Path) -> None:
    """A prefix that moved every turn would break the provider prompt cache."""
    manager = _manager(tmp_path, cache_optimized=True, profile=PROFILE)
    assert manager._build_system_prompt() == manager._build_system_prompt()


def test_no_profile_means_no_block(tmp_path: Path) -> None:
    manager = _manager(tmp_path, cache_optimized=True, profile=None)

    prompt = manager._build_system_prompt()

    assert ic.IDENTITY_BLOCK_HEADER not in prompt
    assert "About the user" not in prompt


def test_the_config_switch_removes_the_block(tmp_path: Path) -> None:
    manager = _manager(tmp_path, cache_optimized=True, profile=PROFILE)
    manager._config.wiki_context.identity_card = False

    assert ic.IDENTITY_BLOCK_HEADER not in manager._build_system_prompt()
