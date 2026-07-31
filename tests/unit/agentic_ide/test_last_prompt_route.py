"""Reading back, word for word, what a pane was last told to do.

The route exists because "I sent it" and "here is what I sent" are different
claims, and only the second one can be checked by the person who has to trust
it. Jarvis composes a brief, types it into a pane and says so — and the pane
routinely shows nothing at that moment: output parked while it was off screen,
an emulator that never painted, a socket reconnecting, a CLI redrawing its
input box out of view. The user's honest reading of that is that nothing
happened.

It is also the CLI-first half (CLAUDE.md §5): the same proof has to be
available from a terminal and a script, not only from the pane's own overlay.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import Registry
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


@pytest.fixture
def client(registry: Registry) -> TestClient:
    from jarvis.ui.web.agentic_ide_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


async def _live_workspace(registry: Registry, folder: Path, count: int = 1) -> list[str]:
    await registry.start(str(folder), [{"agent": "claude"} for _ in range(count)])
    assert registry.session is not None
    for term in list(registry.session.terminals):
        await registry.attach(term.name, 100, 30, _noop, _noop_exit)
    return [t.name for t in registry.session.terminals]


async def test_it_returns_the_prompt_in_full_not_the_state_excerpt(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """The workspace state carries 200 characters; the proof carries all of it."""
    [name] = await _live_workspace(registry, tmp_path)
    brief = "## Task\n" + ("Review the ranking pipeline. " * 200)
    await registry.send_prompt(name, brief)

    response = client.get(f"/api/agentic-ide/terminals/{name}/prompt")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == name
    assert body["text"].startswith("## Task")
    assert body["chars"] == len(body["text"])
    assert body["chars"] > 200
    assert body["at"] is not None


async def test_a_pane_that_was_never_prompted_answers_rather_than_404s(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """"Nothing was sent here" is an answer; a 404 would read as "no such pane"."""
    [name] = await _live_workspace(registry, tmp_path)

    body = client.get(f"/api/agentic-ide/terminals/{name}/prompt").json()

    assert body["text"] == ""
    assert body["at"] is None
    assert body["prompts_sent"] == 0


async def test_an_unknown_pane_is_a_404(client: TestClient) -> None:
    assert client.get("/api/agentic-ide/terminals/Nobody/prompt").status_code == 404


async def test_the_receipt_counts_every_prompt_that_pane_has_had(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    [name] = await _live_workspace(registry, tmp_path)
    await registry.send_prompt(name, "First instruction")
    await registry.send_prompt(name, "Second instruction")

    body = client.get(f"/api/agentic-ide/terminals/{name}/prompt").json()

    assert body["prompts_sent"] == 2
    # The LAST one, not an accumulation: the receipt answers "what is this pane
    # working on now", and a concatenation of both would answer neither.
    assert body["text"] == "Second instruction"


async def test_the_workspace_state_carries_the_timestamp_the_receipt_is_built_from(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """Without this, a viewer that missed the live notice has no way back to it."""
    [name] = await _live_workspace(registry, tmp_path)
    await registry.send_prompt(name, "Look at the failing test")

    state = client.get("/api/agentic-ide/state").json()
    pane = next(t for t in state["session"]["terminals"] if t["name"] == name)

    assert pane["last_prompt_at"] is not None
    assert pane["last_prompt_chars"] == len("Look at the failing test")
