"""Shared pytest fixtures for all test suites."""
from __future__ import annotations

import sys
from pathlib import Path

# Add the repo root to sys.path so tests can import top-level modules like
# `ui.orb` (pytest doesn't add the repo root to sys.path by default).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest
import pytest_asyncio

from jarvis.core.bus import EventBus, reset_default_bus


@pytest.fixture(autouse=True)
def _reset_bus():
    """Reset the global default bus before and after each test."""
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
