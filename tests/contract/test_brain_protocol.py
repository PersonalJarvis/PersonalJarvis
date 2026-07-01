"""Contract tests: every active Brain provider structurally satisfies the Brain protocol.

The providers are **not** instantiated (that would require API keys); instead
we only check that the classes have the required fields + methods.

Ollama providers were removed from the repo in 2025 via commit
`f646273 chore: Ollama komplett entfernt` (the jarvis.toml comment at the
time read: "Ollama-Provider entfernt"). The expected list wasn't updated
along with it back then — caught up here.
"""
from __future__ import annotations

import inspect

import pytest

from jarvis.brain.provider_registry import BrainProviderRegistry

BRAIN_PROVIDERS = [
    "claude-api",
    "claude-api",
    "openrouter",
    "openai",
    "gemini",
]


@pytest.fixture(scope="module")
def registry():
    return BrainProviderRegistry()


def test_all_providers_loaded(registry):
    available = set(registry.available())
    missing = set(BRAIN_PROVIDERS) - available
    assert not missing, f"Fehlende Brain-Provider-Plugins: {missing}"


@pytest.mark.parametrize("name", BRAIN_PROVIDERS)
def test_provider_has_required_attributes(registry, name):
    cls = registry.get_class(name)
    # Class-Level Attributes
    assert hasattr(cls, "name")
    assert hasattr(cls, "context_window")
    assert hasattr(cls, "supports_tools")
    assert hasattr(cls, "supports_vision")

    # Methods
    assert hasattr(cls, "complete")
    assert inspect.iscoroutinefunction(cls.complete) or inspect.isasyncgenfunction(cls.complete)
    assert hasattr(cls, "estimate_cost")
    assert callable(cls.estimate_cost)


@pytest.mark.parametrize("name", BRAIN_PROVIDERS)
def test_provider_name_matches_registration(registry, name):
    cls = registry.get_class(name)
    # Class attr "name" must match the entry-point name
    assert cls.name == name, f"{cls.__name__}.name = {cls.name!r}, Entry-Point = {name!r}"
