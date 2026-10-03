"""M4 curator: RESULT provenance, taint, review gate and crash recovery."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.society.events import MsgType, SocietyEnvelope
from jarvis.society.memory import MEMORY_SHARE_CAPABILITY
from jarvis.society.runtime import SocietyRuntime


async def _runtime(tmp_path: Path) -> SocietyRuntime:
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    await runtime.ensure_started()
    if await runtime.roster.get("scout") is None:
        await runtime.roster.create(name="Scout")
    return runtime


def _result(
    *,
    trace: str,
    output: list[str],
    origin: str = "agent",
    evidence: list[str] | None = None,
) -> SocietyEnvelope:
    return SocietyEnvelope(
        msg_type=MsgType.RESULT,
        from_agent="scout",
        trace_id=trace,
        payload={
            "status": "done",
            "done": "Verified deployment facts and recorded the durable outcome.",
            "output": output,
            "evidence": list(evidence or []),
            "open": [],
            "origin": origin,
        },
    )


@pytest.mark.asyncio
async def test_curator_stages_durable_result_behind_review_gate(tmp_path: Path):
    rt = await _runtime(tmp_path)
    try:
        env = _result(
            trace="curator:artifact",
            output=["file:reports/deployment.md"],
            evidence=["https://example.test/evidence"],
        )
        stored = await rt.store.append_and_publish(env)
        row = await rt.store.knowledge_for_source_event(stored.event_id)
        assert row is not None
        assert row["agent_id"] == "scout"
        assert row["origin"] == "agent"
        assert row["reviewed"] == 0
        assert row["source_event"] == stored.event_id

        source = rt.memory.root() / str(row["wiki_path"])
        text = source.read_text(encoding="utf-8")
        assert "reviewed: false" in text
        assert "proposed: shared" in text
        assert not (rt.memory.root() / "society" / "shared").exists()

        pending = await rt.approvals.pending()
        matching = [
            item
            for item in pending
            if item.capability == MEMORY_SHARE_CAPABILITY
            and int(item.action.get("knowledge_id") or 0) == int(row["id"])
        ]
        assert len(matching) == 1
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_curator_preserves_web_taint_and_is_idempotent(tmp_path: Path):
    rt = await _runtime(tmp_path)
    try:
        stored = await rt.store.append_and_publish(
            _result(
                trace="curator:web",
                output=["chat:society:scout"],
                origin="web",
            )
        )
        await rt.curator.on_result(stored)
        await rt.curator.on_result(stored)

        rows = [
            row
            for row in await rt.store.list_knowledge_rows(agent_id="scout", limit=100)
            if row.get("source_event") == stored.event_id
        ]
        assert len(rows) == 1
        assert rows[0]["origin"] == "web"
        assert rows[0]["reviewed"] == 0
        approvals = [
            item
            for item in await rt.approvals.items()
            if int(item.action.get("knowledge_id") or 0) == int(rows[0]["id"])
        ]
        assert len(approvals) == 1
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_curator_skips_plain_chat_completion(tmp_path: Path):
    rt = await _runtime(tmp_path)
    try:
        stored = await rt.store.append_and_publish(
            _result(trace="curator:chat-only", output=["chat:society:scout"])
        )
        assert await rt.store.knowledge_for_source_event(stored.event_id) is None
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_memory_share_denial_closes_taint_and_cannot_be_reversed(tmp_path: Path):
    from jarvis.ui.web.society_routes import ResolveApprovalBody, resolve_approval

    rt = await _runtime(tmp_path)
    try:
        stored = await rt.store.append_and_publish(
            _result(
                trace="curator:deny",
                output=["file:reports/deny.md"],
                evidence=["https://example.test/source"],
            )
        )
        row = await rt.store.knowledge_for_source_event(stored.event_id)
        assert row is not None and row["reviewed"] == 0
        approval = next(
            item
            for item in await rt.approvals.pending()
            if int(item.action.get("knowledge_id") or 0) == int(row["id"])
        )
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(society=rt))
        )

        denied = await resolve_approval(
            approval.id,
            ResolveApprovalBody(approve=False, note="not shared"),
            request,
        )
        assert denied["approval"]["state"] == "denied"
        assert denied["promoted"] is None
        closed = await rt.store.knowledge_for_source_event(stored.event_id)
        assert closed is not None and closed["reviewed"] == 1
        assert not (rt.memory.root() / "society" / "shared").exists()

        # Approval resolution is idempotent. A later opposite request cannot
        # turn an already-denied review into an approved wiki promotion.
        retried = await resolve_approval(
            approval.id,
            ResolveApprovalBody(approve=True, note="changed mind"),
            request,
        )
        assert retried["approval"]["state"] == "denied"
        assert retried["promoted"] is None
        assert not (rt.memory.root() / "society" / "shared").exists()
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_curator_recovers_result_committed_without_live_publish_handler(tmp_path: Path):
    rt = await _runtime(tmp_path)
    rt.scheduler.detach()
    stored = await rt.store.append_and_publish(
        _result(trace="curator:recover", output=["file:reports/recovered.md"])
    )
    assert await rt.store.knowledge_for_source_event(stored.event_id) is None
    await rt.close()

    recovered = SocietyRuntime(tmp_path, seed_starter_team=False)
    await recovered.ensure_started()
    try:
        row = await recovered.store.knowledge_for_source_event(stored.event_id)
        assert row is not None
        assert row["reviewed"] == 0
        pending = await recovered.approvals.pending()
        assert any(
            item.capability == MEMORY_SHARE_CAPABILITY
            and int(item.action.get("knowledge_id") or 0) == int(row["id"])
            for item in pending
        )
    finally:
        await recovered.close()
