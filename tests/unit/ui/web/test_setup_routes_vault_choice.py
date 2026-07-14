"""Obsidian connect offers a vault choice (spec A6).

Uses the same TestClient + app.state.config stubbing conventions as
tests/unit/ui/web/test_wiki_routes.py — copy its app fixture setup.
"""
from __future__ import annotations

import json
import sqlite3
import types
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.setup.obsidian import ObsidianDetection
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


def test_status_accepts_jarvis_subdirectory_inside_registered_vault(
    tmp_path, monkeypatch,
):
    user_vault = tmp_path / "MyVault"
    user_vault.mkdir()
    app = _app(tmp_path, {"vaults": {"abc123": {"path": str(user_vault)}}})
    app.state.config.wiki_integration.vault_root = user_vault / "Jarvis"
    monkeypatch.setattr(
        setup_routes,
        "detect_obsidian",
        lambda: ObsidianDetection(installed=True, version="1.0"),
    )

    body = TestClient(app).get("/api/setup/obsidian/status").json()

    assert body["vault_registered"] is True
    assert body["recommended_action"] == "ok"


def test_separate_register_uses_injected_obsidian_config(tmp_path, monkeypatch):
    app = _app(tmp_path, {"vaults": {}})
    monkeypatch.setattr(
        setup_routes,
        "detect_obsidian",
        lambda: ObsidianDetection(installed=True, version="1.0"),
    )

    response = TestClient(app).post("/api/setup/obsidian/register")

    assert response.status_code == 200
    state = json.loads(app.state.obsidian_config_path.read_text(encoding="utf-8"))
    registered_paths = {entry["path"] for entry in state["vaults"].values()}
    assert str((tmp_path / "jarvis-vault").resolve()) in registered_paths


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


# ---------------------------------------------------------------------------
# Reindex-on-switch (spec A6): search must reflect the NEW vault immediately,
# never keep serving the previous vault's stale rows.
# ---------------------------------------------------------------------------
def _seed_fts(db_path: Path, vault_root: Path) -> None:
    """Index an existing vault into a fresh jarvis.db FTS table."""
    from jarvis.memory.wiki.fts_index import index_vault

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        index_vault(vault_root, conn)
    finally:
        conn.close()


def _fts_paths(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {row[0] for row in conn.execute("SELECT path FROM wiki_fts")}
    finally:
        conn.close()


def _write_page(root: Path, rel: str, title: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"# {title}\nBody of {title}.\n", encoding="utf-8")


def test_register_existing_mode_reindexes_search_to_the_new_vault(
    tmp_path, monkeypatch,
):
    """After switching to an existing vault, the FTS index must contain the
    NEW vault's pages and none of the previous vault's stale rows (spec A6)."""
    data_dir = tmp_path / "data"
    db_path = data_dir / "jarvis.db"

    # Previously-connected vault, already indexed into the shared FTS db.
    old_vault = tmp_path / "OldVault" / "Jarvis"
    _write_page(old_vault, "entities/old_note.md", "Old Note")
    _seed_fts(db_path, old_vault)
    assert "entities/old_note.md" in _fts_paths(db_path)  # precondition

    # The user's newly-chosen vault carries a different page under Jarvis/.
    new_vault = tmp_path / "NewVault"
    _write_page(new_vault / "Jarvis", "entities/new_note.md", "New Note")

    app = _app(tmp_path, {"vaults": {"abc": {"path": str(new_vault)}}})
    app.state.config.memory = types.SimpleNamespace(data_dir=str(data_dir))
    monkeypatch.setattr(setup_routes, "_write_vault_root_config", lambda values: None)

    resp = TestClient(app).post(
        "/api/setup/obsidian/register",
        json={"mode": "existing", "existing_vault_path": str(new_vault)},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "added"

    paths = _fts_paths(db_path)
    assert "entities/new_note.md" in paths          # new vault indexed
    assert "entities/old_note.md" not in paths      # stale rows cleared


def test_register_existing_mode_dry_run_does_not_reindex(tmp_path, monkeypatch):
    """A dry-run preview must not touch the FTS index either."""
    data_dir = tmp_path / "data"
    db_path = data_dir / "jarvis.db"

    old_vault = tmp_path / "OldVault" / "Jarvis"
    _write_page(old_vault, "entities/old_note.md", "Old Note")
    _seed_fts(db_path, old_vault)

    new_vault = tmp_path / "NewVault"
    _write_page(new_vault / "Jarvis", "entities/new_note.md", "New Note")

    app = _app(tmp_path, {"vaults": {"abc": {"path": str(new_vault)}}})
    app.state.config.memory = types.SimpleNamespace(data_dir=str(data_dir))
    monkeypatch.setattr(setup_routes, "_write_vault_root_config", lambda values: None)

    resp = TestClient(app).post(
        "/api/setup/obsidian/register?dry_run=true",
        json={"mode": "existing", "existing_vault_path": str(new_vault)},
    )
    assert resp.status_code == 200
    # dry-run left the old index untouched — no reindex side effect.
    assert _fts_paths(db_path) == {"entities/old_note.md"}
