"""Tests for the review-pipeline GC CLI (Phase 8.5).

Plan reference: §6.5 acceptance criterion 2 — a temp directory with fake
run dirs of different ages, a GC call, verifies that only the
correct ones were deleted.
"""
from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jarvis.cli.review_gc import (
    main,
    parse_duration,
    run_gc,
    should_delete,
)


def _make_run(
    runs_root: Path,
    run_id: str,
    *,
    final: dict | None = None,
    age_days: float = 0.0,
) -> Path:
    """Creates a fake run dir, optionally with final.json and an mtime backdate."""
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "iter-1").mkdir(exist_ok=True)
    (run_dir / "iter-1" / "worker.out").write_text("body", encoding="utf-8")

    if final is not None:
        final_path = run_dir / "final.json"
        final_path.write_text(json.dumps(final), encoding="utf-8")
        if age_days > 0:
            past = time.time() - (age_days * 86400)
            os.utime(final_path, (past, past))
    return run_dir


# ----------------------------------------------------------------------
# parse_duration
# ----------------------------------------------------------------------


def test_parse_duration_days() -> None:
    assert parse_duration("30d") == timedelta(days=30)


def test_parse_duration_hours() -> None:
    assert parse_duration("12h") == timedelta(hours=12)


def test_parse_duration_minutes() -> None:
    assert parse_duration("60m") == timedelta(minutes=60)


def test_parse_duration_invalid() -> None:
    with pytest.raises(ValueError):
        parse_duration("30")
    with pytest.raises(ValueError):
        parse_duration("garbage")


# ----------------------------------------------------------------------
# should_delete
# ----------------------------------------------------------------------


def test_should_delete_skips_incomplete(tmp_path: Path) -> None:
    """A run without final.json is NEVER deleted (recovery buffer)."""
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    run_dir = _make_run(runs_root, "incomplete", final=None)
    cutoff = datetime.now(UTC) - timedelta(days=1)
    delete, reason = should_delete(
        run_dir, cutoff=cutoff, keep_passing=False, keep_cap_fired=False
    )
    assert delete is False
    assert "incomplete" in reason.lower()


def test_should_delete_old_completed(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    run_dir = _make_run(
        runs_root, "old-pass", final={"outcome": "success"}, age_days=60
    )
    cutoff = datetime.now(UTC) - timedelta(days=30)
    delete, _ = should_delete(
        run_dir, cutoff=cutoff, keep_passing=False, keep_cap_fired=False
    )
    assert delete is True


def test_should_delete_keeps_passing(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    run_dir = _make_run(
        runs_root, "old-pass", final={"outcome": "success"}, age_days=60
    )
    cutoff = datetime.now(UTC) - timedelta(days=30)
    delete, reason = should_delete(
        run_dir, cutoff=cutoff, keep_passing=True, keep_cap_fired=False
    )
    assert delete is False
    assert "keep-passing" in reason.lower()


def test_should_delete_keeps_cap_fired(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    run_dir = _make_run(
        runs_root, "old-cap", final={"outcome": "cap_fired"}, age_days=60
    )
    cutoff = datetime.now(UTC) - timedelta(days=30)
    delete, reason = should_delete(
        run_dir, cutoff=cutoff, keep_passing=False, keep_cap_fired=True
    )
    assert delete is False
    assert "cap" in reason.lower()


def test_should_delete_keeps_recent(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    run_dir = _make_run(
        runs_root, "recent", final={"outcome": "success"}, age_days=1
    )
    cutoff = datetime.now(UTC) - timedelta(days=30)
    delete, reason = should_delete(
        run_dir, cutoff=cutoff, keep_passing=False, keep_cap_fired=False
    )
    assert delete is False
    assert "recent" in reason.lower()


# ----------------------------------------------------------------------
# run_gc
# ----------------------------------------------------------------------


def test_run_gc_dry_run_does_not_delete(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    _make_run(runs_root, "old", final={"outcome": "success"}, age_days=60)

    stats = run_gc(
        runs_root=runs_root,
        gc_log=tmp_path / "gc.log",
        older_than=timedelta(days=30),
        dry_run=True,
        keep_passing=False,
        keep_cap_fired=False,
    )
    assert stats["deleted"] == ["old"]
    # But: the directory is STILL there (dry_run)
    assert (runs_root / "old").is_dir()


def test_run_gc_actually_deletes(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    _make_run(runs_root, "to-delete", final={"outcome": "success"}, age_days=60)
    _make_run(runs_root, "keep-recent", final={"outcome": "success"}, age_days=1)

    stats = run_gc(
        runs_root=runs_root,
        gc_log=tmp_path / "gc.log",
        older_than=timedelta(days=30),
        dry_run=False,
        keep_passing=False,
        keep_cap_fired=False,
    )
    assert stats["deleted"] == ["to-delete"]
    assert not (runs_root / "to-delete").exists()
    assert (runs_root / "keep-recent").is_dir()


def test_run_gc_writes_gc_log(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    gc_log = tmp_path / "review_gc.log"
    _make_run(runs_root, "alt", final={"outcome": "success"}, age_days=60)
    _make_run(runs_root, "neu", final={"outcome": "success"}, age_days=1)

    run_gc(
        runs_root=runs_root,
        gc_log=gc_log,
        older_than=timedelta(days=30),
        dry_run=False,
        keep_passing=False,
        keep_cap_fired=False,
    )

    assert gc_log.exists()
    lines = [
        json.loads(line)
        for line in gc_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    actions = {entry["run_id"]: entry["action"] for entry in lines}
    assert actions["alt"] == "deleted"
    assert actions["neu"] == "kept"


def test_run_gc_does_not_touch_audit_log(tmp_path: Path) -> None:
    """Plan §AD-11: GC must NOT touch the audit log."""
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    _make_run(runs_root, "x", final={"outcome": "success"}, age_days=60)

    audit_log = tmp_path / "review.log"
    audit_log.write_text("audit-content", encoding="utf-8")
    audit_mtime_before = audit_log.stat().st_mtime

    run_gc(
        runs_root=runs_root,
        gc_log=tmp_path / "gc.log",
        older_than=timedelta(days=30),
        dry_run=False,
        keep_passing=False,
        keep_cap_fired=False,
    )

    assert audit_log.read_text(encoding="utf-8") == "audit-content"
    assert audit_log.stat().st_mtime == audit_mtime_before


def test_run_gc_handles_missing_runs_root(tmp_path: Path) -> None:
    stats = run_gc(
        runs_root=tmp_path / "nonexistent",
        gc_log=tmp_path / "gc.log",
        older_than=timedelta(days=30),
        dry_run=False,
        keep_passing=False,
        keep_cap_fired=False,
    )
    assert stats["exists"] is False
    assert stats["deleted"] == []


# ----------------------------------------------------------------------
# main()
# ----------------------------------------------------------------------


def test_main_dry_run_argv(tmp_path: Path, capsys) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    _make_run(runs_root, "alt", final={"outcome": "success"}, age_days=60)

    rc = main([
        "--older-than",
        "30d",
        "--dry-run",
        "--runs-root",
        str(runs_root),
        "--gc-log",
        str(tmp_path / "gc.log"),
    ])
    assert rc == 0
    captured = capsys.readouterr()
    assert "would-delete" in captured.out


def test_main_invalid_duration_returns_2(tmp_path: Path, capsys) -> None:
    rc = main([
        "--older-than",
        "garbage",
        "--runs-root",
        str(tmp_path),
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Invalid duration" in err
