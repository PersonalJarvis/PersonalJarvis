"""Install-by-name across all three published kinds, and the origin it records.

A marketplace page prints one line to copy for a plugin, a skill, and a
wallpaper alike. These tests pin the part the user actually sees afterwards:
the thing lands in the right store, and it is marked as having come from the
marketplace — which is the only way any view can say so later.

Runs against the real routers with a pre-seeded index cache and every store
redirected into tmp. The network is never touched.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from jarvis.marketplace import catalog_data, community_source
from jarvis.marketplace.usage_cards import loader as cards_loader
from jarvis.ui.web import marketplace_routes
from jarvis.ui.web import wallpapers as wallpapers_mod
from jarvis.ui.web.marketplace_routes import _download_image
from jarvis.ui.web.marketplace_routes import router as market_router
from jarvis.ui.web.skills_routes import router as skills_router

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


def _index_payload() -> dict[str, Any]:
    return {
        "revision": 1,
        "generated_at": "2026-08-16T12:00:00Z",
        "plugins": [],
        "skills": [
            {
                "name": "three-point-check",
                "title": "Three Point Check",
                "description": "Summarize any topic in three bullets",
                "publisher": "octocat",
                "version": "1.0.0",
                "raw_url": "https://raw.example/skills/three-point-check/SKILL.md",
                "source_url": "https://github.com/PersonalJarvis/marketplace",
            }
        ],
        "wallpapers": [
            {
                "name": "moonlit-wave",
                "title": "Moonlit Wave",
                "description": "A dark wave under a full moon",
                "publisher": "octocat",
                "version": "1.0.0",
                # The published registry emits `image_url` + `thumb_url` and
                # leaves `raw_url` null — pinned here because reading the
                # wrong field made every published wallpaper uninstallable.
                "image_url": "https://pages.example/wallpapers/moonlit-wave/wallpaper.webp",
                "thumb_url": "https://pages.example/wallpapers/moonlit-wave/thumb.webp",
                "raw_url": None,
                "license": "CC0-1.0",
                "width": 1920,
                "height": 1080,
                "source_url": "https://github.com/PersonalJarvis/marketplace",
                "theme": "dark",
            }
        ],
    }


def _png_bytes(size: tuple[int, int] = (32, 18)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, (12, 12, 20)).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every store in tmp, one fresh index in the cache, no network."""
    cache = tmp_path / "marketplace_index.json"
    cache.write_text(
        json.dumps({"fetched_at": time.time(), "index": _index_payload()}),
        encoding="utf-8",
    )
    monkeypatch.setattr(community_source, "_CACHE_PATH", cache)
    monkeypatch.setattr(community_source, "index_url", lambda: "https://reg.example/index.json")
    monkeypatch.setattr(catalog_data, "_DEFAULT_CATALOG_PATH", tmp_path / "plugin_catalog.json")
    monkeypatch.setattr(cards_loader, "_DATA_CARDS_DIR", tmp_path / "usage_cards")
    monkeypatch.setattr("jarvis.core.paths.user_skills_dir", lambda: tmp_path / "skills")
    # The picker's own store: DATA_DIR is read when WallpaperUploads is built,
    # so patching the module attribute is enough to relocate it.
    monkeypatch.setattr(wallpapers_mod, "DATA_DIR", tmp_path / "wpdata")
    catalog_data.clear_cache()
    yield tmp_path
    catalog_data.clear_cache()


@pytest.fixture()
def offline_downloads(monkeypatch: pytest.MonkeyPatch, env: Path) -> None:
    """Serve both downloads locally: a SKILL.md and an image."""
    from jarvis.skills.finder import SkillFinder

    async def fake_skill_install(self: Any, candidate: Any) -> Path:
        target_dir = env / "skills" / candidate.name
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "SKILL.md"
        target.write_text(_SKILL_MD, encoding="utf-8")
        return target

    async def fake_image(raw_url: str, limit_bytes: int, **_: Any) -> bytes:
        return _png_bytes()

    monkeypatch.setattr(SkillFinder, "install", fake_skill_install)
    monkeypatch.setattr(marketplace_routes, "_download_image", fake_image)


