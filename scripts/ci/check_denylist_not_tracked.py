#!/usr/bin/env python3
"""CI gate: block NEWLY TRACKED files that the distribution denylist withholds.

``privacy_gate/references/distribution-denylist.txt`` is a curated register of
paths that must never reach the public repo — live-vault captures, internal
process docs, key material, personal scratch — each with a written rationale.
It used to be enforced by the depersonalized-snapshot build, which was retired
on 2026-07-17 in favour of keys-only protection. Keys-only does not cover PII,
so the register kept its rationales but lost its teeth, and ~460 withheld files
became public without anyone noticing.

This gate gives it teeth again at the only moment that scales: the instant a
file is ADDED. Like the sibling ``check_no_new_german.py`` it is deliberately
blind to the backlog — the denylisted files already tracked are a cleanup task,
not a reason to block every commit — so it inspects only what the staged change
introduces. That keeps the gate quiet on normal work and loud exactly when a
withheld path is about to become permanent.

Modes:
  ``--staged``  inspect files the staged change ADDS; rc 1 on a match (hook use)
  ``--report``  list every tracked file the denylist withholds (the backlog); rc 0
  ``--check``   like ``--report`` but rc 1 if the backlog is non-empty (release use)

The denylist and its glob semantics are loaded from the privacy-gate module
itself rather than reimplemented, so the two can never drift apart. When that
module is absent — a fork that never had it — this exits 0 with a note: a
missing personal register is not a fork's problem to solve.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_GATE_DIR = _HERE / "privacy_gate"
_ENGINE = _GATE_DIR / "scripts" / "strip_and_scan.py"


def _load_engine():
    """Import the privacy-gate module by path, or None when it is not present."""
    if not _ENGINE.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_privacy_gate_engine", _ENGINE)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(*args: str) -> list[str]:
    out = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", check=False
    )
    if out.returncode != 0:
        return []
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def _added_paths() -> list[str]:
    """Repo-relative posix paths the staged change ADDS (A) or renames in (R)."""
    staged = _git("diff", "--cached", "--name-only", "--diff-filter=AR")
    return [p.replace("\\", "/") for p in staged]


def _tracked_paths() -> list[str]:
    return [p.replace("\\", "/") for p in _git("ls-files")]


def _rationale_for(pattern: str, denylist_text: str) -> str:
    """The comment block directly above ``pattern`` — why it was withheld.

    The register's value is its reasoning, so surface it at the block instead of
    making the author go read the file. Returns '' when a line carries no
    preceding comment.
    """
    lines = denylist_text.splitlines()
    try:
        idx = next(i for i, ln in enumerate(lines) if ln.strip() == pattern)
    except StopIteration:
        return ""
    comment: list[str] = []
    for ln in reversed(lines[:idx]):
        stripped = ln.strip()
        if not stripped.startswith("#"):
            break
        text = stripped.lstrip("#").strip(" -")
        if text:
            comment.append(text)
    return " ".join(reversed(comment))


def _violations(paths: list[str], engine, denylist_text: str) -> list[tuple[str, str, str]]:
    """(path, pattern, rationale) for every path a denylist entry withholds."""
    patterns = [
        ln.strip()
        for ln in denylist_text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    compiled: list[tuple[str, re.Pattern[str]]] = [
        (p, engine._compile_glob(p)) for p in patterns
    ]
    hits: list[tuple[str, str, str]] = []
    for path in paths:
        for pattern, rx in compiled:
            if rx.match(path):
                hits.append((path, pattern, _rationale_for(pattern, denylist_text)))
                break
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="check what the staged change adds")
    mode.add_argument("--report", action="store_true", help="list the tracked backlog")
    mode.add_argument("--check", action="store_true", help="like --report, but rc 1 if non-empty")
    args = ap.parse_args(argv)

    engine = _load_engine()
    denylist_path = _GATE_DIR / "references" / "distribution-denylist.txt"
    if engine is None or not denylist_path.is_file():
        print("check_denylist_not_tracked: SKIP - no privacy-gate register in this tree.")
        return 0

    denylist_text = denylist_path.read_text(encoding="utf-8")
    staged = args.staged or not (args.report or args.check)
    paths = _added_paths() if staged else _tracked_paths()
    hits = _violations(paths, engine, denylist_text)

    if not hits:
        scope = "staged additions" if staged else "tracked tree"
        print(f"check_denylist_not_tracked: OK - no withheld paths in the {scope}.")
        return 0

    if staged:
        print("")
        print("DISTRIBUTION DENYLIST - this change tracks a path meant to stay private:")
        print("")
        for path, pattern, why in hits:
            print(f"  {path}")
            print(f"    withheld by:  {pattern}")
            if why:
                print(f"    reason:       {why}")
        print("")
        print("  Committing it makes it permanent and world-readable. Either keep the")
        print("  file untracked (add it to .gitignore), or - if the register is wrong -")
        print("  edit distribution-denylist.txt in the SAME commit so the decision is")
        print("  recorded rather than bypassed.")
        return 1

    by_pattern: dict[str, int] = {}
    for _, pattern, _why in hits:
        by_pattern[pattern] = by_pattern.get(pattern, 0) + 1
    print(f"check_denylist_not_tracked: {len(hits)} tracked files are withheld by the register.")
    for pattern, count in sorted(by_pattern.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>5}  {pattern}")
    print("")
    print("  These predate the gate. Untrack them with `git rm --cached` (the files stay")
    print("  on disk) and gitignore them, or retire the entry if it no longer applies.")
    print("  Note: untracking does not remove them from history - that needs filter-repo.")
    return 1 if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
