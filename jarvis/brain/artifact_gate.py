"""Explicit-request gate for the ``create_artifact`` tool (ask-only, never ambient).

Building an artifact — a page the user looks at and keeps — is the one
capability a user must switch on with their own words, literally. An
assistant that decides on its own when an answer "would be clearer as a page"
produces a stream of artifacts nobody asked for, and every one of them costs
a background mission on the strongest model, a file in the archive, and a
jump of the UI to another section. So the tool is not merely *discouraged*
ambiently — it is withheld from the model's tool set on every turn that did
not ask for it (see ``BrainManager._hide_artifact_tool_without_request``),
which also keeps its schema out of the request on the ~99% of turns that are
about something else.

The second explicit path is the composer Add menu: pinning the artifact
capability there hands the tool to the turn through the override's extra
hands (applied AFTER this gate), so no word is needed in that case. This
module only judges the words.

Regex-only, provider-agnostic, no model in the detection path (AP-11): the
gate runs on every single turn, so an LLM here would tax exactly the turns
this module exists to keep cheap.

Three rules, in order:

1. A **navigation** utterance is never a request to build. "Zeig mir die
   Artefakte" opens the section that lists what already exists — the
   ``navigate`` tool owns that, and it must not be shadowed by a tool that
   would produce a brand-new page instead of showing the old ones.
2. A **definition question** ("was ist ein Artefakt?") is answered with
   words. The word appearing in a question about the word is not a request.
3. Otherwise: only the artifact said BY NAME ("Artefakt", "Artifact",
   "Artefacto") is a request. Visual verbs and page shapes on their own
   ("visualisier", "Dashboard", "Diagramm", "mach mir eine Seite dazu")
   are NOT requests — the user says the literal word or pins it via Add.

A false negative is cheap and self-correcting — the user says "mach ein
Artefakt draus" and gets it on the next turn. A false positive is the whole
problem this module was written for, so the pattern here is deliberately
a single noun.

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
_NAV_ARTICLES = r"(?:mir\s+|me\s+)?(?:die|den|das|the|la|el|los|las)?"  # i18n-allow: input vocab
_NAV_SECTION_NOUNS = (
    r"visualisierung(?:s\w*)?(?:en)?|"  # i18n-allow: input vocab
    r"visualiz(?:ation|aciones|aci[oó]n)s?|visualisations?|visuals|"
    r"artefakt\w*|artifact\w*|artefact\w*|artefactos?"  # i18n-allow: input vocab
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

# --- 3. The artifact said by name — the only word that builds ---------------
# "mach ein Artefakt draus", "build me an artifact", "hazme un artefacto".
# Nothing else builds: "visualisier", "Dashboard", "Diagramm", "Seite",
# "Bericht" are ordinary conversation until the user says the literal word
# or pins the capability via the Add menu (which bypasses this gate through
# the turn override's extra hands, applied after it).
_EXPLICIT_RE = re.compile(
    r"\b(?:"
    r"artefakt\w*|artifact\w*|artefact\w*|artefactos?"  # i18n-allow: input vocab
    r")\b",
    re.IGNORECASE,
)


def wants_artifact(text: str) -> bool:
    """True when the utterance literally names an artifact to be built.

    The single WORD decision point for offering the ``create_artifact`` tool
    at all. See the module docstring for the three rules. The Add-menu pin
    is the second path and bypasses this function through the turn
    override's extra hands.
    """
    t = (text or "").strip()
    if not t:
        return False
    if _NAVIGATION_RE.search(t):
        return False
    if _DEFINITION_RE.search(t):
        return False
    return bool(_EXPLICIT_RE.search(t))


__all__ = ["wants_artifact"]
