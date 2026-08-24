"""Deterministic explicit-delegation gate for LLM-chosen agent spawns.

Maintainer mandate 2026-07-18 (voice sessions 08:25 + 08:29): the model kept
starting background agents mid-conversation ("... he could buy a Gulfstream
every day", "I want to figure out where to move next") although the user never
asked for one. Every prior fix was prompt-side (router SPAWN-CRITERIA, the
realtime role directives, the spawn_worker tool description) — and the model
kept ignoring it, because a tool description is advice, not enforcement.

This module is the enforcement. An LLM-initiated spawn tool call executes
ONLY when one of these holds:

1. The CURRENT user turn explicitly requests delegation — it names the agent
   vehicle ("agent", "subagent", "<wake-name> Agent", "worker", "mission",
   "openclaw") or a delegation verb/marker ("spawn", "delegate", "in the
   background"), in any supported language. Matching *input vocabulary*, not
   prose — deliberately word-based (the router's force-spawn triggers work the
   same way).
2. The turn is a short, clear YES to a delegation offer the model made right
   after the gate blocked the previous turn (the model is told to offer
   instead of spawn; the user's confirmation then unlocks exactly one spawn).
3. The EFFORT test (``effort_warrants_delegation``) reads the turn as a
   request for a multi-step artefact — a build/write/refactor brief, or
   research whose deliverable is a file. Added on the maintainer mandate
   2026-08-18 ("the goal is that he just DOES things without being told"),
   because rules 1 and 2 meant Jarvis could never start work on its own
   judgement: "Bau mir eine Website mit Flask und einer Startseite" produced
   an offer, never a website. Available in the ``balanced`` and ``permissive``
   force-spawn modes, OFF in ``strict`` — see ``jarvis.core.config``.

Everything else is blocked and fed back to the model as a tool error telling
it to answer inline. The deterministic force-spawn path
(``BrainManager._should_force_spawn``) does NOT run through this gate — it
already fires only on explicit trigger phrases in strict mode and carries its
own decline/negation guards.

Consumers: ``jarvis.brain.tool_use_loop`` (classic pipeline + realtime
delegate mode) and ``jarvis.realtime.tools`` (realtime direct tool mode).
Both share the ONE module-level offer window because both feed the same
single conversation per process; a mode switch keeps the pending offer.
"""
from __future__ import annotations

import logging
import re
import time

from jarvis.core.config import (
    DEFAULT_FORCE_SPAWN_MODE,
    FORCE_SPAWN_MODE_STRICT,
    normalize_force_spawn_mode,
)

log = logging.getLogger(__name__)


# Registered names of every tool that dispatches a background worker mission.
# Kept tiny and explicit — mirrors ``_SPAWN_TOOL_NAMES`` in
# ``jarvis.brain.manager`` (parity-tested in tests/unit/brain/test_spawn_gate.py).
SPAWN_VEHICLE_TOOL_NAMES: frozenset[str] = frozenset({"spawn_worker", "multi_spawn"})


# Explicit delegation vocabulary (DE/EN/ES). A bare "agent" is deliberately
# included: the user-visible brand is "<wake-name> Agent" (dynamic, §4), so
# "spawn einen Gustav Agent" must match for ANY wake word without resolving
# the live brand. Over-matching is safe by construction — a match only means
# the MODEL MAY spawn, it never forces a spawn.
_DELEGATION_MARKER_RE: re.Pattern[str] = re.compile(
    r"(?:"
    # the vehicle, by name (incl. the dynamic "<wake-name> Agent" brand)
    r"\bagent(?:en|es|e|s)?\b"
    r"|\bsub-?agent\w*"
    r"|\bopen[- ]?claw\w*"
    r"|\bworker\w*|\btrabajador\w*"
    r"|\bmission\w*|\bmisi[oó]n\w*"
    # delegation verbs / markers
    r"|\bspawn\w*"
    r"|\bdelegier\w*|\bdelegate\w*|\bdeleg[aá]\w*"
    r"|\bhintergrund\w*|\bbackground\b|\bsegundo\s+plano\b"
    r")",
    re.IGNORECASE,
)

