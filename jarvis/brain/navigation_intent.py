"""Deterministic UI-navigation intent gate (brain side).

``match_navigation_intent("zeig die Socials")`` → ``"socials"``. Used by
``BrainManager.generate()`` to move the desktop UI to a sidebar section BEFORE
the capability gate and the force-spawn heuristic — navigation is a "dumb",
deterministic action (AD-OE3), and routing it through the LLM/spawn path is both
unreliable and wrong (the capability gate would refuse "zeig die Socials"
because 'social' is an external-integration marker).

Conservative by design: a navigation cue AND a known section are both required,
AND they must belong to the same clause — see ``_binds`` below. Pure regex, no
LLM, no IO (AP-9/AP-11). The section vocabulary is shared with the ``navigate``
tool (``SECTION_PHRASES``), so the two never drift.

Why the binding rule exists (live failure 2026-07-29 17:04, BUG-121). "Kannst
du mal bitte Terminal T7 prompten, … wieso das Resuming Feature … nur bei claude
Code Sessions funktioniert und nicht bei Codec Sessions oder bei Open Codes oder
bei anderen Sessions …" navigated the sidebar to *Sessions*: the cue ``open``
came from the CLI product name "Open Code" at offset 269, the section word
``sessions`` from a bug description at offset 300, and the two had nothing to do
with each other. Because this gate runs ahead of the Agentic-IDE fast path, the
turn returned "Opening Sessions." and the pane the user actually addressed was
never briefed — while the live model narrated a briefing that never happened.
<!-- i18n-allow: quoted spoken transcript of the failing utterance -->

This is the same defect class as BUG-120's reply-language gate: ingredients
matched independently across a long utterance with no proximity requirement.
The defense is the same too — bind every ingredient to the cue.
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

# A real navigation command puts the target right behind the verb: "zeige mir
# die Socials", "geh zu den Aufgaben", "show me the agents". What sits between
# them is an article and at most a filler word, never a clause. Measured against
# every phrasing this gate is meant to serve, three words is generous.
_MAX_GAP_WORDS = 3

# Words that end a clause. Their presence between the cue and the section word
# proves the two belong to different statements, whatever the distance —
# "open codes ODER bei anderen sessions" is a comparison, not an order.
# Matching *input vocabulary* across the supported locales, not prose.
_BREAK_WORDS = frozenset(
    {
        "und", "oder", "aber", "sondern", "weil",  # i18n-allow: input vocab
        "dass", "ob", "wenn", "nicht", "kein",  # i18n-allow: input vocab
        "keine", "keinen", "warum", "wieso",  # i18n-allow: input vocab
        "funktioniert",  # i18n-allow: input vocab
        "and", "or", "but", "because", "that", "if", "whether", "not", "why",
        "y", "o", "pero", "porque", "que", "si", "no",  # i18n-allow: input vocab
    }
)

# Sentence enders. A cue in one sentence can never target a word in the next.
_SENTENCE_BREAK = re.compile(r"[.!?;]")


def _binds(gap: str) -> bool:
    """True when the text between a cue and a section word keeps them one command.

    The gap is what the user said between the navigation verb and the section
    name. A command's gap is an article or two; anything longer, anything that
    crosses a sentence, and anything containing a clause-opening word means the
    two matches are unrelated and this gate must not claim the turn.
    """
    if _SENTENCE_BREAK.search(gap):
        return False
    words = gap.split()
    if len(words) > _MAX_GAP_WORDS:
        return False
    return not any(word.strip(",") in _BREAK_WORDS for word in words)


def match_navigation_intent(text: str) -> str | None:
    """Return the canonical section id for a clear navigation command, else None.

    Both a navigation cue and a known section must appear, the section must come
    AFTER the cue (navigation is always verb-then-target), and the two must be
    bound to each other by ``_binds``. A section word that merely occurs
    somewhere in a long utterance never moves the UI.
    """
    t = " ".join((text or "").strip().lower().split())
    if not t:
        return None
    cues = [match.end() for match in _NAV_CUE.finditer(t)]
    if not cues:
        return None
    for phrase, section_id in _PHRASES:
        # Word boundaries so 'board' does not match inside 'keyboard'.
        for hit in re.finditer(r"\b" + re.escape(phrase) + r"\b", t):
            if any(
                cue_end <= hit.start() and _binds(t[cue_end : hit.start()])
                for cue_end in cues
            ):
                return section_id
    return None
