"""REST API for the review pipeline (Phase 8.5).

Plan §6.5 endpoints (all GET, all read-only — Plan §forbidden otherwise):
- ``GET /api/review/runs?limit=&offset=``       → RunSummary[]
- ``GET /api/review/runs/{run_id}``             → RunDetail
- ``GET /api/review/audit?since=&limit=``       → AuditEntry[]
- ``GET /api/review/stats?window_days=``        → aggregated stats dict

Stats are cached for 60 seconds because aggregating over the audit log
requires a linear scan (Plan §6.5).

Worker outputs are excerpted to 500 chars (XSS vector + performance).
The full artifact lives only as a file on disk (`data/review/runs/<id>/iter-N/worker.out`)
and is referenced by the UI via a path link, never rendered inline.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from jarvis.core.review.audit import ReviewAudit
from jarvis.core.review.io import RunDirectory

_LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/api/review", tags=["review"])

WORKER_OUTPUT_EXCERPT_CHARS = 500


# ----------------------------------------------------------------------
# Pydantic response models
# ----------------------------------------------------------------------


class RunSummary(BaseModel):
    """Aggregate of one pipeline run from the audit log."""

    run_id: str
    ts: str
    iterations: int
    final_status: str
    cap_fired: bool = False
    total_latency_ms: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0


class IterationDetail(BaseModel):
    """One iteration in the run-detail view."""

    iteration: int
    worker_output_excerpt: str = ""
    worker_output_truncated: bool = False
    verdict: dict[str, Any] | None = None
    latency_ms: int = 0


class RunDetail(BaseModel):
    """Full run view with iterations + final artifact."""

    run_id: str
    ts: str
    task: str = ""
    rubric_id: str = "default"
    final_status: str
    cap_fired: bool = False
    iterations_total: int
    iterations_detail: list[IterationDetail]
    final_artifact_path: str | None = None


class AuditEntryModel(BaseModel):
    """Raw audit entry from data/review.log."""

    ts: str | None = None
    run_id: str = ""
    iteration: int = 0
    phase: str = ""
    status: str = ""
    issue_count: int = 0
    score: float | None = None
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cap_fired: bool = False


class StatsResponse(BaseModel):
    """Aggregate statistics over pipeline runs."""

    window_days: int
    runs_total: int = 0
    pass_rate: float = 0.0
    cap_fire_rate: float = 0.0
    median_iterations: float = 0.0
    median_latency_ms: float = 0.0
    median_tokens_per_run: float = 0.0
    pass_rate_by_rubric: dict[str, float] = Field(default_factory=dict)


# ----------------------------------------------------------------------
# Dependencies
# ----------------------------------------------------------------------


def _get_audit(request: Request) -> ReviewAudit:
    audit = getattr(request.app.state, "review_audit", None)
    if audit is None:
        audit = ReviewAudit()
    return audit


def _get_runs_root(request: Request) -> Path:
    root = getattr(request.app.state, "review_runs_root", None)
    if root is None:
        return Path("data/review/runs")
    return Path(root)


# ----------------------------------------------------------------------
# Stats cache (60s TTL, Plan §6.5)
# ----------------------------------------------------------------------

_STATS_CACHE: dict[int, tuple[float, StatsResponse]] = {}
_STATS_CACHE_LOCK = threading.Lock()
_STATS_TTL_SECONDS = 60.0


def _cached_stats(window_days: int) -> StatsResponse | None:
    with _STATS_CACHE_LOCK:
        entry = _STATS_CACHE.get(window_days)
        if entry is None:
            return None
        ts, payload = entry
        if time.monotonic() - ts > _STATS_TTL_SECONDS:
            _STATS_CACHE.pop(window_days, None)
            return None
        return payload


def _store_stats(window_days: int, payload: StatsResponse) -> None:
    with _STATS_CACHE_LOCK:
        _STATS_CACHE[window_days] = (time.monotonic(), payload)


# ----------------------------------------------------------------------
# Audit reader
# ----------------------------------------------------------------------


def _read_audit_entries(audit: ReviewAudit, n: int = 5000) -> list[dict[str, Any]]:
    """Reads the last N audit entries. Default of 5000 is generously
    sized (~50 iterations per run × 100 runs).
    """
    return audit.tail(n=n)


def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        # ReviewAudit writes ISO-8601 with a "Z" suffix
        text = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _aggregate_runs(entries: list[dict[str, Any]]) -> dict[str, RunSummary]:
    """Aggregates audit entries into RunSummaries (one per run_id).

    Final-status logic:
    - If a reviewer_spawn entry with status=pass exists → "pass"
    - If only reviewer_spawn entries with needs_revision exist → "cap_fired"
    - If a reviewer_spawn with status=fail exists → "fail"
    - If precheck_fail → "precheck_fail"
    """
    by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        rid = entry.get("run_id")
        if isinstance(rid, str) and rid:
            by_run[rid].append(entry)

    summaries: dict[str, RunSummary] = {}
    for run_id, run_entries in by_run.items():
        # Sort by iteration; precheck=0 first
        sorted_entries = sorted(
            run_entries, key=lambda e: (int(e.get("iteration", 0) or 0), str(e.get("phase", "")))
        )
        ts = sorted_entries[0].get("ts", "") if sorted_entries else ""
        # Iterations = count of distinct `iteration > 0` values
        iters = {int(e.get("iteration", 0) or 0) for e in sorted_entries}
        iters.discard(0)
        iter_count = len(iters)

        reviewer_entries = [e for e in sorted_entries if e.get("phase") == "reviewer_spawn"]
        precheck_fail = any(
            e.get("status") == "precheck_fail" for e in sorted_entries
        )

        if precheck_fail:
            final_status = "precheck_fail"
            cap_fired = False
        elif any(e.get("status") == "pass" for e in reviewer_entries):
            final_status = "pass"
            cap_fired = False
        elif any(e.get("status") == "fail" for e in reviewer_entries):
            final_status = "fail"
            cap_fired = False
        elif reviewer_entries:
            final_status = "cap_fired"
            cap_fired = True
        else:
            final_status = "incomplete"
            cap_fired = False

        total_latency = sum(int(e.get("latency_ms", 0) or 0) for e in sorted_entries)
        total_tokens_in = sum(int(e.get("tokens_in", 0) or 0) for e in sorted_entries)
        total_tokens_out = sum(int(e.get("tokens_out", 0) or 0) for e in sorted_entries)

        summaries[run_id] = RunSummary(
            run_id=run_id,
            ts=str(ts or ""),
            iterations=iter_count,
            final_status=final_status,
            cap_fired=cap_fired,
            total_latency_ms=total_latency,
            total_tokens_in=total_tokens_in,
            total_tokens_out=total_tokens_out,
        )
    return summaries


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------


@router.get("/runs", response_model=list[RunSummary])
def list_runs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[RunSummary]:
    """List of pipeline runs, newest first."""
    audit = _get_audit(request)
    entries = _read_audit_entries(audit)
    summaries = list(_aggregate_runs(entries).values())
    # Newest first — sort by ts descending
    summaries.sort(key=lambda s: s.ts, reverse=True)
    return summaries[offset : offset + limit]


@router.get("/runs/{run_id}", response_model=RunDetail)
def get_run_detail(request: Request, run_id: str) -> RunDetail:
    """Full run view from filesystem + audit.

    Worker output is excerpted to 500 chars (Plan §6.5 — XSS vector and
    performance). Full artifact only via a path link.
    """
    audit = _get_audit(request)
    entries = _read_audit_entries(audit)
    summaries = _aggregate_runs(entries)
    summary = summaries.get(run_id)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"run_id {run_id!r} not found")

    runs_root = _get_runs_root(request)
    run_dir = RunDirectory(runs_root, run_id)
    task_text = ""
    rubric_id = "default"
    if run_dir.task_json_path.exists():
        try:
            task_data = json.loads(run_dir.task_json_path.read_text(encoding="utf-8"))
            task_text = str(task_data.get("task", ""))
            rubric_id = str(task_data.get("rubric_id", "default"))
        except (OSError, json.JSONDecodeError) as exc:
            _LOG.warning("task.json broken for run %s: %s", run_id, exc)

    iterations_detail: list[IterationDetail] = []
    iter_audit = [
        e for e in entries
        if e.get("run_id") == run_id and int(e.get("iteration", 0) or 0) >= 1
    ]
    iter_indexes = sorted({int(e.get("iteration", 0)) for e in iter_audit})
    for iter_idx in iter_indexes:
        worker_path = run_dir.worker_output_path(iter_idx)
        excerpt = ""
        truncated = False
        if worker_path.exists():
            try:
                full = worker_path.read_text(encoding="utf-8")
                excerpt = full[:WORKER_OUTPUT_EXCERPT_CHARS]
                truncated = len(full) > WORKER_OUTPUT_EXCERPT_CHARS
            except OSError:
                pass

        verdict_path = run_dir.verdict_path(iter_idx)
        verdict_data: dict[str, Any] | None = None
        if verdict_path.exists():
            try:
                verdict_data = json.loads(verdict_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                verdict_data = None

        reviewer_entry = next(
            (
                e for e in iter_audit
                if int(e.get("iteration", 0)) == iter_idx
                and e.get("phase") == "reviewer_spawn"
            ),
            None,
        )
        latency_ms = int(reviewer_entry.get("latency_ms", 0)) if reviewer_entry else 0

        iterations_detail.append(
            IterationDetail(
                iteration=iter_idx,
                worker_output_excerpt=excerpt,
                worker_output_truncated=truncated,
                verdict=verdict_data,
                latency_ms=latency_ms,
            )
        )

    final_artifact_path: str | None = None
    if run_dir.final_json_path.exists():
        final_artifact_path = str(run_dir.final_json_path)

    return RunDetail(
        run_id=run_id,
        ts=summary.ts,
        task=task_text,
        rubric_id=rubric_id,
        final_status=summary.final_status,
        cap_fired=summary.cap_fired,
        iterations_total=summary.iterations,
        iterations_detail=iterations_detail,
        final_artifact_path=final_artifact_path,
    )


@router.get("/audit", response_model=list[AuditEntryModel])
def get_audit_entries(
    request: Request,
    since: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
) -> list[AuditEntryModel]:
    """Raw audit entries, newest first, optionally starting at `since` timestamp."""
    audit = _get_audit(request)
    entries = _read_audit_entries(audit, n=limit * 5)  # sample generously

    since_dt = _parse_ts(since) if since else None
    filtered: list[dict[str, Any]] = []
    for entry in entries:
        if since_dt is not None:
            ts = _parse_ts(entry.get("ts"))
            if ts is None or ts < since_dt:
                continue
        filtered.append(entry)

    # Newest first — entries come chronologically from tail(); reverse
    filtered.reverse()
    return [AuditEntryModel(**e) for e in filtered[:limit]]


@router.get("/stats", response_model=StatsResponse)
def get_stats(
    request: Request,
    window_days: int = Query(default=7, ge=1, le=365),
) -> StatsResponse:
    """Aggregated stats over window_days. Result is cached for 60s."""
    cached = _cached_stats(window_days)
    if cached is not None:
        return cached

    audit = _get_audit(request)
    entries = _read_audit_entries(audit, n=10000)
    cutoff = datetime.now(UTC) - timedelta(days=window_days)

    in_window: list[dict[str, Any]] = []
    for entry in entries:
        ts = _parse_ts(entry.get("ts"))
        if ts is None:
            continue
        if ts >= cutoff:
            in_window.append(entry)

    summaries = _aggregate_runs(in_window)
    total_runs = len(summaries)
    if total_runs == 0:
        result = StatsResponse(window_days=window_days)
        _store_stats(window_days, result)
        return result

    pass_count = sum(1 for s in summaries.values() if s.final_status == "pass")
    cap_count = sum(1 for s in summaries.values() if s.cap_fired)

    iters_sorted = sorted(s.iterations for s in summaries.values())
    latency_sorted = sorted(s.total_latency_ms for s in summaries.values())
    tokens_sorted = sorted(
        s.total_tokens_in + s.total_tokens_out for s in summaries.values()
    )

    def _median(values: list[int]) -> float:
        if not values:
            return 0.0
        n = len(values)
        if n % 2 == 1:
            return float(values[n // 2])
        return (values[n // 2 - 1] + values[n // 2]) / 2.0

    # Pass rate by rubric: needs task.json per run; best-effort only.
    by_rubric_total: dict[str, int] = defaultdict(int)
    by_rubric_pass: dict[str, int] = defaultdict(int)
    runs_root = _get_runs_root(request)
    for run_id, summary in summaries.items():
        rubric = "default"
        task_json = runs_root / run_id / "task.json"
        if task_json.exists():
            try:
                rubric = str(
                    json.loads(task_json.read_text(encoding="utf-8")).get(
                        "rubric_id", "default"
                    )
                )
            except (OSError, json.JSONDecodeError):
                pass
        by_rubric_total[rubric] += 1
        if summary.final_status == "pass":
            by_rubric_pass[rubric] += 1

    pass_by_rubric = {
        r: (by_rubric_pass[r] / by_rubric_total[r]) if by_rubric_total[r] else 0.0
        for r in by_rubric_total
    }

    result = StatsResponse(
        window_days=window_days,
        runs_total=total_runs,
        pass_rate=pass_count / total_runs,
        cap_fire_rate=cap_count / total_runs,
        median_iterations=_median(iters_sorted),
        median_latency_ms=_median(latency_sorted),
        median_tokens_per_run=_median(tokens_sorted),
        pass_rate_by_rubric=pass_by_rubric,
    )
    _store_stats(window_days, result)
    return result
