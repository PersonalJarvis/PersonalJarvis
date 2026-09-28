#!/usr/bin/env python3
"""Admit a release tag: nothing ships that main's CI has not proven.

Every tag-triggered workflow (PyPI, desktop installers, signed installer)
runs this first through ``.github/workflows/release-gate.yml``. It checks:

1. the tag is ``vX.Y.Z`` and matches ``pyproject.toml`` and
   ``jarvis.__version__``;
2. ``CHANGELOG.md`` has a ``## [X.Y.Z]`` section;
3. the tagged commit is reachable from ``origin/main`` (no release from an
   unmerged branch);
4. the ``CI gate`` check on that exact commit concluded ``success`` — waiting
   while it is still running, because the release-cut workflow dispatches CI
   for its own version-bump commit right before it tags.

    release_admit.py --tag v2.4.0 --sha <commit> --repo OWNER/NAME [--wait-minutes 150]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_CHECK = "CI gate"


def versions(root: Path = REPO_ROOT) -> tuple[str, str]:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    init = (root / "jarvis" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__ = "([^"]+)"', init)
    return project["project"]["version"], match.group(1) if match else ""


def changelog_has(version: str, root: Path = REPO_ROOT) -> bool:
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    return re.search(rf"^## \[{re.escape(version)}\]", text, re.MULTILINE) is not None


def check_identity(tag: str, root: Path = REPO_ROOT) -> list[str]:
    errors: list[str] = []
    match = re.fullmatch(r"v(\d+\.\d+\.\d+)", tag)
    if not match:
        return [f"tag {tag!r} is not vX.Y.Z"]
    version = match.group(1)
    project, package = versions(root)
    if not (version == project == package):
        errors.append(f"tag {version} != pyproject {project} / jarvis.__version__ {package}")
    if not changelog_has(version, root):
        errors.append(f"CHANGELOG.md has no '## [{version}]' section")
    return errors


def gate_state(repo: str, sha: str) -> str:
    proc = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "gh",
            "api",
            f"repos/{repo}/commits/{sha}/check-runs?check_name=CI%20gate&per_page=50",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if proc.returncode != 0:
        print(f"[admit] check-run lookup failed: {proc.stderr.strip()[:300]}")
        return "missing"
    runs = json.loads(proc.stdout or "{}").get("check_runs", [])
    if not runs:
        return "missing"
    if any(r.get("status") == "completed" and r.get("conclusion") == "success" for r in runs):
        return "success"
    if any(r.get("status") != "completed" for r in runs):
        return "pending"
    return "failure"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--wait-minutes", type=int, default=150)
    parser.add_argument("--skip-ci", action="store_true", help="Identity checks only.")
    args = parser.parse_args(argv)

    errors = check_identity(args.tag)
    subprocess.run(["git", "fetch", "--quiet", "origin", "main"], check=False)  # noqa: S607
    ancestor = subprocess.run(  # noqa: S603
        ["git", "merge-base", "--is-ancestor", args.sha, "origin/main"],  # noqa: S607
        check=False,
    )
    if ancestor.returncode != 0:
        errors.append(f"{args.sha[:12]} is not on origin/main")
    for error in errors:
        print(f"::error title=Release not admitted::{error}")
    if errors:
        return 1
    if args.skip_ci:
        print("[admit] identity OK; CI check skipped on request")
        return 0

    deadline = time.monotonic() + args.wait_minutes * 60
    missing_since = time.monotonic()
    while True:
        state = gate_state(args.repo, args.sha)
        print(f"[admit] CI gate on {args.sha[:12]}: {state}", flush=True)
        if state == "success":
            print(f"[admit] {args.tag} admitted")
            return 0
        if state == "failure":
            print(f"::error::CI gate failed on {args.sha[:12]}; fix main and cut a new tag.")
            return 1
        if state == "missing" and time.monotonic() - missing_since > 15 * 60:
            print(
                f"::error::No CI run exists for {args.sha[:12]}. Run "
                f"`gh workflow run ci.yml --ref main`, then re-run this workflow."
            )
            return 1
        if state == "pending":
            missing_since = time.monotonic()
        if time.monotonic() > deadline:
            print("::error::Timed out waiting for the CI gate.")
            return 1
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
