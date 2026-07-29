"""Recovering from a briefing the user says never arrived.

Live failure 2026-07-29 17:04 (BUG-121). A briefing for T7 was consumed by the
navigation gate, and Jarvis said it had briefed T7 anyway. The user corrected it
twice (both sentences are pinned verbatim as ``COMPLAINT_1`` / ``COMPLAINT_2``
below) — and BOTH corrections produced nothing at all. The flight recorder is
unambiguous: turn 1 routed ``path=native_realtime, reasons=none`` with no
delegate and ``tool_calls=[]``, so no brain ever saw it; the live model
apologised and promised a delivery it had no way to make. Turn 2 routed exactly
the same and only worked because the model happened to call the action tool by
itself. Two wasted turns and an apology are not a recovery path.

Root cause: a correction names no pane and carries no instruction, so every
detector in the workspace returns None and every vocabulary in the planner
misses it. It is only meaningful against the turn before it.

Two layers, matching where the two turns died:

1. ``turn_planner`` inherits ``WORKSPACE`` for a complaint whose PRIOR turn was
   about a pane, so the correction reaches the orchestrator at all.
2. ``_retry_undelivered_agentic_ide_prompt`` delivers the previous turn's
   briefing — but only to a pane whose own receipt (``last_prompt_at``) shows
   nothing arrived. A pane that WAS briefed is answered with the clock time
   instead, because re-sending on an unverified complaint double-briefs a
   working agent.

Deterministic throughout: real detectors, real registry, faked prompt composer.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from jarvis.agentic_ide import prompt_composer
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.intent import reports_undelivered
from jarvis.agentic_ide.prompt_composer import ComposedPrompt
from jarvis.agentic_ide.session import Registry
from jarvis.brain.manager import BrainManager
from jarvis.brain.turn_planner import TurnPath, TurnReason, plan_turn
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import BrainMessage
from tests.fakes.fake_pty_manager import FakePtyManager

# The two turns from the live session, verbatim off the flight recorder.
COMPLAINT_1 = "Du hast es gar nicht gepromptet."  # i18n-allow: transcript
COMPLAINT_2 = "Das war noch nicht geprompted."  # i18n-allow: transcript
ORIGINAL = (
    "Kannst du mal bitte Terminal {a} prompten, dass es einen "  # i18n-allow: transcript
    "Deep Dive machen soll, wieso das Resuming Feature nur bei "  # i18n-allow: transcript
    "claude Code Sessions funktioniert."  # i18n-allow: transcript
)


@pytest.fixture(autouse=True)
def _isolated_recents(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never rewrite the developer's real recent-workspace list from a test."""
    from jarvis.agentic_ide import recents

    store = tmp_path_factory.mktemp("recents") / "recents.json"
    monkeypatch.setattr(recents, "_store_path", lambda: store)


@pytest.fixture(autouse=True)
def _fake_composer(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_compose(utterance: str, **kwargs: object) -> ComposedPrompt:
        name = kwargs["terminal_name"]
        instruction = kwargs.get("instruction") or utterance
        return ComposedPrompt(
            text=f"## Task for {name}\n{instruction}",
            files=["jarvis/core/bus.py"],
            composed_by="llm",
        )

    monkeypatch.setattr(prompt_composer, "compose", fake_compose)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    reg = Registry(pty_manager=FakePtyManager())
    monkeypatch.setattr(session_mod, "get_registry", lambda: reg)
    return reg


@pytest.fixture
def manager() -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    mgr = BrainManager(config=cfg, bus=EventBus(), tools={})
    # Pinned so wording assertions do not depend on the host's locale
    # (AP-23: never test against the maintainer's own configuration).
    mgr._reply_language = "en"
    return mgr


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


async def _open(registry: Registry, folder: Path, count: int) -> None:
    await registry.start(str(folder), [{"agent": "claude"} for _ in range(count)])
    assert registry.session is not None
    for term in list(registry.session.terminals):
        await registry.attach(term.name, 100, 30, _noop, _noop_exit)


def _names(registry: Registry) -> list[str]:
    assert registry.session is not None
    return [t.name for t in registry.session.terminals]


def _prompted(registry: Registry) -> list[str]:
    assert registry.session is not None
    return [t.name for t in registry.session.terminals if t.prompts_sent > 0]


def _original(registry: Registry) -> str:
    return ORIGINAL.format(a=_names(registry)[6])


def _remember(manager: BrainManager, utterance: str) -> None:
    """Put the original request into history, as a real prior turn would."""
    manager._history = [BrainMessage(role="user", content=utterance)]


# --------------------------------------------------------------------------- #
# The shape detector                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        COMPLAINT_1,
        COMPLAINT_2,
        "da ist nichts passiert",  # i18n-allow: speech input under test
        "das hat nicht geklappt",  # i18n-allow: speech input under test
        "mach das nochmal",  # i18n-allow: speech input under test
        "you didn't prompt it",
        "nothing happened",
        "try again",
        "no pasó nada",  # i18n-allow: speech input under test
        "inténtalo de nuevo",  # i18n-allow: speech input under test
    ],
)
def test_a_complaint_is_recognised(text: str) -> None:
    assert reports_undelivered(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # A QUESTION about a delivery must keep reaching the report path.
        "Hast du T7 gepromptet?",  # i18n-allow: speech input under test
        "did you prompt T7?",
        # An ordinary briefing is not a complaint.
        "T7 soll einen Deep Dive machen",  # i18n-allow: speech input under test
        "zeig mir die Sessions",  # i18n-allow: speech input under test
        "wie ist das Wetter",  # i18n-allow: speech input under test
    ],
)
def test_an_ordinary_turn_is_not_a_complaint(text: str) -> None:
    assert reports_undelivered(text) is False


