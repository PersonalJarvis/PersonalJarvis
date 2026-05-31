"""Conservative skip-when-safe vision gate (Wave 1 — omni-latency suite).

The router runs text-only by default for cheap turns, but vision must stay
robust: the screenshot is dropped ONLY when the turn is confidently text-only
(smalltalk / simple Q&A) AND carries no visual-reference marker. Everything
else keeps the image. This avoids the 2026-04-28 regression where on-demand-
only vision made the router hallucinate a blank desktop ("Browser visible"
instead of "8 terminals").
"""
from __future__ import annotations

import re

# Deictic / visual-reference markers (DE + EN). Presence of any marker forces
# the screenshot to be attached even on an otherwise-smalltalk turn. Substring
# matching is intentional ("klick" also catches "anklicken", "schau" catches
# "anschauen") — over-keeping is the safe direction here.
_VISUAL_MARKERS: tuple[str, ...] = (
    "das hier", "das da", "hier auf", "da auf", "hier oben", "hier unten",
    "schau", "sieh", "siehst", "guck", "zeig mir",
    "auf dem bildschirm", "am bildschirm", "im bild", "auf dem screen",
    "dieses fenster", "das fenster", "diese seite", "die seite hier",
    "klick", "markier", "warum rot",
    "this here", "that there", "look at", "see this", "on screen",
    "on the screen", "this window", "what's this", "what is this", "click",
)

_MARKER_RE = re.compile("|".join(re.escape(m) for m in _VISUAL_MARKERS), re.IGNORECASE)


def has_visual_marker(text: str) -> bool:
    """True if the utterance contains a deictic / visual-reference marker."""
    return bool(_MARKER_RE.search(text or ""))


def should_attach_screenshot(text: str, *, is_smalltalk: bool) -> bool:
    """Decide whether to attach the screenshot for this turn (skip-when-safe).

    Returns True (attach) for every turn EXCEPT confidently text-only ones:
    a smalltalk / simple-Q&A turn with no visual-reference marker. When in
    doubt the image is kept — the latency win is taken only where it is clearly
    safe, never at the cost of the router going blind on a real screen question.
    """
    if not is_smalltalk:
        return True
    return has_visual_marker(text)
