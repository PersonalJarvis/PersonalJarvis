"""Test fixtures for the backend.

Pattern: one ``Settings`` override per test with ``tmp_path`` as the DB
path, so no file side effect survives. Admin token is always
``test-admin``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from board_backend.config import Settings
from board_backend.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        admin_token="test-admin",
        db_path=tmp_path / "board.db",
        register_rate_limit_per_minute=10,
        replay_window_seconds=300,
    )


@pytest.fixture
def app(settings: Settings):
    app = create_app(settings=settings)
    # Skip background tasks (StoriesCleanup, FederationPuller) in the test
    # setup — otherwise every test spawns an httpx.AsyncClient + tasks
    # that the TestClient teardown doesn't clean up properly.
    app.state.disable_background = True
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c
