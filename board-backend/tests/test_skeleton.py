"""Skeleton-Smoke-Tests (Commit 1)."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz_returns_ok(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["schema_ok"] is True
    assert body["version"]


def test_create_app_requires_admin_token() -> None:
    """Ohne ADMIN_TOKEN faellt create_app() bewusst lautstark um."""
    import pytest

    from board_backend.config import Settings
    from board_backend.main import create_app

    bad = Settings(admin_token="")
    with pytest.raises(RuntimeError, match="ADMIN_TOKEN"):
        create_app(settings=bad)
