"""The spoken bridge line between "prompt Finn" and Finn actually being briefed.

Writing a pane's prompt is a deliberate quality-tier call — 10-21 s measured,
chosen over a fast rewrite because the coding agent then works from that prompt
for minutes. Until 2026-07-27 that window was silent: the maintainer said
"prompt Finn", heard nothing at all, and read the pause as a hung assistant.
The delivery readback ("Sent to Finn") is correct but arrives a quarter of a
minute too late to be feedback.

So the hand-off now speaks first. What is pinned here is the part that makes it
worth anything:

* it is said BEFORE the slow work starts, not alongside it — a confirmation
  that races the thing it confirms is not a confirmation;
* it is COMPOSED for the request, so it names the topic back instead of reading
  a stock line out of a table (the maintainer's explicit ask: a fixed phrase
  tells you nothing about whether the right thing was understood);
* it degrades to the localized canned phrase with no provider, so a downloader
  with no key hears a sentence rather than the silence this fixes (§3);
* control still comes back to the voice session afterwards with the real
  per-pane verdict — the bridge line never becomes the answer.
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


class _FakeFlashProvider:
    """Stands in for the flash ack provider the readback composer calls.

    Echoes a topic-fitting sentence built from the facts it was handed, which is
    what a real provider does here — and lets the test prove the request
    actually reached the composer instead of only that *something* was spoken.
    """

    def __init__(self) -> None:
        self.personas: list[str] = []

    async def run(
        self, content: str, language: str, *, persona_prompt: str = ""
    ) -> str:
        self.personas.append(persona_prompt)
        return "Passing the wake word timeout over now."


async def test_the_handoff_line_is_spoken_before_the_prompt_is_written(
    manager: BrainManager, registry: Registry, spy: _Spy, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: feedback lands ahead of the slow part, not with it."""
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

    assert announced_when_composing == [True]
    assert reply is not None


async def test_the_line_is_composed_for_this_request_not_a_stock_phrase(
    manager: BrainManager, registry: Registry, spy: _Spy, tmp_path: Path
) -> None:
    provider = _FakeFlashProvider()
    manager._readback_composer = ReadbackComposer(provider=provider)
    await _open(registry, tmp_path, 1)
    (only,) = _names(registry)

    await manager._run_agentic_ide_fast_path(
        f"Tell {only} to fix the wake word timeout"
    )

    assert spy.texts[0] == "Passing the wake word timeout over now."
    # The composer was handed the user's actual words and the call-sign — that
    # is what makes the sentence about THIS request rather than about nothing.
    persona = provider.personas[0]
    assert "wake word timeout" in persona
    assert only in persona


async def test_the_handoff_line_is_a_preamble_on_its_own_source_layer(
    manager: BrainManager, registry: Registry, spy: _Spy, tmp_path: Path
) -> None:
    """``brain.router.ack`` is dropped wholesale while the Flash-Brain is wired.

    Publishing under that layer would make the bridge line disappear on exactly
    the installs that have the most voice machinery turned on, which is the
    silence this feature exists to remove.
    """
    await _open(registry, tmp_path, 1)
    (only,) = _names(registry)

    await manager._run_agentic_ide_fast_path(f"Tell {only} to run the test suite")

    assert spy.events[0].kind == "preamble"
    assert spy.events[0].source_layer == "brain.agentic_ide.handoff"
    assert spy.events[0].language == "en"


async def test_without_a_provider_the_canned_line_is_spoken_instead_of_silence(
    manager: BrainManager, registry: Registry, spy: _Spy, tmp_path: Path
) -> None:
    """The keyless install (§3) still hears something, and it names the pane."""
    assert getattr(manager, "_readback_composer", None) is None
    await _open(registry, tmp_path, 1)
    (only,) = _names(registry)

    await manager._run_agentic_ide_fast_path(f"Tell {only} to run the test suite")

    assert only in spy.texts[0]


async def test_a_fleet_hand_off_names_every_addressed_pane(
    manager: BrainManager, registry: Registry, spy: _Spy, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 2)
    first, second = _names(registry)

    await manager._run_agentic_ide_fast_path(
        f"Tell {first} and {second} to analyse the whole codebase"
    )

    assert first in spy.texts[0]
    assert second in spy.texts[0]


async def test_control_returns_with_the_real_verdict_after_the_run(
    manager: BrainManager, registry: Registry, spy: _Spy, tmp_path: Path
) -> None:
    """The bridge line must never become the answer.

    Two separate statements at two separate times: the announcement says the
    request is on its way, the returned reply says what actually happened to
    each pane. A turn that returned the ack instead would report a delivery it
    never saw.
    """
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

    assert reply is not None
    assert reply not in spy.texts
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
