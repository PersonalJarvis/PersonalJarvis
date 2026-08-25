"""Session policy: the Agents-tier pair, one session per install, an honest refusal."""

from __future__ import annotations

import pytest

from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.core.config import BrainTierConfig, JarvisConfig
from jarvis.local_models import assistant_session as policy


def _cfg(provider: str = "openai", model: str = "gpt-x", **fallback: str) -> JarvisConfig:
    cfg = JarvisConfig()
    cfg.brain.worker = BrainTierConfig(provider=provider, model=model, **fallback)
    return cfg


def _svc() -> AgentChatService:
    return AgentChatService(AgentChatStore(":memory:"))


def test_tier_is_the_worker_pair_and_ready_only_with_a_credential() -> None:
    ready = policy.agents_tier(_cfg(), usable=lambda p: p == "openai")
    assert (ready.provider, ready.model, ready.ready, ready.reason) == ("openai", "gpt-x", True, "")
    blocked = policy.agents_tier(_cfg(), usable=lambda _p: False)
    assert blocked.ready is False and blocked.reason == policy.NOT_READY


def test_unknown_primary_falls_back_to_the_fallback_pair() -> None:
    cfg = _cfg("some-cli-only-thing", "m", fallback_provider="gemini", fallback_model="g-2")
    tier = policy.agents_tier(cfg, usable=lambda _p: True)
    assert (tier.provider, tier.model) == ("gemini", "g-2")


def test_a_cli_only_primary_yields_to_the_tool_capable_fallback() -> None:
    """Antigravity drives a CLI on a flat prompt and drops every tool; the
    assistant needs its lm_* tools, so the chain moves on to a fallback that
    can call them — and says why when none can."""
    cfg = _cfg("antigravity", "gemini-3.5-flash", fallback_provider="gemini", fallback_model="g-2")
    tier = policy.agents_tier(
        cfg, usable=lambda _p: True, tool_capable=lambda p: p != "antigravity"
    )
    assert (tier.provider, tier.model, tier.ready) == ("gemini", "g-2", True)
    stuck = policy.agents_tier(cfg, usable=lambda _p: True, tool_capable=lambda _p: False)
    assert stuck.ready is False and stuck.reason == policy.NO_TOOLS


def test_the_real_antigravity_plugin_reports_no_tools() -> None:
    assert policy._tool_capable("antigravity") is False
    assert policy._tool_capable("gemini") is True


def test_no_worker_tier_is_not_ready() -> None:
    cfg = JarvisConfig()
    cfg.brain.worker = None
    tier = policy.agents_tier(cfg, usable=lambda _p: True)
    assert tier.ready is False and tier.reason == policy.NOT_READY


def test_ensure_session_creates_once_and_recreates_when_the_tier_moves() -> None:
    svc = _svc()
    cfg = _cfg()
    first = policy.ensure_session(svc, cfg, usable=lambda _p: True)
    again = policy.ensure_session(svc, cfg, usable=lambda _p: True)
    assert first.session_id == again.session_id
    assert first.surface == "local-models" and first.provider == "openai"
    assert first.permission_mode == "ask"

    moved = policy.ensure_session(svc, _cfg(model="gpt-y"), usable=lambda _p: True)
    assert moved.session_id != first.session_id and moved.model == "gpt-y"
    assert len(svc.store.list_sessions(surface="local-models")) == 2


def test_ensure_session_refuses_without_the_tier() -> None:
    svc = _svc()
    with pytest.raises(PermissionError, match="Connect the Jarvis Agents tier first"):
        policy.ensure_session(svc, _cfg(), usable=lambda _p: False)
    assert svc.store.list_sessions(surface="local-models") == []


def test_session_state_shape() -> None:
    svc = _svc()
    cfg = _cfg()
    assert policy.session_state(svc, cfg, usable=lambda _p: True) == {
        "session_id": None,
        "surface": "local-models",
        "provider": "openai",
        "model": "gpt-x",
        "ready": True,
        "reason": "",
    }
    session = policy.ensure_session(svc, cfg, usable=lambda _p: True)
    state = policy.session_state(svc, cfg, usable=lambda _p: False)
    assert state["session_id"] == session.session_id
    assert state["ready"] is False and state["reason"] == policy.NOT_READY