def _client(env: Path, bus: Any = None) -> httpx.AsyncClient:
    from jarvis.skills.registry import SkillRegistry

    app = FastAPI()
    app.include_router(market_router)
    app.include_router(skills_router)
    skills_root = env / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    registry = SkillRegistry(root=skills_root)
    registry.reload_sync()
    app.state.skill_registry = registry
    # Absent by default, exactly like a headless boot: the install must work
    # with nothing listening.
    if bus is not None:
        app.state.bus = bus
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class _RecordingBus:
    """Captures what the install announced, in order."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


# ----------------------------------------------------------------------
# Wallpapers
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browse_lists_published_wallpapers(env: Path) -> None:
    async with _client(env) as client:
        resp = await client.get("/api/marketplace/community")
    paper = resp.json()["wallpapers"][0]
    assert paper["name"] == "moonlit-wave"
    assert paper["title"] == "Moonlit Wave"
    assert paper["installed"] is False
    # The picture the install would fetch, whichever field the publisher used.
    assert paper["image_url"].endswith("wallpaper.webp")
    assert paper["raw_url"] == paper["image_url"]
    assert paper["thumb_url"].endswith("thumb.webp")


@pytest.mark.asyncio
async def test_the_download_url_comes_from_the_published_field(env: Path) -> None:
    """Regression: the registry emits `image_url`, not `raw_url`.

    Reading only `raw_url` left every published wallpaper with no download at
    all — browsable, and refused the moment anyone pressed install.
    """
    index, _ = await community_source.get_index()
    paper = index.wallpapers[0]
    assert paper.raw_url is None
    assert paper.download_url == paper.image_url


@pytest.mark.asyncio
async def test_a_non_https_image_url_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The server fetches this URL — plain http would be an SSRF primitive."""
    from jarvis.marketplace.community_source import CommunityWallpaperEntry

    entry = CommunityWallpaperEntry(
        name="x", image_url="http://plain.example/a.webp", thumb_url="http://plain.example/t.webp"
    )
    assert entry.image_url is None
    assert entry.download_url is None


@pytest.mark.asyncio
async def test_install_stores_a_wallpaper_with_its_origin(
    env: Path, offline_downloads: None
) -> None:
    from jarvis.ui.web.wallpapers import WallpaperUploads

    async with _client(env) as client:
        resp = await client.post("/api/marketplace/community/install/moonlit-wave")
        assert resp.status_code == 200
        data = resp.json()
        assert data["kind"] == "wallpaper"
        assert data["title"] == "Moonlit Wave"
        # A picture needs no connect step and no validation: usable at once.
        assert data["ready"] is True
        assert data["next_action"] == "none"

        browse = await client.get("/api/marketplace/community")
        assert browse.json()["wallpapers"][0]["installed"] is True

    stored = WallpaperUploads().list()
    assert len(stored) == 1
    item = stored[0]
    assert item.source == "marketplace"
    assert item.origin is not None
    assert item.origin.source_id == "moonlit-wave"
    assert item.origin.publisher == "octocat"
    assert item.to_json()["sourceId"] == "moonlit-wave"
    # Re-encoded on the way in, exactly like a dragged-in file.
    assert item.path.suffix == ".webp"


@pytest.mark.asyncio
async def test_installing_the_same_wallpaper_twice_409s(
    env: Path, offline_downloads: None
) -> None:
    async with _client(env) as client:
        first = await client.post("/api/marketplace/community/install/moonlit-wave")
        assert first.status_code == 200
        second = await client.post("/api/marketplace/community/install/moonlit-wave")
    assert second.status_code == 409
    assert "already" in second.json()["detail"]


@pytest.mark.asyncio
async def test_theme_change_keeps_the_marketplace_origin(
    env: Path, offline_downloads: None
) -> None:
    """Flipping light/dark rewrites the sidecar — the origin must survive it."""
    from jarvis.ui.web.wallpapers import WallpaperUploads

    async with _client(env) as client:
        await client.post("/api/marketplace/community/install/moonlit-wave")
    store = WallpaperUploads()
    installed = store.list()[0]
    store.set_theme(installed.id, "light")
    reread = store.get(installed.id)
    assert reread is not None
    assert reread.theme == "light"
    assert reread.source == "marketplace"
    assert reread.origin is not None
    assert reread.origin.source_id == "moonlit-wave"


@pytest.mark.asyncio
async def test_an_ordinary_upload_stays_marked_as_the_owners_own(env: Path) -> None:
    from jarvis.ui.web.wallpapers import WallpaperUploads

    item = WallpaperUploads().add(_png_bytes(), filename="my-photo.png")
    assert item.source == "own"
    assert item.origin is None
    assert "sourceId" not in item.to_json()


