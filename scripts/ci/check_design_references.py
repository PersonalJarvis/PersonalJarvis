#!/usr/bin/env python3
"""CI gate: public text never presents another product as our design template.

AGENTS.md ("Public product descriptions"): tracked docs, comments, docstrings,
test descriptions, video copy and changelog text describe our features
directly. They never say a feature is inspired by, modeled on, adapted from or
drawn "the way" another product does it. Required license and attribution
notices and factual integration references stay; this gate never asks anyone
to hide where code came from.

The patterns are deliberately narrow — a product name next to a design verb,
a "<Product>-style" adjective, or "like the <Product> app" — so an integration
reference ("import the Claude Desktop config", "the Codex CLI worker") never
trips it. A line that must keep such wording (a license header, a quoted
user utterance) carries the inline marker ``design-ref-allow``; whole files
that are third-party text are listed in ``_ALLOWED_PATHS``.

Modes::

    python scripts/ci/check_design_references.py            # whole tree (CI)
    python scripts/ci/check_design_references.py --staged   # added lines (pre-commit)

Exit 1 = a confirmed hit; anything else is tooling trouble and the pre-commit
hook fails open on it.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_no_new_german import SCAN_EXT, parse_added_lines  # noqa: E402

_INLINE_ESCAPE = "design-ref-allow"

# Third-party text, the files that define or test this rule, and code that
# carries an upstream license header for what it adapted.
_ALLOWED_PATHS: tuple[str, ...] = (
    "**/THIRD_PARTY_NOTICES*",
    "CODE_OF_CONDUCT.md",
    "jarvis/skills/builtin/skill-creator/references/*",
    "jarvis/ui/web/frontend/src/components/society/AgentSymbol.tsx",
    "scripts/readme-video/src/identity/AgentSymbol.tsx",
    "scripts/ci/check_design_references.py",
    "tests/unit/ci/test_check_design_references.py",
    "**/dist/*",
    "*package-lock.json",
)

# Product names are matched case-sensitively, so "a linear fade", "the cursor"
# or "an arc" never count; the verbs around them ignore case.
_PRODUCTS = (
    r"ShareX|CleanShot(?: X)?|Raycast|Alfred|Spotlight|Grok(?: Bot)?"
    r"|Hermes(?: Agent| Bot| Desktop)?|Nous Research|Codex(?: app| desktop)?"
    r"|Claude(?: app| desktop)?|ChatGPT(?: app)?|Cursor"
    r"|Linear|Notion|Cowork|Superhuman|Wispr(?: Flow)?|Superwhisper|Granola|Arc|Warp"
)
_APPS = r"Codex|Claude|ChatGPT|Grok|Cursor|Raycast|CleanShot|ShareX"
# Every rule needs one of these names, so a line without any is skipped cheaply.
_ANY_PRODUCT = re.compile(_PRODUCTS)

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "design verb next to a product",
        re.compile(
            r"\b(?i:inspired by|modell?ed (?:on|after)|adapted from|borrowed from|copied from"
            r"|design reference|reference product|follows the design of)\b[^.\n]{0,60}?\b(?:"
            + _PRODUCTS
            + r")\b"
        ),
    ),
    (
        "'<Product>-style' adjective",
        re.compile(
            r"\b(?:Codex|Claude|ChatGPT|Cursor|Raycast|Hermes|Grok|Spotlight|Notion|Linear"
            r"|ShareX|CleanShot)[- ](?i:style|like|inspired)\b"
            r"(?! `?(?:event|stream|payload|format|apply_patch))"
        ),
    ),
    (
        "behaves like another app",
        re.compile(
            r"\b(?i:like|after|as in|the way|judged against)(?: the)? (?:" + _APPS + r")"
            r"(?: X)? (?:app|does|draws|shows|marks|opens|lists|keeps)\b"
        ),
    ),
    (
        "another app's look",
        re.compile(
            r"\b(?:" + _APPS + r")(?: X)?(?: app)?['’]s (?i:look|design|layout|style|composer"
            r"|column|opening|voice mode|voice glow|region-capture look|annotate tool"
            r"|Quick Access Overlay)\b"
        ),
    ),
    (
        "names a screenshot tool",
        re.compile(r"\b(?:ShareX|CleanShot)\b", re.IGNORECASE),
    ),
)


def is_allowed(path: str) -> bool:
    norm = path.replace("\\", "/")
    return any(fnmatch.fnmatch(norm, pat) for pat in _ALLOWED_PATHS)


def is_scanned(path: str) -> bool:
    return Path(path).suffix.lower() in SCAN_EXT


def line_hits(text: str) -> list[str]:
    """Names of the rules ``text`` breaks (empty when it is clean)."""
    if not _ANY_PRODUCT.search(text) or _INLINE_ESCAPE in text:
        return []
    return [name for name, rx in _RULES if rx.search(text)]


def find_violations(
    lines: list[tuple[str, int, str]],
) -> list[tuple[str, int, str, str]]:
    out: list[tuple[str, int, str, str]] = []
    scanned: dict[str, bool] = {}
    for path, lineno, text in lines:
        if path not in scanned:
            scanned[path] = is_scanned(path) and not is_allowed(path)
        if not scanned[path]:
            continue
        hits = line_hits(text)
        if hits:
            out.append((path, lineno, hits[0], text.strip()[:140]))
    return out


def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )


def tracked_lines() -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    for path in _git(["ls-files"]).stdout.splitlines():
        path = path.strip()
        if not path or not is_scanned(path) or is_allowed(path):
            continue
        try:
            content = (Path.cwd() / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out.extend((path, n, line) for n, line in enumerate(content.splitlines(), start=1))
    return out


def staged_lines() -> list[tuple[str, int, str]]:
    diff = _git(["diff", "--cached", "--unified=0", "--no-color", "--diff-filter=AM"]).stdout
    return parse_added_lines(diff)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

    staged = "--staged" in argv
    violations = find_violations(staged_lines() if staged else tracked_lines())
    scope = "staged changes" if staged else "whole tree"
    if not violations:
        print(f"design-reference gate OK ({scope}).")
        return 0

    print("DESIGN-REFERENCE GATE FAILED - public text names another product as our template.\n")
    for path, lineno, rule, text in violations:
        print(f"  [x] {path}:{lineno}  ({rule})")
        print(f"      {text}")
    print(
        f"\n{len(violations)} line(s). Describe our feature directly instead "
        "(AGENTS.md, 'Public product descriptions').\n"
        "A license header or a factual integration line may carry an inline "
        f"'{_INLINE_ESCAPE}' marker."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
