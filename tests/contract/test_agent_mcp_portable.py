"""Carrying an ecosystem to another machine: what travels, and what must not.

The load-bearing test here is the negative one. An export is a file people mail
themselves, drop in a repo, or paste into a chat — so a bundle that could carry
a credential or a private transcript is a feature nobody can safely use. The
allowlist is checked against a roster row that has EVERY field populated, so a
field added later shows up as a failure rather than as a leak.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from jarvis.core import runtime_refs
from jarvis.mcp.agents import tools as agent_tools
from jarvis.mcp.agents.portable import (
    BUNDLE_VERSION,
    NEVER_EXPORTED,
    PORTABLE_FIELDS,
    BundleError,
    export_bundle,
    import_bundle,
)
from jarvis.society.runtime import SocietyRuntime


@pytest.fixture
async def source(tmp_path: Path):
    """A machine with a team on it."""
    runtime = SocietyRuntime(tmp_path / "src", seed_starter_team=False)
    await runtime.ensure_started()
    try:
        yield runtime
    finally:
        await runtime.close()


@pytest.fixture
async def target(tmp_path: Path):
    """A fresh machine, wired up the way the MCP tools reach it."""
    runtime = SocietyRuntime(tmp_path / "dst", seed_starter_team=False)
    await runtime.ensure_started()
    app = FastAPI()
    app.state.society = runtime
    app.state.society_factory = lambda: runtime
    runtime_refs.set_web_app(app)
    try:
        yield runtime
    finally:
        runtime_refs._reset_for_tests()
        await runtime.close()


async def _seed(runtime: Any) -> None:
    await runtime.roster.create(
        name="Scout",
        title="Research scout",
        description="Finds and checks facts on the open web.",
        provider="openai",
        model="gpt-5.2",
        daily_budget_usd=3.5,
        permission_ceiling="ask",
        focus=["plugin:web"],
        max_concurrent_runs=2,
    )
    await runtime.roster.create(
        name="Archivist",
        title="Knowledge curator",
        description="Keeps the wiki tidy.",
        provider="anthropic",
        model="claude-sonnet-5",
    )


# ------------------------------------------------------------ what travels


async def test_a_bundle_carries_the_design(source) -> None:
    await _seed(source)

    bundle = await export_bundle(source)

    assert bundle["kind"] == "jarvis.agent-ecosystem"
    assert bundle["version"] == BUNDLE_VERSION
    assert bundle["agent_count"] == 2
    scout = next(a for a in bundle["agents"] if a["name"] == "Scout")
    assert scout["title"] == "Research scout"
    assert scout["provider"] == "openai" and scout["model"] == "gpt-5.2"
    assert scout["daily_budget_usd"] == 3.5
    assert scout["permission_ceiling"] == "ask"
    assert scout["max_concurrent_runs"] == 2
    assert scout["focus"] == ["plugin:web"]


async def test_the_lead_seat_never_travels(source) -> None:
    """Jarvis exists on every install; carrying it would overwrite the target's."""
    await _seed(source)

    bundle = await export_bundle(source)

    assert "Jarvis" not in {a["name"] for a in bundle["agents"]}


# --------------------------------------------------------- what must not


async def test_no_excluded_field_ever_reaches_a_bundle(source) -> None:
    await _seed(source)

    bundle = await export_bundle(source)

    for row in bundle["agents"]:
        for forbidden, why in NEVER_EXPORTED.items():
            assert forbidden not in row, f"{forbidden} leaked into a bundle — {why}"


async def test_the_allowlist_is_the_whole_story(source) -> None:
    """Every exported key was named on purpose; a new roster field cannot slip in."""
    await _seed(source)

    bundle = await export_bundle(source)

    for row in bundle["agents"]:
        unexpected = set(row) - set(PORTABLE_FIELDS)
        assert not unexpected, (
            f"these fields are exported but not on the allowlist: {sorted(unexpected)}. "
            "Add them to PORTABLE_FIELDS deliberately, or to NEVER_EXPORTED with a reason."
        )


async def test_a_bundle_holds_nothing_that_reads_like_a_secret(source) -> None:
    """A blunt check on the serialized agent rows — the form people actually share.

    Only the rows: the bundle's own note says the words "no secrets", and the
    point of this test is what came out of the ROSTER, not our prose.
    """
    await _seed(source)

    text = json.dumps((await export_bundle(source))["agents"]).lower()

    for marker in ("api_key", "apikey", "secret", "token", "password", "bearer", "sk-"):
        assert marker not in text, f"a bundle must never contain {marker!r}"


