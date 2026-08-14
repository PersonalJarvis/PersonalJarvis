"""Community wallpapers: feed model, browse payload, one-click import.

Runs against the real FastAPI router with a pre-seeded index cache (no
network — the image download goes through an httpx.MockTransport) and all
data/ paths redirected into tmp, mirroring test_community_routes.py.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from jarvis.marketplace import catalog_data, community_source
from jarvis.marketplace.usage_cards import loader as cards_loader
from jarvis.ui.web import marketplace_routes
from jarvis.ui.web import wallpapers as wallpapers_module
from jarvis.ui.web.marketplace_routes import router


def _wallpaper_entry(name: str = "aurora-drift", **overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": name,
        "title": "Aurora Drift",
        "description": "Northern lights over a quiet fjord",
        "publisher": "octocat",
        "version": "1.0.0",
        "published_at": "2026-08-14T10:00:00Z",
        "theme": "dark",
        "width": 3840,
        "height": 2160,
        "license": "CC0-1.0",
        "source_url": f"https://github.com/PersonalJarvis/marketplace/tree/main/wallpapers/{name}",
        "image_url": f"https://personaljarvis.github.io/marketplace/wallpapers/{name}/wallpaper.webp",
        "thumb_url": f"https://personaljarvis.github.io/marketplace/wallpapers/{name}/thumb.webp",
    }
    entry.update(overrides)
    return entry


def _index_payload(*wallpaper_entries: dict[str, Any]) -> dict[str, Any]:
    return {
        "revision": 7,
        "generated_at": "2026-08-14T12:00:00Z",
        "plugins": [],
        "skills": [],
        "wallpapers": list(wallpaper_entries),
    }


def _seed_cache(cache: Path, index: dict[str, Any]) -> None:
    cache.write_text(
        json.dumps({"fetched_at": time.time(), "index": index}), encoding="utf-8"
    )


def _png_bytes(color: tuple[int, int, int] = (10, 10, 20)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 36), color).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture()
def wallpaper_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect every data/ path into tmp and pre-seed a FRESH index cache."""
    cache = tmp_path / "marketplace_index.json"
    _seed_cache(cache, _index_payload(_wallpaper_entry()))
    monkeypatch.setattr(community_source, "_CACHE_PATH", cache)
    monkeypatch.setattr(community_source, "index_url", lambda: "https://reg.example/index.json")
    monkeypatch.setattr(catalog_data, "_DEFAULT_CATALOG_PATH", tmp_path / "plugin_catalog.json")
    monkeypatch.setattr(cards_loader, "_DATA_CARDS_DIR", tmp_path / "usage_cards")
    monkeypatch.setattr("jarvis.core.paths.user_skills_dir", lambda: tmp_path / "skills")
    # The import lands in the uploads store — keep it inside tmp too.
    monkeypatch.setattr(wallpapers_module, "DATA_DIR", tmp_path)
    catalog_data.clear_cache()
    cards_loader.load_usage_card.cache_clear()
    yield tmp_path
    catalog_data.clear_cache()
    cards_loader.load_usage_card.cache_clear()


