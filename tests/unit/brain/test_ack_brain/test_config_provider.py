"""Regression coverage for Ack-Brain provider configuration."""

from jarvis.brain.ack_brain.config import AckBrainConfig


def test_grok_provider_configuration_is_startup_safe() -> None:
    """A persisted Grok selection must not abort desktop config validation."""

    config = AckBrainConfig(provider="grok")

    assert config.provider == "grok"
    assert config.providers.grok.model == "grok-4.20-0309-non-reasoning"


def test_unknown_optional_provider_does_not_abort_startup() -> None:
    """A newer optional provider selection must degrade after config load."""

    config = AckBrainConfig.model_validate(
        {
            "provider": "future-provider",
            "providers": {
                "future-provider": {"model": "future-model"},
            },
        }
    )

    assert config.provider == "future-provider"
    assert config.providers.model_extra == {
        "future-provider": {"model": "future-model"},
    }