# --------------------------------------------------------------- importing


async def test_an_import_rebuilds_the_team_on_another_machine(source, target) -> None:
    await _seed(source)
    bundle = await export_bundle(source)

    report = await import_bundle(target, bundle)

    assert sorted(report["created"]) == ["Archivist", "Scout"]
    assert report["updated"] == []
    landed = {a.name: a for a in await target.roster.list()}
    assert "Scout" in landed
    assert landed["Scout"].provider == "openai"
    assert landed["Scout"].daily_budget_usd == 3.5
    assert landed["Scout"].permission_ceiling == "ask"


async def test_importing_twice_changes_nothing_the_second_time(source, target) -> None:
    """Re-applying a bundle must be safe — that is what makes a retry harmless."""
    await _seed(source)
    bundle = await export_bundle(source)

    first = await import_bundle(target, bundle)
    second = await import_bundle(target, bundle)

    assert len(first["created"]) == 2
    assert second["created"] == []
    assert sorted(second["updated"]) == ["Archivist", "Scout"]
    assert len(await target.roster.list()) == 3  # the two plus this machine's lead


async def test_dry_run_reports_the_plan_and_touches_nothing(source, target) -> None:
    await _seed(source)
    bundle = await export_bundle(source)

    plan = await import_bundle(target, bundle, dry_run=True)

    assert plan["dry_run"] is True
    assert sorted(plan["created"]) == ["Archivist", "Scout"]
    assert [a.name for a in await target.roster.list()] == ["Jarvis"]


async def test_overwrite_off_leaves_an_existing_agent_alone(source, target) -> None:
    await _seed(source)
    await target.roster.create(
        name="Scout", title="A different scout", description="Mine.", provider="ollama"
    )
    bundle = await export_bundle(source)

    report = await import_bundle(target, bundle, overwrite=False)

    assert report["created"] == ["Archivist"]
    assert [s["name"] for s in report["skipped"]] == ["Scout"]
    mine = next(a for a in await target.roster.list() if a.name == "Scout")
    assert mine.provider == "ollama", "my Scout must survive untouched"


async def test_a_wrong_bundle_is_refused_with_a_sentence(target) -> None:
    for bad, expected in (
        ("not a dict", "a bundle is a JSON object"),
        ({"kind": "something-else", "version": 1, "agents": []}, "not a Jarvis ecosystem bundle"),
        ({"kind": "jarvis.agent-ecosystem", "version": 99, "agents": []}, "cannot be read"),
        ({"kind": "jarvis.agent-ecosystem", "version": BUNDLE_VERSION, "agents": []}, "no agents"),
        (
            {"kind": "jarvis.agent-ecosystem", "version": BUNDLE_VERSION, "agents": [{"x": 1}]},
            "has no name",
        ),
    ):
        with pytest.raises(BundleError, match=expected):
            await import_bundle(target, bad)


# ------------------------------------------------------- through the tools


async def test_the_round_trip_works_over_the_mcp_tools(source, target) -> None:
    """What a client actually does: export on one box, import on the other."""
    await _seed(source)
    bundle = await export_bundle(source)  # the other machine's client did this

    text = await agent_tools.call("ecosystem_import", {"bundle": bundle, "dry_run": True})
    plan = json.loads(text)
    assert sorted(plan["created"]) == ["Archivist", "Scout"]

    applied = json.loads(await agent_tools.call("ecosystem_import", {"bundle": bundle}))
    assert sorted(applied["created"]) == ["Archivist", "Scout"]

    exported = json.loads(await agent_tools.call("ecosystem_export", {}))
    assert {a["name"] for a in exported["agents"]} == {"Archivist", "Scout"}


async def test_a_bundle_may_arrive_as_a_json_string(source, target) -> None:
    """Clients that cannot send a nested object send the text they saved."""
    await _seed(source)
    bundle_text = json.dumps(await export_bundle(source))

    report = json.loads(await agent_tools.call("ecosystem_import", {"bundle": bundle_text}))

    assert sorted(report["created"]) == ["Archivist", "Scout"]


async def test_broken_json_says_so(target) -> None:
    answer = await agent_tools.call("ecosystem_import", {"bundle": "{ nope"})
    assert "not valid JSON" in answer
