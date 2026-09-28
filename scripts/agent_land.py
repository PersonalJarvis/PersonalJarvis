#!/usr/bin/env python3
"""Land a coding agent's finished branch: rebase, self-check, push.

One command for every agent (Claude Code, Codex, Gemini CLI, ...) working in
its own worktree or clone::

    python scripts/agent_land.py            # on a feature branch -> PR, auto-merge
    python scripts/agent_land.py --direct   # fast-forward main itself

What it does, in order — and it stops at the first real problem:

1. **Rebase** the branch onto the latest ``<remote>/main``. Generated files
   (frontend dist, agent mirrors, reference docs, lockfiles) and append-only
   files (CHANGELOG) are resolved automatically; anything else is listed for
   the agent to resolve (or handed to ``--resolver-cmd``).
2. **Static gates** — ``scripts/ci/run_gates.py``, the same set CI runs.
3. **Relevant tests** — ``scripts/ci/select_tests.py`` picks the test files
   the diff can affect and ``scripts/ci/run_tests_parallel.py`` runs them in
   isolated processes. The full suite runs in CI.
4. **Push** — a feature branch gets a pull request labelled ``auto-merge``;
   the merge train keeps it current and lands it once the CI gate is green.

It never stashes, never force-pushes, and refuses a dirty worktree: the main
checkout is shared with other sessions, so this belongs in your own worktree.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.ci import agent_integrate  # noqa: E402
from scripts.ci.select_tests import SMOKE  # noqa: E402

PY = sys.executable


def run(cmd: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def step(title: str) -> None:
    print(f"\n== {title}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--direct", action="store_true", help="Push straight to main.")
    parser.add_argument("--pr", action="store_true", help="Push the branch and open a PR.")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-gates", action="store_true")
    parser.add_argument("--no-push", action="store_true", help="Check only.")
    parser.add_argument(
        "--resolver-cmd",
        default=os.environ.get("CONFLICT_RESOLVER_CMD", ""),
        help="Command that edits conflicted files in place (prompt on stdin).",
    )
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], check=True).stdout.strip()
    direct = args.direct or (branch == "main" and not args.pr)
    dirty = run(["git", "status", "--porcelain", "--untracked-files=no"]).stdout.strip()
    if dirty:
        print("Refusing to land: the worktree has uncommitted changes to tracked files.")
        print("Commit your own paths first (git commit -- <paths>); never stash here.")
        return 2
    upstream = f"{args.remote}/main"

    step(f"1/4 rebase {branch} onto {upstream}")
    if run(["git", "fetch", "--quiet", args.remote, "main"]).returncode != 0:
        print(f"Could not fetch {upstream}.")
        return 2
    report = agent_integrate.update(upstream, "rebase", args.resolver_cmd, REPO_ROOT)
    print(report)
    if report["status"] == "conflict":
        print("\nThese files conflict and need a human-quality resolution:")
        for path in report.get("unresolved", []):  # type: ignore[union-attr]
            print(f"  - {path}")
        print(
            f"\nRun `git rebase {upstream}`, resolve them (keep both sides' intent), "
            "`git add` them, `git rebase --continue`, then run this script again."
        )
        return 3

    if not args.skip_gates:
        step("2/4 static gates")
        gates = subprocess.run(  # noqa: S603
            [PY, "scripts/ci/run_gates.py", "--base", upstream, "--pr"], cwd=REPO_ROOT, check=False
        )
        if gates.returncode != 0:
            print("A static gate failed - fix it before landing.")
            return 4

    if not args.skip_tests:
        step("3/4 relevant tests")
        changed = run(["git", "diff", "--name-only", f"{upstream}...HEAD"]).stdout
        with tempfile.TemporaryDirectory(prefix="agent-land-") as tmp:
            changed_file = Path(tmp) / "changed.txt"
            selected = Path(tmp) / "selected.txt"
            changed_file.write_text(changed, encoding="utf-8")
            sel = run(
                [
                    PY,
                    "scripts/ci/select_tests.py",
                    "--changed-from",
                    str(changed_file),
                    "--out",
                    str(selected),
                ]
            )
            print(sel.stdout.strip())
            files = [x for x in selected.read_text(encoding="utf-8").splitlines() if x]
            if "mode=all" in sel.stdout:
                print("The change reaches the whole suite; running the contract guards here.")
                files = list(SMOKE)
                selected.write_text("\n".join(files) + "\n", encoding="utf-8")
            if files:
                tests = subprocess.run(  # noqa: S603
                    [
                        PY,
                        "scripts/ci/run_tests_parallel.py",
                        "--files-from",
                        str(selected),
                        "--report",
                        str(Path(tmp) / "report.json"),
                        "--junit-out",
                        str(Path(tmp) / "report.xml"),
                    ],
                    cwd=REPO_ROOT,
                    check=False,
                )
                if tests.returncode != 0:
                    print(
                        "Relevant tests failed. If a failure is pre-existing on main, it is in "
                        "scripts/ci/test-baseline-*.json and CI will not block on it."
                    )
                    return 5
            else:
                print("No test can see this change.")

    if args.no_push:
        print("\nChecks passed; --no-push given, nothing pushed.")
        return 0
    step("4/4 push")
    if direct:
        push = run(["git", "push", args.remote, "HEAD:main"])
        print(push.stdout + push.stderr)
        if push.returncode != 0:
            print("main moved while checking - run this script again (it rebases first).")
            return 6
        return 0
    push = run(["git", "push", "--set-upstream", args.remote, f"HEAD:refs/heads/{branch}"])
    print(push.stdout + push.stderr)
    if push.returncode != 0:
        print("Push rejected. Never force it: pull, re-run this script.")
        return 6
    existing = run(["gh", "pr", "view", branch, "--json", "number", "-q", ".number"])
    if existing.returncode == 0 and existing.stdout.strip():
        print(f"PR #{existing.stdout.strip()} updated; the merge train lands it when green.")
        return 0
    created = run(["gh", "pr", "create", "--fill", "--base", "main", "--label", "auto-merge"])
    print(created.stdout + created.stderr)
    return 0 if created.returncode == 0 else 7


if __name__ == "__main__":
    sys.exit(main())
