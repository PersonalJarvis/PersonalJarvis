"""Shared fixtures for the UltraWiki unit tests."""

from __future__ import annotations

import pytest

import jarvis.ultrawiki.search as search_mod


@pytest.fixture(autouse=True)
def _fresh_query_vector_cache():
    """The query-embedding LRU is process-global; a vector cached by one test
    must never satisfy (or mask) another test's embedding path — that would
    make the suite order-dependent."""
    search_mod._QUERY_VECTOR_CACHE.clear()
    yield
    search_mod._QUERY_VECTOR_CACHE.clear()
