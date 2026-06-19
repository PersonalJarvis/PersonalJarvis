"""Curator-tier brain instantiation disables Gemini extended thinking.

Live finding (2026-06-10): Gemini 3 thinking models burn the
``max_output_tokens`` budget on INTERNAL reasoning tokens — the extractor
hit MAX_TOKENS after 110 visible characters and every batch was discarded
by the truncation guard. Background curation is deterministic JSON work
and must run with ``thinking_budget=0`` (mirrors the router fast-path in
``BrainManager``); Gemini PRO models reject budget=0 with a 400, so they
keep the SDK default. Non-Gemini providers never receive the kwarg.
"""
from __future__ import annotations

from typing import Any

from jarvis.memory.wiki.curator_llm import instantiate_curator_brain


class FakeRegistry:
    def __init__(self, *, reject_kwargs: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._reject_kwargs = reject_kwargs

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        self.calls.append((name, dict(kwargs)))
        if self._reject_kwargs and kwargs:
            raise TypeError("unexpected keyword argument")
        return object()


def test_gemini_flash_gets_thinking_budget_zero() -> None:
    registry = FakeRegistry()
    instantiate_curator_brain(registry, "gemini", "gemini-3-flash-preview")
    name, kwargs = registry.calls[0]
    assert name == "gemini"
    assert kwargs["model"] == "gemini-3-flash-preview"
    assert kwargs["thinking_budget"] == 0


def test_gemini_pro_keeps_sdk_default_thinking() -> None:
    registry = FakeRegistry()
    instantiate_curator_brain(registry, "gemini", "gemini-3.1-pro-preview")
    _name, kwargs = registry.calls[0]
    assert "thinking_budget" not in kwargs


def test_non_gemini_provider_never_gets_the_kwarg() -> None:
    registry = FakeRegistry()
    instantiate_curator_brain(registry, "claude-api", "claude-haiku-4-5-20251001")
    _name, kwargs = registry.calls[0]
    assert "thinking_budget" not in kwargs


def test_type_error_falls_back_to_plain_instantiation() -> None:
    """An older provider signature without the kwarg must not break."""
    registry = FakeRegistry(reject_kwargs=True)
    brain = instantiate_curator_brain(registry, "gemini", "gemini-3-flash-preview")
    assert brain is not None
    # Second call retried without any kwargs.
    assert registry.calls[-1][1] == {}


def test_none_model_is_passed_through() -> None:
    registry = FakeRegistry()
    instantiate_curator_brain(registry, "grok", None)
    _name, kwargs = registry.calls[0]
    assert kwargs.get("model") is None