# ----------------------------------------------------------------------
# Skills
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_writes_a_receipt_for_a_skill(
    env: Path, offline_downloads: None
) -> None:
    """A downloaded SKILL.md says nothing about its origin — the receipt does."""
    from jarvis.skills.origin import read_origin

    async with _client(env) as client:
        await client.post("/api/marketplace/community/install/three-point-check")
    origin = read_origin(env / "skills" / "three-point-check")
    assert origin is not None
    assert origin.source == "marketplace"
    assert origin.source_id == "three-point-check"
    assert origin.publisher == "octocat"
    assert origin.installed_at


@pytest.mark.asyncio
async def test_skill_listing_carries_the_origin(env: Path, offline_downloads: None) -> None:
    async with _client(env) as client:
        await client.post("/api/marketplace/community/install/three-point-check")
        listed = await client.get("/api/skills")
    entry = next(s for s in listed.json()["skills"] if s["name"] == "three-point-check")
    assert entry["origin"]["source"] == "marketplace"
    assert entry["origin"]["publisher"] == "octocat"


def test_a_hand_written_skill_has_no_origin(env: Path) -> None:
    from jarvis.skills.origin import read_origin

    folder = env / "skills" / "mine"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    assert read_origin(folder) is None


def test_an_unreadable_receipt_costs_the_badge_not_the_skill(env: Path) -> None:
    from jarvis.skills.origin import RECEIPT_NAME, read_origin

    folder = env / "skills" / "broken-receipt"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(_SKILL_MD, encoding="utf-8")
    (folder / RECEIPT_NAME).write_text("{not json", encoding="utf-8")
    assert read_origin(folder) is None


# ----------------------------------------------------------------------
# The image download itself
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_refuses_an_oversized_image() -> None:
    """The ceiling is enforced mid-stream, not after the body is absorbed."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 5000)

    with pytest.raises(HTTPException) as excinfo:
        await _download_image(
            "https://raw.example/big.png", 1000, transport=httpx.MockTransport(handler)
        )
    assert excinfo.value.status_code == 400
    assert "larger than" in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_download_refuses_a_redirect_off_https() -> None:
    """The index checks the URL it was given; the redirect chain needs it too."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "https":
            return httpx.Response(302, headers={"location": "http://plain.example/x.png"})
        return httpx.Response(200, content=b"never reached")

    with pytest.raises(HTTPException) as excinfo:
        await _download_image(
            "https://raw.example/x.png", 10_000, transport=httpx.MockTransport(handler)
        )
    assert excinfo.value.status_code == 400
    assert "non-https" in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_download_returns_the_bytes_it_was_served() -> None:
    payload = _png_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    got = await _download_image(
        "https://raw.example/ok.png", 10_000_000, transport=httpx.MockTransport(handler)
    )
    assert got == payload


# ----------------------------------------------------------------------
# Telling the open window about it
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_wallpaper_install_announces_itself(
    env: Path, offline_downloads: None
) -> None:
    """An install from a terminal has to reach the window that is already open.

    Nothing about `jarvis marketplace install` touches the desktop UI, so
    without this announcement the picker kept showing the library the picture
    was already in until the app was restarted.
    """
    from jarvis.core.events import MarketplaceItemInstalled

    bus = _RecordingBus()
    async with _client(env, bus) as client:
        resp = await client.post("/api/marketplace/community/install/moonlit-wave")
    assert resp.status_code == 200

    announced = [e for e in bus.events if isinstance(e, MarketplaceItemInstalled)]
    assert len(announced) == 1
    assert announced[0].kind == "wallpaper"
    assert announced[0].item_id == "moonlit-wave"
    # The picture is usable the moment it lands — the frontend uses this to
    # decide whether it can say "done" or has to point at a next step.
    assert announced[0].ready is True


@pytest.mark.asyncio
async def test_a_skill_install_announces_its_own_kind(
    env: Path, offline_downloads: None
) -> None:
    """One event, three kinds: the receiver reloads only the lane that moved."""
    from jarvis.core.events import MarketplaceItemInstalled

    bus = _RecordingBus()
    async with _client(env, bus) as client:
        resp = await client.post("/api/marketplace/community/install/three-point-check")
    assert resp.status_code == 200

    announced = [e for e in bus.events if isinstance(e, MarketplaceItemInstalled)]
    assert len(announced) == 1
    assert announced[0].kind == "skill"
    assert announced[0].item_id == "three-point-check"


@pytest.mark.asyncio
async def test_an_install_still_works_with_nobody_listening(
    env: Path, offline_downloads: None
) -> None:
    """Headless, or early boot: no bus, and the install must not care."""
    async with _client(env) as client:
        resp = await client.post("/api/marketplace/community/install/moonlit-wave")
    assert resp.status_code == 200
    assert resp.json()["ready"] is True
