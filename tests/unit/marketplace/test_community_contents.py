"""Reading a community entry BEFORE installing it.

The contents route is what makes the "nobody reviewed this" badge actionable:
it hands over the published bytes themselves. These tests pin the three kinds
(plugin manifests, skill text, wallpaper picture), the size ceiling, and the
rule that a failed download degrades to an honest message instead of an
exception.
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
from jarvis.ui.web import marketplace_routes
from jarvis.ui.web.marketplace_routes import router

SKILL_TEXT = "---\nname: three-point-check\n---\n\nSummarize in three bullets.\n"


def _index_payload() -> dict[str, Any]:
    return {
        "revision": 1,
        "generated_at": "2026-08-12T12:00:00Z",
        "plugins": [
            {
                "name": "todo-fox",
                "publisher": "octocat",
                "version": "1.2.0",
                "source_url": "https://github.com/PersonalJarvis/marketplace",
                "plugin_json": {
                    "name": "todo-fox",
                    "description": "Tasks and reminders from TodoFox",
                    "version": "1.2.0",
                    "license": "MIT",
                    "extensions": {
                        EXTENSION_NAMESPACE: {
                            "display_name": "TodoFox",
                            "category": "Lists & Tasks",
                        }
                    },
                },
                "mcp_json": {
                    "mcpServers": {
                        "todo-fox": {
                            "type": "streamable-http",
                            "url": "https://mcp.todofox.example/mcp",
                        }
                    }
                },
            }
        ],
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
        "wallpapers": [
            {
                "name": "rain-antenna-city",
                "title": "Rain Antenna City",
                "description": "Neon rooftops in the rain",
                "publisher": "octocat",
                "raw_url": "https://raw.example/wallpapers/rain-antenna-city.webp",
                "theme": "dark",
            }
        ],
    }


@pytest.fixture()
def community_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh index cache in tmp, and an empty download cache per test."""
    cache = tmp_path / "marketplace_index.json"
    cache.write_text(
        json.dumps({"fetched_at": time.time(), "index": _index_payload()}),
        encoding="utf-8",
    )
    monkeypatch.setattr(community_source, "_CACHE_PATH", cache)
    monkeypatch.setattr(community_source, "index_url", lambda: "https://reg.example/index.json")
    monkeypatch.setattr(catalog_data, "_DEFAULT_CATALOG_PATH", tmp_path / "plugin_catalog.json")
    monkeypatch.setattr("jarvis.core.paths.user_skills_dir", lambda: tmp_path / "skills")
    # Module-global by design (one process-wide download cache) — so a test
    # must never inherit another test's fetch.
    marketplace_routes._content_cache.clear()
    catalog_data.clear_cache()
    yield tmp_path
    marketplace_routes._content_cache.clear()
    catalog_data.clear_cache()


def _client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_plugin_contents_are_the_manifests_verbatim(community_env: Path) -> None:
    """A plugin's manifests need no network — they ride inside the index."""
    async with _client() as client:
        resp = await client.get("/api/marketplace/community/todo-fox/contents")
    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "plugin"
    assert data["root"] == "plugins/todo-fox"
    names = [f["path"] for f in data["files"]]
    assert names == ["plugin.json", "mcp.json"]
    # Pretty-printed and complete: the reader can check the claim the consent
    # dialog makes about where the token goes.
    assert "https://mcp.todofox.example/mcp" in data["files"][1]["text"]
    assert json.loads(data["files"][0]["text"])["name"] == "todo-fox"
    assert data["files"][0]["truncated"] is False


@pytest.mark.asyncio
async def test_skill_contents_download_the_published_text(
    community_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fake(raw_url: str, **_: Any) -> tuple[str, bool]:
        assert raw_url == "https://raw.example/skills/three-point-check/SKILL.md"
        return SKILL_TEXT, False

    monkeypatch.setattr(marketplace_routes, "_download_text", _fake)
    async with _client() as client:
        resp = await client.get("/api/marketplace/community/three-point-check/contents")
    data = resp.json()
    assert data["kind"] == "skill"
    assert data["files"][0]["path"] == "SKILL.md"
    assert data["files"][0]["text"] == SKILL_TEXT
    assert data["files"][0]["size"] == len(SKILL_TEXT.encode("utf-8"))
    assert data["error"] is None


@pytest.mark.asyncio
async def test_unreachable_skill_file_degrades_to_a_message(
    community_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead raw_url must leave the card usable, not raise a 502 at the view."""
    from fastapi import HTTPException

    async def _boom(raw_url: str, **_: Any) -> tuple[str, bool]:
        raise HTTPException(status_code=502, detail="host unreachable")

    monkeypatch.setattr(marketplace_routes, "_download_text", _boom)
    async with _client() as client:
        resp = await client.get("/api/marketplace/community/three-point-check/contents")
    assert resp.status_code == 200
    data = resp.json()
    assert data["files"] == []
    assert "host unreachable" in data["error"]


@pytest.mark.asyncio
async def test_wallpaper_contents_are_the_picture(community_env: Path) -> None:
    async with _client() as client:
        resp = await client.get("/api/marketplace/community/rain-antenna-city/contents")
    data = resp.json()
    assert data["kind"] == "wallpaper"
    assert data["files"] == []
    assert data["image_url"] == "https://raw.example/wallpapers/rain-antenna-city.webp"


@pytest.mark.asyncio
async def test_a_wallpaper_published_as_image_url_still_previews(
    community_env: Path,
) -> None:
    """The published registry emits `image_url`, not `raw_url`.

    Judging the preview by `raw_url` alone told every real wallpaper that it
    "publishes no downloadable image" — while the install, which asks for
    `download_url`, would have fetched it happily.
    """
    payload = _index_payload()
    payload["wallpapers"] = [
        {
            "name": "moonlit-wave",
            "title": "Moonlit Wave",
            "publisher": "octocat",
            "image_url": "https://pages.example/wallpapers/moonlit-wave/wallpaper.webp",
        }
    ]
    community_source._CACHE_PATH.write_text(
        json.dumps({"fetched_at": time.time(), "index": payload}), encoding="utf-8"
    )

    async with _client() as client:
        resp = await client.get("/api/marketplace/community/moonlit-wave/contents")

    data = resp.json()
    assert data["image_url"].endswith("moonlit-wave/wallpaper.webp")
    assert data["error"] is None


@pytest.mark.asyncio
async def test_unknown_entry_404(community_env: Path) -> None:
    async with _client() as client:
        resp = await client.get("/api/marketplace/community/nope/contents")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_download_cuts_oversize_instead_of_refusing() -> None:
    """Half a hostile file is readable evidence; refusing leaves nothing."""
    marketplace_routes._content_cache.clear()
    body = b"x" * (marketplace_routes._MAX_CONTENT_BYTES + 5000)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    text, truncated = await marketplace_routes._download_text(
        "https://raw.example/big.md", transport=transport
    )
    assert truncated is True
    assert len(text) == marketplace_routes._MAX_CONTENT_BYTES
    marketplace_routes._content_cache.clear()


@pytest.mark.asyncio
async def test_download_is_cached_so_reopening_costs_nothing() -> None:
    marketplace_routes._content_cache.clear()
    calls = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=SKILL_TEXT.encode("utf-8"))

    transport = httpx.MockTransport(_handler)
    url = "https://raw.example/cached.md"
    first, _ = await marketplace_routes._download_text(url, transport=transport)
    second, _ = await marketplace_routes._download_text(url, transport=transport)
    assert first == second == SKILL_TEXT
    assert calls == 1
    marketplace_routes._content_cache.clear()
