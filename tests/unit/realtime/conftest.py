"""Shared fixtures for the realtime test tree."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_local_server_singletons():
    """Start every test with fresh local-server module state.

    The install engine keeps a module-singleton progress state and the
    supervisor keeps a spawn rate-limit stamp. Without this reset, whichever
    test spawned first would rate-limit every later spawn test in the same
    pytest session, and an install driven to "error" would leak that phase
    into unrelated tests.
    """
    from jarvis.plugins.realtime.openai_realtime import LocalRealtimeProvider
    from jarvis.realtime.local_server import install, supervisor

    install._reset_for_tests()
    supervisor._reset_for_tests()
    LocalRealtimeProvider._client_cache.clear()
    LocalRealtimeProvider._model_cache.clear()
    yield
    install._reset_for_tests()
    supervisor._reset_for_tests()
    LocalRealtimeProvider._client_cache.clear()
    LocalRealtimeProvider._model_cache.clear()
