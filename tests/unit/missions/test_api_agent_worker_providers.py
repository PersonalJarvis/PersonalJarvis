"""B3/B4 (open-source AP-22): the in-process ApiAgentWorker is provider-agnostic
(it drives a BrainProvider in a tool loop), so claude-api and gemini must be
registered too — a user whose single key is an Anthropic or Gemini API key can
then run heavy missions WITHOUT the npm `claude`/`gemini` CLI binary.
"""
from __future__ import annotations

from jarvis.missions.workers.api_agent_worker import (
    _BRAIN_BY_PROVIDER,
    _DEFAULT_MODEL,
    supports_api_agent_worker,
)


def test_claude_api_has_in_process_worker():
    assert supports_api_agent_worker("claude-api") is True
    mod, cls = _BRAIN_BY_PROVIDER["claude-api"]
    assert "claude" in mod and cls.endswith("Brain")


def test_gemini_has_in_process_worker():
    assert supports_api_agent_worker("gemini") is True
    mod, cls = _BRAIN_BY_PROVIDER["gemini"]
    assert "gemini" in mod and cls.endswith("Brain")


def test_every_api_agent_provider_has_a_default_model():
    for provider in _BRAIN_BY_PROVIDER:
        assert _DEFAULT_MODEL.get(provider), f"{provider} has no documented default model"
