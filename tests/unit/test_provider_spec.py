"""Integritäts-Tests für die deklarative ProviderSpec-Liste.

Wichtig: jeder secret_key, der in PROVIDERS referenziert wird, muss auch im
Setup-Wizard SECRETS-Set existieren — sonst läuft der API-Endpoint
POST /api/secrets/{key} ins Whitelist-Loch.
"""
from __future__ import annotations

from jarvis.setup.wizard import SECRETS
from jarvis.ui.web.provider_spec import (
    PROVIDERS,
    ProviderSpec,
    all_secret_keys,
    get_spec,
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
                f"der nicht in wizard.SECRETS deklariert ist"
            )


def test_only_codex_uses_cli_login() -> None:
    for spec in PROVIDERS:
        assert spec.auth_mode != "subscription_cli"
        if spec.id == "codex":
            assert spec.auth_mode == "codex"
            assert spec.login_cli == ("codex", "login")
        else:
            assert spec.login_cli is None


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
    assert "elevenlabs_api_key" not in keys, "ElevenLabs ist Dead-Code, darf nicht referenziert sein"


def test_no_hardcoded_model_names_in_specs() -> None:
    """Defensives Smoke-Check: Modellnamen-Indikatoren dürfen NICHT im Spec auftauchen."""
    forbidden_substrings = [
        "claude-3", "claude-4", "claude-opus", "claude-haiku", "claude-sonnet",
        "gpt-4", "gpt-5", "o1-", "o3-",
        "gemini-1", "gemini-2", "gemini-3",
    ]
    for spec in PROVIDERS:
        haystack = (spec.id + spec.label).lower()
        for needle in forbidden_substrings:
            assert needle not in haystack, (
                f"Provider '{spec.id}' enthält Modellnamen '{needle}' — laut Spec verboten"
            )


def test_provider_spec_is_frozen_dataclass() -> None:
    spec = PROVIDERS[0]
    assert isinstance(spec, ProviderSpec)
    try:
        spec.id = "mutated"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("ProviderSpec sollte frozen sein")
