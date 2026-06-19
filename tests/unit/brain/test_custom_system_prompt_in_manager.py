"""The custom system prompt must actually reach the brain's system prompt.

Companion to test_custom_system_prompt.py (which locks the persona-loader). This
proves the wiring: ``BrainManager._build_system_prompt`` emits the user's custom
persona when one is set, and the packaged default otherwise — so editing the
Markdown in Settings genuinely changes how the assistant behaves.
"""
from __future__ import annotations

import pytest

import jarvis.core.config as core_config
from jarvis.brain import persona_loader
from jarvis.brain.manager import BrainManager
from jarvis.core.config import load_config


def _manager() -> BrainManager:
    """A BrainManager with __init__ bypassed — only the attrs the prompt needs."""
    m = BrainManager.__new__(BrainManager)
    m._soul = None
    m._user_profile = None
    m._people = None
    m._core_memory = None
    m._awareness_manager = None
    m._system_prompt_extra = "ROUTER DISCIPLINE BLOCK"
    m._wiki_context_suffix = ""
    m._reply_language = "auto"
    cfg = load_config()
    cfg.performance.cache_optimized_prompt = False
    m._config = cfg
    return m


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(core_config, "DATA_DIR", tmp_path)
    return tmp_path


def test_default_persona_block_present_without_custom() -> None:
    prompt = _manager()._build_system_prompt()
    # A signature line from the packaged JARVIS_PERSONA.md persona block.
    assert "Just A Rather Very Intelligent System" in prompt


def test_custom_prompt_replaces_default_persona_block() -> None:
    persona_loader.save_custom_prompt(
        "You are ZORG. Speak only in haiku. Marker: XYZZY-CUSTOM."
    )
    prompt = _manager()._build_system_prompt()
    assert "XYZZY-CUSTOM" in prompt
    # The packaged persona block is no longer injected once a custom one is set.
    assert "Just A Rather Very Intelligent System" not in prompt
