import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.setup import state as setup_state
from jarvis.ui.web import onboarding_routes


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    # The legacy .setup-complete marker resolves next to the state file, so a
    # tmp state path isolates BOTH stores (see setup_complete_marker_path).
    monkeypatch.setattr(onboarding_routes, "_STATE_PATH_OVERRIDE", tmp_path / "s.json")
    monkeypatch.delenv("JARVIS_FORCE_ONBOARDING", raising=False)
    return tmp_path


@pytest.fixture
def client(state_dir):
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


def test_legacy_install_is_migrated(client, state_dir):
    (state_dir / ".setup-complete").write_text("done\n", encoding="utf-8")
    assert client.get("/api/onboarding/state").json()["completed"] is True


def test_force_env_overrides(client, state_dir, monkeypatch):
    (state_dir / ".setup-complete").write_text("done\n", encoding="utf-8")
    monkeypatch.setenv("JARVIS_FORCE_ONBOARDING", "1")
    assert client.get("/api/onboarding/state").json()["completed"] is False


def test_migration_fail_open_when_marker_probe_raises(client, monkeypatch):
    def boom(path=None) -> bool:
        raise RuntimeError("marker check failed")

    monkeypatch.setattr(setup_state, "setup_complete_marker_exists", boom)
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
