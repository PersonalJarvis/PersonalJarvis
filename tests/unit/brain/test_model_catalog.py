"""Tests for jarvis.brain.model_catalog.

The model catalog backs the per-provider model picker in the API-Keys view.
Three concerns:
1. Pure response parsing per provider (Anthropic/OpenAI share the OpenAI
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
    classify_model,
    filter_brain_models,
    is_free_model,
    is_starred_model,
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
        assert {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"} <= {
            m.id for m in spec.curated
        }

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
        # Local "faster-whisper" was removed as a user-selectable STT provider
        # (v1.0.1); the cloud STT providers still each expose a model list.
        for p in ("groq-api", "openai-api", "deepgram"):
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
        # The brain-only CATALOG_PROVIDERS tuple: the live-fetchable API brains.
        for p in (
            "claude-api",
            "openai",
            "gemini",
            "openrouter",
            "grok",
            "nvidia",
        ):
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

    def test_openrouter_extracts_output_modalities(self) -> None:
        payload = {
            "data": [
                {
                    "id": "openai/gpt-5.5",
                    "name": "GPT-5.5",
                    "architecture": {"output_modalities": ["text"]},
                },
                {
                    "id": "openai/gpt-5-image",
                    "name": "Image",
                    "architecture": {"output_modalities": ["image", "text"]},
                },
            ]
        }
        by_id = {m.id: m for m in parse_models_response("openrouter", payload)}
        assert by_id["openai/gpt-5.5"].output_modalities == ("text",)
        assert by_id["openai/gpt-5-image"].output_modalities == ("image", "text")

    def test_missing_architecture_yields_none_modalities(self) -> None:
        # Direct providers (Anthropic/OpenAI /v1/models) return no architecture.
        payload = {"data": [{"id": "gpt-5.5"}]}
        assert parse_models_response("openai", payload)[0].output_modalities is None

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

    def test_frontier_families_rank_above_obscure_models(self) -> None:
        # The user's exact complaint: with OpenRouter's namespaced ids, plain
        # reverse-alphabetical sorting buried GPT/Claude/Gemini below
        # z-ai/qwen/community fine-tunes (whose vendor prefix sorts after "o").
        # The flagship families must come first.
        models = [
            ModelInfo(id="sao10k/l3.3-euryale-70b", label="Euryale"),
            ModelInfo(id="thedrummer/rocinante-12b", label="Rocinante"),
            ModelInfo(id="z-ai/glm-4.5v", label="GLM 4.5V"),
            ModelInfo(id="openai/gpt-5.5", label="GPT-5.5"),
            ModelInfo(id="anthropic/claude-opus-4.8", label="Opus 4.8"),
            ModelInfo(id="google/gemini-3-pro-preview", label="Gemini 3 Pro"),
        ]
        ordered = [m.id for m in sort_models("openrouter", models)]
        assert ordered.index("openai/gpt-5.5") < ordered.index("sao10k/l3.3-euryale-70b")
        assert ordered.index("anthropic/claude-opus-4.8") < ordered.index(
            "thedrummer/rocinante-12b"
        )
        assert ordered.index("google/gemini-3-pro-preview") < ordered.index(
            "z-ai/glm-4.5v"
        )

    def test_newer_version_ranks_above_older_in_same_family(self) -> None:
        models = [
            ModelInfo(id="openai/gpt-5.1", label="x"),
            ModelInfo(id="openai/gpt-5.5", label="y"),
            ModelInfo(id="openai/gpt-5.4", label="z"),
        ]
        ordered = [m.id for m in sort_models("openrouter", models)]
        assert ordered == ["openai/gpt-5.5", "openai/gpt-5.4", "openai/gpt-5.1"]

    def test_main_variant_ranks_above_mini_nano_and_free(self) -> None:
        models = [
            ModelInfo(id="openai/gpt-5.5-mini", label="mini"),
            ModelInfo(id="openai/gpt-5.5", label="main"),
            ModelInfo(id="openai/gpt-5.5-nano", label="nano"),
            ModelInfo(id="openai/gpt-5.5:free", label="free"),
        ]
        ordered = [m.id for m in sort_models("openrouter", models)]
        assert ordered[0] == "openai/gpt-5.5"

    def test_popular_value_family_outranks_unknown_vendor(self) -> None:
        # "Bang per token" families people actually run (DeepSeek, GLM, Qwen,
        # Kimi) rank above an unknown vendor whose prefix sorts high alphabetically.
        models = [
            ModelInfo(id="zzz-vendor/mystery-70b", label="Mystery"),
            ModelInfo(id="deepseek/deepseek-v3.2", label="DeepSeek V3.2"),
            ModelInfo(id="moonshotai/kimi-k2", label="Kimi K2"),
        ]
        ordered = [m.id for m in sort_models("openrouter", models)]
        assert ordered[-1] == "zzz-vendor/mystery-70b"

    def test_superseded_version_sinks_below_other_families_flagship(self) -> None:
        # The "wall of old Claude versions before GPT" fix: only the NEWEST of a
        # product line stays in the top band; older same-line versions drop
        # below every OTHER family's current flagship, so the leading rows show
        # different providers' flagships instead of one provider's back-catalog.
        models = [
            ModelInfo(id="anthropic/claude-opus-4.8", label="Opus 4.8"),
            ModelInfo(id="anthropic/claude-opus-4.7", label="Opus 4.7"),
            ModelInfo(id="anthropic/claude-opus-4.6", label="Opus 4.6"),
            ModelInfo(id="openai/gpt-5.5", label="GPT-5.5"),
        ]
        ordered = [m.id for m in sort_models("openrouter", models)]
        assert ordered[0] == "anthropic/claude-opus-4.8"  # newest Claude = flagship
        assert ordered.index("openai/gpt-5.5") < ordered.index("anthropic/claude-opus-4.7")
        assert ordered.index("openai/gpt-5.5") < ordered.index("anthropic/claude-opus-4.6")
        # The superseded Claude versions still rank above an unknown vendor.
        # (they keep their family relevance in the second band).

    def test_distinct_product_tiers_are_not_treated_as_one_line(self) -> None:
        # gemini flash and pro are different product lines even though the
        # version number sits in the middle — neither must "supersede" the other.
        models = [
            ModelInfo(id="google/gemini-3.5-flash", label="Flash"),
            ModelInfo(id="google/gemini-3.1-pro-preview", label="Pro"),
        ]
        ordered = [m.id for m in sort_models("openrouter", models)]
        # Both are their line's newest → both stay flagship (top band); neither
        # is demoted as "an older version of the other".
        assert set(ordered[:2]) == {"google/gemini-3.5-flash", "google/gemini-3.1-pro-preview"}

    def test_special_purpose_variants_rank_below_the_plain_model(self) -> None:
        # Deep-research / multi-agent / custom-tools are special-purpose siblings,
        # not the default chat brain — the plain model leads each of them.
        models = [
            ModelInfo(id="openai/o3-deep-research", label="o3 DR"),
            ModelInfo(id="openai/o3", label="o3"),
            ModelInfo(id="x-ai/grok-4.3-multi-agent", label="Grok MA"),
            ModelInfo(id="x-ai/grok-4.3", label="Grok"),
            ModelInfo(id="google/gemini-3.1-pro-customtools", label="Gem CT"),
            ModelInfo(id="google/gemini-3.1-pro", label="Gem"),
        ]
        ordered = [m.id for m in sort_models("openrouter", models)]
        assert ordered.index("openai/o3") < ordered.index("openai/o3-deep-research")
        assert ordered.index("x-ai/grok-4.3") < ordered.index("x-ai/grok-4.3-multi-agent")
        assert ordered.index("google/gemini-3.1-pro") < ordered.index(
            "google/gemini-3.1-pro-customtools"
        )

    def test_gpt_5_5_lands_in_leading_slice_against_full_catalog(self) -> None:
        # End-to-end: a realistic mixed catalog (lots of obscure high-alpha
        # vendors) must surface GPT-5.5 within the first handful — the dropdown
        # only shows the leading slice, so rank ~130 (the old bug) hid it.
        obscure = [
            ModelInfo(id=f"zzz-vendor-{i}/model-{i}", label=str(i))
            for i in range(60)
        ]
        models = obscure + [ModelInfo(id="openai/gpt-5.5", label="GPT-5.5")]
        ordered = [m.id for m in sort_models("openrouter", models)]
        assert ordered.index("openai/gpt-5.5") < 10


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
            ModelInfo(id="gpt-realtime-2.1", label="realtime"),
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

    def test_excludes_image_output_model_even_without_name_marker(self) -> None:
        # openrouter/auto outputs text OR image but its id has NO blocklist
        # marker — the old substring filter let it slip through. Modality wins.
        models = [
            ModelInfo(id="openrouter/auto", label="Auto", output_modalities=("text", "image")),
            ModelInfo(id="openai/gpt-5.5", label="GPT", output_modalities=("text",)),
        ]
        assert {m.id for m in filter_brain_models(models)} == {"openai/gpt-5.5"}

    def test_keeps_vision_input_text_output_model(self) -> None:
        # A model that ACCEPTS image input but OUTPUTS text is a valid brain
        # (Computer-Use needs vision-input). It must be kept.
        models = [ModelInfo(id="vendor/vision-llm", label="V", output_modalities=("text",))]
        assert len(filter_brain_models(models)) == 1

    def test_excludes_audio_output_model_by_modality(self) -> None:
        models = [ModelInfo(id="vendor/voice", label="X", output_modalities=("text", "audio"))]
        assert filter_brain_models(models) == []

    def test_excludes_classifier_and_embedding_even_with_text_output(self) -> None:
        # Safety-classifiers (llama-guard) and embedding/rerank/moderation models
        # OUTPUT text but are NOT chat brains. The name blocklist must apply EVEN
        # WHEN modality data is present (regression: modality-only filtering let
        # llama-guard-4-12b / gpt-oss-safeguard-20b slip through with output text).
        models = [
            ModelInfo(id="meta-llama/llama-guard-4-12b", label="Guard", output_modalities=("text",)),
            ModelInfo(id="openai/gpt-oss-safeguard-20b", label="Safeguard", output_modalities=("text",)),
            ModelInfo(id="openai/text-embedding-3-large", label="Embed", output_modalities=("text",)),
            ModelInfo(id="cohere/rerank-3.5", label="Rerank", output_modalities=("text",)),
            ModelInfo(id="openai/gpt-5.5", label="GPT", output_modalities=("text",)),
        ]
        assert {m.id for m in filter_brain_models(models)} == {"openai/gpt-5.5"}

    def test_falls_back_to_substring_when_modalities_absent(self) -> None:
        # No modality data (direct provider /v1/models) → substring blocklist.
        models = [
            ModelInfo(id="veo-3.1-generate", label="Veo"),  # None modalities
            ModelInfo(id="gpt-5.5", label="gpt"),            # None modalities
        ]
        assert {m.id for m in filter_brain_models(models)} == {"gpt-5.5"}


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
    async def test_openrouter_cache_is_capped_at_five_minutes(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        cat = ModelCatalog(cache_path=tmp_path / "c.json", ttl_hours=6)
        cat._cache["openrouter"] = (
            time.time() - 301,
            [ModelInfo(id="openai/gpt-5.5", label="GPT-5.5")],
        )
        calls: list[str] = []

        async def _fetch(provider: str) -> list[ModelInfo]:
            calls.append(provider)
            return [ModelInfo(id="openai/gpt-5.6-sol", label="GPT-5.6 Sol")]

        monkeypatch.setattr(cat, "_fetch_raw", _fetch)
        result = await cat.list_models("openrouter")

        assert calls == ["openrouter"]
        assert result.source == "live"
        assert [m.id for m in result.models] == ["openai/gpt-5.6-sol"]

    @pytest.mark.asyncio
    async def test_fetch_failure_falls_back_to_stale_cache(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        cat = ModelCatalog(cache_path=tmp_path / "c.json", ttl_hours=0)  # all stale
        cat._cache["openrouter"] = (
            time.time() - 99999,
            [ModelInfo(id="openai/gpt-5.5", label="openai/gpt-5.5")],
        )

        async def _fail(provider: str) -> list[ModelInfo]:
            raise RuntimeError("network down")

        monkeypatch.setattr(cat, "_fetch_raw", _fail)
        result = await cat.list_models("openrouter")
        assert result.source == "cache"
        assert result.models[0].id == "openai/gpt-5.5"

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

    def test_catalog_providers_are_the_api_brain_providers(self) -> None:
        # The live-fetchable API brains (each has a /v1/models endpoint). NVIDIA
        # NIM joined as an OpenAI-compatible gateway alongside OpenRouter.
        assert set(CATALOG_PROVIDERS) == {
            "claude-api",
            "openai",
            "gemini",
            "openrouter",
            "grok",
            "nvidia",
        }


# ----------------------------------------------------------------------
# Presentation-only classification — the picker's filter chips + star
# ----------------------------------------------------------------------

class TestModelClassification:
    def test_free_model_detected_by_id_suffix(self) -> None:
        assert is_free_model("nvidia/nemotron-3-ultra:free") is True
        assert is_free_model("openai/gpt-5.5") is False

    def test_free_model_detected_by_label(self) -> None:
        # Some catalogs only mark "(free)" in the human label.
        assert is_free_model("vendor/x", "Vendor X (free)") is True

    def test_starred_matches_across_provider_id_shapes(self) -> None:
        # The same pick is starred whether it is a direct-provider id or an
        # OpenRouter namespaced id — separator/punctuation differences and all.
        assert is_starred_model("claude-opus-4-8") is True
        assert is_starred_model("anthropic/claude-opus-4.8") is True
        # The separate Fast variant is its own starred pick (distinct from base).
        assert is_starred_model("anthropic/claude-opus-4.8-fast") is True
        assert is_starred_model("claude-fable-5") is True
        assert is_starred_model("openai/gpt-5.5") is True
        assert is_starred_model("gemini-3.5-flash") is True
        assert is_starred_model("z-ai/glm-5.2") is True

    def test_starred_is_exact_so_siblings_are_not_starred(self) -> None:
        # Only the named base picks are starred — not their mini/pro siblings.
        assert is_starred_model("openai/gpt-5.5-pro") is False
        assert is_starred_model("openai/gpt-5.5-mini") is False
        assert is_starred_model("z-ai/glm-4.6") is False

    def test_starred_survives_a_free_variant_suffix(self) -> None:
        assert is_starred_model("z-ai/glm-5.2:free") is True

    def test_classify_flagship_is_frontier_not_value(self) -> None:
        tags = classify_model("anthropic/claude-opus-4.8", "Claude Opus 4.8")
        assert tags.frontier is True
        assert tags.value is False
        assert tags.starred is True
        assert tags.free is False

    def test_classify_strong_value_family_is_value_not_frontier(self) -> None:
        tags = classify_model("deepseek/deepseek-v3.2", "DeepSeek V3.2")
        assert tags.value is True
        assert tags.frontier is False

    def test_classify_free_and_value_are_independent(self) -> None:
        # A model can be BOTH a value-band family AND free at once.
        tags = classify_model("z-ai/glm-5.2:free", "Z.AI: GLM 5.2 (free)")
        assert tags.free is True
        assert tags.value is True

    def test_classify_unknown_family_has_no_band(self) -> None:
        tags = classify_model("sao10k/some-community-finetune")
        assert tags.frontier is False
        assert tags.value is False
        assert tags.starred is False


# ----------------------------------------------------------------------
# Realtime catalogs — REALTIME_MODELS / REALTIME_VOICES
#
# Realtime providers need BOTH a model AND a voice selection per provider
# (unlike the single-selection brain/tts/stt catalog), so these two dicts are
# looked up directly by the dedicated /realtime-options endpoint rather than
# being registered into PROVIDER_CATALOG/catalog_spec.
# ----------------------------------------------------------------------


class TestRealtimeCatalog:
    def test_openai_realtime_models_lead_with_the_hardcoded_default(self) -> None:
        from jarvis.brain.model_catalog import REALTIME_MODELS

        ids = [m.id for m in REALTIME_MODELS["openai-realtime"]]
        # Matches _MODEL in jarvis/plugins/realtime/openai_realtime.py — the
        # safe default must lead the list.
        assert ids[0] == "gpt-realtime"
        assert len(ids) == len(set(ids))  # no duplicates
        assert all(m.label for m in REALTIME_MODELS["openai-realtime"])

    def test_gemini_live_models_lead_with_the_hardcoded_default(self) -> None:
        from jarvis.brain.model_catalog import REALTIME_MODELS

        ids = [m.id for m in REALTIME_MODELS["gemini-live"]]
        # Matches _MODEL in jarvis/plugins/realtime/gemini_live.py.
        assert ids[0] == "gemini-3.1-flash-live-preview"
        assert len(ids) == len(set(ids))

    def test_openai_realtime_voices_match_the_ga_voice_set(self) -> None:
        from jarvis.brain.model_catalog import REALTIME_VOICES

        ids = [v.id for v in REALTIME_VOICES["openai-realtime"]]
        assert ids == [
            "alloy",
            "ash",
            "ballad",
            "coral",
            "echo",
            "sage",
            "shimmer",
            "verse",
            "marin",
            "cedar",
        ]

    def test_gemini_live_voices_match_the_prebuilt_voice_set(self) -> None:
        from jarvis.brain.model_catalog import REALTIME_VOICES

        ids = {v.id for v in REALTIME_VOICES["gemini-live"]}
        assert len(ids) == 30
        assert {
            "Puck",
            "Charon",
            "Kore",
            "Fenrir",
            "Aoede",
            "Orus",
            "Leda",
            "Zephyr",
            "Sulafat",
        } <= ids

    def test_realtime_catalogs_are_not_in_the_single_selection_catalog(self) -> None:
        # Realtime is served by its own endpoint (GET/PUT /realtime-options),
        # not the shared /models picker — so it must stay absent from
        # PROVIDER_CATALOG (else /providers/{id}/models would 200 with a
        # single-selection response that can't express model+voice together).
        assert catalog_spec("openai-realtime") is None
        assert catalog_spec("gemini-live") is None
