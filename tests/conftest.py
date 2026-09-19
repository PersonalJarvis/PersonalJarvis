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
def _agentic_ide_history_in_tmp(tmp_path_factory, monkeypatch):  # noqa: ANN001
    """Keep every suite away from the developer's real Agentic-IDE history.

    Two small JSON files decide what the workspace picker shows on its front
    page: the recent-folder list and the resume snapshot. Any test that opens a
    workspace writes both, and it happened for real — a run left seven pytest
    temp folders in the recents list, crowding out the one folder the developer
    had actually opened.

    This lives in the ROOT conftest on purpose. The same redirect existed one
    directory down, covering the suite that was known to open workspaces, and
    the leak came in through a different suite that also does. A guarantee that
    only holds for the tests somebody remembered is not a guarantee.

    The scratch-folder rule is stood down for the same reason it exists.
    ``recents`` refuses to remember anything under the system temp directory —
    and pytest hands out ``tmp_path`` from exactly there, so with the rule live
    every test that opens a workspace in a temporary folder would be recording
    nothing and asserting against an empty list. Redirecting the STORE is what
    protects the developer's data; the rule itself is verified against the real
    temp directory in ``test_recents_scratch_folders.py``.
    """
    from jarvis.agentic_ide import recents, resume_store

    root = tmp_path_factory.mktemp("agentic-ide-history")
    monkeypatch.setattr(recents, "_store_path", lambda: root / "recents.json")
    monkeypatch.setattr(resume_store, "_store_path", lambda: root / "last_session.json")
    monkeypatch.setattr(recents, "_temp_roots", list)
    yield root


@pytest.fixture(autouse=True)
def _macos_shell_registration_in_tmp(tmp_path_factory, monkeypatch):  # noqa: ANN001
    """Keep every suite away from the developer's real macOS shell databases.

    On a Mac the bundle suites build real ``Personal Jarvis.app`` bundles under
    pytest's temp directory, and ``ensure_macos_app_bundle`` then registered
    each one with the REAL LaunchServices database: one developer machine had
    collected some eighty temp bundles under the product's bundle id, every one
    a candidate for "Open With" and ``open -b``. The same call now also keeps
    the login item aimed at the bundle, which would point the developer's real
    LaunchAgent at a temp directory. Root conftest for the same reason as the
    history redirect above: the leak comes in through whichever suite nobody
    thought of. Suites that test these calls themselves patch on top of this.
    """
    import jarvis.autostart.macos as autostart_macos
    import jarvis.setup.macos_app_bundle as mab
    import jarvis.setup.macos_search_index as search_index

    agents = tmp_path_factory.mktemp("launch-agents")
    monkeypatch.setattr(autostart_macos, "_agents_dir", lambda: agents)
    # The label is the product's own, so a real ``launchctl load`` of a test
    # plist collides with — or, through RunAtLoad, starts — the installed app.
    monkeypatch.setattr(autostart_macos, "_launchctl", lambda *_argv: True)
    monkeypatch.setattr(mab, "_LSREGISTER", str(agents / "no-lsregister"))
    monkeypatch.setattr(search_index, "_MDIMPORT", str(agents / "no-mdimport"))
    # ``defaults write com.apple.dock`` + ``killall Dock`` on a developer's Mac.
    import jarvis.setup.macos_dock as macos_dock

    monkeypatch.setattr(macos_dock, "_DEFAULTS", str(agents / "no-defaults"))
    monkeypatch.setattr(macos_dock, "_KILLALL", str(agents / "no-killall"))
    monkeypatch.setattr(macos_dock, "_marker_path", lambda: agents / "macos-dock-pinned")
    yield agents


