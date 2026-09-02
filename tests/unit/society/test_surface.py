"""The society surface: per-session hands, the grant filter and the briefing."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.society import runtime as runtime_mod
from jarvis.society.agent_tools import MESSAGE_TOOL_NAME, SHELL_TOOL_NAME, WIKI_NOTE_TOOL_NAME
from jarvis.society.learning import RUN_SKILL_TOOL_NAME
from jarvis.society.runtime import SocietyRuntime
from jarvis.society.surface import (
    agent_id_of,
    build_briefing,
    capability_epoch,
    society_system_extra,
    society_tool_filter,
    society_tools,
)

FOLDER = ["Read", "Write", "Edit", "Ls", "Glob", "Grep", RUN_SKILL_TOOL_NAME]


def _tool(name: str, desc: str = "x.") -> SimpleNamespace:
    return SimpleNamespace(name=name, description=desc, risk_tier="monitor", schema={})


TOOLS = {
    "gmail": _tool("gmail", "Read and send mail."),
    "search-web": _tool("search-web", "Search the web."),
    "wiki-recall": _tool("wiki-recall", "Search the wiki."),
    "wiki-ingest": _tool("wiki-ingest", "Write the wiki."),
    "spawn-worker": _tool("spawn-worker", "Spawn."),
    "cli_gh": _tool("cli_gh", "GitHub."),
}


@pytest.fixture
async def rt(tmp_path: Path):
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False, brain_tools=lambda: TOOLS)
    await runtime.ensure_started()
    try:
        yield runtime
    finally:
        await runtime.close()


def test_agent_id_of():
    assert agent_id_of("society:scout") == "scout"
    assert agent_id_of("society:") is None
    assert agent_id_of("abc123") is None


def test_without_runtime_everything_is_empty():
    runtime_mod.set_current_runtime(None)  # another test's runtime may still be registered
    assert runtime_mod.current_runtime() is None
    session = SimpleNamespace(session_id="society:scout")
    assert society_tools(None, None, session) == {}
    assert society_tool_filter(session) is None


async def test_tools_and_filter_follow_the_roster_row(rt: SocietyRuntime, tmp_path: Path):
    await rt.roster.create(
        name="Mailbox",
        title="Gmail agent",
        description="Handle my mail.",
        focus=["plugin:gmail"],
        denies=["cli:gh"],
    )
    cfg = SimpleNamespace(wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")))
    session = SimpleNamespace(session_id="society:mailbox")
    own = society_tools(cfg, None, session)
    assert set(own) == {MESSAGE_TOOL_NAME, WIKI_NOTE_TOOL_NAME, SHELL_TOOL_NAME, *FOLDER}
    assert "RunCommand" not in own

    # The briefing fills the cache the sync filter reads.
    extra = await society_system_extra(cfg, None, session)
    assert "## You are Mailbox — Gmail agent" in extra
    merged = {**TOOLS, **own}
    filt = society_tool_filter(session)
    assert filt is not None
    picked = list(filt(merged))
    own_names = {MESSAGE_TOOL_NAME, WIKI_NOTE_TOOL_NAME, SHELL_TOOL_NAME, *FOLDER}
    assert set(picked[: len(own_names)]) == own_names
    assert picked[len(own_names)] == "gmail"  # focus first
    assert "spawn-worker" not in picked and "cli_gh" not in picked
    assert "wiki-ingest" not in picked  # writes go through the namespaced note tool
    assert "wiki-recall" in picked


async def test_allowlist_mode_keeps_only_grants(rt: SocietyRuntime, tmp_path: Path):
    await rt.roster.create(
        name="Narrow", grant_mode="allowlist", grants=["core:search-web"], focus=[]
    )
    cfg = SimpleNamespace(wiki=SimpleNamespace(vault_root=str(tmp_path / "vault")))
    session = SimpleNamespace(session_id="society:narrow")
    await society_system_extra(cfg, None, session)
    filt = society_tool_filter(session)
    assert filt is not None
    picked = list(filt({**TOOLS, **society_tools(cfg, None, session)}))
    assert picked[-1] == "search-web"
    assert {MESSAGE_TOOL_NAME, WIKI_NOTE_TOOL_NAME, SHELL_TOOL_NAME, *FOLDER} <= set(picked)
    assert "gmail" not in picked and "cli_gh" not in picked


async def test_unknown_agent_gets_no_briefing(rt: SocietyRuntime):
    session = SimpleNamespace(session_id="society:ghost")
    assert await society_system_extra(None, None, session) == ""


async def test_briefing_is_deterministic_and_complete(rt: SocietyRuntime):
    await rt.roster.create(name="Scout", title="Research scout", tier="orchestrator")
    mailbox, _ = await rt.roster.create(
        name="Mailbox",
        title="Gmail agent",
        description="Read and answer mail; external mail only after approval.",
        focus=["plugin:gmail"],
    )
    catalog = rt.catalog()
    roster = await rt.roster.list()
    a = build_briefing(mailbox, catalog, roster)
    b = build_briefing(mailbox, catalog, roster)
    assert a == b
    assert "## Standing instructions" in a and "external mail only after approval" in a
    assert "Reach for these first:\n- gmail (plugin:gmail): Read and send mail." in a
    assert "Also available" in a and "built-in: search-web, wiki-ingest, wiki-recall" in a
    assert "spawn-worker" not in a
    assert f"Capability epoch: {capability_epoch(catalog)}" in a
    assert "## The Jarvis ecosystem" in a and "society_message_agent" in a
    assert "## Teammates\n- Jarvis — Lead (lead)\n- Scout — Research scout (orchestrator)" in a
    assert "Mailbox" not in a.split("## Teammates")[1]
    assert "You do not assign work" in a


async def test_capability_epoch_changes_when_hands_change(rt: SocietyRuntime):
    before = capability_epoch(rt.catalog())
    rt._get_tools = lambda: {**TOOLS, "spotify": _tool("spotify")}  # noqa: SLF001 — test seam
    assert capability_epoch(rt.catalog()) != before
