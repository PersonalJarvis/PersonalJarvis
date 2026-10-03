#!/usr/bin/env python3
"""Admit a release tag: nothing ships that main's CI has not proven.

Every tag-triggered workflow (PyPI, desktop installers, signed installer)
runs this first through ``.github/workflows/release-gate.yml``. It checks:

1. the tag is ``vX.Y.Z`` and matches ``pyproject.toml`` and
   ``jarvis.__version__``;
2. ``CHANGELOG.md`` has a ``## [X.Y.Z]`` section;
3. the tagged commit is reachable from ``origin/main`` (no release from an
   unmerged branch);
4. the latest trusted CI workflow run on that exact commit concluded success,
   including ``CI gate`` and release-only qualification. Release-cut dispatches
   full CI on the immutable tag and admission waits while it is still running.

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
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: E402

GATE_CHECK = "CI gate"
QUALIFICATION_CHECK = "release qualification"
CI_WORKFLOW = ".github/workflows/ci.yml"


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


def run_command(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run pipeline tools without a console window or platform-default decoding."""
    return subprocess.run(  # noqa: S603
        args,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=check,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )


def api_pages(endpoint: str, key: str) -> list[dict[str, Any]]:
    """Read every API page; a failed lookup can never stand in for green CI."""
    proc = run_command(["gh", "api", "--paginate", "--slurp", endpoint])
    return [item for page in json.loads(proc.stdout) for item in (page[key] if key else page)]


def latest_ci_run(
    runs: list[dict[str, Any]], repo: str, sha: str, *, tag: str | None = None
) -> dict[str, Any] | None:
    trusted = [
        run
        for run in runs
        if run.get("head_sha") == sha
        and (run.get("head_repository") or {}).get("full_name") == repo
        and run.get("path", "").split("@", 1)[0] == CI_WORKFLOW
        and run.get("event") in {"push", "schedule", "workflow_dispatch"}
        and (tag is None or run.get("head_branch") == tag)
        and (
            run.get("head_branch") == "main"
            or re.fullmatch(r"v\d+\.\d+\.\d+", run.get("head_branch") or "")
        )
    ]
    return max(trusted, key=lambda run: (run["id"], run["run_attempt"]), default=None)


def gate_state(
    repo: str, sha: str, *, require_qualification: bool = False, tag: str | None = None
) -> str:
    """Check the latest attempt of this repository's CI workflow on this SHA.

    Check names alone do not establish provenance. An older green check must
    not override a newer pending/failed run or a failed rerun of the same run.
    Tag admission selects that tag's runs: a separate main run at the same SHA
    may legitimately skip release qualification and must not replace its proof.
    """
    try:
        runs = api_pages(
            f"repos/{repo}/actions/workflows/ci.yml/runs?head_sha={sha}&per_page=100",
            "workflow_runs",
        )
        run = latest_ci_run(runs, repo, sha, tag=tag)
        if run is None:
            return "missing"
        if run.get("status") != "completed":
            return "pending"
        if run.get("conclusion") != "success":
            return "failure"
        jobs = api_pages(
            f"repos/{repo}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100",
            "jobs",
        )
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"[admit] CI evidence lookup failed ({type(exc).__name__})")
        return "failure"
    required = {GATE_CHECK}
    if require_qualification:
        required.add(QUALIFICATION_CHECK)
    for name in required:
        matching = [job for job in jobs if job.get("name") == name]
        if len(matching) != 1 or matching[0].get("conclusion") != "success":
            return "failure"
    return "success"


def check_commit(tag: str, sha: str) -> list[str]:
    """Bind the checked-out bytes, tag and freshly fetched main to one commit."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        return ["invalid release tag or commit SHA"]
    try:
        run_command(["git", "fetch", "--quiet", "origin", "main:refs/remotes/origin/main"])
        head = run_command(["git", "rev-parse", "HEAD^{commit}"]).stdout.strip()
        tagged = run_command(["git", "rev-parse", f"refs/tags/{tag}^{{commit}}"]).stdout.strip()
        if head != sha or tagged != sha:
            return ["checkout, release tag and requested SHA do not identify the same commit"]
        run_command(["git", "merge-base", "--is-ancestor", sha, "origin/main"])
    except (subprocess.SubprocessError, OSError):
        return ["cannot verify release ancestry against freshly fetched origin/main"]
    return []


def wait_for_ci(
    repo: str,
    sha: str,
    wait_minutes: int,
    *,
    require_qualification: bool,
    tag: str | None = None,
) -> int:
    deadline = time.monotonic() + wait_minutes * 60
    while True:
        state = gate_state(repo, sha, require_qualification=require_qualification, tag=tag)
        print(f"[admit] CI on {sha[:12]}: {state}", flush=True)
        if state == "success":
            return 0
        if state == "failure":
            print("::error::Latest CI attempt failed or lacks required release qualification.")
            return 1
        if time.monotonic() >= deadline:
            print("::error::Timed out waiting for CI on the exact release commit.")
            return 1
        time.sleep(min(30, max(0, deadline - time.monotonic())))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag")
    parser.add_argument("--sha", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--wait-minutes", type=int, default=150)
    parser.add_argument("--skip-ci", action="store_true", help="Identity checks only.")
    parser.add_argument(
        "--ci-only", action="store_true", help="Check main before preparing a release."
    )
    args = parser.parse_args(argv)
    if args.ci_only:
        return wait_for_ci(args.repo, args.sha, args.wait_minutes, require_qualification=False)
    if not args.tag:
        parser.error("--tag is required unless --ci-only is specified")
    errors = check_identity(args.tag) + check_commit(args.tag, args.sha)
    for error in errors:
        print(f"::error title=Release not admitted::{error}")
    if errors:
        return 1
    if args.skip_ci:
        print("[admit] identity OK; CI check skipped on request")
        return 0

    return wait_for_ci(
        args.repo,
        args.sha,
        args.wait_minutes,
        require_qualification=True,
        tag=args.tag,
    )


if __name__ == "__main__":
    sys.exit(main())
