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


async def test_runtime_seeds_only_the_lead_by_default(tmp_path: Path):
    """Only Jarvis is on the roster out of the box (maintainer, 2026-09-02); the
    starter team is a seed PROPOSAL, seeded only when asked for."""
    runtime = SocietyRuntime(tmp_path)
    await runtime.ensure_started()
    try:
        lead = await runtime.roster.list()
        assert [a.agent_id for a in lead] == ["jarvis"]
        assert lead[0].avatar["archetype"] == "spirit" and lead[0].avatar["base"] == "gigi"
    finally:
        await runtime.close()
    team = SocietyRuntime(tmp_path / "team", seed_starter_team=True)
    await team.ensure_started()
    try:
        assert [a.agent_id for a in await team.roster.list()] == ["jarvis", "scout", "archivist"]
    finally:
        await team.close()


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
