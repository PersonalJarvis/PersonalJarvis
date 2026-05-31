"""Regression guard: personal / per-user state must never be re-tracked.

This test is the open-source "ships empty" CI guard.  It runs `git ls-files`
against the working tree and FAILS if any personal or per-user path appears in
the tracked file set.  The paths below were removed from tracking in Wave 0 of
the open-source privacy-separation effort (see
Downloads/Open-Source-Privacy-Separation.md).

The exact path list is coordinated with Wave 5 (history purge via git
filter-repo) — both waves must agree on what "personal" means.  Do not modify
this list without also updating the Wave 5 plan at
<your-home>\\Downloads\\Open-Source-History-Cleanup-Plan.md.

Rationale for each category:

  * **Config instances** (`jarvis.toml`, `mcp.json`, `.env`) — per-user
    provider choices, voice IDs, device names, and API keys.  Replaced by
    `*.example` templates.  `.env` was never committed (verified via
    `git log --all -- .env` = 0 results) but is guarded here as a
    belt-and-suspenders check.

  * **Knowledge-vault session content** — auto-generated summaries of the
    owner's real work sessions; exposes a personal activity timeline.  The
    vault *skeleton* (schema.md, README.md, 00-index/, 99-templates/,
    .obsidian/ plugin + theme config) ships as a seed; personal *content*
    directories and personal UI-state files (.obsidian/graph.json,
    .obsidian/hotkeys.json) do not.

  * **Dev scratch / incident files** — pytest dumps, recovery narratives, and
    per-developer root-level text files.

  * **Root-level UI screenshots** (`*-view.png`) — captured from the running
    app during development; not source artefacts.

If this test fails it means a file from one of these categories was re-staged
(e.g. via `git add .`).  Fix: `git rm --cached <path>` and add a matching
pattern to .gitignore.  See Downloads/Open-Source-Privacy-Separation.md
Wave 0 for the full procedure.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Repository root detection
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _is_git_repo(root: Path) -> bool:
    """Return True if *root* is inside a git working tree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _tracked_files(root: Path) -> list[str]:
    """Return the list of files tracked by git, with forward-slash separators."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.skip(f"git ls-files failed: {result.stderr.strip()}")
    return result.stdout.splitlines()


# ---------------------------------------------------------------------------
# Personal-path definitions — final list reconciled with WaveFive 2026-05-31
# ---------------------------------------------------------------------------

# Exact paths (as returned by `git ls-files`, forward-slash) that must never
# appear in the tracked file set.
_BANNED_EXACT: frozenset[str] = frozenset(
    [
        # Config instances (templates ship instead; .env also gitignored)
        "jarvis.toml",
        "mcp.json",
        ".env",
        # Personal Obsidian UI state — NOT part of the seed scaffold.
        # (The other .obsidian/ files — app.json, appearance.json,
        # core-plugins.json, daily-notes.json, templates.json, and the
        # Minimal theme — are intentionally tracked as seed config.)
        "wiki/obsidian-vault/.obsidian/graph.json",
        "wiki/obsidian-vault/.obsidian/hotkeys.json",
        # Personal wiki content files
        "wiki/obsidian-vault/log.md",
        # Root-level dev scratch
        "claude-progress.txt",
        "macicon-candidates.json",
        # Personal incident reports
        "recovery-report.md",
        "restore-report.md",
        "RECOVERY.md",
        "FIXES_OPENCLAW_2026-05-14.md",
        "SUBAGENT_SPAWN_REVIEW_2026-05-25.md",
    ]
)

# Path *prefixes* whose sub-tree must never be tracked (forward-slash, no
# leading slash).  A tracked path is banned if it starts with any of these.
_BANNED_PREFIXES: tuple[str, ...] = (
    # Personal knowledge-vault content directories
    "wiki/obsidian-vault/sessions/",
    "wiki/obsidian-vault/_archive/",
    "wiki/obsidian-vault/entities/",
    "wiki/obsidian-vault/concepts/",
    "wiki/obsidian-vault/projects/",
)

# Broader root-level glob-style guards: catch any *new* file of the same
# class at the repo root (no directory separator in the path).
# These complement the exact names above and cover future additions.
_BANNED_ROOT_PREFIXES: tuple[str, ...] = (
    "_",        # underscore scratch: _admin_unit.txt, _baseline_loopback.txt, …
)

_BANNED_ROOT_SUFFIXES: tuple[str, ...] = (
    "-view.png",    # UI screenshot dumps: agents-view.png, chats-view.png, …
)


def _is_banned(path: str) -> tuple[bool, str]:
    """Return (True, reason) if *path* is a banned personal-state file."""
    # Normalise to forward slashes (git ls-files uses / even on Windows).
    normed = path.replace("\\", "/")

    if normed in _BANNED_EXACT:
        return True, f"exact match in banned list: {path!r}"

    for prefix in _BANNED_PREFIXES:
        if normed.startswith(prefix):
            return True, f"under banned prefix {prefix!r}: {path!r}"

    # Root-level pattern checks — only for files directly at repo root
    # (no directory separator in the normalised path).
    if "/" not in normed:
        for pfx in _BANNED_ROOT_PREFIXES:
            if normed.startswith(pfx):
                return True, f"root-level file with banned prefix {pfx!r}: {path!r}"
        for sfx in _BANNED_ROOT_SUFFIXES:
            if normed.endswith(sfx):
                return True, f"root-level file with banned suffix {sfx!r}: {path!r}"

    return False, ""


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32" and not _is_git_repo(_REPO_ROOT),
                    reason="not in a git repo")
def test_no_personal_state_tracked() -> None:
    """Fail if any personal/per-user path is tracked by git.

    This guard prevents accidental re-introduction of personal state through
    broad ``git add .`` invocations.  It is the enforcement layer for the
    Wave 0 open-source privacy separation, coordinated with the Wave 5
    history purge (same path list, different mechanism).
    """
    if not _is_git_repo(_REPO_ROOT):
        pytest.skip("not inside a git repository — skipping personal-state guard")

    tracked = _tracked_files(_REPO_ROOT)
    violations: list[str] = []
    for path in tracked:
        banned, reason = _is_banned(path)
        if banned:
            violations.append(reason)

    if violations:
        lines = "\n  ".join(violations)
        pytest.fail(
            f"Personal/per-user state is tracked by git ({len(violations)} violation(s)):\n"
            f"  {lines}\n\n"
            "Fix: run `git rm --cached <path>` for each offending file and ensure "
            "the path is covered by a .gitignore pattern.  "
            "See Downloads/Open-Source-Privacy-Separation.md Wave 0 for details."
        )
