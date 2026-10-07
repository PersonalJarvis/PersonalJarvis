"""Jarvis agents by spoken name: aliases, messaging, and the pane/agent split."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.agentic_ide.orchestration import WorkspaceOrchestrator
from jarvis.brain.workspace_tool import _point_at_jarvis_agent
from jarvis.society import runtime as runtime_mod
from jarvis.society.agent_names import (
    ROLE_ALIASES,
    jarvis_agent_hint,
    load_custom_aliases,
    lookup_agent,
    role_tags,
    set_custom_aliases,
)
from jarvis.society.agent_tools import MessageAgentTool
from jarvis.society.runtime import SocietyRuntime

CTX = SimpleNamespace(trace_id=uuid4(), user_utterance="", config={}, memory_read=None)


@pytest.fixture
async def rt(tmp_path: Path):
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    await runtime.ensure_started()
    await runtime.roster.create(name="Jarvis-Scout", title="GitHub trends")
    await runtime.roster.create(name="Jarvis Code", title="Coding agent")
    await runtime.roster.create(name="Gmail-Tagesreport", title="Specialist")
    try:
        yield runtime
    finally:
        runtime_mod.set_current_runtime(None)
        await runtime.close()


async def test_exact_names_and_ids_resolve_exactly_as_before(rt):
    for ref in ("Jarvis Code", "jarvis-code", "JARVIS CODE"):
        found = await lookup_agent(rt, ref)
        assert found.agent is not None and found.agent.agent_id == "jarvis-code"
        assert found.match_info() is None  # exact: nothing to explain
    lead = await lookup_agent(rt, "jarvis")
    assert lead.agent is not None and lead.agent.agent_id == "jarvis"


async def test_misheard_name_reports_how_it_matched(rt):
    found = await lookup_agent(rt, "Jarvis Kot", surface="test")
    assert found.agent is not None and found.agent.agent_id == "jarvis-code"
    assert found.match_info() == {
        "heard": "Jarvis Kot",
        "matched": "Jarvis Code",
        "method": "phonetic",
        "score": pytest.approx(found.resolution.best.score, abs=1e-3),
    }


async def test_role_aliases_follow_the_title(rt):
    code = await rt.roster.resolve("jarvis-code")
    scout = await rt.roster.resolve("jarvis-scout")
    assert role_tags(code) == frozenset({"coding"})
    assert role_tags(scout) == frozenset()
    assert "programmierer" in ROLE_ALIASES["coding"]  # i18n-allow: input vocabulary
    found = await lookup_agent(rt, "Programmierer")  # i18n-allow: speech-recognition input
    assert found.agent is not None and found.agent.agent_id == "jarvis-code"


async def test_custom_aliases_are_stored_and_used(rt):
    spoken = ["Ranger", " Ranger ", "Trendy"]
    stored = await set_custom_aliases(rt.store, "jarvis-scout", spoken)
    assert stored == ("Ranger", "Trendy")
    assert (await load_custom_aliases(rt.store))["jarvis-scout"] == ("Ranger", "Trendy")
    found = await lookup_agent(rt, "Trendy")
    assert found.agent is not None and found.agent.agent_id == "jarvis-scout"
    assert found.resolution.best.method == "alias"


async def test_an_archived_exact_name_does_not_shadow_a_live_agent(rt):
    await rt.roster.create(name="Scout")
    await rt.roster.archive("scout")
    found = await lookup_agent(rt, "Scout")
    assert found.agent is not None and found.agent.agent_id == "jarvis-scout"


async def test_message_agent_reaches_a_misheard_teammate(rt):
    tool = MessageAgentTool(rt, "jarvis")
    res = await tool.execute({"target": "Java Scout", "text": "Run another scout."}, CTX)
    assert res.success, res.error
    assert res.output["target"] == "Jarvis-Scout"


async def test_message_agent_names_candidates_instead_of_a_bare_miss(rt):
    tool = MessageAgentTool(rt, "jarvis")
    close = await tool.execute({"target": "Charles Code", "text": "hi"}, CTX)
    assert close.success is False and close.output["reason"] == "target_unknown"
    assert close.output["did_you_mean"] == ["Jarvis Code"]
    unknown = await tool.execute({"target": "Telegram", "text": "hi"}, CTX)
    assert unknown.output["available"] == ["Gmail-Tagesreport", "Jarvis Code", "Jarvis-Scout"]


async def test_a_pane_lookup_points_at_the_jarvis_agent(rt):
    """Live 2026-10-02: the name went to the coding-pane tool and was 'not found'."""
    runtime_mod.set_current_runtime(rt)
    await rt.roster.list()  # the snapshot a synchronous reader sees
    assert jarvis_agent_hint("Java Scout") == {
        "heard": "Java Scout",
        "agents": ["Jarvis-Scout"],
        "certain": True,
    }
    miss = {
        "status": "needs_clarification",
        "kind": "agent",
        "candidates": [],
        "reason": "Nothing open matches 'Java Scout'.",
    }
    pointed = _point_at_jarvis_agent({"action": "resolve", "agent": "Java Scout"}, miss)
    assert pointed["jarvis_agent"]["agents"] == ["Jarvis-Scout"]
    assert "delegate_to_agent" in pointed["reason"] and "message_agent" in pointed["reason"]
    assert pointed["reason"].endswith("Nothing open matches 'Java Scout'.")
    # A pane that was found, or a create, is left alone.
    resolved = {"status": "resolved", "target": {}}
    args = {"action": "resolve", "agent": "Java Scout"}
    assert _point_at_jarvis_agent(args, resolved) is resolved
    assert _point_at_jarvis_agent({"action": "create", "agent": "Java Scout"}, miss) is miss
    assert jarvis_agent_hint("Telegram") is None


def _graph(*names: str) -> dict:
    agents = [
        {"id": f"pane:{i}", "name": name, "agent": "claude", "status": "live",
         "activity": "waiting", "accepts_tasks": True}
        for i, name in enumerate(names)
    ]  # fmt: skip
    return {
        "active_workspace_id": "ws",
        "projects": [
            {
                "id": "p",
                "name": "Personal Jarvis",
                "path": "/p",
                "workspaces": [
                    {"id": "ws", "name": "Personal Jarvis", "status": "open", "agents": agents}
                ],
            }
        ],
    }


def test_a_misheard_custom_pane_name_resolves():
    orchestrator = WorkspaceOrchestrator(None, None, None)
    graph = _graph("T1", "Update Deep Dive", "Security Audit")
    result = orchestrator.resolve({"agent": "Update Deep Drive"}, graph)
    assert result["status"] == "resolved", result
    assert result["target"]["agent"] == "Update Deep Dive"


def test_a_close_pane_name_is_a_question_and_positions_stay_exact():
    orchestrator = WorkspaceOrchestrator(None, None, None)
    graph = _graph("T1", "T11", "Security Audit")
    close = orchestrator.resolve({"agent": "Secret Audio"}, graph)
    assert close["status"] == "needs_clarification"
    assert [c["name"] for c in close["candidates"]] == ["Security Audit"]
    # "T12" is not a garbled "T11": positions never fuzzy-match.
    assert orchestrator.resolve({"agent": "T12"}, graph)["status"] == "needs_clarification"
