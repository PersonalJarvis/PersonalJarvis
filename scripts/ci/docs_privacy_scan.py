#!/usr/bin/env python3
"""Deterministic personal-data scanner for documentation files.

This is the *fast, deterministic* half of the docs privacy defence (the slow,
semantic half is the ``docs-privacy-reviewer`` sub-agent). It reuses the single
source of truth for masking — the ``ship-public-release`` skill's
``pii-scrub.tsv`` — so a hit here means exactly the same thing it means at ship
time: a personal identifier reached a file that is world-readable forever once
pushed.

Two modes:

* **scan** (default) — read-only. Prints every hit as ``path:line: <why>`` and
  exits non-zero if any are found. Wired into a ``PostToolUse`` hook (warn), the
  ``.githooks/pre-push`` gate (fail-closed), and the ``docs-privacy`` CI job.
* **--fix** — applies the ``scrub`` substitutions in place (canonical ordering
  from the manifest) and replaces the private maintainer emails with a neutral
  placeholder. Used for the one-off bulk clean-up.

Both the ``scrub`` rows and the private ``block-only`` email patterns are read
from the manifest — this script hardcodes NO personal value of its own (so the
script itself never leaks an identifier and never drifts from the manifest). The
emails are ``block-only`` there because they must survive untouched in
``.mailmap``; ``.mailmap`` is not under ``docs/``, so inside documentation we are
free to mask them — and we must, because they would otherwise hard-block the
next public push.

Usage:
    python scripts/ci/docs_privacy_scan.py [PATH ...]        # scan (read-only)
    python scripts/ci/docs_privacy_scan.py --fix [PATH ...]  # mask in place

With no PATH the whole tracked ``docs/`` tree is scanned.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / ".claude" / "skills" / "ship-public-release" / "references" / "pii-scrub.tsv"

# Where a private maintainer email gets masked inside docs (the real addresses
# live only in the manifest + .mailmap; this script never spells one out).
EMAIL_PLACEHOLDER = "maintainer@example.com"

# Text-only file suffixes we look inside. Binary/image docs are skipped.
TEXT_SUFFIXES = {".md", ".markdown", ".html", ".htm", ".txt", ".py", ".toml", ".json", ".yml", ".yaml", ".tsv", ".csv"}


def load_manifest() -> tuple[list[tuple[re.Pattern[str], str, str]], list[re.Pattern[str]]]:
    """Parse the canonical scrub manifest.

    Returns ``(scrub_rules, blockonly_email_patterns)``:
    * ``scrub_rules`` = ``(compiled, replacement, note)`` for every ``scrub`` row,
      in file order (the order is load-bearing: full name before surname, GitHub
      login/slug before the bare first name).
    * ``blockonly_email_patterns`` = compiled patterns from ``block-only`` rows
      that look like an email (so private maintainer mailboxes are masked inside
      docs without this script ever hardcoding the address).
    """
    scrub_rules: list[tuple[re.Pattern[str], str, str]] = []
    blockonly_emails: list[re.Pattern[str]] = []
    for raw in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip("\n")
        if not line or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        action, pattern = parts[0], parts[1]
        replacement = parts[2] if len(parts) > 2 else ""
        note = parts[3] if len(parts) > 3 else ""
        if action == "scrub":
            scrub_rules.append((re.compile(pattern), replacement, note))
        elif action == "block-only" and "@" in pattern:
            blockonly_emails.append(re.compile(pattern))
    return scrub_rules, blockonly_emails


def tracked_docs() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "docs"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [REPO_ROOT / rel for rel in out.splitlines() if rel.strip()]


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def scan_text(
    text: str,
    rules: list[tuple[re.Pattern[str], str, str]],
    emails: list[re.Pattern[str]],
) -> list[tuple[int, str]]:
    """Return (line_number, why) for every personal-data hit."""
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for pat in emails:
            if pat.search(line):
                hits.append((i, "private maintainer email"))
        for pat, _repl, note in rules:
            if pat.search(line):
                hits.append((i, f"{note or pat.pattern}"))
    return hits


def fix_text(
    text: str,
    rules: list[tuple[re.Pattern[str], str, str]],
    emails: list[re.Pattern[str]],
) -> str:
    # Emails first, so the bare-name rules cannot fragment the address.
    for pat in emails:
        text = pat.sub(EMAIL_PLACEHOLDER, text)
    for pat, repl, _note in rules:
        text = pat.sub(repl, text)
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*", help="files to check (default: tracked docs/ tree)")
    ap.add_argument("--fix", action="store_true", help="mask personal data in place")
    args = ap.parse_args()

    rules, emails = load_manifest()

    if args.paths:
        targets = [(Path(p) if Path(p).is_absolute() else (REPO_ROOT / p)).resolve() for p in args.paths]
    else:
        targets = tracked_docs()

    # In hook mode a single file is passed; only act on documentation.
    targets = [
        t for t in targets
        if "docs" in t.parts and t.suffix.lower() in TEXT_SUFFIXES and t.is_file()
    ]

    total_hits = 0
    changed = 0
    for path in targets:
        text = _read(path)
        if text is None:
            continue
        if args.fix:
            new = fix_text(text, rules, emails)
            if new != text:
                path.write_text(new, encoding="utf-8")
                changed += 1
                rel = path.relative_to(REPO_ROOT) if REPO_ROOT in path.parents else path
                print(f"fixed: {rel}")
        else:
            hits = scan_text(text, rules, emails)
            for line_no, why in hits:
                rel = path.relative_to(REPO_ROOT) if REPO_ROOT in path.parents else path
                print(f"{rel}:{line_no}: {why}")
            total_hits += len(hits)

    if args.fix:
        print(f"\n{changed} file(s) masked.")
        return 0

    if total_hits:
        print(
            f"\n{total_hits} personal-data hit(s) found. "
            "Mask them (python scripts/ci/docs_privacy_scan.py --fix <file>) "
            "or run the docs-privacy-reviewer sub-agent before this ships.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