# A vehicle word that REPORTS what already happened is not a delegation
# request. "It spawned on my other screen, but no problem — could you please
# prompt terminal t1 …" (live 2026-08-06 18:51) carried 'spawned' three words
# in, so the workspace stand-down read a comment about the window that had
# just opened as an order for a background agent, and the deterministic
# fast path refused to type into the very pane the sentence addressed. The
# shape is deliberately narrow — a subject pronoun directly in front of the
# simple past — because a genuine request is imperative and has no such
# subject ("spawn an agent that …", "Alex should spawn sub-agents" both
# stay markers).
_REPORTED_VEHICLE_RE: re.Pattern[str] = re.compile(
    r"\b(?:it|that|this|which|one|es|das|der|die)\s+"  # i18n-allow: spoken input
    r"(?:just\s+|gerade\s+)?"  # i18n-allow: spoken-input vocabulary
    r"(?:spawned|delegated)\b",
    re.IGNORECASE,
)


def _marker_spans(text: str) -> list[tuple[int, int]]:
    """Delegation-marker spans, with reported (past-tense) mentions dropped."""
    reported = [m.span() for m in _REPORTED_VEHICLE_RE.finditer(text)]
    return [
        match.span()
        for match in _DELEGATION_MARKER_RE.finditer(text)
        if not any(start <= match.start() < end for start, end in reported)
    ]


# A delegation offer's confirmation is a SHORT stand-alone yes ("Ja, mach
# das", "yes go ahead"). ``classify_response`` substring-matches, so a long
# sentence that merely CONTAINS a yes-word ("Ja, und erzähl mir mehr  # i18n-allow: counter-example
# über Monaco") must never unlock a spawn — same bound as the  # i18n-allow: counter-example
# realtime answer pull-back (``_DELEGATE_ANSWER_MAX_TOKENS``).
_CONFIRM_MAX_WORDS = 6

# …unless the confirmation NAMES the vehicle, which is evidence the six-word
# cap is only guessing at. The counter-example above is dangerous precisely
# because it confirms and then changes the subject; "just do the sub and then
# do it" confirms and points back at the very thing that was offered. Live
# 2026-08-24 10:24 Turn 5: the user's third request in a row, read as a
# confirm by every classifier, refused on length alone (eight words). Spoken
# turns carry filler ("just", "and then") that written ones do not, so the
# cap has to be generous once the vehicle is on the record.
_CONFIRM_MAX_WORDS_NAMING_VEHICLE = 14

# The clipped form of the vehicle, which the marker pattern deliberately does
# NOT carry: ``_DELEGATION_MARKER_RE`` is shared with the Agentic-IDE turn
# detector (``names_spawn_vehicle``), where a bare "sub" would start stealing
# turns from a workspace pane. Inside an ARMED offer window there is no such
# ambiguity — the subject under discussion is the offered agent — so the
# short form is honoured only here. Word-bounded so "subscribe", "submit",
# "subject" and "substitute" stay untouched.
_SHORT_VEHICLE_RE: re.Pattern[str] = re.compile(r"\bsubs?\b", re.IGNORECASE)

# An offer is only fresh for the immediate follow-up exchange. Voice turns
# arrive within seconds; two minutes comfortably covers a slow "hmm... yes"
# without letting a stale offer unlock a spawn much later in the session.
_OFFER_TTL_S = 120.0


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _is_decline_or_feature_talk(text: str) -> bool:
    """True when the user declines a spawn or talks ABOUT the auto-spawn feature.

    Reuses the battle-tested detectors in ``jarvis.brain.manager`` (negation
    windows, "talk to me directly", "auto-spawn" feature naming). Imported
    lazily to keep this a leaf module (manager → tool_use_loop → here would
    otherwise cycle at import time); on any import fault the gate degrades to
    "no decline detected" — the marker match then merely returns the choice
    to the model, which has read the same negated sentence.
    """
    try:
        from jarvis.brain.manager import (  # noqa: PLC0415
            _is_spawn_decline,
            _is_spawn_feature_reference,
        )
    except Exception:  # noqa: BLE001 — gate must never crash a tool turn
        return False
    return _is_spawn_decline(text) or _is_spawn_feature_reference(text)


def _confirm_verdicts(text: str) -> set[str]:
    """Language-agnostic yes/no verdicts for a short answer turn.

    The gate cannot trust a per-turn language tag (STT mislabels are a known
    class), so the answer is classified under every supported language and the
    verdicts are merged; veto keeps its safety priority at the call site.
    """
    try:
        from jarvis.voice.echo_confirmation import classify_response  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — classifier fault = no confirmation
        return set()
    return {classify_response(text, language=lang) for lang in ("de", "en", "es")}


