#!/usr/bin/env python3
"""Flaky-test visibility for the sharded suite.

``run_tests_parallel.py`` re-runs a failed file alone and records it under
``flaky_files`` when that second attempt passes. Such a file is almost always
order-dependent (it leaks or relies on state from a neighbour in its batch),
yet the ratchet never blocks on it, so it used to stay invisible. This script
makes it visible in two places:

* ``summary`` writes flaky files plus new and known failures per OS to the
  GitHub step summary (read-only; safe on every event).
* ``vet-run`` / ``issue`` keep ONE tracking issue, "Flaky tests on main", with
  a rolling table (test file, OS, count, last seen run). The issue body carries
  its own machine-readable state, so no storage outside GitHub is needed and a
  re-run of the same CI run never counts twice.

The GitHub calls stay in ``.github/workflows/flaky-tests.yml``; everything
here is pure data in, text out, so the aggregation is unit-tested.

Commands::

    flaky_report.py summary [--baseline-dir scripts/ci] reports...
    flaky_report.py vet-run run.json --repo OWNER/NAME          # -> GITHUB_OUTPUT lines
    flaky_report.py issue --issues issues.json --out body.md --run-id N
        --run-url URL --run-at ISO [--keep-days 30] reports...    # -> GITHUB_OUTPUT lines
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ci.ratchet_tests import load_baseline, normalize  # noqa: E402

ISSUE_TITLE = "Flaky tests on main"
ISSUE_LABEL = "flaky-tests"
OS_ORDER = ("linux", "windows", "macos")
TRUSTED_EVENTS = frozenset({"push", "schedule", "workflow_dispatch"})
KEEP_DAYS = 30
MAX_LISTED = 40  # per section of the step summary
MAX_RUNS_REMEMBERED = 200
_STATE = re.compile(r"<!--\s*flaky-state:\s*(\{.*?\})\s*-->", re.DOTALL)
_REPORT_NAME = re.compile(r"test-report-(linux|windows|macos)-")
_DASH_DASH = "-" + chr(92) + "u002d"  # JSON escape for "-", spelled out for editors


# --------------------------------------------------------------------------- reports


@dataclass
class OsResult:
    """Everything one OS's shards said in one CI run."""

    shards: int = 0
    tests: int = 0
    passed: int = 0
    flaky: set[str] = field(default_factory=set)
    failures: set[str] = field(default_factory=set)


def os_of(report: dict, path: Path | None = None) -> str:
    """Map a report to linux / windows / macos (platform first, file name second)."""
    platform = str(report.get("platform", ""))
    if platform.startswith("linux"):
        return "linux"
    if platform in {"win32", "cygwin", "msys"}:
        return "windows"
    if platform == "darwin":
        return "macos"
    match = _REPORT_NAME.search(path.name) if path else None
    return match.group(1) if match else "unknown"


