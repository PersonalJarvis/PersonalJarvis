"""The router brain stays off the realtime voice key (mandate 2026-09-29).

Live forensic: GPT-Live owned the OpenAI key, ``brain.primary`` still said
"openai" (invisible while the Brain tab is hidden in realtime mode), and the
boot override put every text turn — including gpt-5.5-pro deep turns — on
the voice budget until the voice call failed with "no credits".
"""

from __future__ import annotations

from jarvis.brain.factory import _router_provider_override
from jarvis.core.config import BrainConfig, BrainTierConfig, JarvisConfig


def _config(*, primary: str, router: str | None, mode: str = "realtime") -> JarvisConfig:
    config = JarvisConfig(brain=BrainConfig(primary=primary))
    config.brain.router = BrainTierConfig(provider=router) if router else None
    config.brain.realtime = BrainTierConfig(provider="openai-live", model="gpt-live-1")
    config.voice.mode = mode
    return config


def test_voice_key_primary_does_not_override_the_router_provider() -> None:
    assert _router_provider_override(_config(primary="openai", router="grok")) is None


def test_single_key_install_keeps_the_voice_key_router() -> None:
    """No other router provider: the key still carries text turns (AP-22)."""
    assert _router_provider_override(_config(primary="openai", router=None)) == "openai"


def test_router_on_the_voice_key_too_keeps_the_primary_override() -> None:
    assert _router_provider_override(_config(primary="openai", router="codex")) == "openai"


def test_a_primary_off_the_voice_key_still_overrides_the_router() -> None:
    assert _router_provider_override(_config(primary="gemini", router="grok")) == "gemini"


def test_pipeline_voice_keeps_the_ordinary_override() -> None:
    config = _config(primary="openai", router="grok", mode="pipeline")

    assert _router_provider_override(config) == "openai"


def test_same_provider_needs_no_override() -> None:
    assert _router_provider_override(_config(primary="grok", router="grok")) is None
