#!/usr/bin/env python3
"""Release-completeness gate - a release ships the ENTIRE current local state.

The recurring failure this gate exists for (CLAUDE.md §2 + §3 device-parity
triage, docs/device-parity-debugging.md): a release is cut while fixes still
sit uncommitted or unpublished on the dev box, or with a stale frontend
bundle - and every other device then runs "the new version" without them,
which reads as "Jarvis is broken on my other machine".

Checks (fail-closed; run BEFORE tagging, from the repo root):

1. **Version parity** - ``jarvis/__init__.py`` and ``pyproject.toml`` agree.
2. **Dirty tree** - every dirty file is either allowlisted volatile telemetry
   or explicitly acknowledged with ``--ack-dirty``; the excluded files are
   printed so the maintainer consciously ships without them, never silently.
   (The working tree is shared between parallel agent sessions - §9 - so
   "dirty" can be legitimate; the gate forces the exclusion to be a DECISION.)
3. **Reconcile** - the local branch is not BEHIND the public remote.
4. **Dist freshness** - no commit newer than the last ``dist/`` rebuild
   touches the frontend sources (a stale bundle ships invisible-old UI).
5. ``--verify-release`` - AFTER publishing: the latest *published* GitHub
   Release matches the local version. A pushed tag without a published
   Release updates no managed install (the in-app updater only follows
   ``releases/latest``).

Cross-platform, stdlib-only. Exit codes: 0 = gate passed, 1 = gate failed,
2 = environment/usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Tracked files that legitimately churn on every boot (telemetry stamps).
# A curated register, never a free pass - each entry names its reason.
DIRTY_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Boot/TTU telemetry stamp, rewritten by every desktop start.
        "desktop-ttu-latest.json",
    }
)

_FRONTEND_SRC = "jarvis/ui/web/frontend/src"
_FRONTEND_DIST = "jarvis/ui/web/dist"

_RELEASES_LATEST_API = "https://api.github.com/repos/{slug}/releases/latest"


def _run_git(args: list[str], *, cwd: Path) -> tuple[int, str]:
    """Run git and return ``(returncode, stdout)``; never raises on failure.

    stdout keeps its leading whitespace: ``git status --porcelain`` encodes the
    index/worktree state in the first two COLUMNS, so a ``.strip()`` here would
    eat the leading space of the first entry and shift its path parse by one.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, f"could not run git: {exc}"
    out = (proc.stdout or "").rstrip("\n")
    if proc.returncode != 0:
        # Git reports the actual failure reason (missing remote, offline, auth)
        # on STDERR — a failing check must surface it, never an empty string.
        err = (proc.stderr or "").strip()
        if err:
            out = f"{out}\n{err}".strip("\n") if out else err
    return proc.returncode, out


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested)
# --------------------------------------------------------------------------- #
def parse_dirty_paths(porcelain: str) -> list[str]:
    """Paths from ``git status --porcelain``, rename targets included."""
    paths: list[str] = []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:]
        # Renames read ``old -> new``; the NEW path is what a release would miss.
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        paths.append(entry.strip().strip('"'))
    return paths


def filter_allowlisted(paths: list[str], allowlist: frozenset[str]) -> list[str]:
    """Dirty paths that actually block the gate (allowlist removed)."""
    return [p for p in paths if p.replace("\\", "/") not in allowlist]


def read_versions(root: Path) -> tuple[str | None, str | None]:
    """``(package_version, pyproject_version)`` - ``None`` when unreadable."""
    package = pyproject = None
    try:
        text = (root / "jarvis" / "__init__.py").read_text(encoding="utf-8")
        m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
        package = m.group(1) if m else None
    except OSError:
        pass
    try:
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        pyproject = m.group(1) if m else None
    except OSError:
        pass
    return package, pyproject


def release_matches(tag: str, version: str) -> bool:
    """True iff a GitHub release tag names exactly ``version``."""
    return bool(version) and tag.strip().lstrip("vV") == version.strip()


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def check_version_parity(root: Path) -> tuple[bool, str]:
    package, pyproject = read_versions(root)
    if not package or not pyproject:
        return False, "could not read the version from jarvis/__init__.py or pyproject.toml"
    if package != pyproject:
        return False, f"version drift: jarvis/__init__.py={package} pyproject.toml={pyproject}"
    return True, f"version {package} consistent"


