#!/usr/bin/env python3
"""CI gate: block NEWLY ADDED German text from reaching GitHub.

Enforces the repo's Output Language Policy (CLAUDE.md, HIGHEST PRIORITY) without
drowning in the in-flight DE->EN translation backlog: it inspects only the lines
a push/PR ADDS (the ``+`` lines of the diff), never the pre-existing German that
the translation effort is still working through.

Diff base resolution (most specific first):
  1. an explicit ``<base>`` CLI argument;
  2. GitHub Actions ``pull_request`` event   -> pull_request.base.sha;
  3. GitHub Actions ``push`` event           -> event ``before`` SHA;
  4. ``origin/$GITHUB_BASE_REF`` if set;
  5. local fallback: ``origin/main``.

A line is a violation when it is German (``_german_detect.looks_german``) AND its
file is a scanned text type AND the file is not on the path allowlist AND the line
does not carry an inline ``i18n-allow`` escape.

Exit non-zero with a readable report on the first run that finds violations, so a
branch-protection required check turns the merge red. Mirrors the style of the
sibling gates ``check_import_clean.py`` / ``assert_min_passed.py``.
"""
from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _german_detect import looks_german  # noqa: E402  (path set up above)

_HERE = Path(__file__).resolve().parent
_ALLOWLIST_PATH = _HERE / "german-allowlist.txt"

# Only these file types are scanned. Anything else (images, binaries, lockfiles)
# is skipped outright.
SCAN_EXT: frozenset[str] = frozenset(
    {
        ".py", ".md", ".txt", ".rst",
        ".ts", ".tsx", ".js", ".jsx",
        ".json", ".toml", ".yaml", ".yml",
        ".html", ".css", ".cfg", ".ini",
    }
)

# Inline escape: a line containing this token is never flagged.
_INLINE_ESCAPE = "i18n-allow"


def load_allowlist(path: Path = _ALLOWLIST_PATH) -> list[str]:
    if not path.exists():
        return []
    patterns: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def is_allowlisted(path: str, patterns: list[str]) -> bool:
    norm = path.replace("\\", "/")
    for pat in patterns:
        if fnmatch.fnmatch(norm, pat):
            return True
        # directory-prefix patterns ('dir/*') also cover the whole subtree
        if pat.endswith("/*") and (norm == pat[:-2] or norm.startswith(pat[:-1])):
            return True
    return False


def is_scanned(path: str) -> bool:
    return Path(path).suffix.lower() in SCAN_EXT


def parse_added_lines(diff_text: str) -> list[tuple[str, int, str]]:
    """Extract ``(path, new_line_number, added_text)`` triples from a unified
    diff produced with ``--unified=0``. Deletions are ignored (removing German is
    always fine)."""
    import re

    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
    added: list[tuple[str, int, str]] = []
    current: str | None = None
    new_lineno = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            if target == "/dev/null":
                current = None
            elif target.startswith(("a/", "b/")):
                current = target[2:]
            else:
                current = target
        elif line.startswith("@@"):
            m = hunk_re.match(line)
            if m:
                new_lineno = int(m.group(1))
        elif line.startswith("+") and not line.startswith("+++"):
            if current is not None:
                added.append((current, new_lineno, line[1:]))
            new_lineno += 1
        elif line.startswith("-") and not line.startswith("---"):
            # deletion: does not advance the new-file line counter
            continue
        elif not line.startswith(("diff ", "index ", "--- ")):
            # context line (none with -U0, but be defensive)
            new_lineno += 1
    return added


def find_violations(
    added: list[tuple[str, int, str]], patterns: list[str]
) -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    for path, lineno, text in added:
        if not is_scanned(path):
            continue
        if is_allowlisted(path, patterns):
            continue
        if _INLINE_ESCAPE in text:
            continue
        if looks_german(text):
            out.append((path, lineno, text.strip()[:120]))
    return out


def resolve_base(argv: list[str]) -> str:
    if argv:
        return argv[0]
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_path and Path(event_path).exists():
        try:
            event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            event = {}
        if event_name == "pull_request":
            sha = (
                event.get("pull_request", {}).get("base", {}).get("sha")
            )
            if sha:
                return sha
        before = event.get("before")
        if before and set(before) != {"0"}:
            return before
    base_ref = os.environ.get("GITHUB_BASE_REF")
    if base_ref:
        return f"origin/{base_ref}"
    return "origin/main"


def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def get_diff(base: str) -> str:
    if _git(["rev-parse", "--verify", "--quiet", base]).returncode != 0:
        print(
            f"WARNING: diff base '{base}' not found locally; "
            "skipping language gate (nothing to compare against).",
            file=sys.stderr,
        )
        return ""
    result = _git(
        ["diff", "--unified=0", "--no-color", "--diff-filter=AM", f"{base}...HEAD"]
    )
    return result.stdout


def get_staged_diff() -> str:
    """Unified diff of the STAGED index vs HEAD (additions only) — for pre-commit.

    Lets the gate run at COMMIT time (the real write path, incl. the auto-save
    stop-hook), not only on an explicit push/PR.
    """
    result = _git(["diff", "--cached", "--unified=0", "--no-color", "--diff-filter=AM"])
    return result.stdout


def scan_all_tracked(patterns: list[str]) -> list[tuple[str, int, str]]:
    """Whole-tree scan of every git-tracked, scanned, non-allowlisted file.

    For the public-release gate: a depersonalized snapshot must be German-free in
    FULL, not just in a diff (the diff/PR gates are structurally blind to backlog).
    """
    out: list[tuple[str, int, str]] = []
    for path in _git(["ls-files"]).stdout.splitlines():
        path = path.strip()
        if not path or not is_scanned(path) or is_allowlisted(path, patterns):
            continue
        try:
            content = (Path.cwd() / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(content.splitlines(), start=1):
            if _INLINE_ESCAPE in line:
                continue
            if looks_german(line):
                out.append((path, lineno, line.strip()[:120]))
    return out


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Windows consoles default to cp1252; make the stream UTF-8 where
    # possible so a non-ASCII path in a report can never crash the gate.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

    flags = {a for a in argv if a.startswith("--")}
    positional = [a for a in argv if not a.startswith("--")]
    patterns = load_allowlist()

    if "--all" in flags:
        # Whole-tree scan (public-release snapshot gate).
        violations = scan_all_tracked(patterns)
        scope = "whole tree"
    elif "--staged" in flags:
        # Staged index vs HEAD (pre-commit).
        added = parse_added_lines(get_staged_diff())
        violations = find_violations(added, patterns)
        scope = "staged changes"
    else:
        # Default: added lines vs a diff base (pre-push / PR CI).
        base = resolve_base(positional)
        added = parse_added_lines(get_diff(base))
        violations = find_violations(added, patterns)
        scope = f"added lines (diff base: {base})"

    if violations:
        print("OUTPUT-LANGUAGE GATE FAILED - German text detected.\n")
        print(
            "Every committed artifact must be English (CLAUDE.md, Output Language\n"
            f"Policy). The lines below ({scope}) look German:\n"
        )
        for path, lineno, text in violations:
            print(f"  [x] {path}:{lineno}")
            print(f"      {text}")
        print(
            f"\n{len(violations)} German line(s) found. Translate them to English, "
            "or - if a line is intentionally German -\n"
            "add an inline 'i18n-allow' marker, or extend "
            "scripts/ci/german-allowlist.txt for a whole file."
        )
        return 1

    print(f"output-language gate OK - no German ({scope}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
