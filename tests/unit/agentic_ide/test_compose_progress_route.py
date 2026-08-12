"""The typed prompt bar hears the brief being written, beat by beat.

Composition is 10-30 s of real model work. The composer has always narrated
its stages, but for a typed send that narration went to stdout — the one place
the desktop user is guaranteed not to be looking — so the bar showed a silent
spinner and a working composer was indistinguishable from a wedged one. The
route now relays each beat onto the event bus as ``AgenticIdeComposeProgress``,
the same channel ``AgenticIdePromptSent`` already rides to every client.

Pinned here: the beats reach the bus with the fields a client renders from, a
dry run stays silent, and a missing bus costs the narration, never the brief.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import prompt_composer
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.prompt_composer import ComposedPrompt, ComposeNotice
from jarvis.agentic_ide.session import Registry
from jarvis.core.events import AgenticIdeComposeProgress
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture(autouse=True)
def _isolated_recents(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.agentic_ide import recents

    store = tmp_path_factory.mktemp("recents") / "recents.json"
    monkeypatch.setattr(recents, "_store_path", lambda: store)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    reg = Registry(pty_manager=FakePtyManager())
    monkeypatch.setattr(session_mod, "get_registry", lambda: reg)
    from jarvis.ui.web import agentic_ide_routes

    monkeypatch.setattr(agentic_ide_routes, "get_registry", lambda: reg)
    return reg


class _Bus:
    """Captures everything published; the assertions read it back."""

    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)


@pytest.fixture
def bus() -> _Bus:
    return _Bus()


@pytest.fixture
def client(registry: Registry, bus: _Bus) -> TestClient:
    from jarvis.ui.web.agentic_ide_routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.bus = bus
    return TestClient(app)


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


async def _live_pane(registry: Registry, folder: Path) -> str:
    await registry.start(str(folder), [{"agent": "claude"}])
    assert registry.session is not None
    term = registry.session.terminals[0]
    await registry.attach(term.name, 100, 30, _noop, _noop_exit)
    return term.name


def _narrating_compose(beats: list[ComposeNotice]):  # noqa: ANN202 - test double
    """A composer stand-in that emits ``beats`` through the caller's sink."""

    async def _compose(
        _utterance: str,
        *,
        on_progress=None,  # noqa: ANN001
        **_kwargs: object,
    ) -> ComposedPrompt:
        if on_progress is not None:
            for beat in beats:
                on_progress(beat)
        return ComposedPrompt(text="## Task\nDo the thing.", composed_by="llm")

    return _compose


async def test_compose_beats_reach_the_bus_a_client_already_holds(
    client: TestClient, registry: Registry, bus: _Bus, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = await _live_pane(registry, tmp_path)
    beats = [
        ComposeNotice(stage="start", message="Writing the brief.", terminal=name, kind="implement"),
        ComposeNotice(stage="ready", message="Brief written.", terminal=name, kind="implement"),
    ]
    monkeypatch.setattr(prompt_composer, "compose", _narrating_compose(beats))

    response = client.post(
        f"/api/agentic-ide/terminals/{name}/prompt",
        json={"prompt": "do the thing", "compose": True},
    )

    assert response.status_code == 200
    heard = [e for e in bus.events if isinstance(e, AgenticIdeComposeProgress)]
    assert [(b.stage, b.message) for b in heard] == [
        ("start", "Writing the brief."),
        ("ready", "Brief written."),
    ]
    # The fields a client filters and renders from, not just the prose.
    assert all(b.terminal == name for b in heard)
    assert all(b.kind == "implement" for b in heard)
    assert registry.session is not None
    assert all(b.session_id == registry.session.id for b in heard)


async def test_a_dry_run_narrates_nothing(
    client: TestClient, registry: Registry, bus: _Bus, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preview nobody asked to send must not put "writing…" lines on screen."""
    name = await _live_pane(registry, tmp_path)
    beats = [ComposeNotice(stage="start", message="Writing.", terminal=name)]
    monkeypatch.setattr(prompt_composer, "compose", _narrating_compose(beats))

    response = client.post(
        f"/api/agentic-ide/terminals/{name}/prompt",
        json={"prompt": "do the thing", "compose": True, "dry_run": True},
    )

    assert response.status_code == 200
    assert not [e for e in bus.events if isinstance(e, AgenticIdeComposeProgress)]


async def test_a_missing_bus_costs_the_narration_never_the_brief(
    registry: Registry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Headless embedders without an event bus still get their prompt typed."""
    from jarvis.ui.web.agentic_ide_routes import router

    app = FastAPI()
    app.include_router(router)  # deliberately: no app.state.bus
    busless = TestClient(app)
    name = await _live_pane(registry, tmp_path)
    monkeypatch.setattr(prompt_composer, "compose", _narrating_compose([]))

    response = busless.post(
        f"/api/agentic-ide/terminals/{name}/prompt",
        json={"prompt": "do the thing", "compose": True},
    )

    assert response.status_code == 200
    assert response.json()["sent"] == "## Task\nDo the thing."