@pytest.fixture(autouse=True)
def _pricing_feed_in_tmp(tmp_path_factory, monkeypatch):  # noqa: ANN001
    """Keep cost tracking's provider-feed refresh off the network and off the
    developer's real ``data/model_catalog_cache.json``.

    ``jarvis.brain.cost.ensure_pricing_for`` prices a model the static table
    does not know by refreshing the OpenRouter feed ONCE per process — a real
    HTTP round-trip that would also rewrite the real cache file. Every suite
    that runs a brain turn with a made-up model id ("fake-model") would trigger
    it. So each test gets its own feed file, stamped as fetched just now: the
    refresh sees a fresh feed that simply does not list the model and stays
    home. Suites that exercise the refresh itself (test_cost_feed_pricing)
    override the path and the catalog again on top of this.
    """
    import json
    import time

    from jarvis.brain import cost

    root = tmp_path_factory.mktemp("pricing-feed")
    path = root / "model_catalog_cache.json"
    path.write_text(
        json.dumps({"openrouter": {"fetched_at": time.time(), "models": []}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cost, "_feed_path_override", path)
    monkeypatch.setattr(cost, "_feed_loaded", None)
    monkeypatch.setattr(cost, "_feed_fetched_at", 0.0)
    monkeypatch.setattr(cost, "_feed_rates", {})
    monkeypatch.setattr(cost, "_feed_by_name", {})
    monkeypatch.setattr(cost, "_refresh_attempted", set())
    yield


@pytest.fixture(autouse=True)
def _user_environment_and_config_soll_in_tmp(tmp_path_factory, monkeypatch):  # noqa: ANN001
    """Never let a test write the developer's registry or scripts/config-soll.json.

    Provider-switch writers persist three layers: jarvis.toml (tests pass a
    tmp path), config-soll.json and the User-scope JARVIS__* registry values.
    A writer test that forgot to patch the last two rewrote the real ones: a
    full run left JARVIS__STT__MODEL=whisper-large-v3 and
    JARVIS__BRAIN__PRIMARY=openrouter in HKCU\\Environment, which env-over-toml
    then applied to the running app on its next start. Tests that exercise
    these layers still patch them explicitly; this is the floor under them.
    """
    import os
    import shutil

    from jarvis.core import config_writer

    root = tmp_path_factory.mktemp("config_soll")
    soll = root / "config-soll.json"
    real = config_writer.PROJECT_ROOT / "scripts" / "config-soll.json"
    if real.is_file():
        shutil.copyfile(real, soll)
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: soll)
    monkeypatch.setattr(config_writer, "_set_user_env_var_winreg", lambda name, value: None)
    before = {k: v for k, v in os.environ.items() if k.startswith("JARVIS__")}
    yield
    for key in [k for k in os.environ if k.startswith("JARVIS__")]:
        if key not in before:
            del os.environ[key]
    os.environ.update(before)


@pytest.fixture(autouse=True)
def _reset_bus():
    """Reset the global default bus before and after each test."""
    reset_default_bus()
    yield
    reset_default_bus()


@pytest.fixture(autouse=True)
def _reset_config_caches():
    """Start every test with a cold config cache.

    ``jarvis.core.config`` remembers the parsed TOML and the routing decision
    derived from it, both keyed on the config FILE's identity — which is what
    makes them safe in production and unsafe across tests: a suite that swaps
    the config by monkeypatching ``load_config`` changes nothing about any file,
    so without this the second test is answered with the first one's config.

    Cheap (two dict clears) and applied to every test rather than the ones that
    happen to need it today, because the failure mode is a test passing on the
    previous test's data — which is silent, order-dependent, and exactly what
    caught three model-catalog tests when the routing cache was introduced.
    """
    from jarvis.core.config import clear_config_cache

    clear_config_cache()
    yield
    clear_config_cache()


@pytest.fixture(autouse=True)
def _reset_entry_point_cache():
    """Start every test with a cold entry-point catalogue.

    Same reasoning as the config caches above: ``jarvis.core.entry_points``
    remembers the installed plugin catalogue for the life of the process, which
    is safe in production (a new entry-point needs a restart anyway) and unsafe
    across tests — a suite that monkeypatches ``importlib.metadata.entry_points``
    to fake a plugin set would otherwise be answered from whatever an earlier
    test happened to read from the real installation.
    """
    from jarvis.core.entry_points import invalidate

    invalidate()
    yield
    invalidate()


@pytest_asyncio.fixture
async def fresh_bus():
    """Fresh EventBus for each test."""
    bus = EventBus()
    yield bus


@pytest.fixture
def anyio_backend():
    return "asyncio"
