"""Obsidian connect offers a vault choice (spec A6).

Uses the same TestClient + app.state.config stubbing conventions as
tests/unit/ui/web/test_wiki_routes.py — copy its app fixture setup.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web import setup_routes


def _app(tmp_path: Path, obsidian_json: dict | None) -> FastAPI:
    app = FastAPI()
    app.include_router(setup_routes.router)

    class _WikiCfg:
        vault_root = tmp_path / "jarvis-vault"

    class _Cfg:
        wiki_integration = _WikiCfg()

    app.state.config = _Cfg()
    cfg_path = tmp_path / "obsidian" / "obsidian.json"
    if obsidian_json is not None:
        cfg_path.parent.mkdir(parents=True)
        cfg_path.write_text(json.dumps(obsidian_json), encoding="utf-8")
    app.state.obsidian_config_path = cfg_path  # route override hook
    return app


def test_vault_list_returns_registered_vaults(tmp_path):
    user_vault = tmp_path / "MyVault"
    user_vault.mkdir()
    app = _app(tmp_path, {"vaults": {"abc123": {"path": str(user_vault)}}})
    resp = TestClient(app).get("/api/setup/obsidian/vaults")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["vaults"][0]["path"] == str(user_vault)


def test_register_existing_mode_points_vault_root_into_jarvis_subfolder(
    tmp_path, monkeypatch,
):
    user_vault = tmp_path / "MyVault"
    user_vault.mkdir()
    app = _app(tmp_path, {"vaults": {"abc123": {"path": str(user_vault)}}})

    written: dict = {}

    def _fake_update(values):  # captures the config_writer call
        written.update(values)

    monkeypatch.setattr(setup_routes, "_write_vault_root_config", _fake_update)
    resp = TestClient(app).post(
        "/api/setup/obsidian/register",
        json={"mode": "existing", "existing_vault_path": str(user_vault)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_vault_root"] == str(user_vault / "Jarvis")
    assert body["restart_required"] is True
    assert (user_vault / "Jarvis").is_dir()          # subfolder created
    assert written                                    # config write happened


def test_register_existing_mode_rejects_unknown_path(tmp_path):
    app = _app(tmp_path, {"vaults": {}})
    resp = TestClient(app).post(
        "/api/setup/obsidian/register",
        json={"mode": "existing", "existing_vault_path": str(tmp_path / "nope")},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "config_missing"


def test_register_existing_mode_rejects_missing_path(tmp_path, monkeypatch):
    """Omitted path must fail closed, never default to Path('.') == server CWD."""
    monkeypatch.chdir(tmp_path)  # so an accidental Path(".") hit is observable
    app = _app(tmp_path, {"vaults": {}})

    written: dict = {}
    monkeypatch.setattr(setup_routes, "_write_vault_root_config", written.update)
    resp = TestClient(app).post(
        "/api/setup/obsidian/register",
        json={"mode": "existing"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "config_missing"
    assert not (tmp_path / "Jarvis").exists()  # no folder in the server CWD
    assert not written                          # config was never touched


def test_register_existing_mode_dry_run_previews_without_side_effects(
    tmp_path, monkeypatch,
):
    """?dry_run=true previews the would-be vault root: no mkdir, no config write."""
    user_vault = tmp_path / "MyVault"
    user_vault.mkdir()
    app = _app(tmp_path, {"vaults": {"abc123": {"path": str(user_vault)}}})

    written: dict = {}
    monkeypatch.setattr(setup_routes, "_write_vault_root_config", written.update)
    resp = TestClient(app).post(
        "/api/setup/obsidian/register?dry_run=true",
        json={"mode": "existing", "existing_vault_path": str(user_vault)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "added"
    assert body["active_vault_root"] == str(user_vault / "Jarvis")
    assert body["restart_required"] is True
    assert not (user_vault / "Jarvis").exists()  # nothing created
    assert not written                            # nothing persisted
