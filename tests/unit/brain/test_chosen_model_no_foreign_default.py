"""Regression: a provider's missing ``deep_model`` must reuse the user's CHOSEN
model, never a hardcoded foreign-family default.

Live forensic 2026-06-29 (session 18:09:59): the user's brain was OpenRouter with
``model = "nvidia/nemotron-3-ultra-550b-a55b:free"`` — a FREE model that answers
HTTP 200 with the current key. Yet every turn spoke the provider-down apology
("Entschuldige, ich komme gerade nicht an mein Sprachmodell"). Root cause: the
OpenRouter account had hit its per-key spend limit, so PAID models 403'd
("Key limit exceeded (total limit)") while the chosen FREE model kept working.
But the fallback chain never tried the free model for the deep slot:
``_deep_model("openrouter")`` returned the hardcoded ``anthropic/claude-opus-4.8``
default (no ``deep_model`` in jarvis.toml) — a PAID model blocked by the same
limit — so the turn bricked despite a healthy, user-selected model.

A universal gateway (OpenRouter) selected by the user must run the model the user
chose, not silently hijack the turn onto the most expensive Anthropic model.
Provider-agnostic per AP-21/AP-22 and the open-source single-key mandate (§3).
"""
from __future__ import annotations

import logging

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainProviderConfig, JarvisConfig

_FREE = "nvidia/nemotron-3-ultra-550b-a55b:free"


def _manager_with_openrouter_free() -> BrainManager:
    config = JarvisConfig()
    config.brain.primary = "openrouter"
    # The user picked a single free model and set NO separate deep_model — the
    # common case for an OpenRouter-only downloader.
    config.brain.providers["openrouter"] = BrainProviderConfig(model=_FREE)
    return BrainManager(config=config, bus=EventBus(), tools={})


def test_fast_model_uses_chosen_openrouter_model() -> None:
    mgr = _manager_with_openrouter_free()
    assert mgr._fast_model("openrouter") == _FREE


def test_deep_model_without_explicit_deep_reuses_chosen_model() -> None:
    mgr = _manager_with_openrouter_free()
    deep = mgr._deep_model("openrouter") or ""
    # The bug returned "anthropic/claude-opus-4.8" here.
    assert deep == _FREE, f"deep slot hijacked to a foreign-family default: {deep!r}"
    assert "anthropic/claude" not in deep


def test_fast_chain_never_injects_paid_anthropic_default_for_openrouter() -> None:
    mgr = _manager_with_openrouter_free()
    # Only OpenRouter is reachable (single-provider downloader).
    mgr._registry.available = lambda: {"openrouter"}  # type: ignore[assignment]
    mgr._dead_providers.clear()

    chain = mgr._build_fallback_chain("fast")
    openrouter_models = [m for (p, m) in chain if p == "openrouter"]

    assert openrouter_models, "OpenRouter must appear in its own chain"
    for m in openrouter_models:
        assert "anthropic/claude" not in (m or ""), (
            f"chain hijacked OpenRouter onto a paid Anthropic default: {m!r}"
        )
        assert m == _FREE


def test_get_brain_keeps_chosen_model_when_base_url_kwarg_rejected() -> None:
    """The deepest root cause (live forensic 2026-06-29): even with the chain
    correctly choosing nemotron:free, the brain that actually ran sent
    anthropic/claude-opus-4.8 on the wire.

    ``jarvis.toml`` sets ``[brain.providers.openrouter] base_url=...``, so
    ``_get_brain`` passes ``base_url`` to ``instantiate``. But
    ``OpenRouterBrain.__init__`` accepts only ``model`` → ``TypeError`` → the old
    fallback re-instantiated with NO kwargs at all, dropping ``model`` too, so the
    brain fell back to its hardcoded ``DEFAULT_MODEL`` = anthropic/claude-opus-4.8
    — a PAID model 403-blocked by the user's spend-limited OpenRouter key. The
    chosen model MUST survive a rejected optional kwarg. Generic: any
    OpenAI-compatible brain (openai/grok/openrouter) with a base_url in config
    would otherwise silently run its hardcoded default model.
    """
    config = JarvisConfig()
    config.brain.primary = "openrouter"
    config.brain.providers["openrouter"] = BrainProviderConfig(
        model=_FREE,
        base_url="https://openrouter.ai/api/v1",
    )
    mgr = BrainManager(config=config, bus=EventBus(), tools={})

    brain = mgr._get_brain("openrouter", _FREE)
    got = getattr(brain, "_model", None)
    assert got == _FREE, f"brain ran the wrong model on the wire: {got!r}"
    assert got != "anthropic/claude-opus-4.8"


