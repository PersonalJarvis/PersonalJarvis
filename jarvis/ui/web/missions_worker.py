"""OpenClaw-Worker-Aggregator fuer das Mission-Detail-API.

Phase 9 (Welle 4 UI): Wenn der OpenClaw-Harness in den Mission-Manager-Worker-
Layer verdrahtet wird, emittiert er ``WorkerSpawned``-Events mit einer
session_id und ``step["harness"] == "openclaw"``. Dieser Aggregator zieht aus
dem Event-Stream einer Mission alle OpenClaw-spezifischen UI-Felder ab —
``state_dir``, ``log_path``, ``reattach_status``, ``cost_usd``, ``tokens``.

Pure helpers, keine IO. Tests hierzu liegen in
``tests/missions/api/test_missions_worker_aggregator.py``.

Vertrag-Kompatibilitaet (siehe ``docs/openclaw-bridge.md``):
- ``MISSION_STATE_DIR`` Konvention aus ``OpenClawHarness._build_spec``:
  ``<worktree>/.openclaw_state/<session_id>/openclaw_state``.
- ``log_path`` Konvention: OpenClaw schreibt ``run.log`` ins state_dir
  (siehe Welle-3 spike-results §SP-4).
- Reattach-Status: ``live`` solange weder ``WorkerKilled`` noch ein
  Mission-Terminal-Event nachfolgt; sonst ``ended``/``killed``.
"""
from __future__ import annotations

import os.path
from typing import Any, Iterable, Literal

from jarvis.missions.events import EventEnvelope


ReattachStatus = Literal["live", "ended", "killed", "unknown"]


def _is_worker_mission(spawn_payload: Any) -> bool:
    """Detection-Heuristik fuer OpenClaw-Worker.

    Primaer: ``step["harness"] == "openclaw"`` — das ist der kanonische Marker
    sobald der Worker-Layer den OpenClaw-Harness ruft.

    Fallback: ``session_id is not None`` UND ``model`` enthaelt einen
    ``provider/model``-Slash (Provider-Prefix-Konvention von OpenClaw, siehe
    ``OpenClawHarness.build_spawn_args``). Damit greift die UI auch wenn der
    Worker-Layer den ``step``-Marker noch nicht setzt.
    """
    step = getattr(spawn_payload, "step", None) or {}
    if isinstance(step, dict) and step.get("harness") == "openclaw":
        return True

    session_id = getattr(spawn_payload, "session_id", None)
    model = getattr(spawn_payload, "model", "") or ""
    return bool(session_id) and "/" in model


def _derive_state_dir(worktree: str, session_id: str) -> str:
    """Reproduziert ``OpenClawHarness._build_spec``-Konvention.

    Gibt einen *forward-slash*-pfad zurueck (UI-friendly auch auf Windows).
    """
    if not worktree or not session_id:
        return ""
    return os.path.join(worktree, ".openclaw_state", session_id, "openclaw_state").replace(
        "\\", "/"
    )


def _derive_log_path(state_dir: str) -> str:
    """OpenClaw schreibt ``run.log`` ins state_dir (Welle-3 SP-4 Befund)."""
    if not state_dir:
        return ""
    return f"{state_dir}/run.log"


def extract_worker_missions(
    events: Iterable[EventEnvelope],
) -> list[dict[str, Any]]:
    """Aggregiert OpenClaw-Worker-Snapshots aus einem Event-Stream.

    Idempotent + ohne IO. Die Reihenfolge der Eintraege entspricht der
    chronologischen Spawn-Reihenfolge.

    Felder pro Worker:
        worker_id, model, session_id, state_dir, log_path,
        cost_usd, tokens_used, reattach_status, spawned_ms, ended_ms,
        ended_reason

    Aggregations-Regeln:
        - ``cost_usd`` = letzter ``WorkerProgress.cost_so_far`` ODER
          ``WorkerDraftReady.cost_usd`` (was zuletzt kam, gewinnt).
        - ``tokens_used`` analog ueber ``tokens_so_far`` / ``tokens_used``.
        - ``reattach_status``: ``killed`` wenn ``WorkerKilled`` gesehen,
          ``ended`` wenn Mission terminal (APPROVED/FAILED/CANCELLED/
          TIMED_OUT) ohne explizites Kill, sonst ``live``.
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
            # Terminal-State der ganzen Mission — alle live-Worker als ended
            # markieren (sofern nicht bereits killed).
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