class DelegationOfferWindow:
    """One-shot confirm window armed by a gate-blocked spawn attempt.

    When the gate blocks, the model is instructed to answer inline and — for
    genuinely heavy tasks — OFFER delegation. The user's short affirmative on
    the following turn must then unlock the spawn although it contains no
    delegation vocabulary of its own. This window carries exactly that state:
    armed with the blocked turn's text, consumed by one confirmed spawn.
    """

    def __init__(self, ttl_s: float = _OFFER_TTL_S) -> None:
        self._ttl_s = ttl_s
        self._armed_text = ""
        self._armed_at = 0.0

    def arm(self, blocked_turn_text: str) -> None:
        self._armed_text = _normalized(blocked_turn_text)
        self._armed_at = time.monotonic()

    def disarm(self) -> None:
        self._armed_text = ""
        self._armed_at = 0.0

    def consume_confirm(self, turn_text: str) -> bool:
        """True exactly once, for a short clear YES within the TTL.

        The turn that armed the window can never confirm itself, a long
        sentence never confirms, and any veto wording closes the window for
        good (declined offers must not linger as an unlockable spawn). A
        confirmation that names the vehicle gets the longer cap — see
        ``_CONFIRM_MAX_WORDS_NAMING_VEHICLE``.
        """
        if not self._armed_text:
            return False
        if (time.monotonic() - self._armed_at) > self._ttl_s:
            self.disarm()
            return False
        norm = _normalized(turn_text)
        if not norm or norm == self._armed_text:
            return False
        verdicts = _confirm_verdicts(norm)
        if "veto" in verdicts:
            self.disarm()
            return False
        names_vehicle = bool(_marker_spans(norm)) or bool(_SHORT_VEHICLE_RE.search(norm))
        cap = _CONFIRM_MAX_WORDS_NAMING_VEHICLE if names_vehicle else _CONFIRM_MAX_WORDS
        if len(norm.split()) > cap:
            return False
        if "confirm" in verdicts:
            self.disarm()
            return True
        return False


# ONE conversation per process (desktop app / headless session), so ONE shared
# window across the classic and realtime paths. Tests reset via ``disarm()``.
OFFER_WINDOW = DelegationOfferWindow()


def names_spawn_vehicle(user_text: str) -> bool:
    """True when the utterance names the background-agent vehicle explicitly.

    Public because the Agentic-IDE turn detector needs the SAME answer this gate
    uses: a workspace terminal may claim a turn only when the user did *not* ask
    for a background agent. Sharing the one pattern keeps "spawn an agent that
    helps Kai" a spawn while "let Kai do it" reaches Kai.
    """
    return bool(_marker_spans((user_text or "").strip()))


def spawn_vehicle_spans(user_text: str) -> list[tuple[int, int]]:
    """Character spans of every explicit spawn-vehicle mention, in order.

    The positional view of ``names_spawn_vehicle``, for the one caller that
    needs to know not merely THAT the vehicle was named but WHERE: the
    Agentic-IDE precedence rule has to tell "spawn an agent that helps Kai"
    (an order to Jarvis, vehicle word first) from "Alex should spawn
    sub-agents" (a description of Alex's work, vehicle word behind the
    call-sign). Both share this ONE pattern so the two answers cannot drift.
    """
    return _marker_spans((user_text or "").strip())


def addressed_pane_blocks_spawn(user_text: str) -> bool:
    """True when this turn is aimed at a workspace terminal, which owns it.

    The scope correction to the first attempt at this (maintainer, 2026-07-28).
    That version blocked EVERY spawn while coding mode was on, which bought the
    fix by deleting a feature: brainstorming inside the IDE and asking for a
    background agent is a legitimate, common thing to do, and the mode is not a
    reason to refuse it.

    What actually decides is the TURN, not the mode. Addressing a pane is an
    instruction to that pane — "sub-agents" inside it is the CLI agent's own
    fan-out vocabulary, describing work that happens once the brief arrives in
    the terminal. A turn that addresses no pane is unaffected, in or out of
    coding mode, so the background agent stays fully available.

    Delegated to ``intent.owns_turn`` rather than re-deciding here: that is the
    ONE precedence rule the router's force-spawn guard and this gate already
    share, and a second opinion would drift.

    Never raises — the workspace is an optional surface and must never be able
    to break spawn routing; a fault answers "does not block".
    """
    try:
        from jarvis.agentic_ide.intent import owns_turn  # noqa: PLC0415

        return owns_turn(user_text)
    except Exception:  # noqa: BLE001 — optional surface, never fatal to routing
        return False


