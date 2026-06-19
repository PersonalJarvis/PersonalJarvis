import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web import onboarding_routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(onboarding_routes, "_STATE_PATH_OVERRIDE", tmp_path / "s.json")
    monkeypatch.setattr(onboarding_routes, "is_first_run", lambda: True)
    monkeypatch.delenv("JARVIS_FORCE_ONBOARDING", raising=False)
    app = FastAPI()
    app.include_router(onboarding_routes.router)
    return TestClient(app)


def test_state_starts_incomplete(client):
    body = client.get("/api/onboarding/state").json()
    assert body["completed"] is False
    assert body["terms"]["current_version"] == "1.0"
    assert body["terms"]["accepted"] is False
    assert body["terms"]["accepted_version"] is None
    assert body["steps"][0] == "welcome"
    assert len(body["legal_references"]) >= 3
    assert body["wake_word_acknowledged"] is False


def test_terms_endpoint(client):
    body = client.get("/api/onboarding/terms").json()
    assert body["version"] == "1.0"
    assert "Personal Jarvis" in body["text"]


def test_accept_then_complete(client):
    assert client.post("/api/onboarding/accept-terms").json()["version"] == "1.0"
    client.post("/api/onboarding/acknowledge-wake-word")
    client.post("/api/onboarding/step", json={"step": "finish", "skipped": ["mic-test"]})
    client.post("/api/onboarding/complete")
    body = client.get("/api/onboarding/state").json()
    assert body["completed"] is True
    assert body["terms"]["accepted"] is True
    assert body["terms"]["accepted_version"] == "1.0"
    assert body["wake_word_acknowledged"] is True
    assert body["skipped_steps"] == ["mic-test"]
    assert body["current_step"] == "finish"


def test_legacy_install_is_migrated(client, monkeypatch):
    monkeypatch.setattr(onboarding_routes, "is_first_run", lambda: False)
    assert client.get("/api/onboarding/state").json()["completed"] is True


def test_force_env_overrides(client, monkeypatch):
    monkeypatch.setattr(onboarding_routes, "is_first_run", lambda: False)
    monkeypatch.setenv("JARVIS_FORCE_ONBOARDING", "1")
    assert client.get("/api/onboarding/state").json()["completed"] is False


def test_migration_fail_open_when_is_first_run_raises(client, monkeypatch):
    def boom() -> bool:
        raise RuntimeError("marker check failed")

    monkeypatch.setattr(onboarding_routes, "is_first_run", boom)
    r = client.get("/api/onboarding/state")
    assert r.status_code == 200
    assert r.json()["completed"] is False  # safe default, not a 500


def test_router_registers_all_paths():
    paths = {r.path for r in onboarding_routes.router.routes}
    assert paths >= {
        "/api/onboarding/state",
        "/api/onboarding/terms",
        "/api/onboarding/step",
        "/api/onboarding/accept-terms",
        "/api/onboarding/acknowledge-wake-word",
        "/api/onboarding/complete",
    }
