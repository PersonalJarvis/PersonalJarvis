#!/usr/bin/env python3
"""Sharded, process-isolated pytest runner (the CI test engine).

Why this exists: one monolithic ``pytest tests/`` process took 75-95 minutes
on a 4-core runner and routinely hit the job timeout. A single wedged test ate
the whole budget, and module state leaked across the ~2 000 test files.

How it works (the idea is borrowed from Hermes Agent's per-file runner):

1. **Discover** every ``test_*.py`` / ``*_test.py`` under the roots.
2. **Shard** (``--shard 2/6``): longest-processing-time bin packing over the
   per-file duration cache, so shards finish together. Every shard computes
   the same packing, so no coordination is needed.
3. **Batch**: consecutive files of one directory run in one pytest process
   (the ~1.5 s interpreter + conftest start-up is paid once per batch).
4. **Isolate on failure**: a failed or timed-out batch is re-run file by file
   in fresh processes, and a failed file gets one more fresh attempt. A pass
   on that attempt is reported as FLAKY, not as a failure.
5. **Bound**: every process has a wall-clock budget and is killed with its
   whole process tree, so a hang costs one file, never the job.

Outputs: ``--report`` (JSON: failed test ids, flaky files, counts) and
``--junit-out`` (merged JUnit XML), plus ``--durations-out`` (timings of files
that passed on the first attempt — a hang can never ratchet its own budget).

The runner exits 1 when any test failed for real; whether that blocks the
build is ``test_ratchet.py``'s decision (known failures are baselined).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET  # noqa: S405 - parses our own pytest output
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MARKERS = (
    "not skip_ci and not slow and not openclaw_live and not voice_latency and not eval"
)
_SKIP_DIRS = {"__pycache__", "node_modules", "fakes", "fixtures", ".pytest_cache"}
DEFAULT_DURATION = 4.0  # seconds, for a file the cache has never seen
MIN_FILE_TIMEOUT = 300.0
MAX_FILE_TIMEOUT = 900.0
BATCH_TARGET_SECONDS = 45.0
BATCH_MAX_FILES = 12
_OK_EXIT_CODES = {0, 5}  # 5 = every test deselected / none collected


# --------------------------------------------------------------------------- discovery


def discover(roots: list[str], repo: Path = REPO_ROOT) -> list[str]:
    files: set[str] = set()
    for root in roots:
        base = (repo / root).resolve()
        if base.is_file():
            files.add(base.relative_to(repo).as_posix())
            continue
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            rel = path.relative_to(repo)
            if any(part in _SKIP_DIRS for part in rel.parts):
                continue
            if path.name.startswith("test_") or path.name.endswith("_test.py"):
                files.add(rel.as_posix())
    return sorted(files)


def load_durations(path: Path | None) -> dict[str, float]:
    if not path or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[runner] duration cache unreadable ({exc}); using defaults", flush=True)
        return {}
    return {str(k): float(v) for k, v in data.items() if isinstance(v, int | float)}


def estimate(file: str, durations: dict[str, float]) -> float:
    return max(0.5, durations.get(file, DEFAULT_DURATION))


def shard(files: list[str], index: int, total: int, durations: dict[str, float]) -> list[str]:
    """Deterministic LPT bin packing; returns shard ``index`` (1-based) of ``total``."""
    if total <= 1:
        return list(files)
    bins: list[tuple[float, int, list[str]]] = [(0.0, i, []) for i in range(total)]
    for file in sorted(files, key=lambda f: (-estimate(f, durations), f)):
        load, idx, members = min(bins, key=lambda b: (b[0], b[1]))
        members.append(file)
        bins[idx] = (load + estimate(file, durations), idx, members)
    return sorted(bins[index - 1][2])


def make_batches(files: list[str], durations: dict[str, float]) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    current_dir = None
    load = 0.0
    for file in sorted(files):
        directory = file.rsplit("/", 1)[0]
        cost = estimate(file, durations)
        # A file that alone exceeds the target always runs by itself.
        if current and (
            directory != current_dir
            or load + cost > BATCH_TARGET_SECONDS
            or len(current) >= BATCH_MAX_FILES
            or cost > BATCH_TARGET_SECONDS
        ):
            batches.append(current)
            current, load = [], 0.0
        current.append(file)
        current_dir = directory
        load += cost
    if current:
        batches.append(current)
    return batches


def file_timeout(file: str, durations: dict[str, float]) -> float:
    return min(MAX_FILE_TIMEOUT, max(MIN_FILE_TIMEOUT, 3 * durations.get(file, 0.0)))


# --------------------------------------------------------------------------- execution


@dataclass
class RunResult:
    files: list[str]
    exit_code: int
    duration: float
    timed_out: bool
    failed_ids: list[str] = field(default_factory=list)
    file_seconds: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    junit: Path | None = None
    log_tail: str = ""

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.exit_code in _OK_EXIT_CODES and not self.failed_ids


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    try:
        if os.name == "nt":
            subprocess.run(  # noqa: S603
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],  # noqa: S607
                capture_output=True,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[runner] could not kill process tree {proc.pid}: {exc}", flush=True)
    try:
        proc.kill()
    except OSError:
        pass  # already gone - the tree kill above reaped it


def _module_to_file(classname: str, files: list[str]) -> str | None:
    dotted = classname.replace(".", "/")
    best = None
    for file in files:
        stem = file[:-3]
        if dotted == stem or dotted.startswith(stem + "/"):
            if best is None or len(stem) > len(best[:-3]):
                best = file
    return best


def parse_junit(path: Path, files: list[str]) -> tuple[list[str], dict[str, float], dict[str, int]]:
    """Return (failed ids, seconds per file, counts) from one pytest JUnit file."""
    failed: list[str] = []
    seconds: dict[str, float] = {}
    counts = {"tests": 0, "passed": 0, "failed": 0, "skipped": 0}
    if not path.is_file():
        return failed, seconds, counts
    try:
        root = ET.parse(path).getroot()  # noqa: S314 - our own pytest output
    except ET.ParseError as exc:
        print(f"[runner] unreadable JUnit {path.name}: {exc}", flush=True)
        return failed, seconds, counts
    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")
        counts["tests"] += 1
        owner = _module_to_file(classname, files) or (files[0] if len(files) == 1 else "")
        seconds[owner] = seconds.get(owner, 0.0) + float(case.get("time", 0) or 0)
        if case.find("failure") is not None or case.find("error") is not None:
            counts["failed"] += 1
            failed.append(f"{classname}::{name}")
        elif case.find("skipped") is not None:
            counts["skipped"] += 1
        else:
            counts["passed"] += 1
    return failed, seconds, counts


class Runner:
    def __init__(self, args: argparse.Namespace, durations: dict[str, float], workdir: Path):
        self.args = args
        self.durations = durations
        self.workdir = workdir
        self._seq = 0
        self._lock = threading.Lock()

    def _next_id(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    def run(self, files: list[str], timeout: float) -> RunResult:
        run_id = self._next_id()
        junit = self.workdir / f"run-{run_id:05d}.xml"
        log = self.workdir / f"run-{run_id:05d}.log"
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            *files,
            "-q",
            "-p",
            "no:cacheprovider",
            "-o",
            "faulthandler_timeout=240",
            f"--basetemp={self.workdir / f'tmp-{run_id:05d}'}",
            f"--junitxml={junit}",
            "-m",
            self.args.markers,
            *shlex.split(self.args.pytest_args or ""),
        ]
        env = dict(os.environ)
        env.setdefault("PYTHONUTF8", "1")
        env["JARVIS_TEST_RUNNER_ISOLATED"] = "1"
        popen_kwargs: dict[str, object] = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        else:
            popen_kwargs["start_new_session"] = True
        started = time.monotonic()
        timed_out = False
        with log.open("wb") as sink:
            proc = subprocess.Popen(  # noqa: S603
                cmd,
                cwd=REPO_ROOT,
                stdout=sink,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                **popen_kwargs,  # type: ignore[arg-type]
            )
            try:
                exit_code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_tree(proc)
                exit_code = proc.wait()
        duration = time.monotonic() - started
        failed, seconds, counts = parse_junit(junit, files)
        tail = ""
        if timed_out or exit_code not in _OK_EXIT_CODES or failed:
            text = log.read_text(encoding="utf-8", errors="replace")
            tail = "\n".join(text.splitlines()[-60:])
            if timed_out:
                failed = failed or [f"{f}::<timeout {int(timeout)}s>" for f in files]
            elif not failed:
                failed = [f"{f}::<pytest exit {exit_code}>" for f in files]
        return RunResult(
            files, exit_code, duration, timed_out, failed, seconds, counts, junit, tail
        )

    def run_unit(self, batch: list[str]) -> dict[str, object]:
        """Run one batch with isolation-on-failure and a flaky retry."""
        budget = min(1800.0, sum(file_timeout(f, self.durations) for f in batch))
        first = self.run(batch, budget)
        outcome: dict[str, object] = {"runs": [first], "flaky": [], "first_pass": []}
        if first.ok:
            outcome["first_pass"] = list(batch)
            return outcome
        runs: list[RunResult] = []
        flaky: list[str] = []
        for file in batch:
            budget_one = file_timeout(file, self.durations)
            single = first if len(batch) == 1 else self.run([file], budget_one)
            if not single.ok and not single.timed_out:
                retry = self.run([file], budget_one)
                if retry.ok:
                    flaky.append(file)
                    single = retry
            runs.append(single)
        outcome["runs"] = runs
        outcome["flaky"] = flaky
        return outcome


# --------------------------------------------------------------------------- reporting


def merge_junit(paths: list[Path], out: Path) -> None:
    merged = ET.Element("testsuites")
    for path in paths:
        if not path.is_file():
            continue
        try:
            root = ET.parse(path).getroot()  # noqa: S314 - our own pytest output
        except ET.ParseError as exc:
            print(f"[runner] skipping unreadable JUnit {path.name}: {exc}", flush=True)
            continue
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        merged.extend(suites)
    out.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(merged).write(out, encoding="utf-8", xml_declaration=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("roots", nargs="*", default=["tests"], help="Discovery roots.")
    parser.add_argument("--files-from", type=Path, help="Explicit file list (one per line).")
    parser.add_argument("--shard", default="1/1", help="i/n — run shard i of n.")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    parser.add_argument("--markers", default=DEFAULT_MARKERS)
    parser.add_argument("--pytest-args", default="", help="Extra args for every pytest run.")
    parser.add_argument("--durations", type=Path, default=REPO_ROOT / "test_durations.json")
    parser.add_argument("--durations-out", type=Path)
    parser.add_argument("--report", type=Path, default=Path("test-report.json"))
    parser.add_argument("--junit-out", type=Path, default=Path("report.xml"))
    parser.add_argument("--list", action="store_true", help="Print the shard's files and exit.")
    args = parser.parse_args(argv)
    # Windows consoles default to cp1252; a test log with box-drawing
    # characters must never crash the runner that is reporting it (AP-1).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    index, total = (int(x) for x in args.shard.split("/", 1))
    durations = load_durations(args.durations)
    if args.files_from:
        wanted = [line.strip() for line in args.files_from.read_text(encoding="utf-8").splitlines()]
        files = sorted({f for f in wanted if f and (REPO_ROOT / f).is_file()})
    else:
        files = discover(args.roots)
    mine = shard(files, index, total, durations)
    if args.list:
        print("\n".join(mine))
        return 0

    print(
        f"[runner] shard {index}/{total}: {len(mine)} of {len(files)} files, "
        f"{args.workers} workers, markers={args.markers!r}",
        flush=True,
    )
    batches = make_batches(mine, durations)
    started = time.monotonic()
    workdir = Path(tempfile.mkdtemp(prefix="jarvis-tests-"))
    runner = Runner(args, durations, workdir)
    all_runs: list[RunResult] = []
    flaky: list[str] = []
    first_pass: list[str] = []
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(runner.run_unit, batch) for batch in batches]
        for future in futures:
            outcome = future.result()
            runs = outcome["runs"]  # type: ignore[assignment]
            all_runs.extend(runs)  # type: ignore[arg-type]
            flaky.extend(outcome["flaky"])  # type: ignore[arg-type]
            first_pass.extend(outcome["first_pass"])  # type: ignore[arg-type]
            done += 1
            for run in runs:  # type: ignore[attr-defined]
                if not run.ok:
                    label = "TIMEOUT" if run.timed_out else f"FAIL exit={run.exit_code}"
                    names = " ".join(run.files)
                    print(f"\n[runner] {label}: {names}\n{run.log_tail}\n", flush=True)
            if done % 20 == 0 or done == len(futures):
                print(f"[runner] {done}/{len(futures)} batches done", flush=True)

    failed_ids = sorted({fid for run in all_runs for fid in run.failed_ids})
    counts = {"tests": 0, "passed": 0, "failed": 0, "skipped": 0}
    for run in all_runs:
        for key in counts:
            counts[key] += run.counts.get(key, 0)
    elapsed = time.monotonic() - started
    report = {
        "shard": f"{index}/{total}",
        "platform": sys.platform,
        "files": len(mine),
        "counts": counts,
        "failed_ids": failed_ids,
        "flaky_files": sorted(set(flaky)),
        "timeouts": sorted({f for run in all_runs if run.timed_out for f in run.files}),
        "elapsed_seconds": round(elapsed, 1),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    merge_junit([run.junit for run in all_runs if run.junit], args.junit_out)

    if args.durations_out:
        fresh: dict[str, float] = {}
        for run in all_runs:
            for file, secs in run.file_seconds.items():
                if file in first_pass:
                    fresh[file] = round(secs + 1.0, 2)  # + interpreter start-up share
        payload = json.dumps(fresh, indent=1, sort_keys=True) + "\n"
        args.durations_out.write_text(payload, encoding="utf-8")

    print(
        f"[runner] done in {elapsed:.0f}s: {counts['passed']} passed, {counts['failed']} failed, "
        f"{counts['skipped']} skipped, {len(set(flaky))} flaky files, "
        f"{len(report['timeouts'])} timed-out files",
        flush=True,
    )
    return 1 if failed_ids else 0


if __name__ == "__main__":
    sys.exit(main())