# ──────────────────────────────────────────────────────────────────────────
# The EFFORT route (maintainer mandate 2026-08-18)
# ──────────────────────────────────────────────────────────────────────────
#
# The vocabulary route above asks "did the user name the vehicle?". This one
# asks "is the WORK itself plainly a multi-step artefact?" — a build / write /
# refactor brief, or research whose deliverable is a file. A question, a
# lookup, a chat turn and anything cheap stay inline, as before.
#
# The direction of error is NOT symmetric here, and the whole test is built
# around that asymmetry: an unrequested background agent is expensive,
# surprising and hard to stop (forensic 2026-05-01 — the model hallucinated a
# spawn on the chit-chat turn "es geht ab" and Jarvis then claimed to have
# started tests it never started), while a missed delegation costs one inline
# answer plus the offer the model is told to make. So every stage is
# conjunctive and EVERY doubt resolves to "no spawn":
#
#   1. REQUEST SHAPE   the turn must be an order or a request TO Jarvis — not
#                      a report of finished work, not an information question.
#   2. NOT CHEAP       an explicit small-scope word ("kurz", "quick", "nur",
#                      "einfach") vetoes outright.
#   3. NOT CONVERSATION the manager's existing stand-down detectors
#                      (instructional / opinion / coaching) veto; reused rather
#                      than re-decided, so the effort route cannot drift away
#                      from the force-spawn path.
#   4. DELIVERABLE     a build verb + an artefact noun, a named file, or a
#                      code-work verb + a code object.
#   5. SCOPE           at least one SUBSTANCE signal (two or more named
#                      components, an explicit multi-step marker, or a research
#                      verb next to the deliverable) AND two signals in total.
#
# Where the "unsure" line sits: stage 5. One signal is not enough. "Bau mir
# eine Website" names one component and stops — the model answers inline and
# may offer. "Bau mir eine Website mit Flask und einer Startseite" carries two
# components, a specification and a coordination: three signals, so it goes.
#
# All matching is pure regex over the user's own words (AP-11 safe, no LLM in
# the gate, provider-agnostic) plus the manager's existing detectors.

#: A brief this long is multi-step on its own — but only ever as ONE modifier
#: signal, never on its own (a long chat turn is still a chat turn).
_HEAVY_MIN_WORDS = 12

#: First-person past tense: the user is REPORTING work, not ordering it.
#: ``_BUILD_VERB_RE`` deliberately matches "wrote" / "geschrieben" / "saved"
#: for the artifact classifier, so without this an "I already wrote the report
#: and the summary" turn would read as a build brief.
_REPORTED_WORK_RE: re.Pattern[str] = re.compile(
    r"\b(?:ich|wir|i|we)\s+(?:hab|habe|haben|hatte|hatten|"  # i18n-allow: spoken input
    r"have|had|already)\b"
    r"|\b(?:schon|bereits|already)\s+"  # i18n-allow: spoken input
    r"(?:gebaut|geschrieben|erstellt|built|written|created)\b",  # i18n-allow: spoken input
    re.IGNORECASE,
)

#: An interrogative opener makes the turn a question about the world. Answered
#: inline, whatever artefact nouns it happens to contain ("Wie baue ich eine
#: Website mit Flask und einer Startseite?" is a how-to, not a brief).
_INFO_QUESTION_RE: re.Pattern[str] = re.compile(
    r"^\s*(?:und\s+|aber\s+|okay,?\s+|ok,?\s+|also,?\s+)?"  # i18n-allow: spoken input
    r"(?:was|wie|warum|wieso|weshalb|wann|wo|wer|wem|wen|welch\w*"  # i18n-allow: DE interrogatives
    r"|what|how|why|when|where|who|whom|which"
    r"|qu[eé]|c[oó]mo|cu[aá]ndo|d[oó]nde|qui[eé]n)\b",  # i18n-allow: ES interrogatives
    re.IGNORECASE,
)

#: A polite request frame. A turn ending in "?" is a question UNLESS it is
#: framed as a request ("Kannst du mir … bauen?"), which is how people ask for
#: work out loud in German and English alike.
_REQUEST_FRAME_RE: re.Pattern[str] = re.compile(
    r"\b(?:kannst|k[oö]nntest|w[uü]rdest|bitte"  # i18n-allow: DE request frame
    r"|ich\s+(?:m[oö]chte|will|brauche|br[aä]uchte|h[aä]tte)"  # i18n-allow: DE request frame
    r"|can\s+you|could\s+you|would\s+you|please"
    r"|i\s+(?:want|need)|i'?d\s+like)\b",
    re.IGNORECASE,
)

