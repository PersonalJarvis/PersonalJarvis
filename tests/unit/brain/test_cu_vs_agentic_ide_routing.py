"""Computer-Use-vs-Agentic-IDE routing (desktop verbs inside a pane order).

Live bug (voice session 2026-07-26 09:44, focused coding mode ON, twelve panes
open): the user asked, in German, for the pane called Bruno to do a deep dive
into why text cannot be pasted in the Agentic IDE. The verbatim transcript is
``LIVE_FAILURE`` below. Jarvis did not brief Bruno. It drove Computer-Use
instead, clicked into the workspace's own chat box and typed a prompt there —
the assistant operating its own UI by hand while a coding agent named in the
sentence sat idle.

Root cause (measured, not guessed): two deterministic gates claim this turn and
the older one runs first. ``BrainManager.think`` consults
``_run_local_action_fast_path`` before ``_run_agentic_ide_fast_path``, and the
desktop gate matches on the GUI verb "kopieren" — a word that appears here in
the *description of the problem*, never as an order to copy anything. A gate
that reads intent from single verbs cannot tell "copy this" from "copying is
broken", so the tie has to be broken by the stronger signal: the user named a
pane.

This is the sibling of ``test_cu_vs_spawn_routing.py``. There a depth marker
("Deep Dive") wrongly beat an explicit screen request; here a screen verb
wrongly beats an explicitly addressed terminal. Same shape, opposite direction:
whichever gate holds the more specific evidence wins.

The second half is why the reordering alone would not be safe. Name resolution
is fuzzy on purpose (a call-sign arrives through speech recognition), and the
weakest evidence path in ``intent.detect_all`` — "the utterance names a pane and
contains a verb" — accepts a merely *similar* word. Measured against the
shipping name pool, "unten" resolves to "Hunter" and "dann" to "Dana"; against
the live session's pool "keine" resolved to "Kai"  # i18n-allow: quoted transcript tokens
(all four are everyday words of the spoken language, quoted as measurement
data), which is how the failing turn addressed a second pane nobody had
named. Letting the workspace outrank the
desktop gate on that evidence would hand ordinary desktop commands to a coding
agent. So a name that is only *approximately* right can no longer claim a turn
on its own.

Deterministic throughout: real gate, real detector, faked prompt composer.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import intent, prompt_composer
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.prompt_composer import ComposedPrompt
from jarvis.agentic_ide.session import Registry
from jarvis.brain.local_action_gate import LocalActionMode, match_local_action
from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from tests.fakes.fake_pty_manager import FakePtyManager

# The verbatim transcript of the live failure, as the session log recorded it.
LIVE_FAILURE = (
    "Deine Aufgabe ist es Bruno einen Deep Dive machen zu lassen und zu "  # i18n-allow: German speech input under test
    "schauen, wieso man ähm bei Personal Jabber in der Igentic IDE Mode ähm "  # i18n-allow: German speech input under test
    "keine Texte rein kopieren kann. Also z.B. mit Steuerung STRG ähm + V "  # i18n-allow: German speech input under test
    "kann man keine Texte einkopieren auf Windows und es soll natürlich auf "  # i18n-allow: German speech input under test
    "jedem Betriebssystem funktionieren. Schau dir das an und hilf mir dabei "  # i18n-allow: German speech input under test
    "das zu fixen."  # i18n-allow: German speech input under test
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
    """A deterministic stand-in for the quality-tier prompt writer."""

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
    """Open ``count`` panes AND bring their agents live."""
    await registry.start(str(folder), [{"agent": "claude"} for _ in range(count)])
    assert registry.session is not None
    for term in list(registry.session.terminals):
        await registry.attach(term.name, 100, 30, _noop, _noop_exit)


def _names(registry: Registry) -> list[str]:
    assert registry.session is not None
    return [t.name for t in registry.session.terminals]


def _prompted(registry: Registry) -> list[str]:
    """Every pane that actually received a prompt."""
    assert registry.session is not None
    return [t.name for t in registry.session.terminals if t.prompts_sent > 0]


# --------------------------------------------------------------------------- #
# The collision itself                                                        #
# --------------------------------------------------------------------------- #


def test_the_desktop_gate_really_does_claim_this_turn() -> None:
    """Pin the precondition, so the guard below is never tested against a myth.

    If this ever stops holding, the ordering guard has become dead weight and
    the tests that follow would pass for the wrong reason.
    """
    plan = match_local_action(LIVE_FAILURE, "de")
    assert plan is not None
    assert plan.mode is LocalActionMode.COMPUTER_USE


def test_an_addressed_pane_outranks_the_desktop_gate() -> None:
    """The workspace owns the turn although it is full of GUI vocabulary."""
    assert intent.owns_turn(LIVE_FAILURE, names=["Bruno", "Iris", "Vega"]) is True


async def test_live_failure_briefs_the_pane_instead_of_driving_the_screen(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """End to end: the exact utterance must reach the addressed agent."""
    await _open(registry, tmp_path, 3)
    addressed = _names(registry)[1]

    reply = await manager._run_agentic_ide_fast_path(
        LIVE_FAILURE.replace("Bruno", addressed)
    )

    assert reply is not None
    assert _prompted(registry) == [addressed]


async def test_the_desktop_gate_stands_down_while_a_pane_is_addressed(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """The guard ``think`` consults before it runs the desktop fast path.

    Asked against a REAL open workspace, because the whole failure was that the
    gate never learned a workspace was there.
    """
    await _open(registry, tmp_path, 3)
    addressed = _names(registry)[1]

    assert manager._agentic_ide_owns_turn(
        LIVE_FAILURE.replace("Bruno", addressed)
    ) is True


@pytest.mark.parametrize(
    "utterance",
    [
        "kopier den Text aus dem Fenster",  # i18n-allow: German speech input under test
        "mach mal einen Screenshot vom Bildschirm",  # i18n-allow: German speech input under test
    ],
)
async def test_the_desktop_gate_keeps_screen_work_with_a_workspace_open(
    manager: BrainManager, registry: Registry, tmp_path: Path, utterance: str
) -> None:
    """Having panes open must not turn the assistant into a pane-only agent.

    An open workspace is the normal state while coding; if it withdrew the
    desktop gate wholesale, "take a screenshot" would stop working for as long
    as the IDE is up.
    """
    await _open(registry, tmp_path, 3)

    assert manager._agentic_ide_owns_turn(utterance) is False


async def test_no_workspace_open_changes_nothing(manager: BrainManager) -> None:
    """With no session at all the guard is inert — the common case."""
    assert manager._agentic_ide_owns_turn(LIVE_FAILURE) is False


# --------------------------------------------------------------------------- #
# ... without swallowing genuine desktop commands                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "utterance",
    [
        "kopier den Text aus dem Fenster",  # i18n-allow: German speech input under test
        "mach mal einen Screenshot vom Bildschirm",  # i18n-allow: German speech input under test
        "copy the text from that window",
    ],
)
def test_a_plain_desktop_command_keeps_its_turn(utterance: str) -> None:
    """No pane named, no claim — Computer-Use still owns these."""
    assert intent.owns_turn(utterance, names=["Bruno", "Hunter", "Dana"]) is False


@pytest.mark.parametrize(
    ("utterance", "pane"),
    [
        # "unten" (below) sounds close enough to "Hunter" to resolve, and
        # "dann" (then) to "Dana" — both measured against the shipping pool.
        ("mach das Fenster unten zu und kopier den Text", "Hunter"),  # i18n-allow: German speech input under test
        ("klick da oben drauf und dann kopier das", "Dana"),  # i18n-allow: German speech input under test
    ],
)
def test_a_merely_similar_word_never_addresses_a_pane(
    utterance: str, pane: str
) -> None:
    """The weak evidence path must not hand desktop work to a coding agent.

    Without this, making the workspace outrank the desktop gate would be a net
    loss: ordinary screen commands would be typed into a pane whose call-sign
    merely rhymes with a word in the sentence.
    """
    assert intent.detect(utterance, names=[pane, "Alex", "Blake"]) is None
    assert intent.owns_turn(utterance, names=[pane, "Alex", "Blake"]) is False


def test_an_exactly_named_pane_still_claims_the_weak_path() -> None:
    """The recall side of the same rule: a real name needs no addressing shape.

    "Bruno" in the live failure carried no "tell X to ..." construction — it was
    found purely because the utterance names a pane and reads as an instruction.
    That path has to survive the hardening above.
    """
    found = intent.detect(LIVE_FAILURE, names=["Bruno", "Iris"])
    assert found is not None
    assert found.terminal == "Bruno"


def test_only_the_named_pane_is_briefed() -> None:
    """The live turn addressed exactly one pane, not two.

    In the live session an ordinary negation word of the spoken language
    resolved to the call-sign "Kai", so the fan-out would have briefed a second
    agent the user never named — and reported both as working.
    """
    addressed = intent.detect_all(LIVE_FAILURE, names=["Bruno", "Kai", "Iris"])
    assert [item.terminal for item in addressed] == ["Bruno"]
