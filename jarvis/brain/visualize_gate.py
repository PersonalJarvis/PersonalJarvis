"""Explicit-request gate for the ``visualize`` tool (ask-only, never ambient).

Drawing a picture is the one capability a user must be able to switch on with
their own words. An assistant that decides on its own when an answer "would be
clearer as a diagram" produces a stream of pictures nobody asked for, and every
one of them costs a tool call, a rendered artifact on disk, and a jump of the
UI to another section. So the tool is not merely *discouraged* ambiently — it is
withheld from the model's tool set on every turn that did not ask for it (see
``BrainManager._hide_visualize_tool_without_request``), which also keeps its
schema out of the request on the ~99% of turns that are about something else.

Regex-only, provider-agnostic, no model in the detection path (AP-11): the gate
runs on every single turn, so an LLM here would tax exactly the turns this
module exists to keep cheap.

Three rules, in order:

1. A **navigation** utterance is never a request to draw. "Zeig mir die
   Visualisierungen" opens the section that lists what already exists — the
   ``navigate`` tool owns that, and it must not be shadowed by a tool that
   would produce a brand-new picture instead of showing the old ones.
2. A **definition question** ("was ist eine Visualisierung?") is answered with
   words. The word appearing in a question about the word is not a request.
3. Otherwise: an explicit drawing VERB on its own, or a build/show verb
   together with a visual NOUN, is a request.

A false negative is cheap and self-correcting — the user says "visualisier mir
das" and gets it on the next turn. A false positive is the whole problem this
module was written for, so every pattern here is deliberately narrow.

Every literal below is input-matching vocabulary in the user's spoken languages
(DE/EN/ES), which the language policy allows on the input surface.
"""

from __future__ import annotations

import re

# --- 1. Navigation to the existing gallery — never a request to draw ---------
# "zeig mir die Visualisierungen", "open the visualization section". The
# negative lookahead keeps a genuine request that happens to start with a nav
# verb: "zeig mir eine Visualisierung VON den Zahlen" still wants a new picture.
_NAV_VERBS = (
    r"öffne|oeffne|geh(?:e)?\s+(?:mal\s+)?(?:zu|in)|"  # i18n-allow: input vocab
    r"wechs(?:le|el)\s+(?:zu|in)|navigier\w*\s+zu|zeig(?:e|s)?|"  # i18n-allow: input vocab
    r"open|go\s+to|switch\s+to|navigate\s+to|show|"
    r"abre|ve\s+a|muestra(?:me)?"  # i18n-allow: input vocab
)
_NAV_ARTICLES = (
    r"(?:mir\s+|me\s+)?(?:die|den|das|the|la|el|los|las)?"  # i18n-allow: input vocab
)
_NAV_SECTION_NOUNS = (
    r"visualisierung(?:s\w*)?(?:en)?|"  # i18n-allow: input vocab
    r"visualiz(?:ation|aciones|aci[oó]n)s?|visualisations?|visuals"
)
_NAV_SUFFIX = (
    r"(?:\s*[-–]?\s*"
    r"(?:section|bereich|sektion|tab|board|ansicht|view))?"  # i18n-allow: input vocab
)
# A following "of/for/about" turns the noun back into the THING being asked for.
_NAV_NOT_FOLLOWED_BY = (
    r"(?!\s*(?:von|vom|für|fuer|davon|dazu|hiervon|of|for|about|de|del))"  # i18n-allow: input vocab
)
_NAVIGATION_RE = re.compile(
    rf"\b(?:{_NAV_VERBS})\s+{_NAV_ARTICLES}\s*"
    rf"(?:{_NAV_SECTION_NOUNS}){_NAV_SUFFIX}\b{_NAV_NOT_FOLLOWED_BY}",
    re.IGNORECASE,
)

