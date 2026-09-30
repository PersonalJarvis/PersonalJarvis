"""A keyless local provider must activate as the Jarvis-Agent without a key (#223)."""

from __future__ import annotations

import pytest

from jarvis.brain import app_control
from jarvis.core.config import JarvisConfig
from jarvis.missions.worker_runtime.provider_map import JARVIS_TO_WORKER_SLUG

# Every keyless provider that can also run as the Jarvis-Agent.
KEYLESS_AGENTS = sorted(app_control.LOCAL_PROVIDERS & set(JARVIS_TO_WORKER_SLUG))


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", KEYLESS_AGENTS)
async def test_local_provider_switches_without_a_saved_key(monkeypatch, provider) -> None:
    assert "ollama" in KEYLESS_AGENTS
    monkeypatch.setattr(app_control.cfg_mod, "get_jarvis_agent_secret", lambda _p: None)
    result = await app_control._switch_subagent(
        provider, cfg=JarvisConfig(), persist=False, old=None
    )
    assert result["ok"], result
    assert result["new_provider"] == provider


@pytest.mark.asyncio
async def test_keyed_provider_without_a_key_is_still_refused(monkeypatch) -> None:
    monkeypatch.setattr(app_control.cfg_mod, "get_jarvis_agent_secret", lambda _p: None)
    result = await app_control._switch_subagent(
        "openai", cfg=JarvisConfig(), persist=False, old=None
    )
    assert not result["ok"]
    assert result["error_kind"] == "missing_credential"