def report_files(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(sorted(path.rglob("test-report*.json")))
        elif path.is_file():
            found.append(path)
    return found


def collect(paths: list[Path]) -> dict[str, OsResult]:
    """Group shard reports by OS; an unreadable report is skipped with a warning."""
    results: dict[str, OsResult] = {}
    for path in report_files(paths):
        try:
            report = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            print(f"::warning::unreadable test report {path}: {exc}")
            continue
        if not isinstance(report, dict):
            print(f"::warning::test report {path} is not an object")
            continue
        result = results.setdefault(os_of(report, path), OsResult())
        counts = report.get("counts") if isinstance(report.get("counts"), dict) else {}
        result.shards += 1
        result.tests += int(counts.get("tests", 0) or 0)
        result.passed += int(counts.get("passed", 0) or 0)
        result.flaky.update(str(f) for f in report.get("flaky_files", []) or [])
        result.failures.update(normalize(str(f)) for f in report.get("failed_ids", []) or [])
    return results


def split_failures(failures: set[str], baseline: set[str] | None) -> tuple[list[str], list[str]]:
    """(new, known) — the ratchet's rule: anything outside the baseline is new."""
    known_ids = baseline or set()
    return sorted(failures - known_ids), sorted(failures & known_ids)


def _ordered(results: dict[str, OsResult]) -> list[str]:
    return sorted(results, key=lambda name: (OS_ORDER + (name,)).index(name))


def _code(text: str) -> str:
    return "`" + text.replace("`", "'").replace("|", "\\|") + "`"


def _bullets(items: list[str]) -> list[str]:
    lines = [f"- {_code(item)}" for item in items[:MAX_LISTED]]
    if len(items) > MAX_LISTED:
        lines.append(f"- ... and {len(items) - MAX_LISTED} more")
    return lines


def summary_lines(results: dict[str, OsResult], baseline_dir: Path) -> list[str]:
    lines = ["### Flaky files and failures", ""]
    if not results:
        return [*lines, "No test reports in this run (the python lane did not run)."]
    for name in _ordered(results):
        result = results[name]
        baseline = load_baseline(baseline_dir / f"test-baseline-{name}.json")
        new, known = split_failures(result.failures, baseline)
        lines.append(
            f"**{name}** ({result.shards} shard(s), {result.passed}/{result.tests} passed): "
            f"{len(result.flaky)} flaky file(s), {len(new)} new failure(s), "
            f"{len(known)} known failure(s)"
        )
        if result.flaky:
            lines += ["", "Flaky (failed in a batch, passed alone - suspect test order):"]
            lines += _bullets(sorted(result.flaky))
        if new:
            lines += ["", "New failures (not in the baseline):"]
            lines += _bullets(new)
        if known:
            lines += ["", "<details><summary>Known (baselined) failures</summary>", ""]
            lines += _bullets(known)
            lines += ["", "</details>"]
        lines.append("")
    return lines


# --------------------------------------------------------------------------- run vetting


def vet_run(run: dict, repo: str) -> tuple[dict[str, str], str]:
    """Accept only a finished CI run of this repository's main branch.

    The issue job holds ``issues: write``; a manually supplied run id must never
    let a pull-request run (possibly from a fork) feed it data.
    """
    head_repo = ((run.get("head_repository") or {}).get("full_name")) or ""
    if head_repo != repo:
        return {}, f"run belongs to {head_repo or 'an unknown repository'}, not {repo}"
    if run.get("head_branch") != "main":
        return {}, f"run is on branch {run.get('head_branch')!r}, not main"
    if run.get("event") not in TRUSTED_EVENTS:
        return {}, f"run was triggered by {run.get('event')!r}, not a main push or nightly"
    if run.get("status") != "completed":
        return {}, "run has not completed"
    run_id = str(run.get("id", ""))
    if not run_id.isdigit():
        return {}, "run has no numeric id"
    return {
        "id": run_id,
        "url": str(run.get("html_url", "")),
        "at": str(run.get("run_started_at") or run.get("created_at") or ""),
        "event": str(run.get("event")),
    }, ""


# --------------------------------------------------------------------------- tracking issue


def parse_state(body: str | None) -> dict:
    """Read the state block; a missing or hand-mangled block starts fresh."""
    empty: dict = {"entries": {}, "runs": []}
    match = _STATE.search(body or "")
    if not match:
        return empty
    try:
        state = json.loads(match.group(1))
    except ValueError:
        return empty
    if not isinstance(state, dict):
        return empty
    entries = state.get("entries")
    runs = state.get("runs")
    clean: dict[str, dict] = {}
    for key, entry in (entries if isinstance(entries, dict) else {}).items():
        if not (isinstance(entry, dict) and entry.get("file") and entry.get("os")):
            continue
        count = entry.get("count", 0)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            continue
        clean[str(key)] = entry
    return {
        "entries": clean,
        "runs": [str(r) for r in runs] if isinstance(runs, list) else [],
    }


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def merge_run(
    state: dict,
    results: dict[str, OsResult],
    *,
    run_id: str,
    run_url: str,
    run_at: str,
    keep_days: int = KEEP_DAYS,
) -> dict:
    """Fold one CI run into the rolling state.

    Count = number of distinct main/nightly runs in which the file was flaky on
    that OS. A run already folded in (a re-run attempt, a manual backfill) adds
    nothing. Rows not seen for ``keep_days`` drop out.
    """
    entries = {key: dict(entry) for key, entry in state.get("entries", {}).items()}
    runs = list(state.get("runs", []))
    if run_id not in runs:
        for os_name, result in results.items():
            for file in result.flaky:
                key = f"{os_name}|{file}"
                entry = entries.get(key) or {"file": file, "os": os_name, "count": 0}
                entry["count"] = int(entry.get("count", 0) or 0) + 1
                entry.setdefault("first_seen", run_at)
                entry.update(last_seen=run_at, last_run_id=run_id, last_run_url=run_url)
                entries[key] = entry
        runs = [*runs, run_id][-MAX_RUNS_REMEMBERED:]
    now = _parse_time(run_at) or datetime.now(UTC)
    cutoff = now - timedelta(days=keep_days)
    kept = {}
    for key, entry in entries.items():
        seen = _parse_time(str(entry.get("last_seen", "")))
        if seen is None or seen >= cutoff:
            kept[key] = entry
    return {"entries": kept, "runs": runs, "updated": run_at, "updated_run": run_url}


def render_issue(state: dict, keep_days: int = KEEP_DAYS) -> str:
    # Most frequent first; ties by most recently seen, then by OS and file.
    rows = sorted(state["entries"].values(), key=lambda e: (e["os"], e["file"]))
    rows.sort(key=lambda e: str(e.get("last_seen", "")), reverse=True)
    rows.sort(key=lambda e: int(e.get("count", 0) or 0), reverse=True)
    lines = [
        "Test files that failed inside their batch on `main` and then passed when the",
        "parallel runner re-ran them alone (`scripts/ci/run_tests_parallel.py`). That",
        "pattern almost always means test-order dependence: shared module state, a",
        "leaked patch, a fixed port or a temp file another test also uses. The ratchet",
        "does not block on them, so this table is where they stay visible.",
        "",
        "Updated automatically by `.github/workflows/flaky-tests.yml` after every",
        f"push and nightly CI run on `main`. Rows not seen for {keep_days} days drop out;",
        "fix a test, and its row ages out. Edits to this body are overwritten.",
        "",
    ]
    if rows:
        lines += ["| Test file | OS | Count | Last seen |", "| --- | --- | ---: | --- |"]
        for entry in rows:
            seen = str(entry.get("last_seen", ""))[:10] or "?"
            url = str(entry.get("last_run_url", ""))
            last = f"[{seen}]({url})" if url.startswith("https://") else seen
            lines.append(
                f"| {_code(str(entry['file']))} | {entry['os']} | {int(entry.get('count', 0))} "
                f"| {last} |"
            )
    else:
        lines.append(f"No flaky test files on `main` in the last {keep_days} days.")
    updated_run = str(state.get("updated_run", ""))
    if updated_run.startswith("https://"):
        lines += ["", f"Last update: [{str(state.get('updated', ''))[:16]}]({updated_run})"]
    # An HTML comment may not contain "--"; inside JSON strings the escape
    # decodes back to the same text.
    payload = json.dumps(state, sort_keys=True, separators=(",", ":")).replace("--", _DASH_DASH)
    lines += ["", f"<!-- flaky-state: {payload} -->", ""]
    return "\n".join(lines)


def pick_issue(issues: list[dict]) -> dict | None:
    """The tracking issue: exact title, an open one first, then the oldest."""
    matches = [i for i in issues if isinstance(i, dict) and i.get("title") == ISSUE_TITLE]
    if not matches:
        return None
    matches.sort(key=lambda i: (str(i.get("state", "")).upper() != "OPEN", int(i["number"])))
    return matches[0]


def plan_issue(issue: dict | None, body: str, has_rows: bool) -> str:
    """create | edit | reopen | none."""
    if issue is None:
        return "create" if has_rows else "none"
    if str(issue.get("state", "")).upper() != "OPEN":
        return "reopen" if has_rows else "none"
    return "edit" if issue.get("body") != body else "none"


# --------------------------------------------------------------------------- commands


def _output(values: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in values.items()]
    print("\n".join(lines))
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def _step_summary(lines: list[str]) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def cmd_summary(args: argparse.Namespace) -> int:
    lines = summary_lines(collect(args.reports), args.baseline_dir)
    print("\n".join(lines))
    _step_summary(lines)
    return 0


def cmd_vet_run(args: argparse.Namespace) -> int:
    run = json.loads(args.run.read_text(encoding="utf-8-sig"))
    values, reason = vet_run(run if isinstance(run, dict) else {}, args.repo)
    if reason:
        print(f"::error::refusing CI run: {reason}")
        return 1
    _output(values)
    return 0


def cmd_issue(args: argparse.Namespace) -> int:
    if not args.run_id.isdigit():
        print("::error::--run-id must be numeric")
        return 1
    issues = []
    if args.issues.is_file():
        issues = json.loads(args.issues.read_text(encoding="utf-8-sig"))
    issue = pick_issue(issues if isinstance(issues, list) else [])
    state = merge_run(
        parse_state(issue.get("body") if issue else ""),
        collect(args.reports),
        run_id=args.run_id,
        run_url=args.run_url,
        run_at=args.run_at,
        keep_days=args.keep_days,
    )
    body = render_issue(state, args.keep_days)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(body, encoding="utf-8")
    action = plan_issue(issue, body, bool(state["entries"]))
    _output(
        {
            "action": action,
            "number": str(issue["number"]) if issue else "",
            "rows": str(len(state["entries"])),
        }
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    summary = sub.add_parser("summary")
    summary.add_argument("--baseline-dir", type=Path, default=REPO_ROOT / "scripts" / "ci")
    summary.add_argument("reports", nargs="*", type=Path)
    vet = sub.add_parser("vet-run")
    vet.add_argument("run", type=Path)
    vet.add_argument("--repo", required=True)
    issue = sub.add_parser("issue")
    issue.add_argument("--issues", type=Path, required=True)
    issue.add_argument("--out", type=Path, required=True)
    issue.add_argument("--run-id", required=True)
    issue.add_argument("--run-url", required=True)
    issue.add_argument("--run-at", required=True)
    issue.add_argument("--keep-days", type=int, default=KEEP_DAYS)
    issue.add_argument("reports", nargs="*", type=Path)
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    handler = {"summary": cmd_summary, "vet-run": cmd_vet_run, "issue": cmd_issue}[args.cmd]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
