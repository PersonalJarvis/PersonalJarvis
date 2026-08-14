"""REST API for the sub-agent dashboard (desktop UI).

Endpoints:
- ``GET /api/sub-agents/tree``          → snapshot of all active agents.
- ``GET /api/sub-agents/{trace_id}``    → a single node (for the detail panel).
- ``GET /api/sub-agents/{trace_id}/transcript`` → chat-like worker transcript
  (thinking / tool calls / results), live while running, archived afterwards.

The router expects a ``SubAgentRegistry`` on
``app.state.sub_agent_registry`` (set by ``WebServer._build_app``) and, for
transcripts, a ``WorkerTranscriptArchiver`` on
``app.state.worker_transcript_archiver`` (set when the Phase-6 stack boots).
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging

from fastapi import APIRouter, HTTPException, Request

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sub-agents", tags=["sub-agents"])


@router.get("/tree")
async def get_tree(request: Request) -> dict:
    """Current agent tree (all running + TTL-buffered nodes)."""
    registry = getattr(request.app.state, "sub_agent_registry", None)
    if registry is None:
        return {"roots": [], "all": {}, "count": 0, "server_ts_ns": 0}
    return registry.to_json()


@router.get("/{trace_id}")
async def get_agent(trace_id: str, request: Request) -> dict:
    """A single agent node in detail (for the detail panel)."""
    registry = getattr(request.app.state, "sub_agent_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="sub-agent registry not ready")
    stripped = trace_id.replace("-", "")
    node = registry.snapshot().get(stripped)
    if node is None:
        raise HTTPException(status_code=404, detail=f"agent {trace_id} not found")
    return dataclasses.asdict(node)


@router.get("/{trace_id}/transcript")
async def get_transcript(trace_id: str, request: Request) -> dict:
    """Chat-like transcript of one worker row.

    ``trace_id`` is the board key of a Worker row (= the mission worker_id).
    While the worker runs this reads its tee'd ``stream.jsonl`` live; after
    the mission ends it reads the durable copy in ``data/agent_transcripts/``.
    404 when neither exists — e.g. router or errand rows, which have no
    worker stream. Every preview in the response is redacted + capped at
    parse time (``jarvis.core.redact.safe_preview``).
    """
    # Local import: the missions package is heavyweight and must not load on
    # web-server module import (nothing heavy on the boot critical path).
    from jarvis.missions.worker_transcript import load_transcript

    archiver = getattr(request.app.state, "worker_transcript_archiver", None)
    registry = getattr(request.app.state, "sub_agent_registry", None)

    # The clicked row is usually the MISSION node (workers collapse into it
    # on the board) — the stream belongs to its worker children. Try the row
    # itself first, then its children newest-first.
    candidates = [trace_id.replace("-", "")]
    if registry is not None:
        node = registry.snapshot().get(candidates[0])
        if node is not None:
            candidates.extend(reversed(node.children_trace_ids))

    for candidate in candidates:
        worktree = (
            archiver.worktree_for(candidate) if archiver is not None else None
        )
        result = await asyncio.to_thread(
            load_transcript, candidate, worktree=worktree
        )
        if result is not None:
            return {**result, "worker_trace_id": candidate}
    raise HTTPException(status_code=404, detail=f"no transcript for {trace_id}")
