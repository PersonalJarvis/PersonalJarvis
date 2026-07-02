"""Computer-Use vision-model rescue: pick_vision_model + the CU chain swap.

CU is screenshot-grounded. A text-only configured model (e.g. an OpenRouter
DeepSeek pin) must not knock its provider out of the CU chain when the same
key unlocks vision-capable siblings — the provider's best vision model is
swapped in for the mission instead (AP-22 "works with whatever key").
"""
from __future__ import annotations

import json

import jarvis.core.config as cfg
from jarvis.brain.model_catalog import pick_vision_model
from jarvis.brain.model_catalog import pick_fast_vision_model
from jarvis.cu.brain_call import _speed_tune_chain


def _write_cache(tmp_path, models, provider="openrouter"):
    (tmp_path / "model_catalog_cache.json").write_text(
        json.dumps({provider: {"fetched_at": 0, "models": models}}),
        encoding="utf-8",
    )


def test_picks_a_vision_model_and_ignores_text_only(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    _write_cache(tmp_path, [
        {"id": "x/text-only", "input_modalities": ["text"]},
        {"id": "x/multi-vl", "input_modalities": ["text", "image"]},
    ])
    assert pick_vision_model("openrouter") == "x/multi-vl"


def test_prefers_flagship_band_over_unknown_family(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    _write_cache(tmp_path, [
        {"id": "tiny/obscure-vl", "input_modalities": ["text", "image"]},
        {
            "id": "anthropic/claude-sonnet-5",
            "input_modalities": ["text", "image", "file"],
        },
    ])
    # The picker's relevance sort puts the known flagship family first — the
    # rescue pick must equal the top row of the vision-filtered dropdown.
    assert pick_vision_model("openrouter") == "anthropic/claude-sonnet-5"


def test_media_generation_models_are_not_picked(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    _write_cache(tmp_path, [
        {
            "id": "google/gemini-3-pro-image",
            "input_modalities": ["image", "text"],
            "output_modalities": ["image"],
        },
    ])
    # Accepts images but GENERATES images -> not a brain; nothing to pick.
    assert pick_vision_model("openrouter") is None


def test_no_modality_data_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    _write_cache(tmp_path, [{"id": "gemini-3.5-flash"}], provider="gemini")
    assert pick_vision_model("gemini") is None


def test_missing_cache_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    assert pick_vision_model("openrouter") is None


# ---------------------------------------------------------------------------
# CU chain rescue
# ---------------------------------------------------------------------------

def test_chain_swaps_blind_model_for_vision_sibling(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    _write_cache(tmp_path, [
        {"id": "deepseek/deepseek-v4-flash", "input_modalities": ["text"]},
        {"id": "qwen/qwen3-vl", "input_modalities": ["text", "image"]},
    ])
    chain = [("openrouter", "deepseek/deepseek-v4-flash")]
    assert _speed_tune_chain(chain) == [("openrouter", "qwen/qwen3-vl")]


def test_chain_keeps_vision_and_unknown_models(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    _write_cache(tmp_path, [
        {"id": "x/multi", "input_modalities": ["text", "image"]},
    ])
    chain = [
        ("openrouter", "x/multi"),      # vision True -> untouched
        ("gemini", "gemini-3.5-flash"),  # unknown -> untouched
        ("claude-api", None),            # no explicit model -> untouched
    ]
    assert _speed_tune_chain(chain) == chain


def test_chain_keeps_blind_model_when_no_sibling_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    _write_cache(tmp_path, [
        {"id": "x/text-a", "input_modalities": ["text"]},
        {"id": "x/text-b", "input_modalities": ["text"]},
    ])
    chain = [("openrouter", "x/text-a")]
    # No vision sibling -> unchanged; the selector's blind-skip handles it.
    assert _speed_tune_chain(chain) == chain


# ---------------------------------------------------------------------------
# Fast-class preference (mission wall-clock is dominated by step THINK time)
# ---------------------------------------------------------------------------

_FAST_CATALOG = [
    {"id": "anthropic/claude-opus-4.8", "input_modalities": ["text", "image"]},
    {"id": "google/gemini-3.5-flash", "input_modalities": ["text", "image"]},
    {"id": "deepseek/deepseek-v4-flash", "input_modalities": ["text"]},
]


def test_fast_pick_prefers_fast_class_over_flagship(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    _write_cache(tmp_path, _FAST_CATALOG)
    assert pick_fast_vision_model("openrouter") == "google/gemini-3.5-flash"


def test_fast_pick_falls_back_to_flagship_without_fast_sibling(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    _write_cache(tmp_path, [
        {"id": "anthropic/claude-opus-4.8", "input_modalities": ["text", "image"]},
    ])
    assert pick_fast_vision_model("openrouter") == "anthropic/claude-opus-4.8"


def test_chain_steps_flagship_down_to_fast_vision(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    _write_cache(tmp_path, _FAST_CATALOG)
    chain = [("openrouter", "anthropic/claude-opus-4.8")]
    assert _speed_tune_chain(chain) == [("openrouter", "google/gemini-3.5-flash")]


def test_chain_respects_an_explicit_cu_pin(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    _write_cache(tmp_path, _FAST_CATALOG)
    chain = [("openrouter", "anthropic/claude-opus-4.8")]
    # The user pinned this model for CU — never second-guess it.
    assert _speed_tune_chain(chain, pinned={"openrouter"}) == chain


def test_chain_keeps_an_already_fast_model(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    _write_cache(tmp_path, _FAST_CATALOG)
    chain = [("openrouter", "google/gemini-3.5-flash")]
    assert _speed_tune_chain(chain) == chain


def test_direct_provider_flagship_falls_back_to_router_tier_default(
    monkeypatch, tmp_path,
):
    # Direct endpoints (gemini/claude-api) expose no modality metadata, so the
    # catalog pick is empty — the provider's curated router-tier default takes
    # over for the mission steps.
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)  # no catalog cache at all
    from jarvis.brain.manager import get_tier_default_model

    expected = get_tier_default_model("router", "gemini")
    assert expected, "router-tier default for gemini must exist"
    chain = [("gemini", "gemini-3-pro")]
    assert _speed_tune_chain(chain) == [("gemini", expected)]


def test_explicit_pin_detection_reads_raw_config_field():
    from types import SimpleNamespace

    from jarvis.cu.brain_call import _explicit_cu_pin

    class FakeManager:
        def __init__(self, cu_model, model):
            self._cfg = SimpleNamespace(cu_model=cu_model, model=model)

        def _provider_cfg(self, name):
            return self._cfg

    # Only the RAW cu_model field counts as a pin — a configured main model
    # must NOT (the resolver falls back to it, which previously made every
    # provider look pinned and disabled the speed tune).
    assert _explicit_cu_pin(FakeManager("x/pinned", "x/main"), "p") == "x/pinned"
    assert _explicit_cu_pin(FakeManager(None, "x/main"), "p") is None
    assert _explicit_cu_pin(FakeManager("", "x/main"), "p") is None
