"""Tests for jarvis.brain.frontier_resolver.

Three areas of focus:
1. Provider picker (deterministic, against static model lists).
2. Cache TTL (fresh = no fetch, stale = fetch).
3. HTTP failure → cache fallback / None.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from jarvis.brain.frontier_resolver import (
    FrontierModel,
    FrontierResolver,
    _pick_anthropic,
    _pick_gemini,
    _pick_openai,
)


# ----------------------------------------------------------------------
# Provider-Picker
# ----------------------------------------------------------------------

class TestPickAnthropic:
    def test_fast_picks_newest_haiku(self) -> None:
        models = [
            "claude-haiku-3-5",
            "claude-haiku-4-5-20251001",
            "claude-haiku-4-6",
            "claude-opus-4-7",
            "claude-sonnet-4-6",
        ]
        assert _pick_anthropic(models, "fast") == "claude-haiku-4-6"

    def test_deep_picks_newest_opus(self) -> None:
        models = [
            "claude-opus-4-6",
            "claude-opus-4-7",
            "claude-opus-3-5",
            "claude-haiku-4-5",
        ]
        assert _pick_anthropic(models, "deep") == "claude-opus-4-7"

    def test_no_match_returns_none(self) -> None:
        # Only Sonnet models, no Haiku/Opus.
        models = ["claude-sonnet-4-6", "claude-sonnet-4-5"]
        assert _pick_anthropic(models, "fast") is None
        assert _pick_anthropic(models, "deep") is None


class TestPickGemini:
    def test_fast_picks_newest_flash(self) -> None:
        models = [
            "gemini-2.5-flash",
            "gemini-3-flash",
            "gemini-3-pro",
            "gemini-3.1-pro-preview",
        ]
        assert _pick_gemini(models, "fast") == "gemini-3-flash"

    def test_deep_picks_newest_pro_preview_over_old_ga(self) -> None:
        # 3.1-pro-preview > 3-pro (3.1 > 3)
        models = [
            "gemini-2.5-pro",
            "gemini-3-pro",
            "gemini-3.1-pro-preview",
        ]
        assert _pick_gemini(models, "deep") == "gemini-3.1-pro-preview"

    def test_deep_excludes_lite(self) -> None:
        models = [
            "gemini-3-pro",
            "gemini-3.1-pro-lite",  # synthetisch — falls Google das releast
        ]
        # Should pick 3-pro, not the lite one.
        assert _pick_gemini(models, "deep") == "gemini-3-pro"

    def test_fast_excludes_pro(self) -> None:
        models = [
            "gemini-3-flash",
            "gemini-3-pro",
        ]
        assert _pick_gemini(models, "fast") == "gemini-3-flash"


class TestPickOpenAI:
    def test_fast_picks_newest_non_pro(self) -> None:
        models = [
            "gpt-4o",
            "gpt-5",
            "gpt-5.5",
            "gpt-5.5-pro",
            "gpt-5-mini",
            "gpt-5-nano",
        ]
        assert _pick_openai(models, "fast") == "gpt-5.5"

    def test_deep_picks_pro(self) -> None:
        models = [
            "gpt-4o",
            "gpt-5",
            "gpt-5.5-pro",
            "gpt-5-pro",
        ]
        assert _pick_openai(models, "deep") == "gpt-5.5-pro"

    def test_fast_excludes_preview(self) -> None:
        models = ["gpt-5.5", "gpt-6-preview"]
        assert _pick_openai(models, "fast") == "gpt-5.5"


# ----------------------------------------------------------------------
# Cache-TTL
# ----------------------------------------------------------------------

class TestCache:
    def test_load_cache_from_disk(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "frontier_cache.json"
        cache_path.write_text(json.dumps({
            "gemini": {
                "fast": {
                    "provider": "gemini",
                    "tier": "fast",
                    "model_id": "gemini-3-flash",
                    "fetched_at": time.time(),
                },
            },
        }))
        resolver = FrontierResolver(cache_path=cache_path, ttl_hours=24)
        cached = resolver._cache["gemini"]["fast"]
        assert cached.model_id == "gemini-3-flash"

    def test_load_cache_handles_corrupt_json(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "frontier_cache.json"
        cache_path.write_text("{ this is not json")
        resolver = FrontierResolver(cache_path=cache_path)
        assert resolver._cache == {}

    def test_save_cache_persists_to_disk(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "frontier_cache.json"
        resolver = FrontierResolver(cache_path=cache_path)
        resolver._cache["openai"] = {
            "fast": FrontierModel(
                provider="openai", tier="fast",
                model_id="gpt-5.5", fetched_at=time.time(),
            ),
        }
        resolver._save_cache()
        loaded = json.loads(cache_path.read_text())
        assert loaded["openai"]["fast"]["model_id"] == "gpt-5.5"

    def test_is_fresh_within_ttl(self, tmp_path: Path) -> None:
        resolver = FrontierResolver(cache_path=tmp_path / "c.json", ttl_hours=24)
        fresh = FrontierModel(
            provider="x", tier="fast", model_id="m",
            fetched_at=time.time(),
        )
        assert resolver._is_fresh(fresh) is True

    def test_is_stale_beyond_ttl(self, tmp_path: Path) -> None:
        resolver = FrontierResolver(cache_path=tmp_path / "c.json", ttl_hours=1)
        stale = FrontierModel(
            provider="x", tier="fast", model_id="m",
            fetched_at=time.time() - 7200,  # 2h alt, ttl 1h
        )
        assert resolver._is_fresh(stale) is False


# ----------------------------------------------------------------------
# resolve_latest mit gemockten _fetch_models
# ----------------------------------------------------------------------

class TestResolveLatest:
    @pytest.mark.asyncio
    async def test_unknown_provider_returns_none(self, tmp_path: Path) -> None:
        resolver = FrontierResolver(cache_path=tmp_path / "c.json")
        result = await resolver.resolve_latest("nonexistent", "fast")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_hit_skips_fetch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        resolver = FrontierResolver(cache_path=tmp_path / "c.json")
        # Cache hit for (gemini, fast)
        resolver._cache["gemini"] = {
            "fast": FrontierModel(
                provider="gemini", tier="fast",
                model_id="gemini-3-flash", fetched_at=time.time(),
            ),
        }

        called = {"count": 0}

        async def _fail_fetch(provider: str) -> list[str]:
            called["count"] += 1
            raise RuntimeError("Should not be called on cache hit")

        monkeypatch.setattr(resolver, "_fetch_models", _fail_fetch)
        result = await resolver.resolve_latest("gemini", "fast")
        assert result == "gemini-3-flash"
        assert called["count"] == 0

    @pytest.mark.asyncio
    async def test_fetch_failure_falls_back_to_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        resolver = FrontierResolver(
            cache_path=tmp_path / "c.json", ttl_hours=0,  # alle stale
        )
        # Stale cache entry
        resolver._cache["gemini"] = {
            "fast": FrontierModel(
                provider="gemini", tier="fast",
                model_id="gemini-3-flash",
                fetched_at=time.time() - 86400,  # 1 Tag alt
            ),
        }

        async def _fail_fetch(provider: str) -> list[str]:
            raise httpx_RequestError()  # Provider down

        monkeypatch.setattr(resolver, "_fetch_models", _fail_fetch)
        result = await resolver.resolve_latest("gemini", "fast")
        # Trotz Fetch-Fail: Cache liefert.
        assert result == "gemini-3-flash"

    @pytest.mark.asyncio
    async def test_fetch_success_updates_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        resolver = FrontierResolver(cache_path=tmp_path / "c.json")

        async def _fake_fetch(provider: str) -> list[str]:
            return ["gemini-2.5-flash", "gemini-3-flash"]

        monkeypatch.setattr(resolver, "_fetch_models", _fake_fetch)
        result = await resolver.resolve_latest("gemini", "fast")
        assert result == "gemini-3-flash"
        # Cache geupdatet
        assert resolver._cache["gemini"]["fast"].model_id == "gemini-3-flash"


# Synthetic exception type for the HTTP failure test.
class httpx_RequestError(Exception):
    pass


def test_event_loop_fixture_works() -> None:
    """Sanity check: sync test runs (no async-fixture issue)."""
    assert asyncio.iscoroutinefunction(FrontierResolver.resolve_latest)
