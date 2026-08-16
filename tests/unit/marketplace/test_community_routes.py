"""Community marketplace routes: browse, install, uninstall.

Runs against the real FastAPI router with a pre-seeded index cache (no
network) and all data/ paths redirected into tmp — the same end-to-end wiring
the Plugins view exercises, minus the browser.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from jarvis.marketplace import catalog_data, community_source
from jarvis.marketplace.agent_plugins_loader import EXTENSION_NAMESPACE
from jarvis.marketplace.usage_cards import loader as cards_loader
from jarvis.ui.web.marketplace_routes import router


def _plugin_entry(name: str = "todo-fox", **manifest_overrides: Any) -> dict[str, Any]:
    plugin_json: dict[str, Any] = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": name,
        "description": "Tasks and reminders from TodoFox",
        "version": "1.2.0",
        "license": "MIT",
        "extensions": {
            EXTENSION_NAMESPACE: {
                "display_name": "TodoFox",
                "category": "Lists & Tasks",
                "auth": {
                    "mode": "pat_paste",
                    "token_creation_url": "https://todofox.example/settings/tokens",
                    "token_prefix": "tfx_",
                    "validation_endpoint": "https://api.todofox.example/v1/me",
                    "instruction_md": "Create a token in Settings.",
                },
            }
        },
    }
    plugin_json.update(manifest_overrides)
    return {
        "name": name,
        "publisher": "octocat",
        "version": "1.2.0",
        "published_at": "2026-08-12T10:00:00Z",
        "source_url": f"https://github.com/PersonalJarvis/marketplace/tree/main/plugins/{name}",
        "plugin_json": plugin_json,
        "mcp_json": {
            "mcpServers": {
                name: {
                    "type": "streamable-http",
                    "url": "https://mcp.todofox.example/mcp",
                }
            }
        },
        "usage_card": "---\nkeywords: todo, fox\n---\nUse TodoFox for task work.",
    }


def _index_payload(*plugin_entries: dict[str, Any]) -> dict[str, Any]:
    return {
        "revision": 1,
        "generated_at": "2026-08-12T12:00:00Z",
        "plugins": list(plugin_entries),
        "skills": [
            {
                "name": "three-point-check",
                "title": "Three Point Check",
                "description": "Summarize any topic in three bullets",
                "publisher": "octocat",
                "raw_url": "https://raw.example/skills/three-point-check/SKILL.md",
                "source_url": "https://github.com/PersonalJarvis/marketplace",
            }
        ],
    }


@pytest.fixture()
def community_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect every data/ path into tmp and pre-seed a FRESH index cache."""
    cache = tmp_path / "marketplace_index.json"
    cache.write_text(
        json.dumps({"fetched_at": time.time(), "index": _index_payload(_plugin_entry())}),
        encoding="utf-8",
    )
    monkeypatch.setattr(community_source, "_CACHE_PATH", cache)
    monkeypatch.setattr(community_source, "index_url", lambda: "https://reg.example/index.json")
    monkeypatch.setattr(catalog_data, "_DEFAULT_CATALOG_PATH", tmp_path / "plugin_catalog.json")
    monkeypatch.setattr(cards_loader, "_DATA_CARDS_DIR", tmp_path / "usage_cards")
    monkeypatch.setattr("jarvis.core.paths.user_skills_dir", lambda: tmp_path / "skills")
    catalog_data.clear_cache()
    cards_loader.load_usage_card.cache_clear()
    yield tmp_path
    catalog_data.clear_cache()
    cards_loader.load_usage_card.cache_clear()


def _client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_browse_lists_converted_plugins_and_skills(community_env: Path) -> None:
    async with _client() as client:
        resp = await client.get("/api/marketplace/community")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "fresh"
    plugin = data["plugins"][0]
    assert plugin["valid"] is True
    assert plugin["id"] == "todo-fox"
    assert plugin["source"] == "community"
    assert plugin["publisher"] == "octocat"
    assert plugin["installed"] is False
    assert plugin["seed_conflict"] is False
    assert plugin["mcp_server"]["url"] == "https://mcp.todofox.example/mcp"
    skill = data["skills"][0]
    assert skill["name"] == "three-point-check"
    assert skill["installed"] is False


