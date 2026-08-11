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
def _fresh_duplicate_memory() -> None:
    """The duplicate guard's memory is module-level; tests must not share it."""
    from jarvis.agentic_ide import fanout

    fanout._RECENT_FANOUTS.clear()
    yield
    fanout._RECENT_FANOUTS.clear()


@pytest.fixture(autouse=True)
def _offline_planner(monkeypatch: pytest.MonkeyPatch) -> None:
    """No provider: the deterministic split carries every test here.

    That is the configuration an arbitrary downloader has, so pinning the route
    against it is the point rather than a shortcut (§3).
    """
    monkeypatch.setattr(work_split, "_resolve_splitter", lambda: (None, ""))

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
    """With nothing able to MOUNT the panes, the route says so instead of lying.

    A pane's agent process starts when the workspace view mounts it, not when
    the registry entry is created. This client has no bus, so no view is ever
    told the panes exist and none of them can come up — waiting would only turn
    an honest "not_running" into a slow one.

    When there IS a bus, the route waits for the panes and briefs them; that is
    ``test_fanout_opens_and_briefs_a_pane_in_one_call`` below.
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


# ------------------------------------------------- prompting a pane that is not there
# A live voice session on 2026-07-27 asked for a pane called "Lee", which was
# not running. The prompt 404'd with a bare "no terminal called that", the caller
# read it as "then open one", and call-signs come from a fixed pool — so the pane
# it opened was called something else and the very same prompt failed again. Four
# rounds later the workspace had four blank panes and nobody had been briefed.
# The error now names the move that actually recovers, and it says the same thing
# whether or not composition was asked for.


async def test_prompting_an_unknown_pane_says_how_to_recover(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    names = await _live_workspace(registry, tmp_path, 2)

    response = client.post(
        "/api/agentic-ide/terminals/Lee/prompt",
        json={"prompt": "do a deep dive", "compose": False},
    )

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "No terminal called 'Lee'" in detail
    assert all(name in detail for name in names), "the running panes are named"
    assert "cannot be requested" in detail, "opening a pane will not create this name"
    assert "/api/agentic-ide/fanout" in detail, "the one call that does recover"


async def test_the_unknown_pane_error_does_not_depend_on_composition(
    client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """One cause, one answer.

    Composition used to decide the shape of this failure: 404 with it on, 409
    with it off. Two different-looking dead ends for one cause is how a caller
    learns to retry instead of to recover.
    """
    await _live_workspace(registry, tmp_path, 1)

    composed = client.post(
        "/api/agentic-ide/terminals/Lee/prompt",
        json={"prompt": "do a deep dive", "compose": True},
    )
    verbatim = client.post(
        "/api/agentic-ide/terminals/Lee/prompt",
        json={"prompt": "do a deep dive", "compose": False},
    )

    assert composed.status_code == verbatim.status_code == 404
    assert composed.json()["detail"] == verbatim.json()["detail"]


class _MountingBus:
    """A bus whose subscriber behaves like the workspace view: it MOUNTS panes.

    That is the whole mechanism the route depends on — the view hears about new
    panes, mounts them, and their agents start. Simulating it here is what makes
    "open one and brief it" testable end to end instead of only up to the point
    where the old code gave up.
    """

    def __init__(self, registry: Registry) -> None:
        self._registry = registry
        self.sections: list[str] = []

    async def publish(self, event: object) -> None:
        section = getattr(event, "section", None)
        if section is not None:
            self.sections.append(section)
            return
        for name in getattr(event, "names", ()):
            await self._registry.attach(name, 100, 30, _noop, _noop_exit)


@pytest.fixture
def mounting_client(registry: Registry) -> TestClient:
    from jarvis.ui.web.agentic_ide_routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.bus = _MountingBus(registry)
    return TestClient(app)


async def test_fanout_opens_and_briefs_a_pane_in_one_call(
    mounting_client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """The recovery the 404 points at has to actually work in one step.

    This is the whole answer to "if no terminal is called that, open one and
    prompt it": the reply carries the call-sign the new pane was really given,
    so the caller never has to guess a name that was never available to it —
    and the pane is actually briefed, which is what "in one call" has to mean.
    """
    await _live_workspace(registry, tmp_path, 1)

    response = mounting_client.post(
        "/api/agentic-ide/fanout",
        json={"instruction": "investigate the empty area", "spawn": [{"count": 1}]},
    )

    assert response.status_code == 200
    body = response.json()
    opened = [t["name"] for t in body["opened"]]
    assert len(opened) == 1
    assert body["ok"] is True
    assert [d["terminal"] for d in body["delivered"]] == opened
    assert body["undelivered"] == []


# ---------------------------------------------------------- duplicate spawns
# Live failure 2026-08-11 16:38: a spoken "open two terminals and have them fix
# the macOS bugs" was executed once, and 37 s later the router brain — whose
# context did not yet show the first fleet's success — issued the same order
# again with spawn. Four agents ended up racing on one task in one shared
# working tree. The route now refuses the repeat while the first fleet's panes
# are still open, BEFORE any pane is created.


async def test_a_repeated_spawn_for_the_same_order_is_refused(
    mounting_client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    await _live_workspace(registry, tmp_path, 1)
    first = mounting_client.post(
        "/api/agentic-ide/fanout",
        json={
            "instruction": "fix all bugs on macOS and check the git history on GitHub",
            "spawn": [{"count": 2}],
        },
    )
    assert first.status_code == 200
    briefed = [d["terminal"] for d in first.json()["delivered"]]
    assert registry.session is not None
    panes_before = len(registry.session.terminals)

    # The router re-dictating an order is not byte-identical — the live
    # duplicate had corrected a transcription typo. Near-identical must match.
    second = mounting_client.post(
        "/api/agentic-ide/fanout",
        json={
            "instruction": "fix all bugs on macOS and check the Git history on GitHub",
            "spawn": [{"count": 2}],
        },
    )

    assert second.status_code == 409
    detail = second.json()["detail"]
    assert all(name in detail for name in briefed), "the working panes are named"
    assert "force" in detail, "the deliberate-repeat escape hatch is named"
    assert len(registry.session.terminals) == panes_before, "no pane was opened"


async def test_force_spawns_the_same_order_again(
    mounting_client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """An explicit "run it again" stays possible — it just has to be explicit."""
    await _live_workspace(registry, tmp_path, 1)
    instruction = "fix all bugs on macOS and check the git history on GitHub"
    mounting_client.post(
        "/api/agentic-ide/fanout", json={"instruction": instruction, "spawn": [{"count": 2}]}
    )

    repeat = mounting_client.post(
        "/api/agentic-ide/fanout",
        json={"instruction": instruction, "spawn": [{"count": 2}], "force": True},
    )

    assert repeat.status_code == 200
    assert len(repeat.json()["opened"]) == 2


async def test_rebriefing_existing_panes_is_never_a_duplicate(
    mounting_client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """Only a SPAWN repeat is refused.

    Re-briefing panes the user can see is cheap and often deliberate ("tell
    them again"); the guard exists for the invisible second fleet, not for it.
    """
    names = await _live_workspace(registry, tmp_path, 2)
    instruction = "fix all bugs on macOS and check the git history on GitHub"
    first = mounting_client.post(
        "/api/agentic-ide/fanout", json={"instruction": instruction, "terminals": names}
    )
    assert first.status_code == 200

    again = mounting_client.post(
        "/api/agentic-ide/fanout", json={"instruction": instruction, "terminals": names}
    )

    assert again.status_code == 200


async def test_a_repeat_after_the_first_fleet_closed_is_legitimate(
    mounting_client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """Gone panes make a re-run a fresh order, not a duplicate."""
    await _live_workspace(registry, tmp_path, 1)
    instruction = "fix all bugs on macOS and check the git history on GitHub"
    first = mounting_client.post(
        "/api/agentic-ide/fanout", json={"instruction": instruction, "spawn": [{"count": 2}]}
    )
    briefed = [d["terminal"] for d in first.json()["delivered"]]
    await registry.close_terminals(briefed)

    second = mounting_client.post(
        "/api/agentic-ide/fanout", json={"instruction": instruction, "spawn": [{"count": 2}]}
    )

    assert second.status_code == 200


async def test_short_steering_strokes_are_never_duplicates(
    mounting_client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """Fleet-wide "continue" twice in a row is normal steering, not a repeat."""
    await _live_workspace(registry, tmp_path, 1)
    for _ in range(2):
        response = mounting_client.post(
            "/api/agentic-ide/fanout",
            json={"instruction": "continue", "spawn": [{"count": 1}]},
        )
        assert response.status_code == 200


async def test_opening_panes_brings_the_workspace_forward(
    mounting_client: TestClient, registry: Registry, tmp_path: Path
) -> None:
    """A workspace behind another section never starts the panes it just got.

    The mount is what starts a pane's agent, so a fan-out that opens panes
    without bringing the view forward can be relied on to brief nobody.
    """
    await _live_workspace(registry, tmp_path, 1)

    mounting_client.post(
        "/api/agentic-ide/fanout",
        json={"instruction": "investigate the empty area", "spawn": [{"count": 1}]},
    )

    assert mounting_client.app.state.bus.sections == ["agentic-ide"]
