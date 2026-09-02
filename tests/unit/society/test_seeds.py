"""Starter team once per install; proposals only for connected capabilities."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from jarvis.society.capabilities import build_catalog
from jarvis.society.roster import Roster
from jarvis.society.runtime import SocietyRuntime
from jarvis.society.seeds import STARTER_TEAM, propose_seeds, seed_first_run
from jarvis.society.store import SocietyStore


def _tool(name: str, desc: str = "x.") -> SimpleNamespace:
    return SimpleNamespace(name=name, description=desc, risk_tier="monitor", schema={})


async def test_starter_team_is_seeded_once(tmp_path: Path):
    store = SocietyStore(tmp_path / "society.db")
    await store.open()
    roster = Roster(store)
    created = await seed_first_run(roster, store)
    assert created == ["scout", "archivist"]
    scout = await roster.get("scout")
    assert scout is not None and scout.may_assign and scout.tier == "orchestrator"
    archivist = await roster.get("archivist")
    assert archivist is not None and str(archivist.permission_ceiling) == "safe"
    # The user archives Archivist; a restart must not bring it back.
    await roster.archive("archivist")
    assert await seed_first_run(roster, store) == []
    assert [a.agent_id for a in await roster.list()] == ["scout"]
    await store.close()


async def test_runtime_seeds_lead_and_team_by_default(tmp_path: Path):
    runtime = SocietyRuntime(tmp_path)
    await runtime.ensure_started()
    try:
        ids = [a.agent_id for a in await runtime.roster.list()]
        assert ids == ["jarvis", "scout", "archivist"]
    finally:
        await runtime.close()
    quiet = SocietyRuntime(tmp_path / "quiet", seed_starter_team=False)
    await quiet.ensure_started()
    try:
        assert [a.agent_id for a in await quiet.roster.list()] == ["jarvis"]
    finally:
        await quiet.close()


def test_proposals_follow_connected_capabilities():
    catalog = build_catalog(
        {
            "gmail": _tool("gmail", "Read and send mail."),
            "cli_gh": _tool("cli_gh", "GitHub."),
            "spotify": _tool("spotify", "Music."),
        },
        connected=lambda name, kind: name != "spotify",
    )
    proposals = propose_seeds(catalog)
    assert [p["name"] for p in proposals] == ["Mailbox", "Repo"]
    mailbox = proposals[0]
    assert mailbox["focus"][0] == "plugin:gmail"
    assert mailbox["approval_rules"]["require_approval"] == ["plugin:gmail:send"]
    assert mailbox["tier"] == "specialist" and mailbox["reason"] == "plugin:gmail"
    # Already created names are not proposed again.
    assert [p["name"] for p in propose_seeds(catalog, {"mailbox"})] == ["Repo"]
    assert propose_seeds([]) == []


def test_starter_team_shape():
    assert [s["name"] for s in STARTER_TEAM] == ["Scout", "Archivist"]
    assert all(s["description"] for s in STARTER_TEAM)
