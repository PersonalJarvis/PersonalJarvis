"""Every gate table admits the keyless local providers (S5 parity pins).

The recurring bug class here is a provider that exists but is invisible in
one surface because a per-surface list was not extended (BUG-008 shape).
These pins make each gate's membership explicit — and spec-DERIVED where the
gate is about "local" (auth_mode "none"), never a hand-maintained name list.
"""

from __future__ import annotations

from jarvis.brain.app_control import _NO_CREDENTIAL_PROVIDERS, LOCAL_PROVIDERS
from jarvis.ui.web.provider_spec import PROVIDERS

_LOCAL_BRAIN_IDS = {"ollama", "local-openai"}


def test_local_providers_are_spec_derived() -> None:
    spec_locals = {s.id for s in PROVIDERS if s.auth_mode == "none"}
    assert LOCAL_PROVIDERS == spec_locals
    assert _LOCAL_BRAIN_IDS <= LOCAL_PROVIDERS
    # Airgapped profile finally has providers to switch TO.
    assert LOCAL_PROVIDERS, "airgapped profile admits no provider switch again"


def test_no_credential_set_equals_local_set() -> None:
    """auth_mode "none" IS the definition of "needs no credential" — one
    spec-derived set, two names, zero drift."""
    assert _NO_CREDENTIAL_PROVIDERS == LOCAL_PROVIDERS


def test_worker_provider_map_has_rows_for_locals() -> None:
    from jarvis.missions.worker_runtime.provider_map import (
        to_worker_slug,
        validate_configured_providers,
    )

    assert to_worker_slug("ollama") == "ollama"
    assert to_worker_slug("local-openai") == "local-openai"
    assert validate_configured_providers(sorted(_LOCAL_BRAIN_IDS)) == []


def test_api_agent_worker_covers_locals() -> None:
    from jarvis.missions.init import _API_AGENT_SLUGS
    from jarvis.missions.workers.api_agent_worker import supports_api_agent_worker

    for provider in _LOCAL_BRAIN_IDS:
        assert provider in _API_AGENT_SLUGS
        assert supports_api_agent_worker(provider)


def test_api_family_viability_accepts_keyless_locals(monkeypatch) -> None:
    """No credential can exist for a local family — viability must not demand
    one (the old `if not key: return False` would silently skip them)."""
    import jarvis.core.config as cfg_mod
    from jarvis.missions.init import _api_key_family_viable

    monkeypatch.setattr(cfg_mod, "get_jarvis_agent_secret", lambda pid: None)
    for provider in _LOCAL_BRAIN_IDS:
        assert _api_key_family_viable(provider) is True
    # A keyed cloud family without a key stays non-viable — unchanged.
    assert _api_key_family_viable("openrouter") is False


def test_critic_prefers_cloud_and_ends_local() -> None:
    from jarvis.missions.critic.runner import _API_CRITIC_PROVIDERS

    assert _API_CRITIC_PROVIDERS[-2:] == ("ollama", "local-openai")
    # Cloud graders stay ahead of the local last resort.
    assert _API_CRITIC_PROVIDERS.index("openrouter") < _API_CRITIC_PROVIDERS.index("ollama")


def test_tool_model_static_status_treats_keyless_as_ready() -> None:
    """The Tool-Model tab must never show a local provider as blocked for a
    "missing credential" (is_credential_present is True for auth_mode none,
    unknown model capabilities default to capable)."""
    from jarvis.ui.web.tool_model_routes import _static_candidate_status

    for provider in _LOCAL_BRAIN_IDS:
        verdict = _static_candidate_status(provider, None)
        assert verdict["ready"] is True, (provider, verdict)
