"""Gemeinsame Pytest-Fixtures für alle Test-Suites."""
from __future__ import annotations

import sys
from pathlib import Path

# Repo-Root in sys.path aufnehmen, damit Tests Top-Level-Module wie `ui.orb`
# importieren koennen (Pytest setzt sys.path standardmaessig nicht auf den Repo-Root).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest
import pytest_asyncio

from jarvis.core.bus import EventBus, reset_default_bus


@pytest.fixture(autouse=True)
def _reset_bus():
    """Reset des globalen Default-Bus vor und nach jedem Test."""
    reset_default_bus()
    yield
    reset_default_bus()


@pytest_asyncio.fixture
async def fresh_bus():
    """Frischer EventBus pro Test."""
    bus = EventBus()
    yield bus


@pytest.fixture
def anyio_backend():
    return "asyncio"
