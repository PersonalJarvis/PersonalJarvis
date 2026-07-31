"""Full-app tests for the /api/ultrawiki/vault REST surface.

The vault is the Obsidian half of the readable knowledge base: the same
projection written to disk as Markdown. These routes are what the UI drives —
where the vault is, what is in it, whether Obsidian knows about it, and the
two actions (export, register).

The honesty cases carry the weight. Obsidian is absent on a headless server
and on most fresh machines, and an export must still work there and say so;
a failed write must name the path and the OS error rather than a spinner that
never ends.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer
from jarvis.ultrawiki import service as uw_service_mod
from jarvis.ultrawiki.service import UltraWikiService
from jarvis.ultrawiki.types import DocType, ItemState, RawItem


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    toml_path = tmp_path / "jarvis.toml"
    toml_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", str(toml_path))

    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    cfg.memory.data_dir = str(tmp_path / "data")
    cfg.ultrawiki.enabled = True
    cfg.ultrawiki.vault_path = str(tmp_path / "vault")

    server = WebServer(cfg, bus=EventBus())
    service = UltraWikiService(cfg, embedding_backend_factory=lambda: None)
    server.app.state.ultrawiki = service
    uw_service_mod.clear_jobs()
    with TestClient(server.app) as client:
        yield SimpleNamespace(
            client=client, service=service, server=server, cfg=cfg, tmp=tmp_path
        )
        client.portal.call(service.shutdown)
    uw_service_mod.clear_jobs()


def seed(env) -> None:
    async def _run() -> None:
        await env.service.ensure_started()
        store = env.service._store  # noqa: SLF001 — documented test seam
        await store.upsert_source("src1", connector="local-folder", label="Docs")
        await store.upsert_items(
            "src1",
            [
                RawItem(
                    external_id="a",
                    body="Routes via Tahiti.",
                    permalink="app://a",
                    timestamp_utc="2026-03-01T10:00:00Z",
                    title="Conversation on 2026-03-01",
                )
            ],
        )
        item = await store.get_item_by_external_id("src1", "a")
        await store.add_document(
            item["id"],
            DocType.SUMMARY,
            "Routes via Tahiti.",
            distill_json=json.dumps(
                {
                    "question": "How do I get to Bora Bora?",
                    "summary": "Routes via Tahiti.",
                    "resolution": "",
                    "entities": ["Bora Bora", "Tahiti"],
                    "refs": [],
                }
            ),
            distill_version=1,
        )
        await store.mark_stage_done(item["id"], ItemState.DISTILLED)

    env.client.portal.call(_run)


def stub_obsidian(monkeypatch, *, installed: bool, registered: bool = False):
    from jarvis.setup import obsidian as obsidian_mod

    monkeypatch.setattr(
        obsidian_mod,
        "detect_obsidian",
        lambda *a, **k: obsidian_mod.ObsidianDetection(installed=installed),
    )
    monkeypatch.setattr(
        obsidian_mod,
        "read_obsidian_vaults",
        lambda *a, **k: SimpleNamespace(vaults=[], config_path=Path("nowhere.json")),
    )
    monkeypatch.setattr(
        obsidian_mod, "is_vault_registered", lambda *a, **k: registered
    )


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_status_names_the_path_before_anything_was_exported(env, monkeypatch):
    stub_obsidian(monkeypatch, installed=False)

    body = env.client.get("/api/ultrawiki/vault/status").json()

    assert body["ok"] is True
    assert body["path"].endswith("vault")
    assert body["exists"] is False
    assert body["notes"] == 0
    assert body["obsidian"]["installed"] is False


def test_status_reports_what_the_export_left_behind(env, monkeypatch):
    stub_obsidian(monkeypatch, installed=True)
    seed(env)
    env.client.post("/api/ultrawiki/vault/export")

    body = env.client.get("/api/ultrawiki/vault/status").json()

    assert body["exists"] is True
    assert body["notes"] > 0
    assert body["last_export_at"]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_writes_the_vault_and_reports_the_numbers(env, monkeypatch):
    stub_obsidian(monkeypatch, installed=False)
    seed(env)

    response = env.client.post("/api/ultrawiki/vault/export")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["topics"] == 2
    assert body["moments"] == 1
    assert body["written"] > 0
    assert (env.tmp / "vault" / "Topics" / "Bora Bora.md").exists()


def test_export_works_without_obsidian_installed(env, monkeypatch):
    # A headless server has no Obsidian and never will; the files are still
    # the point.
    stub_obsidian(monkeypatch, installed=False)
    seed(env)

    body = env.client.post("/api/ultrawiki/vault/export").json()

    assert body["ok"] is True
    assert (env.tmp / "vault" / "README.md").exists()


def test_an_unwritable_vault_path_fails_with_the_reason(env, monkeypatch):
    stub_obsidian(monkeypatch, installed=False)
    seed(env)
    blocker = env.tmp / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    env.cfg.ultrawiki.vault_path = str(blocker)

    response = env.client.post("/api/ultrawiki/vault/export")

    assert response.status_code == 500
    assert "blocked" in response.json()["detail"]


def test_export_is_declared_dangerous(env):
    spec = env.server.app.openapi()
    operation = spec["paths"]["/api/ultrawiki/vault/export"]["post"]
    assert operation.get("x-jarvis-dangerous") is True


# ---------------------------------------------------------------------------
# Obsidian registration
# ---------------------------------------------------------------------------


def test_register_reports_success(env, monkeypatch):
    from jarvis.setup import obsidian as obsidian_mod

    stub_obsidian(monkeypatch, installed=True)
    seed(env)
    env.client.post("/api/ultrawiki/vault/export")
    monkeypatch.setattr(
        obsidian_mod,
        "register_vault",
        lambda *a, **k: obsidian_mod.RegisterResult(status="added"),
    )

    body = env.client.post("/api/ultrawiki/vault/register").json()

    assert body["status"] == "added"


def test_register_refuses_before_the_vault_exists(env, monkeypatch):
    stub_obsidian(monkeypatch, installed=True)

    response = env.client.post("/api/ultrawiki/vault/register")

    # Registering a folder that is not there would make Obsidian show an
    # empty vault and the user conclude that nothing was ever exported.
    assert response.status_code == 409
    assert "export" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Mode discipline
# ---------------------------------------------------------------------------


def test_vault_routes_answer_409_while_ultra_mode_is_off(env):
    env.cfg.ultrawiki.enabled = False

    assert env.client.get("/api/ultrawiki/vault/status").status_code == 409
    assert env.client.post("/api/ultrawiki/vault/export").status_code == 409
