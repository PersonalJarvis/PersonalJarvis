#!/usr/bin/env python3
"""Gate the bundled brand marks in the Plugins store.

Three failure modes this catches, all of which shipped at least once:

1. **An unsafe SVG.** An SVG is executable content: it can carry a script, an
   event handler, or a reference that phones a third party on render. These
   files come from outside the project, so they are checked rather than
   trusted.
2. **A licence gap.** A mark in the folder with no row in ``LOGOS.md`` is an
   asset whose provenance nobody can reconstruct later.
3. **A wordmark in a square tile.** A horizontal logotype squeezed into 40 px
   is unreadable. Vendors publish both; the icon variant is the one that
   belongs here.

Run standalone or from the pre-push hook. Exit 0 = clean, 1 = findings.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_BRANDS = _ROOT / "jarvis" / "ui" / "web" / "frontend" / "src" / "assets" / "brands"
_LEDGER = _BRANDS / "LOGOS.md"

# Generous: a real icon is square, but a few legitimate marks are slightly
# rectangular. Anything past this is a logotype.
_MIN_ASPECT, _MAX_ASPECT = 0.7, 1.45
_MAX_BYTES = 60_000

_ACTIVE = re.compile(r"<\s*(script|foreignObject|iframe|animate|set)\b", re.I)
_EVENT_ATTR = re.compile(r"\son[a-z]+\s*=", re.I)
_EXTERNAL = re.compile(r"(href|xlink:href|src)\s*=\s*[\"']\s*(https?:|//)", re.I)
_RASTER = re.compile(r"<\s*image\b", re.I)
_VIEWBOX = re.compile(r'viewBox\s*=\s*["\']([\d.eE+\-\s]+)["\']', re.I)


def _aspect(svg: str) -> float | None:
    match = _VIEWBOX.search(svg)
    if not match:
        return None
    try:
        parts = [float(value) for value in match.group(1).split()]
    except ValueError:
        return None
    if len(parts) != 4 or parts[3] == 0:
        return None
    return parts[2] / parts[3]


def main() -> int:
    if not _BRANDS.is_dir():
        print("check_brand_logos: no brands folder - nothing to check.")
        return 0

    ledger = _LEDGER.read_text(encoding="utf-8") if _LEDGER.exists() else ""
    problems: list[str] = []

    for path in sorted(_BRANDS.glob("*.svg")):
        plugin_id = path.stem
        svg = path.read_text(encoding="utf-8", errors="replace")
        where = path.relative_to(_ROOT).as_posix()

        if _ACTIVE.search(svg):
            problems.append(f"{where}: contains active content (script/animation/embed)")
        if _EVENT_ATTR.search(svg):
            problems.append(f"{where}: carries an inline event handler")
        if _EXTERNAL.search(svg):
            problems.append(f"{where}: references a third-party URL at render time")
        if _RASTER.search(svg):
            problems.append(f"{where}: embeds a raster image (use vector paths)")
        if len(svg.encode("utf-8")) > _MAX_BYTES:
            problems.append(f"{where}: {len(svg)} bytes exceeds the {_MAX_BYTES} limit")

        ratio = _aspect(svg)
        if ratio is None:
            problems.append(f"{where}: no usable viewBox, so it cannot be laid out")
        elif not (_MIN_ASPECT <= ratio <= _MAX_ASPECT):
            problems.append(
                f"{where}: aspect {ratio:.2f} is a wordmark, not an icon - "
                "use the vendor's icon variant"
            )

        if f"| {plugin_id} |" not in ledger:
            problems.append(
                f"{where}: no row in LOGOS.md - every bundled mark needs its "
                "source and legal basis recorded"
            )

    if problems:
        print("BRAND-LOGO GATE FAILED\n")
        for problem in problems:
            print(f"  [x] {problem}")
        print(
            f"\n{len(problems)} finding(s). See "
            "jarvis/ui/web/frontend/src/assets/brands/LOGOS.md for the rules."
        )
        return 1

    count = len(list(_BRANDS.glob("*.svg")))
    print(f"check_brand_logos: OK - {count} bundled marks, all safe and recorded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
