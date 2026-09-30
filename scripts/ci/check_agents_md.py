#!/usr/bin/env python3
"""Keep ``AGENTS.md`` the repository's only agent-instructions file.

Claude Code, Codex and Gemini CLI all read ``AGENTS.md``. Claude Code reads it
only while no ``CLAUDE.md``, ``.claude/CLAUDE.md`` or ``CLAUDE.local.md`` sits in
the working directory or above it: one committed anywhere in the tree would
silently replace the whole rulebook for every Claude session started there.

This gate fails when the root ``AGENTS.md`` is not tracked or when any
``CLAUDE.md`` / ``CLAUDE.local.md`` is, outside the standalone side-projects
that merely co-live in this repo and keep their own rulebook. It reads the
index, so the pre-commit hook blocks a staged file before it lands, and CI
blocks one that got past.

Used from ``.githooks/pre-commit`` and ``scripts/ci/run_gates.py``.

stdlib-only; resolves the repo root via ``git rev-parse`` so it works in linked
worktrees too.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path, PurePosixPath

AGENTS_NAME = "AGENTS.md"
FORBIDDEN_NAMES = frozenset({"CLAUDE.md", "CLAUDE.local.md"})
# Independent projects with their own rulebook (see the side-project block in
# privacy_gate/references/distribution-denylist.txt). A Jarvis session never
# starts inside them, so their CLAUDE.md cannot hide this repo's AGENTS.md.
SIDE_PROJECTS = ("skillbook/",)

# Exit codes: 0 = clean, 1 = forbidden file tracked, 3 = setup error.
EXIT_OK = 0
EXIT_FORBIDDEN = 1
EXIT_SETUP = 3


def _repo_root() -> Path | None:
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip())


def _tracked(repo: Path) -> list[str] | None:
    proc = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True)
    if proc.returncode != 0:
        return None
    return [p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print nothing on the success path.",
    )
    args = parser.parse_args(argv)

    repo = _repo_root()
    tracked = _tracked(repo) if repo is not None else None
    if tracked is None:
        sys.stderr.write("check_agents_md: not inside a git repo (or git missing).\n")
        return EXIT_SETUP

    if AGENTS_NAME not in tracked:
        sys.stderr.write(f"check_agents_md: {AGENTS_NAME} is not tracked at the repo root.\n")
        return EXIT_SETUP

    forbidden = sorted(
        p
        for p in tracked
        if PurePosixPath(p).name in FORBIDDEN_NAMES and not p.startswith(SIDE_PROJECTS)
    )
    if forbidden:
        sys.stderr.write(
            "check_agents_md: AGENTS.md is the only agent-instructions file. Claude Code "
            "reads a CLAUDE.md INSTEAD of AGENTS.md, so these would hide the rulebook:\n"
        )
        for path in forbidden:
            sys.stderr.write(f"  {path}\n")
        sys.stderr.write("Move their content into the AGENTS.md beside them and remove them.\n")
        return EXIT_FORBIDDEN

    if not args.quiet:
        print("check_agents_md: AGENTS.md is the only agent-instructions file.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
