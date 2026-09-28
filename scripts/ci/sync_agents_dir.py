#!/usr/bin/env python3
"""Copy shared agent knowledge from ``.agents/`` into ``.claude/``.

Claude Code discovers project subagents and skills in ``.claude/``. The shared
source lives in ``.agents/``; generated Claude copies are never authoritative.

This script is the single sync engine, used from three places:

  * ``.githooks/pre-commit`` (with ``--stage``)  -- commits carry both trees.
  * ``--check`` in CI / manual verification       -- exit non-zero on drift,
    change nothing.

Per file pair (same relative path in both trees), the ``.agents/`` content
wins. Creation, edits, and deletion of a source file propagate to ``.claude/``.

Privacy guard: any path that git ignores on EITHER side (e.g. the private
``.claude/skills/security-github/``) is excluded from the mirror entirely —
the sync must never copy a deliberately-untracked file into a tracked tree.

``--stage`` stages both members only when a pair was already staged.

stdlib-only; resolves the repo root via ``git rev-parse`` so it works in
linked worktrees too. Cheap no-op on the common path (trees equal).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

CLAUDE_ROOT = ".claude"
AGENTS_ROOT = ".agents"
SYNC_SUBDIRS = ("agents", "commands", "skills")

# Exit codes: 0 = in sync (or synced), 1 = drift found, 3 = setup error.
EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_SETUP = 3


def _git(*args: str, repo: Path, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, input=stdin)


def _repo_root() -> Path | None:
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip())


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return None


def _norm(data: bytes | None) -> bytes | None:
    """Normalise line endings so a pure CRLF/LF skew is not seen as a diff."""
    if data is None:
        return None
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _collect_relpaths(repo: Path) -> set[str]:
    """Union of file relpaths (posix, relative to the tree root) in scope.

    Includes source files and index-tracked paths. An untracked local Claude
    definition with no shared source must never be removed by this projection.
    """
    rels: set[str] = set()
    scope = [f"{root}/{sub}" for root in (CLAUDE_ROOT, AGENTS_ROOT) for sub in SYNC_SUBDIRS]
    for sub in SYNC_SUBDIRS:
        base = repo / AGENTS_ROOT / sub
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if f.is_file():
                rels.add(f.relative_to(repo / AGENTS_ROOT).as_posix())
    tracked = _git("ls-files", "-z", "--", *scope, repo=repo)
    if tracked.returncode == 0:
        for raw in tracked.stdout.split(b"\x00"):
            if not raw:
                continue
            path = raw.decode("utf-8")
            for root_name in (CLAUDE_ROOT, AGENTS_ROOT):
                prefix = f"{root_name}/"
                if path.startswith(prefix):
                    rels.add(path[len(prefix) :])
    return rels


def _ignored_paths(repo: Path, candidates: list[str]) -> set[str]:
    """Subset of ``candidates`` (repo-relative posix paths) that git ignores."""
    if not candidates:
        return set()
    proc = _git(
        "check-ignore",
        "-z",
        "--stdin",
        repo=repo,
        stdin=b"\x00".join(c.encode("utf-8") for c in candidates) + b"\x00",
    )
    # rc 0 = some ignored, 1 = none ignored, anything else = setup trouble
    # (fail open: treat as "none ignored" is UNSAFE here, so fail closed by
    # treating every candidate as ignored — a skipped sync is recoverable, a
    # leaked private file is not).
    if proc.returncode not in (0, 1):
        return set(candidates)
    return {p.decode("utf-8") for p in proc.stdout.split(b"\x00") if p}


def _decide_source(
    claude: bytes | None,
    agents: bytes | None,
) -> tuple[str, bytes | None] | None:
    """Return the Claude copy to update, or None when it already matches."""
    if _norm(claude) == _norm(agents):
        return None
    return (CLAUDE_ROOT, agents)


def _write_target(repo: Path, target_root: str, rel: str, content: bytes | None) -> None:
    path = repo / target_root / rel
    if content is None:
        path.unlink(missing_ok=True)
        # prune now-empty dirs up to (not including) the tree root
        parent = path.parent
        stop = repo / target_root
        while parent != stop and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _staged_paths(repo: Path) -> set[str]:
    proc = _git(
        "diff",
        "--cached",
        "--name-only",
        "-z",
        "--",
        CLAUDE_ROOT,
        AGENTS_ROOT,
        repo=repo,
    )
    if proc.returncode != 0:
        return set()
    return {p.decode("utf-8") for p in proc.stdout.split(b"\x00") if p}


def _stage_pair(repo: Path, rel: str) -> bool:
    """git add both members of a pair; skip a member that exists nowhere."""
    ok = True
    for root_name in (CLAUDE_ROOT, AGENTS_ROOT):
        posix = f"{root_name}/{rel}"
        exists = (repo / root_name / rel).is_file()
        tracked = bool(_git("ls-files", "--cached", "--", posix, repo=repo).stdout.strip())
        if not exists and not tracked:
            continue  # nothing to add and no deletion to record
        if _git("add", "--", posix, repo=repo).returncode != 0:
            ok = False
    return ok


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
        help="after syncing, 'git add' affected pairs (for the pre-commit hook).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print nothing on the no-op / success path.",
    )
    args = parser.parse_args(argv)

    repo = _repo_root()
    if repo is None:
        sys.stderr.write("sync_agents_dir: not inside a git repo (or git missing).\n")
        return EXIT_SETUP

    rels = sorted(_collect_relpaths(repo))
    both_sides = [f"{root}/{rel}" for rel in rels for root in (CLAUDE_ROOT, AGENTS_ROOT)]
    ignored = _ignored_paths(repo, both_sides)

    drifted: list[tuple[str, str, bytes | None]] = []  # (rel, target_root, content)

    for rel in rels:
        claude_posix = f"{CLAUDE_ROOT}/{rel}"
        agents_posix = f"{AGENTS_ROOT}/{rel}"
        if claude_posix in ignored or agents_posix in ignored:
            continue  # private / deliberately-untracked: never mirrored

        claude = _read_bytes(repo / CLAUDE_ROOT / rel)
        agents = _read_bytes(repo / AGENTS_ROOT / rel)
        decision = _decide_source(claude, agents)
        if decision is not None:
            target_root, content = decision
            drifted.append((rel, target_root, content))

    if args.check:
        for rel, target_root, _content in drifted:
            sys.stderr.write(
                f"sync_agents_dir: DRIFT -- {rel} (would rewrite {target_root}/{rel}).\n"
            )
        if drifted:
            return EXIT_DRIFT
        if not args.quiet:
            print("sync_agents_dir: .claude/ and .agents/ already in sync.")
        return EXIT_OK

    for rel, target_root, content in drifted:
        _write_target(repo, target_root, rel, content)
        verb = "deleted" if content is None else "rewrote"
        if not args.quiet:
            print(f"sync_agents_dir: {verb} {target_root}/{rel} from .agents/.")

    if args.stage:
        # Only pairs with staged involvement are (re-)staged: a live working-
        # tree sync of an uncommitted edit must not sneak into this commit.
        staged = _staged_paths(repo)
        stage_rels = {
            rel
            for rel in rels
            if f"{CLAUDE_ROOT}/{rel}" in staged or f"{AGENTS_ROOT}/{rel}" in staged
        }
        for rel in sorted(stage_rels):
            if not _stage_pair(repo, rel):
                sys.stderr.write(f"sync_agents_dir: 'git add' failed for pair {rel}.\n")
                return EXIT_SETUP

    if not drifted and not args.quiet:
        print("sync_agents_dir: .claude/ and .agents/ already in sync.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
