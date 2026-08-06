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
        good (declined offers must not linger as an unlockable spawn).
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
        if len(norm.split()) > _CONFIRM_MAX_WORDS:
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


def llm_spawn_allowed(user_text: str) -> bool:
    """Gate an LLM-chosen spawn tool call against the user's ACTUAL turn.

    Side effects (documented contract, shared by both call sites): a blocked
    conversational turn arms the offer window; an allowed spawn disarms it.
    ``user_text`` must be the verbatim user turn (``ctx.user_utterance`` /
    the realtime transcript), never the model's paraphrase — a paraphrase can
    smuggle in delegation vocabulary the user never spoke.
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
    "background agent — a clear yes on their next turn unlocks this function."
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
    "addressed_pane_blocks_spawn",
    "llm_spawn_allowed",
    "names_spawn_vehicle",
    "spawn_blocked_feedback",
    "spawn_vehicle_spans",
]
