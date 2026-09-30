"""Tests for the cache-optimized prompt layout (Wave 2 — omni-latency).

The provider prompt cache (Gemini CachedContent / Anthropic cache_control) is
keyed on the system prompt. Per-turn dynamic context (date / wiki) used to be
baked into it and changed every turn, so the cache never hit (measured
baseline: ~9-12 s TTFT despite caching being ON). Wave 2 moves that context
onto the user message so the system prompt stays byte-stable and the cache
actually warms up.
"""
from __future__ import annotations

from jarvis.brain.manager import BrainManager
from jarvis.core.config import load_config

_WIKI = "WIKI_CONTEXT_MARKER: The fictional test user prefers synthwave."


def _manager(*, cache_optimized: bool) -> BrainManager:
    m = BrainManager.__new__(BrainManager)  # bypass heavy __init__
    m._soul = None
    m._user_profile = None
    m._people = None
    m._core_memory = None
    m._system_prompt_extra = "ROUTER DISCIPLINE BLOCK"
    m._wiki_context_suffix = _WIKI
    m._reply_language = "auto"
    cfg = load_config()
    cfg.performance.cache_optimized_prompt = cache_optimized
    m._config = cfg
    return m


def test_cache_optimized_excludes_dynamic_from_system_prompt() -> None:
    m = _manager(cache_optimized=True)
    sp = m._build_system_prompt()
    assert _WIKI not in sp
    # static content is still present
    assert "ROUTER DISCIPLINE BLOCK" in sp


def test_system_prompt_byte_stable_across_wiki_changes() -> None:
    # The caching guarantee: different per-turn context -> identical system prompt.
    m = _manager(cache_optimized=True)
    first = m._build_system_prompt()
    m._wiki_context_suffix = "WIKI_CONTEXT_MARKER: something else entirely."
    second = m._build_system_prompt()
    assert first == second


def test_legacy_mode_keeps_dynamic_in_system_prompt() -> None:
    m = _manager(cache_optimized=False)
    sp = m._build_system_prompt()
    assert _WIKI in sp


def test_turn_context_carries_date_and_wiki() -> None:
    m = _manager(cache_optimized=True)
    ctx = m._build_turn_context()
    assert _WIKI in ctx
    # a date/time stamp is injected (fixes the missing BUG-005 date injection)
    assert "20" in ctx  # year prefix, e.g. 2026


def test_turn_context_empty_in_legacy_mode() -> None:
    m = _manager(cache_optimized=False)
    assert m._build_turn_context() == ""
