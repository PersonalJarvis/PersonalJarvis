"""The agent-instructions file must actually reach the brain's system prompt.

Companion to test_agent_instructions.py (which locks the file/IO layer). This
proves the wiring: ``BrainManager._build_system_prompt`` injects the current
Jarvis.md state as a distinct, guard-railed block when one is set, and emits an
explicit empty-state block otherwise so old Jarvis.md instructions cannot linger
by imitation.
"""
from __future__ import annotations

import pytest

import jarvis.core.config as core_config
from jarvis.brain import agent_instructions
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


def test_empty_preferences_state_without_a_file() -> None:
    m = _manager()
    prompt = m._build_system_prompt()
    assert "USER PREFERENCES & STANDING INSTRUCTIONS" in prompt
    assert "No active user preferences are currently set" in prompt
    # The filename is brand-derived (wake word "Ruben" → Ruben.md), so derive
    # the expectation the same way — never assert the host's live brand.
    filename = agent_instructions.instructions_filename(m._config)
    assert f"Ignore any earlier {filename} instructions" in prompt


def test_agent_instructions_injected_with_filename_and_guardrail() -> None:
    cfg = load_config()
    agent_instructions.save_agent_instructions(cfg, "Always answer in haiku. Marker: PREF-XYZZY.")
    prompt = _manager()._build_system_prompt()
    assert "PREF-XYZZY" in prompt
    assert "USER PREFERENCES & STANDING INSTRUCTIONS" in prompt
    # The dynamic filename is surfaced so the model knows the provenance.
    assert agent_instructions.instructions_filename(cfg) in prompt
    # The block is framed as preferences that never override safety.
    assert "never override" in prompt.lower()
