#!/usr/bin/env python3
"""Keep the ``.claude/`` and ``.agents/`` team-knowledge trees in sync.

Sibling of ``sync_agents_md.py`` (which twins CLAUDE.md and AGENTS.md), same
idea one level up: the versioned agent knowledge under ``.claude/`` — the
``agents/``, ``commands/`` and ``skills/`` subtrees — is addressed to EVERY
coding agent, not just Claude Code. ``.agents/`` is the tool-neutral twin
other agents (Codex, Gemini CLI, ...) can read without knowing anything about
Claude. ``.claude/`` is canonical; edit either side and the other follows.

This script is the single sync engine, used from three places:

  * ``.githooks/pre-commit`` (with ``--stage``)  -- the hard guarantee: every
    commit lands both trees in sync, regardless of who edited them.
  * the Claude Code ``PostToolUse`` hook          -- live mirroring while an
    edit happens, so the working tree is already in sync before any commit.
  * ``--check`` in CI / manual verification       -- exit non-zero on drift,
    change nothing.

Per file pair (same relative path in both trees) the decision mirrors the
MD engine exactly:

  * same content                       -> nothing to do.
  * exactly one side changed vs HEAD   -> the other side follows (creation,
                                          rewrite, and deletion all propagate).
  * both changed vs HEAD and differ    -> genuine conflict: that pair is left
                                          untouched and reported, exit 2.
                                          Clean pairs are still synced.
  * neither changed vs HEAD but differ -> pre-existing drift: ``.claude/`` is
                                          the canonical tie-breaker — except
                                          when one side simply does not exist
                                          yet (no working copy, no HEAD
                                          baseline): then the existing side is
                                          the source, so a brand-new file on
                                          either side is never destroyed.

Privacy guard: any path that git ignores on EITHER side (e.g. the private
``.claude/skills/security-github/``) is excluded from the mirror entirely —
the sync must never copy a deliberately-untracked file into a tracked tree.

``--stage`` additionally re-stages both members of every pair that already has
staged changes on at least one side, so a commit can never carry a half-synced
pair (the MD engine gets this for free by always adding both files).

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

# Exit codes: 0 = in sync (or synced), 1 = drift found in --check mode,
# 2 = unresolvable conflict (both sides edited differently), 3 = setup error.
EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_CONFLICT = 2
EXIT_SETUP = 3


def _git(*args: str, repo: Path, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, input=stdin)


def _repo_root() -> Path | None:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
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


def _in_head(posix_path: str, repo: Path) -> bool:
    return _git("cat-file", "-e", f"HEAD:{posix_path}", repo=repo).returncode == 0


def _changed_vs_head(posix_path: str, repo: Path, working: bytes | None) -> bool:
    """Has this path's content changed vs its committed HEAD version?

    "changed" means "present in HEAD AND its text differs from the HEAD blob"
    (a deleted-in-worktree tracked file therefore counts as changed). A path
    with no HEAD version has no committed baseline to have diverged from, so
    it is never "changed" — same rule as the MD engine.
    """
    show = _git("cat-file", "-p", f"HEAD:{posix_path}", repo=repo)
    if show.returncode != 0:
        return False  # not in HEAD -> no committed baseline
    return _norm(working) != _norm(show.stdout)


def _collect_relpaths(repo: Path) -> set[str]:
    """Union of file relpaths (posix, relative to the tree root) in scope.

    Includes index-tracked paths as well as on-disk files, so a pair whose
    working copies were BOTH already deleted still gets its staged deletion
    completed on the twin side.
    """
    rels: set[str] = set()
    scope = [f"{root}/{sub}" for root in (CLAUDE_ROOT, AGENTS_ROOT) for sub in SYNC_SUBDIRS]
    for root_name in (CLAUDE_ROOT, AGENTS_ROOT):
        for sub in SYNC_SUBDIRS:
            base = repo / root_name / sub
            if not base.is_dir():
                continue
            for f in base.rglob("*"):
                if f.is_file():
                    rels.add(f.relative_to(repo / root_name).as_posix())
    tracked = _git("ls-files", "-z", "--", *scope, repo=repo)
    if tracked.returncode == 0:
        for raw in tracked.stdout.split(b"\x00"):
            if not raw:
                continue
            path = raw.decode("utf-8")
            for root_name in (CLAUDE_ROOT, AGENTS_ROOT):
                prefix = f"{root_name}/"
                if path.startswith(prefix):
                    rels.add(path[len(prefix):])
    return rels


def _ignored_paths(repo: Path, candidates: list[str]) -> set[str]:
    """Subset of ``candidates`` (repo-relative posix paths) that git ignores."""
    if not candidates:
        return set()
    proc = _git(
        "check-ignore", "-z", "--stdin", repo=repo,
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
    claude_changed: bool,
    agents_changed: bool,
) -> tuple[str, bytes | None] | None:
    """Return (target_root, content_to_write) or None if already in sync.

    ``content_to_write`` of None means "delete the target". Raises ValueError
    on an unresolvable conflict.
    """
    if _norm(claude) == _norm(agents):
        return None

    if claude_changed and agents_changed:
        raise ValueError("both sides were changed and now differ")

    if agents_changed and not claude_changed:
        return (CLAUDE_ROOT, agents)
    if claude_changed and not agents_changed:
        return (AGENTS_ROOT, claude)

    # Neither side changed vs HEAD. A side that does not exist at all (and,
    # being unchanged, has no HEAD baseline either) is simply not born yet:
    # the existing side is the source. Otherwise .claude/ is canonical.
    if claude is None:
        return (CLAUDE_ROOT, agents)
    return (AGENTS_ROOT, claude)


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
        "diff", "--cached", "--name-only", "-z", "--",
        CLAUDE_ROOT, AGENTS_ROOT, repo=repo,
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

    conflicts: list[str] = []
    drifted: list[tuple[str, str, bytes | None]] = []  # (rel, target_root, content)

    for rel in rels:
        claude_posix = f"{CLAUDE_ROOT}/{rel}"
        agents_posix = f"{AGENTS_ROOT}/{rel}"
        if claude_posix in ignored or agents_posix in ignored:
            continue  # private / deliberately-untracked: never mirrored

        claude = _read_bytes(repo / CLAUDE_ROOT / rel)
        agents = _read_bytes(repo / AGENTS_ROOT / rel)
        try:
            decision = _decide_source(
                claude,
                agents,
                _changed_vs_head(claude_posix, repo, claude),
                _changed_vs_head(agents_posix, repo, agents),
            )
        except ValueError:
            conflicts.append(rel)
            continue
        if decision is not None:
            target_root, content = decision
            drifted.append((rel, target_root, content))

    if args.check:
        for rel, target_root, _content in drifted:
            sys.stderr.write(
                f"sync_agents_dir: DRIFT -- {rel} (would rewrite {target_root}/{rel}).\n"
            )
        for rel in conflicts:
            sys.stderr.write(f"sync_agents_dir: CONFLICT -- {rel} (both sides edited).\n")
        if conflicts:
            return EXIT_CONFLICT
        if drifted:
            return EXIT_DRIFT
        if not args.quiet:
            print("sync_agents_dir: .claude/ and .agents/ already in sync.")
        return EXIT_OK

    for rel, target_root, content in drifted:
        _write_target(repo, target_root, rel, content)
        verb = "deleted" if content is None else "rewrote"
        print(f"sync_agents_dir: {verb} {target_root}/{rel} to match its twin.")

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

    if conflicts:
        for rel in conflicts:
            sys.stderr.write(
                f"sync_agents_dir: CONFLICT -- {rel} was changed on both sides and "
                "now differs; reconcile by hand, then retry.\n"
            )
        return EXIT_CONFLICT

    if not drifted and not args.quiet:
        print("sync_agents_dir: .claude/ and .agents/ already in sync.")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
