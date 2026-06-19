"""Tests for the cache-optimized prompt layout (Wave 2 — omni-latency).

The provider prompt cache (Gemini CachedContent / Anthropic cache_control) is
keyed on the system prompt. Today awareness + wiki are baked into it and change
every turn, so the cache never hits (measured baseline: ~9-12 s TTFT despite
caching being ON). Wave 2 moves the per-turn dynamic context (date / awareness /
wiki) onto the user message so the system prompt stays byte-stable and the
cache actually warms up.
"""
from __future__ import annotations

from jarvis.brain.manager import BrainManager
from jarvis.core.config import load_config

_AWARE_A = "Active window: Code.exe — main.py"
_AWARE_B = "Active window: chrome.exe — youtube.com"
_WIKI = "WIKI_CONTEXT_MARKER: Ruben prefers synthwave."


class _FakeAwarenessState:
    def __init__(self, snap: str) -> None:
        self.snap = snap

    def snapshot_for_prompt(self, max_chars: int = 600) -> str:
        return self.snap


class _FakeAwareness:
    def __init__(self, snap: str) -> None:
        self.state = _FakeAwarenessState(snap)


def _manager(*, cache_optimized: bool, snap: str) -> BrainManager:
    m = BrainManager.__new__(BrainManager)  # bypass heavy __init__
    m._soul = None
    m._user_profile = None
    m._people = None
    m._core_memory = None
    m._awareness_manager = _FakeAwareness(snap)
    m._system_prompt_extra = "ROUTER DISCIPLINE BLOCK"
    m._wiki_context_suffix = _WIKI
    m._reply_language = "auto"
    cfg = load_config()
    cfg.performance.cache_optimized_prompt = cache_optimized
    m._config = cfg
    return m


def test_cache_optimized_excludes_dynamic_from_system_prompt() -> None:
    m = _manager(cache_optimized=True, snap=_AWARE_A)
    sp = m._build_system_prompt()
    assert _AWARE_A not in sp
    assert _WIKI not in sp
    # static content is still present
    assert "ROUTER DISCIPLINE BLOCK" in sp


def test_system_prompt_byte_stable_across_awareness_changes() -> None:
    # The caching guarantee: different awareness state -> identical system prompt.
    m = _manager(cache_optimized=True, snap=_AWARE_A)
    first = m._build_system_prompt()
    m._awareness_manager = _FakeAwareness(_AWARE_B)
    m._wiki_context_suffix = "WIKI_CONTEXT_MARKER: something else entirely."
    second = m._build_system_prompt()
    assert first == second


def test_legacy_mode_keeps_dynamic_in_system_prompt() -> None:
    m = _manager(cache_optimized=False, snap=_AWARE_A)
    sp = m._build_system_prompt()
    assert _AWARE_A in sp
    assert _WIKI in sp


def test_turn_context_carries_date_awareness_and_wiki() -> None:
    m = _manager(cache_optimized=True, snap=_AWARE_A)
    ctx = m._build_turn_context()
    assert _AWARE_A in ctx
    assert _WIKI in ctx
    # a date/time stamp is injected (fixes the missing BUG-005 date injection)
    assert "20" in ctx  # year prefix, e.g. 2026


def test_turn_context_empty_in_legacy_mode() -> None:
    m = _manager(cache_optimized=False, snap=_AWARE_A)
    assert m._build_turn_context() == ""
