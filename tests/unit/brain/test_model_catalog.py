"""Tests for jarvis.brain.model_catalog.

The model catalog backs the per-provider model picker in the API-Keys view.
Three concerns:
1. Pure response parsing per provider (Anthropic/OpenAI/Grok share the OpenAI
   ``data[].id`` shape; Gemini uses ``models[].name``; OpenRouter adds a human
   ``name``).
2. Cache TTL — a fresh cache entry skips the network; ``force_refresh`` bypasses.
3. Honest source labelling — live fetch → ``live``; served from a still-fresh
   cache → ``cache``; fetch fails with no usable cache → ``static`` fallback.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from jarvis.brain.model_catalog import (
    CATALOG_PROVIDERS,
    PROVIDER_CATALOG,
    CatalogResult,
    ModelCatalog,
    ModelInfo,
    catalog_spec,
    filter_brain_models,
    parse_models_response,
    sort_models,
)

# ----------------------------------------------------------------------
# Generalized provider catalog — brain/subagent model catalogs + tts + stt
# ----------------------------------------------------------------------

class TestProviderCatalog:
    def test_codex_catalog_is_available_for_subagent_model_picker(self) -> None:
        spec = catalog_spec("codex")
        assert spec is not None
        assert spec.tier == "brain"
        assert spec.selects == "model"
        assert any("gpt-5.5" in m.id for m in spec.curated)

    def test_antigravity_is_curated_brain_provider(self) -> None:
        spec = catalog_spec("antigravity")
        assert spec is not None
        assert spec.tier == "brain"
        assert spec.selects == "model"
        assert spec.live is False  # OAuth CLI has no /v1/models endpoint
        assert "antigravity" not in CATALOG_PROVIDERS  # never live-fetched
        ids = [m.id for m in spec.curated]
        # Flash (the fast default) is offered first; Pro is the deep option.
        assert ids[0] == "gemini-3.5-flash"
        assert "gemini-3.1-pro-preview" in ids
        # No phantom ids that aren't real gemini-CLI models.
        assert "gemini-3-pro" not in ids
        assert "gemini-3-flash-preview" not in ids

    def test_grok_voice_is_a_tts_voice_provider(self) -> None:
        spec = catalog_spec("grok-voice")
        assert spec.tier == "tts"
        assert spec.selects == "voice"
        ids = {m.id for m in spec.curated}
        assert {"leo", "rex", "sal", "ara", "eve"} <= ids

    def test_gemini_tts_lists_charon_voice(self) -> None:
        spec = catalog_spec("gemini-flash-tts")
        assert spec.tier == "tts"
        assert spec.selects == "voice"
        assert any(m.id == "Charon" for m in spec.curated)

    def test_cartesia_selects_a_model(self) -> None:
        spec = catalog_spec("cartesia")
        assert spec.tier == "tts"
        assert spec.selects == "model"
        assert any("sonic" in m.id for m in spec.curated)

    def test_stt_providers_select_a_model(self) -> None:
        for p in ("groq-api", "faster-whisper", "openai-api", "deepgram"):
            spec = catalog_spec(p)
            assert spec is not None, p
            assert spec.tier == "stt"
            assert spec.selects == "model"
            assert spec.curated, p

    def test_unknown_provider_has_no_spec(self) -> None:
        assert catalog_spec("nonexistent") is None

    @pytest.mark.asyncio
    async def test_list_models_for_tts_returns_voices(self, tmp_path: Path) -> None:
        cat = ModelCatalog(cache_path=tmp_path / "c.json")
        result = await cat.list_models("grok-voice")
        assert result.selects == "voice"
        assert any(m.id == "leo" for m in result.models)

    @pytest.mark.asyncio
    async def test_list_models_for_stt_returns_models(self, tmp_path: Path) -> None:
        cat = ModelCatalog(cache_path=tmp_path / "c.json")
        result = await cat.list_models("deepgram")
        assert result.selects == "model"
        assert any("nova" in m.id for m in result.models)

    def test_brain_catalog_providers_unchanged(self) -> None:
        # The brain-only CATALOG_PROVIDERS tuple stays the 5 API brains.
        for p in ("claude-api", "openai", "gemini", "grok", "openrouter"):
            assert p in CATALOG_PROVIDERS
        assert PROVIDER_CATALOG["claude-api"].tier == "brain"


# ----------------------------------------------------------------------
# parse_models_response — pure per-provider parser
# ----------------------------------------------------------------------

class TestParseModelsResponse:
    def test_openai_shape(self) -> None:
        payload = {"data": [{"id": "gpt-5.5"}, {"id": "gpt-5.5-pro"}]}
        models = parse_models_response("openai", payload)
        assert ModelInfo(id="gpt-5.5", label="gpt-5.5") in models
        assert ModelInfo(id="gpt-5.5-pro", label="gpt-5.5-pro") in models

    def test_anthropic_shape(self) -> None:
        payload = {"data": [{"id": "claude-opus-4-8"}, {"id": "claude-haiku-4-5"}]}
        ids = [m.id for m in parse_models_response("claude-api", payload)]
        assert ids == ["claude-opus-4-8", "claude-haiku-4-5"]

    def test_grok_shape(self) -> None:
        payload = {"data": [{"id": "grok-4.3"}]}
        assert parse_models_response("grok", payload) == [
            ModelInfo(id="grok-4.3", label="grok-4.3"),
        ]

    def test_gemini_strips_models_prefix_and_uses_display_name(self) -> None:
        payload = {
            "models": [
                {"name": "models/gemini-3-flash", "displayName": "Gemini 3 Flash"},
                {"name": "models/gemini-3.1-pro-preview"},
            ]
        }
        models = parse_models_response("gemini", payload)
        assert ModelInfo(id="gemini-3-flash", label="Gemini 3 Flash") in models
        # No displayName → label falls back to the id.
        assert ModelInfo(id="gemini-3.1-pro-preview", label="gemini-3.1-pro-preview") in models

    def test_openrouter_uses_human_name_as_label(self) -> None:
        payload = {
            "data": [
                {"id": "anthropic/claude-opus-4.8", "name": "Anthropic: Claude Opus 4.8"},
                {"id": "openai/gpt-5.5"},  # missing name → label = id
            ]
        }
        models = parse_models_response("openrouter", payload)
        assert ModelInfo(
            id="anthropic/claude-opus-4.8", label="Anthropic: Claude Opus 4.8"
        ) in models
        assert ModelInfo(id="openai/gpt-5.5", label="openai/gpt-5.5") in models

    def test_empty_payload_yields_empty_list(self) -> None:
        assert parse_models_response("openai", {}) == []
        assert parse_models_response("gemini", {}) == []

    def test_skips_entries_without_id(self) -> None:
        payload = {"data": [{"id": ""}, {"foo": "bar"}, {"id": "gpt-5.5"}]}
        assert [m.id for m in parse_models_response("openai", payload)] == ["gpt-5.5"]


# ----------------------------------------------------------------------
# sort_models — newest/frontier first, stale demoted
# ----------------------------------------------------------------------

class TestSortModels:
    def test_stale_models_sink_below_frontier(self) -> None:
        models = [
            ModelInfo(id="gpt-4o", label="gpt-4o"),          # stale (EOL)
            ModelInfo(id="gpt-5.5", label="gpt-5.5"),        # frontier
        ]
        ordered = sort_models("openai", models)
        assert ordered[0].id == "gpt-5.5"
        assert ordered[-1].id == "gpt-4o"

    def test_openrouter_stale_detected_by_suffix(self) -> None:
        models = [
            ModelInfo(id="openai/gpt-4o", label="x"),        # stale via suffix
            ModelInfo(id="openai/gpt-5.5", label="y"),       # frontier
        ]
        ordered = sort_models("openrouter", models)
        assert ordered[0].id == "openai/gpt-5.5"
        assert ordered[-1].id == "openai/gpt-4o"


# ----------------------------------------------------------------------
# filter_brain_models — drop non-text / generative-media models
# ----------------------------------------------------------------------

class TestFilterBrainModels:
    def test_drops_gemini_media_and_embedding_models(self) -> None:
        models = [
            ModelInfo(id="gemini-3.5-flash", label="Gemini 3.5 Flash"),
            ModelInfo(id="gemini-3.1-pro-preview", label="Gemini 3.1 Pro"),
            ModelInfo(id="veo-3.1-generate-preview", label="Veo 3.1"),
            ModelInfo(id="imagen-4.0-ultra-generate", label="Imagen 4"),
            ModelInfo(id="lyria-3-pro-preview", label="Lyria 3"),
            ModelInfo(id="nano-banana-pro-preview", label="Nano Banana"),
            ModelInfo(id="gemini-embedding-001", label="Embedding"),
            ModelInfo(id="gemini-2.5-flash-tts", label="Flash TTS"),
            ModelInfo(id="gemini-2.5-flash-image", label="Flash Image"),
        ]
        ids = {m.id for m in filter_brain_models(models)}
        assert "gemini-3.5-flash" in ids
        assert "gemini-3.1-pro-preview" in ids
        assert "veo-3.1-generate-preview" not in ids
        assert "imagen-4.0-ultra-generate" not in ids
        assert "lyria-3-pro-preview" not in ids
        assert "nano-banana-pro-preview" not in ids
        assert "gemini-embedding-001" not in ids
        assert "gemini-2.5-flash-tts" not in ids
        assert "gemini-2.5-flash-image" not in ids

    def test_drops_openai_media_and_audio_models(self) -> None:
        models = [
            ModelInfo(id="gpt-5.5", label="gpt-5.5"),
            ModelInfo(id="gpt-image-1", label="image"),
            ModelInfo(id="dall-e-3", label="dalle"),
            ModelInfo(id="whisper-1", label="whisper"),
            ModelInfo(id="tts-1-hd", label="tts"),
            ModelInfo(id="text-embedding-3-large", label="embed"),
            ModelInfo(id="sora-2", label="sora"),
        ]
        ids = {m.id for m in filter_brain_models(models)}
        assert ids == {"gpt-5.5"}

    def test_keeps_all_anthropic_chat_models(self) -> None:
        models = [
            ModelInfo(id="claude-opus-4-8", label="Opus"),
            ModelInfo(id="claude-haiku-4-5", label="Haiku"),
        ]
        assert len(filter_brain_models(models)) == 2

    def test_keeps_namespaced_openrouter_chat_models(self) -> None:
        models = [
            ModelInfo(id="anthropic/claude-opus-4.8", label="Opus"),
            ModelInfo(id="openai/gpt-5.5", label="gpt"),
        ]
        assert len(filter_brain_models(models)) == 2


# ----------------------------------------------------------------------
# Cache I/O + TTL
# ----------------------------------------------------------------------

class TestCache:
    def test_round_trip(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "model_catalog_cache.json"
        cat = ModelCatalog(cache_path=cache_path)
        cat._cache["gemini"] = (
            time.time(),
            [ModelInfo(id="gemini-3-flash", label="Gemini 3 Flash")],
        )
        cat._save_cache()
        reloaded = ModelCatalog(cache_path=cache_path)
        ts, models = reloaded._cache["gemini"]
        assert models[0].id == "gemini-3-flash"
        assert models[0].label == "Gemini 3 Flash"

    def test_corrupt_cache_is_discarded(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "model_catalog_cache.json"
        cache_path.write_text("{ not json", encoding="utf-8")
        cat = ModelCatalog(cache_path=cache_path)
        assert cat._cache == {}


# ----------------------------------------------------------------------
# list_models — fetch / cache / fallback orchestration
# ----------------------------------------------------------------------

class TestListModels:
    @pytest.mark.asyncio
    async def test_fresh_cache_skips_fetch(self, tmp_path: Path, monkeypatch) -> None:
        cat = ModelCatalog(cache_path=tmp_path / "c.json", ttl_hours=6)
        cat._cache["gemini"] = (
            time.time(),
            [ModelInfo(id="gemini-3-flash", label="Gemini 3 Flash")],
        )

        async def _boom(provider: str) -> list[ModelInfo]:
            raise AssertionError("must not fetch on a fresh cache hit")

        monkeypatch.setattr(cat, "_fetch_raw", _boom)
        result = await cat.list_models("gemini")
        assert isinstance(result, CatalogResult)
        assert result.source == "cache"
        assert result.models[0].id == "gemini-3-flash"

    @pytest.mark.asyncio
    async def test_fetch_success_is_live_and_cached(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        cache_path = tmp_path / "c.json"
        cat = ModelCatalog(cache_path=cache_path, ttl_hours=6)

        async def _fetch(provider: str) -> list[ModelInfo]:
            return [ModelInfo(id="gpt-5.5", label="gpt-5.5")]

        monkeypatch.setattr(cat, "_fetch_raw", _fetch)
        result = await cat.list_models("openai")
        assert result.source == "live"
        assert [m.id for m in result.models] == ["gpt-5.5"]
        # Cache persisted for the next call.
        assert json.loads(cache_path.read_text(encoding="utf-8"))["openai"]

    @pytest.mark.asyncio
    async def test_force_refresh_bypasses_fresh_cache(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        cat = ModelCatalog(cache_path=tmp_path / "c.json", ttl_hours=6)
        cat._cache["openai"] = (time.time(), [ModelInfo(id="old", label="old")])

        async def _fetch(provider: str) -> list[ModelInfo]:
            return [ModelInfo(id="gpt-5.5", label="gpt-5.5")]

        monkeypatch.setattr(cat, "_fetch_raw", _fetch)
        result = await cat.list_models("openai", force_refresh=True)
        assert result.source == "live"
        assert [m.id for m in result.models] == ["gpt-5.5"]

    @pytest.mark.asyncio
    async def test_fetch_failure_falls_back_to_stale_cache(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        cat = ModelCatalog(cache_path=tmp_path / "c.json", ttl_hours=0)  # all stale
        cat._cache["grok"] = (
            time.time() - 99999,
            [ModelInfo(id="grok-4.3", label="grok-4.3")],
        )

        async def _fail(provider: str) -> list[ModelInfo]:
            raise RuntimeError("network down")

        monkeypatch.setattr(cat, "_fetch_raw", _fail)
        result = await cat.list_models("grok")
        assert result.source == "cache"
        assert result.models[0].id == "grok-4.3"

    @pytest.mark.asyncio
    async def test_fetch_failure_no_cache_yields_static(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        cat = ModelCatalog(cache_path=tmp_path / "c.json", ttl_hours=6)

        async def _fail(provider: str) -> list[ModelInfo]:
            raise RuntimeError("no key")

        monkeypatch.setattr(cat, "_fetch_raw", _fail)
        result = await cat.list_models("gemini")
        assert result.source == "static"
        # The static fallback must still surface at least the maintained
        # frontier default so the dropdown is never empty.
        assert any("gemini" in m.id for m in result.models)

    @pytest.mark.asyncio
    async def test_list_models_filters_out_media_models(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        cat = ModelCatalog(cache_path=tmp_path / "c.json", ttl_hours=6)

        async def _fetch(provider: str) -> list[ModelInfo]:
            return [
                ModelInfo(id="gemini-3.5-flash", label="Gemini 3.5 Flash"),
                ModelInfo(id="veo-3.1-generate-preview", label="Veo 3.1"),
                ModelInfo(id="imagen-4.0-ultra", label="Imagen"),
            ]

        monkeypatch.setattr(cat, "_fetch_raw", _fetch)
        result = await cat.list_models("gemini")
        ids = [m.id for m in result.models]
        assert ids == ["gemini-3.5-flash"]

    @pytest.mark.asyncio
    async def test_static_fallback_shows_full_claude_family(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Claude is driven via the Max subscription (no API key), so the live
        # /v1/models fetch always 401s — the picker must still offer the full
        # curated family (Fable / Opus / Sonnet / Haiku), not just 2 defaults.
        cat = ModelCatalog(cache_path=tmp_path / "c.json", ttl_hours=6)

        async def _fail(provider: str) -> list[ModelInfo]:
            raise RuntimeError("401")

        monkeypatch.setattr(cat, "_fetch_raw", _fail)
        result = await cat.list_models("claude-api")
        assert result.source == "static"
        ids = {m.id for m in result.models}
        assert any("fable" in i for i in ids)
        assert any("opus" in i for i in ids)
        assert any("sonnet" in i for i in ids)
        assert any("haiku" in i for i in ids)

    def test_every_catalog_provider_has_a_curated_family(self) -> None:
        from jarvis.brain.model_catalog import CURATED_MODELS

        for provider in CATALOG_PROVIDERS:
            assert CURATED_MODELS.get(provider), f"{provider} needs a curated list"
            # Curated ids must survive the brain-model filter (no media/embeds).
            assert filter_brain_models(CURATED_MODELS[provider]) == CURATED_MODELS[provider]

    def test_catalog_providers_are_the_five_api_brain_providers(self) -> None:
        assert set(CATALOG_PROVIDERS) == {
            "claude-api",
            "openai",
            "gemini",
            "grok",
            "openrouter",
        }
