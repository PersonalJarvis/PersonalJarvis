"""Contract tests: every registered harness plugin satisfies the protocol."""
from __future__ import annotations

import inspect

import pytest

from jarvis.harness.manager import HarnessManager

# Registered ``jarvis.harness`` entry-points. NB: ``codex`` is a *brain*
# entry-point (jarvis.plugins.brain.codex:CodexBrain), not a harness — the
# Codex worker is a mission worker (CodexDirectWorker), so it is intentionally
# absent here.
HARNESSES = [
    "open-interpreter",
    "mcp-remote",
    "python-script",
]


@pytest.fixture(scope="module")
def manager():
    return HarnessManager()


def test_all_harnesses_discovered(manager):
    avail = set(manager.available())
    missing = set(HARNESSES) - avail
    assert not missing, f"Fehlende Harness-Plugins: {missing}"


@pytest.mark.parametrize("name", HARNESSES)
def test_harness_has_required_attrs(manager, name):
    harness = manager.get(name)
    assert isinstance(harness.name, str) and harness.name
    assert hasattr(harness, "version")
    assert hasattr(harness, "supports_versions")
    assert inspect.iscoroutinefunction(harness.health)
    assert inspect.isasyncgenfunction(harness.invoke) or inspect.iscoroutinefunction(harness.invoke)
    assert inspect.iscoroutinefunction(harness.cancel)


@pytest.mark.parametrize("name", HARNESSES)
def test_harness_name_matches_registration(manager, name):
    harness = manager.get(name)
    assert harness.name == name
