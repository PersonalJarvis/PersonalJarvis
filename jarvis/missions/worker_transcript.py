"""Durable per-worker transcripts for the Sub-Agents board.

The ONLY complete record of what a mission worker did — thinking blocks, every
tool call with its input, every tool result — is the per-task
``logs/stream.jsonl`` its subprocess tees line by line. That file lives inside
the mission output directory, which the cleanup sweeps prune (measured
2026-08-14: 7 of 555 mission folders left on disk). This module makes the
record survive the prune and turns it into UI-renderable transcript items:

* :class:`WorkerTranscriptArchiver` — a MissionBus subscriber that copies each
  worker's ``stream.jsonl`` into ``data/agent_transcripts/`` the moment the
  worker finishes (draft ready / killed) and again at mission end (covering
  crash paths where no per-worker terminal event fired).
* :func:`load_transcript` — serves the live file while the worker runs and the
  archived copy afterwards, parsed into ordered transcript items.

Parsing rides :func:`jarvis.missions.stream_evidence.normalize_worker_stream`,
so all three provider stream shapes (claude / codex / gemini) come out as the
same canonical items — the same provider-agnostic doctrine as the Critic.

Every preview passes :func:`jarvis.core.redact.safe_preview` before it leaves
this module: the raw stream may quote a credential a tool printed, and the
transcript endpoint must never become the place where it resurfaces.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from jarvis.core.redact import safe_preview
from jarvis.missions.events import (
    EventEnvelope,
    MissionApproved,
    MissionCancelled,
    MissionFailed,
    MissionTimedOut,
    WorkerDraftReady,
    WorkerKilled,
    WorkerSpawned,
)
from jarvis.missions.stream_evidence import normalize_worker_stream

log = logging.getLogger(__name__)

#: Archive location, relative to the app working directory — the same
#: convention as ``data/flight_recorder`` and ``data/missions.db``.
TRANSCRIPT_DIR = Path("data") / "agent_transcripts"

#: Per-item preview cap. The full record stays in the archived raw file; the
#: transcript endpoint is a reading surface, not a bulk-export channel.
_PREVIEW_CHARS = 2000

#: Read cap for one stream file — mirrors ``run_plan.MAX_STREAM_BYTES`` so a
#: runaway worker log cannot balloon a request.
_MAX_STREAM_BYTES = 24 * 1024 * 1024

#: Transcript-item ceiling per response. The tail wins (the newest activity is
#: what the user opens the view for); a ``truncated`` head item keeps the cut
#: honest — a shortened transcript must never read as a shorter run.
_MAX_ITEMS = 500


def _norm(worker_id: str) -> str:
    return worker_id.replace("-", "")


def transcript_path(worker_id: str) -> Path:
    return TRANSCRIPT_DIR / f"{_norm(worker_id)}.jsonl"


def _meta_path(worker_id: str) -> Path:
    return TRANSCRIPT_DIR / f"{_norm(worker_id)}.meta.json"


def _candidate_stream_paths(worktree: str) -> list[Path]:
    """Possible ``stream.jsonl`` locations for a worker, best-first.

    The worktree is ``<mission>/tasks/<task>/workspace``; its task's log dir
    sits beside it. The mission-wide glob is the fallback for the task-id vs.
    directory-slug mismatch (the orchestrator names the log dir after
    ``step.task_id[:13]``, the worktree after the task slug).
    """
    if not worktree:
        return []
    task_dir = Path(worktree).parent
    candidates = [task_dir / "logs" / "stream.jsonl"]
    mission_dir = task_dir.parent.parent
    try:
        extra = sorted(
            mission_dir.glob("tasks/*/logs/stream.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        extra = []
    for p in extra:
        if p not in candidates:
            candidates.append(p)
    return candidates


def archive_worker_stream(
    worker_id: str, mission_id: str, worktree: str
) -> Path | None:
    """Copy the worker's stream into the durable archive. Best-effort.

    Returns the archive path, or None when no source file exists (yet). Safe
    to call repeatedly — later calls simply refresh the copy.
    """
    for src in _candidate_stream_paths(worktree):
        try:
            if not src.is_file() or src.stat().st_size == 0:
                continue
            dst = transcript_path(worker_id)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            _meta_path(worker_id).write_text(
                json.dumps(
                    {
                        "worker_id": worker_id,
                        "mission_id": mission_id,
                        "archived_ms": int(time.time() * 1000),
                        "source": str(src),
                    }
                ),
                encoding="utf-8",
            )
            return dst
        except OSError as exc:
            log.warning("transcript archive failed for %s: %s", worker_id, exc)
    return None


def _flatten_result_content(content: Any) -> str:
    """Flatten a tool_result ``content`` (str | list[block] | dict) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for blk in content:
            if isinstance(blk, dict):
                parts.append(str(blk.get("text", "")) or json.dumps(blk))
            else:
                parts.append(str(blk))
        return " ".join(p for p in parts if p)
    if isinstance(content, dict):
        return str(content.get("text", "")) or json.dumps(content)
    return "" if content is None else str(content)


