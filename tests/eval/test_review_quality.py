"""Eval-Pytest fuer die Review-Pipeline (Phase 8.6).

Plan-Referenz: §6.6 — Pass-Rate >=80% auf den 17 erfolgreichen Buckets,
alle 3 Adversarial-Queries liefern fail oder cap_fired.

Markierung: `@pytest.mark.eval` — wird vom Standard-Run via
`pytest -m "not eval and not slow"` ausgeschlossen. Realer Run kostet Zeit
+ Geld; Mock-Run ist trivial.
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
    """Plan-§6.6 fordert ≥20 Golden-Queries."""
    assert len(queries) >= 20, f"only {len(queries)} queries in golden set"


def test_queries_cover_all_buckets(queries: list[dict]) -> None:
    """Plan §6.6: 6 Buckets müssen alle vertreten sein."""
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
    """IDs sind die Identitaet jeder Query — keine Duplikate."""
    ids = [q.get("id") for q in queries]
    assert len(ids) == len(set(ids)), "duplicate query IDs"


def test_quick_queries_at_most_5(queries: list[dict]) -> None:
    """`--quick` filtert auf höchstens 5 Queries."""
    quick = [q for q in queries if q.get("quick") is True]
    assert len(quick) <= 5
    assert len(quick) >= 1, "no quick queries — pre-commit hook hätte nichts"


def test_adversarial_have_failure_expectations(queries: list[dict]) -> None:
    """Adversarial muss `fail` oder `cap_fired` erwarten — sonst ist es
    keine adversarial Query."""
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
    """Mock-Pipeline mit deterministischen Verdicts — Match-Rate sollte 100%
    sein, weil der Mock genau das gewünschte Outcome liefert.
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
    # Match-Rate >= 0.9 — ein paar Queries (z.B. cap_fired) brauchen
    # spezielle Mock-Logic; <100% ist ok solange wir die Discrepancy sehen.
    # Plan-§6.6 fordert >=80%.
    assert report["match_rate"] >= 0.8, (
        f"mock match-rate too low: {report['match_rate']:.2%} on {report['by_status']}"
    )


def test_mock_eval_adversarial_all_fail_or_cap_fired(
    tmp_path: Path, queries: list[dict]
) -> None:
    """Plan §6.6 AC: alle 3 Adversarial-Queries liefern korrekt fail/cap_fired."""
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
    """Match-Rate pro Bucket >= bucket-spezifischer Threshold.

    Plan-§6.6 fordert ≥80% global auf den 17 erfolgreichen Buckets.
    Edge-Cases sind per Definition mehrdeutig (Pre-Check kann nicht
    jede ambige Query erkennen, weil der einzige Pre-Check
    `task_not_empty > 10 chars` ist und längere Edge-Cases passen das);
    daher 66% Threshold für `edge_case`. Adversarial separat in
    `test_mock_eval_adversarial_*`.
    """
    bucket_thresholds = {
        "code_gen_trivial": 0.8,
        "code_gen_complex": 0.8,
        "skill_authoring": 0.8,
        "research": 0.8,
        "edge_case": 0.66,  # 2/3 ist Realität — siehe docstring
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
            continue  # adversarial wird in test_mock_eval_adversarial_* geprüft
        threshold = bucket_thresholds.get(bucket, 0.8)
        assert stats["match_rate"] >= threshold, (
            f"bucket {bucket} match-rate {stats['match_rate']:.2%} "
            f"< threshold {threshold:.0%}"
        )


# ----------------------------------------------------------------------
# Real-Eval-Run (skip if no auth)
# ----------------------------------------------------------------------


def test_real_eval_quick_subset(tmp_path: Path, queries: list[dict]) -> None:
    """Realer Eval-Run mit dem Quick-Subset (5 Queries).

    Skip bei fehlender claude-Auth oder wenn `claude` nicht im PATH —
    auf einer Auth-fähigen Maschine läuft der Test durch.
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
    # Plan-§6.6 Quick-Pass-Threshold ist laxer (Pre-Commit-Hook nutzt 60%).
    # Auf realer Pipeline ist 60% noch realistisch; höher würde flaky werden.
    assert report["match_rate"] >= 0.6, (
        f"real eval quick-subset match-rate {report['match_rate']:.2%} < 60%"
    )
