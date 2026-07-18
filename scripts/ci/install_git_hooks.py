#!/usr/bin/env python3
"""Wire up the repo's tracked git hooks (idempotent).

Since 2026-07-18 the repo uses the standard shared-history workflow
(CLAUDE.md rule 2): direct pushes to the public flagship repo are the normal
flow, protected by the fail-closed credential gates inside the tracked
``.githooks/pre-push`` hook (secret scan, private-key gate) plus GitHub-side
secret scanning with push protection. The old raw-push hard-block
(``guard_no_raw_public_push``) is retired.

Run once per fresh clone / worktree:

    python scripts/ci/install_git_hooks.py

It does two things, both idempotent:

1. Points ``core.hooksPath`` at the tracked ``.githooks/`` dir so the
   versioned hooks actually run (a per-clone setting a fresh clone lacks).
2. Removes a previously injected ``guard_no_raw_public_push`` block from a
   legacy ``.git/hooks/pre-push``, so old clones stop hard-blocking the now
   normal public push.

stdlib-only; resolves the hooks dir via ``git rev-parse`` so it works in
linked worktrees too.
"""
from __future__ import annotations

import os
import subprocess
import sys

MARKER_BEGIN = "# >>> guard_no_raw_public_push (managed by scripts/ci/install_git_hooks.py) >>>"
MARKER_END = "# <<< guard_no_raw_public_push <<<"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True,
    ).stdout.strip()


def _ensure_hookspath() -> None:
    """If the repo ships a tracked ``.githooks/`` dir, point git at it."""
    tracked = _git("ls-files", ".githooks/")
    if not tracked:
        return  # repo doesn't use a tracked hooks dir → leave default .git/hooks
    current = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"], capture_output=True, text=True,
    ).stdout.strip()
    if current != ".githooks":
        _git("config", "core.hooksPath", ".githooks")
        print("core.hooksPath set to .githooks (tracked hooks now active).")


def _hooks_path() -> str:
    out = _git("rev-parse", "--git-path", "hooks")
    # `--git-path` is relative to the repo root; resolve against the toplevel.
    top = _git("rev-parse", "--show-toplevel")
    return out if os.path.isabs(out) else os.path.join(top, out)


def _strip_legacy_guard(pre_push: str) -> None:
    """Remove the retired raw-push guard block from a legacy hook file."""
    if not os.path.exists(pre_push):
        return
    with open(pre_push, encoding="utf-8") as fh:
        content = fh.read()
    if MARKER_BEGIN not in content:
        return
    start = content.index(MARKER_BEGIN)
    end = content.index(MARKER_END)
    if end < start:
        return  # malformed markers → leave the file alone
    end += len(MARKER_END)
    # Also swallow the trailing newline of the removed block, if present.
    if end < len(content) and content[end] == "\n":
        end += 1
    with open(pre_push, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content[:start] + content[end:])
    print(f"pre-push: removed the retired raw-push guard block ({pre_push}).")


def main() -> int:
    try:
        _ensure_hookspath()
        hooks_dir = _hooks_path()
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.stderr.write("install_git_hooks: not inside a git repo (or git missing).\n")
        return 1

    _strip_legacy_guard(os.path.join(hooks_dir, "pre-push"))
    print("git hooks wired (tracked .githooks/ active; no injected blocks).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
