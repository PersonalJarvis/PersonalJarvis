"""GET /api/settings/input-isolation + POST /api/settings/restart-unelevated.

The user-visible bug behind these routes: with the desktop app running elevated,
Windows discards every synthetic keystroke and automation query coming from
ordinary user software, so dictation apps, text expanders, and password-manager
auto-type silently stop working inside the Jarvis window. Elevation survives a
normal in-app restart, so the repair needs its own endpoint.

Every test injects the privilege verdict — none of them read the host's real
state, so they hold identically on the maintainer's Windows box and in a Linux
container.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.platform.input_isolation import (
    InputIsolationReason,
    InputIsolationReport,
)
from jarvis.ui.web import settings_routes
from jarvis.ui.web.settings_routes import router


def _client(desktop=None, kontrollierer=None, mission_manager=None):
    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(ui=SimpleNamespace())
    if desktop is not None:
        app.state.desktop_app = desktop
    if kontrollierer is not None:
        app.state.kontrollierer = kontrollierer
    if mission_manager is not None:
        app.state.mission_manager = mission_manager
    return TestClient(app)


def _report(*, blocked: bool, reason: InputIsolationReason, repairable: bool):
    return InputIsolationReport(
        blocked=blocked,
        reason=reason,
        platform="win32",
        summary="summary" if blocked else "",
        remedy="remedy" if blocked else "",
        can_restart_unelevated=repairable,
    )


@pytest.fixture
def pin_report(monkeypatch):
    """Pin the privilege verdict the routes see."""

    def _pin(report):
        monkeypatch.setattr(
            "jarvis.platform.input_isolation.describe_input_isolation",
            lambda **_kw: report,
        )

    return _pin


class TestStatusRoute:
    def test_reports_a_blocked_window_with_an_explanation(self, pin_report):
        pin_report(
            _report(
                blocked=True,
                reason=InputIsolationReason.ELEVATED,
                repairable=True,
            )
        )
        body = _client().get("/api/settings/input-isolation").json()
        assert body["blocked"] is True
        assert body["reason"] == "elevated"
        assert body["can_restart_unelevated"] is True
        assert body["summary"]

    def test_reports_a_healthy_window_without_noise(self, pin_report):
        pin_report(
            _report(blocked=False, reason=InputIsolationReason.NONE, repairable=False)
        )
        body = _client().get("/api/settings/input-isolation").json()
        assert body["blocked"] is False
        assert body["summary"] == ""

    def test_works_without_a_desktop_app(self, pin_report):
        """Headless hosts must still answer — the CLI reads this too."""
        pin_report(
            _report(blocked=False, reason=InputIsolationReason.NONE, repairable=False)
        )
        assert _client().get("/api/settings/input-isolation").status_code == 200


class TestRepairRoute:
    def test_schedules_an_unelevated_restart(self, pin_report):
        pin_report(
            _report(blocked=True, reason=InputIsolationReason.ELEVATED, repairable=True)
        )
        calls = {"n": 0}

        def request_unelevated_restart():
            calls["n"] += 1
            return (True, "restart scheduled")

        r = _client(
            SimpleNamespace(request_unelevated_restart=request_unelevated_restart)
        ).post("/api/settings/restart-unelevated")
        assert r.status_code == 200
        assert r.json()["unelevated"] is True
        assert calls["n"] == 1

    def test_rejects_control_bearer_before_privilege_or_restart_work(self, pin_report):
        """The alternate restart path has the same human-presence boundary."""
        pin_report(
            _report(blocked=True, reason=InputIsolationReason.ELEVATED, repairable=True)
        )
        calls = {"n": 0}
        desktop = SimpleNamespace(
            request_unelevated_restart=lambda: (
                calls.__setitem__("n", calls["n"] + 1) or True,
                "scheduled",
            )
        )

        r = _client(desktop).post(
            "/api/settings/restart-unelevated?force=true",
            headers={"Authorization": "Bearer control-client"},
        )

        assert r.status_code == 403
        assert r.json()["detail"]["error"] == "interactive_restart_required"
        assert calls["n"] == 0

    def test_refuses_when_there_is_nothing_to_repair(self, pin_report):
        """Never restart a healthy app: the button must be inert, not eager."""
        pin_report(
            _report(blocked=False, reason=InputIsolationReason.NONE, repairable=False)
        )
        called = {"n": 0}
        desktop = SimpleNamespace(
            request_unelevated_restart=lambda: called.__setitem__("n", 1)
        )
        r = _client(desktop).post("/api/settings/restart-unelevated")
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "not_elevated"
        assert called["n"] == 0

    def test_refuses_when_privileges_cannot_be_dropped_on_this_account(
        self, pin_report
    ):
        """UAC disabled / built-in Administrator: report it, do not restart into
        the same broken state."""
        pin_report(
            _report(
                blocked=True, reason=InputIsolationReason.ELEVATED, repairable=False
            )
        )
        r = _client(SimpleNamespace(request_unelevated_restart=lambda: (True, "")))
        response = r.post("/api/settings/restart-unelevated")
        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "cannot_drop_privileges"

    def test_keeps_the_app_up_when_de_elevation_fails(self, pin_report):
        """A failed repair must surface the reason, not leave a dead app."""
        pin_report(
            _report(blocked=True, reason=InputIsolationReason.ELEVATED, repairable=True)
        )
        desktop = SimpleNamespace(
            request_unelevated_restart=lambda: (False, "no linked token")
        )
        r = _client(desktop).post("/api/settings/restart-unelevated")
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "deescalation_failed"
        assert "no linked token" in r.json()["detail"]["message"]

    def test_503_on_a_headless_host(self, pin_report):
        pin_report(
            _report(blocked=True, reason=InputIsolationReason.ELEVATED, repairable=True)
        )
        assert _client().post("/api/settings/restart-unelevated").status_code == 503

    def test_running_missions_block_the_restart_unless_forced(self, pin_report):
        """Same guard as /restart-app — a repair must not silently kill work."""
        pin_report(
            _report(blocked=True, reason=InputIsolationReason.ELEVATED, repairable=True)
        )
        called = {"n": 0}
        desktop = SimpleNamespace(
            request_unelevated_restart=lambda: (called.__setitem__("n", 1), "")
        )
        client = _client(
            desktop,
            kontrollierer=SimpleNamespace(running_mission_ids=lambda: ["019e-aaa"]),
            mission_manager=SimpleNamespace(),
        )
        r = client.post("/api/settings/restart-unelevated")
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "missions_running"
        assert called["n"] == 0


class TestRouteContract:
    def test_repair_route_is_marked_dangerous(self):
        """check_danger_metadata.py requires the flag on destructive routes."""
        route = next(
            r
            for r in settings_routes.router.routes
            if getattr(r, "path", "") == "/api/settings/restart-unelevated"
        )
        assert route.openapi_extra == {"x-jarvis-dangerous": True}

    def test_status_route_is_a_plain_get(self):
        route = next(
            r
            for r in settings_routes.router.routes
            if getattr(r, "path", "") == "/api/settings/input-isolation"
        )
        assert set(route.methods) == {"GET"}
