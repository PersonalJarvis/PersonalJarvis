"""The roster: adopt-before-mint, one lead, validation, no session column."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jarvis.society.events import AgentState, GrantMode, PermissionCeiling, Tier
from jarvis.society.failure_reasons import FailureReason
from jarvis.society.roster import Roster, RosterError, canonical_session_id, slugify
from jarvis.society.store import SocietyStore


@pytest.fixture
async def roster(tmp_path: Path):
    store = SocietyStore(tmp_path / "society.db")
    await store.open()
    try:
        yield Roster(store)
    finally:
        await store.close()


def test_slugify_folds_and_never_empties():
    assert slugify("Mail Bot") == "mail-bot"
    assert slugify("Süßer Bär!") == "susser-bar"  # i18n-allow: umlaut folding sample
    assert slugify("!!!") == "agent"


def test_canonical_session_is_a_pure_function():
    assert canonical_session_id("scout") == "society:scout"


async def test_create_then_adopt_by_name(roster: Roster):
    scout, created = await roster.create(name="Scout", title="Research scout")
    assert created is True
    assert scout.agent_id == "scout"
    assert scout.tier is Tier.SPECIALIST
    assert scout.state is AgentState.ACTIVE
    assert scout.grant_mode is GrantMode.ALL
    assert scout.permission_ceiling is PermissionCeiling.MONITOR
    assert scout.wiki_namespace == "society/scout/"
    assert scout.session_id == "society:scout"

    again, created_again = await roster.create(name="scout", title="ignored")
    assert created_again is False
    assert again.agent_id == "scout"
    assert again.title == "Research scout"
    assert len(await roster.list()) == 1


async def test_only_jarvis_can_be_the_lead(roster: Roster):
    with pytest.raises(RosterError) as exc:
        await roster.create(name="Boss", tier=Tier.LEAD)
    assert exc.value.reason is FailureReason.TIER_NOT_ALLOWED

    jarvis, _ = await roster.create(name="Jarvis", tier=Tier.LEAD, title="Lead")
    assert jarvis.agent_id == "jarvis"
    assert jarvis.may_assign

    with pytest.raises(RosterError):
        await roster.update("jarvis", {"tier": "specialist"})
    scout, _ = await roster.create(name="Scout")
    with pytest.raises(RosterError):
        await roster.update(scout.agent_id, {"tier": "lead"})
    with pytest.raises(RosterError):
        await roster.archive("jarvis")


async def test_orchestrator_defaults_and_parent_validation(roster: Roster):
    orch, _ = await roster.create(name="Planner", tier="orchestrator")
    assert orch.max_concurrent_runs == 3
    assert orch.may_assign
    with pytest.raises(RosterError) as exc:
        await roster.create(name="Kid", parent_agent_id="nobody")
    assert exc.value.reason is FailureReason.TARGET_UNKNOWN
    kid, _ = await roster.create(name="Kid", parent_agent_id="planner")
    assert kid.parent_agent_id == "planner"
    with pytest.raises(RosterError):
        await roster.update("kid", {"parent_agent_id": "kid"})


async def test_update_validates_and_sorts_grants(roster: Roster):
    scout, _ = await roster.create(name="Scout")
    updated = await roster.update(
        scout.agent_id,
        {
            "grant_mode": "allowlist",
            "grants": ["plugin:gmail", "core:search-web", "plugin:gmail"],
            "focus": ["plugin:gmail"],
            "approval_rules": {"require_approval": ["plugin:gmail:send"]},
            "daily_budget_usd": 1.5,
            "permission_ceiling": "ask",
            "skills": None,
        },
    )
    assert updated.grant_mode is GrantMode.ALLOWLIST
    assert updated.grants == ["core:search-web", "plugin:gmail"]
    assert updated.approval_rules == {
        "require_approval": ["plugin:gmail:send"],
        "always_allow": [],
    }
    assert updated.daily_budget_usd == 1.5
    assert updated.skills is None

    uncapped = await roster.update(scout.agent_id, {"daily_budget_usd": 0})
    assert uncapped.daily_budget_usd == 0.0
    with pytest.raises(RosterError):
        await roster.update(scout.agent_id, {"daily_budget_usd": -1})

    with pytest.raises(RosterError):
        await roster.update(scout.agent_id, {"grants": "plugin:gmail"})
    with pytest.raises(RosterError):
        await roster.update(scout.agent_id, {"permission_ceiling": "block"})
    with pytest.raises(RosterError):
        await roster.update(scout.agent_id, {"max_concurrent_runs": 0})
    with pytest.raises(RosterError):
        await roster.update(scout.agent_id, {"agent_id": "other"})
    with pytest.raises(RosterError) as exc:
        await roster.update("ghost", {"title": "x"})
    assert exc.value.reason is FailureReason.TARGET_UNKNOWN


async def test_archive_hides_from_default_list(roster: Roster):
    scout, _ = await roster.create(name="Scout")
    await roster.archive(scout.agent_id)
    assert await roster.list() == []
    archived = await roster.list(include_archived=True)
    assert [a.state for a in archived] == [AgentState.ARCHIVED]


async def test_resolve_by_id_name_or_slug(roster: Roster):
    await roster.create(name="Mail Bot")
    assert (await roster.resolve("mail-bot")) is not None
    assert (await roster.resolve("MAIL BOT")) is not None
    assert (await roster.resolve("Mail bot")) is not None
    assert (await roster.resolve("nobody")) is None


async def test_name_rules(roster: Roster):
    with pytest.raises(RosterError):
        await roster.create(name="")
    with pytest.raises(RosterError):
        await roster.create(name="a/b")
    with pytest.raises(RosterError):
        await roster.create(name="@scout")


def test_roster_has_no_session_pointer_column(tmp_path: Path):
    """The canonical chat is a pure function of agent_id; a pointer column is
    the drift bug this design refuses to ship."""
    schema = (Path("jarvis/society/society_schema.sql")).read_text(encoding="utf-8")
    conn = sqlite3.connect(tmp_path / "probe.db")
    conn.executescript(schema)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(society_agents)")}
    conn.close()
    assert not any("session" in c or "chat" in c for c in cols), cols


def test_record_to_dict_is_json_shaped(tmp_path: Path):
    from jarvis.society.roster import AgentRecord

    rec = AgentRecord.from_row(
        {
            "agent_id": "x",
            "name": "X",
            "tier": "specialist",
            "created_ms": 1,
            "updated_ms": 1,
            "approval_rules_json": "not json",
            "skills_json": '["a"]',
        }
    )
    d = rec.to_dict()
    assert d["approval_rules"] == {"require_approval": [], "always_allow": []}
    assert d["skills"] == ["a"]
    assert d["session_id"] == "society:x"


async def test_focus_keeps_the_order_it_was_given(roster: Roster):
    """Focus is what the agent reaches for FIRST, in that order — never sorted."""
    agent, _ = await roster.create(
        name="Mailbox",
        focus=["plugin:gmail", "cli:gh", "plugin:gmail", "core:search-web"],
        grants=["z", "a"],
    )
    assert agent.focus == ["plugin:gmail", "cli:gh", "core:search-web"]
    assert agent.grants == ["a", "z"]  # grants stay a sorted set
    updated = await roster.update("mailbox", {"focus": ["core:search-web", "plugin:gmail"]})
    assert updated.focus == ["core:search-web", "plugin:gmail"]