@pytest.mark.asyncio
async def test_install_persists_catalog_entry_and_usage_card(
    community_env: Path,
) -> None:
    async with _client() as client:
        resp = await client.post("/api/marketplace/community/plugins/todo-fox/install")
        assert resp.status_code == 200
        assert resp.json()["plugin"]["status"] == "not_connected"

        # The installed plugin is now a first-class store card…
        listed = await client.get("/api/marketplace/plugins")
        entry = next(p for p in listed.json()["plugins"] if p["id"] == "todo-fox")
        assert entry["source"] == "community"
        assert entry["status"] == "not_connected"

        # …and browse reflects the install.
        browse = await client.get("/api/marketplace/community")
        assert browse.json()["plugins"][0]["installed"] is True

    override = json.loads((community_env / "plugin_catalog.json").read_text(encoding="utf-8"))
    assert any(p["id"] == "todo-fox" for p in override["plugins"])
    card = cards_loader.load_usage_card("todo-fox")
    assert card is not None and "todo" in card.keywords


@pytest.mark.asyncio
async def test_install_unknown_name_404(community_env: Path) -> None:
    async with _client() as client:
        resp = await client.post("/api/marketplace/community/plugins/nope/install")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_install_reserved_seed_id_409(
    community_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = community_env / "marketplace_index.json"
    cache.write_text(
        json.dumps(
            {
                "fetched_at": time.time(),
                "index": _index_payload(_plugin_entry(name="github")),
            }
        ),
        encoding="utf-8",
    )
    async with _client() as client:
        resp = await client.post("/api/marketplace/community/plugins/github/install")
    assert resp.status_code == 409
    assert "built-in" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_install_invalid_manifest_400(community_env: Path) -> None:
    entry = _plugin_entry()
    entry["mcp_json"]["mcpServers"]["todo-fox"]["url"] = "http://plain.example/mcp"
    (community_env / "marketplace_index.json").write_text(
        json.dumps({"fetched_at": time.time(), "index": _index_payload(entry)}),
        encoding="utf-8",
    )
    async with _client() as client:
        browse = await client.get("/api/marketplace/community")
        assert browse.json()["plugins"][0]["valid"] is False
        resp = await client.post("/api/marketplace/community/plugins/todo-fox/install")
    assert resp.status_code == 400
    assert "https" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_uninstall_removes_entry_and_card(community_env: Path) -> None:
    async with _client() as client:
        await client.post("/api/marketplace/community/plugins/todo-fox/install")
        resp = await client.delete("/api/marketplace/community/plugins/todo-fox")
        assert resp.status_code == 200
        assert resp.json()["removed"] is True

        listed = await client.get("/api/marketplace/plugins")
        assert all(p["id"] != "todo-fox" for p in listed.json()["plugins"])
    assert cards_loader.load_usage_card("todo-fox") is None


@pytest.mark.asyncio
async def test_uninstall_seed_plugin_409(community_env: Path) -> None:
    async with _client() as client:
        resp = await client.delete("/api/marketplace/community/plugins/github")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_refresh_bypasses_ttl(community_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    real_get_index = community_source.get_index

    async def spy(*, force: bool = False, transport: Any = None) -> Any:
        calls.append(force)
        return await real_get_index(force=False, transport=transport)

    monkeypatch.setattr(community_source, "get_index", spy)
    async with _client() as client:
        resp = await client.post("/api/marketplace/community/refresh")
    assert resp.status_code == 200
    assert calls == [True]


# ----------------------------------------------------------------------
# Install by name (skill OR plugin) — the published one-liner
# ----------------------------------------------------------------------

_SKILL_MD = """---
schema_version: "1"
name: three-point-check
version: "1.0.0"
description: Summarize any topic in exactly three bullets plus a takeaway.
when_to_use: When someone asks for a brief, a TLDR, or the short version.
category: productivity
---

# Three Point Check

Produce three bullets and one takeaway line.
"""


def _client_with_skills(skills_root: Path) -> tuple[httpx.AsyncClient, Any]:
    """Test client whose app carries a real SkillRegistry (skill installs need it)."""
    from jarvis.skills.registry import SkillRegistry

    app = FastAPI()
    app.include_router(router)
    registry = SkillRegistry(root=skills_root)
    registry.reload_sync()
    app.state.skill_registry = registry
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )
    return client, registry


@pytest.fixture()
def offline_skill_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Write the SKILL.md locally instead of fetching it (no network in tests).

    The download itself — https-only, slug-only, path containment — is covered
    in tests/unit/marketplace/test_community_hardening.py; here we only need a
    file on disk so the registry parse + lifecycle state are the REAL ones.
    """
    from jarvis.skills.finder import SkillFinder

    async def fake_install(self: Any, candidate: Any) -> Path:
        target_dir = tmp_path / "skills" / candidate.name
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "SKILL.md"
        target.write_text(_SKILL_MD, encoding="utf-8")
        return target

    monkeypatch.setattr(SkillFinder, "install", fake_install)


@pytest.mark.asyncio
async def test_install_by_name_installs_a_skill_and_reports_it_ready(
    community_env: Path, offline_skill_download: None
) -> None:
    client, _ = _client_with_skills(community_env / "skills")
    async with client:
        resp = await client.post(
            "/api/marketplace/community/install/three-point-check"
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "skill"
    assert data["id"] == "three-point-check"
    assert data["title"] == "Three Point Check"
    # A valid skill is live the moment it parses — the CLI reports exactly this.
    assert data["state"] == "validated"
    assert data["ready"] is True
    assert data["next_action"] == "none"
    assert data["problem"] is None
    assert data["location"].endswith("SKILL.md")
    assert (community_env / "skills" / "three-point-check" / "SKILL.md").exists()


def _broken_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str
) -> None:
    from jarvis.skills.finder import SkillFinder

    async def fake_install(self: Any, candidate: Any) -> Path:
        target_dir = tmp_path / "skills" / candidate.name
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "SKILL.md"
        target.write_text(content, encoding="utf-8")
        return target

    monkeypatch.setattr(SkillFinder, "install", fake_install)


@pytest.mark.asyncio
async def test_install_by_name_reports_a_broken_skill_as_not_ready(
    community_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A file that lands but fails validation must NOT be reported as usable."""
    _broken_download(
        monkeypatch,
        tmp_path,
        _SKILL_MD.replace(
            "category: productivity",
            "category: productivity\ntriggers:\n  - type: voice\n",
        ),
    )
    client, _ = _client_with_skills(community_env / "skills")
    async with client:
        resp = await client.post(
            "/api/marketplace/community/install/three-point-check"
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert data["state"] == "draft"
    assert data["next_action"] == "repair"
    assert "pattern" in (data["problem"] or "")


@pytest.mark.asyncio
async def test_install_by_name_unreadable_file_422_names_the_path(
    community_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No frontmatter at all: the file lands under no known name — say so.

    The loader keys a skill by its frontmatter name, so such a download
    registers under nothing the caller asked for. That used to surface as an
    uncaught KeyError (HTTP 500) that never mentioned the downloaded file.
    """
    _broken_download(monkeypatch, tmp_path, "no frontmatter at all\n")
    client, _ = _client_with_skills(community_env / "skills")
    async with client:
        resp = await client.post(
            "/api/marketplace/community/install/three-point-check"
        )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "SKILL.md" in detail
    assert "three-point-check" in detail


@pytest.mark.asyncio
async def test_install_by_name_installs_a_plugin_as_not_connected(
    community_env: Path,
) -> None:
    client, _ = _client_with_skills(community_env / "skills")
    async with client:
        resp = await client.post("/api/marketplace/community/install/todo-fox")
    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "plugin"
    assert data["title"] == "TodoFox"
    # Installed is NOT usable for a plugin: it still needs the user's account.
    assert data["ready"] is False
    assert data["state"] == "not_connected"
    assert data["next_action"] == "connect"
    assert data["plugin"]["id"] == "todo-fox"


@pytest.mark.asyncio
async def test_install_by_name_unknown_entry_404_names_the_closest_match(
    community_env: Path,
) -> None:
    client, _ = _client_with_skills(community_env / "skills")
    async with client:
        resp = await client.post(
            "/api/marketplace/community/install/three-point-chek"
        )
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "three-point-chek" in detail
    assert "three-point-check" in detail
