#!/usr/bin/env python3
"""Sweep local scratch out of the working tree.

Two modes, both dry-run unless ``--delete`` is passed:

``--root-media`` (default)
    The fast path, safe to wire into a session hook: loose captures and scratch
    notes sitting directly in the repository root. It touches only the root
    directory itself, so it stays fast on a tree with 200k files.

``--all``
    Adds the heavy directories — render output, tool caches, dead build trees.
    Meant to be run by hand every few weeks, not on a hook.

Safety rules, enforced at run time rather than assumed from the lists below:

* a file is removed only when ``git check-ignore`` already claims it, so a
  tracked or newly-authored file can never be swept;
* a directory is removed only when ``git ls-files`` reports nothing tracked
  inside it;
* ``--older-than-hours`` keeps recent files, so an image handed to a coding
  agent minutes ago survives the sweep it might otherwise trigger;
* nothing outside the repository root is reachable.

Runs on Windows, macOS and Linux.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

#: Captures that pile up in the root when an image is handed to a coding agent.
MEDIA_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".bmp", ".mp4", ".mov", ".webm", ".avi",
}

#: Loose scratch files that belong to a tool run, not to the project.
ROOT_FILE_GLOBS = [
    ".tmp-*.log",
    "scratch_*.py",
    "scratch_*.log",
    "scratch_*.txt",
    "_tmp_*.py",
    "voice-session-*",
    "Unbenanntes Dokument*",  # i18n-allow: literal name a German-locale editor makes
    "STARTUP_*.md",
    "computer-use-*.md",
    "progress.md",
    "progress-*.md",
    "progress_*.md",
    "boot-latest.json",
    "boot-baseline.json",
    "desktop-boot-*.json",
    "ci-repro-report.xml",
    "test_output.txt",
]

#: Build output, render output and tool caches. Only swept with --all.
HEAVY_DIRS = [
    "video/out",
    "video/reel",
    "video/node_modules",
    # wiki-video/ and video/ are TRACKED Remotion source projects; only their
    # install and render output is disposable.
    "wiki-video/node_modules",
    "wiki-video/out",
    "outputs",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".hypothesis",
    ".tmp_video_analysis",
    ".tmp_pytest",
    ".tmp_research",
    ".tmp_anim_preview",
    ".tmp",
    ".audit-tmp",
    ".bridge",
    ".bridgespace",
    ".boot-bench",
    ".playwright-mcp",
]

#: Live config and data a glob above might otherwise match.
NEVER_TOUCH = {"jarvis.toml", "usage.db", "jarvis.toml.example", "mcp.json"}


def human(num_bytes: int) -> str:
    """Format a byte count for the report."""
    mb = num_bytes / (1024 * 1024)
    return f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.1f} MB"


def ignored_paths(root: Path, candidates: list[Path]) -> set[Path]:
    """Return the subset of *candidates* that git's ignore rules already claim.

    Paths go in as batched positional arguments, deliberately NOT through
    ``check-ignore --stdin``: that form was measured returning empty or partial
    results on Windows for paths the per-path call reports as ignored. Silent
    under-reporting here would leave scratch behind on every run.
    """
    hits: set[Path] = set()
    for start in range(0, len(candidates), 150):
        rels = [
            str(p.relative_to(root)).replace("\\", "/")
            for p in candidates[start:start + 150]
        ]
        try:
            done = subprocess.run(
                ["git", "-c", "core.quotePath=false", "check-ignore", "--", *rels],
                cwd=root, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
        except OSError as exc:
            print(f"  ! git check-ignore failed: {exc}", file=sys.stderr)
            return hits
        for line in done.stdout.splitlines():
            if line.strip():
                hits.add((root / line.strip()).resolve())
    return hits


def has_tracked_files(root: Path, rel: str) -> bool:
    """True when git tracks anything under *rel* — then it is not ours to sweep."""
    try:
        done = subprocess.run(
            ["git", "ls-files", "--", rel], cwd=root, capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
    except OSError:
        return True  # cannot prove it is safe, so treat it as tracked
    return bool(done.stdout.strip())


def dir_size(path: Path) -> int:
    """Total bytes under *path*, skipping entries that cannot be read."""
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
        except OSError:
            continue  # vanished or locked mid-walk; not an error for an estimate
    return total


def collect_root_files(root: Path, min_age_hours: float) -> list[tuple[Path, int]]:
    """Loose, ignored, sufficiently old scratch files sitting in the root."""
    cutoff = time.time() - min_age_hours * 3600
    candidates: list[Path] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_file() or entry.name in NEVER_TOUCH:
            continue
        try:
            if entry.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        if entry.suffix.lower() in MEDIA_SUFFIXES or any(
            entry.match(pattern) for pattern in ROOT_FILE_GLOBS
        ):
            candidates.append(entry)

    claimed = ignored_paths(root, candidates)
    found = []
    for entry in candidates:
        if entry.resolve() in claimed:
            try:
                found.append((entry, entry.stat().st_size))
            except OSError:
                continue
    return found


def collect_heavy_dirs(root: Path) -> list[tuple[Path, int]]:
    """Disposable directories that hold nothing git tracks."""
    found = []
    for rel in HEAVY_DIRS:
        path = root / rel
        if not path.is_dir() or has_tracked_files(root, rel):
            continue
        found.append((path, dir_size(path)))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root (default: .)")
    parser.add_argument("--all", action="store_true", help="also sweep heavy directories")
    parser.add_argument("--delete", action="store_true", help="actually remove")
    parser.add_argument(
        "--older-than-hours", type=float, default=24.0,
        help="keep root files newer than this (default: 24)",
    )
    parser.add_argument("--quiet", action="store_true", help="one summary line, hook use")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not (root / ".git").exists():
        if not args.quiet:
            print(f"not a git repository: {root}", file=sys.stderr)
        return 0 if args.quiet else 2

    files = collect_root_files(root, args.older_than_hours)
    dirs = collect_heavy_dirs(root) if args.all else []
    total = sum(size for _, size in files) + sum(size for _, size in dirs)

    if not files and not dirs:
        if not args.quiet:
            print("Nothing to sweep.")
        return 0

    if not args.quiet:
        for path, size in sorted(dirs, key=lambda r: -r[1]):
            print(f"  {human(size):>10}  {path.relative_to(root)}/")
        for path, size in sorted(files, key=lambda r: -r[1])[:20]:
            print(f"  {human(size):>10}  {path.name}")
        if len(files) > 20:
            print(f"  ... and {len(files) - 20} more files")

    if not args.delete:
        if not args.quiet:
            print(f"\nDry run — {human(total)} would be freed. Add --delete to sweep.")
        return 0

    for path, _ in dirs:
        shutil.rmtree(path, ignore_errors=True)
    removed = 0
    for path, _ in files:
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            if not args.quiet:
                print(f"  ! {path.name}: {exc}", file=sys.stderr)

    label = f"{removed} file(s)" + (f" + {len(dirs)} dir(s)" if dirs else "")
    print(f"cleanup_scratch: swept {label}, {human(total)} freed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
