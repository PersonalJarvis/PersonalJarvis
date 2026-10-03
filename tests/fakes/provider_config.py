"""In-memory configuration for provider adapter tests, including endpoint routing."""

from jarvis.core import config


def install_provider_config(monkeypatch, value):
    """Keep adapters away from the host TOML without replacing routing decisions."""
    monkeypatch.setattr(config, "load_config", lambda: value)
    monkeypatch.setattr(
        config, "_cached_endpoint_route",
        lambda provider, default: config._endpoint_route(value, provider, default),
    )
