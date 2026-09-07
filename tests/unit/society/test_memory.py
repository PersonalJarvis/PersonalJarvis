"""The society's one memory service: scopes, labels, the share gate, the board digest."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.society.agent_tools import MemoryRecallTool, WikiNoteTool
from jarvis.society.events import MsgType
from jarvis.society.memory import MEMORY_SHARE_CAPABILITY, MemoryRefused
from jarvis.society.runtime import SocietyRuntime
from jarvis.society.surface import build_briefing


@pytest.fixture
async def rt(tmp_path: Path):
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    await runtime.ensure_started()
    await runtime.roster.create(name="Scout")
    await runtime.roster.create(name="Archivist")
    try:
        yield runtime
    finally:
        await runtime.close()


CTX = SimpleNamespace(trace_id=uuid4(), user_utterance="", config={}, memory_read=None)


async def test_pages_are_schema_valid_and_scoped(rt: SocietyRuntime, tmp_path: Path):
    vault = tmp_path / "vault"
    scout = await rt.roster.get("scout")
    rel = await rt.memory.remember(scout, "The user hosts on Hetzner.", root=vault)
    assert rel == "society/scout/memory.md"
    text = (vault / rel).read_text(encoding="utf-8")
    assert text.startswith("---\ntype: society\n") and "author: agent:scout" in text
    note, row_id = await rt.memory.note(
        scout, "VPS prices", "Hetzner CX22 costs 4 EUR.", root=vault
    )
    assert note.startswith("society/scout/") and row_id > 0
    # The vault's own page parser accepts the page (it lands in the FTS index).
    from jarvis.memory.wiki.page import parse_markdown

    page = parse_markdown((vault / note).read_text(encoding="utf-8"), vault / note)
    assert page.page_type == "society"


@pytest.mark.parametrize("knowledge_scope", ["own", "shared"])
async def test_recall_only_reads_own_notes_and_preserves_existing_vault(
    rt: SocietyRuntime, tmp_path: Path, knowledge_scope: str
):
    vault = tmp_path / "vault"
    await rt.roster.update("scout", {"knowledge_scope": knowledge_scope})
    scout = await rt.roster.get("scout")
    archivist = await rt.roster.get("archivist")
    await rt.memory.note(scout, "Hetzner notes", "Hetzner hosting is cheap.", root=vault)
    await rt.memory.note(
        archivist, "Hetzner rumor", "Hetzner raises prices, says a blog.", origin="web", root=vault
    )
    got = await rt.memory.propose_shared(
        archivist, "Hosting policy", "We host on Hetzner in Falkenstein.", root=vault
    )
    await rt.memory.promote(got["knowledge_id"], root=vault)
    hits = await rt.memory.recall(scout, "Hetzner hosting", k=5, root=vault)
    assert len(hits) == 1 and hits[0].scope == "own"
    assert hits[0].path.startswith("society/scout/")
    before = {p: p.read_bytes() for p in vault.rglob("*.md")}
    assert any("society/shared/" in p.as_posix() for p in before)
    # An unreadable foreign page proves recall does not even scan the other namespace.
    foreign = vault / "society" / "archivist" / "unreadable.md"
    foreign.write_bytes(b"\xff")
    assert len(await rt.memory.recall(scout, "Hetzner hosting", root=vault)) == 1
    assert all(p.read_bytes() == content for p, content in before.items())


async def test_head_shows_only_own_memory_without_shared_prompts(
    rt: SocietyRuntime, tmp_path: Path
):
    vault = tmp_path / "vault"
    scout = await rt.roster.get("scout")
    archivist = await rt.roster.get("archivist")
    assert "Nothing remembered yet" in rt.memory.head(scout, root=vault)
    await rt.memory.remember(scout, "Prefers short answers.", root=vault)
    await rt.memory.note(archivist, "Web claim", "Some web claim.", origin="web", root=vault)
    got = await rt.memory.propose_shared(archivist, "Team rule", "Always cite sources.", root=vault)
    head = rt.memory.head(scout, root=vault)
    assert "Prefers short answers." in head
    assert "Web claim" not in head and "Team rule" not in head  # not reviewed yet
    await rt.memory.promote(got["knowledge_id"], root=vault)
    head = rt.memory.head(scout, root=vault)
    assert "Team rule" not in head and "Web claim" not in head
    assert "kind shared" not in head
    # And the briefing carries it as its own section.
    briefing = build_briefing(scout, [], [scout], memory=head)
    assert "## Your memory" in briefing and "Prefers short answers." in briefing


async def test_user_wiki_stays_available_only_through_explicit_wiki_lookup(
    rt: SocietyRuntime, tmp_path: Path
):
    from jarvis.memory.wiki.fts_index import ensure_schema, index_vault
    from jarvis.memory.wiki.search import VaultSearch
    from jarvis.plugins.tool.wiki_recall import WikiRecallTool

    vault = tmp_path / "vault"
    vault.mkdir()
    page = vault / "hosting.md"
    page.write_text(
        "---\ntype: note\n---\n# Hosting\nHetzner hosts the project.\n", encoding="utf-8"
    )
    original = page.read_bytes()
    data_dir = tmp_path / "wiki-data"
    data_dir.mkdir()
    conn = sqlite3.connect(data_dir / "jarvis.db")
    try:
        ensure_schema(conn)
        index_vault(vault, conn)
        conn.commit()
        rt._get_cfg = lambda: SimpleNamespace(memory=SimpleNamespace(data_dir=str(data_dir)))
        scout = await rt.roster.get("scout")
        assert await rt.memory.recall(scout, "Hetzner", root=vault) == []
        explicit = WikiRecallTool(search=VaultSearch(vault, conn=conn))
        result = await explicit.execute({"query": "Hetzner"}, CTX)
        assert result.success and "hosting.md" in result.output
        assert page.read_bytes() == original
    finally:
        conn.close()


async def test_share_is_gated_by_the_approvals_queue(rt: SocietyRuntime, tmp_path: Path):
    vault = tmp_path / "vault"
    scout = await rt.roster.get("scout")
    got = await rt.memory.propose_shared(scout, "Hosting", "Use Hetzner.", root=vault)
    assert not (vault / "society" / "shared").exists()
    pending = await rt.approvals.pending()
    assert [a.capability for a in pending] == [MEMORY_SHARE_CAPABILITY]
    assert pending[0].action["knowledge_id"] == got["knowledge_id"]
    rel = await rt.memory.promote(got["knowledge_id"], root=vault)
    text = (vault / rel).read_text(encoding="utf-8")
    assert rel == "society/shared/hosting.md"
    assert "reviewed: true" in text and "promoted_from: society/scout/" in text
    rows = await rt.store.list_knowledge_rows(agent_id="scout")
    assert rows[0]["reviewed"] == 1
    # The source page is still the agent's own and untouched.
    assert "proposed: shared" in (vault / got["path"]).read_text(encoding="utf-8")
    with pytest.raises(MemoryRefused):
        await rt.memory.promote(999_999, root=vault)


async def test_dismiss_marks_reviewed_without_promotion(rt: SocietyRuntime, tmp_path: Path):
    vault = tmp_path / "vault"
    scout = await rt.roster.get("scout")
    _, row_id = await rt.memory.note(scout, "Draft", "Not for the team.", root=vault)
    await rt.memory.dismiss(row_id)
    rows = await rt.store.list_knowledge_rows(agent_id="scout")
    assert rows[0]["reviewed"] == 1
    assert not (vault / "society" / "shared").exists()


async def test_secrets_never_enter_memory(rt: SocietyRuntime, tmp_path: Path):
    vault = tmp_path / "vault"
    scout = await rt.roster.get("scout")
    with pytest.raises(MemoryRefused):
        key = "sk-ant-api03-" + "abcdefghij" * 9
        await rt.memory.remember(scout, f"The key is {key}", root=vault)
    tool = WikiNoteTool(rt, "scout", vault_root=vault)
    res = await tool.execute(
        {"kind": "memory", "text": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij0123"}, CTX
    )
    assert res.success is False and res.output["reason"] == "blocked_by_policy"
    assert not (vault / "society" / "scout" / "memory.md").exists()


async def test_every_operation_is_a_memory_digest_on_the_board(rt: SocietyRuntime, tmp_path: Path):
    vault = tmp_path / "vault"
    scout = await rt.roster.get("scout")
    before = await rt.store.last_seq()
    await rt.memory.remember(scout, "Fact.", root=vault)
    await rt.memory.recall(scout, "fact", root=vault)
    events = [e for e in await rt.store.events_since(before) if e.msg_type is MsgType.DIGEST]
    assert [e.payload["op"] for e in events] == ["remember", "recall"]
    assert all(e.payload["kind"] == "memory" and e.from_agent == "scout" for e in events)
    assert events[1].payload["hits"] == 1


async def test_tools_reject_shared_writes_and_ignore_spoofed_recall_actor(
    rt: SocietyRuntime, tmp_path: Path
):
    vault = tmp_path / "vault"
    note = WikiNoteTool(rt, "scout", vault_root=vault)
    res = await note.execute(
        {"kind": "shared", "title": "Naming", "text": "Slugs are kebab-case."}, CTX
    )
    assert not res.success and res.output["reason"] == "blocked_by_policy"
    assert await rt.approvals.pending() == []
    assert not (vault / "society").exists()
    res = await note.execute(
        {"kind": "note", "title": "Naming", "text": "Slugs are kebab-case."}, CTX
    )
    assert res.success
    bad = await note.execute({"kind": "wat", "text": "x"}, CTX)
    assert bad.success is False
    recall = MemoryRecallTool(rt, "archivist", vault_root=vault)
    out = await recall.execute({"query": "kebab-case slugs", "agent_id": "scout"}, CTX)
    assert out.success and out.output["hits"] == []
    own = await MemoryRecallTool(rt, "scout", vault_root=vault).execute(
        {"query": "kebab-case slugs"}, CTX
    )
    assert own.success and own.output["hits"][0]["scope"] == "own"
    empty = await recall.execute({"query": "zzz-nothing"}, CTX)
    assert empty.output["text"] == "No memory matches."


async def test_overview_lists_shared_agents_and_queue(rt: SocietyRuntime, tmp_path: Path):
    vault = tmp_path / "vault"
    scout = await rt.roster.get("scout")
    await rt.memory.remember(scout, "Likes maps.", root=vault)
    got = await rt.memory.propose_shared(scout, "Maps", "Use OSM.", root=vault)
    view = await rt.memory.overview(root=vault)
    assert view["shared"] == []
    me = next(a for a in view["agents"] if a["agent_id"] == "scout")
    assert "Likes maps." in me["memory_head"] and me["notes"] == 1
    assert {u["id"] for u in view["unreviewed"]} == {1, got["knowledge_id"]}
    await rt.memory.promote(got["knowledge_id"], root=vault)
    view = await rt.memory.overview(root=vault)
    assert view["shared"][0]["title"] == "Maps"
