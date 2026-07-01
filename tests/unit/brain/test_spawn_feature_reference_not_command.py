"""Naming the auto-spawn FEATURE must not be read as a spawn COMMAND.

Live bug 2026-07-01 (voice session 21:26:44). The user made a META complaint
ABOUT the auto-spawn feature (the verbatim utterance is pinned below in
``LIVE_META_UTTERANCE``), not a task. But
``BrainManager._should_force_spawn`` hoists any ``force_spawn_phrases`` match to
an immediate ``True`` ahead of every disambiguation guard, and its
vehicle-trigger regex ``(?:^|\\b)(?:…|spawn|…)(?:\\b|$)`` matches the "Spawn"
inside "Auto-Spawn" (the hyphen is a word boundary). So a full Opus swarm mission
force-spawned — and its bounded "still on it" heartbeats then spoke out of
nowhere for minutes. The irony: complaining about auto-spawn triggered an
auto-spawn.

Fix: ``auto-spawn`` / ``automatic spawn(ing)`` is a feature NAME, never a vehicle
imperative — nobody dispatches a worker by saying "auto-spawn". A dedicated
guard (``_is_spawn_feature_reference``) stands the force-spawn down BEFORE the
negation-blind vehicle hoist, mirroring the existing ``_is_spawn_decline`` guard.
The 2026-06-15 mandate ("when I say subagent it MUST spawn") is preserved: an
explicit imperative ("Spawne einen Subagenten …") carries no "auto"/"automatic"
prefix and still force-spawns.

Deterministic — no LLM, no real brain. Exercises ``_should_force_spawn`` and the
``_is_spawn_feature_reference`` predicate directly.
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig


class _FakeSpawnTool:
    name = "spawn_worker"
    schema: dict[str, Any] = {}


class _FakeDispatchTool:
    name = "dispatch_to_harness"
    schema: dict[str, Any] = {}


class _InertExecutor:
    async def execute(self, *_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("executor must not run in a classification test")


def _manager() -> BrainManager:
    """Strict-mode (production default) manager with the spawn tool wired."""
    config = JarvisConfig()
    config.brain.routing.force_spawn_mode = "strict"
    return BrainManager(
        config=config,
        bus=EventBus(),
        tools={
            "spawn_worker": _FakeSpawnTool(),
            "dispatch_to_harness": _FakeDispatchTool(),
        },
        tool_executor=_InertExecutor(),  # type: ignore[arg-type]
    )


# The exact live utterance that mis-fired (2026-07-01 21:26:44).
LIVE_META_UTTERANCE = (
    "Ja, mach das mal. Ach, Ticker, das ist echt nervig. "  # i18n-allow
    "Auto-Spawn, das müssen wir erstmal fixen, Digga."  # i18n-allow
)


def test_live_auto_spawn_complaint_does_not_force_spawn() -> None:
    """The reported bug: complaining about auto-spawn must NOT force-spawn."""
    manager = _manager()
    assert manager._should_force_spawn(LIVE_META_UTTERANCE) is False, (
        "a complaint ABOUT the auto-spawn feature was read as a spawn command "
        "— the 'Spawn' inside 'Auto-Spawn' hoisted to a force-spawn"
    )


# Talking ABOUT the feature (naming / complaining / asking to fix), DE/EN.
_FEATURE_REFERENCES = [
    "Auto-Spawn, das müssen wir fixen",  # i18n-allow
    "Das Auto-Spawn nervt total",  # i18n-allow
    "auto spawn triggert dauernd ungewollt",  # i18n-allow
    "autospawn is broken",
    "can you fix the automatic spawning",
]


@pytest.mark.parametrize("utterance", _FEATURE_REFERENCES)
def test_feature_reference_predicate_matches(utterance: str) -> None:
    from jarvis.brain.manager import _is_spawn_feature_reference

    assert _is_spawn_feature_reference(utterance) is True


@pytest.mark.parametrize("utterance", _FEATURE_REFERENCES)
def test_feature_reference_does_not_force_spawn(utterance: str) -> None:
    manager = _manager()
    assert manager._should_force_spawn(utterance) is False, (
        f"feature reference {utterance!r} was wrongly force-spawned"
    )


# The 2026-06-15 mandate must survive: an explicit imperative that NAMES the
# vehicle still force-spawns. None of these carries an "auto"/"automatic" prefix.
_EXPLICIT_COMMANDS = [
    "Spawne einen Subagenten der die Logs analysiert",  # i18n-allow
    "Delegier das an einen Subagenten",  # i18n-allow
    "Starte OpenClaw und bau mir einen ausführlichen Report",  # i18n-allow
]


@pytest.mark.parametrize("utterance", _EXPLICIT_COMMANDS)
def test_explicit_spawn_command_still_force_spawns(utterance: str) -> None:
    manager = _manager()
    assert manager._should_force_spawn(utterance) is True, (
        f"explicit vehicle command {utterance!r} must still force-spawn "
        "(mandate 2026-06-15)"
    )


@pytest.mark.parametrize("utterance", _EXPLICIT_COMMANDS)
def test_explicit_command_is_not_a_feature_reference(utterance: str) -> None:
    from jarvis.brain.manager import _is_spawn_feature_reference

    assert _is_spawn_feature_reference(utterance) is False
