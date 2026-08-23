"""The detail page's file card: what a plugin is made of, as readable files.

A plugin is a catalog entry, not a folder on disk, so the route writes the
pieces out the way a folder would hold them — ``plugin.json``, ``mcp.json``
when there is a server, the usage card the brain reads — and never a secret.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from jarvis.marketplace import catalog_data
from jarvis.ui.web.marketplace_routes import router


def _catalog(tmp_path: Path) -> Path:
    seed = json.loads(catalog_data._PACKAGE_SEED_PATH.read_text(encoding="utf-8-sig"))
    doc: dict[str, Any] = {
        "version": seed["version"],
        "schema_version": seed["schema_version"],
        "plugins": [
            {
                "id": "todo-fox",
                "display_name": "TodoFox",
                "description": "Tasks and reminders from TodoFox",
                "category": "Lists & Tasks",
                "source": "local",
                "logo_slug": "todoist",
                "auth": {
                    "mode": "hosted_mcp_allowlist",
                    "mcp_url": "https://mcp.todofox.example/mcp",
                },
                "mcp_server": {
                    "command": "npx",
                    "args": ["-y", "todo-fox-mcp"],
                    "env": {"TODOFOX_TOKEN": "sk-live-very-secret", "TODOFOX_REF": "$TODOFOX"},
                },
            }
        ],
    }
    path = tmp_path / "plugin_catalog.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.fixture()
def catalog_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog_data, "_DEFAULT_CATALOG_PATH", _catalog(tmp_path))
    catalog_data.clear_cache()
    yield
    catalog_data.clear_cache()


def _client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_plugin_files_list_manifest_and_masked_mcp_block(catalog_env: None) -> None:
    async with _client() as client:
        resp = await client.get("/api/marketplace/plugins/todo-fox/files")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    paths = [f["path"] for f in body["files"]]
    assert paths[:2] == ["plugin.json", "mcp.json"]

    manifest = json.loads(body["files"][0]["text"])
    assert manifest["id"] == "todo-fox"
    assert "mcp_server" not in manifest  # it has its own file

    mcp = json.loads(body["files"][1]["text"])
    env = mcp["mcpServers"]["todo-fox"]["env"]
    assert env["TODOFOX_REF"] == "$TODOFOX"  # a credential-store reference is safe
    assert "secret" not in env["TODOFOX_TOKEN"]  # a literal is masked
    assert "sk-live-very-secret" not in resp.text


@pytest.mark.asyncio
async def test_plugin_files_include_the_usage_card_when_one_ships(catalog_env: None) -> None:
    # The seed catalog's GitHub plugin carries a usage card; the files route
    # puts it next to the manifests so the owner can read what the brain reads.
    catalog_data.clear_cache()
    async with _client() as client:
        resp = await client.get("/api/marketplace/plugins/github/files")
    assert resp.status_code == 200, resp.text
    paths = [f["path"] for f in resp.json()["files"]]
    assert "plugin.json" in paths
    assert "USAGE.md" in paths


@pytest.mark.asyncio
async def test_plugin_files_unknown_id_is_404(catalog_env: None) -> None:
    async with _client() as client:
        resp = await client.get("/api/marketplace/plugins/nope/files")
    assert resp.status_code == 404
