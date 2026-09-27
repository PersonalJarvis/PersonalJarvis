"""Window background controls thread-hop native calls and share a bounded status contract."""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import get_args, get_type_hints

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.desktop_background import BackgroundStatus
from jarvis.ui.web.desktop_routes import router


class FakeDesktop:
    def __init__(self):
        self.enabled = False
        self.native_thread = None

    def get_background_status(self):
        self.native_thread = threading.get_ident()
        return {
            "available": True, "reason": None, "enabled": self.enabled,
            "mode": "foreground", "window_open": True,
        }

    def set_background_mode(self, enabled):
        self.enabled = enabled
        return self.get_background_status()


def test_native_calls_are_off_loop_and_enable_requires_boolean():
    app = FastAPI()
    app.include_router(router)
    native = FakeDesktop()
    app.state.desktop_app = native

    @app.get("/loop-thread")
    async def loop_thread():
        return {"thread": threading.get_ident()}

    with TestClient(app) as client:
        loop = client.get("/loop-thread").json()["thread"]
        assert client.get("/api/window/background").json()["enabled"] is False
        assert native.native_thread != loop
        assert client.post("/api/window/background", json={"enabled": "yes"}).status_code == 422
        assert not native.enabled
        assert client.post("/api/window/background", json={"enabled": True}).json()["enabled"]
        assert native.native_thread != loop


def test_headless_reports_existing_background_owner_without_claiming_a_window():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.get("/api/window/background").json()
        assert response == {
            "available": False, "reason": "headless_host", "enabled": True,
            "mode": "background", "window_open": False,
        }
        assert client.post("/api/window/background", json={"enabled": False}).json() == response


def test_background_mode_python_typescript_parity():
    source = (
        Path(__file__).resolve().parents[2]
        / "jarvis/ui/web/frontend/src/components/society/mars/MarsBackgroundControl.tsx"
    ).read_text(encoding="utf-8")
    block = re.search(r"BACKGROUND_MODES\s*=\s*\[([^]]+)\]", source)
    assert block
    assert tuple(re.findall(r'"([^"]+)"', block[1])) == get_args(
        get_type_hints(BackgroundStatus)["mode"]
    )
