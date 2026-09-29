#!/usr/bin/env python3
"""Copy the shared ``AGENTS.md`` instructions to Claude's compatibility file.

``AGENTS.md`` is authoritative. ``CLAUDE.md`` remains byte-identical because
Claude Code's AGENTS.md support is conditional and older releases need it.

This script is the single sync engine, used from three places:

  * ``.githooks/pre-commit`` (with ``--stage``)  -- the hard guarantee: every
    commit lands both files in sync.
  * ``--check`` in CI / manual verification         -- exit non-zero on drift,
    change nothing.

Any difference is fixed by copying ``AGENTS.md`` to ``CLAUDE.md``. A missing
source is a setup error, not a reason to replace it with the generated copy.

Line endings are normalized for comparison because Git may check out CRLF on
Windows. When a real sync happens, exact source bytes are written to the copy.

stdlib-only; resolves the repo root via ``git rev-parse`` so it works in linked
worktrees too. Designed to be a cheap no-op on the common path (files equal).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

CLAUDE_NAME = "CLAUDE.md"
AGENTS_NAME = "AGENTS.md"

# Exit codes: 0 = in sync (or synced), 1 = drift found, 3 = setup error.
EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_SETUP = 3


def _git(*args: str, repo: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True)


def _repo_root() -> Path | None:
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip())


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _norm(data: bytes | None) -> bytes | None:
    """Normalise line endings so a pure CRLF/LF skew is not seen as a diff."""
    if data is None:
        return None
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _decide_source(
    claude: bytes | None,
    agents: bytes | None,
) -> tuple[str, bytes] | None:
    """Return the Claude copy to update, or None when it already matches."""
    if _norm(claude) == _norm(agents):
        return None
    return (CLAUDE_NAME, agents if agents is not None else b"")


def _pair_staged(repo: Path) -> bool:
    staged = _git(
        "diff",
        "--cached",
        "--name-only",
        "-z",
        "--",
        AGENTS_NAME,
        CLAUDE_NAME,
        repo=repo,
    )
    return staged.returncode == 0 and bool(staged.stdout)


def _stage_pair(repo: Path) -> bool:
    return _git("add", "--", AGENTS_NAME, CLAUDE_NAME, repo=repo).returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift without changing anything (exit 1 on drift).",
    )
    parser.add_argument(
        "--stage",
        action="store_true",
        help="after syncing, 'git add' both files (for the pre-commit hook).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print nothing on the no-op / success path.",
    )
    args = parser.parse_args(argv)

    repo = _repo_root()
    if repo is None:
        sys.stderr.write("sync_agents_md: not inside a git repo (or git missing).\n")
        return EXIT_SETUP

    claude_path = repo / CLAUDE_NAME
    agents_path = repo / AGENTS_NAME

    claude = _read_bytes(claude_path)
    agents = _read_bytes(agents_path)

    if agents is None:
        sys.stderr.write(f"sync_agents_md: missing source {AGENTS_NAME}.\n")
        return EXIT_SETUP

    decision = _decide_source(claude, agents)

    if decision is None:
        if args.stage and _pair_staged(repo) and not _stage_pair(repo):
            sys.stderr.write("sync_agents_md: 'git add' failed.\n")
            return EXIT_SETUP
        if not args.quiet:
            print(f"sync_agents_md: {CLAUDE_NAME} and {AGENTS_NAME} already in sync.")
        return EXIT_OK

    target_name, content = decision

    if args.check:
        sys.stderr.write(
            f"sync_agents_md: DRIFT -- {CLAUDE_NAME} and {AGENTS_NAME} differ "
            f"(would rewrite {target_name}).\n"
        )
        return EXIT_DRIFT

    (repo / target_name).write_bytes(content)
    if not args.quiet:
        print(f"sync_agents_md: rewrote {target_name} from {AGENTS_NAME}.")

    if args.stage and _pair_staged(repo):
        if not _stage_pair(repo):
            sys.stderr.write("sync_agents_md: 'git add' failed.\n")
            return EXIT_SETUP

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