#: An explicit small-scope word. Vetoes the effort route outright — the user
#: said the job is small, and a background agent for a small job is exactly
#: the surprise this gate exists to prevent. Deliberately generous (the common
#: German fillers "nur" and "einfach" are in here): over-vetoing costs an
#: inline answer, under-vetoing costs an unwanted mission.
_CHEAP_SCOPE_RE: re.Pattern[str] = re.compile(
    r"\b(?:kurz\w*|klein\w*|schnell\w*|einfach\w*|nur|winzig\w*"  # i18n-allow: DE scope words
    r"|mal\s+eben|eben\s+mal|einzeiler|minimal\w*|trivial\w*"  # i18n-allow: DE scope words
    r"|quick\w*|simple|simply|just|short|briefly|tiny"
    r"|one[-\s]?liner|single\s+line)\b",
    re.IGNORECASE,
)

#: Code work is the second deliverable shape next to a document: a refactor or
#: a migration produces a diff, which is exactly what the Worker->Critic
#: pipeline grades. Disjoint from ``_BUILD_VERB_RE`` on purpose — that one
#: stays untouched so the artifact classifier keeps its current behaviour.
_CODE_WORK_VERB_RE: re.Pattern[str] = re.compile(
    r"\b(?:refactor\w*|refaktor\w*|migrat\w*|migrier\w*|portier\w*"  # i18n-allow: DE code verbs
    r"|implementier\w*|implement\w*|rewrite|rewrit\w*|umschreib\w*"  # i18n-allow: DE code verbs
    r"|umbau\w*|modularisier\w*|entkoppel\w*|umstrukturier\w*"  # i18n-allow: DE code verbs
    r"|restructur\w*|clean\s+up)",
    re.IGNORECASE,
)

#: The object a code-work verb acts on.
_CODE_OBJECT_RE: re.Pattern[str] = re.compile(
    r"\b(?:code|codebase|repo|repository|modul\w*|module|modules"  # i18n-allow: DE code nouns
    r"|funktion\w*|function|functions|klasse|klassen|class|classes"  # i18n-allow: DE code nouns
    r"|projekt\w*|project|api|apis|endpoint\w*|pipeline"  # i18n-allow: DE code nouns
    r"|service|services|paket\w*|package|packages|test|tests)\b",  # i18n-allow: DE code nouns
    re.IGNORECASE,
)

#: Named parts of a deliverable. Counting DISTINCT ones is the strongest
#: substance signal there is: a brief that names two or more parts describes a
#: job with parts, which is what a background agent is for.
_COMPONENT_NOUN_RE: re.Pattern[str] = re.compile(
    r"\b(?:startseite\w*|start\s?page|homepage|landing\s?page"  # i18n-allow: DE component nouns
    r"|login|anmeldung|registrierung|sign\s?up|signup"  # i18n-allow: DE component nouns
    r"|datenbank\w*|database|endpoint\w*|route|routen|routes"  # i18n-allow: DE component nouns
    r"|formular\w*|kontaktformular\w*|navigation|men[uü]|menu"  # i18n-allow: DE component nouns
    r"|footer|header|impressum|unterseite\w*|seiten"  # i18n-allow: DE component nouns
    r"|deployment|readme|dokumentation|documentation"  # i18n-allow: DE component nouns
    r"|struktur\w*|architektur\w*|backend|frontend"  # i18n-allow: DE component nouns
    r"|diagramm\w*|chart|charts|tabelle\w*)\b",  # i18n-allow: DE component nouns
    re.IGNORECASE,
)

#: An explicit multi-step / from-scratch marker.
_MULTI_STEP_RE: re.Pattern[str] = re.compile(
    r"\bschritt\s+f[uü]r\s+schritt\b|\bstep\s+by\s+step\b"  # i18n-allow: DE multi-step marker
    r"|\bdanach\b|\banschlie[sß]end\w*|\bzuerst\b"  # i18n-allow: DE multi-step marker
    r"|\bvon\s+grund\s+auf\b|\bmehrere\b|\bmehreren\b"  # i18n-allow: DE multi-step marker
    r"|\bkomplett\w*|\bvollst[aä]ndig\w*"  # i18n-allow: DE multi-step marker
    r"|\bthen\b|\bafterwards?\b|\bend[-\s]?to[-\s]?end\b"
    r"|\bfrom\s+scratch\b|\bseveral\b|\bmultiple\b",
    re.IGNORECASE,
)

