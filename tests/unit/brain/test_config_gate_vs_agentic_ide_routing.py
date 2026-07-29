"""Config-gate-vs-Agentic-IDE routing (a setting word inside a pane order).

Live bug (voice session 2026-07-28 20:34, coding mode ON, six panes open): the
user asked for two coding agents to be briefed with split work. Jarvis briefed
neither. It changed ``brain.reply_language`` to ``auto``, persisted that to
jarvis.toml, and answered the turn — then the live model, which is never told
any of this, filled the silence by claiming both agents had been given their
tasks. Nothing was written to any pane and no brain ever saw the request.

Root cause (measured, not guessed): ``BrainManager.generate`` runs the
deterministic self-configuration gates BEFORE the Agentic-IDE delivery path,
and ``voice_command_gate._match_language_switch`` searched the WHOLE utterance
for each ingredient of a language command independently. In this dictated
paragraph it found "automatisch" at offset 458 (the user DESCRIBING a bug —  # i18n-allow: quoted transcript token
text is not inserted automatically), the verb "stellen" at 866 (from  # i18n-allow: quoted transcript token
"Rückfragen stellen") and an "in" before both: three unrelated clauses, up to  # i18n-allow: quoted transcript tokens
468 characters apart, assembled into "switch the reply language to auto". The
gate returned, and the delivery path 283 lines further down never ran.

Two independent fixes, one test file:

1. ``_match_language_switch`` binds its ingredients to a window around the
   language word, so scattered co-occurrence can no longer form a command.
2. A turn that NAMES A RUNNING PANE outranks the self-configuration gates
   altogether — the same precedence the desktop gate already honours (see
   ``test_cu_vs_agentic_ide_routing.py``). Briefing an agent and changing a
   setting are not things a user can mean at the same time, so however
   plausible a config match looks, the named agent is the stronger evidence.

The cancel intercept is deliberately NOT behind that precedence: stopping work
is a safety control and must keep working under every phrasing.

Deterministic throughout: real gate, real detector, faked prompt composer.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import prompt_composer
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.prompt_composer import ComposedPrompt
from jarvis.agentic_ide.session import Registry
from jarvis.brain.manager import BrainManager
from jarvis.brain.voice_command_gate import match_voice_command
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from tests.fakes.fake_pty_manager import FakePtyManager

# The live transcript, trimmed to the part that carries the failure — every
# word that made the gate fire is kept at its original distance from the
# others. ``{a}`` and ``{b}`` stand where the user named two panes.
LIVE_FAILURE = (
    "Was geht ab? Ähm, kannst du bitte für mich ähm {a} und {b} prompten, "  # i18n-allow: German speech input under test
    "dass sie die Dev machen sollen, alle Funktionalitäten rund um unsere "  # i18n-allow: German speech input under test
    "neues ähm Voice Feature ähm machen so ähm und gucken, welche "  # i18n-allow: German speech input under test
    "Funktionalitäten nicht funktionieren. Ähm, was mir ähm extrem "  # i18n-allow: German speech input under test
    "aufgefallen ist, ist z.B., dass es nicht funktioniert, dass ähm wenn "  # i18n-allow: German speech input under test
    "man überhaupt diese Shortcut-Kombination ähm macht, welche wir in "  # i18n-allow: German speech input under test
    "den in der Einstellungssektion haben, dass es dann nicht automatisch "  # i18n-allow: German speech input under test
    "in das Textfeld reingepromptet wird, ähm wo man drinnen ist. Das "  # i18n-allow: German speech input under test
    "funktioniert nicht. Soll in der Codebase nachschauen, wenn was sie "  # i18n-allow: German speech input under test
    "machen sollen, wenn sie wenn sie das nicht wissen, dann ähm sollen "  # i18n-allow: German speech input under test
    "sie mir Rückfragen stellen. Ähm du musst natürlich die beiden ähm "  # i18n-allow: German speech input under test
    "prompten, dass sie verschiedene Aufgaben machen. Also du splittest "  # i18n-allow: German speech input under test
    "die beiden Aufgaben."  # i18n-allow: German speech input under test
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


def _spoken(registry: Registry) -> str:
    """The live utterance with the two addressed panes filled in."""
    names = _names(registry)
    return LIVE_FAILURE.format(a=names[0], b=names[4])


# --------------------------------------------------------------------------- #
# Fix 1 — the gate may not assemble a command out of scattered words           #
# --------------------------------------------------------------------------- #


def test_the_language_gate_no_longer_claims_this_turn() -> None:
    """The ingredients are all present; only their distance disqualifies them."""
    assert "automatisch" in LIVE_FAILURE  # i18n-allow: quoted transcript token
    assert "stellen" in LIVE_FAILURE  # i18n-allow: quoted transcript token

    m = match_voice_command(LIVE_FAILURE.format(a="T1", b="T5"))

    assert m is None or m.kind != "language_switch"


def test_a_real_language_switch_is_untouched() -> None:
    """The recall side: the gate still exists to catch these."""
    m = match_voice_command("stell auf Englisch um")  # i18n-allow: German speech input under test
    assert m is not None and m.kind == "language_switch"
    assert m.target == "en"


# --------------------------------------------------------------------------- #
# Fix 2 — a named pane outranks the self-configuration gates                   #
# --------------------------------------------------------------------------- #


async def test_the_config_gates_stand_down_while_a_pane_is_addressed(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """The guard ``generate`` consults before it runs any config gate.

    Asked against a REAL open workspace, because the whole failure was that the
    config gate never learned a workspace was there.
    """
    await _open(registry, tmp_path, 6)

    assert manager._agentic_ide_owns_turn(_spoken(registry)) is True


async def test_the_addressed_panes_are_briefed_end_to_end(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """What the user actually asked for: both named agents get the work.

    The live turn delivered to neither, so this asserts the whole point of the
    feature — and that the SECOND pane is not lost, which is its own recurring
    failure mode.
    """
    await _open(registry, tmp_path, 6)
    names = _names(registry)

    reply = await manager._run_agentic_ide_fast_path(_spoken(registry))

    assert reply is not None
    assert _prompted(registry) == [names[0], names[4]]


async def test_a_forced_config_match_still_cannot_take_a_pane_turn(
    manager: BrainManager,
    registry: Registry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The precedence itself, proven independently of the gate's own fix.

    The detector is forced to claim a language switch, so this fails unless
    ``generate`` really does consult the workspace BEFORE its config gates.
    Belt and braces on purpose: fix 1 stops this particular sentence, fix 2
    stops the whole class — some future config gate will match something in a
    long dictated paragraph again.
    """
    await _open(registry, tmp_path, 6)
    names = _names(registry)
    applied: list[str] = []

    monkeypatch.setattr(manager, "_detect_language_switch_intent", lambda _t: "en")

    def _record(code: str) -> str:
        applied.append(code)
        return "language switched"

    monkeypatch.setattr(manager, "_apply_reply_language_switch", _record)

    await manager.generate(_spoken(registry), use_history=False)

    assert applied == []
    assert _prompted(registry) == [names[0], names[4]]


async def test_a_plain_language_switch_keeps_its_turn_with_panes_open(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """Having panes open must not withdraw the config gates wholesale.

    An open workspace is the normal state while coding; if it disabled the
    language switch, "answer in English from now on" would stop working for as
    long as the IDE is up.
    """
    await _open(registry, tmp_path, 6)

    assert manager._agentic_ide_owns_turn("stell auf Englisch um") is False  # i18n-allow: German speech input under test
    m = match_voice_command("stell auf Englisch um")  # i18n-allow: German speech input under test
    assert m is not None and m.kind == "language_switch"


async def test_no_workspace_open_changes_nothing(manager: BrainManager) -> None:
    """With no session at all the guard is inert — the common case."""
    assert manager._agentic_ide_owns_turn(LIVE_FAILURE.format(a="T1", b="T5")) is False