def check_dirty_tree(root: Path, *, ack_dirty: bool) -> tuple[bool, str]:
    rc, out = _run_git(["status", "--porcelain"], cwd=root)
    if rc != 0:
        return False, f"git status failed: {out}"
    blocking = filter_allowlisted(parse_dirty_paths(out), DIRTY_ALLOWLIST)
    if not blocking:
        return True, "working tree clean (allowlisted telemetry ignored)"
    listing = "\n    ".join(blocking)
    if ack_dirty:
        return True, (
            "shipping WITHOUT these dirty files (explicitly acknowledged via "
            f"--ack-dirty):\n    {listing}"
        )
    return False, (
        "dirty files would be missing from the release - commit them (or let "
        "their owning session commit), or re-run with --ack-dirty to ship "
        f"without them consciously:\n    {listing}"
    )


def check_not_behind_public(root: Path, *, remote: str, branch: str) -> tuple[bool, str]:
    rc, out = _run_git(["fetch", "--quiet", remote, branch], cwd=root)
    if rc != 0:
        return False, f"could not fetch {remote}/{branch} (offline?): {out}"
    rc, out = _run_git(["rev-list", "--count", f"{branch}..{remote}/{branch}"], cwd=root)
    out = out.strip()
    if rc != 0 or not out.isdigit():
        return False, f"could not compare {branch} against {remote}/{branch}: {out}"
    behind = int(out)
    if behind:
        return False, (
            f"{branch} is {behind} commit(s) BEHIND {remote}/{branch} - reconcile "
            "first (a release must never roll the public line back)"
        )
    rc, ahead = _run_git(["rev-list", "--count", f"{remote}/{branch}..{branch}"], cwd=root)
    ahead = ahead.strip()
    note = f" ({ahead} local commit(s) will publish)" if ahead.isdigit() and int(ahead) else ""
    return True, f"{branch} is not behind {remote}/{branch}{note}"


def _last_commit_ts(root: Path, path: str) -> int | None:
    rc, out = _run_git(["log", "-1", "--format=%ct", "--", path], cwd=root)
    out = out.strip()
    return int(out) if rc == 0 and out.isdigit() else None


def check_dist_freshness(root: Path) -> tuple[bool, str]:
    src_ts = _last_commit_ts(root, _FRONTEND_SRC)
    dist_ts = _last_commit_ts(root, _FRONTEND_DIST)
    if src_ts is None:
        return True, "no committed frontend sources - dist check not applicable"
    if dist_ts is None:
        return False, "frontend sources are committed but no dist/ bundle is"
    if src_ts > dist_ts:
        return False, (
            "frontend sources changed AFTER the last dist/ rebuild - run "
            "`npm run build` in jarvis/ui/web/frontend and commit the bundle"
        )
    return True, "dist/ bundle is at least as new as the frontend sources"


def check_published_release(root: Path, *, slug: str) -> tuple[bool, str]:
    package, _ = read_versions(root)
    if not package:
        return False, "could not read the local version to verify the release"
    url = _RELEASES_LATEST_API.format(slug=slug)
    request = urllib.request.Request(  # noqa: S310 - fixed https URL, no user scheme
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "PersonalJarvis-ReleaseGate",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return False, f"could not query the latest published release: {exc}"
    tag = str(data.get("tag_name") or "")
    if release_matches(tag, package):
        return True, f"published release {tag} matches local version {package}"
    return False, (
        f"latest PUBLISHED release is {tag or 'none'} but the local version is "
        f"{package} - a pushed tag without a published GitHub Release updates "
        "no managed install; publish the Release"
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--ack-dirty",
        action="store_true",
        help="ship without the listed dirty files (a conscious decision, printed)",
    )
    parser.add_argument(
        "--verify-release",
        action="store_true",
        help="post-publish mode: also verify the published GitHub Release",
    )
    parser.add_argument("--remote", default="public", help="public remote name")
    parser.add_argument("--branch", default="main", help="release branch")
    parser.add_argument(
        "--repo-slug",
        default="PersonalJarvis/PersonalJarvis",
        help="GitHub owner/name for --verify-release",
    )
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists():
        print("check_release_completeness: not a git checkout", file=sys.stderr)
        return 2

    checks: list[tuple[str, tuple[bool, str]]] = [
        ("version parity", check_version_parity(root)),
        ("dirty tree", check_dirty_tree(root, ack_dirty=args.ack_dirty)),
        (
            "reconcile with public",
            check_not_behind_public(root, remote=args.remote, branch=args.branch),
        ),
        ("dist freshness", check_dist_freshness(root)),
    ]
    if args.verify_release:
        checks.append(("published release", check_published_release(root, slug=args.repo_slug)))

    failed = False
    for name, (ok, message) in checks:
        marker = "PASS" if ok else "FAIL"
        print(f"[{marker}] {name}: {message}")
        failed = failed or not ok

    if failed:
        print(
            "\ncheck_release_completeness: gate FAILED - this release would NOT "
            "ship the entire current local state (CLAUDE.md section 2)."
        )
        return 1
    print("\ncheck_release_completeness: OK -release ships the full local state.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
