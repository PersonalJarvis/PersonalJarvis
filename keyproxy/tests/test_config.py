"""config.py — load provider map (real keys + bases) + admin key from ENV."""

from __future__ import annotations

import pytest

from keyproxy.config import ProxyConfig, load_config


def test_loads_real_key_per_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEYPROXY_OPENAI_KEY", "sk-real-openai")
    monkeypatch.setenv("KEYPROXY_CLAUDE_API_KEY", "sk-ant-real")
    monkeypatch.setenv("KEYPROXY_ADMIN_KEY", "admin-secret")
    cfg = load_config()

    looked = cfg.lookup("openai")
    assert looked is not None
    vendor, base, key = looked
    assert vendor == "openai_compatible"
    assert base == "https://api.openai.com/v1"  # default
    assert key == "sk-real-openai"

    # provider_id with a hyphen maps to UPPER + underscore env var.
    claude = cfg.lookup("claude-api")
    assert claude is not None
    assert claude[2] == "sk-ant-real"


def test_base_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEYPROXY_OPENAI_KEY", "sk-real")
    monkeypatch.setenv("KEYPROXY_OPENAI_BASE", "https://proxy.internal/v1")
    monkeypatch.setenv("KEYPROXY_ADMIN_KEY", "admin")
    cfg = load_config()
    looked = cfg.lookup("openai")
    assert looked is not None
    assert looked[1] == "https://proxy.internal/v1"


def test_lookup_unknown_provider_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KEYPROXY_ADMIN_KEY", "admin")
    cfg = load_config()
    assert cfg.lookup("not-a-provider") is None


def test_lookup_known_provider_without_configured_key_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A provider that is in the wire contract but has no real key set is not
    # available — fail closed, do not invent a key.
    monkeypatch.delenv("KEYPROXY_GROK_KEY", raising=False)
    monkeypatch.setenv("KEYPROXY_ADMIN_KEY", "admin")
    cfg = load_config()
    assert cfg.lookup("grok") is None


def test_admin_key_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEYPROXY_ADMIN_KEY", "super-secret-admin")
    cfg = load_config()
    assert cfg.admin_key == "super-secret-admin"


def test_allow_insecure_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEYPROXY_ADMIN_KEY", "admin")
    monkeypatch.delenv("KEYPROXY_ALLOW_INSECURE", raising=False)
    assert load_config().allow_insecure is False

    monkeypatch.setenv("KEYPROXY_ALLOW_INSECURE", "1")
    assert load_config().allow_insecure is True

    monkeypatch.setenv("KEYPROXY_ALLOW_INSECURE", "true")
    assert load_config().allow_insecure is True

    monkeypatch.setenv("KEYPROXY_ALLOW_INSECURE", "0")
    assert load_config().allow_insecure is False


def test_configured_providers_lists_only_keyed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for env in (
        "KEYPROXY_OPENAI_KEY",
        "KEYPROXY_CLAUDE_API_KEY",
        "KEYPROXY_GROK_KEY",
        "KEYPROXY_GEMINI_KEY",
        "KEYPROXY_OPENROUTER_KEY",
        "KEYPROXY_GROQ_API_KEY",
    ):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("KEYPROXY_OPENAI_KEY", "k1")
    monkeypatch.setenv("KEYPROXY_GEMINI_KEY", "k2")
    monkeypatch.setenv("KEYPROXY_ADMIN_KEY", "admin")
    cfg = load_config()
    assert set(cfg.configured_providers()) == {"openai", "gemini"}


def test_real_keys_not_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEYPROXY_OPENAI_KEY", "sk-super-secret-value")
    monkeypatch.setenv("KEYPROXY_ADMIN_KEY", "admin-secret-value")
    cfg = load_config()
    text = repr(cfg)
    assert "sk-super-secret-value" not in text
    assert "admin-secret-value" not in text


def test_provider_id_upper_mapping() -> None:
    assert ProxyConfig.env_key_name("groq-api") == "KEYPROXY_GROQ_API_KEY"
    assert ProxyConfig.env_base_name("claude-api") == "KEYPROXY_CLAUDE_API_BASE"
