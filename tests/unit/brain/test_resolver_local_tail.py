"""Resolver stage 6: keyless local brains close the fallback chain.

Local-first mandate 2026-07-25: a ZERO-key install must land background
resolves (bio/persona/skills) on its own hardware instead of repeat-401ing
on dead cloud defaults — but any funded cloud family stays PREFERRED, so the
local tail comes after the keyed families and never reorders them.
"""

from __future__ import annotations

import pytest

import jarvis.core.config as cfg_mod
from jarvis.brain import resolver
from jarvis.core.config import BrainConfig, BrainProviderConfig, JarvisConfig


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """No real keys, no OAuth rescue, fresh resolver cache."""
    resolver._reset_for_tests()
    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda pid: None)
    import jarvis.brain.manager as manager

    monkeypatch.setattr(manager, "_keyless_provider_is_rescued_by_oauth", lambda pid: False)
    yield
    resolver._reset_for_tests()


def _keys(monkeypatch, *providers: str) -> None:
    monkeypatch.setattr(
        cfg_mod,
        "get_provider_secret",
        lambda pid: "sk-test" if pid in providers else None,
    )


def test_local_tail_closes_the_chain_after_keyed_families(monkeypatch) -> None:
    _keys(monkeypatch, "gemini", "openrouter")
    config = JarvisConfig(brain=BrainConfig(primary="gemini"))

    chain = resolver._resolve_chain(config)
    providers = [p for p, _ in chain]

    assert providers[0] == "gemini"
    # Keyed family the user actually holds comes before any local provider.
    assert providers.index("openrouter") < providers.index("ollama")
    # Both local brains are present, in card order, at the tail.
    assert providers[-2:] == ["ollama", "local-openai"]


def test_local_provider_as_primary_is_not_duplicated_by_the_tail(monkeypatch) -> None:
    config = JarvisConfig(brain=BrainConfig(primary="ollama"))

    chain = resolver._resolve_chain(config)
    providers = [p for p, _ in chain]

    assert providers.count("ollama") == 1
    assert providers[0] == "ollama"
    assert "local-openai" in providers


def test_zero_key_install_still_resolves_onto_local(monkeypatch) -> None:
    """The AP-22 acceptance shape: no key anywhere → the chain still ends on
    reachable local providers instead of only a dead keyless claude-api."""
    config = JarvisConfig()

    chain = resolver._resolve_chain(config)
    providers = [p for p, _ in chain]

    assert "ollama" in providers
    assert "local-openai" in providers


def test_local_tail_honors_user_picked_model(monkeypatch) -> None:
    """The user's [brain.providers.ollama].model pick rides into the tail —
    never overridden by a hardcoded default (AP-21)."""
    config = JarvisConfig(
        brain=BrainConfig(
            primary="gemini",
            providers={"ollama": BrainProviderConfig(model="qwen3.5:9b")},
        )
    )
    _keys(monkeypatch, "gemini")

    chain = resolver._resolve_chain(config)
    by_provider = dict(chain)

    assert by_provider["ollama"] == "qwen3.5:9b"


def test_local_fallback_default_is_unchanged() -> None:
    """Existing installs keep their configured local_fallback default — the
    local tail is ADDITIVE, never a silent default flip."""
    assert JarvisConfig().brain.local_fallback == "claude-api"
