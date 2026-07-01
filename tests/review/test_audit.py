"""Tests for the ReviewAudit JSON-lines logger (Phase 8.1).

Plan reference: §6.1 acceptance criterion 3 — concurrent-write test
(10 threads x 100 entries), no data loss.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from jarvis.core.review.audit import (
    AuditPhase,
    AuditRecord,
    AuditStatus,
    ReviewAudit,
)


def _make_record(
    *,
    run_id: str = "run-1",
    iteration: int = 1,
    phase: AuditPhase = AuditPhase.REVIEWER_SPAWN,
    status: AuditStatus = AuditStatus.PASS,
    score: float | None = 0.9,
    issue_count: int = 0,
    cap_fired: bool = False,
) -> AuditRecord:
    return AuditRecord(
        run_id=run_id,
        iteration=iteration,
        phase=phase,
        status=status,
        score=score,
        issue_count=issue_count,
        cap_fired=cap_fired,
    )


# ----------------------------------------------------------------------
# Path override
# ----------------------------------------------------------------------


def test_constructor_path_override(tmp_path: Path) -> None:
    """Plan §6.1: default path is hardcoded, overridable via the ctor."""
    custom = tmp_path / "subdir" / "review.log"
    audit = ReviewAudit(path=custom)
    assert audit.path == custom


def test_default_path_is_data_review_log() -> None:
    audit = ReviewAudit()
    assert audit.path == Path("data") / "review.log"


def test_constructor_accepts_string_path(tmp_path: Path) -> None:
    custom_str = str(tmp_path / "review.log")
    audit = ReviewAudit(path=custom_str)
    assert audit.path == Path(custom_str)


# ----------------------------------------------------------------------
# Single write
# ----------------------------------------------------------------------


def test_append_iteration_single_write(tmp_path: Path) -> None:
    """One append_iteration -> exactly one line, parseable."""
    log = tmp_path / "review.log"
    audit = ReviewAudit(path=log)
    record = _make_record()

    audit.append_iteration(record)

    assert log.exists()
    raw = log.read_text(encoding="utf-8").strip()
    assert raw, "log file is empty after write"
    assert "\n" not in raw, "single record produced multiple lines"

    parsed = json.loads(raw)
    assert parsed["run_id"] == "run-1"
    assert parsed["iteration"] == 1
    assert parsed["phase"] == "reviewer_spawn"
    assert parsed["status"] == "pass"
    assert parsed["score"] == 0.9
    assert parsed["cap_fired"] is False
    assert "ts" in parsed


def test_append_iteration_creates_parent_dir(tmp_path: Path) -> None:
    """Parent dir is created if missing."""
    log = tmp_path / "deep" / "nested" / "review.log"
    audit = ReviewAudit(path=log)
    audit.append_iteration(_make_record())
    assert log.exists()


def test_append_iteration_appends_not_overwrites(tmp_path: Path) -> None:
    """Multiple records -> multiple lines, ordered as written."""
    log = tmp_path / "review.log"
    audit = ReviewAudit(path=log)

    audit.append_iteration(_make_record(iteration=1))
    audit.append_iteration(_make_record(iteration=2))
    audit.append_iteration(_make_record(iteration=3))

    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert [p["iteration"] for p in parsed] == [1, 2, 3]


def test_append_iteration_with_optional_fields_omitted(tmp_path: Path) -> None:
    """Pre-check failure writes without score/tokens — defaults kick in."""
    log = tmp_path / "review.log"
    audit = ReviewAudit(path=log)

    record = AuditRecord(
        run_id="r",
        iteration=0,
        phase=AuditPhase.PRECHECK,
        status=AuditStatus.PRECHECK_FAIL,
    )
    audit.append_iteration(record)

    parsed = json.loads(log.read_text(encoding="utf-8").strip())
    assert parsed["score"] is None
    assert parsed["tokens_in"] == 0
    assert parsed["tokens_out"] == 0
    assert parsed["latency_ms"] == 0
    assert parsed["issue_count"] == 0


# ----------------------------------------------------------------------
# Concurrent write (10 threads x 100 entries)
# ----------------------------------------------------------------------


def test_concurrent_write_no_corruption(tmp_path: Path) -> None:
    """Plan §6.1 AC: 10 threads x 100 entries -> 1000 lines, all parseable."""
    log = tmp_path / "review.log"
    audit = ReviewAudit(path=log)

    n_threads = 10
    per_thread = 100

    def worker(thread_id: int) -> None:
        for i in range(per_thread):
            audit.append_iteration(
                _make_record(
                    run_id=f"thread-{thread_id}",
                    iteration=i,
                    phase=AuditPhase.WORKER_SPAWN,
                    status=AuditStatus.PASS,
                    score=float(thread_id) / 10.0,
                )
            )

    threads = [
        threading.Thread(target=worker, args=(t,), name=f"audit-{t}")
        for t in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    raw_text = log.read_text(encoding="utf-8")
    lines = [line for line in raw_text.splitlines() if line.strip()]

    # No corruption, no missing lines
    assert len(lines) == n_threads * per_thread, (
        f"expected {n_threads * per_thread} lines, got {len(lines)}"
    )

    # All lines are valid JSON
    parsed = [json.loads(line) for line in lines]

    # Exactly `per_thread` entries per thread
    by_thread: dict[str, list[int]] = {}
    for p in parsed:
        by_thread.setdefault(p["run_id"], []).append(p["iteration"])
    assert len(by_thread) == n_threads
    for thread_id, iterations in by_thread.items():
        assert sorted(iterations) == list(range(per_thread)), (
            f"thread {thread_id}: expected 0..{per_thread - 1}, got {sorted(iterations)}"
        )


# ----------------------------------------------------------------------
# tail()
# ----------------------------------------------------------------------


def test_tail_returns_last_n(tmp_path: Path) -> None:
    log = tmp_path / "review.log"
    audit = ReviewAudit(path=log)
    for i in range(5):
        audit.append_iteration(_make_record(iteration=i))

    tail = audit.tail(n=3)
    assert len(tail) == 3
    assert [t["iteration"] for t in tail] == [2, 3, 4]


def test_tail_empty_log(tmp_path: Path) -> None:
    audit = ReviewAudit(path=tmp_path / "no-such-file.log")
    assert audit.tail() == []


def test_tail_zero_returns_empty(tmp_path: Path) -> None:
    log = tmp_path / "review.log"
    audit = ReviewAudit(path=log)
    audit.append_iteration(_make_record())
    assert audit.tail(0) == []


def test_tail_skips_corrupt_lines(tmp_path: Path) -> None:
    """Corrupt JSON lines are skipped, not propagated."""
    log = tmp_path / "review.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    audit = ReviewAudit(path=log)
    audit.append_iteration(_make_record(iteration=1))
    # We manually append artificial corruption
    with log.open("a", encoding="utf-8") as fh:
        fh.write("this is { not valid json\n")
    audit.append_iteration(_make_record(iteration=2))

    tail = audit.tail()
    iterations = [t["iteration"] for t in tail]
    assert iterations == [1, 2]


# ----------------------------------------------------------------------
# AuditRecord validation
# ----------------------------------------------------------------------


def test_audit_record_invalid_phase_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AuditRecord(
            run_id="r",
            iteration=0,
            phase="bogus_phase",  # type: ignore[arg-type]
            status=AuditStatus.PASS,
        )


def test_audit_record_invalid_status_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AuditRecord(
            run_id="r",
            iteration=0,
            phase=AuditPhase.PRECHECK,
            status="approved",  # type: ignore[arg-type]
        )


def test_audit_record_negative_iteration_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AuditRecord(
            run_id="r",
            iteration=-1,
            phase=AuditPhase.PRECHECK,
            status=AuditStatus.PASS,
        )


def test_audit_record_score_out_of_range_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AuditRecord(
            run_id="r",
            iteration=0,
            phase=AuditPhase.REVIEWER_SPAWN,
            status=AuditStatus.PASS,
            score=1.5,
        )


def test_audit_record_to_jsonline_no_newline() -> None:
    """to_jsonline must NOT contain a trailing newline — the logger adds that."""
    record = _make_record()
    line = record.to_jsonline()
    assert "\n" not in line
    parsed = json.loads(line)
    assert parsed["run_id"] == "run-1"
