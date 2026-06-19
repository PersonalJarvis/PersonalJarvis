"""Contract-Tests: alle aktiven Brain-Provider erfüllen strukturell das Brain-Protocol.

Die Provider werden **nicht** instanziiert (würde API-Keys verlangen), sondern
es wird nur geprüft, dass die Klassen die Pflichtfelder + Methoden haben.

Ollama-Provider wurden 2025 via `f646273 chore: Ollama komplett entfernt` aus
dem Repo entfernt (jarvis.toml-Kommentar: "Ollama-Provider entfernt"). Die
erwartete Liste wurde damals nicht mit-aktualisiert — hier nachgezogen.
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
    "grok",
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
    # Class-Attr "name" muss dem Entry-Point-Namen entsprechen
    assert cls.name == name, f"{cls.__name__}.name = {cls.name!r}, Entry-Point = {name!r}"
