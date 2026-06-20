"""W1a: resolve_provider_endpoint — explicit base_url override vs vendor default."""
from __future__ import annotations

import jarvis.core.config as cfg
from jarvis.core.config import (
    BrainConfig,
    BrainProviderConfig,
    JarvisConfig,
    ResolvedEndpoint,
    resolve_provider_endpoint,
)


def _cfg_with(provider_id: str, base_url: str | None) -> JarvisConfig:
    providers = {provider_id: BrainProviderConfig(base_url=base_url)}
    return JarvisConfig(brain=BrainConfig(providers=providers))


def test_returns_vendor_default_when_no_override(monkeypatch):
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: "sk-real")
    res = resolve_provider_endpoint(
        "grok", vendor_default_base_url="https://api.x.ai/v1", config=JarvisConfig()
    )
    assert isinstance(res, ResolvedEndpoint)
    assert res.base_url == "https://api.x.ai/v1"
    assert res.credential == "sk-real"
    assert res.via_proxy is False


def test_explicit_override_wins(monkeypatch):
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: "sk-real")
    res = resolve_provider_endpoint(
        "grok",
        vendor_default_base_url="https://api.x.ai/v1",
        config=_cfg_with("grok", "https://proxy.example/p/grok/v1"),
    )
    assert res.base_url == "https://proxy.example/p/grok/v1"
    assert res.credential == "sk-real"


def test_none_default_stays_none(monkeypatch):
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: "sk-real")
    res = resolve_provider_endpoint("openai", vendor_default_base_url=None, config=JarvisConfig())
    assert res.base_url is None


def test_loads_config_when_not_injected(monkeypatch):
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: "sk-real")
    monkeypatch.setattr(cfg, "load_config", lambda: _cfg_with("openai", "https://p/v1"))
    res = resolve_provider_endpoint("openai", vendor_default_base_url=None)
    assert res.base_url == "https://p/v1"