def test_explicit_deep_model_still_wins() -> None:
    """Guard: providers that DO set deep_model keep it (claude-api/gemini/openai)."""
    config = JarvisConfig()
    config.brain.primary = "claude-api"
    config.brain.providers["claude-api"] = BrainProviderConfig(
        model="claude-haiku-4-5-20251001",
        deep_model="claude-opus-4-8",
    )
    mgr = BrainManager(config=config, bus=EventBus(), tools={})
    assert mgr._deep_model("claude-api") == "claude-opus-4-8"
    assert mgr._fast_model("claude-api") == "claude-haiku-4-5-20251001"


# ----------------------------------------------------------------------
# Chosen-model contract: the model on the wire == the SELECTED model, for
# EVERY provider/model. User mandate 2026-06-29: "it must work with whatever
# model you pick — pick GPT-5.5 and GPT-5.5 runs, never Opus in the background
# while the UI shows GPT-5.5." Provider-agnostic, OS-independent (pure model
# resolution — no keyring/OS/network touched by _get_brain construction).
# ----------------------------------------------------------------------

# (provider, chosen_model, has_base_url): each pairing a downloader might pick.
# openrouter carries base_url in jarvis.toml — the kwarg that triggered the
# TypeError → DEFAULT_MODEL collapse — so both an openrouter-namespaced model
# AND a cross-vendor model picked THROUGH openrouter (the user's GPT-5.5 example)
# are covered.
_CONTRACT_CASES = [
    ("openrouter", "openai/gpt-5.5", True),
    ("openrouter", "deepseek/deepseek-v4-flash", True),
    ("openrouter", _FREE, True),
    ("openai", "gpt-5.5", False),
    ("claude-api", "claude-opus-4-8", False),
    ("gemini", "gemini-3.5-flash", False),
    ("antigravity", "gemini-3.5-flash", False),
]


@pytest.mark.parametrize("provider,model,has_base_url", _CONTRACT_CASES)
def test_get_brain_runs_exactly_the_selected_model(
    provider: str, model: str, has_base_url: bool
) -> None:
    """The constructed brain must run the SELECTED model — never a DEFAULT_MODEL."""
    config = JarvisConfig()
    config.brain.primary = provider
    kwargs: dict[str, str] = {"model": model}
    if has_base_url:
        kwargs["base_url"] = "https://openrouter.ai/api/v1"
    config.brain.providers[provider] = BrainProviderConfig(**kwargs)
    mgr = BrainManager(config=config, bus=EventBus(), tools={})

    brain = mgr._get_brain(provider, model)
    got = getattr(brain, "_model", None)
    assert got == model, (
        f"{provider}: picked {model!r} but the brain runs {got!r} — the selected "
        f"model is not the one used (silent DEFAULT_MODEL drift)."
    )


def test_model_drift_is_logged_loudly_not_silent(caplog: pytest.LogCaptureFixture) -> None:
    """If a brain ever ignores the requested model (future regression), _get_brain
    must surface it as an ERROR — never let a wrong model run silently.

    Simulated by a registry whose instantiate ignores the model kwarg, so the
    built brain reports a different _model than requested."""
    config = JarvisConfig()
    config.brain.primary = "openrouter"
    config.brain.providers["openrouter"] = BrainProviderConfig(model="openai/gpt-5.5")
    mgr = BrainManager(config=config, bus=EventBus(), tools={})

    class _Stub:
        _model = "anthropic/claude-opus-4.8"  # ignores the requested model

    mgr._registry.instantiate = lambda name, **kw: _Stub()  # type: ignore[assignment]

    with caplog.at_level(logging.ERROR):
        mgr._get_brain("openrouter", "openai/gpt-5.5")

    assert any("MODEL DRIFT" in r.message for r in caplog.records), (
        "a wrong model on the wire must be logged loudly, not silently accepted"
    )
