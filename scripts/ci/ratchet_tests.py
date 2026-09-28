#!/usr/bin/env python3
"""Known-failure ratchet for the sharded test suite.

The old CI step ended in ``|| echo`` — ANY failure rode through green as long
as the passed count stayed above a floor. The suite does carry a backlog of
failures (mostly platform-specific), so going strict in one step would turn
every PR red for reasons it did not cause. The ratchet splits the two:

* a failure listed in ``scripts/ci/test-baseline-<os>.json`` is KNOWN — it is
  reported, never blocking;
* any other failure is NEW — it blocks the change that introduced it;
* a known failure that now passes is reported so the baseline can shrink
  (``update`` rewrites it from a full run; it may only get smaller in review);
* ``flaky_files`` lists test FILES whose tests flip between runs on a busy
  runner (timing-sensitive). Any failure inside such a file is known. It is
  kept by hand, ``update`` preserves it, and it stays short on purpose.

Commands::

    ratchet_tests.py check --baseline B.json report*.json     # per shard, blocking
    ratchet_tests.py summary --baseline B.json --floor report*.json
    ratchet_tests.py update --out B.json report*.json          # regenerate
    ratchet_tests.py durations --out D.json dur*.json          # merge timing caches

A missing baseline file means "not bootstrapped yet": ``check`` reports and
passes, so a first run on a new OS leg can produce its own baseline.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_CRASH = re.compile(r"::<(?:timeout|pytest exit)[^>]*>$")


def normalize(test_id: str) -> str:
    """Timeouts and crashes differ only in their number between runs."""
    return _CRASH.sub("::<crash>", test_id)


def load_reports(paths: list[Path]) -> list[dict]:
    reports = []
    for path in paths:
        if path.is_dir():
            reports.extend(load_reports(sorted(path.rglob("test-report*.json"))))
            continue
        reports.append(json.loads(path.read_text(encoding="utf-8")))
    return reports


def load_baseline(path: Path) -> set[str] | None:
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return {normalize(x) for x in data.get("known_failures", [])}


def load_flaky_files(path: Path | None) -> list[str]:
    if not path or not path.is_file():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get("flaky_files", []))


def in_flaky_file(test_id: str, flaky_files: list[str]) -> bool:
    """True when ``test_id`` (dotted ``module::name`` or ``path::<crash>``) lives
    in one of ``flaky_files`` (repo-relative ``.py`` paths)."""
    head = test_id.split("::", 1)[0]
    for file in flaky_files:
        dotted = file[:-3].replace("/", ".") if file.endswith(".py") else file
        if head == file or head == dotted or head.startswith(dotted + "."):
            return True
    return False


def failed_ids(reports: list[dict]) -> set[str]:
    return {normalize(fid) for report in reports for fid in report.get("failed_ids", [])}


def _summary(lines: list[str]) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def cmd_check(args: argparse.Namespace) -> int:
    reports = load_reports(args.reports)
    baseline = load_baseline(args.baseline)
    failures = failed_ids(reports)
    if baseline is None:
        print(f"[ratchet] no baseline at {args.baseline} - report-only bootstrap mode")
        for fid in sorted(failures):
            print(f"  failing: {fid}")
        return 0
    flaky_files = load_flaky_files(args.baseline)
    new = sorted(f for f in failures - baseline if not in_flaky_file(f, flaky_files))
    known = sorted(set(failures) - set(new))
    flaky = sorted({f for r in reports for f in r.get("flaky_files", [])})
    print(f"[ratchet] {len(failures)} failing ids: {len(new)} new, {len(known)} known (baselined)")
    for fid in flaky:
        print(f"  flaky (passed on retry): {fid}")
    lines = [f"### Test ratchet ({args.baseline.name})", ""]
    if new:
        lines.append(f"**{len(new)} NEW failure(s)** — not baselined, so this change owns them:")
        lines.extend(f"- `{fid}`" for fid in new)
        for fid in new:
            print(f"::error title=New test failure::{fid}")
    else:
        lines.append(f"No new failures ({len(known)} known, {len(flaky)} flaky files).")
    _summary(lines)
    return 1 if new else 0


def cmd_summary(args: argparse.Namespace) -> int:
    reports = load_reports(args.reports)
    if not reports:
        print("[ratchet] no shard reports found - the test lane produced nothing")
        return 1
    counts = {"tests": 0, "passed": 0, "failed": 0, "skipped": 0}
    for report in reports:
        for key in counts:
            counts[key] += report.get("counts", {}).get(key, 0)
    status = 0
    lines = [
        f"### Test suite ({len(reports)} shards)",
        "",
        f"{counts['passed']} passed · {counts['failed']} failed · {counts['skipped']} skipped",
    ]
    if args.floor:
        from scripts.ci.assert_min_passed import FLOOR

        if counts["passed"] < FLOOR:
            lines.append(f"**FAIL: passed={counts['passed']} is below the floor {FLOOR}**")
            print(f"::error::passed={counts['passed']} < FLOOR={FLOOR} (mass-skip regression?)")
            status = 1
        else:
            lines.append(f"Min-passed floor: {counts['passed']} >= {FLOOR}")
    baseline = load_baseline(args.baseline) if args.baseline else None
    if baseline is not None:
        fixed = sorted(baseline - failed_ids(reports))
        if fixed:
            lines.append("")
            lines.append(f"{len(fixed)} baselined failure(s) now pass — shrink the baseline:")
            lines.extend(f"- `{fid}`" for fid in fixed[:50])
    slowest = max(reports, key=lambda r: float(r.get("elapsed_seconds", 0)))
    lines.append(f"Slowest shard: {slowest.get('shard')} ({slowest.get('elapsed_seconds')} s)")
    print("\n".join(lines))
    _summary(lines)
    return status


def cmd_update(args: argparse.Namespace) -> int:
    reports = load_reports(args.reports)
    failures = sorted(failed_ids(reports))
    flaky_files = load_flaky_files(args.out)
    payload = {
        "comment": (
            "Known test failures. Generated by scripts/ci/ratchet_tests.py update from "
            "one or more full CI runs on main (the union, so timing-sensitive tests that "
            "flip between runs are covered); entries may only be removed by hand, never "
            "added to hide a regression."
        ),
        "flaky_files": flaky_files,
        "known_failures": failures,
    }
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"[ratchet] wrote {len(failures)} known failures to {args.out}")
    return 0


def cmd_durations(args: argparse.Namespace) -> int:
    merged: dict[str, float] = {}
    if args.base and args.base.is_file():
        merged.update(json.loads(args.base.read_text(encoding="utf-8")))
    for path in args.inputs:
        files = sorted(path.rglob("durations*.json")) if path.is_dir() else [path]
        for file in files:
            merged.update(json.loads(file.read_text(encoding="utf-8")))
    payload = json.dumps(dict(sorted(merged.items())), indent=1) + "\n"
    args.out.write_text(payload, encoding="utf-8")
    print(f"[ratchet] duration cache: {len(merged)} files -> {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    check = sub.add_parser("check")
    check.add_argument("--baseline", type=Path, required=True)
    check.add_argument("reports", nargs="+", type=Path)
    summary = sub.add_parser("summary")
    summary.add_argument("--baseline", type=Path)
    summary.add_argument("--floor", action="store_true")
    summary.add_argument("reports", nargs="+", type=Path)
    update = sub.add_parser("update")
    update.add_argument("--out", type=Path, required=True)
    update.add_argument("reports", nargs="+", type=Path)
    durations = sub.add_parser("durations")
    durations.add_argument("--base", type=Path)
    durations.add_argument("--out", type=Path, required=True)
    durations.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args(argv)
    handler = {
        "check": cmd_check,
        "summary": cmd_summary,
        "update": cmd_update,
        "durations": cmd_durations,
    }[args.cmd]
    return handler(args)


if __name__ == "__main__":
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    sys.exit(main())
