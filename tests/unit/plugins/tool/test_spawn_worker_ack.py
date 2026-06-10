"""Tests for the spoken spawn acknowledgement wiring in ``SpawnWorkerTool``.

History: the spawn ACK was a fixed German scaffold ("Mach ich, ich
kümmere mich im Hintergrund darum, ...") plus a 6-phrase rotation — the
user flagged that twice (2026-05-26, 2026-06-10) as robotic. The tool now
delegates phrasing to ``SpawnAnnouncementComposer`` (brain-supplied
``spoken_ack`` → flash-LLM → bilingual no-repeat fallback). These tests
pin the tool-side contract:

* every dispatch path speaks the composer's output (never a hardcoded
  string in this module),
* the brain's ``spoken_ack`` / ``language`` args reach the composer,
* the cooldown-suppress path uses the deterministic "already_running"
  kind (no LLM on a duplicate-rejection turn),
* without an injected announcer the tool still always produces a
  non-empty spoken confirmation (AD-OE6 zero silent drops),
* the old stock template can never come back.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import uuid4

import pytest

import jarvis.plugins.tool.spawn_worker as spawn_worker_module
from jarvis.brain.ack_brain.spawn_announcement import (
    _FALLBACK_ALREADY_RUNNING,
    _FALLBACK_SPAWN,
)
from jarvis.core.bus import EventBus
from jarvis.core.protocols import ExecutionContext
from jarvis.plugins.tool.spawn_worker import SpawnWorkerTool


class _FakeMissionManager:
    def __init__(self) -> None:
        self.dispatch_calls: list[dict[str, Any]] = []

    async def dispatch(
        self, *, prompt: str, language: str, source_actor: str
    ) -> str:
        mid = f"mission_{len(self.dispatch_calls):04d}"
        self.dispatch_calls.append(
            {"prompt": prompt, "language": language, "id": mid}
        )
        return mid


class _FakeAnnouncer:
    """Records compose() kwargs; returns a fixed marker string."""

    def __init__(self, result: str = "COMPOSED ANNOUNCEMENT") -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def compose(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return self.result


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(), user_utterance="", config={}, memory_read=None
    )


async def _drain_background_tasks() -> None:
    """Let the fire-and-forget dispatch task run to completion."""
    await asyncio.sleep(0.05)


# --------------------------------------------------------------------------- #
# Composer wiring                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_execute_speaks_the_composer_output() -> None:
    announcer = _FakeAnnouncer()
    tool = SpawnWorkerTool(
        bus=EventBus(), manager=_FakeMissionManager(), announcer=announcer
    )

    result = await tool.execute(
        {"utterance": "Schau in mein Gmail.", "action": ""}, _ctx()
    )
    await _drain_background_tasks()

    assert result.success is True
    assert result.output == "COMPOSED ANNOUNCEMENT"
    assert len(announcer.calls) == 1


@pytest.mark.asyncio
async def test_brain_supplied_spoken_ack_and_language_reach_composer() -> None:
    announcer = _FakeAnnouncer()
    tool = SpawnWorkerTool(
        bus=EventBus(), manager=_FakeMissionManager(), announcer=announcer
    )

    await tool.execute(
        {
            "utterance": "Check my Gmail inbox please.",
            "action": "checks the Gmail inbox",
            "target": "for new invoices",
            "spoken_ack": "Got it, I'll go through your Gmail and report back.",
            "language": "en",
        },
        _ctx(),
    )
    await _drain_background_tasks()

    call = announcer.calls[0]
    assert call["candidate"] == (
        "Got it, I'll go through your Gmail and report back."
    )
    assert call["language"] == "en"
    assert call["action"] == "checks the Gmail inbox"
    assert call["target"] == "for new invoices"
    assert call["utterance"] == "Check my Gmail inbox please."


@pytest.mark.asyncio
async def test_force_spawn_path_passes_no_candidate() -> None:
    """Force-spawn (action='') has no brain text — composer gets candidate=None."""
    announcer = _FakeAnnouncer()
    tool = SpawnWorkerTool(
        bus=EventBus(), manager=_FakeMissionManager(), announcer=announcer
    )

    await tool.execute(
        {"utterance": "Bau mir eine Webseite.", "action": "", "language": "de"},
        _ctx(),
    )
    await _drain_background_tasks()

    call = announcer.calls[0]
    assert call["candidate"] is None
    assert call["language"] == "de"
    # The UI fallback action ("einer komplexen Aufgabe nachgeht") must NOT
    # leak into the spoken-announcement context — it is a placeholder.
    assert call["action"] == ""


@pytest.mark.asyncio
async def test_cooldown_suppress_uses_already_running_kind() -> None:
    announcer = _FakeAnnouncer(result="STILL RUNNING")
    tool = SpawnWorkerTool(
        bus=EventBus(), manager=_FakeMissionManager(), announcer=announcer
    )
    tool._last_spawn_at = time.monotonic() - 1.0
    tool._active_dispatches = 1

    result = await tool.execute(
        {"utterance": "Und noch eine Sache bitte.", "action": "x"}, _ctx()
    )

    assert result.output == "STILL RUNNING"
    assert announcer.calls[0]["kind"] == "already_running"


# --------------------------------------------------------------------------- #
# Default announcer (no injection) — guaranteed spoken confirmation           #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_default_announcer_yields_fallback_pool_phrase_de() -> None:
    tool = SpawnWorkerTool(bus=EventBus(), manager=_FakeMissionManager())

    result = await tool.execute(
        {"utterance": "Schau in mein Gmail.", "action": "", "language": "de"},
        _ctx(),
    )
    await _drain_background_tasks()

    assert result.output in _FALLBACK_SPAWN["de"]


@pytest.mark.asyncio
async def test_default_announcer_yields_fallback_pool_phrase_en() -> None:
    tool = SpawnWorkerTool(bus=EventBus(), manager=_FakeMissionManager())

    result = await tool.execute(
        {
            "utterance": "Please check my Gmail.",
            "action": "",
            "language": "en",
        },
        _ctx(),
    )
    await _drain_background_tasks()

    assert result.output in _FALLBACK_SPAWN["en"]


@pytest.mark.asyncio
async def test_default_announcer_cooldown_phrase_is_bilingual() -> None:
    tool = SpawnWorkerTool(bus=EventBus(), manager=_FakeMissionManager())
    tool._last_spawn_at = time.monotonic() - 1.0
    tool._active_dispatches = 1

    result = await tool.execute(
        {"utterance": "One more thing please.", "action": "x", "language": "en"},
        _ctx(),
    )

    assert result.output in _FALLBACK_ALREADY_RUNNING["en"]


@pytest.mark.asyncio
async def test_spawn_acks_vary_and_never_repeat_back_to_back() -> None:
    """Successive force-spawns must not sound identical (no-repeat memory)."""
    tool = SpawnWorkerTool(bus=EventBus(), manager=_FakeMissionManager())

    outs: list[str] = []
    for _ in range(8):
        tool._active_dispatches = 0  # re-open the liveness gate per call
        result = await tool.execute(
            {"utterance": "Bau mir was.", "action": "", "language": "de"},
            _ctx(),
        )
        outs.append(result.output)
    await _drain_background_tasks()

    assert all(isinstance(o, str) and o.strip() for o in outs)
    for a, b in zip(outs, outs[1:], strict=False):
        assert a != b, f"same spawn ACK twice in a row: {a!r}"
    assert len(set(outs)) >= 3


# --------------------------------------------------------------------------- #
# Regression guards                                                           #
# --------------------------------------------------------------------------- #


def test_old_stock_template_machinery_is_gone() -> None:
    """The fixed scaffold and the finite rotation pools must not come back."""
    assert not hasattr(spawn_worker_module, "_build_context_ack")
    assert not hasattr(spawn_worker_module, "_GENERIC_ACK_VARIANTS")
    assert not hasattr(spawn_worker_module, "_COOLDOWN_SUPPRESS_ACKS")


@pytest.mark.asyncio
async def test_no_announcement_contains_the_banned_template_wording() -> None:
    tool = SpawnWorkerTool(bus=EventBus(), manager=_FakeMissionManager())

    for _ in range(10):
        tool._active_dispatches = 0
        result = await tool.execute(
            {"utterance": "Mach mir bitte etwas fertig.", "action": "", "language": "de"},
            _ctx(),
        )
        assert "im Hintergrund darum" not in result.output
        assert "vom User beschriebenen Workflow" not in result.output
    await _drain_background_tasks()


def test_schema_offers_spoken_ack_and_language() -> None:
    """The router brain must be invited to phrase the announcement itself."""
    props = SpawnWorkerTool.schema["properties"]
    assert "spoken_ack" in props
    assert "language" in props
    # Optional fields — the force-spawn path calls without them.
    assert SpawnWorkerTool.schema["required"] == ["utterance", "action"]
    description = props["spoken_ack"]["description"]
    assert "stock phrase" in description
