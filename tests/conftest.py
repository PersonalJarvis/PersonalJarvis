"""Shared pytest fixtures for all test suites."""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit

# Add the repo root to sys.path so tests can import top-level modules like
# `ui.orb` (pytest doesn't add the repo root to sys.path by default).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from jarvis.core.bus import EventBus, reset_default_bus  # noqa: E402


def pytest_configure(config) -> None:  # noqa: ANN001
    config.addinivalue_line(
        "markers",
        "no_auto_web_auth: use explicit credentials against the production web boundary",
    )


@pytest.fixture(autouse=True)
def _authenticated_test_clients(request, monkeypatch):  # noqa: ANN001
    """Give legacy TestClient suites a real authenticated browser session.

    Security-boundary tests opt out with ``no_auto_web_auth`` and present their
    own credentials. This fixture changes only test clients: production still
    has no loopback, dev-mode, or pytest bypass.
    """
    if request.node.get_closest_marker("no_auto_web_auth") is not None:
        yield
        return

    from fastapi.testclient import TestClient

    from jarvis.ui.web.missions_auth import register_token, revoke_token
    from jarvis.ui.web.surface_security import COOKIE_NAME

    token = "jarvis-pytest-browser-session"  # noqa: S105
    monkeypatch.setenv("JARVIS_TRUSTED_HOSTS", "testserver,test")
    original_init = TestClient.__init__

    def authenticated_init(client, *args, **kwargs):  # noqa: ANN001
        base_url = str(kwargs.get("base_url", "http://testserver"))
        parsed = urlsplit(base_url)
        origin = f"{parsed.scheme or 'http'}://{parsed.netloc or 'testserver'}"
        headers = dict(kwargs.get("headers") or {})
        headers.setdefault("origin", origin)
        kwargs["headers"] = headers
        register_token(token)
        original_init(client, *args, **kwargs)
        client.cookies.set(COOKIE_NAME, token)

    monkeypatch.setattr(TestClient, "__init__", authenticated_init)
    try:
        yield
    finally:
        revoke_token(token)


@pytest.fixture(autouse=True)
def _browser_lock_pinned_on():
    """Pin the optional browser lock ON for every test, deterministic.

    The production default is OFF (loopback walks in without a credential),
    which would silently pass requests in any suite that uses a loopback
    client peer — and the lazy raw-TOML seed would otherwise read the host
    machine's real ``jarvis.toml``. Tests that exercise the open-access mode
    flip the flag explicitly and rely on this fixture's teardown reset.
    """
    from jarvis.ui.web.surface_security import (
        reset_browser_login_required,
        set_browser_login_required,
    )

    set_browser_login_required(True)
    yield
    reset_browser_login_required()


@pytest.fixture(autouse=True)
def _reset_bus():
    """Reset the global default bus before and after each test."""
    reset_default_bus()
    yield
    reset_default_bus()


@pytest_asyncio.fixture
async def fresh_bus():
    """Fresh EventBus for each test."""
    bus = EventBus()
    yield bus


@pytest.fixture
def anyio_backend():
    return "asyncio"
