"""OpenClaw worker aggregator for the mission-detail API.

Phase 9 (Welle 4 UI): once the OpenClaw harness is wired into the
mission-manager worker layer, it emits ``WorkerSpawned`` events with a
session_id and ``step["harness"] == "openclaw"``. This aggregator pulls all
OpenClaw-specific UI fields out of a mission's event stream —
``state_dir``, ``log_path``, ``reattach_status``, ``cost_usd``, ``tokens``.

Pure helpers, no IO. Tests for this live in
``tests/missions/api/test_missions_worker_aggregator.py``.

Contract compatibility (see ``docs/openclaw-bridge.md``):
- ``MISSION_STATE_DIR`` convention from ``OpenClawHarness._build_spec``:
  ``<worktree>/.openclaw_state/<session_id>/openclaw_state``.
- ``log_path`` convention: OpenClaw writes ``run.log`` into state_dir
  (see the Welle-3 spike results §SP-4).
- Reattach status: ``live`` as long as neither ``WorkerKilled`` nor a
  mission-terminal event follows; otherwise ``ended``/``killed``.
"""
from __future__ import annotations

import os.path
from collections.abc import Iterable
from typing import Any, Literal

from jarvis.missions.events import EventEnvelope

ReattachStatus = Literal["live", "ended", "killed", "unknown"]


def _is_worker_mission(spawn_payload: Any) -> bool:
    """Detection heuristic for an OpenClaw worker.

    Primary: ``step["harness"] == "openclaw"`` — this is the canonical marker
    once the worker layer calls the OpenClaw harness.

    Fallback: ``session_id is not None`` AND ``model`` contains a
    ``provider/model`` slash (OpenClaw's provider-prefix convention, see
    ``OpenClawHarness.build_spawn_args``). This way the UI still works even
    when the worker layer doesn't set the ``step`` marker yet.
    """
    step = getattr(spawn_payload, "step", None) or {}
    if isinstance(step, dict) and step.get("harness") == "openclaw":
        return True

    session_id = getattr(spawn_payload, "session_id", None)
    model = getattr(spawn_payload, "model", "") or ""
    return bool(session_id) and "/" in model


def _derive_state_dir(worktree: str, session_id: str) -> str:
    """Reproduces the ``OpenClawHarness._build_spec`` convention.

    Returns a *forward-slash* path (UI-friendly even on Windows).
    """
    if not worktree or not session_id:
        return ""
    return os.path.join(worktree, ".openclaw_state", session_id, "openclaw_state").replace(
        "\\", "/"
    )


def _derive_log_path(state_dir: str) -> str:
    """OpenClaw writes ``run.log`` into state_dir (Welle-3 SP-4 finding)."""
    if not state_dir:
        return ""
    return f"{state_dir}/run.log"


def extract_worker_missions(
    events: Iterable[EventEnvelope],
) -> list[dict[str, Any]]:
    """Aggregates OpenClaw worker snapshots from an event stream.

    Idempotent + no IO. Entry order matches the
    chronological spawn order.

    Fields per worker:
        worker_id, model, session_id, state_dir, log_path,
        cost_usd, tokens_used, reattach_status, spawned_ms, ended_ms,
        ended_reason

    Aggregation rules:
        - ``cost_usd`` = the latest ``WorkerProgress.cost_so_far`` OR
          ``WorkerDraftReady.cost_usd`` (whichever came last wins).
        - ``tokens_used`` analogously via ``tokens_so_far`` / ``tokens_used``.
        - ``reattach_status``: ``killed`` if ``WorkerKilled`` was seen,
          ``ended`` if the mission is terminal (APPROVED/FAILED/CANCELLED/
          TIMED_OUT) without an explicit kill, else ``live``.
    """
    workers: dict[str, dict[str, Any]] = {}
    spawn_order: list[str] = []
    mission_terminal_reason: str | None = None

    for env in events:
        payload = env.payload
        et = payload.event_type

        if et == "WorkerSpawned" and _is_worker_mission(payload):
            wid = payload.worker_id
            if wid in workers:
                continue
            state_dir = _derive_state_dir(
                payload.worktree, payload.session_id or ""
            )
            workers[wid] = {
                "worker_id": wid,
                "model": payload.model,
                "session_id": payload.session_id,
                "state_dir": state_dir,
                "log_path": _derive_log_path(state_dir),
                "cost_usd": 0.0,
                "tokens_used": 0,
                "reattach_status": "live",
                "spawned_ms": env.ts_ms,
                "ended_ms": None,
                "ended_reason": None,
                "pid": payload.pid,
                "worktree": payload.worktree,
            }
            spawn_order.append(wid)

        elif et == "WorkerProgress":
            w = workers.get(payload.worker_id)
            if w is None:
                continue
            if payload.cost_so_far:
                w["cost_usd"] = float(payload.cost_so_far)
            if payload.tokens_so_far:
                w["tokens_used"] = int(payload.tokens_so_far)

        elif et == "WorkerDraftReady":
            w = workers.get(payload.worker_id)
            if w is None:
                continue
            w["cost_usd"] = float(payload.cost_usd)
            w["tokens_used"] = int(payload.tokens_used)
            w["ended_ms"] = env.ts_ms
            w["reattach_status"] = "ended"
            w["ended_reason"] = "draft_ready"

        elif et == "WorkerKilled":
            w = workers.get(payload.worker_id)
            if w is None:
                continue
            w["reattach_status"] = "killed"
            w["ended_ms"] = env.ts_ms
            w["ended_reason"] = payload.reason

        elif et in ("MissionApproved", "MissionFailed", "MissionCancelled", "MissionTimedOut"):
            # Terminal state of the whole mission — mark all live workers as
            # ended (unless already killed).
            mission_terminal_reason = {
                "MissionApproved": "mission_approved",
                "MissionFailed": "mission_failed",
                "MissionCancelled": "mission_cancelled",
                "MissionTimedOut": "mission_timed_out",
            }[et]
            for w in workers.values():
                if w["reattach_status"] == "live":
                    w["reattach_status"] = "ended"
                    w["ended_ms"] = env.ts_ms
                    w["ended_reason"] = mission_terminal_reason

    return [workers[wid] for wid in spawn_order]


__all__ = ["extract_worker_missions", "ReattachStatus"]
