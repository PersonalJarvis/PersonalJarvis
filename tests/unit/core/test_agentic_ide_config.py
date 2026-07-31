"""The [agentic_ide] block — additive, optional, defaulting to today's behaviour.

The default matters more than the feature: an install that never touches this
setting must resolve its prompt writer exactly as it did before the block
existed, because most downloaders will never open it.
"""
from __future__ import annotations

import pytest

from jarvis.core.config import JarvisConfig


def test_default_is_auto() -> None:
    """An install that never sets this behaves exactly as before."""
    assert JarvisConfig().agentic_ide.prompt_writer == "auto"


@pytest.mark.parametrize(
    "value", ["auto", "subscription", "api", "codex", "claude-cli", "antigravity"]
)
def test_accepted_values(value: str) -> None:
    config = JarvisConfig.model_validate({"agentic_ide": {"prompt_writer": value}})
    assert config.agentic_ide.prompt_writer == value


@pytest.mark.parametrize("value", ["nonsense!!", "", "  ", None, 42, "a b"])
def test_unusable_value_falls_back_to_auto_rather_than_failing_boot(
    value: object,
) -> None:
    """A hand-edited or downgraded config must never stop the app booting.

    Whether the named provider EXISTS is decided at resolve time, not here: a
    config naming a provider this build does not ship has to degrade, not block
    the boot of everything else.
    """
    config = JarvisConfig.model_validate({"agentic_ide": {"prompt_writer": value}})
    assert config.agentic_ide.prompt_writer == "auto"


def test_unknown_keys_are_tolerated() -> None:
    """Forward compatibility: a newer install's extra key must not break boot (AP-16)."""
    config = JarvisConfig.model_validate(
        {"agentic_ide": {"prompt_writer": "auto", "future_key": 1}}
    )
    assert config.agentic_ide.prompt_writer == "auto"


def test_a_missing_block_is_not_an_error() -> None:
    """Every existing jarvis.toml on disk lacks this block entirely."""
    config = JarvisConfig.model_validate({"brain": {"primary": "gemini"}})
    assert config.agentic_ide.prompt_writer == "auto"
