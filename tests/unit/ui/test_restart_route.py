"""POST /api/settings/restart-app — one-click self-restart of the desktop app."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

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


def _running_kontrollierer(*ids):
    return SimpleNamespace(running_mission_ids=lambda: list(ids))


def _manager_with_prompts(prompts):
    async def mission(mid):
        prompt = prompts.get(mid)
        return None if prompt is None else SimpleNamespace(prompt=prompt)

    return SimpleNamespace(mission=mission)


def test_restart_schedules_when_window_present():
    calls = {"n": 0}

    def request_restart():
        calls["n"] += 1
        return True

    r = _client(SimpleNamespace(request_restart=request_restart)).post(
        "/api/settings/restart-app"
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "restarting": True}
    assert calls["n"] == 1


def test_restart_503_without_desktop_app():
    r = _client().post("/api/settings/restart-app")  # headless: no desktop_app
    assert r.status_code == 503


def test_restart_503_when_no_window():
    # desktop present but request_restart returns False (headless / no window)
    desktop = SimpleNamespace(request_restart=lambda: False)
    r = _client(desktop).post("/api/settings/restart-app")
    assert r.status_code == 503


def test_restart_blocked_409_when_missions_running():
    """A live mission must not be silently killed by a restart (no force)."""
    calls = {"n": 0}
    desktop = SimpleNamespace(request_restart=lambda: calls.__setitem__("n", 1))
    k = _running_kontrollierer("019e-aaa", "019e-bbb")
    mgr = _manager_with_prompts(
        {"019e-aaa": "research US visa rules", "019e-bbb": "build the dashboard"}
    )
    r = _client(desktop, kontrollierer=k, mission_manager=mgr).post(
        "/api/settings/restart-app"
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "missions_running"
    ids = {m["id"] for m in detail["missions"]}
    assert ids == {"019e-aaa", "019e-bbb"}
    titles = {m["title"] for m in detail["missions"]}
    assert "research US visa rules" in titles
    # The app was NOT restarted — the running mission survives.
    assert calls["n"] == 0


def test_restart_forced_through_when_missions_running():
    """``?force=true`` is the explicit override — restart proceeds."""
    calls = {"n": 0}
    desktop = SimpleNamespace(request_restart=lambda: calls.__setitem__("n", 1) or True)
    k = _running_kontrollierer("019e-aaa")
    mgr = _manager_with_prompts({"019e-aaa": "long mission"})
    r = _client(desktop, kontrollierer=k, mission_manager=mgr).post(
        "/api/settings/restart-app?force=true"
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "restarting": True}
    assert calls["n"] == 1


def test_restart_proceeds_when_no_missions_running():
    """No live mission → the guard is transparent, normal restart."""
    calls = {"n": 0}
    desktop = SimpleNamespace(request_restart=lambda: calls.__setitem__("n", 1) or True)
    k = _running_kontrollierer()  # empty
    r = _client(desktop, kontrollierer=k, mission_manager=_manager_with_prompts({})).post(
        "/api/settings/restart-app"
    )
    assert r.status_code == 200
    assert calls["n"] == 1
