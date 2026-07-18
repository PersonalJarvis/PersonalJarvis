"""Computer-Use-vs-sub-agent-spawn routing (force-spawn depth-marker overlap).

Live bug: "Mach einen Deep Dive mit Computer Use in meinem Chrome Browser und  # i18n-allow
such mir den neuesten Post von Elon Musk raus" was force-spawned into a  # i18n-allow
background sub-agent mission instead of reaching the Computer-Use path.

Root cause (evidence, not guess): ``BrainManager._should_force_spawn`` hoists any
``force_spawn_phrases`` match to an immediate ``True`` BEFORE the LLM router ever
sees the turn. That phrase list mixes two semantically different things:

* **vehicle names** (``subagent`` / ``spawn`` / ``jarvis-agent`` / ``openclaw``
  legacy alias / ``delegate``) — the user named the worker. Unambiguous;
  absolute priority (mandate 2026-06-15).
* **depth markers** (``deep dive`` / ``gründlich`` / ``umfassend`` / …) — these  # i18n-allow
  describe thoroughness, NOT a vehicle, and OVERLAP with computer-use requests.

A depth marker must not override an explicit on-screen/computer/browser request:
the computer-use-vs-spawn call is the LLM router's (it owns ``computer_use`` plus
the SYSTEM_PROMPT rule "Bildschirm/Browser bedienen ist computer_use, kein  # i18n-allow
spawn_worker"). The deterministic gate only decides whether to FORCE a spawn —
for an ambiguous depth marker + a computer-use request it must stand down and let
the LLM decide. A genuine depth/research request WITHOUT a screen signal still
force-spawns; a turn that NAMES the vehicle always force-spawns.

These tests are fully deterministic — no LLM, no real brain — they exercise
``_should_force_spawn`` directly.
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.brain.manager import BrainManager, _looks_like_pc_control
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
    """Strict-mode (production default) manager with spawn_worker AND the
    computer-use harness wired — the realistic gate path."""
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


# The live failure: a depth marker ("Deep Dive") wrapping an explicit
# computer-use / browser request. Must NOT force-spawn — it routes to the LLM
# router which picks computer_use.
FAILING_UTTERANCE = (
    "Mach einen Deep Dive mit Computer Use in meinem Chrome Browser und "  # i18n-allow
    "such mir den neuesten Post von Elon Musk raus"  # i18n-allow
)


def test_explicit_computer_use_request_does_not_force_spawn() -> None:
    """The reported bug: a 'Deep Dive ... mit Computer Use ... Chrome Browser'
    turn must reach Computer-Use, never a sub-agent spawn."""
    manager = _manager()
    assert manager._should_force_spawn(FAILING_UTTERANCE) is False, (
        "explicit Computer-Use request was force-spawned into a sub-agent mission "
        "— the depth keyword 'Deep Dive' overrode the on-screen/browser intent"
    )


def test_failing_utterance_is_recognised_as_computer_use() -> None:
    """Sanity pin for the routing contract: the failing turn IS an on-screen /
    browser request, so once the deterministic gate stands down the LLM router
    (which owns computer_use) is the one that decides — i.e. it routes to CU."""
    assert _looks_like_pc_control(FAILING_UTTERANCE) is True


def test_computer_use_is_reachable_by_the_router() -> None:
    """The LLM router can only pick Computer-Use if the tool is in its set."""
    from jarvis.brain.factory import ROUTER_TOOLS

    assert "computer-use" in ROUTER_TOOLS


# --------------------------------------------------------------------------
# Balanced scenario matrix.
# --------------------------------------------------------------------------

# Explicit on-screen / computer / browser requests — even when wrapped in a
# depth marker — must NOT force-spawn (they reach the LLM router -> computer_use).
_REACH_COMPUTER_USE = [
    FAILING_UTTERANCE,
    # depth marker ("gründliche") + explicit screen control ("Bildschirm"/"klick")  # i18n-allow
    "Mach eine gründliche Analyse auf meinem Bildschirm und klick dich durch",  # i18n-allow
    # English depth marker + browser
    "Do a deep dive in my browser and click through the open tabs",
    # a plain pc-control request (no depth marker at all) must also stay off the
    # spawn path
    "Schreib ABC in das ChatGPT Eingabefeld",
]


@pytest.mark.parametrize("utterance", _REACH_COMPUTER_USE)
def test_on_screen_intents_do_not_force_spawn(utterance: str) -> None:
    manager = _manager()
    assert manager._should_force_spawn(utterance) is False, (
        f"on-screen/computer/browser request {utterance!r} was wrongly force-spawned"
    )


# Genuine heavy background work must STILL force-spawn deterministically.
_STILL_SPAWNS = [
    # depth marker WITHOUT a screen signal (the test_cli_capability_routing
    # contract: 'deep dive' over a CLI capability still spawns)
    "Mach einen Deep Dive in meine Google Cloud Kosten",
    # depth research, no screen signal
    "Mach eine umfassende Recherche über die KI-News der letzten Woche",  # i18n-allow
    # explicit vehicle name — absolute priority (mandate 2026-06-15)
    "Spawne einen Subagenten der die Logs analysiert",  # i18n-allow
    "Delegier das an einen Subagenten",  # i18n-allow
    # explicit vehicle name DESPITE a screen signal — naming the vehicle wins
    # over the computer-use stand-down (the mandate must survive the fix)
    "Spawne einen Subagenten der in Chrome klickt und mir einen Report baut",  # i18n-allow
]


@pytest.mark.parametrize("utterance", _STILL_SPAWNS)
def test_genuine_background_work_still_force_spawns(utterance: str) -> None:
    manager = _manager()
    assert manager._should_force_spawn(utterance) is True, (
        f"genuine heavy/explicit work {utterance!r} should still force-spawn"
    )


# Smalltalk never spawns.
_SMALLTALK = ["Hallo", "Wie geht's?", "Danke", "Was ist die Hauptstadt von Frankreich?"]  # i18n-allow


@pytest.mark.parametrize("utterance", _SMALLTALK)
def test_smalltalk_never_force_spawns(utterance: str) -> None:
    manager = _manager()
    assert manager._should_force_spawn(utterance) is False, (
        f"smalltalk {utterance!r} wrongly force-spawned"
    )


def test_vehicle_plus_screen_signal_distinguishes_from_depth_plus_screen() -> None:
    """The crux of the fix: a VEHICLE name + screen signal still spawns, while a
    DEPTH marker + the same screen signal stands down. Proves the decision is not
    a blanket keyword match but a vehicle-vs-depth distinction that hands the
    ambiguous case to the LLM."""
    manager = _manager()
    vehicle = "Spawne einen Subagenten der in Chrome klickt und mir einen Report baut"  # i18n-allow
    depth = "Mach einen Deep Dive und klick dich durch Chrome"  # i18n-allow
    assert _looks_like_pc_control(vehicle) is True
    assert _looks_like_pc_control(depth) is True
    assert manager._should_force_spawn(vehicle) is True, "vehicle name must still spawn"
    assert manager._should_force_spawn(depth) is False, "depth + screen must reach CU"
