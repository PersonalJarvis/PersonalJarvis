"""reconcile_autostart: the self-healing decision table (spec §4)."""

from __future__ import annotations

import jarvis.autostart as autostart
from jarvis.autostart.protocol import AutostartStatus

from .conftest import make_caps, make_cfg


class _FakeManager:
    def __init__(self, *, supported: bool, installed: bool, matches: bool) -> None:
        self._supported = supported
        self._installed = installed
        self._matches = matches
        self.calls: list[str] = []

    def _st(self) -> AutostartStatus:
        return AutostartStatus(
            supported=self._supported,
            installed=self._installed,
            matches_spec=self._matches,
            entry_path="/x",
            detail="fake",
        )

    def status(self, spec):  # noqa: ARG002
        self.calls.append("status")
        return self._st()

    def install(self, spec):  # noqa: ARG002
        self.calls.append("install")
        self._installed = True
        self._matches = True
        return self._st()

    def uninstall(self):
        self.calls.append("uninstall")
        self._installed = False
        return self._st()


def _patch(monkeypatch, manager: _FakeManager) -> None:
    monkeypatch.setattr(autostart, "make_autostart_manager", lambda caps: manager)


def test_enabled_and_missing_installs(monkeypatch) -> None:
    mgr = _FakeManager(supported=True, installed=False, matches=False)
    _patch(monkeypatch, mgr)
    autostart.reconcile_autostart(make_cfg(enabled=True), make_caps())
    assert "install" in mgr.calls
    assert "uninstall" not in mgr.calls


def test_enabled_and_stale_reinstalls(monkeypatch) -> None:
    mgr = _FakeManager(supported=True, installed=True, matches=False)
    _patch(monkeypatch, mgr)
    autostart.reconcile_autostart(make_cfg(enabled=True), make_caps())
    assert "install" in mgr.calls


def test_enabled_and_current_is_noop(monkeypatch) -> None:
    mgr = _FakeManager(supported=True, installed=True, matches=True)
    _patch(monkeypatch, mgr)
    autostart.reconcile_autostart(make_cfg(enabled=True), make_caps())
    assert mgr.calls == ["status"]


def test_disabled_and_present_uninstalls(monkeypatch) -> None:
    mgr = _FakeManager(supported=True, installed=True, matches=True)
    _patch(monkeypatch, mgr)
    autostart.reconcile_autostart(make_cfg(enabled=False), make_caps())
    assert "uninstall" in mgr.calls
    assert "install" not in mgr.calls


def test_disabled_and_absent_is_noop(monkeypatch) -> None:
    mgr = _FakeManager(supported=True, installed=False, matches=False)
    _patch(monkeypatch, mgr)
    autostart.reconcile_autostart(make_cfg(enabled=False), make_caps())
    assert mgr.calls == ["status"]


def test_unsupported_host_never_touches_entry(monkeypatch) -> None:
    mgr = _FakeManager(supported=False, installed=False, matches=False)
    _patch(monkeypatch, mgr)
    autostart.reconcile_autostart(make_cfg(enabled=True), make_caps(display_present=False))
    assert mgr.calls == ["status"]


def test_reconcile_never_raises(monkeypatch) -> None:
    def _boom(caps):  # noqa: ARG001
        raise RuntimeError("boom")

    monkeypatch.setattr(autostart, "make_autostart_manager", _boom)
    status = autostart.reconcile_autostart(make_cfg(enabled=True), make_caps())
    assert status.supported is False
    assert "error" in status.detail.lower()
