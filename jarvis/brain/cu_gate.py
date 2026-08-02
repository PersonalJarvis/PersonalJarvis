"""Deterministic explicit-desktop gate for LLM-chosen computer_use calls.

Live incident 2026-07-21 11:36 (voice session 06a65611): the user asked a pure
knowledge question — "braucht die Golf 100 Start- und  # i18n-allow: forensic quote
Landebahn" (what runway does the Gulfstream G100 need)  # i18n-allow: quote cont.
— the realtime layer correctly delegated the turn to the router brain, and the
router brain then called ``computer_use``: it physically opened Safari on the
user's screen and typed "Gulfstream G100 runway requirements" into Google.
A web lookup the tool model can do invisibly (own knowledge or ``search_web``)
hijacked the user's live desktop for ~35 seconds.

This is the spawn-gate lesson applied to the desktop (see
``jarvis/brain/spawn_gate.py``, maintainer mandate 2026-07-18): a tool
description is advice, not enforcement. This module is the enforcement. An
LLM-initiated ``computer_use`` call executes ONLY when one of these holds:

1. The CURRENT user turn explicitly names the desktop vehicle — an on-screen
   action verb ("open", "click", "type", "scroll", ...) or a screen/app/
   browser noun ("Chrome", "terminal", "Bildschirm",  # i18n-allow: vocab token
   ...), in any supported language. Matching *input vocabulary*, not prose —
   deliberately word-based, mirroring the spawn gate.
2. The conversation is still inside a desktop episode: a Computer-Use mission
   is active or finished within the follow-up window
   (``cu_run_registry.has_recent_run``). This keeps the BUG-105 corrective
   follow-ups alive ("try again", "do it in my Chrome browser" — the latter
   also matches rule 1) without letting a cold research question start one.

**Looking is not operating (maintainer mandate 2026-08-02).** A surface NOUN is
not a command: "what is on my screen?" names the screen without asking for
anything to be done to it, and it used to pass rule 1 on the bare noun alone —
so a question that Screen Context answers with one screenshot instead started a
desktop mission that moved the user's mouse. The vocabulary is therefore split
into ACTION verbs and SURFACE nouns, and a turn that
``jarvis.screen_context.intent`` reads as a look request is refused even when it
names a surface and even inside a live desktop episode. Only an action verb, an
explicitly named Computer-Use, or a surface noun in a turn that is NOT a look
request may still drive the desktop. This gate is the enforcement half; the
other half is that a successful Screen Context capture strips every tool from
the turn (``BrainManager.generate``), so the two paths can never both run.

Everything else is blocked and fed back to the model as a tool error telling
it to answer inline (or via ``search_web`` for fresh facts). Over-ALLOWING is
safe by construction — a match only means the MODEL MAY drive the desktop, it
never forces it. The deterministic local-action fast path
(``jarvis/brain/local_action_gate.py``) does NOT run through this gate — it
already fires only on explicit action grammar.

Consumers: ``jarvis.brain.tool_use_loop`` (classic pipeline + realtime
delegate mode) and ``jarvis.realtime.tools`` (realtime direct tool mode).
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)


#: Registered names of every router tool that drives the live desktop.
#: Kept tiny and explicit — mirrors ``SPAWN_VEHICLE_TOOL_NAMES`` in
#: ``jarvis.brain.spawn_gate``.
CU_VEHICLE_TOOL_NAMES: frozenset[str] = frozenset({"computer_use"})


#: How long after a Computer-Use mission a vehicle-free follow-up may still
#: re-drive the desktop. Matches the registry's "recently finished" context
#: bound — the same window BUG-105 uses to describe prior missions.
FOLLOW_UP_WINDOW_S: float = 15 * 60.0


# German umlauts are transliterated to their digraphs before matching, exactly
# like jarvis/brain/turn_planner.py, so the vocabulary below is written in
# digraph form ("oeffn", "drueck").
_UMLAUT_TRANSLITERATION = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}  # i18n-allow: umlaut mapping data
)


# Tech proper names and terms of art that merely CONTAIN a vehicle token.
# Masked out of the turn before the vehicle match runs, so a product name can
# never read as an on-screen command. Live incident 2026-07-27 11:52 (voice
# session 57cc5f5f): "…BGE-M3 und Gemini oder Open AI  # i18n-allow: live quote
# Embedding 3 Large, was der Unterschied?" — a pure  # i18n-allow: quote cont.
# model-comparison question. The provider name
# "Open AI" matched the English open-verb, the gate allowed, and the router's
# computer_use call took a screenshot and started driving the desktop.
#
# This is the same defect class the local-action gate fixed on 2026-07-10
# ("OpenRouter-Zugang checkst" read as "open X and Y"); the fix never reached
# this gate. Masking beats inlining negative lookaheads: the vehicle pattern
# below is already dense, and every entry here carries its own reason.
#
# The mask is deliberately narrow — only multi-token names where the vehicle
# token is provably part of the NAME. A bare "Edge"/"Windows"/"Programm" stays
# a vehicle noun, because "mach den Edge zu" is a real desktop command.
_PRODUCT_NAME_NOISE_RE: re.Pattern[str] = re.compile(
    # "open" as a brand/license prefix, never the imperative verb. Written
    # with an optional space so both "openai" and "Open AI" are covered.
    r"\bopen[-\s]?(?:ai|router|source|weights?|models?|web[-\s]?ui|interpreter"
    r"|claw\w*|wake[-\s]?word|cv|ssl|vino|telemetry|street[-\s]?map)\b"
    # "window" as a model/attention term, never the desktop object.
    # i18n-allow: German/Spanish speech-input matching data
    r"|\b(?:context|kontext|contexto|sliding|rolling|attention|token)"
    r"[-\s]?(?:windows?|ventanas?)\b"
    # "edge" as an engineering term, never the browser.
    r"|\bedge[-\s]?(?:cases?|computing|functions?|runtimes?|deployments?)\b"
    r"|\bcutting[-\s]?edge\b",
    re.IGNORECASE,
)


# Explicit desktop-vehicle vocabulary (DE/EN/ES) — speech-input matching data.
#
# Split into ACTION verbs and SURFACE nouns because the two carry different
# authority: a verb asks for something to be DONE, a noun only names where. A
# noun alone used to be enough, which is how "what is on my screen?" started a
# desktop mission (see the module docstring).
#
# Deliberate exclusions (each one is a live trap):
# * bare "start": the incident utterance itself contains the German NOUN
#   "Start- und Landebahn" (runway) —  # i18n-allow: names the German noun under exclusion
#   only the conjugated imperatives ("starte", "startet", "starten") count.
# * bare "type": "what type of runway ..." is a question form; "type" counts
#   only when not preceded by a question/genitive word.
# * "google"/"search"/"such": search intent belongs to search_web, never to
#   the desktop — "google das im Browser" still passes via "browser".
# * bare "open\w*": it swallowed every "Open…" product name as an open-verb
#   (see _PRODUCT_NAME_NOISE_RE). Pinned to the real English conjugations,
#   exactly like ``_OPEN_VERB_RE`` in jarvis/brain/local_action_gate.py.
_DESKTOP_ACTION_RE: re.Pattern[str] = re.compile(
    r"(?:"
    # --- action verbs (en) ---
    r"\bopen(?:s|ed|ing)?\b|\blaunch\w*\b|\bclick\w*\b|\bdouble-?click\w*\b|\btap\b"
    r"|\bscroll\w*\b|\bdrag\w*\b|\bhover\b|\bpress\w*\b|\bpaste\b"
    r"|\bminimi[sz]e\b|\bmaximi[sz]e\b|\bnavigate\b|\bbrowse\b"
    r"|\brefresh\w*\b|\breload\w*\b"
    r"|\blog\s?in\b|\bsign\s?in\b|\bgo\s+to\b"
    r"|(?<!what )(?<!which )(?<!of )\btype\b"
    r"|\bstart(?:s|ed|ing)?\s+(?:up\s+)?(?:the|a|an|my)\b"
    # --- action verbs (de) ---  # i18n-allow: German speech-input matching data
    r"|\boeffn\w*|\baufmach\w*|\bklick\w*|\btipp\w*|\bscroll\w*"
    r"|\bzieh\w*\b|\bdrueck\w*|\bschliess\w*|\bnavigier\w*|\beinfueg\w*"
    r"|\bstart(?:e|et|en)\b"
    r"|\baktualisier\w*\b|\bneu\s+lad\w*\b"
    r"|\bmach\w*\b[^.?!]{0,40}\b(?:auf|zu)\b"
    r"|\bgeh\w*\s+(?:auf|zu|in)\b"
    r"|\blogg\w*\b"
    # --- action verbs (es) ---  # i18n-allow: Spanish speech-input matching data
    r"|\babr(?:e|a|as|ir)\b|\bcli(?:c|ca|quea)\b|\bpincha\w*|\bpulsa\w*"
    r"|\btecle\w*|\bdesplaz\w*|\barrastr\w*|\bpega\b|\bcierr\w*|\bnaveg\w*"
    r"|\bactualiz\w*|\brecarg\w*"
    r")",
    re.IGNORECASE,
)

# Naming the harness itself is the strongest possible desktop signal — the
# maintainer mandate is "Computer-Use only when Computer-Use is asked for", and
# this is that ask, spelled out. Mirrors ``_EXPLICIT_COMPUTER_USE_RE`` in
# jarvis/screen_context/intent.py, which stands Screen Context down for the
# same phrases.
_EXPLICIT_HARNESS_RE: re.Pattern[str] = re.compile(
    r"\bcomputer[-\s]?use\b",
    re.IGNORECASE,
)

_DESKTOP_SURFACE_RE: re.Pattern[str] = re.compile(
    r"(?:"
    # --- screen/app/browser nouns (en/de/es share most brand tokens) ---
    r"\bscreen\w*|\bdesktop\b|\bbrowser\w*|\bchrome\b|\bsafari\b"
    r"|\bfirefox\b|\bedge\b|\btabs?\b|\bwindows?\b|\bmouse\b|\bcursor\w*"
    r"|\bkeyboard\b|\bterminal\w*|\bapps?\b|\bapplications?\b"
    r"|\bprogram(?:me?s?)?\b|\bwebsites?\b|\bwebpages?\b|\burls?\b"
    r"|\bbuttons?\b|\bmenus?\b|\btaskbar\b|\bdock\b|\bfinder\b"
    r"|\bexplorer\b|\bnotepad\b|\baddress\s+bar\b"
    # i18n-allow: German speech-input matching data (nouns)
    r"|\bbildschirm\w*|\bfenster\b|\bmaus\b|\bmauszeiger\w*|\btastatur\w*"
    r"|\banwendung\w*|\bprogramm\w*|\bwebseite\w*|\bschaltflaech\w*"
    r"|\bmenue\w*|\btaskleiste\w*|\badresszeile\w*"
    # i18n-allow: Spanish speech-input matching data (nouns)
    r"|\bpantalla\w*|\bescritorio\b|\bnavegador\w*|\bpestan\w*"
    r"|\bventana\w*|\braton\b|\bteclado\b|\baplicacion\w*|\bboton\w*"
    r")",
    re.IGNORECASE,
)


def _normalized(text: str) -> str:
    """Casefold + transliterate, then blank out tech proper names.

    The mask runs BEFORE the vehicle match so a product name ("Open AI",
    "context window", "edge case") can never be read as an on-screen command.
    It substitutes a space, not the empty string, so the surrounding words keep
    their boundaries and a genuine command in the same turn still matches.
    """
    folded = (text or "").casefold().translate(_UMLAUT_TRANSLITERATION)
    return _PRODUCT_NAME_NOISE_RE.sub(" ", folded)


def _is_look_request(user_text: str) -> bool:
    """Whether this turn asks Jarvis to LOOK at the screen rather than use it.

    Delegated to ``jarvis.screen_context.intent`` instead of re-derived here,
    for the same reason the turn planner delegates it: two vocabularies for one
    question drift apart, and the half that drifts decides whether the user's
    mouse starts moving. That module is pure regex over the utterance — no IO,
    no model call — so it keeps this gate's promise on the voice hot path.

    A classifier defect must not brick desktop automation, so a raised
    exception answers "not a look request" and leaves the old behaviour intact.
    """
    try:
        from jarvis.screen_context.intent import (  # noqa: PLC0415
            classify,
            requests_screen_operation,
        )
        from jarvis.screen_context.models import VisualIntent  # noqa: PLC0415

        if requests_screen_operation(user_text):
            return False
        return classify(user_text).intent is not VisualIntent.NONE
    except Exception:  # noqa: BLE001 — the gate must never crash a tool turn
        log.warning(
            "cu_gate: visual-intent probe failed — treating the turn as a "
            "non-look turn",
            exc_info=True,
        )
        return False


def llm_computer_use_allowed(user_text: str) -> bool:
    """May an LLM-chosen ``computer_use`` call execute for this user turn?

    True when the turn asks for an on-screen ACTION, names the Computer-Use
    harness itself, names a desktop surface without asking to look at it, or
    when the conversation is still inside a recent desktop episode (BUG-105
    corrective follow-ups).

    False for a look request ("what is on my screen?", "read this window to
    me") even when it names a surface and even inside a live desktop episode.
    Answering a question by driving the desktop is the defect this rule exists
    for; Screen Context answers those turns with one short-lived screenshot.

    An EMPTY turn fails OPEN: this gate exists to stop question-shaped voice
    turns from hijacking the screen, and non-conversational launch routes
    (scheduled missions, REST) may reach the loop without a user utterance —
    blocking those would brick legitimate desktop automation.
    """
    normalized = _normalized(user_text).strip()
    if not normalized:
        return True
    if _DESKTOP_ACTION_RE.search(normalized) or _EXPLICIT_HARNESS_RE.search(
        normalized
    ):
        return True
    # Checked before the surface nouns AND before the recent-run window: a
    # question about the screen stays a question, whatever else is going on.
    if _is_look_request(user_text):
        log.info(
            "cu_gate: blocked computer_use — this turn asks to LOOK at the "
            "screen, not to operate it"
        )
        return False
    if _DESKTOP_SURFACE_RE.search(normalized):
        return True
    try:
        from jarvis.harness.cu_run_registry import has_recent_run  # noqa: PLC0415
        if has_recent_run(FOLLOW_UP_WINDOW_S):
            return True
    except Exception:  # noqa: BLE001 — the gate must never crash a tool turn
        log.debug("cu_gate: recent-run probe failed", exc_info=True)
    return False


#: Structured tool error the model receives instead of a mission. English —
#: internal steering text, never spoken verbatim (suppress_response tools
#: aside, the model rephrases in the conversation language).
CU_BLOCKED_MODEL_FEEDBACK: str = (
    "computer_use was NOT executed: the user did not ask you to operate the "
    "screen, an app, or the browser — this turn is an information request. "
    "Never drive the user's live desktop to look something up. Answer the "
    "question directly from your own knowledge now, or call search_web first "
    "if it genuinely needs current/volatile facts. computer_use is only for "
    "turns where the user explicitly wants an on-screen action performed "
    "(open/click/type/scroll, a named app, the browser)."
)


__all__ = [
    "CU_BLOCKED_MODEL_FEEDBACK",
    "CU_VEHICLE_TOOL_NAMES",
    "FOLLOW_UP_WINDOW_S",
    "llm_computer_use_allowed",
]
