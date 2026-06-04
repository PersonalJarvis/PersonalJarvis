"""Review pipeline eval harness (Phase 8.6).

Plan reference: §6.6 — reproducible pass-rate measurement over a
constant golden-query suite.

Modes:
- ``--mock``: deterministic mock pipeline (no claude spawn, no cost).
  Default TRUE when `claude` is not in PATH OR neither
  ANTHROPIC_API_KEY nor CLAUDE_CODE_OAUTH_TOKEN is set.
- ``--real``: real pipeline spawns via WorkerSpawner/ReviewerSpawner.

Invocations:
    jarvis-review-eval --quick
    jarvis-review-eval --queries tests/eval/review_quality/queries.json
    jarvis-review-eval --bucket code_gen_trivial --report out.json
    jarvis-review-eval --real --bucket research

Report schema: see plan §6.6.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis.core.review.audit import ReviewAudit
from jarvis.core.review.checks import (
    PostCheckRunner,
    PreCheckRunner,
    output_not_empty,
    task_not_empty,
)
from jarvis.core.review.pipeline import ReviewPipeline
from jarvis.core.review.state import RunState
from jarvis.core.review.verdict import (
    ReviewIssue,
    ReviewStatus,
    ReviewVerdict,
)

_LOG = logging.getLogger(__name__)

DEFAULT_QUERIES_PATH = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "eval"
    / "review_quality"
    / "queries.json"
)


# ----------------------------------------------------------------------
# Mock-Pipeline (deterministisch, queries-aware)
# ----------------------------------------------------------------------


def _mock_outcome_for_query(query: dict[str, Any]) -> ReviewStatus | str:
    """Deterministically returns the expected outcome for a query.

    Adversarial → fail. Edge with precheck_fail → filtered by pre-check
    (NOT returned by the mock reviewer). Otherwise → pass.
    A mock run therefore always achieves a 100% pass rate on buckets
    that expect pass — this is exactly the mock contract.
    """
    expected = query.get("expected_status", "pass")
    if expected == "fail":
        return ReviewStatus.FAIL
    if expected == "cap_fired":
        return ReviewStatus.NEEDS_REVISION  # provoziert cap_fire
    if expected == "precheck_fail":
        return "precheck_fail"
    return ReviewStatus.PASS


async def _mock_worker_spawn(state: RunState, iteration: int) -> str:
    return f"[mock-worker iter={iteration}] produced artifact for {state.run_id[:8]}"


def _mock_reviewer_for_query(
    query: dict[str, Any],
):
    target = _mock_outcome_for_query(query)

    async def reviewer(
        state: RunState, worker_output: str, iteration: int
    ) -> ReviewVerdict:
        if target is ReviewStatus.PASS:
            return ReviewVerdict(
                status=ReviewStatus.PASS,
                summary="mock: looks good",
                score=0.95,
            )
        if target is ReviewStatus.FAIL:
            return ReviewVerdict(
                status=ReviewStatus.FAIL,
                summary="mock: architectural defect",
                issues=[
                    ReviewIssue(
                        severity="critical",
                        description="task is logically impossible",
                    )
                ],
                score=0.0,
            )
        # NEEDS_REVISION → cap_fire path
        return ReviewVerdict(
            status=ReviewStatus.NEEDS_REVISION,
            summary=f"mock: still needs work (iter {iteration})",
            issues=[
                ReviewIssue(
                    severity="warning", description="missing detail X"
                )
            ],
            score=0.5,
        )

    return reviewer


# ----------------------------------------------------------------------
# Real-Mode Spawn-Detection
# ----------------------------------------------------------------------


def _can_run_real() -> tuple[bool, str]:
    if shutil.which("claude") is None:
        return False, "claude CLI not in PATH"
    if not (
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    ):
        return False, "no Anthropic auth (ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN)"
    return True, "ready"


def _build_real_pipeline(
    runs_root: Path, audit: ReviewAudit, max_iterations: int
) -> ReviewPipeline:
    from jarvis.core.review.spawns import ReviewerSpawner, WorkerSpawner
    from jarvis.harness.manager import HarnessManager

    manager = HarnessManager()
    worker = WorkerSpawner(harness_manager=manager, runs_root=runs_root, timeout_s=120)
    reviewer = ReviewerSpawner(
        harness_manager=manager, runs_root=runs_root, timeout_s=60
    )
    return ReviewPipeline(
        worker_spawn=worker.spawn,
        reviewer_spawn=reviewer.spawn,
        prechecks=PreCheckRunner([task_not_empty]),
        postchecks=PostCheckRunner([output_not_empty]),
        audit=audit,
        max_iterations=max_iterations,
    )


def _build_mock_pipeline(
    audit: ReviewAudit, query: dict[str, Any], max_iterations: int
) -> ReviewPipeline:
    return ReviewPipeline(
        worker_spawn=_mock_worker_spawn,
        reviewer_spawn=_mock_reviewer_for_query(query),
        prechecks=PreCheckRunner([task_not_empty]),
        postchecks=PostCheckRunner([output_not_empty]),
        audit=audit,
        max_iterations=max_iterations,
    )


# ----------------------------------------------------------------------
# Eval Run
# ----------------------------------------------------------------------


async def _run_one_query(
    query: dict[str, Any],
    *,
    audit: ReviewAudit,
    runs_root: Path,
    use_mock: bool,
    max_iterations: int,
) -> dict[str, Any]:
    """Runs a pipeline for one query and returns the result dict."""
    t0 = time.monotonic()
    if use_mock:
        pipeline = _build_mock_pipeline(audit, query, max_iterations)
    else:
        pipeline = _build_real_pipeline(runs_root, audit, max_iterations)

    actual_status: str
    iterations: int
    try:
        result = await pipeline.run(
            query["task"],
            rubric_id=query.get("rubric_id", "default"),
            max_iterations=query.get("expected_max_iterations") or max_iterations,
        )
        # Plan §6.6 report schema uses "pass"/"fail"/"cap_fired" —
        # PipelineOutcome.SUCCESS.value is "success", hence the mapping.
        outcome_value = result.outcome.value
        if outcome_value == "success":
            actual_status = "pass"
        else:
            actual_status = outcome_value
        iterations = len(result.iterations)
    except Exception as exc:  # noqa: BLE001 — eval must never crash
        actual_status = "error"
        iterations = 0
        _LOG.warning("query %s crashed: %s", query.get("id"), exc)

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    expected_status = query.get("expected_status", "pass")
    # `cap_fired` counts as a pass-match when expected=cap_fired; likewise
    # `fail` as a match for adversarial queries.
    matched = actual_status == expected_status

    return {
        "id": query.get("id", "?"),
        "bucket": query.get("bucket", "unknown"),
        "task": query.get("task", "")[:200],
        "rubric_id": query.get("rubric_id", "default"),
        "expected_status": expected_status,
        "actual_status": actual_status,
        "match": matched,
        "iterations": iterations,
        "latency_ms": elapsed_ms,
        "tokens": 0,  # Phase-9 cost monitoring; not measured yet
        "mock": use_mock,
    }


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = defaultdict(int)
    by_bucket: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "matched": 0}
    )
    matched_total = 0
    for r in results:
        by_status[r["actual_status"]] += 1
        b = by_bucket[r["bucket"]]
        b["n"] += 1
        if r["match"]:
            b["matched"] += 1
            matched_total += 1

    bucket_summary: dict[str, dict[str, float]] = {}
    for bucket, stats in by_bucket.items():
        n = stats["n"]
        bucket_summary[bucket] = {
            "n": n,
            "match_rate": (stats["matched"] / n) if n else 0.0,
        }

    total = len(results)
    return {
        "total": total,
        "matched_total": matched_total,
        "match_rate": (matched_total / total) if total else 0.0,
        "by_status": dict(by_status),
        "by_bucket": bucket_summary,
    }


async def run_eval(
    *,
    queries: list[dict[str, Any]],
    audit_path: Path,
    runs_root: Path,
    use_mock: bool,
    max_iterations: int = 3,
) -> dict[str, Any]:
    audit = ReviewAudit(path=audit_path)
    results: list[dict[str, Any]] = []
    for q in queries:
        rec = await _run_one_query(
            q,
            audit=audit,
            runs_root=runs_root,
            use_mock=use_mock,
            max_iterations=max_iterations,
        )
        results.append(rec)
    summary = _aggregate(results)
    return {
        "ts": datetime.now(UTC).isoformat(),
        "mock": use_mock,
        **summary,
        "queries": results,
    }


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _load_queries(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return list(data.get("queries", []))
    if isinstance(data, list):
        return list(data)
    raise ValueError(f"queries.json hat unerwartetes Format: {type(data).__name__}")


def _filter_queries(
    queries: list[dict[str, Any]],
    *,
    quick: bool,
    bucket: str | None,
) -> list[dict[str, Any]]:
    out = list(queries)
    if quick:
        out = [q for q in out if q.get("quick") is True][:5]
    if bucket:
        out = [q for q in out if q.get("bucket") == bucket]
    return out


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis-review-eval",
        description=(
            "Führt die Review-Pipeline gegen den Golden-Query-Set aus "
            "und schreibt einen Pass-Rate-Report."
        ),
    )
    parser.add_argument(
        "--queries",
        type=Path,
        default=DEFAULT_QUERIES_PATH,
        help=f"Queries-JSON. Default: {DEFAULT_QUERIES_PATH}",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Pfad für JSON-Report. Default: stdout.",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Nur Queries mit `quick=true`-Flag (max 5).",
    )
    parser.add_argument(
        "--bucket",
        default=None,
        help="Filter auf einen Bucket (z.B. code_gen_trivial).",
    )
    parser.add_argument(
        "--parallelism",
        type=int,
        default=1,
        help="Aktuell auf 1 fixiert (Reproduzierbarkeit). Phase-9-Backlog.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--mock",
        action="store_true",
        default=None,
        help="Deterministische Mock-Pipeline (kein Spawn, keine Kosten). "
             "Default: True wenn keine claude-Auth verfügbar.",
    )
    mode.add_argument(
        "--real",
        action="store_true",
        default=False,
        help="Echte Pipeline-Spawns gegen claude-CLI. Setzt Auth voraus.",
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=Path("data/review/runs"),
    )
    parser.add_argument(
        "--audit-log",
        type=Path,
        default=Path("data/review.log"),
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.0,
        help="Wenn match_rate < threshold: exit 1.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.real:
        ok, reason = _can_run_real()
        if not ok:
            print(
                f"--real not possible: {reason}",
                file=sys.stderr,
            )
            return 2
        use_mock = False
    elif args.mock:
        use_mock = True
    else:
        ok, _ = _can_run_real()
        use_mock = not ok

    try:
        queries = _load_queries(args.queries)
    except (FileNotFoundError, ValueError) as exc:
        print(f"queries-load failed: {exc}", file=sys.stderr)
        return 2

    queries = _filter_queries(queries, quick=args.quick, bucket=args.bucket)
    if not queries:
        print("no queries match filter", file=sys.stderr)
        return 2

    report = asyncio.run(
        run_eval(
            queries=queries,
            audit_path=args.audit_log,
            runs_root=args.runs_root,
            use_mock=use_mock,
        )
    )

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
        # Windows cp1252 lacks U+2192 (->); use plain ASCII for stdout.
        print(
            f"wrote report -> {args.report} "
            f"(match_rate={report['match_rate']:.2%}, "
            f"n={report['total']}, mock={use_mock})"
        )
    else:
        print(text)

    if args.threshold > 0 and report["match_rate"] < args.threshold:
        print(
            f"FAIL: match_rate {report['match_rate']:.2%} < "
            f"threshold {args.threshold:.2%}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
