"""The REST surface for running a fleet: spawn, split, deliver.

Why this route exists at all, given the voice path already works: the CLI-first
contract (CLAUDE.md §5) says a capability that lives only in the voice path is
not done. Speaking is how the maintainer uses this, but the same fleet has to
be startable from the terminal, from a script, and from the workspace UI — and
one route is also what keeps those three from growing three different ideas of
what "split the work" means.

The honesty rules are the same as the spoken path's and are pinned here too:
the response names who was briefed AND who was not, because a fleet that
half-started is the failure this whole change set exists around.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.agentic_ide import prompt_composer, work_split
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.prompt_composer import ComposedPrompt
from jarvis.agentic_ide.session import Registry
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture(autouse=True)
def _isolated_recents(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.agentic_ide import recents

    store = tmp_path_factory.mktemp("recents") / "recents.json"
    monkeypatch.setattr(recents, "_store_path", lambda: store)


@pytest.fixture(autouse=True)
def _offline_planner(monkeypatch: pytest.MonkeyPatch) -> None:
    """No provider: the deterministic split carries every test here.

    That is the configuration an arbitrary downloader has, so pinning the route
    against it is the point rather than a shortcut (§3).
    """
    monkeypatch.setattr(work_split, "_resolve_splitter", lambda: None)

    async def fake_compose(utterance: str, **kwargs: object) -> ComposedPrompt:
        name = kwargs["terminal_name"]
        instruction = kwargs.get("instruction") or utterance
        return ComposedPrompt(text=f"## {name}\n{instruction}", composed_by="fallback")

    monkeypatch.setattr(prompt_composer, "compose", fake_compose)


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


async def _live_workspace(registry: Registry, folder: Path, count: int) -> list[str]:
    await registry.start(str(folder), [{"agent": "claude"} for _ in range(count)])
    assert registry.session is not None
    for term in list(registry.session.terminals):
        await registry.attach(term.name, 100, 30, _noop, _noop_exit)
    return [t.name for t in registry.session.terminals]


# ------------------------------------------------------------------ fan-out
async def test_the_route_briefs_every_named_terminal(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    names = await _live_workspace(registry, tmp_path, 2)

    response = client.post(
        "/api/agentic-ide/fanout",
        json={"instruction": "analyse the codebase", "terminals": names},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert sorted(d["terminal"] for d in body["delivered"]) == sorted(names)
    assert body["undelivered"] == []


async def test_the_route_reports_who_was_not_reached(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    names = await _live_workspace(registry, tmp_path, 2)
    assert registry.session is not None
    dead = registry.session.find(names[1])
    assert dead is not None
    dead.status = "exited"
    dead.pty_id = None

    body = client.post(
        "/api/agentic-ide/fanout", json={"instruction": "analyse it", "terminals": names}
    ).json()

    assert body["ok"] is False, "a partial fan-out is not a plain success"
    assert [d["terminal"] for d in body["undelivered"]] == [names[1]]
    assert body["undelivered"][0]["reason_code"] == "not_running"


async def test_splitting_gives_each_terminal_its_own_brief(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    for sub in ("jarvis", "docs", "tests"):
        (tmp_path / sub).mkdir()
    names = await _live_workspace(registry, tmp_path, 3)

    body = client.post(
        "/api/agentic-ide/fanout",
        json={
            "instruction": "audit cross-platform support",
            "terminals": names,
            "split": True,
        },
    ).json()

    assert body["ok"] is True
    assert body["split"]["split_by"] == "fallback"
    areas = [a["area"] for a in body["split"]["assignments"]]
    assert len(set(areas)) == 3, "two agents on one area is the waste to avoid"
    prompts = {
        t.name: t.last_prompt for t in (registry.session.terminals if registry.session else [])
    }
    assert len(set(prompts.values())) == 3


async def test_without_split_every_terminal_gets_the_same_brief(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    names = await _live_workspace(registry, tmp_path, 2)

    body = client.post(
        "/api/agentic-ide/fanout", json={"instruction": "run the test suite", "terminals": names}
    ).json()

    assert body["split"] is None
    assert registry.session is not None
    prompts = [(t.last_prompt or "").replace(t.name, "X") for t in registry.session.terminals]
    assert prompts[0] == prompts[1]


async def test_the_route_opens_the_fleet_it_needs(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """ "Four Codex agents on this" with two panes open must open four more."""
    await _live_workspace(registry, tmp_path, 2)

    body = client.post(
        "/api/agentic-ide/fanout",
        json={"instruction": "analyse it", "spawn": [{"count": 4, "agent": "codex"}]},
    ).json()

    assert registry.session is not None
    assert len(registry.session.terminals) == 6
    assert [t.agent for t in registry.session.terminals[2:]] == ["codex"] * 4
    # Only the NEW panes are addressed: the two already working on something
    # else must not be interrupted by a fleet request.
    assert len(body["opened"]) == 4
    assert len(body["delivered"]) + len(body["undelivered"]) == 4


async def test_a_freshly_opened_pane_is_reported_as_not_yet_running(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """KNOWN LIMIT, pinned so it cannot be mistaken for success.

    A pane's agent process starts when the workspace VIEW mounts it, not when
    the registry entry is created — so panes opened by this call are still
    ``pending`` microseconds later and nothing can be typed into them yet.

    The route reports that truthfully rather than claiming a briefed fleet, and
    that honesty is what this test pins. What it does NOT yet do is wait for
    the panes to come up and brief them once they have; until it does,
    spawn+brief in one call opens the fleet and briefs nobody. Delivering after
    the panes go live is the remaining piece of this feature.
    """
    await _live_workspace(registry, tmp_path, 1)

    body = client.post(
        "/api/agentic-ide/fanout",
        json={"instruction": "analyse it", "spawn": [{"count": 2, "agent": "codex"}]},
    ).json()

    assert body["ok"] is False
    assert len(body["undelivered"]) == 2
    assert {d["reason_code"] for d in body["undelivered"]} == {"not_running"}


async def test_a_mixed_fleet_opens_both_kinds(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    await _live_workspace(registry, tmp_path, 1)

    client.post(
        "/api/agentic-ide/fanout",
        json={
            "instruction": "analyse it",
            "spawn": [{"count": 2, "agent": "codex"}, {"count": 3, "agent": "claude"}],
        },
    )

    assert registry.session is not None
    opened = [t.agent for t in registry.session.terminals[1:]]
    assert opened.count("codex") == 2
    assert opened.count("claude") == 3


async def test_naming_nothing_at_all_is_refused(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """Neither terminals nor a spawn request: there is no fleet to brief."""
    await _live_workspace(registry, tmp_path, 2)
    response = client.post("/api/agentic-ide/fanout", json={"instruction": "analyse it"})
    assert response.status_code == 400


async def test_no_workspace_is_a_clean_conflict_not_a_crash(
    client: TestClient, registry: Registry
) -> None:
    response = client.post(
        "/api/agentic-ide/fanout", json={"instruction": "analyse it", "terminals": ["Alex"]}
    )
    assert response.status_code == 409


async def test_dry_run_plans_without_typing_anything(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """Seeing the division of labour before eight agents act on it."""
    for sub in ("jarvis", "docs"):
        (tmp_path / sub).mkdir()
    names = await _live_workspace(registry, tmp_path, 2)

    body = client.post(
        "/api/agentic-ide/fanout",
        json={
            "instruction": "audit it",
            "terminals": names,
            "split": True,
            "dry_run": True,
        },
    ).json()

    assert body["dry_run"] is True
    assert len(body["split"]["assignments"]) == 2
    assert registry.session is not None
    assert all(t.prompts_sent == 0 for t in registry.session.terminals)
