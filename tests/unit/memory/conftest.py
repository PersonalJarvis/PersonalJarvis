"""Shared fixtures for the curator/memory unit tests.

Fakes instead of mocks:
- `FakeBus` for the merger (collects all published events in a list).
- Fresh workspace per test via `tmp_path` — no collision between tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from jarvis.core.events import Event
from jarvis.memory.curator.merger import Merger
from jarvis.memory.curator.validator import Validator
from jarvis.memory.people import PersonStore
from jarvis.memory.user_profile import UserProfile
from jarvis.memory.workspace import Workspace


# ----------------------------------------------------------------------
# FakeBus — collects published events instead of actually dispatching them
# ----------------------------------------------------------------------

@dataclass
class FakeBus:
    """Minimal bus fake for merger tests. Stores events in `published`."""

    published: list[Event] = field(default_factory=list)

    async def publish(self, event: Event) -> None:
        self.published.append(event)

    # The merger only uses .publish — we don't need the rest of the
    # EventBus methods. Stubs keep mypy quiet.
    def subscribe(self, *_args, **_kwargs) -> None:  # pragma: no cover
        pass

    def subscribe_all(self, *_args, **_kwargs) -> None:  # pragma: no cover
        pass

    def unsubscribe(self, *_args, **_kwargs) -> None:  # pragma: no cover
        pass


@pytest.fixture
def fake_bus() -> FakeBus:
    return FakeBus()


# ----------------------------------------------------------------------
# Workspace / Profile / PersonStore
# ----------------------------------------------------------------------

@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    """Fresh workspace in tmp_path; initialized via Workspace.ensure."""
    return Workspace.ensure(tmp_path / "ws")


@pytest.fixture
def profile(workspace: Workspace) -> UserProfile:
    return UserProfile.load(workspace.user_path)


@pytest.fixture
def person_store(workspace: Workspace) -> PersonStore:
    return PersonStore(workspace=workspace)


@pytest.fixture
def validator(profile: UserProfile, person_store: PersonStore) -> Validator:
    return Validator(profile=profile, people=person_store)


@pytest.fixture
def merger(profile: UserProfile, person_store: PersonStore, fake_bus: FakeBus) -> Merger:
    return Merger(profile=profile, people=person_store, bus=fake_bus)