#: A research / analysis verb. Next to a file deliverable this is the
#: "research-with-a-deliverable" shape: the answer is not spoken, it is
#: written down, and that is a mission the critic can grade via git diff.
_RESEARCH_VERB_RE: re.Pattern[str] = re.compile(
    r"\b(?:recherchier\w*|analysier\w*|untersuch\w*|vergleich\w*"  # i18n-allow: DE research verbs
    r"|evaluier\w*|bewert\w*|research\w*|analyz\w*|analys\w*"  # i18n-allow: DE research verbs
    r"|investigat\w*|compar\w*|evaluat\w*|assess\w*|benchmark\w*)",
    re.IGNORECASE,
)

#: "… with Flask", "… using Postgres" — a named constraint on the artefact.
_SPEC_PREPOSITION_RE: re.Pattern[str] = re.compile(
    r"\b(?:mit|mittels|auf\s+basis\s+von"  # i18n-allow: DE prepositions
    r"|with|using|based\s+on|via)\s+\w",
    re.IGNORECASE,
)

#: Coordination — the brief lists more than one thing.
_COORDINATION_RE: re.Pattern[str] = re.compile(
    r"\b(?:und|sowie|au[sß]erdem|zus[aä]tzlich"  # i18n-allow: DE coordination
    r"|and|plus|additionally)\b|,",
    re.IGNORECASE,
)


def _artifact_regexes() -> tuple[re.Pattern[str], ...] | None:
    """The manager's build-verb / artefact-noun / named-file patterns.

    Imported lazily for the same reason as ``_is_decline_or_feature_talk``:
    this stays a leaf module. Sharing the manager's ONE set of patterns is the
    point — the effort route and the force-spawn path must agree on what "an
    artefact" is. On any import fault the effort route is simply unavailable
    (``None``), which fails CLOSED: no spawn.
    """
    try:
        from jarvis.brain.manager import (  # noqa: PLC0415
            _BUILD_VERB_RE,
            _DOC_NOUN_RE,
            _NAMED_FILE_RE,
        )
    except Exception:  # noqa: BLE001 — gate must never crash a tool turn
        return None
    return (_BUILD_VERB_RE, _DOC_NOUN_RE, _NAMED_FILE_RE)


def _conversational_standdown(text: str) -> bool:
    """True when an existing detector already calls this turn conversation.

    Reuses the manager's battle-tested stand-downs instead of forming a second
    opinion: an instructional "how do I …", an opinion/advice question, and
    conversational coaching. An import fault answers True — the effort route
    then simply does not fire, which is the safe side.

    ``_looks_like_pc_control`` is deliberately NOT in this set. Its pattern
    matches the bare verb "schreib", which is also a build verb, so it fires on
    "schreib mir einen Bericht über X" — the single most common shape of a
    write-a-deliverable brief. The codebase already resolves that collision the
    other way round (``_should_force_spawn``: the pc-control stand-down applies
    only ``and not self._research_wants_artifact(t)`` — an artefact build wins
    over a screen mention), and on the deterministic path that stand-down has
    already run before the effort test is reached.
    """
    try:
        from jarvis.brain.manager import (  # noqa: PLC0415
            _is_conversational_coaching,
            _is_instructional_question,
            _is_opinion_advice_question,
        )
    except Exception:  # noqa: BLE001 — gate must never crash a tool turn
        return True
    return bool(
        _is_instructional_question(text)
        or _is_opinion_advice_question(text)
        or _is_conversational_coaching(text)
    )


