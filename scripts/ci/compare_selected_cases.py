"""Diagnostic-only execution of selected test cases using the target's runner."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("selection", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("side")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    selected = json.loads(args.selection.read_text("utf-8"))
    assert selected
    for filename, cases in selected.items():
        assert filename.startswith("tests/") and filename.endswith(".py")
        assert (root / filename).resolve().is_relative_to(root)
        assert (root / filename).is_file()
        assert cases and all(case == filename or case.startswith(filename + "::") for case in cases)
    if args.validate_only:
        print(f"Validated {len(selected)} files / {sum(map(len, selected.values()))} selections")
        return
    sys.path.insert(0, str(root))
    from scripts.ci.run_tests_parallel import DEFAULT_MARKERS, Runner, merge_junit

    args.output.mkdir(parents=True, exist_ok=True)
    work = args.output / f"logs-{args.side}"
    work.mkdir()
    runner = Runner(
        argparse.Namespace(markers=DEFAULT_MARKERS, pytest_args="--continue-on-collection-errors -p preview_completion_stress"),
        {},
        work,
    )
    # Each file gets a fresh process and the same bounded test environment.
    # There are no retries: divergent first-attempt failures remain visible.
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda cases: runner.run(cases, 180), selected.values()))
    counts = {"tests": 0, "passed": 0, "failed": 0, "skipped": 0}
    for result in results:
        for key in counts:
            counts[key] += result.counts.get(key, 0)
    report = {
        "counts": counts,
        "failed_ids": sorted({test for result in results for test in result.failed_ids}),
        "timeouts": [result.files for result in results if result.timed_out],
        "exits": [result.exit_code for result in results],
    }
    (args.output / f"test-report-{args.side}.json").write_text(json.dumps(report, indent=2))
    merge_junit(
        [result.junit for result in results if result.junit is not None],
        args.output / f"junit-{args.side}.xml",
    )
    print(json.dumps({"side": args.side, "counts": counts, "timeouts": len(report["timeouts"])}))


if __name__ == "__main__":
    main()
