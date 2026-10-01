"""Install-by-name across the published kinds, and the origin it records.

A marketplace page prints one line to copy for a plugin and a skill alike.
These tests pin the part the user actually sees afterwards:
the thing lands in the right store, and it is marked as having come from the
marketplace — which is the only way any view can say so later.

Runs against the real routers with a pre-seeded index cache and every store
redirected into tmp. The network is never touched.
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
from jarvis.marketplace.usage_cards import loader as cards_loader
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
        # Wallpapers were retired as a marketplace kind. A registry build from
        # before that may still publish the section, so the fixture keeps one
        # entry to prove the app ignores it.
        "wallpapers": [
            {
                "name": "moonlit-wave",
                "title": "Moonlit Wave",
                "image_url": "https://pages.example/wallpapers/moonlit-wave/wallpaper.webp",
            }
        ],
    }


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
    catalog_data.clear_cache()
    yield tmp_path
    catalog_data.clear_cache()


@pytest.fixture()
def offline_downloads(monkeypatch: pytest.MonkeyPatch, env: Path) -> None:
    """Serve the SKILL.md download locally."""
    from jarvis.skills.finder import SkillFinder

    async def fake_skill_install(self: Any, candidate: Any) -> Path:
        target_dir = env / "skills" / candidate.name
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "SKILL.md"
        target.write_text(_SKILL_MD, encoding="utf-8")
        return target

    monkeypatch.setattr(SkillFinder, "install", fake_skill_install)


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
# Wallpapers are no longer a published kind
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_feed_that_still_lists_wallpapers_is_browsed_without_them(env: Path) -> None:
    """An older registry build may still carry `wallpapers`; the app ignores it."""
    async with _client(env) as client:
        resp = await client.get("/api/marketplace/community")
    assert resp.status_code == 200
    body = resp.json()
    assert "wallpapers" not in body
    assert [s["name"] for s in body["skills"]] == ["three-point-check"]


@pytest.mark.asyncio
async def test_a_wallpaper_name_is_not_installable(env: Path) -> None:
    async with _client(env) as client:
        resp = await client.post("/api/marketplace/community/install/moonlit-wave")
    assert resp.status_code == 404


# ----------------------------------------------------------------------
# Skills
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_writes_a_receipt_for_a_skill(env: Path, offline_downloads: None) -> None:
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
# Telling the open window about it
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_skill_install_announces_its_own_kind(env: Path, offline_downloads: None) -> None:
    """One event per install: the receiver reloads only the lane that moved.

    An install from a terminal has to reach the window that is already open —
    nothing about `jarvis marketplace install` touches the desktop UI.
    """
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
        resp = await client.post("/api/marketplace/community/install/three-point-check")
    assert resp.status_code == 200
    assert resp.json()["kind"] == "skill"