def effort_warrants_delegation(user_text: str) -> bool:
    """True when the turn is plainly a multi-step artefact brief.

    The second route to a permitted spawn, next to the delegation vocabulary.
    See the block comment above for the five stages and for where the "unsure"
    line sits (stage 5: one scope signal is not enough).

    Pure and side-effect free — the offer window is untouched here, so the
    caller keeps the one place that arms and disarms it. Callers:
    ``llm_spawn_allowed`` (LLM-chosen spawns) and
    ``BrainManager._should_force_spawn`` (the deterministic path), both only in
    the ``balanced`` / ``permissive`` force-spawn modes.
    """
    text = (user_text or "").strip()
    if not text:
        return False
    # 1. request shape
    if _REPORTED_WORK_RE.search(text):
        return False
    if _INFO_QUESTION_RE.search(text):
        return False
    if text.endswith("?") and not _REQUEST_FRAME_RE.search(text):
        return False
    # 2. explicit small scope
    if _CHEAP_SCOPE_RE.search(text):
        return False
    # 3. the existing conversational stand-downs
    if _conversational_standdown(text):
        return False
    # 4. a deliverable
    patterns = _artifact_regexes()
    if patterns is None:
        return False
    build_verb_re, doc_noun_re, named_file_re = patterns
    # A build verb takes either the manager's artefact nouns or a named part of
    # one ("eine Dokumentation", "eine Datenbank") — the manager's list is a
    # closed set tuned for its own classifier and is left untouched. Widening
    # here is safe because stage 5 below, not this stage, is what actually
    # decides: "Baue mir ein Login" reaches stage 5 with one named part and
    # stops there.
    has_deliverable = bool(
        named_file_re.search(text)
        or (
            build_verb_re.search(text)
            and (doc_noun_re.search(text) or _COMPONENT_NOUN_RE.search(text))
        )
        or (_CODE_WORK_VERB_RE.search(text) and _CODE_OBJECT_RE.search(text))
    )
    if not has_deliverable:
        return False
    # 5. scope — one SUBSTANCE signal is mandatory, two signals in total.
    named_parts = {
        match.group(0).lower()
        for pattern in (doc_noun_re, _COMPONENT_NOUN_RE, _CODE_OBJECT_RE)
        for match in pattern.finditer(text)
    }
    substance = 0
    if len(named_parts) >= 2:
        substance += 1
    if _MULTI_STEP_RE.search(text):
        substance += 1
    if _RESEARCH_VERB_RE.search(text):
        substance += 1
    if not substance:
        return False
    signals = substance
    if _SPEC_PREPOSITION_RE.search(text):
        signals += 1
    if _COORDINATION_RE.search(text):
        signals += 1
    if len(text.split()) >= _HEAVY_MIN_WORDS:
        signals += 1
    if signals < 2:
        return False
    log.info(
        "spawn gate: effort route — multi-step artefact brief (%d signals) in "
        "%r",
        signals,
        text[:80],
    )
    return True


def active_force_spawn_mode() -> str:
    """The live ``brain.routing.force_spawn_mode``.

    Read from the running ``BrainManager`` (the ONE accessor,
    ``BrainManager.force_spawn_mode``) so a mid-session config change is seen
    without re-reading ``jarvis.toml`` on every tool call. Without a registered
    manager there is no user configuration to honour, so the SHIPPED default
    answers — the effort test itself is the conservative layer here, not this
    lookup.
    """
    try:
        from jarvis.core import runtime_refs  # noqa: PLC0415

        manager = runtime_refs.get_brain_manager()
    except Exception:  # noqa: BLE001 — gate must never crash a tool turn
        return DEFAULT_FORCE_SPAWN_MODE
    if manager is None:
        return DEFAULT_FORCE_SPAWN_MODE
    return normalize_force_spawn_mode(getattr(manager, "force_spawn_mode", ""))


def effort_route_enabled() -> bool:
    """True when the active mode grants the effort route (not ``strict``)."""
    return active_force_spawn_mode() != FORCE_SPAWN_MODE_STRICT


def llm_spawn_allowed(user_text: str) -> bool:
    """Gate an LLM-chosen spawn tool call against the user's ACTUAL turn.

    Side effects (documented contract, shared by both call sites): a blocked
    conversational turn arms the offer window; an allowed spawn disarms it.
    ``user_text`` must be the verbatim user turn (``ctx.user_utterance`` /
    the realtime transcript), never the model's paraphrase — a paraphrase can
    smuggle in delegation vocabulary the user never spoke.

    An EMPTY turn fails CLOSED, unlike the desktop gate next door
    (``cu_gate.llm_computer_use_allowed`` fails open). The asymmetry is
    deliberate: without the user's words there is nothing to judge, and the
    2026-05-01 forensic is exactly a spawn nobody asked for being reported as
    started work. A blocked spawn costs an inline answer; an invented one costs
    a background agent and a false claim.
    """
    text = (user_text or "").strip()
    if not text:
        return False
    if _is_decline_or_feature_talk(text):
        log.info("spawn gate: decline / feature talk — spawn blocked")
        return False
    # An addressed Agentic-IDE terminal outranks a spawn. Checked before the
    # delegation marker so a depth word inside a terminal instruction ("let Kai
    # do a deep dive") cannot dispatch a background mission — but AFTER the
    # decline guard. ``owns_turn`` still stands down for a turn that ASKS for a
    # background agent ("spawn an agent that helps Kai"), which is what keeps
    # delegation available inside the workspace; what it no longer stands down
    # for is a pane being told to fan out ("Alex should spawn sub-agents").
    if addressed_pane_blocks_spawn(text):
        log.info(
            "spawn gate: an open Agentic-IDE terminal is addressed — "
            "the workspace handles this turn, no background agent"
        )
        return False
    if _marker_spans(text):
        OFFER_WINDOW.disarm()
        return True
    # The effort route (2026-08-18). Placed AFTER every guard above and after
    # the vocabulary route, so it can only ever widen what an already-clean
    # turn may do — a decline, feature talk, or an addressed workspace terminal
    # still wins. Two independent judgements have to agree before anything
    # starts: the model chose to call the spawn tool, and this deterministic
    # test reads the turn as a multi-step artefact brief.
    if effort_route_enabled() and effort_warrants_delegation(text):
        OFFER_WINDOW.disarm()
        return True
    if OFFER_WINDOW.consume_confirm(text):
        log.info("spawn gate: delegation offer confirmed — spawn allowed once")
        return True
    # Arm the offer window only on a SUBSTANTIVE turn (the one the model can
    # make a delegation offer about). A veto turn closes any pending offer
    # instead; a bare yes/no turn must never arm — otherwise two consecutive
    # affirmations ("Ja bitte" ... "Ja mach") would read as offer + confirm
    # and unlock a spawn nobody asked for.
    verdicts = _confirm_verdicts(text)
    if "veto" in verdicts:
        OFFER_WINDOW.disarm()
    elif "confirm" not in verdicts:
        OFFER_WINDOW.arm(text)
    log.info(
        "spawn gate: no explicit delegation request in turn %r — spawn blocked",
        text[:80],
    )
    return False


