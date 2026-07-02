"""GC for review-pipeline run artifacts (Phase 8.5).

Plan reference: §6.5 (CLI), §AD-11 (separate stores). This tool deletes
completed run directories under `data/review/runs/<run_id>/` whose
`final.json` is older than `--older-than`. Partially-finished runs
(without `final.json`) are NEVER deleted — recovery buffer.

The audit log (`data/review.log`) is NOT touched (plan §AD-11): it is
the unbroken audit trail. The GC run itself writes every deletion event
to `data/review_gc.log` (separately).

Usage:
    jarvis-review-gc [--older-than 30d] [--dry-run] [--keep-passing]
                     [--keep-cap-fired] [--runs-root <path>]
                     [--gc-log <path>]

Default: 30d, no-dry-run, no filters — deletes all completed runs
older than 30 days.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

_LOG = logging.getLogger(__name__)

DEFAULT_RUNS_ROOT = Path("data/review/runs")
DEFAULT_GC_LOG = Path("data/review_gc.log")
DEFAULT_OLDER_THAN = "30d"

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([dhm])\s*$", re.IGNORECASE)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def parse_duration(text: str) -> timedelta:
    """Parse `30d`/`12h`/`60m` → `timedelta`. Raises `ValueError` on invalid format."""
    m = _DURATION_RE.match(text)
    if not m:
        raise ValueError(
            f"Invalid duration {text!r} — expected format like '30d', '12h', '60m'"
        )
    value = int(m.group(1))
    unit = m.group(2).lower()
    if unit == "d":
        return timedelta(days=value)
    if unit == "h":
        return timedelta(hours=value)
    return timedelta(minutes=value)


def _read_final(run_dir: Path) -> dict | None:
    """Read `final.json` if it exists AND is parseable. Otherwise None."""
    final_path = run_dir / "final.json"
    if not final_path.exists():
        return None
    try:
        return json.loads(final_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _is_completed(run_dir: Path) -> bool:
    """A run counts as completed when `final.json` exists and is parseable."""
    return _read_final(run_dir) is not None


def _final_mtime(run_dir: Path) -> datetime | None:
    """Mtime of `final.json` as a UTC datetime; None if not present."""
    final_path = run_dir / "final.json"
    if not final_path.exists():
        return None
    try:
        return datetime.fromtimestamp(final_path.stat().st_mtime, tz=UTC)
    except OSError:
        return None


# ----------------------------------------------------------------------
# Decision-Logic
# ----------------------------------------------------------------------


def should_delete(
    run_dir: Path,
    *,
    cutoff: datetime,
    keep_passing: bool,
    keep_cap_fired: bool,
) -> tuple[bool, str]:
    """Decide whether a run directory should be deleted.

    Returns `(delete, reason)`. Reason is always a human-readable
    string that is written to the GC log.
    """
    if not run_dir.is_dir():
        return False, "not a directory"

    if not _is_completed(run_dir):
        return False, "incomplete (no final.json) — recovery buffer"

    mtime = _final_mtime(run_dir)
    if mtime is None or mtime > cutoff:
        return False, f"too recent (mtime={mtime})"

    final = _read_final(run_dir) or {}
    outcome = str(final.get("outcome", ""))

    if keep_passing and outcome == "success":
        return False, "kept (--keep-passing, outcome=success)"
    if keep_cap_fired and outcome == "cap_fired":
        return False, "kept (--keep-cap-fired, outcome=cap_fired)"

    return True, f"older than cutoff (outcome={outcome or 'unknown'})"


# ----------------------------------------------------------------------
# Run
# ----------------------------------------------------------------------


def run_gc(
    *,
    runs_root: Path,
    gc_log: Path,
    older_than: timedelta,
    dry_run: bool,
    keep_passing: bool,
    keep_cap_fired: bool,
) -> dict:
    """Execute the GC run and return a stats dict."""
    cutoff = datetime.now(UTC) - older_than
    deleted: list[str] = []
    kept: list[tuple[str, str]] = []

    if not runs_root.is_dir():
        return {
            "runs_root": str(runs_root),
            "exists": False,
            "deleted": [],
            "kept": [],
            "dry_run": dry_run,
        }

    gc_log.parent.mkdir(parents=True, exist_ok=True)

    for run_dir in sorted(runs_root.iterdir()):
        delete, reason = should_delete(
            run_dir,
            cutoff=cutoff,
            keep_passing=keep_passing,
            keep_cap_fired=keep_cap_fired,
        )
        if delete:
            if not dry_run:
                try:
                    shutil.rmtree(run_dir)
                except OSError as exc:
                    _log_gc(gc_log, run_dir, "skipped", f"rmtree error: {exc}", dry_run)
                    kept.append((run_dir.name, f"rmtree error: {exc}"))
                    continue
            _log_gc(gc_log, run_dir, "deleted" if not dry_run else "would-delete", reason, dry_run)
            deleted.append(run_dir.name)
        else:
            _log_gc(gc_log, run_dir, "kept", reason, dry_run)
            kept.append((run_dir.name, reason))

    return {
        "runs_root": str(runs_root),
        "exists": True,
        "deleted": deleted,
        "kept": [{"run_id": rid, "reason": reason} for rid, reason in kept],
        "dry_run": dry_run,
        "cutoff": cutoff.isoformat(),
    }


def _log_gc(
    gc_log: Path, run_dir: Path, action: str, reason: str, dry_run: bool
) -> None:
    """Write one JSON-Lines entry per deletion/skip event."""
    line = json.dumps(
        {
            "ts": datetime.now(UTC).isoformat(),
            "run_id": run_dir.name,
            "action": action,
            "reason": reason,
            "dry_run": dry_run,
        },
        ensure_ascii=False,
    )
    try:
        with gc_log.open("a", encoding="utf-8", newline="") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        _LOG.warning("gc_log write failed: %s", exc)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis-review-gc",
        description=(
            "Deletes completed review-pipeline run directories "
            "whose final.json is older than --older-than."
        ),
    )
    parser.add_argument(
        "--older-than",
        default=DEFAULT_OLDER_THAN,
        help="Minimum age to delete (format: '30d', '12h', '60m'). Default 30d.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Lists directories that would be deleted, but does NOT delete.",
    )
    parser.add_argument(
        "--keep-passing",
        action="store_true",
        help="Keeps runs with outcome=success (even if old).",
    )
    parser.add_argument(
        "--keep-cap-fired",
        action="store_true",
        help="Keeps runs with outcome=cap_fired (even if old).",
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=DEFAULT_RUNS_ROOT,
        help=f"Run-directory root. Default: {DEFAULT_RUNS_ROOT}",
    )
    parser.add_argument(
        "--gc-log",
        type=Path,
        default=DEFAULT_GC_LOG,
        help=f"GC-Audit-Log (JSON-Lines). Default: {DEFAULT_GC_LOG}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        older_than = parse_duration(args.older_than)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    stats = run_gc(
        runs_root=args.runs_root,
        gc_log=args.gc_log,
        older_than=older_than,
        dry_run=args.dry_run,
        keep_passing=args.keep_passing,
        keep_cap_fired=args.keep_cap_fired,
    )

    n_del = len(stats["deleted"])
    n_kept = len(stats["kept"])
    label = "would-delete" if args.dry_run else "deleted"
    print(f"{label}: {n_del} run-dir(s); kept: {n_kept}")
    if args.dry_run and stats["deleted"]:
        print("\n".join(f"  {rid}" for rid in stats["deleted"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
