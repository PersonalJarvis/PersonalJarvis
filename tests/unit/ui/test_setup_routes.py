"""Unit tests for the Phase B9 Obsidian Setup Wizard REST routes.

Mounts ``setup_routes.router`` on a fresh FastAPI app, monkeypatches the
detector + writer in :mod:`jarvis.setup.obsidian` so the real
``%APPDATA%\\obsidian\\obsidian.json`` is never touched, and asserts the
HTTP shapes / status codes documented in the route module.

The tests use a tiny ``SimpleNamespace`` config so we never have to
construct a full :class:`JarvisConfig`. The vault path resolution path
through ``app.state.repo_root`` is exercised by passing ``tmp_path`` as
the repo root.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# The route module imports detect_obsidian / read_obsidian_vaults /
# is_vault_registered / register_vault by name at import time, so the
# tests monkeypatch the names ON THE ROUTE MODULE (not on the underlying
# package) — that is the binding the routes actually call.
import jarvis.setup.state as _state_mod
from jarvis.setup.obsidian import (
    ObsidianDetection,
    ObsidianVaultsState,
    RegisterResult,
    VaultEntry,
)
from jarvis.ui.web import setup_routes
from jarvis.ui.web.setup_routes import router as setup_router


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _make_app(tmp_path: Path) -> FastAPI:
    """Build a minimal FastAPI app with the setup router mounted.

    ``app.state.repo_root`` is set to ``tmp_path`` so a relative
    ``vault_root`` resolves into a writable scratch directory rather
    than the real repo.
    """
    app = FastAPI()
    app.include_router(setup_router)
    wiki_cfg = SimpleNamespace(vault_root=Path("wiki/obsidian-vault"))
    app.state.config = SimpleNamespace(wiki_integration=wiki_cfg)
    app.state.repo_root = tmp_path
    return app


@pytest.fixture
def client_factory(tmp_path: Path):
    """Return a callable that yields a fresh ``(app, client, vault_path)``."""

    def _build() -> tuple[FastAPI, TestClient, Path]:
        app = _make_app(tmp_path)
        return app, TestClient(app), (tmp_path / "wiki" / "obsidian-vault").resolve()

    return _build


# ----------------------------------------------------------------------
# /api/setup/obsidian/status
# ----------------------------------------------------------------------


def test_status_all_ok(monkeypatch: pytest.MonkeyPatch, client_factory) -> None:
    """Installed + config exists + vault registered -> recommended_action='ok'."""
    _, client, vault_path = client_factory()

    monkeypatch.setattr(
        setup_routes,
        "detect_obsidian",
        lambda: ObsidianDetection(
            installed=True,
            exe_path=Path(r"C:\fake\Obsidian.exe"),
            version="1.7.4.0",
        ),
    )
    monkeypatch.setattr(
        setup_routes,
        "read_obsidian_vaults",
        lambda: ObsidianVaultsState(
            config_path=Path(r"C:\fake\obsidian.json"),
            config_exists=True,
            vaults=[VaultEntry(id="abc123", path=vault_path)],
        ),
    )
    monkeypatch.setattr(setup_routes, "is_vault_registered", lambda _v, _p: True)

    resp = client.get("/api/setup/obsidian/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["installed"] is True
    assert body["version"] == "1.7.4.0"
    assert body["config_exists"] is True
    assert body["vault_registered"] is True
    assert body["recommended_action"] == "ok"
    assert body["note"] is None
    assert body["vault_path"].endswith("obsidian-vault")


def test_status_not_installed(monkeypatch: pytest.MonkeyPatch, client_factory) -> None:
    """Obsidian missing -> recommended_action='install_obsidian'."""
    _, client, _vault = client_factory()

    monkeypatch.setattr(
        setup_routes,
        "detect_obsidian",
        lambda: ObsidianDetection(installed=False, exe_path=None, version=None),
    )
    monkeypatch.setattr(
        setup_routes,
        "read_obsidian_vaults",
        lambda: ObsidianVaultsState(
            config_path=Path(r"C:\fake\obsidian.json"),
            config_exists=False,
            vaults=[],
        ),
    )
    monkeypatch.setattr(setup_routes, "is_vault_registered", lambda _v, _p: False)

    resp = client.get("/api/setup/obsidian/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["installed"] is False
    assert body["recommended_action"] == "install_obsidian"


def test_status_installed_but_not_registered(
    monkeypatch: pytest.MonkeyPatch, client_factory
) -> None:
    """Installed but vault not in obsidian.json -> 'register_vault'."""
    _, client, _vault = client_factory()

    monkeypatch.setattr(
        setup_routes,
        "detect_obsidian",
        lambda: ObsidianDetection(
            installed=True, exe_path=Path(r"C:\fake\Obsidian.exe"), version="1.7.0.0"
        ),
    )
    monkeypatch.setattr(
        setup_routes,
        "read_obsidian_vaults",
        lambda: ObsidianVaultsState(
            config_path=Path(r"C:\fake\obsidian.json"),
            config_exists=True,
            vaults=[],
        ),
    )
    monkeypatch.setattr(setup_routes, "is_vault_registered", lambda _v, _p: False)

    resp = client.get("/api/setup/obsidian/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["installed"] is True
    assert body["vault_registered"] is False
    assert body["recommended_action"] == "register_vault"


def test_status_detection_raises_returns_200_with_note(
    monkeypatch: pytest.MonkeyPatch, client_factory
) -> None:
    """A raised detector must NOT 5xx the UI — fall back with a note."""
    _, client, _vault = client_factory()

    def _boom() -> ObsidianDetection:
        raise RuntimeError("registry blew up")

    monkeypatch.setattr(setup_routes, "detect_obsidian", _boom)

    resp = client.get("/api/setup/obsidian/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["installed"] is False
    assert body["recommended_action"] == "ok"
    assert body["note"] is not None
    assert "detection error" in body["note"]
    assert "registry blew up" in body["note"]


# ----------------------------------------------------------------------
# /api/setup/obsidian/register
# ----------------------------------------------------------------------


def test_register_added(monkeypatch: pytest.MonkeyPatch, client_factory) -> None:
    """status='added' -> HTTP 200 with uuid + backup path echoed."""
    _, client, _vault = client_factory()

    monkeypatch.setattr(
        setup_routes,
        "register_vault",
        lambda _p, *, dry_run=False: RegisterResult(
            status="added",
            vault_uuid="deadbeefcafef00d",
            backup_path=Path(r"C:\fake\obsidian.json.b9-backup-20260514-100000"),
        ),
    )

    resp = client.post("/api/setup/obsidian/register")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "added"
    assert body["vault_uuid"] == "deadbeefcafef00d"
    assert body["backup_path"].endswith("b9-backup-20260514-100000")


def test_register_dry_run_passes_flag(
    monkeypatch: pytest.MonkeyPatch, client_factory
) -> None:
    """?dry_run=true must be forwarded to register_vault."""
    _, client, _vault = client_factory()

    captured: dict[str, Any] = {}

    def _fake_register(path: Path, *, dry_run: bool = False) -> RegisterResult:
        captured["path"] = path
        captured["dry_run"] = dry_run
        return RegisterResult(status="added", vault_uuid="0123456789abcdef")

    monkeypatch.setattr(setup_routes, "register_vault", _fake_register)

    resp = client.post("/api/setup/obsidian/register?dry_run=true")
    assert resp.status_code == 200
    assert captured["dry_run"] is True
    body = resp.json()
    assert body["status"] == "added"
    assert body["backup_path"] is None  # dry-run never has a backup


def test_register_already_registered(
    monkeypatch: pytest.MonkeyPatch, client_factory
) -> None:
    """status='already_registered' -> HTTP 200 (idempotent re-register)."""
    _, client, _vault = client_factory()

    monkeypatch.setattr(
        setup_routes,
        "register_vault",
        lambda _p, *, dry_run=False: RegisterResult(status="already_registered"),
    )
    resp = client.post("/api/setup/obsidian/register")
    assert resp.status_code == 200
    assert resp.json()["status"] == "already_registered"


def test_register_config_missing_returns_409(
    monkeypatch: pytest.MonkeyPatch, client_factory
) -> None:
    """obsidian.json missing -> 409 so the UI can ask the user to launch Obsidian."""
    _, client, _vault = client_factory()

    monkeypatch.setattr(
        setup_routes,
        "register_vault",
        lambda _p, *, dry_run=False: RegisterResult(status="config_missing"),
    )

    resp = client.post("/api/setup/obsidian/register")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["status"] == "config_missing"


def test_register_rolled_back_returns_500(
    monkeypatch: pytest.MonkeyPatch, client_factory
) -> None:
    """status='rolled_back' from the writer -> HTTP 500 with envelope."""
    _, client, _vault = client_factory()

    monkeypatch.setattr(
        setup_routes,
        "register_vault",
        lambda _p, *, dry_run=False: RegisterResult(
            status="rolled_back",
            error="post-write verification failed",
        ),
    )

    resp = client.post("/api/setup/obsidian/register")
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["status"] == "rolled_back"
    assert detail["error"] == "post-write verification failed"


def test_register_unexpected_exception_returns_500(
    monkeypatch: pytest.MonkeyPatch, client_factory
) -> None:
    """An exception escaping register_vault must still 500 with envelope."""
    _, client, _vault = client_factory()

    def _boom(_path: Path, *, dry_run: bool = False) -> RegisterResult:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(setup_routes, "register_vault", _boom)

    resp = client.post("/api/setup/obsidian/register")
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["status"] == "rolled_back"
    assert "disk on fire" in detail["error"]
    assert detail["vault_uuid"] is None
    assert detail["backup_path"] is None


# ----------------------------------------------------------------------
# OpenAPI surface
# ----------------------------------------------------------------------


def test_routes_appear_in_openapi_schema(client_factory) -> None:
    """OpenAPI schema must list both setup endpoints."""
    _, client, _vault = client_factory()
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert "/api/setup/obsidian/status" in paths
    assert "/api/setup/obsidian/register" in paths
    assert "get" in paths["/api/setup/obsidian/status"]
    assert "post" in paths["/api/setup/obsidian/register"]


# ----------------------------------------------------------------------
# /api/setup/state  +  /api/setup/state/obsidian-seen   (Sub-Agent 6)
# ----------------------------------------------------------------------
#
# The state-flag store defaults to ``Path("data") / "setup_state.json"``
# relative to CWD. Tests redirect that path by chdir-ing into ``tmp_path``
# so the real repo's ``data/`` directory is never touched.


def test_get_state_returns_false_initially(
    monkeypatch: pytest.MonkeyPatch, client_factory, tmp_path: Path
) -> None:
    """Fresh sandbox (no setup_state.json) → obsidian_setup_seen=false."""
    # setup/state.py now anchors the path to the package root (not CWD) so
    # monkeypatch.chdir no longer isolates it.  Point _DEFAULT_STATE_PATH at a
    # fresh temp location so the real repo's data/setup_state.json is ignored.
    monkeypatch.setattr(_state_mod, "_DEFAULT_STATE_PATH", tmp_path / "data" / "setup_state.json")
    _, client, _vault = client_factory()

    resp = client.get("/api/setup/state")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"obsidian_setup_seen": False}


def test_post_obsidian_seen_then_get_state_true(
    monkeypatch: pytest.MonkeyPatch, client_factory, tmp_path: Path
) -> None:
    """POST sets the flag; the subsequent GET reflects the mutation."""
    monkeypatch.setattr(_state_mod, "_DEFAULT_STATE_PATH", tmp_path / "data" / "setup_state.json")
    _, client, _vault = client_factory()

    post = client.post("/api/setup/state/obsidian-seen")
    assert post.status_code == 200
    assert post.json() == {"ok": True}

    get = client.get("/api/setup/state")
    assert get.status_code == 200
    assert get.json() == {"obsidian_setup_seen": True}

    # The file actually exists where we expect it.
    state_file = tmp_path / "data" / "setup_state.json"
    assert state_file.exists()


def test_get_state_corrupt_file_returns_false(
    monkeypatch: pytest.MonkeyPatch, client_factory, tmp_path: Path
) -> None:
    """A corrupt setup_state.json must NOT 5xx — degrade to ``false``."""
    state_file = tmp_path / "data" / "setup_state.json"
    monkeypatch.setattr(_state_mod, "_DEFAULT_STATE_PATH", state_file)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("{not valid", encoding="utf-8")

    _, client, _vault = client_factory()
    resp = client.get("/api/setup/state")
    assert resp.status_code == 200
    assert resp.json() == {"obsidian_setup_seen": False}
