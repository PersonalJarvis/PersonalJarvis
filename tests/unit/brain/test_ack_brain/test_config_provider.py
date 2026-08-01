"""Regression coverage for Ack-Brain provider configuration."""

from jarvis.brain.ack_brain.config import AckBrainConfig


def test_grok_provider_configuration_is_startup_safe() -> None:
    """A persisted Grok selection must not abort desktop config validation."""

    config = AckBrainConfig(provider="grok")

    assert config.provider == "grok"
    assert config.providers.grok.model == "grok-4.20-0309-non-reasoning"
