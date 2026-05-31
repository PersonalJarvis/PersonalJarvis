"""Shared Fixtures fuer die Curator-/Memory-Unit-Tests.

Fakes statt Mocks:
- `FakeBus` fuer den Merger (sammelt alle publizierten Events in einer Liste).
- Fresh-Workspace pro Test via `tmp_path` — keine Kollision zwischen Tests.
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
# FakeBus — sammelt publizierte Events statt wirklich zu dispatchen
# ----------------------------------------------------------------------

@dataclass
class FakeBus:
    """Minimaler Bus-Fake fuer Merger-Tests. Speichert Events in `published`."""

    published: list[Event] = field(default_factory=list)

    async def publish(self, event: Event) -> None:
        self.published.append(event)

    # Der Merger nutzt nur .publish — die restlichen EventBus-Methoden brauchen
    # wir nicht. Stubs halten mypy-mässig ruhig.
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
    """Frischer Workspace im tmp_path; initialisiert via Workspace.ensure."""
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
