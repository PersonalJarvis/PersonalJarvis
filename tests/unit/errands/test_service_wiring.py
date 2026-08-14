"""Pins for the runner seam — get_runner() against the REAL config schema.

The service seam reads BrainManager internals leniently (getattr with a None
default) so a partially wired app degrades instead of crashing. The cost of
that leniency: a wrong attribute name never raises, it just leaves the runner
unavailable forever. That is exactly how reading ``brain.provider`` (a field
that does not exist — BrainConfig names it ``primary``) silently disabled
every errand for a whole boot. These tests therefore push a REAL JarvisConfig
through the seam, so a renamed config field turns the lights red here instead
of only in the desktop log.
"""

from __future__ import annotations

import pytest

from jarvis.core import runtime_refs
from jarvis.core.config import JarvisConfig
from jarvis.errands import service


class _FakeBrain:
    """Stands in for a Brain instance; the seam only passes it along."""


class _FakeExecutor:
    """Stands in for the ToolExecutor; the seam only passes it along."""


class _FakeManager:
    """The exact attribute surface get_runner() reads off the real manager."""

    def __init__(self, config: JarvisConfig) -> None:
        self._config = config
        self._tools = {"web_search": object()}
        self._tool_executor = _FakeExecutor()
        self.requested: list[str] = []

    def _get_brain(self, provider: str) -> _FakeBrain:
        self.requested.append(provider)
        return _FakeBrain()


@pytest.fixture(autouse=True)
def _clean_runner():
    service.reset_runner()
    yield
    service.reset_runner()


@pytest.fixture
def manager(tmp_path, monkeypatch) -> _FakeManager:
    config = JarvisConfig()
    config.memory.data_dir = str(tmp_path)
    fake = _FakeManager(config)
    monkeypatch.setattr(runtime_refs, "get_brain_manager", lambda: fake)
    return fake


def test_get_runner_builds_from_the_real_config_schema(manager) -> None:
    """A default JarvisConfig must be enough to wire the runner."""
    runner = service.get_runner()
    assert runner is not None
    # The brain the runner thinks with is the configured PRIMARY provider —
    # resolved through the manager, not re-derived from some other field.
    assert manager.requested == [manager._config.brain.primary]


def test_get_runner_reports_unavailable_when_brain_resolution_fails(manager, monkeypatch) -> None:
    """A dead provider yields an honest None, never a half-wired runner."""

    def _boom(provider: str):
        raise RuntimeError("provider not installed")

    monkeypatch.setattr(manager, "_get_brain", _boom)
    assert service.get_runner() is None
