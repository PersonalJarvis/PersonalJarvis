"""Visual-reference vision gate (Hybrid — attach-only-on-reference).

The router runs text-only by default. A screenshot is attached ONLY when the
utterance clearly refers to the screen (deictic pointer, screen noun, look/click
verb, read-out/diagnosis). Inverted from the old skip-when-safe default, which
attached on every non-smalltalk turn and let a fresh screenshot dominate the
model's attention over the conversation history (user asked "what did we just
discuss?" and got a screen-based answer).

The on-demand screenshot tool (wired in tool_use_loop, Wave 2) is the safety net
for screen references the markers miss, so the router never goes blind on a real
screen question (anti-regression vs. the 2026-04-28 blank-desktop hallucination).
"""
from __future__ import annotations

import re

# Visual-reference markers (DE + EN). Substring matching is intentional
# ("klick" also catches "anklicken", "schau" catches "anschauen"). Markers are
# kept specific on purpose: a false negative is recoverable (the brain can call
# the screenshot tool), a false positive re-introduces the per-turn image tax
# this change exists to remove. Deliberately NOT included: bare "tab", "dort",
# "warum ist das" (without a colour), "was ist das" (without "hier") — too broad,  # i18n-allow: quoted German input examples deliberately excluded from the matcher
# they fire on non-visual turns.
_VISUAL_MARKERS: tuple[str, ...] = (
    # deictic / pointing
    "das hier", "das da", "hier auf", "da auf", "hier oben", "hier unten",  # i18n-allow: German visual-reference input-matching vocabulary
    "hier links", "hier rechts", "hier im", "hier in der",
    # look / show verbs
    "schau", "sieh", "siehst", "guck", "zeig mir", "zeig mal",
    # screen / window / page nouns
    "auf dem bildschirm", "am bildschirm", "im bild", "auf dem screen",
    "bildschirm", "dieses fenster", "das fenster", "diese seite",  # i18n-allow: German visual-reference input-matching vocabulary
    "die seite hier", "fehlermeldung", "knopf", "button", "menü", "menue",  # i18n-allow: German visual-reference input-matching vocabulary
    # "dialog" alone is a false positive in DE (= a conversation); require the
    # UI sense explicitly. Missed UI dialogs are caught by the on-demand tool.
    "dialogfeld", "dialog box",
    # actions on the screen
    "klick", "markier", "scroll", "öffne das", "oeffne das", "mach das zu",  # i18n-allow: German visual-reference input-matching vocabulary
    "mach das fenster", "schließ das fenster", "schliess das fenster",  # i18n-allow: German visual-reference input-matching vocabulary
    # spatial screen references — a quadrant/position implies "on the screen"
    "oben links", "oben rechts", "unten links", "unten rechts",
    "links oben", "rechts oben", "links unten", "rechts unten",
    "da oben", "da unten",
    # diagnosis / read-out — what is written there. "steht da"/"da steht" are
    # screen read-outs; "steht an" (scheduled) deliberately does NOT match.
    "warum ist das rot", "warum ist das grau", "warum ist das blau",  # i18n-allow: German visual-reference input-matching vocabulary
    "was steht da", "was steht hier", "steht da", "da steht", "steht oben",
    "steht hier", "steht dort", "was ist das hier", "lies", "vorlesen",  # i18n-allow: German visual-reference input-matching vocabulary
    "fehlermeldung vor",
    # English
    "this here", "that there", "look at", "see this", "on screen",
    "on the screen", "this window", "what's this", "what is this", "click",
    "the screen", "read this", "this error", "this button", "this page",
)

_MARKER_RE = re.compile("|".join(re.escape(m) for m in _VISUAL_MARKERS), re.IGNORECASE)


def has_visual_marker(text: str) -> bool:
    """True if the utterance contains a deictic / visual-reference marker."""
    return bool(_MARKER_RE.search(text or ""))


def should_attach_screenshot(text: str, *, is_smalltalk: bool = False) -> bool:
    """Decide whether to attach the screenshot for this turn (attach-on-reference).

    Returns True ONLY when the utterance clearly refers to the screen. A plain
    content question — even a non-smalltalk one — gets NO screenshot, so the
    conversation history stays the model's primary context. The on-demand
    screenshot tool is the fallback for references the markers miss.

    ``is_smalltalk`` is accepted for backward compatibility with the existing
    call site but no longer forces attachment; the decision is the visual-marker
    signal alone.
    """
    return has_visual_marker(text)