@pytest.fixture()
def image_transport(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Serve bytes for the image download; tests set ``state['body']``."""
    state: dict[str, Any] = {"body": _png_bytes(), "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(str(request.url))
        return httpx.Response(200, content=state["body"])

    monkeypatch.setattr(marketplace_routes, "_WALLPAPER_TRANSPORT", httpx.MockTransport(handler))
    return state


def _client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _uploads_dir(tmp_path: Path) -> Path:
    return tmp_path / wallpapers_module.UPLOADS_DIRNAME


@pytest.mark.asyncio
async def test_browse_lists_wallpapers_with_install_state(wallpaper_env: Path) -> None:
    async with _client() as client:
        resp = await client.get("/api/marketplace/community")
    assert resp.status_code == 200
    data = resp.json()
    wallpaper = data["wallpapers"][0]
    assert wallpaper["name"] == "aurora-drift"
    assert wallpaper["title"] == "Aurora Drift"
    assert wallpaper["publisher"] == "octocat"
    assert wallpaper["theme"] == "dark"
    assert wallpaper["installable"] is True
    assert wallpaper["installed"] is False
    assert wallpaper["installed_id"] is None
    assert wallpaper["install"]["cli"] == "jarvis marketplace install aurora-drift"
    assert wallpaper["install"]["runner"].startswith("uvx --from personal-jarvis ")


@pytest.mark.asyncio
async def test_unsafe_wallpaper_names_are_dropped(wallpaper_env: Path) -> None:
    """A traversal-shaped name costs exactly its own entry, never the index."""
    index = _index_payload(
        _wallpaper_entry(),
        _wallpaper_entry(name="../evil"),
        _wallpaper_entry(name="UPPER"),
    )
    _seed_cache(wallpaper_env / "marketplace_index.json", index)
    async with _client() as client:
        resp = await client.get("/api/marketplace/community")
    names = [w["name"] for w in resp.json()["wallpapers"]]
    assert names == ["aurora-drift"]


@pytest.mark.asyncio
async def test_non_https_image_url_degrades_entry(wallpaper_env: Path) -> None:
    """An http image is never fetched — the card says so instead of a button."""
    index = _index_payload(
        _wallpaper_entry(image_url="http://reg.example/wallpaper.webp", thumb_url=None)
    )
    _seed_cache(wallpaper_env / "marketplace_index.json", index)
    async with _client() as client:
        resp = await client.get("/api/marketplace/community")
    wallpaper = resp.json()["wallpapers"][0]
    assert wallpaper["image_url"] is None
    assert wallpaper["installable"] is False
    assert wallpaper["install"] is None


@pytest.mark.asyncio
async def test_install_downloads_reencodes_and_stamps_origin(
    wallpaper_env: Path, image_transport: dict[str, Any]
) -> None:
    async with _client() as client:
        resp = await client.post("/api/marketplace/community/wallpapers/aurora-drift/install")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["already_installed"] is False
    item = body["wallpaper"]
    assert item["origin"] == "community:aurora-drift"
    assert item["title"] == "Aurora Drift"
    # The downloaded PNG was re-encoded: what is on disk is a WebP the app
    # produced, not the bytes the network delivered.
    stored = _uploads_dir(wallpaper_env) / f"{item['id']}.webp"
    raw = stored.read_bytes()
    assert raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
    assert image_transport["requests"] == [
        "https://personaljarvis.github.io/marketplace/wallpapers/aurora-drift/wallpaper.webp"
    ]


@pytest.mark.asyncio
async def test_install_twice_returns_the_existing_copy(
    wallpaper_env: Path, image_transport: dict[str, Any]
) -> None:
    async with _client() as client:
        first = await client.post("/api/marketplace/community/wallpapers/aurora-drift/install")
        second = await client.post("/api/marketplace/community/wallpapers/aurora-drift/install")
        browse = await client.get("/api/marketplace/community")
    assert second.status_code == 200
    assert second.json()["already_installed"] is True
    assert second.json()["wallpaper"]["id"] == first.json()["wallpaper"]["id"]
    # Exactly one image landed on disk, and browse reports the import.
    images = list(_uploads_dir(wallpaper_env).glob("u*.webp"))
    assert len(images) == 1
    wallpaper = browse.json()["wallpapers"][0]
    assert wallpaper["installed"] is True
    assert wallpaper["installed_id"] == first.json()["wallpaper"]["id"]


@pytest.mark.asyncio
async def test_install_unknown_wallpaper_answers_404(
    wallpaper_env: Path, image_transport: dict[str, Any]
) -> None:
    async with _client() as client:
        resp = await client.post("/api/marketplace/community/wallpapers/no-such-thing/install")
    assert resp.status_code == 404
    assert image_transport["requests"] == []


@pytest.mark.asyncio
async def test_install_refuses_bytes_that_are_not_an_image(
    wallpaper_env: Path, image_transport: dict[str, Any]
) -> None:
    """A registry entry serving junk surfaces as the store's own sentence."""
    image_transport["body"] = b"this is not an image at all"
    async with _client() as client:
        resp = await client.post("/api/marketplace/community/wallpapers/aurora-drift/install")
    assert resp.status_code == 400
    assert "not an image" in resp.json()["detail"]
    assert list(_uploads_dir(wallpaper_env).glob("u*.webp")) == []


@pytest.mark.asyncio
async def test_install_prefers_the_registry_theme_call(
    wallpaper_env: Path, image_transport: dict[str, Any]
) -> None:
    """The feed computed light/dark from the full-size original — a local
    guess from the same pixels that disagrees loses."""
    index = _index_payload(_wallpaper_entry(theme="light"))
    _seed_cache(wallpaper_env / "marketplace_index.json", index)
    # A near-black picture: the local heuristic would call it dark.
    image_transport["body"] = _png_bytes((5, 5, 5))
    async with _client() as client:
        resp = await client.post("/api/marketplace/community/wallpapers/aurora-drift/install")
    assert resp.status_code == 200
    assert resp.json()["wallpaper"]["theme"] == "light"
