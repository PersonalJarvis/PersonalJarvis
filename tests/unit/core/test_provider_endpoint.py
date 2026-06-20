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


# ── W2: team-mode flip ──────────────────────────────────────────────────────
from jarvis.core.config import TeamProxyConfig  # noqa: E402


def _team_cfg(url: str, *, enabled: bool = True, local: list[str] | None = None) -> JarvisConfig:
    return JarvisConfig(
        team_proxy=TeamProxyConfig(enabled=enabled, url=url, local_providers=local or [])
    )


def test_team_mode_routes_through_proxy(monkeypatch):
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: "sk-real")
    monkeypatch.setattr(cfg, "get_secret", lambda key, env=None: "tok-123")
    res = resolve_provider_endpoint(
        "grok",
        vendor_default_base_url="https://api.x.ai/v1",
        config=_team_cfg("https://keys.acme.dev"),
    )
    assert res.base_url == "https://keys.acme.dev/p/grok"
    assert res.credential == "tok-123"
    assert res.via_proxy is True


def test_team_mode_trailing_slash_normalized(monkeypatch):
    monkeypatch.setattr(cfg, "get_secret", lambda key, env=None: "tok-123")
    res = resolve_provider_endpoint("openai", config=_team_cfg("https://keys.acme.dev/"))
    assert res.base_url == "https://keys.acme.dev/p/openai"


def test_team_mode_local_provider_stays_direct(monkeypatch):
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: "sk-real")
    monkeypatch.setattr(cfg, "get_secret", lambda key, env=None: "tok-123")
    res = resolve_provider_endpoint(
        "faster-whisper",
        vendor_default_base_url=None,
        config=_team_cfg("https://keys.acme.dev", local=["faster-whisper"]),
    )
    assert res.via_proxy is False
    assert res.credential == "sk-real"


def test_team_mode_disabled_stays_direct(monkeypatch):
    monkeypatch.setattr(cfg, "get_provider_secret", lambda pid: "sk-real")
    res = resolve_provider_endpoint(
        "grok",
        vendor_default_base_url="https://api.x.ai/v1",
        config=_team_cfg("https://keys.acme.dev", enabled=False),
    )
    assert res.via_proxy is False
    assert res.base_url == "https://api.x.ai/v1"
