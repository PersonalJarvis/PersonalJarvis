"""Tests fuer Review-UI-API-Routes (Phase 8.5).

Plan-Referenz: §6.5 Akzeptanzkriterium 1 — alle 4 GET-Endpunkte gegen
gemocktes Audit-Log und gemockte Run-Directories.

Skip-Bedingung: lokale fastapi==0.119.1 + starlette==1.0.0 sind nicht
kompatibel (Starlette hat `on_startup` entfernt; FastAPI reicht es noch
durch). Pre-existing — gleicher Bug in tests/missions/api/*,
tests/board/*, tests/unit/test_voice_bridge_routes.py. Auf einem
kompatiblen Stack laufen die Tests durch.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# Module-level skip wenn der pre-existing FastAPI/Starlette-Versions-Drift
# das ganze APIRouter-Import-Pattern blockiert.
try:
    from fastapi import APIRouter as _APIRouter

    _APIRouter(prefix="/probe", tags=["probe"])
    _ROUTER_OK = True
except Exception:  # noqa: BLE001
    _ROUTER_OK = False

pytestmark = pytest.mark.skipif(
    not _ROUTER_OK,
    reason="pre-existing fastapi/starlette version drift "
    "(Starlette removed `on_startup`); same bug as tests/board/*, "
    "tests/missions/api/*, tests/unit/test_voice_bridge_routes.py",
)

if _ROUTER_OK:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from jarvis.core.review.audit import (
        AuditPhase,
        AuditRecord,
        AuditStatus,
        ReviewAudit,
    )
    from jarvis.core.review.io import RunDirectory
    from jarvis.ui.web.review_routes import router as review_router


def _build_app(audit: ReviewAudit, runs_root: Path) -> FastAPI:
    app = FastAPI()
    app.state.review_audit = audit
    app.state.review_runs_root = runs_root
    app.include_router(review_router)
    return app


def _seed_audit(audit: ReviewAudit, run_id: str, *, statuses: list[str]) -> None:
    """Schreibt für `run_id` worker_spawn + reviewer_spawn pro Iter."""
    for iteration, status_value in enumerate(statuses, start=1):
        audit.append_iteration(
            AuditRecord(
                run_id=run_id,
                iteration=iteration,
                phase=AuditPhase.WORKER_SPAWN,
                status=AuditStatus.PASS,
                latency_ms=200,
            )
        )
        audit.append_iteration(
            AuditRecord(
                run_id=run_id,
                iteration=iteration,
                phase=AuditPhase.REVIEWER_SPAWN,
                status=AuditStatus(status_value),
                issue_count=1 if status_value == "needs_revision" else 0,
                score=0.95 if status_value == "pass" else 0.5,
                latency_ms=400,
                tokens_in=1500,
                tokens_out=300,
            )
        )


@pytest.fixture
def client_factory(tmp_path: Path):
    """Factory die einen TestClient + audit + runs_root liefert."""

    def make() -> tuple[TestClient, ReviewAudit, Path]:
        audit = ReviewAudit(path=tmp_path / "review.log")
        runs_root = tmp_path / "runs"
        runs_root.mkdir(parents=True, exist_ok=True)
        app = _build_app(audit, runs_root)
        return TestClient(app), audit, runs_root

    return make


# ----------------------------------------------------------------------
# /api/review/runs
# ----------------------------------------------------------------------


def test_runs_empty_when_no_audit(client_factory) -> None:
    client, _, _ = client_factory()
    res = client.get("/api/review/runs")
    assert res.status_code == 200
    assert res.json() == []


def test_runs_aggregates_audit_to_summary(client_factory) -> None:
    client, audit, _ = client_factory()
    _seed_audit(audit, "abc-1", statuses=["pass"])
    _seed_audit(audit, "abc-2", statuses=["needs_revision", "needs_revision", "pass"])

    res = client.get("/api/review/runs")
    assert res.status_code == 200
    data = res.json()
    assert {r["run_id"] for r in data} == {"abc-1", "abc-2"}

    by_id = {r["run_id"]: r for r in data}
    assert by_id["abc-1"]["iterations"] == 1
    assert by_id["abc-1"]["final_status"] == "pass"
    assert by_id["abc-1"]["cap_fired"] is False

    assert by_id["abc-2"]["iterations"] == 3
    assert by_id["abc-2"]["final_status"] == "pass"
    # Latency-Summen kommen aus den Audit-Einträgen
    assert by_id["abc-2"]["total_latency_ms"] > 0


def test_runs_pagination_limit(client_factory) -> None:
    client, audit, _ = client_factory()
    for i in range(10):
        _seed_audit(audit, f"run-{i:02d}", statuses=["pass"])

    res = client.get("/api/review/runs?limit=3")
    assert res.status_code == 200
    assert len(res.json()) == 3


def test_runs_cap_fired_classified(client_factory) -> None:
    client, audit, _ = client_factory()
    # Drei needs_revision-Verdicts → cap_fired
    _seed_audit(audit, "cap-run", statuses=["needs_revision"] * 3)
    res = client.get("/api/review/runs")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["final_status"] == "cap_fired"
    assert data[0]["cap_fired"] is True


# ----------------------------------------------------------------------
# /api/review/runs/{run_id}
# ----------------------------------------------------------------------


def test_run_detail_404_for_unknown_id(client_factory) -> None:
    client, _, _ = client_factory()
    res = client.get("/api/review/runs/does-not-exist")
    assert res.status_code == 404


def test_run_detail_includes_iteration_data(client_factory) -> None:
    client, audit, runs_root = client_factory()
    _seed_audit(audit, "detail-run", statuses=["pass"])

    # Run-Dir mit task.json + iter-1/worker.out + iter-1/verdict.json
    run_dir = RunDirectory(runs_root, "detail-run").ensure()
    run_dir.write_task(task="schreibe ein test-script", rubric_id="code_generation")
    run_dir.write_worker_output(1, "produced artifact body" * 5)
    run_dir.write_verdict(
        1,
        {
            "status": "pass",
            "summary": "ok",
            "issues": [],
            "rubric_results": [],
            "score": 0.95,
        },
    )

    res = client.get("/api/review/runs/detail-run")
    assert res.status_code == 200
    data = res.json()
    assert data["run_id"] == "detail-run"
    assert data["task"] == "schreibe ein test-script"
    assert data["rubric_id"] == "code_generation"
    assert data["final_status"] == "pass"
    assert len(data["iterations_detail"]) == 1
    iter1 = data["iterations_detail"][0]
    assert iter1["iteration"] == 1
    assert iter1["worker_output_excerpt"]
    assert iter1["verdict"]["status"] == "pass"


def test_run_detail_excerpt_truncated_to_500_chars(client_factory) -> None:
    client, audit, runs_root = client_factory()
    _seed_audit(audit, "long-run", statuses=["pass"])

    run_dir = RunDirectory(runs_root, "long-run").ensure()
    run_dir.write_task(task="x" * 50, rubric_id="default")
    long_output = "a" * 1500
    run_dir.write_worker_output(1, long_output)
    run_dir.write_verdict(
        1,
        {"status": "pass", "summary": "ok", "issues": [], "rubric_results": [], "score": 1.0},
    )

    res = client.get("/api/review/runs/long-run")
    assert res.status_code == 200
    iter1 = res.json()["iterations_detail"][0]
    assert len(iter1["worker_output_excerpt"]) == 500
    assert iter1["worker_output_truncated"] is True


# ----------------------------------------------------------------------
# /api/review/audit
# ----------------------------------------------------------------------


def test_audit_returns_entries(client_factory) -> None:
    client, audit, _ = client_factory()
    _seed_audit(audit, "x", statuses=["pass"])
    res = client.get("/api/review/audit")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    # 2 Einträge pro Iter (worker + reviewer), 1 Iter
    assert len(data) == 2
    phases = {e["phase"] for e in data}
    assert phases == {"worker_spawn", "reviewer_spawn"}


def test_audit_limit_parameter(client_factory) -> None:
    client, audit, _ = client_factory()
    for i in range(5):
        _seed_audit(audit, f"r{i}", statuses=["pass"])
    # Insgesamt 10 Audit-Einträge, limit=3 → 3
    res = client.get("/api/review/audit?limit=3")
    assert res.status_code == 200
    assert len(res.json()) == 3


# ----------------------------------------------------------------------
# /api/review/stats
# ----------------------------------------------------------------------


def test_stats_returns_zero_when_no_runs(client_factory) -> None:
    client, _, _ = client_factory()
    res = client.get("/api/review/stats?window_days=7")
    assert res.status_code == 200
    data = res.json()
    assert data["window_days"] == 7
    assert data["runs_total"] == 0
    assert data["pass_rate"] == 0.0


def test_stats_aggregates_pass_rate(client_factory) -> None:
    client, audit, _ = client_factory()
    _seed_audit(audit, "a", statuses=["pass"])
    _seed_audit(audit, "b", statuses=["pass"])
    _seed_audit(audit, "c", statuses=["needs_revision", "needs_revision", "needs_revision"])

    res = client.get("/api/review/stats?window_days=30")
    assert res.status_code == 200
    data = res.json()
    assert data["runs_total"] == 3
    # 2 pass aus 3 → 0.6667
    assert data["pass_rate"] == pytest.approx(2 / 3, abs=0.01)
    # 1 cap_fired aus 3
    assert data["cap_fire_rate"] == pytest.approx(1 / 3, abs=0.01)


def test_stats_window_days_invalid_rejected(client_factory) -> None:
    client, _, _ = client_factory()
    # window_days < 1
    res = client.get("/api/review/stats?window_days=0")
    assert res.status_code == 422
    res = client.get("/api/review/stats?window_days=400")
    assert res.status_code == 422


def test_stats_cached_within_60s(client_factory, monkeypatch) -> None:
    """Wiederholter Aufruf mit gleichem window_days → Cache-Hit (kein Re-Read)."""
    from jarvis.ui.web import review_routes

    # Cache leeren falls vom anderen Test gefüllt
    review_routes._STATS_CACHE.clear()

    client, audit, _ = client_factory()
    _seed_audit(audit, "x", statuses=["pass"])

    res1 = client.get("/api/review/stats?window_days=7")
    assert res1.status_code == 200
    # Audit modifizieren — neuer Run hinzu
    _seed_audit(audit, "y", statuses=["pass"])
    res2 = client.get("/api/review/stats?window_days=7")
    # Wegen Cache: gleicher runs_total wie res1 (nicht 2)
    assert res2.json()["runs_total"] == res1.json()["runs_total"]
