"""The pane-level prompt history is complete, durable, and exact."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import prompt_history
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.session import Registry
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    monkeypatch.setattr(prompt_history, "_store_dir", lambda: tmp_path / "prompt-history")
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


async def _live_pane(
    registry: Registry, folder: Path, monkeypatch: pytest.MonkeyPatch
) -> str:
    await registry.start(str(folder), [{"agent": "claude"}])
    assert registry.session is not None
    term = registry.session.terminals[0]
    await registry.attach(term.name, 100, 30, _noop, _noop_exit)

    async def accepted(*_args: object) -> bool:
        return True

    monkeypatch.setattr(registry, "_write_and_confirm", accepted)
    return term.name


async def test_history_returns_every_full_prompt_newest_first(
    client: TestClient,
    registry: Registry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = await _live_pane(registry, tmp_path, monkeypatch)
    first = ("## First task\n" + ("Inspect the parser carefully. " * 120)).strip()
    second = "## Second task\nWrite the regression tests."
    await registry.send_prompt(name, first)
    await registry.send_prompt(name, second)

    response = client.get(f"/api/agentic-ide/terminals/{name}/prompts")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["available"] == 2
    assert body["complete"] is True
    assert [item["text"] for item in body["items"]] == [second, first]
    assert body["items"][1]["chars"] == len(first)
    assert [item["sequence"] for item in body["items"]] == [2, 1]


async def test_history_survives_after_the_live_memory_copy_is_gone(
    client: TestClient,
    registry: Registry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = await _live_pane(registry, tmp_path, monkeypatch)
    await registry.send_prompt(name, "Review the release checklist")
    assert registry.session is not None
    registry.session.terminals[0].prompt_records.clear()

    body = client.get(f"/api/agentic-ide/terminals/{name}/prompts").json()

    assert body["items"][0]["text"] == "Review the release checklist"
    assert body["complete"] is True


async def test_a_pane_with_no_prompts_has_an_honest_empty_history(
    client: TestClient,
    registry: Registry,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = await _live_pane(registry, tmp_path, monkeypatch)

    body = client.get(f"/api/agentic-ide/terminals/{name}/prompts").json()

    assert body == {
        "name": name,
        "total": 0,
        "available": 0,
        "complete": True,
        "items": [],
    }


def test_history_id_round_trips_through_the_metadata_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.agentic_ide import resume_store

    monkeypatch.setattr(resume_store, "_store_path", lambda: tmp_path / "session.json")
    pane = resume_store.SnapshotTerminal(
        key="t1", name="T1", agent="claude", history_id="pane-lifetime-id"
    )
    snapshot = resume_store.Snapshot(
        saved_at=1.0,
        workspaces=[
            resume_store.SnapshotWorkspace(
                session_id="ide_test",
                folder=str(tmp_path),
                terminals=[pane],
            )
        ],
    )

    resume_store.save(snapshot)
    loaded = resume_store.load()

    assert loaded is not None
    assert loaded.workspaces[0].terminals[0].history_id == "pane-lifetime-id"
