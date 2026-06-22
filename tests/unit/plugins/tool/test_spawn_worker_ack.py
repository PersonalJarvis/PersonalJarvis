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


@pytest.mark.asyncio
async def test_resolved_output_language_drives_mission_and_ack() -> None:
    """The turn's resolved ``ctx.config['output_language']`` — not a hardcoded
    'de' and not the STT tag — decides BOTH the mission dispatch language and
    the spoken ACK language.

    Forensic 2026-06-20 ("Mask it up"): an English turn (STT mis-tagged
    German) resolved to output_language='en', but the mission was dispatched
    language='de' and the ACK pulled the German fallback pool. The mission-fail
    phrase then came out German, and the German ACK text was spoken by the
    Cartesia *English* voice = "British accent on a German sentence". Both
    spoken surfaces must follow the one authoritative resolver.
    """
    mgr = _FakeMissionManager()
    announcer = _FakeAnnouncer()
    tool = SpawnWorkerTool(bus=EventBus(), manager=mgr, announcer=announcer)

    ctx = ExecutionContext(
        trace_id=uuid4(),
        user_utterance="Mask it up",
        config={"output_language": "en"},
        memory_read=None,
    )
    # No "language" arg from the brain — exactly the live "Mask it up" turn.
    await tool.execute({"utterance": "Mask it up", "action": ""}, ctx)
    await _drain_background_tasks()

    assert announcer.calls[0]["language"] == "en"
    assert mgr.dispatch_calls[0]["language"] == "en"


@pytest.mark.asyncio
async def test_ctx_output_language_overrides_stale_brain_arg() -> None:
    """The authoritative resolved language wins over a conflicting tool-call arg.

    The brain's ``language`` argument is a guess (it can echo a wrong STT tag);
    ``ctx.config['output_language']`` is the single resolver's verdict and must
    win, so no layer re-derives the language on its own terms (Runtime Output
    Language doctrine).
    """
    mgr = _FakeMissionManager()
    announcer = _FakeAnnouncer()
    tool = SpawnWorkerTool(bus=EventBus(), manager=mgr, announcer=announcer)

    ctx = ExecutionContext(
        trace_id=uuid4(),
        user_utterance="Mask it up",
        config={"output_language": "en"},
        memory_read=None,
    )
    await tool.execute(
        {"utterance": "Mask it up", "action": "", "language": "de"}, ctx
    )
    await _drain_background_tasks()

    assert announcer.calls[0]["language"] == "en"
    assert mgr.dispatch_calls[0]["language"] == "en"


@pytest.mark.asyncio
async def test_spanish_turn_caps_mission_language_to_de_but_acks_in_es() -> None:
    """An "es" turn must not thread "es" into the de/en-only mission contract.

    MissionManager.dispatch + the mission voice readback are de/en only, so the
    mission language is capped to "de" for a Spanish turn (the spoken ACK itself
    is still Spanish via the composer). Threading "es" into dispatch would
    violate its Literal["de","en"] contract and the completion readback has no
    "es" template.
    """
    mgr = _FakeMissionManager()
    announcer = _FakeAnnouncer()
    tool = SpawnWorkerTool(bus=EventBus(), manager=mgr, announcer=announcer)
    utter = "Abre mi Gmail y busca facturas nuevas"
    ctx = ExecutionContext(
        trace_id=uuid4(),
        user_utterance=utter,
        config={"output_language": "es"},
        memory_read=None,
    )
    await tool.execute({"utterance": utter, "action": "revisa el Gmail"}, ctx)
    await _drain_background_tasks()

    assert announcer.calls[0]["language"] == "es"
    assert mgr.dispatch_calls[0]["language"] == "de"


@pytest.mark.asyncio
async def test_context_bleed_utterance_falls_back_to_verbatim_turn() -> None:
    """The worker must carry the REAL spoken turn, never an echoed old task.

    Forensic 2026-06-20: under a full provider collapse the turn ran on a
    degraded fallback model fed a long prior context. For the spoken turn
    "Mask it up" it called spawn_worker carrying a PREVIOUS request
    ("emigrate to Melbourne") in both the utterance and action args — the
    worker then built an entirely foreign task. ctx.user_utterance is the
    ground truth for this turn; when the brain's utterance shares no content
    word with it, the verbatim turn wins and the (equally bled) action/target
    are dropped.
    """
    mgr = _FakeMissionManager()
    tool = SpawnWorkerTool(bus=EventBus(), manager=mgr, announcer=_FakeAnnouncer())
    ctx = ExecutionContext(
        trace_id=uuid4(),
        user_utterance="Mask it up",
        config={"output_language": "en"},
        memory_read=None,
    )
    await tool.execute(
        {
            "utterance": "research everything I need to emigrate to Melbourne",
            "action": "prepare a detailed Melbourne emigration report",
            "target": "Melbourne emigration checklist",
        },
        ctx,
    )
    await _drain_background_tasks()

    prompt = mgr.dispatch_calls[0]["prompt"]
    assert "Mask it up" in prompt
    assert "Melbourne" not in prompt


@pytest.mark.asyncio
async def test_related_interpretation_is_preserved() -> None:
    """A genuine interpretation of the SAME turn keeps the brain's action.

    Guard against over-correction: when the brain's utterance shares content
    with the spoken turn, it is a real interpretation (possibly enriched), so
    the action/target must survive — only an unrelated (bled) call is dropped.
    """
    mgr = _FakeMissionManager()
    tool = SpawnWorkerTool(bus=EventBus(), manager=mgr, announcer=_FakeAnnouncer())
    ctx = ExecutionContext(
        trace_id=uuid4(),
        user_utterance="Schau in mein Gmail nach neuen Rechnungen",
        config={"output_language": "de"},
        memory_read=None,
    )
    await tool.execute(
        {
            "utterance": "Schau in mein Gmail nach neuen Rechnungen",
            "action": "durchsucht das Gmail-Postfach nach Rechnungen",
            "target": "",
        },
        ctx,
    )
    await _drain_background_tasks()

    prompt = mgr.dispatch_calls[0]["prompt"]
    assert "durchsucht das Gmail-Postfach nach Rechnungen" in prompt


def test_schema_offers_spoken_ack_and_language() -> None:
    """The router brain must be invited to phrase the announcement itself."""
    props = SpawnWorkerTool.schema["properties"]
    assert "spoken_ack" in props
    assert "language" in props
    # Optional fields — the force-spawn path calls without them.
    assert SpawnWorkerTool.schema["required"] == ["utterance", "action"]
    description = props["spoken_ack"]["description"]
    assert "stock phrase" in description
