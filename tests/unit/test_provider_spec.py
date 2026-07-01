"""Integrity tests for the declarative ProviderSpec list.

Important: every secret_key referenced in PROVIDERS must also exist in the
setup wizard's SECRETS set — otherwise the API endpoint
POST /api/secrets/{key} falls into the whitelist hole.
"""
from __future__ import annotations

from jarvis.setup.wizard import SECRETS
from jarvis.ui.web.provider_spec import (
    PROVIDERS,
    AltCredential,
    ProviderSpec,
    all_secret_keys,
    get_spec,
    provider_billing,
)


def test_provider_ids_are_unique() -> None:
    ids = [spec.id for spec in PROVIDERS]
    assert len(ids) == len(set(ids)), f"Duplikate in PROVIDERS: {ids}"


def test_every_secret_key_exists_in_wizard() -> None:
    wizard_keys = {spec.key for spec in SECRETS}
    for spec in PROVIDERS:
        for key in spec.secret_keys:
            assert key in wizard_keys, (
                f"Provider '{spec.id}' referenziert secret_key '{key}', "
                f"is not declared in wizard.SECRETS"
            )


def test_only_subscription_cli_providers_use_cli_login() -> None:
    """A login CLI belongs to the subscription/login providers only. Codex logs
    in via ``codex login``; Antigravity drives the bare ``agy`` binary (it has
    no ``login`` subcommand). Every pure API-key / local provider has none."""
    for spec in PROVIDERS:
        assert spec.auth_mode != "subscription_cli"  # never the legacy literal
        if spec.id == "codex":
            assert spec.auth_mode == "codex"
            assert spec.login_cli == ("codex", "login")
        elif spec.id == "antigravity":
            assert spec.auth_mode == "antigravity"
            assert spec.login_cli == ("agy",)
        else:
            assert spec.login_cli is None, f"{spec.id}: unexpected login_cli"


def test_api_key_specs_have_dashboard_url() -> None:
    for spec in PROVIDERS:
        if spec.auth_mode == "api_key":
            assert spec.dashboard_url, f"{spec.id}: api_key braucht dashboard_url"
            assert spec.secret_keys, f"{spec.id}: api_key braucht mindestens einen secret_key"


def test_codex_spec_is_separate_from_openai_api_key() -> None:
    codex = get_spec("codex")
    openai = get_spec("openai")
    assert codex is not None
    assert openai is not None
    assert codex.auth_mode == "codex"
    assert codex.secret_keys == ("codex_openai_api_key",)
    assert openai.secret_keys == ("openai_api_key",)


def test_none_specs_have_no_credentials() -> None:
    for spec in PROVIDERS:
        if spec.auth_mode == "none":
            assert spec.secret_keys == ()
            assert spec.login_cli is None


def test_get_spec_lookup() -> None:
    assert get_spec("openai") is not None
    assert get_spec("claude-api") is not None
    assert get_spec("does-not-exist") is None


def test_all_secret_keys_collects_unique_set() -> None:
    keys = all_secret_keys()
    assert "anthropic_api_key" in keys
    assert "gemini_api_key" in keys
    assert "openai_api_key" in keys
    assert "elevenlabs_api_key" not in keys, "ElevenLabs is dead code and must not be referenced"


def test_no_hardcoded_model_names_in_specs() -> None:
    """Defensive smoke check: model-name indicators must NOT appear in a spec."""
    forbidden_substrings = [
        "claude-3", "claude-4", "claude-opus", "claude-haiku", "claude-sonnet",
        "gpt-4", "gpt-5", "o1-", "o3-",
        "gemini-1", "gemini-2", "gemini-3",
    ]
    for spec in PROVIDERS:
        haystack = (spec.id + spec.label).lower()
        for needle in forbidden_substrings:
            assert needle not in haystack, (
                f"Provider '{spec.id}' contains model name '{needle}' — forbidden by the spec"
            )


def test_provider_spec_is_frozen_dataclass() -> None:
    spec = PROVIDERS[0]
    assert isinstance(spec, ProviderSpec)
    try:
        spec.id = "mutated"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("ProviderSpec should be frozen")


# ── New: per-provider credential help + billing classification ───────────────


def test_every_provider_has_credential_help() -> None:
    """Each provider explains, in plain English, what credential it needs and
    what it is for — the user-facing 'which key / subscription, and what for'."""
    for spec in PROVIDERS:
        assert spec.credential_help, f"{spec.id}: missing credential_help text"
        assert spec.credential_help.strip() == spec.credential_help


def test_provider_billing_is_derived_from_auth_mode() -> None:
    """Billing is capability-driven, never branched on a provider name
    (multi-provider mandate). API-key → pay-per-token; a subscription-login
    provider that ALSO accepts an API key → subscription_or_api; one that does
    not → subscription; local → no credential. Both Codex and Antigravity now
    accept an API key (the ChatGPT/Google subscription OR a per-token key), so
    both are subscription_or_api."""
    by_id = {spec.id: spec for spec in PROVIDERS}
    assert provider_billing(by_id["claude-api"]) == "api"
    assert provider_billing(by_id["gemini"]) == "api"
    assert provider_billing(by_id["antigravity"]) == "subscription_or_api"
    assert provider_billing(by_id["codex"]) == "subscription_or_api"
    assert provider_billing(by_id["faster-whisper"]) == "local"


def test_antigravity_accepts_api_key_billing() -> None:
    """Antigravity mirrors Codex: it bills over the Google subscription OAuth OR
    a Gemini API key (the Google Cloud credential), so it carries a secret key."""
    antigravity = get_spec("antigravity")
    assert antigravity is not None
    assert antigravity.auth_mode == "antigravity"
    assert "gemini_api_key" in antigravity.secret_keys
    assert provider_billing(antigravity) == "subscription_or_api"


def test_billing_covers_every_provider() -> None:
    allowed = {"api", "subscription", "subscription_or_api", "local"}
    for spec in PROVIDERS:
        assert provider_billing(spec) in allowed, f"{spec.id}: unknown billing"


def test_gemini_offers_both_aistudio_and_vertex() -> None:
    """The Gemini brain + TTS must surface BOTH credential paths so a user does
    not top up an AI-Studio key while Jarvis bills a Vertex service account
    (the 2026-06-22 forensic). Primary path = AI Studio (API key); the
    alt_credential = Vertex AI (service account, separate billing)."""
    for pid in ("gemini", "gemini-flash-tts"):
        spec = get_spec(pid)
        assert spec is not None, pid
        # Primary path is the AI-Studio API key.
        assert spec.auth_mode == "api_key"
        assert spec.dashboard_url and "aistudio.google.com" in spec.dashboard_url
        # The Vertex path is offered as an explicit alternative.
        assert spec.alt_credential is not None, f"{pid}: missing Vertex alt path"
        alt = spec.alt_credential
        assert "vertex" in alt.label.lower()
        assert alt.billing == "api"
        assert alt.credential_help
        assert alt.dashboard_url and "cloud.google.com" in alt.dashboard_url


def test_non_gemini_providers_have_no_alt_credential() -> None:
    """Only Gemini has the AI-Studio-vs-Vertex split today; every other provider
    keeps a single credential path (alt_credential is None)."""
    for spec in PROVIDERS:
        if spec.id in ("gemini", "gemini-flash-tts"):
            continue
        assert spec.alt_credential is None, f"{spec.id}: unexpected alt_credential"


def test_alt_credential_is_frozen() -> None:
    alt = AltCredential(
        label="x", billing="api", credential_help="y", dashboard_url=None,
    )
    try:
        alt.label = "mutated"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("AltCredential should be frozen")
