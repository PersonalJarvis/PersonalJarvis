#!/usr/bin/env python3
"""Install the repo's local git hooks (idempotent, additive).

Right now this installs ONE guard: a `pre-push` block that hard-blocks a raw push
from the working repo to the public flagship `PersonalJarvis/PersonalJarvis`
(see `scripts/ci/guard_no_raw_public_push.py` + CLAUDE.md "THE GitHub repository").

Run once per fresh clone / worktree:

    python scripts/ci/install_git_hooks.py

It is **additive and idempotent**: if a `pre-push` hook already exists (e.g. the
language-policy gate), the guard block is inserted right after the shebang and the
rest of the hook is left untouched. Re-running is a no-op. `.git/hooks/` is not
version-controlled, so this installer (which IS tracked) is how the guard travels
with the codebase.

stdlib-only; resolves the hooks dir via `git rev-parse` so it works in linked
worktrees too.
"""
from __future__ import annotations

import os
import subprocess
import sys

MARKER_BEGIN = "# >>> guard_no_raw_public_push (managed by scripts/ci/install_git_hooks.py) >>>"
MARKER_END = "# <<< guard_no_raw_public_push <<<"

GUARD_BLOCK = f"""{MARKER_BEGIN}
# Hard-block a raw push to the PUBLIC flagship repo PersonalJarvis/PersonalJarvis.
# That repo only ever receives a depersonalized snapshot via the
# ship-public-release skill (CLAUDE.md rule 2). git passes: pre-push <name> <url>.
__pj_guard="scripts/ci/guard_no_raw_public_push.py"
if [ -f "$__pj_guard" ]; then
    __pj_py="/c/Program Files/Python311/python.exe"
    [ -x "$__pj_py" ] || __pj_py="$(command -v python3 || command -v python)"
    if [ -n "$__pj_py" ]; then
        "$__pj_py" "$__pj_guard" "$1" "$2" </dev/null || exit 1
    fi
fi
{MARKER_END}
"""

NEW_HOOK = "#!/bin/sh\n" + GUARD_BLOCK + "exit 0\n"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True,
    ).stdout.strip()


def _ensure_hookspath() -> None:
    """If the repo ships a tracked ``.githooks/`` dir, point git at it.

    The repo keeps its hooks under version control in ``.githooks/`` (the language
    gate + this guard), but ``core.hooksPath`` is a per-clone setting that a fresh
    clone does not have. Set it so the tracked hooks actually run.
    """
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


def main() -> int:
    try:
        _ensure_hookspath()
        hooks_dir = _hooks_path()
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.stderr.write("install_git_hooks: not inside a git repo (or git missing).\n")
        return 1

    os.makedirs(hooks_dir, exist_ok=True)
    pre_push = os.path.join(hooks_dir, "pre-push")

    if os.path.exists(pre_push):
        with open(pre_push, encoding="utf-8") as fh:
            content = fh.read()
        if MARKER_BEGIN in content:
            print(f"pre-push: public-push guard already installed ({pre_push}).")
            return 0
        lines = content.splitlines(keepends=True)
        if lines and lines[0].startswith("#!"):
            new = lines[0] + GUARD_BLOCK + "".join(lines[1:])
        else:
            new = "#!/bin/sh\n" + GUARD_BLOCK + content
        action = "extended existing"
    else:
        new = NEW_HOOK
        action = "created"

    with open(pre_push, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new)
    try:
        os.chmod(pre_push, 0o755)  # noqa: S103 - a git hook must be executable
    except OSError:
        pass  # Windows / filesystems without exec bit - git for Windows runs it anyway.

    print(f"pre-push: {action} hook with the public-push guard ({pre_push}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