def parse_transcript_items(stream_text: str) -> list[dict[str, Any]]:
    """Normalized stream text -> ordered, redacted transcript items.

    Item kinds: ``thinking`` / ``text`` / ``tool_use`` / ``tool_result`` /
    ``result`` / ``truncated``. Tolerant like the evidence extractors — an
    unparseable line is skipped, a half-flushed stream never raises.
    """
    items: list[dict[str, Any]] = []
    tool_names: dict[str, str] = {}
    for line in normalize_worker_stream(stream_text).splitlines():
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        frame_type = obj.get("type")
        if frame_type == "assistant":
            message = obj.get("message") or {}
            for blk in message.get("content") or []:
                if not isinstance(blk, dict):
                    continue
                block_type = blk.get("type")
                if block_type == "thinking":
                    text = str(blk.get("thinking", "")).strip()
                    if text:
                        items.append({
                            "kind": "thinking",
                            "text": safe_preview(text, max_chars=_PREVIEW_CHARS),
                        })
                elif block_type == "text":
                    text = str(blk.get("text", "")).strip()
                    if text:
                        items.append({
                            "kind": "text",
                            "text": safe_preview(text, max_chars=_PREVIEW_CHARS),
                        })
                elif block_type == "tool_use":
                    tool_use_id = str(blk.get("id", ""))
                    tool_name = str(blk.get("name", ""))
                    if tool_use_id:
                        tool_names[tool_use_id] = tool_name
                    items.append({
                        "kind": "tool_use",
                        "tool_use_id": tool_use_id,
                        "tool_name": tool_name,
                        "args_preview": safe_preview(
                            blk.get("input"), max_chars=_PREVIEW_CHARS
                        ),
                    })
        elif frame_type == "user":
            message = obj.get("message") or {}
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for blk in content:
                if not isinstance(blk, dict) or blk.get("type") != "tool_result":
                    continue
                tool_use_id = str(blk.get("tool_use_id", ""))
                items.append({
                    "kind": "tool_result",
                    "tool_use_id": tool_use_id,
                    "tool_name": tool_names.get(tool_use_id, ""),
                    "is_error": bool(blk.get("is_error")),
                    "preview": safe_preview(
                        _flatten_result_content(blk.get("content")),
                        max_chars=_PREVIEW_CHARS,
                    ),
                })
        elif frame_type == "result":
            final_text = obj.get("result")
            if isinstance(final_text, str) and final_text.strip():
                items.append({
                    "kind": "result",
                    "text": safe_preview(
                        final_text.strip(), max_chars=_PREVIEW_CHARS
                    ),
                })
    if len(items) > _MAX_ITEMS:
        dropped = len(items) - _MAX_ITEMS
        items = items[-_MAX_ITEMS:]
        items.insert(0, {"kind": "truncated", "dropped_items": dropped})
    return items


def _read_capped(path: Path) -> str:
    with path.open("rb") as f:
        raw = f.read(_MAX_STREAM_BYTES)
    return raw.decode("utf-8", errors="replace")


def load_transcript(
    worker_id: str, *, worktree: str | None = None
) -> dict[str, Any] | None:
    """The transcript for one worker — live file first, archive second.

    Returns ``{"source", "items", "stream_bytes"}`` or None when neither
    exists (router/errand rows have no worker stream).
    """
    sources: list[tuple[str, Path]] = []
    if worktree:
        sources.extend(("live", p) for p in _candidate_stream_paths(worktree))
    sources.append(("archive", transcript_path(worker_id)))
    for source, path in sources:
        try:
            if not path.is_file() or path.stat().st_size == 0:
                continue
            text = _read_capped(path)
        except OSError as exc:
            log.warning("transcript read failed for %s: %s", worker_id, exc)
            continue
        return {
            "source": source,
            "items": parse_transcript_items(text),
            "stream_bytes": len(text),
        }
    return None


class WorkerTranscriptArchiver:
    """MissionBus subscriber that keeps worker streams past the prune.

    Also the live-path directory for the transcript endpoint: while a worker
    runs, :meth:`worktree_for` points ``load_transcript`` at the tee'd file on
    disk. Errors never propagate — archiving is observability, and a broken
    copy must not block mission flow (same doctrine as ``_safe_dispatch``).
    """

    def __init__(self) -> None:
        self._worktrees: dict[str, str] = {}
        self._mission_workers: dict[str, set[str]] = {}
        self._attached = False

    def attach(self, mission_bus: Any) -> WorkerTranscriptArchiver:
        if self._attached:
            return self
        mission_bus.subscribe_all(self._on_envelope)
        self._attached = True
        return self

    def worktree_for(self, worker_id: str) -> str | None:
        return self._worktrees.get(_norm(worker_id))

    async def _on_envelope(self, envelope: EventEnvelope) -> None:
        payload = envelope.payload
        if isinstance(payload, WorkerSpawned):
            self._worktrees[_norm(payload.worker_id)] = payload.worktree
            self._mission_workers.setdefault(
                envelope.mission_id, set()
            ).add(payload.worker_id)
            return
        if isinstance(payload, (WorkerDraftReady, WorkerKilled)):
            await self._archive(payload.worker_id, envelope.mission_id)
            return
        if isinstance(
            payload,
            (MissionApproved, MissionFailed, MissionCancelled, MissionTimedOut),
        ):
            for worker_id in self._mission_workers.pop(envelope.mission_id, set()):
                await self._archive(worker_id, envelope.mission_id)

    async def _archive(self, worker_id: str, mission_id: str) -> None:
        worktree = self._worktrees.get(_norm(worker_id))
        if not worktree:
            return
        try:
            await asyncio.to_thread(
                archive_worker_stream, worker_id, mission_id, worktree
            )
        except Exception as exc:  # noqa: BLE001 — observability must not block missions
            log.warning("transcript archiver failed for %s: %s", worker_id, exc)


__all__ = [
    "TRANSCRIPT_DIR",
    "WorkerTranscriptArchiver",
    "archive_worker_stream",
    "load_transcript",
    "parse_transcript_items",
    "transcript_path",
]
