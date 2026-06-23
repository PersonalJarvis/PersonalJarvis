"""Regression guard: the shipped ``jarvis`` package must not impersonate
Marvel's J.A.R.V.I.S. character.

Trademark/IP audit 2026-06-22: Marvel Characters, Inc. (Disney) owns the
registered wordmark "JARVIS" for voice-assistant software and actively enforces
it (Jarvis.ai -> Jasper rebrand, BlackBerry opposition, 2025 AI cease-and-desist
wave). The product deliberately keeps the *name* "Jarvis", but it must NOT
present itself as the Marvel character: no "modeled on Paul Bettany / the Iron
Man / Avengers films", no Tony-Stark persona, no Marvel source attribution.
Those positive-mimicry markers turn a defensible generic name into a documented
character copy and destroy any "generic name" defense.

This test scans the importable package (``jarvis/**.py`` + ``jarvis/**.md``) and
fails if any unambiguous Marvel-character marker reappears. The bare name
"JARVIS" is intentionally NOT forbidden — only the character identification is.
"""
from __future__ import annotations

import re
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "jarvis"

# Unambiguous markers that identify the Marvel character specifically. The bare
# product name "JARVIS"/"Jarvis" is intentionally absent — the product keeps it.
_FORBIDDEN_MARKERS = (
    "paul bettany",
    "bettany",
    "iron man",
    "iron-man",
    "ironman",
    "avengers",
    "tony stark",
    "tony-stark",
    "mr. stark",
    "mr stark",
    "stark industries",
    "stark's jarvis",
    "just a rather very intelligent",
    "marvel",
)

_PATTERN = re.compile(
    "|".join(re.escape(marker) for marker in _FORBIDDEN_MARKERS),
    re.IGNORECASE,
)


def _iter_package_text_files():
    for path in _PACKAGE_ROOT.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".py", ".md"}:
            yield path


def test_shipped_package_has_no_marvel_character_markers() -> None:
    hits: list[str] = []
    for path in _iter_package_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _PATTERN.search(line):
                rel = path.relative_to(_PACKAGE_ROOT.parent)
                hits.append(f"{rel}:{lineno}: {line.strip()}")
    assert not hits, (
        "Marvel-character impersonation markers found in the shipped package. "
        "Keep the name 'Jarvis', but drop the Marvel character (see the "
        "2026-06-22 trademark audit):\n  " + "\n  ".join(hits)
    )
