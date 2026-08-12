"""Community index fetch + cache: fresh/fetched/stale/unavailable/disabled."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from jarvis.marketplace import community_source


def _index_payload(revision: int = 1) -> dict[str, Any]:
    return {
        "revision": revision,
        "generated_at": "2026-08-12T12:00:00Z",
        "plugins": [
            {
                "name": "todo-fox",
                "publisher": "octocat",
                "version": "1.0.0",
                "plugin_json": {"name": "todo-fox", "description": "d"},
                "mcp_json": None,
                "usage_card": "---\nkeywords: todo\n---\nbody",
                "brand_new_field_from_future": True,
            }
        ],
        "skills": [
            {
                "name": "three-point-check",
                "description": "Three bullets",
                "publisher": "octocat",
                "raw_url": "https://raw.example/skills/three-point-check/SKILL.md",
            }
        ],
    }


@pytest.fixture()
def cache_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "marketplace_index.json"
    monkeypatch.setattr(community_source, "_CACHE_PATH", path)
    monkeypatch.setattr(community_source, "index_url", lambda: "https://reg.example/index.json")
    return path


def _transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _seed_cache(path: Path, payload: dict[str, Any], fetched_at: float) -> None:
    path.write_text(json.dumps({"fetched_at": fetched_at, "index": payload}), encoding="utf-8")


def _cache_exists(path: Path) -> bool:
    return path.exists()


def _seed_corrupt_cache(path: Path) -> None:
    path.write_text("{not json", encoding="utf-8")


async def test_fetch_validates_and_caches(cache_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_index_payload())

    index, status = await community_source.get_index(transport=_transport(handler))
    assert status == "fetched"
    assert index is not None
    assert index.plugins[0].name == "todo-fox"
    assert index.skills[0].raw_url is not None
    assert _cache_exists(cache_path)


async def test_within_ttl_serves_cache_without_network(cache_path: Path) -> None:
    _seed_cache(cache_path, _index_payload(revision=7), time.time())

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not fetch inside the TTL")

    index, status = await community_source.get_index(transport=_transport(handler))
    assert status == "fresh"
    assert index is not None and index.revision == 7


async def test_force_refetches_inside_ttl(cache_path: Path) -> None:
    _seed_cache(cache_path, _index_payload(revision=1), time.time())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_index_payload(revision=2))

    index, status = await community_source.get_index(force=True, transport=_transport(handler))
    assert status == "fetched"
    assert index is not None and index.revision == 2


async def test_network_failure_serves_stale_cache(cache_path: Path) -> None:
    _seed_cache(cache_path, _index_payload(revision=3), 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    index, status = await community_source.get_index(transport=_transport(handler))
    assert status == "stale"
    assert index is not None and index.revision == 3


async def test_network_failure_without_cache_is_honest(cache_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    index, status = await community_source.get_index(transport=_transport(handler))
    assert status == "unavailable"
    assert index is None


async def test_empty_url_disables(cache_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(community_source, "index_url", lambda: "")
    index, status = await community_source.get_index()
    assert status == "disabled"
    assert index is None


async def test_corrupt_cache_refetches(cache_path: Path) -> None:
    _seed_corrupt_cache(cache_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_index_payload(revision=9))

    index, status = await community_source.get_index(transport=_transport(handler))
    assert status == "fetched"
    assert index is not None and index.revision == 9


async def test_malformed_index_body_rejected(cache_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"plugins": [{"no_name": True}]})

    index, status = await community_source.get_index(transport=_transport(handler))
    assert status == "unavailable"
    assert index is None


def test_config_default_points_at_registry_pages() -> None:
    from jarvis.core.config import MarketplaceConfig

    assert MarketplaceConfig().community_index_url.startswith("https://")
    assert MarketplaceConfig().community_index_url.endswith("/index.json")
