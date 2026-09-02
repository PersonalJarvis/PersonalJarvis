"""The agent's two hands: one send path with both gates, a namespaced wiki note."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.society.agent_tools import MessageAgentTool, WikiNoteTool
from jarvis.society.capabilities import NEVER_GRANTED, build_catalog
from jarvis.society.events import MsgType
from jarvis.society.runtime import SocietyRuntime


@pytest.fixture
async def rt(tmp_path: Path):
    runtime = SocietyRuntime(tmp_path)
    await runtime.ensure_started()
    await runtime.roster.create(name="Scout")
    await runtime.roster.create(name="Archivist")
    try:
        yield runtime
    finally:
        await runtime.close()


CTX = SimpleNamespace(trace_id=uuid4(), user_utterance="", config={}, memory_read=None)


async def test_message_appends_one_typed_envelope(rt: SocietyRuntime):
    tool = MessageAgentTool(rt, "scout")
    res = await tool.execute({"target": "Archivist", "text": "hi", "kind": "query"}, CTX)
    assert res.success, res.error
    assert res.output["delivered_to"] == "Archivist"
    inbox = await rt.store.inbox_for("archivist")
    assert [(e.msg_type, e.from_agent) for e in inbox] == [(MsgType.QUERY, "scout")]


async def test_message_gates(rt: SocietyRuntime):
    tool = MessageAgentTool(rt, "scout")
    assert (await tool.execute({"target": "nobody", "text": "x"}, CTX)).output["reason"] == (
        "target_unknown"
    )
    assert (await tool.execute({"target": "scout", "text": "x"}, CTX)).success is False
    assert (await tool.execute({"target": "archivist", "text": ""}, CTX)).success is False
    bad_kind = await tool.execute({"target": "archivist", "text": "x", "kind": "assign"}, CTX)
    assert bad_kind.success is False
    await rt.roster.update("archivist", {"state": "paused"})
    paused = await tool.execute({"target": "archivist", "text": "x"}, CTX)
    assert paused.output["reason"] == "target_paused"
    await rt.store.set_kill_switch(True)
    halted = await tool.execute({"target": "archivist", "text": "x"}, CTX)
    assert halted.output["reason"] == "kill_switch"
    ghost = MessageAgentTool(rt, "ghost")
    await rt.store.set_kill_switch(False)
    assert (await ghost.execute({"target": "scout", "text": "x"}, CTX)).success is False


def test_message_tool_is_never_in_the_catalog():
    """Gate 1: no other surface can grant the society tools through the catalog."""
    assert "society_message_agent" in NEVER_GRANTED
    fake = {"society_message_agent": SimpleNamespace(description="", risk_tier="monitor")}
    assert build_catalog(fake) == []


async def test_wiki_note_writes_into_the_namespace_only(rt: SocietyRuntime, tmp_path: Path):
    vault = tmp_path / "vault"
    tool = WikiNoteTool(rt, "scout", vault_root=vault)
    res = await tool.execute(
        {"title": "VPS providers", "text": "Hetzner wins.", "origin": "web"}, CTX
    )
    assert res.success, res.error
    path = vault / res.output["path"]
    assert path.parent == vault / "society" / "scout"
    body = path.read_text(encoding="utf-8")
    assert body.startswith("---\n") and "author: agent:scout" in body and "origin: web" in body
    assert "reviewed: false" in body and "# VPS providers" in body
    rows = await rt.store.list_knowledge_rows(agent_id="scout")
    assert len(rows) == 1 and rows[0]["reviewed"] == 0 and rows[0]["origin"] == "web"

    # Same title twice never overwrites.
    again = await tool.execute({"title": "VPS providers", "text": "Update."}, CTX)
    assert again.output["path"] != res.output["path"]

    # Memory appends to one page.
    m1 = await tool.execute({"kind": "memory", "text": "The user prefers Hetzner."}, CTX)
    m2 = await tool.execute({"kind": "memory", "text": "Budget is 5 EUR/month."}, CTX)
    assert m1.output["path"] == m2.output["path"] == "society/scout/memory.md"
    memory = (vault / "society" / "scout" / "memory.md").read_text(encoding="utf-8")
    assert "Hetzner" in memory and "5 EUR" in memory and memory.count("## ") == 2
    assert not any(p.is_file() for p in vault.iterdir() if p.suffix == ".md")


async def test_wiki_note_refuses_inactive_caller(rt: SocietyRuntime, tmp_path: Path):
    await rt.roster.update("scout", {"state": "paused"})
    tool = WikiNoteTool(rt, "scout", vault_root=tmp_path / "vault")
    res = await tool.execute({"text": "x"}, CTX)
    assert res.success is False
    assert not (tmp_path / "vault").exists()
