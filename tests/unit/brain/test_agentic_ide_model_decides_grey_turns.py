"""Who decides that a sentence was meant for a pane — the regex, or the model.

Maintainer complaint, 2026-08-13: "it always has to prompt some terminal. It
doesn't think at all, it isn't intelligent — why are we constraining the
codebase so the model gets bottlenecked?"

Measured, the complaint was exact. Nothing about typing into a coding agent was
ever a model decision: ``BrainManager.think`` runs the deterministic
addressed-terminal fast path BEFORE the model sees the turn, so whenever
``intent.detect_all`` matched, the utterance was composed and typed and the
model was never asked. And its weakest branch matches on two ingredients that
ordinary talk carries constantly — a call-sign, and any verb from a fixed list.
The call-signs are ordinary first names, so a sentence about a COLLEAGUE named
Kai who wanted to look at the repo carries both ingredients and orders nobody.
See ``GREY_TURNS`` for the measured examples, quoted as the speech input they
are.

The fix is not a better regex — the written evidence in those sentences really
is identical, so no pattern can separate them. It is to stop pretending the
detector's weakest reading is a fact:

* an utterance carrying a real ADDRESSING SHAPE stays deterministic, because
  that shape is what makes claiming the turn safe (and it is the guarantee the
  2026-07-25 live bug bought);
* an utterance carrying only a name and a verb is reported as ``likely``, the
  fast path stands down, and the turn reaches the model — which sees the panes,
  their output and the conversation, and holds ``agentic-ide-prompt``.

What must NOT change is the force-spawn precedence: standing down here may never
turn a pane order into an invisible background mission. That is why the wide
``owns_turn`` still answers yes for a grey turn — it only ever WITHHOLDS.

Deterministic throughout: real gate, real detector, faked prompt composer.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import intent, prompt_composer
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.prompt_composer import ComposedPrompt
from jarvis.agentic_ide.session import Registry
from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from tests.fakes.fake_pty_manager import FakePtyManager

# Ordinary talk that happens to contain a call-sign and a work verb. None of
# these hands anybody work; every one of them was typed into a coding agent
# before this change.
GREY_TURNS = (
    "der Kai aus dem Team wollte das Repo mal analysieren",  # i18n-allow
    "ich muss noch checken ob Alex das richtig verstanden hat",  # i18n-allow
    "bei Alex läuft der Test gerade durch",  # i18n-allow
    "Alex meinte, wir sollten die Tests nochmal machen",  # i18n-allow
)

# Real orders. Each carries a shape that means "this is for you" and nothing
# else, so each stays deterministic.
ADDRESSED_TURNS = (
    "sag Alex, er soll die Tests fixen",  # i18n-allow
    "Alex soll mal einen Deep Dive auf den Wake-Bug machen",  # i18n-allow
    "prompte Alex mit dem Wake-Bug",  # i18n-allow
    "lass Alex den kaputten Test reparieren",  # i18n-allow
    "Alex, mach mal einen Deep Dive auf den Router",  # i18n-allow
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
            files=[],
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


async def _open_with_alex_and_kai(registry: Registry, folder: Path) -> list[str]:
    """A workspace whose call-signs are the ordinary first names under test.

    Pinned rather than drawn from the pool: the whole point of these cases is
    that a call-sign is also somebody's name, so the test has to state which
    names it means (AP-23 — never assert against whatever the pool happened to
    hand out on this machine).
    """
    # Both panes run the same agent: which CLI is behind a pane has no bearing
    # on who a sentence addresses, and the fake PTY only carries "claude"
    # cleanly.
    await registry.start(
        str(folder),
        [{"agent": "claude", "name": "Alex"}, {"agent": "claude", "name": "Kai"}],
    )
    assert registry.session is not None
    for term in list(registry.session.terminals):
        await registry.attach(term.name, 100, 30, _noop, _noop_exit)
    names = [t.name for t in registry.session.terminals]
    assert names == ["Alex", "Kai"], names
    return names


def _typed(registry: Registry) -> list[str]:
    """Every pane that actually received a prompt."""
    assert registry.session is not None
    return [t.name for t in registry.session.terminals if t.prompts_sent > 0]


# --------------------------------------------------------------------------- #
# The detector grades its own evidence                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("utterance", ADDRESSED_TURNS)
def test_a_real_order_is_certain(utterance: str) -> None:
    """An addressing shape is the evidence that makes acting safe."""
    found = intent.detect_all(utterance, names=["Alex", "Kai"])
    assert found, f"a real order must still be detected: {utterance!r}"
    assert all(item.confidence == intent.CONFIDENCE_CERTAIN for item in found)


@pytest.mark.parametrize("utterance", GREY_TURNS)
def test_ordinary_talk_about_a_pane_is_only_likely(utterance: str) -> None:
    """A name plus a verb is a guess, and now says so."""
    found = intent.detect_all(utterance, names=["Alex", "Kai"])
    assert found, "still detected — the reading is real, just not certain"
    assert all(item.confidence == intent.CONFIDENCE_LIKELY for item in found)


# --------------------------------------------------------------------------- #
# What each grade is allowed to do                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("utterance", ADDRESSED_TURNS)
async def test_a_real_order_is_still_delivered_deterministically(
    manager: BrainManager, registry: Registry, tmp_path: Path, utterance: str
) -> None:
    """The promise of the surface: talking to a pane makes that pane work."""
    await _open_with_alex_and_kai(registry, tmp_path)

    reply = await manager._run_agentic_ide_fast_path(utterance)

    assert reply is not None, "an addressed pane is never handed to the model"
    assert _typed(registry) == ["Alex"]


@pytest.mark.parametrize("utterance", GREY_TURNS)
async def test_grey_talk_types_nothing_and_reaches_the_model(
    manager: BrainManager, registry: Registry, tmp_path: Path, utterance: str
) -> None:
    """The complaint itself: nothing is typed on a sentence that ordered nothing.

    ``None`` is how this path says "not mine" — the turn then runs the ordinary
    way, where the model holds ``agentic-ide-prompt`` and can still send the
    work if it reads the sentence as an order.
    """
    await _open_with_alex_and_kai(registry, tmp_path)

    reply = await manager._run_agentic_ide_fast_path(utterance)

    assert reply is None, "the fast path must stand down on uncertain evidence"
    assert _typed(registry) == [], "and nothing may reach an agent"


@pytest.mark.parametrize("utterance", GREY_TURNS)
def test_standing_down_never_hands_the_turn_to_force_spawn(utterance: str) -> None:
    """The guarantee bought by the 2026-07-25 live bug survives the change.

    The wide ``owns_turn`` is what the force-spawn guard and the spawn gate
    consult, and it still answers yes here. So a grey turn reaches the model —
    it does NOT become an invisible background mission in a fresh worktree
    while the panes sit idle, which is the failure this whole module exists to
    prevent.
    """
    names = ["Alex", "Kai"]
    assert intent.owns_turn(utterance, names=names) is True
    # …and the narrow question, the one asked by callers that ACT, says no.
    assert intent.owns_turn(utterance, names=names, only_certain=True) is False


@pytest.mark.parametrize("utterance", ADDRESSED_TURNS)
def test_a_real_order_answers_yes_to_both_questions(utterance: str) -> None:
    """Nothing about the addressed path is narrowed by the split."""
    names = ["Alex", "Kai"]
    assert intent.owns_turn(utterance, names=names) is True
    assert intent.owns_turn(utterance, names=names, only_certain=True) is True


# --------------------------------------------------------------------------- #
# The capabilities a grey turn no longer costs                                #
# --------------------------------------------------------------------------- #


def test_a_grey_turn_no_longer_switches_off_the_config_gates(
    manager: BrainManager,
) -> None:
    """Being wrong used to cost the whole turn, not just the pane reading.

    ``ide_owns_turn`` disables the provider, language, sub-agent and depth
    gates for the rest of the turn. On a merely plausible pane reading that
    left the user unable to change a setting for no reason they could see —
    and, now that the fast path also stands down on that evidence, without even
    briefing an agent in exchange.
    """
    for utterance in GREY_TURNS:
        assert (
            manager._agentic_ide_owns_turn(utterance, only_certain=True) is False
        ), utterance


# --------------------------------------------------------------------------- #
# The undo                                                                    #
# --------------------------------------------------------------------------- #


def test_cancelling_reaches_briefs_that_are_still_being_written(
    manager: BrainManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Stop" now recalls an order during the writer's 10-30 second window.

    Until 2026-08-13 only hanging up the call could do that, so a spoken order
    the user changed their mind about arrived anyway. The window is real and
    long, and nothing has been typed yet while it is open — the PTY write is
    the last step of a fan-out.
    """
    seen: list[str] = []

    def fake_cancel(*, reason: str = "") -> int:
        seen.append(reason)
        return 2

    from jarvis.agentic_ide import fanout

    monkeypatch.setattr(fanout, "cancel_spoken_deliveries", fake_cancel)

    assert manager._cancel_workspace_briefs_in_flight() == 2
    assert seen, "the cancellation reason is passed on for the log"


def test_cancelling_with_nothing_in_flight_is_a_quiet_zero(
    manager: BrainManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Too late, or nothing running: cancel nothing and say so honestly."""
    from jarvis.agentic_ide import fanout

    monkeypatch.setattr(fanout, "cancel_spoken_deliveries", lambda **_: 0)

    assert manager._cancel_workspace_briefs_in_flight() == 0
