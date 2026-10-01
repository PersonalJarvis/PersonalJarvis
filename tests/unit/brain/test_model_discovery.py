"""Public model discovery: a login with no catalog of its own still gets new releases.

A subscription login (Claude Code, Vertex) or a missing key leaves a picker on
the curated list; the public OpenRouter feed is what adds a model released
after that list was written (Opus 5.5 arriving while the list ended at Opus 5).
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

from jarvis.brain import model_catalog as catalog_module
from jarvis.brain.model_catalog import (
    ModelCatalog,
    ModelInfo,
    discovered_from_feed,
    merge_discovered,
    native_model_id,
)

# Rows are dated relative to the real clock so the age window holds on any day.
NOW = time.time()
DAY = 86_400.0


def _feed_row(model_id: str, name: str, age_days: float, **extra: Any) -> dict[str, Any]:
    return {"id": model_id, "name": name, "created": int(NOW - age_days * DAY), **extra}


FEED: dict[str, Any] = {
    "data": [
        _feed_row("anthropic/claude-opus-5.5", "Anthropic: Claude Opus 5.5", 1),
        _feed_row("anthropic/claude-opus-5.5:batch", "Anthropic: Claude Opus 5.5 (batch)", 1),
        _feed_row("anthropic/claude-opus-5", "Anthropic: Claude Opus 5", 60),
        _feed_row("anthropic/claude-3-opus", "Anthropic: Claude 3 Opus", 900),
        _feed_row("openai/gpt-6-sol", "OpenAI: GPT-6 Sol", 1),
        _feed_row(
            "openai/gpt-6-image",
            "OpenAI: GPT-6 Image",
            1,
            architecture={"output_modalities": ["image", "text"]},
        ),
        _feed_row("google/gemini-3.8-flash", "Google: Gemini 3.8 Flash", 20),
        _feed_row("x-ai/grok-4.7", "xAI: Grok 4.7", 5),
        _feed_row("deepseek/deepseek-v5", "DeepSeek: V5", 1),
    ]
}


def _feed() -> list[ModelInfo]:
    return catalog_module.parse_models_response("openrouter", FEED)


class _Client:
    """One scripted response per ``get``; records each call."""

    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def get(self, url: str, **_kwargs: Any) -> httpx.Response:
        self.calls.append(url)
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _ok(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, request=httpx.Request("GET", "https://x/models"), json=payload)


def _unauthorized() -> httpx.Response:
    return httpx.Response(401, request=httpx.Request("GET", "https://x/models"), json={})


def test_anthropic_ids_are_mapped_to_the_native_hyphen_spelling() -> None:
    assert native_model_id("claude-api", "claude-opus-5.5") == "claude-opus-5-5"
    assert native_model_id("openai", "gpt-5.6-sol") == "gpt-5.6-sol"
    assert native_model_id("gemini", "gemini-3.8-flash") == "gemini-3.8-flash"


def test_discovery_keeps_recent_chat_models_of_the_one_vendor_newest_first() -> None:
    claude = discovered_from_feed("claude-api", _feed(), now=NOW)

    assert [m.id for m in claude] == ["claude-opus-5-5", "claude-opus-5"]
    assert claude[0].label == "Claude Opus 5.5"


def test_discovery_drops_image_generators_and_other_vendors() -> None:
    assert [m.id for m in discovered_from_feed("openai", _feed(), now=NOW)] == ["gpt-6-sol"]
    assert [m.id for m in discovered_from_feed("gemini", _feed(), now=NOW)] == ["gemini-3.8-flash"]
    assert [m.id for m in discovered_from_feed("grok", _feed(), now=NOW)] == ["grok-4.7"]


def test_a_row_without_a_release_time_is_not_discovered() -> None:
    feed = [ModelInfo(id="anthropic/claude-opus-9", label="Claude Opus 9")]
    assert discovered_from_feed("claude-api", feed, now=NOW) == []


def test_merge_adds_only_what_the_curated_list_lacks_and_leads_with_it() -> None:
    curated = [
        ModelInfo(id="claude-opus-5", label="Claude Opus 5"),
        ModelInfo(id="claude-haiku-4-5-20251001", label="Claude Haiku 4.5"),
    ]
    discovered = [
        ModelInfo(id="claude-opus-5-5", label="Claude Opus 5.5"),
        ModelInfo(id="claude-opus-5", label="Claude Opus 5"),
        # Same model as the dated curated id: matched on the label.
        ModelInfo(id="claude-haiku-4-5", label="Claude Haiku 4.5"),
    ]

    merged = merge_discovered(curated, discovered)

    assert [m.id for m in merged] == [
        "claude-opus-5-5",
        "claude-opus-5",
        "claude-haiku-4-5-20251001",
    ]


def test_a_new_generation_ranks_as_frontier_the_day_it_ships() -> None:
    assert catalog_module._family_rank("gpt-6-sol") == catalog_module._family_rank("gpt-5.5")
    assert catalog_module._family_rank("gemini-4-pro") >= catalog_module.FRONTIER_RANK_FLOOR
    assert catalog_module._family_rank("gpt-4o") < catalog_module.VALUE_RANK_FLOOR


@pytest.mark.asyncio
async def test_keyless_claude_picker_gains_the_new_release_from_the_public_feed(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(catalog_module.cfg, "get_provider_secret", lambda _p: None)
    client = _Client([_ok(FEED)])
    catalog = ModelCatalog(cache_path=tmp_path / "c.json", http_client_factory=lambda: client)

    result = await catalog.list_models("claude-api")

    ids = [m.id for m in result.models]
    assert result.source == "static"
    assert "claude-opus-5-5" in ids
    # The curated floor is still there.
    assert "claude-sonnet-5" in ids
    assert client.calls == ["https://openrouter.ai/api/v1/models"]


@pytest.mark.asyncio
async def test_a_working_live_catalog_is_not_mixed_with_discovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(catalog_module.cfg, "get_provider_secret", lambda _p: "sk-test")
    live = {"data": [{"id": "claude-opus-5", "display_name": "Claude Opus 5"}]}
    client = _Client([_ok(live)])
    catalog = ModelCatalog(cache_path=tmp_path / "c.json", http_client_factory=lambda: client)

    result = await catalog.list_models("claude-api")

    assert result.source == "live"
    assert [m.id for m in result.models] == ["claude-opus-5"]
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_subscription_spec_with_discovery_lists_new_models_before_aliases(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(catalog_module.cfg, "get_provider_secret", lambda _p: None)
    client = _Client([_ok(FEED)])
    catalog = ModelCatalog(cache_path=tmp_path / "c.json", http_client_factory=lambda: client)

    result = await catalog.list_models("claude-cli")

    ids = [m.id for m in result.models]
    assert ids[:2] == ["claude-opus-5-5", "claude-opus-5"]
    assert {"sonnet", "opus", "haiku"} <= set(ids)


@pytest.mark.asyncio
async def test_offline_feed_leaves_the_curated_list_and_is_not_retried_at_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(catalog_module.cfg, "get_provider_secret", lambda _p: None)
    client = _Client([httpx.ConnectError("offline"), httpx.ConnectError("offline")])
    catalog = ModelCatalog(cache_path=tmp_path / "c.json", http_client_factory=lambda: client)

    first = await catalog.list_models("claude-api")
    second = await catalog.list_models("claude-api")

    assert [m.id for m in first.models] == [m.id for m in second.models]
    assert "claude-opus-5-5" in [m.id for m in first.models]  # the curated floor
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_discovered_reads_the_cache_on_disk_without_network(tmp_path) -> None:
    client = _Client([_ok(FEED)])
    first = ModelCatalog(cache_path=tmp_path / "c.json", http_client_factory=lambda: client)
    await first.refresh_discovery()

    reopened = ModelCatalog(cache_path=tmp_path / "c.json", http_client_factory=lambda: None)

    assert "claude-opus-5-5" in [m.id for m in reopened.discovered("claude-api")]


def test_claude_code_picker_leads_with_discovered_releases(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from jarvis.agent_chat import catalog as chat_catalog

    catalog = ModelCatalog(cache_path=tmp_path / "c.json")
    catalog._cache["openrouter"] = (
        NOW,
        [ModelInfo(id="anthropic/claude-opus-6", label="Anthropic: Claude Opus 6", created=NOW)],
    )
    monkeypatch.setattr(catalog_module, "_shared_catalog", catalog)

    models = chat_catalog.claude_code_models()

    assert models[0].id == "claude-opus-6"
    assert models[1:] == chat_catalog.CLAUDE_CODE_MODELS
