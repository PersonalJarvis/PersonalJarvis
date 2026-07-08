"""NVIDIA NIM is wired at the SAME three provider sites as OpenRouter.

The reference provider is OpenRouter: an OpenAI-compatible, bring-your-own-key
brain that also serves as an in-process ApiAgentWorker subagent. NVIDIA NIM must
match that pattern at all three sites — (1) the API/provider + credential layer,
(2) the "Brain Provider" UI spec + live model catalog, (3) the subagent worker —
so behaviour and appearance stay identical across platforms.
"""
from __future__ import annotations

from jarvis.brain.manager import (
    PROVIDER_ALIASES,
    TIER_DEFAULTS_BY_PROVIDER,
    get_tier_default_model,
)
from jarvis.brain.model_catalog import _ENDPOINTS, CATALOG_PROVIDERS, catalog_spec
from jarvis.brain.provider_registry import BrainProviderRegistry
from jarvis.core import config as cfg
from jarvis.missions.init import _API_AGENT_SLUGS, _select_subagent_worker_kind
from jarvis.missions.worker_runtime.provider_map import env_vars_for, to_worker_slug
from jarvis.missions.workers.api_agent_worker import (
    _BRAIN_BY_PROVIDER,
    _DEFAULT_MODEL,
    supports_api_agent_worker,
)
from jarvis.ui.web.provider_spec import get_spec


# ── Site 1: API / provider + credential layer ────────────────────────────────
def test_nvidia_is_a_registered_brain_plugin() -> None:
    reg = BrainProviderRegistry()
    assert "nvidia" in reg.available()
    assert "nvidia" not in reg.failed()
    brain = reg.instantiate("nvidia")
    assert brain.name == "nvidia"
    assert brain.can_call_tools() is True


def test_nvidia_credential_slot_resolves() -> None:
    assert cfg.PROVIDER_SECRET_CANDIDATES["nvidia"] == (
        ("nvidia_api_key", "NVIDIA_API_KEY"),
    )


def test_nvidia_endpoint_uses_nim_base_url() -> None:
    # No override → the NIM base URL; with a key it flows into the SDK client.
    ep = cfg.resolve_provider_endpoint(
        "nvidia", vendor_default_base_url="https://integrate.api.nvidia.com/v1"
    )
    assert ep.base_url == "https://integrate.api.nvidia.com/v1"


# ── Site 2: "Brain Provider" UI spec + live model catalog ─────────────────────
def test_nvidia_provider_spec_matches_openrouter_shape() -> None:
    nv = get_spec("nvidia")
    ref = get_spec("openrouter")
    assert nv is not None and ref is not None
    assert nv.tier == ref.tier == "brain"
    assert nv.auth_mode == ref.auth_mode == "api_key"
    assert nv.secret_keys == ("nvidia_api_key",)
    assert nv.dashboard_url  # a place to generate the key
    assert nv.brain_switchable is True


def test_nvidia_has_a_live_model_catalog() -> None:
    spec = catalog_spec("nvidia")
    assert spec is not None
    assert spec.tier == "brain"
    assert spec.live is True  # /v1/models is fetchable, like OpenRouter
    assert spec.curated  # offline fallback exists
    assert "nvidia" in CATALOG_PROVIDERS
    url, auth = _ENDPOINTS["nvidia"]
    assert url == "https://integrate.api.nvidia.com/v1/models"
    # Public catalog (like OpenRouter): key attached when present, else anonymous.
    assert auth == "bearer_opt"


def test_nvidia_has_tier_defaults() -> None:
    assert get_tier_default_model("router", "nvidia")
    assert get_tier_default_model("deep", "nvidia")
    assert "nvidia" in TIER_DEFAULTS_BY_PROVIDER["router"]
    assert "nvidia" in TIER_DEFAULTS_BY_PROVIDER["deep"]
    # Spoken aliases resolve to the canonical slug.
    assert PROVIDER_ALIASES["nvidia"] == "nvidia"
    assert PROVIDER_ALIASES["nemotron"] == "nvidia"


# ── Site 3: subagent worker selection ────────────────────────────────────────
def test_nvidia_runs_as_in_process_api_agent_subagent() -> None:
    # Like openai/openrouter: routed to the in-process ApiAgentWorker, never the
    # silent Claude fallback (the "selected provider must run" mandate).
    assert "nvidia" in _API_AGENT_SLUGS
    assert _select_subagent_worker_kind("nvidia", "") == "api_agent"
    assert supports_api_agent_worker("nvidia") is True
    assert "nvidia" in _BRAIN_BY_PROVIDER
    assert _DEFAULT_MODEL["nvidia"]


def test_nvidia_is_a_selectable_subagent_mapping() -> None:
    # MAPPINGS drives the API-Keys "Subagents" tab rows; nvidia must be present
    # (its worker_slug is a placeholder — it runs via ApiAgentWorker, not the CLI).
    assert to_worker_slug("nvidia") == "nvidia"
    assert env_vars_for("nvidia") == ("NVIDIA_API_KEY",)


# ── Regression: the provider-test must tolerate NIM's slow time-to-first-byte ──
def test_provider_test_default_timeout_clears_nim_ttfb() -> None:
    """The 'Test' button + section-health run through run_provider_test. Its
    timeout ceiling must clear a slow-but-healthy provider's TTFB — NIM's free
    dev tier was measured at 13-30s+ (live 2026-07-08). At the old 25s default a
    legit NIM call timed out and was shown as an 'API Integration Error'; the
    connect timeout (~5s) still catches a genuinely dead endpoint fast."""
    import inspect

    from jarvis.brain.provider_test import run_provider_test

    default = inspect.signature(run_provider_test).parameters["timeout_s"].default
    assert default >= 45.0, f"provider-test timeout {default}s too tight for NIM TTFB"
