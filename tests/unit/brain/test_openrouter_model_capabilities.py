"""H4/H5 (OpenRouter special case): OpenRouter gateways ~325 models with mixed
capabilities. supports_vision / supports_tools must be resolved PER SELECTED model
(from the cached /v1/models data) — not a fixed class attr — so a text-only or
non-tool model the user picked degrades honestly (CU planner delegates, tool turns
skip) instead of 400-ing the provider. Unknown → capable (no regression).
"""
from __future__ import annotations

import json

import jarvis.core.config as cfg
from jarvis.brain.model_catalog import model_capabilities


def _write_cache(tmp_path, models):
    (tmp_path / "model_catalog_cache.json").write_text(
        json.dumps({"openrouter": {"fetched_at": 0, "models": models}}), encoding="utf-8"
    )


def test_capabilities_text_only_model(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    _write_cache(tmp_path, [{"id": "x/text", "input_modalities": ["text"], "supported_parameters": ["temperature"]}])
    assert model_capabilities("openrouter", "x/text") == {"vision": False, "tools": False}


def test_capabilities_vision_and_tools_model(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    _write_cache(tmp_path, [{"id": "x/multi", "input_modalities": ["text", "image"], "supported_parameters": ["tools", "temperature"]}])
    assert model_capabilities("openrouter", "x/multi") == {"vision": True, "tools": True}


def test_capabilities_unknown_model_is_none(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    _write_cache(tmp_path, [])
    assert model_capabilities("openrouter", "x/missing") == {"vision": None, "tools": None}


def test_openrouter_brain_reflects_text_only_model(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    _write_cache(tmp_path, [{"id": "x/text", "input_modalities": ["text"], "supported_parameters": []}])
    from jarvis.plugins.brain.openrouter import OpenRouterBrain

    b = OpenRouterBrain("x/text")
    assert b.supports_vision is False
    assert b.can_call_tools() is False


def test_openrouter_brain_defaults_capable_when_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    _write_cache(tmp_path, [])
    from jarvis.plugins.brain.openrouter import OpenRouterBrain

    b = OpenRouterBrain("x/unknown-model")
    assert b.supports_vision is True
    assert b.can_call_tools() is True
