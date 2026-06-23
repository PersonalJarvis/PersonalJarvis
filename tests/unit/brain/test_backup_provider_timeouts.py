"""Wave-3 latency fix: bound the openai-SDK backup providers' read timeout.

``openai``/``openrouter`` are built on the openai SDK, whose DEFAULT read
timeout is 600 s. A hung backup provider on the fallback chain could hold the
brain coroutine far longer than intended. Cap read at 30 s (well under the
brain stall guard) while keeping connect snappy (5 s) for fast-fail on a dead
endpoint.
"""
from __future__ import annotations

import pytest

import jarvis.core.config as cfg
from jarvis.plugins.brain.openai import OpenAIBrain
from jarvis.plugins.brain.openrouter import OpenRouterBrain


@pytest.mark.parametrize("brain_cls", [OpenAIBrain, OpenRouterBrain])
def test_backup_provider_client_has_bounded_timeout(brain_cls, monkeypatch) -> None:
    monkeypatch.setattr(cfg, "get_provider_secret", lambda *a, **k: "test-key")
    brain = brain_cls()
    client = brain._ensure_client()
    # The openai SDK exposes the configured timeout on ``.timeout`` as an
    # httpx.Timeout. read must be capped (was 600 s default), connect snappy.
    assert client.timeout.read == 30.0, "read timeout must be capped to 30s"
    assert client.timeout.connect == 5.0, "connect timeout stays at 5s for fast-fail"
