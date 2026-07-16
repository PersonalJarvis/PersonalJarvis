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
        "/api/onboarding/decline-terms",
        "/api/onboarding/acknowledge-wake-word",
        "/api/onboarding/complete",
    }


# ---------------------------------------------------------------- decline-terms
# Design 2026-07-09: the install one-liner never asks anything, so the Terms
# gate is the ONE consent moment — declining it quits the whole app.


def test_decline_terms_quits_before_acceptance(client, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(
        onboarding_routes, "_schedule_app_shutdown", lambda req: calls.append(1)
    )
    r = client.post("/api/onboarding/decline-terms")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "quitting": True}
    assert calls == [1]
    # Nothing is persisted on decline — the next start shows the gate again.
    assert client.get("/api/onboarding/state").json()["terms"]["accepted"] is False


def test_decline_terms_409_after_acceptance(client, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(
        onboarding_routes, "_schedule_app_shutdown", lambda req: calls.append(1)
    )
    client.post("/api/onboarding/accept-terms")
    r = client.post("/api/onboarding/decline-terms")
    assert r.status_code == 409
    assert calls == []


def test_schedule_shutdown_prefers_desktop_quit(monkeypatch):
    from types import SimpleNamespace

    timers: list[object] = []

    class FakeTimer:
        def __init__(self, *args, **kwargs):
            timers.append(self)

        def start(self):  # pragma: no cover - must never run in this test
            raise AssertionError("hard-exit timer must not start on a desktop host")

    monkeypatch.setattr(onboarding_routes.threading, "Timer", FakeTimer)
    quit_calls: list[int] = []
    desktop = SimpleNamespace(request_quit=lambda: quit_calls.append(1) or True)
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(desktop_app=desktop)))
    onboarding_routes._schedule_app_shutdown(req)
    assert quit_calls == [1]
    assert timers == []


def test_schedule_shutdown_headless_falls_back_to_hard_exit(monkeypatch):
    from types import SimpleNamespace

    started: list[tuple] = []

    class FakeTimer:
        def __init__(self, *args, **kwargs):
            self.args = args

        def start(self):
            started.append(self.args)

    monkeypatch.setattr(onboarding_routes.threading, "Timer", FakeTimer)
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    onboarding_routes._schedule_app_shutdown(req)
    assert len(started) == 1  # armed exactly one delayed exit, nothing immediate


# ---------------------------------------------------------------- complete → fresh restart
# Maintainer request 2026-07-15: the first launch happens straight out of the
# installer, so the process that hosted onboarding predates every configured
# provider (language, wake word, API keys). Completing onboarding therefore
# restarts the app fresh via the relauncher; hosts without a window (headless)
# simply complete in place, and a restart failure never fails the request.


def _client_with_desktop(desktop):
    app = FastAPI()
    app.include_router(onboarding_routes.router)
    app.state.desktop_app = desktop
    return TestClient(app)


def test_complete_restarts_fresh_on_desktop(state_dir):
    from types import SimpleNamespace

    completed_when_restart_fired: list[bool] = []

    def restart() -> bool:
        # The completion marker must already be persisted when the restart
        # fires, so the fresh instance can never re-open the gate.
        completed_when_restart_fired.append(
            onboarding_routes._safe_state_payload()["completed"]
        )
        return True

    c = _client_with_desktop(SimpleNamespace(request_restart=restart))
    r = c.post("/api/onboarding/complete")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "restarting": True}
    assert completed_when_restart_fired == [True]


def test_complete_without_desktop_completes_in_place(client):
    r = client.post("/api/onboarding/complete")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "restarting": False}
    assert client.get("/api/onboarding/state").json()["completed"] is True


def test_complete_when_restart_reports_no_window(state_dir):
    from types import SimpleNamespace

    c = _client_with_desktop(SimpleNamespace(request_restart=lambda: False))
    r = c.post("/api/onboarding/complete")
    assert r.json() == {"ok": True, "restarting": False}
    assert c.get("/api/onboarding/state").json()["completed"] is True


def test_complete_survives_restart_failure(state_dir):
    from types import SimpleNamespace

    def boom() -> bool:
        raise RuntimeError("relauncher spawn failed")

    c = _client_with_desktop(SimpleNamespace(request_restart=boom))
    r = c.post("/api/onboarding/complete")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "restarting": False}
    assert c.get("/api/onboarding/state").json()["completed"] is True