# --- 2. A question ABOUT the word, not a request for the thing ---------------
_DEFINITION_RE = re.compile(
    r"\b(?:"
    r"was\s+(?:ist|sind|bedeutet|heißt|heisst)|"  # i18n-allow: input vocab
    r"erkl[äa]r\w*\s+mir\s+was|"  # i18n-allow: input vocab
    r"what\s+(?:is|are|does)|explain\s+what|"
    r"qu[ée]\s+(?:es|son|significa)"  # i18n-allow: input vocab
    r")\b",
    re.IGNORECASE,
)

# --- 3a. Drawing verbs strong enough on their own ----------------------------
# "visualisier mir das", "visualize this", "mach eine Mindmap draus". Each of
# these names the ACT of drawing; no second signal is needed. Stems are matched
# so every inflection is covered ("visualisiere", "visualizing", "visualízalo").
_EXPLICIT_RE = re.compile(
    r"\b(?:"
    # One stem for all three languages and every inflection, accents included:
    # visualisieren, visualize, visualising, visualízamelo.
    r"visual[ií]\w*|"  # i18n-allow: input vocab
    r"veranschaulich\w*|skizzier\w*|"  # i18n-allow: input vocab
    r"schaubild|flussdiagramm|ablaufdiagramm|organigramm|"  # i18n-allow: input vocab
    r"mindmap|mind\s+map|flowchart|flow\s+chart|org\s+chart|"
    r"esquematiza\w*|diagrama\s+de\s+flujo"  # i18n-allow: input vocab
    r")\b",
    re.IGNORECASE,
)

# --- 3b. Build/show verb + visual noun — a request only in combination -------
# "diagramm" or "chart" alone is ordinary conversation ("der Chart ist rot").
# Paired with a verb that PRODUCES or DISPLAYS something, it is a request.
_VERB_RE = re.compile(
    r"\b(?:"
    r"mach|mache|erstell\w*|bau\w*|zeichne|zeichnen|mal|male|"  # i18n-allow: input vocab
    r"stell\w*|gib\s+mir|zeig\w*|erkl[äa]r\w*|fass\w*|"  # i18n-allow: input vocab
    r"make|create|build|draw|sketch|render|turn\s+(?:it|this|that)\s+into|"
    r"give\s+me|show|explain|summari[sz]e|map\s+out|"
    # "chart"/"graph"/"plot" are nouns as often as verbs, and _NOUN_RE already
    # matches them. Without an object they would let ONE word play both halves
    # of the combination and fire on "wie ist der bitcoin chart gerade".
    r"(?:plot|chart|graph|map)\s+(?:it|this|that|these|those|the)|"
    r"haz(?:me)?|crea|dibuja|muestra(?:me)?|resume"  # i18n-allow: input vocab
    r")\b",
    re.IGNORECASE,
)

_NOUN_RE = re.compile(
    r"\b(?:"
    r"diagramm\w*|grafik\w*|schaubild\w*|zeitstrahl|"  # i18n-allow: input vocab
    r"bildlich|grafisch|visuell|anschaulich|als\s+bild|"  # i18n-allow: input vocab
    r"diagram|chart|graphic|timeline|infographic|"
    r"visually|graphically|as\s+(?:a\s+)?(?:picture|image|drawing)|"
    r"diagrama|gr[áa]fic[oa]s?|l[íi]nea\s+de\s+tiempo|"  # i18n-allow: input vocab
    r"visualmente|gr[áa]ficamente"  # i18n-allow: input vocab
    r")\b",
    re.IGNORECASE,
)


def wants_visualization(text: str) -> bool:
    """True when the utterance explicitly asks for a picture to be drawn.

    The single decision point for offering the ``visualize`` tool at all. See
    the module docstring for the three rules and why each one is narrow.
    """
    t = (text or "").strip()
    if not t:
        return False
    if _NAVIGATION_RE.search(t):
        return False
    if _DEFINITION_RE.search(t):
        return False
    if _EXPLICIT_RE.search(t):
        return True
    return bool(_VERB_RE.search(t) and _NOUN_RE.search(t))


__all__ = ["wants_visualization"]
