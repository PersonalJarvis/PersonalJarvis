"""Handing an order to a coding pane says nothing until it has actually landed.

Writing a pane's prompt is a deliberate quality-tier call — 10-21 s measured,
chosen over a fast rewrite because the coding agent then works from that prompt
for minutes. On 2026-07-27 a spoken bridge line was added to fill that window,
and it failed the same day in the worst available way: the realtime provider
re-voiced the interim sentence as a completed action ("I have forwarded the bug
to Alex") while nothing had reached the pane yet, and on a turn whose delivery
then did not happen at all, that false sentence was the only thing the
maintainer ever heard.

The lesson is not "word the interim line more carefully" — a downstream model is
free to re-tense whatever it is handed, so any statement made before the work is
done is a claim that can become a lie. What this file pins is therefore the
absence of that statement:

* the hand-off emits NO announcement of any kind before the prompt is written,
  while it is written, or while it is being typed;
* the one and only sentence it produces is the returned per-pane verdict,
  derived from what actually reached which terminal;
* a delivery that did not happen says so, instead of a success being implied by
  an earlier line nobody corrected.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import prompt_composer
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.prompt_composer import ComposedPrompt
from jarvis.agentic_ide.session import Registry
from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.events import AnnouncementRequested
from jarvis.voice.contextual_readback import ReadbackComposer
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture(autouse=True)
def _isolated_recents(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never rewrite the developer's real recent-workspace list from a test."""
    from jarvis.agentic_ide import recents

    store = tmp_path_factory.mktemp("recents") / "recents.json"
    monkeypatch.setattr(recents, "_store_path", lambda: store)


class _Spy:
    """Every announcement this turn published, in order."""

    def __init__(self, bus: EventBus) -> None:
        self.events: list[AnnouncementRequested] = []
        bus.subscribe(AnnouncementRequested, self._on)

    async def _on(self, event: AnnouncementRequested) -> None:
        self.events.append(event)

    @property
    def texts(self) -> list[str]:
        return [e.text for e in self.events]


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def spy(bus: EventBus) -> _Spy:
    return _Spy(bus)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    reg = Registry(pty_manager=FakePtyManager())
    monkeypatch.setattr(session_mod, "get_registry", lambda: reg)
    return reg


@pytest.fixture
def manager(bus: EventBus) -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    mgr = BrainManager(config=cfg, bus=bus, tools={})
    # Pinned so the wording assertions do not depend on the host's locale
    # (AP-23: never test against the maintainer's own configuration).
    mgr._reply_language = "en"
    return mgr


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


class _LoudProvider:
    """A composer provider that would speak if anything ever asked it to.

    Its presence is the point: with a reachable provider wired, an interim line
    would be composed and published. Silence with this in place proves nothing
    is asking for one, rather than that a fallback path happened to be empty.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(
        self, content: str, language: str, *, persona_prompt: str = ""
    ) -> str:
        self.calls.append(persona_prompt)
        return "I have passed that on already."


async def test_nothing_is_spoken_while_the_prompt_is_still_being_written(
    manager: BrainManager, registry: Registry, spy: _Spy, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression itself: no claim exists while the claim is not yet true."""
    provider = _LoudProvider()
    manager._readback_composer = ReadbackComposer(provider=provider)
    await _open(registry, tmp_path, 1)
    (only,) = _names(registry)
    announced_when_composing: list[bool] = []

    async def watching_compose(utterance: str, **kwargs: object) -> ComposedPrompt:
        announced_when_composing.append(bool(spy.events))
        return ComposedPrompt(text="## Task\ndo it", files=[], composed_by="llm")

    monkeypatch.setattr(prompt_composer, "compose", watching_compose)

    reply = await manager._run_agentic_ide_fast_path(
        f"Tell {only} to fix the wake word timeout"
    )

    assert announced_when_composing == [False]
    assert provider.calls == []
    assert reply is not None


async def test_the_only_statement_is_the_verdict_returned_after_delivery(
    manager: BrainManager, registry: Registry, spy: _Spy, tmp_path: Path
) -> None:
    """One turn, one sentence — and it is the one derived from what happened."""
    manager._readback_composer = ReadbackComposer(provider=_LoudProvider())
    await _open(registry, tmp_path, 1)
    (only,) = _names(registry)

    reply = await manager._run_agentic_ide_fast_path(
        f"Tell {only} to fix the wake word timeout"
    )

    assert spy.events == []
    assert reply is not None
    assert only in reply


async def test_a_fleet_hand_off_stays_silent_too(
    manager: BrainManager, registry: Registry, spy: _Spy, tmp_path: Path
) -> None:
    """Several panes take longer still — and are still not worth a false claim."""
    manager._readback_composer = ReadbackComposer(provider=_LoudProvider())
    await _open(registry, tmp_path, 2)
    first, second = _names(registry)

    reply = await manager._run_agentic_ide_fast_path(
        f"Tell {first} and {second} to analyse the whole codebase"
    )

    assert spy.events == []
    assert reply is not None
    assert first in reply
    assert second in reply


async def test_a_delivery_that_did_not_happen_is_reported_as_not_happened(
    manager: BrainManager, registry: Registry, spy: _Spy, tmp_path: Path
) -> None:
    """The failure mode this file exists for, seen from the other end.

    With an interim line in place, an undelivered pane left the user holding a
    spoken "passing it on" and nothing after it. The verdict is now the first
    and only thing said, so a pane that was never reached is named as such.
    """
    manager._readback_composer = ReadbackComposer(provider=_LoudProvider())
    await _open(registry, tmp_path, 2)
    first, second = _names(registry)
    assert registry.session is not None
    dead = registry.session.find(second)
    assert dead is not None
    dead.status = "exited"
    dead.pty_id = None

    reply = await manager._run_agentic_ide_fast_path(
        f"Tell {first} and {second} to analyse the whole codebase"
    )

    assert spy.events == []
    assert reply is not None
    lowered = reply.lower()
    assert second in reply
    assert any(word in lowered for word in ("not", "could not", "n't"))


async def test_a_question_about_a_pane_announces_nothing(
    manager: BrainManager, registry: Registry, spy: _Spy, tmp_path: Path
) -> None:
    """A read is answered by the normal path; a hand-off line there is a lie."""
    await _open(registry, tmp_path, 1)
    (only,) = _names(registry)

    reply = await manager._run_agentic_ide_fast_path(f"What is {only} doing?")

    assert reply is None
    assert spy.events == []


async def test_an_unaddressed_turn_announces_nothing(
    manager: BrainManager, registry: Registry, spy: _Spy, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 1)

    reply = await manager._run_agentic_ide_fast_path(
        "What is the weather like tomorrow?"
    )

    assert reply is None
    assert spy.events == []


async def test_the_visible_chat_terminal_beats_a_stale_conversation_target(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """The live 2026-08-02 T1 -> "this terminal" -> wrong-T1 regression."""
    await _open(registry, tmp_path, 4)
    assert registry.session is not None
    registry.set_surface_context(
        workspace_id=registry.session.id,
        view="chat",
        on_screen=True,
        terminal="T4",
    )

    utterance = (
        "Kannst du bitte das Terminal prompten "  # i18n-allow: spoken input
        "hier und prüfen, ob der neue Subscription-Pfad "  # i18n-allow: spoken input
        "dieselben Funktionen hat?"  # i18n-allow: spoken input
    )
    reply = await manager._run_agentic_ide_fast_path(utterance)

    first = registry.session.find("T1")
    visible = registry.session.find("T4")
    assert first is not None and first.prompts_sent == 0
    assert visible is not None and visible.prompts_sent == 1
    assert reply is not None and "T4" in reply
