"""Background resolves stay off the realtime voice key (mandate 2026-09-29).

The OpenAI key a user adds for GPT-Live pays for the voice call and its
thinking model. Skill drafting, bios and other background work must reach
that key only when no other cloud family can answer, and a single-key install
must still resolve onto it before an absent local server.
"""

from __future__ import annotations

import pytest

import jarvis.core.config as cfg_mod
from jarvis.brain import resolver
from jarvis.core.config import BrainConfig, BrainTierConfig, JarvisConfig


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
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


def _gpt_live(primary: str, *, mode: str = "realtime") -> JarvisConfig:
    config = JarvisConfig(brain=BrainConfig(primary=primary))
    config.brain.realtime = BrainTierConfig(provider="openai-live", model="gpt-live-1")
    config.voice.mode = mode
    return config


def _providers(config: JarvisConfig) -> list[str]:
    return [provider for provider, _ in resolver._resolve_chain(config)]


def test_voice_key_primary_leaves_the_chain_while_other_families_are_keyed(
    monkeypatch,
) -> None:
    _keys(monkeypatch, "openai", "gemini", "grok")

    providers = _providers(_gpt_live("openai"))

    assert "openai" not in providers
    assert "gemini" in providers
    assert "grok" in providers


def test_voice_key_still_precedes_the_keyless_local_tail(monkeypatch) -> None:
    """A single-key install without a local server keeps a working resolve."""
    _keys(monkeypatch, "openai")

    providers = _providers(_gpt_live("openai"))

    assert providers.index("openai") < providers.index("ollama")
    assert providers[-2:] == ["ollama", "local-openai"]


def test_a_deliberate_local_primary_keeps_the_lead(monkeypatch) -> None:
    _keys(monkeypatch, "openai")

    providers = _providers(_gpt_live("ollama"))

    assert providers[0] == "ollama"


def test_pipeline_voice_leaves_an_openai_primary_first(monkeypatch) -> None:
    _keys(monkeypatch, "openai", "gemini")

    providers = _providers(_gpt_live("openai", mode="pipeline"))

    assert providers[0] == "openai"


def test_codex_without_a_login_is_no_alternative_to_the_voice_key(monkeypatch) -> None:
    """Codex falls back to the same OpenAI key slot when nobody is signed in."""
    _keys(monkeypatch, "openai", "codex")

    providers = _providers(_gpt_live("openai"))

    assert "openai" in providers
    assert providers.index("openai") < providers.index("ollama")