# The one blocked-tool message both call sites feed back to the model. Keeping
# it here guarantees the classic and realtime paths never drift apart in what
# they teach the model to do next.
SPAWN_BLOCKED_MODEL_FEEDBACK: str = (
    "spawn_worker was not executed: the user did not explicitly ask to "
    "delegate this to a background agent. Answer the user's turn directly "
    "yourself, right now, inline. If (and only if) the task genuinely needs "
    "multi-minute background work, you may ASK the user whether to start a "
    "background agent — a clear yes on their next turn unlocks this function. "
    # 2026-08-24: the offer must be a YES/NO question about the vehicle and
    # nothing else. Live voice session 10:24: blocked three turns running, the
    # model answered each block by asking WHICH worker and WHAT the task was —
    # a task the user had already spelled out in full. Re-asking for a brief
    # the user just gave reads as amnesia, and it cost him the whole session.
    "Ask only WHETHER to start one. Never ask what the task is or which agent "
    "to use: the user has already told you, it is in this conversation, and "
    "asking again reads as if you forgot. Restate the task in your offer so "
    "they only have to say yes."
)


# The addressed-pane variant. The generic text above would mislead here: it
# invites the model to OFFER a background agent, and when the user has just told
# a terminal to do something that offer is exactly the wrong next move — the
# work belongs in the pane they named. "Sub-agents" in such a turn is the CLI
# agent's own fan-out, which happens inside the terminal once the brief lands.
SPAWN_BLOCKED_ADDRESSED_PANE_FEEDBACK: str = (
    "spawn_worker was not executed: this turn is addressed to a coding "
    "terminal in the open workspace, and that terminal does this work. Send "
    "the user's request to it with the agentic-ide-prompt function, in the "
    "user's own words — including any instruction to spawn sub-agents, which "
    "is an instruction for the agent IN that terminal to carry out itself. You "
    "may still use other functions to gather context for the brief. Do not "
    "offer a background agent for this turn."
)


def spawn_blocked_feedback(user_text: str = "") -> str:
    """The tool-error text matching WHY the spawn was blocked.

    One function so the classic and realtime paths cannot teach the model two
    different next moves for the same block. ``user_text`` is the verbatim turn
    the gate just judged; without it the generic text is the honest answer.
    """
    if user_text and addressed_pane_blocks_spawn(user_text):
        return SPAWN_BLOCKED_ADDRESSED_PANE_FEEDBACK
    return SPAWN_BLOCKED_MODEL_FEEDBACK


__all__ = [
    "OFFER_WINDOW",
    "SPAWN_BLOCKED_ADDRESSED_PANE_FEEDBACK",
    "SPAWN_BLOCKED_MODEL_FEEDBACK",
    "SPAWN_VEHICLE_TOOL_NAMES",
    "DelegationOfferWindow",
    "active_force_spawn_mode",
    "addressed_pane_blocks_spawn",
    "effort_route_enabled",
    "effort_warrants_delegation",
    "llm_spawn_allowed",
    "names_spawn_vehicle",
    "spawn_blocked_feedback",
    "spawn_vehicle_spans",
]
