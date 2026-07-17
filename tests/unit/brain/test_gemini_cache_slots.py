"""Multi-slot Gemini context cache (per-turn re-creation fix, 2026-07-17).

Live forensic: the voice manager legitimately varies the tool set per
utterance (screen-tool gating) and the deadline-forced final round strips
tools entirely. With a single cache slot, every flap between those recurring
(system, tools) variants re-created the server-side cache (~1.5 s + billed
storage) — observed as one "Gemini context cache created" per delegated turn
plus a second one inside deadline turns.

Contract under test:
  1. Two alternating (system, tools) variants each create their cache ONCE;
     revisiting a variant reuses its slot (no third create).
  2. The slot map is bounded (oldest evicted beyond _MAX_CACHE_SLOTS).
  3. ``invalidate_cache()`` clears the slots too — after a stale-cache 403
     no dead id can be served from a sibling slot.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.plugins.brain.gemini import _MAX_CACHE_SLOTS, GeminiBrain

_BIG = "X" * (4096 * 4 + 100)  # clears the _MIN_CACHE_TOKENS floor


class _FakeCaches:
    def __init__(self) -> None:
        self.creates = 0

    async def create(self, *, model: str, config: Any) -> Any:
        self.creates += 1
        return SimpleNamespace(name=f"cachedContents/slot-{self.creates}")


def _provider() -> tuple[GeminiBrain, _FakeCaches]:
    provider = GeminiBrain(model="gemini-3.5-flash")
    caches = _FakeCaches()
    provider._client = SimpleNamespace(aio=SimpleNamespace(caches=caches))
    return provider, caches


def _tools(marker: str) -> list[dict[str, Any]]:
    # Shape mirrors _build_gemini_tool_declarations output: genai Tool dicts
    # (CreateCachedContentConfig validates them — raw Anthropic-format tool
    # dicts would fail validation and silently skip the cache).
    return [{"function_declarations": [{"name": marker, "description": _BIG}]}]


@pytest.mark.asyncio
async def test_alternating_variants_reuse_their_slots() -> None:
    provider, caches = _provider()

    name_a1 = await provider._ensure_cache(_BIG, _tools("full-set"))
    name_b1 = await provider._ensure_cache(_BIG, None)  # deadline round: no tools
    name_a2 = await provider._ensure_cache(_BIG, _tools("full-set"))
    name_b2 = await provider._ensure_cache(_BIG, None)

    assert caches.creates == 2, "each recurring variant must be created once"
    assert name_a1 == name_a2
    assert name_b1 == name_b2
    assert name_a1 != name_b1


@pytest.mark.asyncio
async def test_slot_map_is_bounded() -> None:
    provider, caches = _provider()

    for index in range(_MAX_CACHE_SLOTS + 2):
        await provider._ensure_cache(_BIG, _tools(f"variant-{index}"))

    assert caches.creates == _MAX_CACHE_SLOTS + 2
    assert len(provider._cache_slots) == _MAX_CACHE_SLOTS


@pytest.mark.asyncio
async def test_invalidate_cache_drops_all_slots() -> None:
    provider, caches = _provider()

    await provider._ensure_cache(_BIG, _tools("full-set"))
    await provider._ensure_cache(_BIG, None)
    assert provider._cache_slots

    provider.invalidate_cache()

    assert provider._cached_content_name is None
    assert provider._cache_signature is None
    assert not provider._cache_slots
    # The next call must CREATE again, never serve a dead id from a slot.
    await provider._ensure_cache(_BIG, _tools("full-set"))
    assert caches.creates == 3
