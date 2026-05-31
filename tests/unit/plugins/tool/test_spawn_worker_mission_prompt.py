"""Tests for the mission-prompt construction in spawn_openclaw.

Live incident 2026-05-29 (mission 019e70a9): the user's request reached the
worker as a VAD-cut fragment. STT captured only 'die Detailwürfelspiele.html'
(1.7s of speech); the brain still understood the intent and emitted
action='eine HTML-Seite namens Würfelspiel.html baut', but the mission prompt
used the verbatim `utterance` field alone, so the worker built from the
fragment. The tool schema deliberately keeps `utterance` verbatim (no detail
loss), so the fix enriches the mission prompt with the interpreted `action`
(+ target + context_hints) while preserving the verbatim words.
"""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.protocols import ExecutionContext
from jarvis.plugins.tool.spawn_worker import (
    SpawnWorkerTool,
    _build_mission_prompt,
)


class _RecordingManager:
    def __init__(self) -> None:
        self.dispatched: list[str] = []

    async def dispatch(self, *, prompt: str, language: str, source_actor: str) -> str:
        self.dispatched.append(prompt)
        return f"mission_{len(self.dispatched)}"


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(), user_utterance="", config={}, memory_read=None
    )


def test_fragment_utterance_is_enriched_with_action() -> None:
    """A fragment utterance + a rich interpreted action must yield a mission
    prompt that carries the action's intent (the real filename), not just the
    fragment."""
    prompt = _build_mission_prompt(
        utterance="die Detailwürfelspiele.html",
        action="eine HTML-Seite namens Würfelspiel.html baut",
        target="Desktop-App Outputs",
    )
    assert "Würfelspiel.html" in prompt, (
        f"interpreted filename missing from mission prompt: {prompt!r}"
    )
    # The verbatim words are preserved as context (schema's no-detail-loss rule).
    assert "die Detailwürfelspiele.html" in prompt
    # The target is carried too.
    assert "Desktop-App Outputs" in prompt


def test_context_hints_are_included() -> None:
    prompt = _build_mission_prompt(
        utterance="bau das",
        action="eine Flask-App baut",
        target="",
        context_hints=["Port 8000", "mit /health-Endpoint"],
    )
    assert "Flask-App" in prompt
    assert "Port 8000" in prompt
    assert "/health-Endpoint" in prompt


def test_no_action_falls_back_to_raw_utterance() -> None:
    """Force-spawn path passes action='' — there is no interpretation, so the
    mission prompt is the raw utterance verbatim (unchanged behaviour)."""
    prompt = _build_mission_prompt(
        utterance="lies die Datei x und fasse sie zusammen", action=""
    )
    assert prompt == "lies die Datei x und fasse sie zusammen"


def test_generic_default_action_is_not_treated_as_interpretation() -> None:
    """The generic ACK filler ('einer komplexen Aufgabe nachgeht') is NOT a
    real interpretation — it must not become the worker's task; fall back to
    the verbatim utterance."""
    prompt = _build_mission_prompt(
        utterance="mach das für mich",
        action="einer komplexen Aufgabe nachgeht",
    )
    assert prompt == "mach das für mich"


def test_empty_everything_returns_empty() -> None:
    assert _build_mission_prompt(utterance="", action="") == ""


@pytest.mark.asyncio
async def test_execute_dispatches_enriched_prompt_not_raw_fragment() -> None:
    """End-to-end: execute() must dispatch the mission with the enriched
    prompt (carrying the interpreted filename), not the raw VAD fragment."""
    mgr = _RecordingManager()
    tool = SpawnWorkerTool(bus=EventBus(), manager=mgr)

    await tool.execute(
        {
            "utterance": "die Detailwürfelspiele.html",
            "action": "eine HTML-Seite namens Würfelspiel.html baut",
            "target": "Desktop-App Outputs",
        },
        _ctx(),
    )
    await asyncio.sleep(0.05)  # let the fire-and-forget bg task reach dispatch

    assert mgr.dispatched, "execute() must have dispatched a mission"
    prompt = mgr.dispatched[0]
    assert "Würfelspiel.html" in prompt, (
        f"dispatched prompt must carry the interpreted intent, got {prompt!r}"
    )
