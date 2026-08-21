"""The blank-window watchdog's health probe must not rebuild its client.

The watchdog asks ``/api/health`` once a second, forever, and the first
version asked through ``httpx.get()``. That is a fresh client per call, and
building a client builds an SSL context — ``ssl.create_default_context()``
reads and parses the entire CA bundle — even though the URL is plain
``http://`` on loopback where no certificate is ever checked.

Profiled on the maintainer's laptop 2026-08-21 while the app sat idle,
``create_default_context`` was **54 % of all backend CPU time**: by a wide
margin the hottest thing the process did, spent entirely on certificates for
a connection to itself.

These tests pin the client to one build per run, and pin the two behaviours
that must survive that change: an unreachable server still answers "no", and
a missing httpx still answers "no" instead of raising.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from jarvis.ui.desktop_app import DesktopApp


class _Cfg:
    """Just the one config value the probe reads."""

    def __init__(self, port: int = 47821) -> None:
        self.ui = types.SimpleNamespace(admin_api_port=port)


class _FakeApp:
    """The attribute surface the probe actually touches.

    A real ``DesktopApp`` would boot a log sink, a config load and a session
    token; the probe needs a port and somewhere to cache a client.
    """

    def __init__(self, port: int = 47821) -> None:
        self.cfg = _Cfg(port)

    _health_probe_client = DesktopApp._health_probe_client
    _backend_healthy = DesktopApp._backend_healthy


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeClient:
    """Counts its own construction, because that is the thing under test."""

    built = 0

    def __init__(self, **kwargs: Any) -> None:
        type(self).built += 1
        self.kwargs = kwargs
        self.calls: list[str] = []
        self.closed = False
        self.status = 200
        self.raises: Exception | None = None

    def get(self, url: str, **_: Any) -> _Response:
        self.calls.append(url)
        if self.raises is not None:
            raise self.raises
        return _Response(self.status)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_httpx(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """A stand-in ``httpx`` the probe's local import will find."""
    _FakeClient.built = 0
    module = types.ModuleType("httpx")
    module.Client = _FakeClient  # type: ignore[attr-defined]

    def _forbidden(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("httpx.get() builds a client per call — use the reused one")

    module.get = _forbidden  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "httpx", module)
    return module


def test_client_is_built_once_across_many_probes(fake_httpx: types.ModuleType) -> None:
    app = _FakeApp()

    for _ in range(50):
        assert app._backend_healthy() is True

    assert _FakeClient.built == 1, "one client per run, not one per probe"
    assert len(app._health_http.calls) == 50


def test_probe_hits_the_configured_port(fake_httpx: types.ModuleType) -> None:
    app = _FakeApp(port=51234)

    app._backend_healthy()

    assert app._health_http.calls == ["http://127.0.0.1:51234/api/health"]


def test_non_200_is_not_healthy(fake_httpx: types.ModuleType) -> None:
    app = _FakeApp()
    app._backend_healthy()
    app._health_http.status = 503

    assert app._backend_healthy() is False


def test_unreachable_server_answers_no(fake_httpx: types.ModuleType) -> None:
    """A refused connection is the answer, not an exception."""
    app = _FakeApp()
    app._backend_healthy()
    app._health_http.raises = OSError("connection refused")

    assert app._backend_healthy() is False


def test_reused_client_survives_a_failed_probe(fake_httpx: types.ModuleType) -> None:
    """One bad probe must not throw the client away and start rebuilding."""
    app = _FakeApp()
    app._backend_healthy()
    app._health_http.raises = OSError("connection refused")
    assert app._backend_healthy() is False

    app._health_http.raises = None
    assert app._backend_healthy() is True
    assert _FakeClient.built == 1


def test_missing_httpx_answers_no_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A build without httpx must degrade, not crash the watchdog."""
    import builtins

    real_import = builtins.__import__

    def _no_httpx(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "httpx":
            raise ImportError("no httpx in this build")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_httpx)
    app = _FakeApp()

    assert app._health_probe_client() is None
    assert app._backend_healthy() is False


def test_timeout_is_carried_by_the_client(fake_httpx: types.ModuleType) -> None:
    """The per-call timeout moved onto the client; it must not be lost."""
    app = _FakeApp()
    app._backend_healthy()

    assert app._health_http.kwargs.get("timeout") == 1.0
