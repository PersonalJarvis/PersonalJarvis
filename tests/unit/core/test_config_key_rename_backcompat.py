"""Back-compat tests for the 2026-06-29 Jarvis-Agents config-key rename.

Verifies that existing installs whose jarvis.toml / env-vars still use the OLD
key names ([brain.sub_jarvis], [harness.openclaw], [sub_agents]) continue to
populate the new fields (brain.worker, harness.jarvis_agent, jarvis_agents)
after the rename.  Also verifies that the NEW key names work.

These tests do NOT touch the real jarvis.toml on disk.
"""
from __future__ import annotations

import os

import pytest

from jarvis.core.config import BrainConfig, HarnessConfig, JarvisConfig


# ---------------------------------------------------------------------------
# 6a — [sub_agents] → [jarvis_agents]
# ---------------------------------------------------------------------------


def test_jarvis_agents_populated_from_old_sub_agents_key() -> None:
    """Old [sub_agents] TOML key populates the new jarvis_agents field."""
    cfg = JarvisConfig(**{"sub_agents": {"output_dir": "old-path"}})
    assert cfg.jarvis_agents is not None


def test_jarvis_agents_populated_from_new_key() -> None:
    """New [jarvis_agents] TOML key populates jarvis_agents field."""
    cfg = JarvisConfig(**{"jarvis_agents": {"output_dir": "new-path"}})
    assert cfg.jarvis_agents is not None


def test_jarvis_agents_default_when_absent() -> None:
    """Absent [jarvis_agents] / [sub_agents] block yields the default."""
    cfg = JarvisConfig()
    assert cfg.jarvis_agents is not None  # default_factory


# ---------------------------------------------------------------------------
# 6b — [harness.openclaw] → [harness.jarvis_agent]
# ---------------------------------------------------------------------------


def test_harness_jarvis_agent_from_old_openclaw_key() -> None:
    """Old [harness.openclaw] TOML key populates the new jarvis_agent field."""
    cfg = HarnessConfig(**{"openclaw": {"version": "1.0.0", "enabled": True}})
    assert cfg.jarvis_agent is not None
    assert cfg.jarvis_agent.version == "1.0.0"
    assert cfg.jarvis_agent.enabled is True


def test_harness_jarvis_agent_from_new_key() -> None:
    """New [harness.jarvis_agent] TOML key populates jarvis_agent field."""
    cfg = HarnessConfig(**{"jarvis_agent": {"version": "2.0.0", "enabled": False}})
    assert cfg.jarvis_agent is not None
    assert cfg.jarvis_agent.version == "2.0.0"


def test_harness_jarvis_agent_none_when_absent() -> None:
    """Absent harness block leaves jarvis_agent as None."""
    cfg = HarnessConfig()
    assert cfg.jarvis_agent is None


# ---------------------------------------------------------------------------
# 6c — [brain.sub_jarvis] → [brain.worker]   (highest risk)
# ---------------------------------------------------------------------------


def test_brain_worker_from_old_sub_jarvis_key() -> None:
    """Old [brain.sub_jarvis] TOML key populates the new brain.worker field."""
    cfg = BrainConfig(
        **{"sub_jarvis": {"provider": "antigravity", "model": "claude-opus-4-8"}}
    )
    assert cfg.worker is not None
    assert cfg.worker.provider == "antigravity"
    assert cfg.worker.model == "claude-opus-4-8"


def test_brain_worker_from_new_key() -> None:
    """New [brain.worker] TOML key populates brain.worker field."""
    cfg = BrainConfig(
        **{"worker": {"provider": "gemini", "model": "gemini-3.5-flash"}}
    )
    assert cfg.worker is not None
    assert cfg.worker.provider == "gemini"
    assert cfg.worker.model == "gemini-3.5-flash"


def test_brain_worker_none_when_absent() -> None:
    """Absent [brain.worker] / [brain.sub_jarvis] block leaves worker as None."""
    cfg = BrainConfig()
    assert cfg.worker is None


