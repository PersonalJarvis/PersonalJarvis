"""Regression tests for the factory wire-in.

Wave-4 migration: there used to be two tiers, ``router`` and ``sub_jarvis``,
and ``SUB_TOOLS`` was the tool list for the sub tier. The Sub-Jarvis tier
was replaced by the OpenClaw bridge (see docs/openclaw-bridge.md §11)
— only ``router`` remains; ``SUB_TOOLS`` has been deleted.

Verifies:
- build_default_brain(tier="router") returns a BrainManager with the router tier
- spawn-worker is a tool in the router tool set (ROUTER_TOOLS constant)
- the router system prompt is injected (via _system_prompt_extra)
- JARVIS_BRAIN=legacy escape returns no spawn_worker
"""
from __future__ import annotations

import os

import pytest

from jarvis.brain.factory import ROUTER_TOOLS, build_default_brain


def test_router_tools_has_spawn_worker() -> None:
    assert "spawn-worker" in ROUTER_TOOLS


def test_build_default_brain_router_tier() -> None:
    os.environ.pop("JARVIS_BRAIN", None)
    brain = build_default_brain(tier="router")
    tools = getattr(brain, "_tools", {})
    # AD-OC1 Lazy-Resolver: ``spawn_worker`` is registered unconditionally,
    # even when no MissionManager has been set yet via
    # ``set_mission_manager``. The tool resolves the manager at execute-time,
    # so a post-bootstrap ``set_mission_manager`` becomes visible without
    # rebuilding the Brain. This was the root cause of the silent
    # no-delegation bug fixed on 2026-05-10.
    assert isinstance(tools, dict), "Tools-Dict erwartet"
    assert "spawn_worker" in tools, (
        "spawn_worker must be registered even without MissionManager — "
        "lazy-resolver pattern (AD-OC1)"
    )


def test_router_system_prompt_is_injected() -> None:
    os.environ.pop("JARVIS_BRAIN", None)
    brain = build_default_brain(tier="router")
    extra = getattr(brain, "_system_prompt_extra", "")
    assert any(kw in extra for kw in ("Router", "Delegator", "spawn_worker", "SPAWN")), (
        f"Router system prompt not injected. _system_prompt_extra[:200]: {extra[:200]!r}"
    )


def test_legacy_mode_escape_works() -> None:
    os.environ["JARVIS_BRAIN"] = "legacy"
    try:
        brain = build_default_brain(tier="router")
        tools = getattr(brain, "_tools", {})
        assert "spawn_worker" not in tools, (
            "legacy path must not load spawn_worker"
        )
    finally:
        os.environ.pop("JARVIS_BRAIN", None)


def test_echo_mode_escape_works() -> None:
    os.environ["JARVIS_BRAIN"] = "echo"
    try:
        brain = build_default_brain(tier="router")
        import asyncio
        result = asyncio.run(brain("Hallo"))
        assert result.startswith("Echo:")
    finally:
        os.environ.pop("JARVIS_BRAIN", None)


def test_verdichter_provider_follows_brain_primary_when_non_claude(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-LIVE-04 regression — when `[brain.primary]` is not Claude
    but the Verdichter config still carries its legacy `provider =
    "claude-api"` default, the factory must redirect the verdichter
    brain instantiation to the user's primary provider. Otherwise the
    live log fills with `Your credit balance is too low to access the
    Anthropic API` for an account the user does not own."""
    from jarvis.brain import factory as factory_module
    from jarvis.brain.provider_registry import BrainProviderRegistry
    from jarvis.core import config as config_module

    cfg = config_module.load_config()
    # Stage the precondition: brain.primary points at a non-Claude
    # provider (mirror what BUG-017 left in `jarvis.toml` — Grok).
    monkeypatch.setattr(cfg.brain, "primary", "grok", raising=False)
    # Ensure a `grok` ProviderConfig with a sane `model` exists so the
    # redirect can pick a real model id.
    providers = dict(cfg.brain.providers)
    if "grok" not in providers:
        from jarvis.core.config import BrainProviderConfig

        providers["grok"] = BrainProviderConfig(
            api="grok",
            model="grok-4.3",
            deep_model="grok-4.3",
        )
        monkeypatch.setattr(cfg.brain, "providers", providers, raising=False)
    monkeypatch.setattr(cfg.awareness.verdichter, "provider", "claude-api", raising=False)
    monkeypatch.setattr(cfg.awareness.verdichter, "model", "claude-haiku-4-5-20251001", raising=False)
    monkeypatch.setattr(cfg.awareness.verdichter, "enabled", True, raising=False)
    monkeypatch.setattr(cfg.awareness, "enabled", True, raising=False)

    monkeypatch.setattr(config_module, "load_config", lambda: cfg)

    captured: list[tuple[str, str]] = []

    real_instantiate = BrainProviderRegistry.instantiate

    def fake_instantiate(self, provider: str, *, model: str | None = None, **kw):
        captured.append((provider, model or ""))
        # Return whatever the real registry would have given us for the
        # primary so the rest of the factory build doesn't have to mock
        # an entire Brain.
        if provider == "claude-api":
            raise AssertionError(
                f"factory must NOT call claude-api when brain.primary={cfg.brain.primary!r}"
            )
        return real_instantiate(self, provider, model=model, **kw)

    monkeypatch.setattr(BrainProviderRegistry, "instantiate", fake_instantiate)

    os.environ.pop("JARVIS_BRAIN", None)
    try:
        factory_module.build_default_brain(tier="router")
    except Exception:
        # Factory may still fail downstream (missing credentials etc.) —
        # we only care about the provider-redirect, which fires before
        # any real call.
        pass

    verdichter_calls = [
        (p, m) for (p, m) in captured if p == "grok" and "grok" in m.lower()
    ]
    assert verdichter_calls, (
        f"Expected at least one Verdichter instantiation redirected to grok; "
        f"captured={captured}"
    )
