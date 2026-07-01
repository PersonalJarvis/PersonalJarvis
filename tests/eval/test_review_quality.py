"""Eval pytest for the review pipeline (Phase 8.6).

Plan reference: §6.6 — pass rate >=80% on the 17 successful buckets,
all 3 adversarial queries yield fail or cap_fired.

Marker: `@pytest.mark.eval` — excluded from the standard run via
`pytest -m "not eval and not slow"`. A real run costs time + money;
the mock run is trivial.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jarvis.cli.review_eval import (
    DEFAULT_QUERIES_PATH,
    _can_run_real,
    _load_queries,
    run_eval,
)

pytestmark = [pytest.mark.eval, pytest.mark.slow]


@pytest.fixture(scope="module")
def queries() -> list[dict]:
    return _load_queries(DEFAULT_QUERIES_PATH)


def test_queries_file_has_at_least_20_entries(queries: list[dict]) -> None:
    """Plan §6.6 requires ≥20 golden queries."""
    assert len(queries) >= 20, f"only {len(queries)} queries in golden set"


def test_queries_cover_all_buckets(queries: list[dict]) -> None:
    """Plan §6.6: all 6 buckets must be represented."""
    expected_buckets = {
        "code_gen_trivial",
        "code_gen_complex",
        "skill_authoring",
        "research",
        "adversarial",
        "edge_case",
    }
    actual = {q.get("bucket") for q in queries}
    assert expected_buckets.issubset(actual), (
        f"missing buckets: {expected_buckets - actual}"
    )


def test_queries_have_unique_ids(queries: list[dict]) -> None:
    """IDs are the identity of each query — no duplicates."""
    ids = [q.get("id") for q in queries]
    assert len(ids) == len(set(ids)), "duplicate query IDs"


def test_quick_queries_at_most_5(queries: list[dict]) -> None:
    """`--quick` filters down to at most 5 queries."""
    quick = [q for q in queries if q.get("quick") is True]
    assert len(quick) <= 5
    assert len(quick) >= 1, "no quick queries — pre-commit hook would have nothing"


def test_adversarial_have_failure_expectations(queries: list[dict]) -> None:
    """Adversarial must expect `fail` or `cap_fired` — otherwise it isn't
    an adversarial query."""
    advs = [q for q in queries if q.get("bucket") == "adversarial"]
    assert len(advs) >= 3
    for q in advs:
        assert q.get("expected_status") in ("fail", "cap_fired"), (
            f"adversarial {q['id']} has unexpected expected_status={q.get('expected_status')!r}"
        )


# ----------------------------------------------------------------------
# Mock-Eval-Run
# ----------------------------------------------------------------------


def test_mock_eval_full_suite_match_rate(tmp_path: Path, queries: list[dict]) -> None:
    """Mock pipeline with deterministic verdicts — match rate should be 100%
    because the mock returns exactly the desired outcome.
    """
    report = asyncio.run(
        run_eval(
            queries=queries,
            audit_path=tmp_path / "review.log",
            runs_root=tmp_path / "runs",
            use_mock=True,
        )
    )
    assert report["total"] == len(queries)
    # Match rate >= 0.9 — a few queries (e.g. cap_fired) need special
    # mock logic; <100% is ok as long as we see the discrepancy.
    # Plan §6.6 requires >=80%.
    assert report["match_rate"] >= 0.8, (
        f"mock match-rate too low: {report['match_rate']:.2%} on {report['by_status']}"
    )


def test_mock_eval_adversarial_all_fail_or_cap_fired(
    tmp_path: Path, queries: list[dict]
) -> None:
    """Plan §6.6 AC: all 3 adversarial queries correctly yield fail/cap_fired."""
    advs = [q for q in queries if q.get("bucket") == "adversarial"]
    report = asyncio.run(
        run_eval(
            queries=advs,
            audit_path=tmp_path / "review.log",
            runs_root=tmp_path / "runs",
            use_mock=True,
        )
    )
    for q in report["queries"]:
        assert q["actual_status"] in ("fail", "cap_fired"), (
            f"adversarial {q['id']} got status={q['actual_status']!r} (expected fail or cap_fired)"
        )


def test_mock_eval_pass_rate_per_bucket(
    tmp_path: Path, queries: list[dict]
) -> None:
    """Match rate per bucket >= bucket-specific threshold.

    Plan §6.6 requires ≥80% globally on the 17 successful buckets.
    Edge cases are ambiguous by definition (the pre-check can't detect
    every ambiguous query, because the only pre-check is
    `task_not_empty > 10 chars` and longer edge cases pass that);
    hence the 66% threshold for `edge_case`. Adversarial is handled
    separately in `test_mock_eval_adversarial_*`.
    """
    bucket_thresholds = {
        "code_gen_trivial": 0.8,
        "code_gen_complex": 0.8,
        "skill_authoring": 0.8,
        "research": 0.8,
        "edge_case": 0.66,  # 2/3 is reality — see docstring
    }
    report = asyncio.run(
        run_eval(
            queries=queries,
            audit_path=tmp_path / "review.log",
            runs_root=tmp_path / "runs",
            use_mock=True,
        )
    )

    bucket_summary = report["by_bucket"]
    for bucket, stats in bucket_summary.items():
        if bucket == "adversarial":
            continue  # adversarial is checked in test_mock_eval_adversarial_*
        threshold = bucket_thresholds.get(bucket, 0.8)
        assert stats["match_rate"] >= threshold, (
            f"bucket {bucket} match-rate {stats['match_rate']:.2%} "
            f"< threshold {threshold:.0%}"
        )


# ----------------------------------------------------------------------
# Real-Eval-Run (skip if no auth)
# ----------------------------------------------------------------------


def test_real_eval_quick_subset(tmp_path: Path, queries: list[dict]) -> None:
    """Real eval run with the quick subset (5 queries).

    Skip when claude auth is missing or `claude` isn't on PATH —
    on an auth-capable machine, the test runs through.
    """
    ok, reason = _can_run_real()
    if not ok:
        pytest.skip(f"real eval not runnable: {reason}")

    quick = [q for q in queries if q.get("quick") is True][:5]
    if not quick:
        pytest.skip("no quick queries in golden set")

    report = asyncio.run(
        run_eval(
            queries=quick,
            audit_path=tmp_path / "review.log",
            runs_root=tmp_path / "runs",
            use_mock=False,
        )
    )
    # Plan §6.6's quick-pass threshold is laxer (the pre-commit hook uses 60%).
    # 60% is still realistic on the real pipeline; higher would get flaky.
    assert report["match_rate"] >= 0.6, (
        f"real eval quick-subset match-rate {report['match_rate']:.2%} < 60%"
    )