def test_brain_worker_fallback_chain_preserved() -> None:
    """Fallback fields on the old block come through intact."""
    cfg = BrainConfig(
        **{
            "sub_jarvis": {
                "provider": "antigravity",
                "model": "claude-opus-4-8",
                "fallback_provider": "gemini",
                "fallback_model": "gemini-3.1-pro-preview",
            }
        }
    )
    assert cfg.worker is not None
    assert cfg.worker.fallback_provider == "gemini"
    assert cfg.worker.fallback_model == "gemini-3.1-pro-preview"


# ---------------------------------------------------------------------------
# Full round-trip via JarvisConfig (as load_config constructs it)
# ---------------------------------------------------------------------------


def test_full_config_old_keys_round_trip() -> None:
    """JarvisConfig(**raw_dict_with_old_keys) populates all three renamed fields."""
    raw = {
        "brain": {
            "sub_jarvis": {
                "provider": "antigravity",
                "model": "claude-opus-4-8",
            }
        },
        "harness": {
            "openclaw": {
                "version": "1.0.0",
                "enabled": False,
            }
        },
        "sub_agents": {},
    }
    cfg = JarvisConfig(**raw)

    # brain.worker (was brain.sub_jarvis)
    assert cfg.brain.worker is not None
    assert cfg.brain.worker.provider == "antigravity"
    assert cfg.brain.worker.model == "claude-opus-4-8"

    # harness.jarvis_agent (was harness.openclaw)
    assert cfg.harness.jarvis_agent is not None
    assert cfg.harness.jarvis_agent.version == "1.0.0"

    # jarvis_agents (was sub_agents)
    assert cfg.jarvis_agents is not None


def test_full_config_new_keys_round_trip() -> None:
    """JarvisConfig(**raw_dict_with_new_keys) also populates all three fields."""
    raw = {
        "brain": {
            "worker": {
                "provider": "gemini",
                "model": "gemini-3.5-flash",
            }
        },
        "harness": {
            "jarvis_agent": {
                "version": "2.0.0",
                "enabled": False,
            }
        },
        "jarvis_agents": {},
    }
    cfg = JarvisConfig(**raw)

    assert cfg.brain.worker is not None
    assert cfg.brain.worker.provider == "gemini"

    assert cfg.harness.jarvis_agent is not None
    assert cfg.harness.jarvis_agent.version == "2.0.0"

    assert cfg.jarvis_agents is not None


# ---------------------------------------------------------------------------
# ENV migration shim (_migrate_worker_env_vars)
# ---------------------------------------------------------------------------


def test_migrate_worker_env_vars_copies_old_to_new(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old JARVIS__BRAIN__SUB_JARVIS__PROVIDER is copied to the new name."""
    from jarvis.core.config import _migrate_worker_env_vars

    monkeypatch.setenv("JARVIS__BRAIN__SUB_JARVIS__PROVIDER", "antigravity")
    monkeypatch.delenv("JARVIS__BRAIN__WORKER__PROVIDER", raising=False)

    _migrate_worker_env_vars()

    assert os.environ["JARVIS__BRAIN__WORKER__PROVIDER"] == "antigravity"


def test_migrate_worker_env_vars_does_not_overwrite_new(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Does not overwrite JARVIS__BRAIN__WORKER__PROVIDER if already set."""
    from jarvis.core.config import _migrate_worker_env_vars

    monkeypatch.setenv("JARVIS__BRAIN__SUB_JARVIS__PROVIDER", "old-value")
    monkeypatch.setenv("JARVIS__BRAIN__WORKER__PROVIDER", "gemini")

    _migrate_worker_env_vars()

    assert os.environ["JARVIS__BRAIN__WORKER__PROVIDER"] == "gemini"  # unchanged


def test_migrate_worker_env_vars_noop_when_old_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-op when neither old nor new is set."""
    from jarvis.core.config import _migrate_worker_env_vars

    monkeypatch.delenv("JARVIS__BRAIN__SUB_JARVIS__PROVIDER", raising=False)
    monkeypatch.delenv("JARVIS__BRAIN__WORKER__PROVIDER", raising=False)

    _migrate_worker_env_vars()  # must not raise

    assert "JARVIS__BRAIN__WORKER__PROVIDER" not in os.environ
