"""Verdichter model resolution — the invalid-hardcoded-model 400 fix.

Live bug: "Verdichter brain-call failed: 400 - 'claude-haiku-4-5-20251001 is not
a valid model ID'". The awareness Verdichter config defaults to the Anthropic
provider + a hardcoded Anthropic model id; when the active provider is OpenRouter
(or any non-Claude gateway) that id is rejected. The resolver must redirect the
provider AND pick a model VALID for it (capability/tier-based), never leave the
Anthropic id behind.
"""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.awareness.config import AwarenessVerdichterConfig
from jarvis.brain.factory import _resolve_verdichter_provider_model
from jarvis.brain.manager import get_tier_default_model

_HARDCODED_ANTHROPIC = "claude-haiku-4-5-20251001"


def _brain(primary: str, providers: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(primary=primary, providers=providers or {})


def test_openrouter_without_pinned_model_resolves_a_valid_openrouter_model():
    """The exact live 400: primary=openrouter, NO [brain.providers.openrouter]
    block. The Anthropic id must be replaced by the openrouter fast-tier default
    (a valid, free model), not sent to OpenRouter verbatim."""
    v_cfg = AwarenessVerdichterConfig()  # provider=claude-api, model=<anthropic id>
    provider, model = _resolve_verdichter_provider_model(v_cfg, _brain("openrouter"))
    assert provider == "openrouter"
    assert model != _HARDCODED_ANTHROPIC
    assert model == get_tier_default_model("router", "openrouter")


def test_configured_provider_model_wins():
    """A user-pinned [brain.providers.openrouter].model is used verbatim."""
    v_cfg = AwarenessVerdichterConfig()
    providers = {"openrouter": SimpleNamespace(model="mistralai/mistral-small:free")}
    provider, model = _resolve_verdichter_provider_model(
        v_cfg, _brain("openrouter", providers)
    )
    assert provider == "openrouter"
    assert model == "mistralai/mistral-small:free"


def test_deep_model_used_when_model_missing():
    v_cfg = AwarenessVerdichterConfig()
    providers = {"openrouter": SimpleNamespace(model=None, deep_model="some/deep:free")}
    provider, model = _resolve_verdichter_provider_model(
        v_cfg, _brain("openrouter", providers)
    )
    assert provider == "openrouter"
    assert model == "some/deep:free"


def test_anthropic_user_keeps_defaults():
    """A genuine Anthropic (claude-api) user keeps the legacy default — no
    redirect, no model change."""
    v_cfg = AwarenessVerdichterConfig()
    provider, model = _resolve_verdichter_provider_model(v_cfg, _brain("claude-api"))
    assert provider == "claude-api"
    assert model == _HARDCODED_ANTHROPIC


def test_gemini_primary_resolves_gemini_model():
    v_cfg = AwarenessVerdichterConfig()
    provider, model = _resolve_verdichter_provider_model(v_cfg, _brain("gemini"))
    assert provider == "gemini"
    assert model != _HARDCODED_ANTHROPIC
    assert model == get_tier_default_model("router", "gemini")