# --------------------------------------------------------------------------- #
# Layer 1 — the correction reaches the orchestrator at all                     #
# --------------------------------------------------------------------------- #


def test_the_complaint_routes_to_the_orchestrator() -> None:
    """The live turn routed ``native_realtime, reasons=none`` and died there."""
    plan = plan_turn(
        COMPLAINT_1,
        context=(ORIGINAL.format(a="T7"),),
        workspace_names=("T7",),
    )

    assert plan.path is TurnPath.ORCHESTRATOR
    assert TurnReason.WORKSPACE in plan.reasons


def test_a_complaint_without_a_workspace_prior_stays_native() -> None:
    """Both halves are required — a bare "try again" is not workspace work."""
    plan = plan_turn(
        COMPLAINT_1,
        context=("wie wird das Wetter morgen",),  # i18n-allow: speech input under test
        workspace_names=("T7",),
    )

    assert TurnReason.WORKSPACE not in plan.reasons


# --------------------------------------------------------------------------- #
# Layer 2 — the previous turn's briefing is actually delivered                 #
# --------------------------------------------------------------------------- #


async def test_the_complaint_delivers_the_previous_briefing(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """What the user asked for twice and never got."""
    await _open(registry, tmp_path, 7)
    _remember(manager, _original(registry))

    reply = await manager._run_agentic_ide_fast_path(COMPLAINT_1)

    assert reply is not None
    assert _prompted(registry) == [_names(registry)[6]]


async def test_a_pane_with_a_receipt_is_not_briefed_twice(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """A complaint is not proof — the pane's own receipt decides.

    Re-sending here would put two briefings into an agent that is already
    working, so the honest answer is the clock time.
    """
    await _open(registry, tmp_path, 7)
    _remember(manager, _original(registry))
    assert registry.session is not None
    term = registry.session.find(_names(registry)[6])
    assert term is not None
    term.last_prompt_at = time.time() - 30.0

    reply = await manager._run_agentic_ide_fast_path(COMPLAINT_1)

    assert reply is not None
    assert _prompted(registry) == []
    assert _names(registry)[6] in reply


async def test_a_stale_receipt_does_not_block_a_retry(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """Beyond the window the receipt is from another conversation."""
    await _open(registry, tmp_path, 7)
    _remember(manager, _original(registry))
    assert registry.session is not None
    term = registry.session.find(_names(registry)[6])
    assert term is not None
    term.last_prompt_at = time.time() - (manager._IDE_RETRY_WINDOW_S + 60.0)

    reply = await manager._run_agentic_ide_fast_path(COMPLAINT_1)

    assert reply is not None
    assert _prompted(registry) == [_names(registry)[6]]


async def test_a_complaint_with_no_pane_in_the_prior_turn_does_nothing(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """Never replay a sentence that was not about the workspace."""
    await _open(registry, tmp_path, 7)
    _remember(manager, "wie wird das Wetter morgen")  # i18n-allow: speech input under test

    reply = await manager._run_agentic_ide_fast_path(COMPLAINT_1)

    assert _prompted(registry) == []
    assert reply is None


async def test_the_second_complaint_recovers_too(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """The live session's turn 2, which also produced nothing."""
    await _open(registry, tmp_path, 7)
    _remember(manager, _original(registry))

    reply = await manager._run_agentic_ide_fast_path(COMPLAINT_2)

    assert reply is not None
    assert _prompted(registry) == [_names(registry)[6]]
