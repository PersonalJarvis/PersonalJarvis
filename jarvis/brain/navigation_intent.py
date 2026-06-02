"""Deterministic UI-navigation intent gate (brain side).

``match_navigation_intent("zeig die Socials")`` → ``"socials"``. Used by
``BrainManager.generate()`` to move the desktop UI to a sidebar section BEFORE
the capability gate and the force-spawn heuristic — navigation is a "dumb",
deterministic action (AD-OE3), and routing it through the LLM/spawn path is both
unreliable and wrong (the capability gate would refuse "zeig die Socials"
because 'social' is an external-integration marker).

Conservative by design: a navigation cue AND a known section are both required,
so unrelated utterances never hijack the UI. Pure regex, no LLM, no IO
(AP-9/AP-11). The section vocabulary is shared with the ``navigate`` tool
(``SECTION_PHRASES``), so the two never drift.
"""
from __future__ import annotations

import re

from jarvis.plugins.tool.navigate import SECTION_PHRASES

# Navigation cues (DE + EN). A bare section mention without one of these never
# navigates ("was kann ich in den Einstellungen ändern" must NOT jump).
_NAV_CUE = re.compile(
    r"\b(?:"
    r"zeig(?:e|st|t|s)?(?:\s+mir)?"
    r"|öffne|oeffne|öffnen|aufmachen"
    r"|geh(?:e)?\s+(?:zu|auf|in|zur|zum)"
    r"|wechs(?:le|el|elt|eln)?\s+(?:zu|auf|in|zur|zum)"
    r"|navigier(?:e|st|t)?\s+(?:zu|auf)"
    r"|bring\s+mich\s+(?:zu|auf|in|zur|zum)"
    r"|spring(?:e)?\s+(?:zu|auf|in|zur|zum)"
    r"|go\s+to|open|show(?:\s+me)?|switch\s+to|navigate\s+to|take\s+me\s+to|jump\s+to"
    r")\b",
    re.I,
)

# Longest phrase first so "social media" beats "social", "cli test hub" wins, etc.
_PHRASES: tuple[tuple[str, str], ...] = tuple(
    sorted(SECTION_PHRASES.items(), key=lambda kv: -len(kv[0]))
)


def match_navigation_intent(text: str) -> str | None:
    """Return the canonical section id for a clear navigation command, else None."""
    t = " ".join((text or "").strip().lower().split())
    if not t or not _NAV_CUE.search(t):
        return None
    for phrase, section_id in _PHRASES:
        # Word boundaries so 'board' does not match inside 'keyboard'.
        if re.search(r"\b" + re.escape(phrase) + r"\b", t):
            return section_id
    return None
