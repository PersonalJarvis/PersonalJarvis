"""Explicit "do it on screen / in the browser" must reach Computer-Use, reliably.

User pain (2026-06-21): "mach es am Bildschirm" / "do it on screen" sometimes
spawned a sub-agent mission or didn't reliably reach Computer-Use, because the
screen surface ("Bildschirm" / "screen") was not recognized as a pc-control
signal at all. This fix makes an explicit screen request:

* never force-spawn a sub-agent (a worker has no desktop), AND
* count as an action turn so a tool-incapable talker (Antigravity/Codex CLI)
  delegates it to a tool-capable provider, which picks computer_use.

It must NOT hijack a build-a-deliverable request that merely mentions the screen
("build me a website and show it on screen" still spawns a mission), and it must
not over-trigger on a knowledge question.

Deterministic — no LLM, no real brain.
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.brain.manager import BrainManager, _looks_like_pc_control
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig


class _FakeSpawn:
    name = "spawn_worker"
    schema: dict[str, Any] = {}


class _FakeDispatch:
    name = "dispatch_to_harness"
    schema: dict[str, Any] = {}


class _Inert:
    async def execute(self, *_a: Any, **_k: Any) -> Any:  # pragma: no cover
        raise AssertionError("no exec in a classification test")


def _manager() -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.routing.force_spawn_mode = "strict"
    return BrainManager(
        config=cfg,
        bus=EventBus(),
        tools={"spawn_worker": _FakeSpawn(), "dispatch_to_harness": _FakeDispatch()},
        tool_executor=_Inert(),  # type: ignore[arg-type]
    )


_ON_SCREEN = [
    "Mach das für mich am Bildschirm",  # i18n-allow
    "Mach es auf dem Bildschirm",  # i18n-allow
    "Bediene das am Bildschirm für mich",  # i18n-allow
    "Do it on screen for me",
    "On my screen, find the latest post",
]


@pytest.mark.parametrize("utterance", _ON_SCREEN)
def test_explicit_screen_is_recognised_as_pc_control(utterance: str) -> None:
    """The screen surface must register as a pc-control signal (so the turn is an
    action turn → delegated to a tool-capable provider → computer_use)."""
    assert _looks_like_pc_control(utterance) is True, (
        f"explicit screen request {utterance!r} not recognised as pc-control"
    )


@pytest.mark.parametrize("utterance", _ON_SCREEN)
def test_explicit_screen_is_an_action_turn(utterance: str) -> None:
    assert _manager()._turn_has_action_intent(utterance) is True, (
        f"explicit screen request {utterance!r} not flagged as an action turn"
    )


@pytest.mark.parametrize("utterance", _ON_SCREEN)
def test_explicit_screen_never_force_spawns(utterance: str) -> None:
    assert _manager()._should_force_spawn(utterance) is False, (
        f"explicit screen request {utterance!r} wrongly force-spawned a worker"
    )


def test_build_and_show_on_screen_stays_inline_without_trigger() -> None:
    """A build-a-deliverable request that mentions the screen no longer
    force-spawns implicitly (mandate 2026-07-21: strict mode is explicit-only)
    — the router LLM handles it inline or offers delegation."""
    assert _manager()._should_force_spawn(
        "Bau mir eine Website und zeig sie mir am Bildschirm"  # i18n-allow
    ) is False


def test_screen_knowledge_question_does_not_force_spawn() -> None:
    assert _manager()._should_force_spawn("Was ist ein Bildschirm?") is False  # i18n-allow
