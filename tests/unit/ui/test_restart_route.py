"""POST /api/settings/restart-app — one-click self-restart of the desktop app."""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.settings_routes import restart_app, router


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


def _fake_request(desktop=None, kontrollierer=None, mission_manager=None):
    state = SimpleNamespace()
    if desktop is not None:
        state.desktop_app = desktop
    if kontrollierer is not None:
        state.kontrollierer = kontrollierer
    if mission_manager is not None:
        state.mission_manager = mission_manager
    return SimpleNamespace(app=SimpleNamespace(state=state))


async def test_restart_survives_exhausted_default_thread_pool():
    """A restart must work even when the shared default ThreadPoolExecutor is
    saturated by un-cancellable hung threads.

    Forensic 2026-06-29: the custom-wake ctranslate2 transcription hung in C
    code; its 8 s ``asyncio.timeout`` cancelled only the *await*, abandoning the
    pool thread mid-call. Within minutes every default-pool worker was wedged,
    so the old ``await asyncio.to_thread(request_restart)`` queued forever — the
    restart POST never returned and the button spun "Restarting…" with the
    window still up. The restart trigger must run OFF the shared pool.
    """
    loop = asyncio.get_running_loop()
    release = threading.Event()
    # Saturate the default executor well beyond any platform's max_workers
    # (min(32, cpu+4)); the surplus queues, so no free worker remains.
    blockers = [loop.run_in_executor(None, release.wait) for _ in range(64)]
    await asyncio.sleep(0.1)  # let the blockers claim every pool thread

    called = threading.Event()

    def request_restart():
        called.set()
        return True

    request = _fake_request(SimpleNamespace(request_restart=request_restart))
    try:
        # With the bug this never resolves (queued behind the dead pool); the
        # 5 s cap turns the hang into a clean failure instead of a stuck test.
        result = await asyncio.wait_for(restart_app(request, force=True), timeout=5.0)
    finally:
        release.set()
        await asyncio.gather(*blockers, return_exceptions=True)

    assert result == {"ok": True, "restarting": True}
    assert called.is_set()


async def test_restart_guard_does_not_hang_on_wedged_mission_manager():
    """A wedged mission manager must not hang the restart guard.

    The guard looks up titles for in-flight missions to show in the 409 body.
    If that lookup blocks (a sick manager — the very state a user restarts to
    escape), the guard must time out and fall back to id-only summaries rather
    than hang the POST forever.
    """
    forever = asyncio.Event()  # never set → mission() would block indefinitely

    async def mission(mid):
        await forever.wait()
        return SimpleNamespace(prompt="never returns")

    request = _fake_request(
        SimpleNamespace(request_restart=lambda: True),
        kontrollierer=_running_kontrollierer("019e-aaa"),
        mission_manager=SimpleNamespace(mission=mission),
    )
    from fastapi import HTTPException

    try:
        await asyncio.wait_for(restart_app(request, force=False), timeout=5.0)
        raise AssertionError("expected HTTPException(409)")
    except HTTPException as exc:
        assert exc.status_code == 409
        detail = exc.detail
        assert detail["error"] == "missions_running"
        assert [m["id"] for m in detail["missions"]] == ["019e-aaa"]
