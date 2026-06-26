#!/usr/bin/env python3
"""Keep ``AGENTS.md`` and ``CLAUDE.md`` carrying the same content.

The two files are intentional twins: ``CLAUDE.md`` is the long-standing agent
contract for this repo, and ``AGENTS.md`` is the cross-tool standard name that
other coding agents look for. The maintainer wants them to always hold the
*exact same content* -- edit one and the other follows, in either direction.

This script is the single sync engine, used from three places:

  * ``.githooks/pre-commit`` (with ``--stage``)  -- the hard guarantee: every
    commit lands both files in sync, regardless of who edited them (Claude, the
    maintainer, a parallel session, a plain editor).
  * the Claude Code ``PostToolUse`` hook            -- live mirroring while an
    edit happens, so the working tree is already in sync before any commit.
  * ``--check`` in CI / manual verification         -- exit non-zero on drift,
    change nothing.

Sync direction ("and the other way around"):

  * same content                       -> nothing to do.
  * exactly one changed vs HEAD        -> the unchanged file is rewritten to
                                          match the changed one (true bidirec-
                                          tional follow).
  * both changed vs HEAD and differ    -> genuine conflict: STOP, change
                                          nothing, exit 2. No silent clobber.
  * neither changed vs HEAD but differ -> pre-existing drift: CLAUDE.md is the
                                          canonical tie-breaker; AGENTS.md is
                                          rewritten to match it.

Line-ending discipline (this repo runs ``core.autocrlf=true`` and CLAUDE.md is
NOT pinned in .gitattributes): "changed vs HEAD" is answered by git itself
(``git diff --quiet HEAD``), so a pure CRLF/LF skew between the working tree and
the HEAD blob never looks like an edit. The content-equality check normalises
line endings, because "the same stuff" means the same text, not the same
invisible CR bytes. When a real sync happens, the target is written with the
*exact* bytes of the source, so the two files end up byte-identical in the tree.

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

# Exit codes: 0 = in sync (or synced), 1 = drift found in --check mode,
# 2 = unresolvable conflict (both sides edited differently), 3 = setup error.
EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_CONFLICT = 2
EXIT_SETUP = 3


def _git(*args: str, repo: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True)


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
    except FileNotFoundError:
        return None


def _norm(data: bytes | None) -> bytes | None:
    """Normalise line endings so a pure CRLF/LF skew is not seen as a diff."""
    if data is None:
        return None
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _changed_vs_head(name: str, repo: Path, working: bytes | None) -> bool:
    """Has ``name``'s content changed vs its committed HEAD version?

    "changed" means "tracked AND its text differs from the HEAD blob". A file
    with no HEAD version (untracked / brand-new) has no committed baseline to
    have diverged *from*, so it is NOT "changed" -- it can therefore never be
    one half of a conflict, and CLAUDE.md (the canonical file, which does have
    history) wins instead.

    The comparison is done on line-ending-normalised content rather than via
    ``git diff``, so it is fully deterministic regardless of how the HEAD blob
    happens to be stored (this repo runs ``core.autocrlf=true`` and CLAUDE.md's
    blob still carries legacy CRLF) -- a pure CRLF/LF skew is never an "edit".
    """
    show = _git("cat-file", "-p", f"HEAD:{name}", repo=repo)
    if show.returncode != 0:
        return False  # untracked / not in HEAD -> no committed baseline
    return _norm(working) != _norm(show.stdout)


def _decide_source(
    claude: bytes | None,
    agents: bytes | None,
    claude_changed: bool,
    agents_changed: bool,
) -> tuple[str, bytes] | None:
    """Return (target_name, content_to_write) or None if already in sync.

    Raises ValueError on an unresolvable conflict.
    """
    if _norm(claude) == _norm(agents):
        return None  # same content (covers both-missing -> handled by caller)

    if claude_changed and agents_changed:
        # Both edited away from their committed state, and they differ. We must
        # not guess which wins -- that would silently destroy one side's edit.
        raise ValueError(
            "both CLAUDE.md and AGENTS.md were changed and now differ; "
            "reconcile them by hand, then re-stage."
        )

    if agents_changed and not claude_changed:
        # AGENTS.md is the freshly edited side -> CLAUDE.md follows it.
        return (CLAUDE_NAME, agents if agents is not None else b"")

    # CLAUDE.md is the edited side, OR neither changed but they drifted
    # (CLAUDE.md is the canonical tie-breaker) -> AGENTS.md follows CLAUDE.md.
    return (AGENTS_NAME, claude if claude is not None else b"")


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

    if claude is None and agents is None:
        sys.stderr.write(
            f"sync_agents_md: neither {CLAUDE_NAME} nor {AGENTS_NAME} exists.\n"
        )
        return EXIT_SETUP

    try:
        decision = _decide_source(
            claude,
            agents,
            _changed_vs_head(CLAUDE_NAME, repo, claude),
            _changed_vs_head(AGENTS_NAME, repo, agents),
        )
    except ValueError as exc:
        sys.stderr.write(f"sync_agents_md: CONFLICT -- {exc}\n")
        return EXIT_CONFLICT

    if decision is None:
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
    print(f"sync_agents_md: rewrote {target_name} to match its twin.")

    if args.stage:
        add = _git("add", "--", CLAUDE_NAME, AGENTS_NAME, repo=repo)
        if add.returncode != 0:
            sys.stderr.write(
                "sync_agents_md: 'git add' failed:\n"
                + add.stderr.decode("utf-8", "replace")
            )
            return EXIT_SETUP

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
