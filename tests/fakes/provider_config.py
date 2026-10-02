"""An explicit model-backed configuration source for provider adapter tests."""

from __future__ import annotations

from jarvis.core import config as cfg


def use_provider_config(monkeypatch, config: cfg.JarvisConfig) -> None:
    """Feed adapter tests their model without reading the host's TOML cache.

    Production caches an immutable route directly from TOML. These tests pin
    adapter URLs and credentials, while the configuration-cache suite exercises
    file identity and invalidation separately.
    """
    monkeypatch.setattr(cfg, "load_config", lambda: config)
    monkeypatch.setattr(
        cfg,
        "_cached_endpoint_route",
        lambda provider, default: cfg._endpoint_route(config, provider, default),
    )
