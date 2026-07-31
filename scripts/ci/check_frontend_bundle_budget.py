#!/usr/bin/env python
"""Frontend startup-bundle budget guard — fail when the entry chunk gets fat again.

Why this exists (2026-07-26): ``MainView.tsx`` imported all 21 section views
statically, so EVERY section — plus the terminal emulator and the charting
library almost nobody opens on start — was linked into the single entry chunk.
It reached 2.83 MB, and a WebView must download, parse and execute all of it
before React paints its first pixel. The boot splash in ``index.html`` only
hid the wait; it did not shorten it.

This is the frontend twin of ``check_boot_budget.py``: that one guards the
Python startup path, this one guards the bytes the UI must chew through before
it is interactive. Both exist because the regression class is identical — a
feature "only adds a bit" until the app takes seconds to become usable.

What it measures: the size of the ES module entry chunk that ``dist/index.html``
actually references (never a guessed filename — ``dist/`` accumulates stale
hashed chunks from earlier builds), plus the stylesheet it loads. Lazy chunks
are deliberately NOT counted: they are the fix, and they load off the critical
path.

Budget: ``JARVIS_BUNDLE_BUDGET_ENTRY_KB`` (default 1200). The measured entry
after the code-splitting fix is ~700 KB, so the budget leaves generous headroom
for normal feature work while still blocking a relapse toward 2.8 MB.

Exit codes: 0 = within budget, 1 = budget exceeded, 78 = skipped (no build
present) so callers can treat "could not measure" as neutral rather than red.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST = REPO_ROOT / "jarvis" / "ui" / "web" / "dist"
INDEX_HTML = DIST / "index.html"

SKIP_EXIT = 78  # "could not measure" — neutral, not a failure

DEFAULT_ENTRY_BUDGET_KB = 1200.0

# The entry is the module script Vite injects; the stylesheet rides the same
# critical path. Both are matched off index.html so a stale sibling chunk in
# dist/ can never be mistaken for the live entry.
_ENTRY_RE = re.compile(
    r"""<script[^>]*\btype=["']module["'][^>]*\bsrc=["']([^"']+)["']""",
    re.IGNORECASE,
)
_CSS_RE = re.compile(
    r"""<link[^>]*\brel=["']stylesheet["'][^>]*\bhref=["']([^"']+)["']""",
    re.IGNORECASE,
)


def _resolve(asset_href: str) -> Path:
    """Map an index.html href ("/assets/index-abc.js") onto its file in dist/."""
    return DIST / asset_href.lstrip("/")


def _kb(path: Path) -> float:
    return path.stat().st_size / 1024.0


def main() -> int:
    if not INDEX_HTML.is_file():
        print(f"SKIP: no frontend build at {INDEX_HTML} (run `npm run build`)")
        return SKIP_EXIT

    html = INDEX_HTML.read_text(encoding="utf-8", errors="replace")

    entry_match = _ENTRY_RE.search(html)
    if entry_match is None:
        print("SKIP: index.html references no module entry script — unknown build layout")
        return SKIP_EXIT

    entry = _resolve(entry_match.group(1))
    if not entry.is_file():
        print(f"SKIP: entry chunk {entry.name} referenced but missing from dist/")
        return SKIP_EXIT

    try:
        budget_kb = float(
            os.environ.get("JARVIS_BUNDLE_BUDGET_ENTRY_KB", DEFAULT_ENTRY_BUDGET_KB)
        )
    except ValueError:
        budget_kb = DEFAULT_ENTRY_BUDGET_KB

    entry_kb = _kb(entry)

    css_match = _CSS_RE.search(html)
    css_kb = 0.0
    if css_match is not None:
        css = _resolve(css_match.group(1))
        if css.is_file():
            css_kb = _kb(css)

    print(f"entry chunk : {entry.name}  {entry_kb:8.1f} KB  (budget {budget_kb:.0f} KB)")
    if css_kb:
        print(f"stylesheet  : {css_kb:8.1f} KB  (not budgeted, reported for context)")

    if entry_kb > budget_kb:
        print(
            f"\nFAIL: the startup bundle is {entry_kb - budget_kb:.1f} KB over budget.\n"
            "Every byte here is parsed before the UI paints. The usual cause is a\n"
            "view or heavy library imported STATICALLY where it should be lazy:\n"
            "check src/components/layout/MainView.tsx and any new top-level import."
        )
        return 1

    print("\nOK: startup bundle within budget.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
