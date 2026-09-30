"""Regression tests for the factory wire-in.

Wave-4 migration: there used to be two tiers, ``router`` and ``sub_jarvis``,
and ``SUB_TOOLS`` was the tool list for the sub tier. The Sub-Jarvis tier
was replaced by the Jarvis-Agents bridge (see docs/jarvis-agents-bridge.md §11)
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
