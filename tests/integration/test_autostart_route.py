"""Integration tests for /api/settings/autostart.

The desktop Settings view toggles login autostart through this endpoint. We
monkeypatch the autostart manager + capability probe so the test never touches
the real OS startup folder, and stub the config writer so it never rewrites the
real jarvis.toml.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import jarvis.autostart as autostart
import jarvis.platform.capabilities as caps_mod
from jarvis.autostart.protocol import AutostartStatus
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.platform.capabilities import Capabilities
from jarvis.ui.web.server import WebServer


def _caps(*, display_present: bool = True) -> Capabilities:
    return Capabilities(
        platform="linux",
        has_hotkey=False,
        has_ax_tree=False,
        has_overlay=False,
        has_pty=False,
        has_elevation=False,
        display_present=display_present,
        is_wayland=False,
        ax_permission_granted=None,
    )


class _FakeManager:
    def __init__(self, *, supported: bool = True) -> None:
        self._supported = supported
        self._installed = False
        self.calls: list[str] = []

    def _st(self) -> AutostartStatus:
        return AutostartStatus(
            supported=self._supported,
            installed=self._installed,
            matches_spec=self._installed,
            entry_path="/fake/autostart/personal-jarvis.desktop",
            detail="fake",
        )

    def status(self, spec):  # noqa: ARG002
        return self._st()

    def install(self, spec):  # noqa: ARG002
        self.calls.append("install")
        self._installed = self._supported
        return self._st()

    def uninstall(self):
        self.calls.append("uninstall")
        self._installed = False
        return self._st()


@pytest.fixture
def server() -> Iterator[WebServer]:
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    bus = EventBus()
    s = WebServer(cfg, bus=bus)
    s.app.state.config = cfg
    s.app.state.bus = bus
    yield s


@pytest.fixture
def fake_manager(monkeypatch: pytest.MonkeyPatch) -> _FakeManager:
    mgr = _FakeManager(supported=True)
    monkeypatch.setattr(autostart, "make_autostart_manager", lambda caps: mgr)
    monkeypatch.setattr(caps_mod, "detect_capabilities", lambda: _caps())
    return mgr


@pytest.fixture(autouse=True)
def _no_toml_write(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    calls: list[bool] = []
    from jarvis.core import config_writer

    monkeypatch.setattr(config_writer, "set_autostart", lambda enabled, **kw: calls.append(enabled))
    return calls


def test_get_reports_state(server: WebServer, fake_manager: _FakeManager) -> None:
    with TestClient(server.app) as client:
        resp = client.get("/api/settings/autostart")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True  # default
        assert body["supported"] is True
        assert body["platform"] == "linux"
        assert "jarvis.ui.web.launcher" in body["resolved_command"]


def test_put_enable_installs_and_persists(
    server: WebServer, fake_manager: _FakeManager, _no_toml_write: list[bool]
) -> None:
    with TestClient(server.app) as client:
        resp = client.put("/api/settings/autostart", json={"enabled": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True
        assert body["applied_live"] is True
        assert body["installed"] is True
    assert "install" in fake_manager.calls
    assert _no_toml_write == [True]
    assert server.app.state.config.autostart.enabled is True


def test_put_disable_uninstalls(
    server: WebServer, fake_manager: _FakeManager, _no_toml_write: list[bool]
) -> None:
    with TestClient(server.app) as client:
        resp = client.put("/api/settings/autostart", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
    assert "uninstall" in fake_manager.calls
    assert _no_toml_write == [False]
    assert server.app.state.config.autostart.enabled is False


def test_put_on_headless_is_honest(
    server: WebServer, monkeypatch: pytest.MonkeyPatch, _no_toml_write: list[bool]
) -> None:
    # Headless host: the toggle persists but supported=false and applied_live=false.
    monkeypatch.setattr(caps_mod, "detect_capabilities", lambda: _caps(display_present=False))
    with TestClient(server.app) as client:
        resp = client.put("/api/settings/autostart", json={"enabled": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["supported"] is False
        assert body["applied_live"] is False
    # Intent still persisted (it'll apply if the box later gains a display).
    assert _no_toml_write == [True]
