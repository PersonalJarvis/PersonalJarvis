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
    return bool(_DELEGATION_MARKER_RE.search((user_text or "").strip()))


def spawn_vehicle_spans(user_text: str) -> list[tuple[int, int]]:
    """Character spans of every explicit spawn-vehicle mention, in order.

    The positional view of ``names_spawn_vehicle``, for the one caller that
    needs to know not merely THAT the vehicle was named but WHERE: the
    Agentic-IDE precedence rule has to tell "spawn an agent that helps Kai"
    (an order to Jarvis, vehicle word first) from "Alex should spawn
    sub-agents" (a description of Alex's work, vehicle word behind the
    call-sign). Both share this ONE pattern so the two answers cannot drift.
    """
    return [
        (match.start(), match.end())
        for match in _DELEGATION_MARKER_RE.finditer((user_text or "").strip())
    ]


def coding_mode_blocks_spawn() -> bool:
    """True while the Agentic IDE is the active mode, which forbids a spawn.

    Maintainer mandate 2026-07-27: inside the coding workspace, "sub-agent" is
    the CLI agent's own fan-out vocabulary — every agentic coding tool has the
    feature and users ask for it constantly. Reading those words as an order to
    dispatch a JARVIS mission worker is a collision the user cannot phrase their
    way out of, so the mode decides instead of the wording: while coding mode is
    on, work goes to the panes on screen and no internal worker is dispatched.

    The mode is deliberately the toggle a user can see (the app-wide coding-mode
    badge), not the mere presence of a workspace: an open workspace with the
    toggle off is "terminals on a screen", and delegation there is a legitimate
    request. Turning the toggle off restores the background agent in full.

    Never raises — the workspace is an optional surface and must never be able
    to break spawn routing; a fault answers "does not block".
    """
    try:
        from jarvis.agentic_ide.session import coding_mode_active  # noqa: PLC0415

        return coding_mode_active()
    except Exception:  # noqa: BLE001 — optional surface, never fatal to routing
        return False


def _agentic_ide_claims_turn(text: str) -> bool:
    """True when an open Agentic-IDE terminal is being addressed instead.

    An agent already runs in a pane the user can see and named; typing the work
    into it is what they asked for, and dispatching an invisible background
    worker instead is the live 2026-07-25 defect this guard closes. Import is
    local and failure-tolerant: the workspace is an optional surface and must
    never be able to break spawn routing.
    """
    try:
        from jarvis.agentic_ide.intent import owns_turn

        return owns_turn(text)
    except Exception:  # noqa: BLE001 - optional surface, never fatal to routing
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
    # Coding mode outranks EVERYTHING below, including explicit delegation
    # vocabulary. See ``coding_mode_blocks_spawn`` — inside the Agentic IDE the
    # word "sub-agent" belongs to the CLI agent in the pane, so no wording may
    # unlock an internal worker while that mode is on.
    if coding_mode_blocks_spawn():
        log.info("spawn gate: Agentic-IDE coding mode is on — no background agent")
        return False
    if _is_decline_or_feature_talk(text):
        log.info("spawn gate: decline / feature talk — spawn blocked")
        return False
    # An addressed Agentic-IDE terminal outranks a spawn. Checked before the
    # delegation marker so a depth word inside a terminal instruction ("let Kai
    # do a deep dive") cannot dispatch a background mission — but AFTER the
    # decline guard, and ``owns_turn`` itself stands down when the user named the
    # vehicle, so an explicit "spawn an agent" still spawns.
    if _agentic_ide_claims_turn(text):
        log.info(
            "spawn gate: an open Agentic-IDE terminal is addressed — "
            "the workspace handles this turn, no background agent"
        )
        return False
    if _DELEGATION_MARKER_RE.search(text):
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


# The coding-mode variant. The generic text above would mislead here: it invites
# the model to OFFER a background agent, and in the Agentic IDE that offer is
# exactly the wrong next move — the work belongs in a pane the user is looking
# at. "Sub-agents" in this mode means the CLI agent's own fan-out, which happens
# inside the terminal once the brief arrives there.
SPAWN_BLOCKED_CODING_MODE_FEEDBACK: str = (
    "spawn_worker was not executed: the Agentic-IDE coding mode is ON, and in "
    "that mode Jarvis never starts its own background workers. The coding "
    "agents in the named terminals do this work. Send the user's request to the "
    "right terminal with the agentic-ide-prompt function, in the user's own "
    "words — including any instruction to spawn sub-agents, which is an "
    "instruction for the agent IN that terminal to carry out itself. If no "
    "terminal is addressed, answer inline or ask which one should take it. Do "
    "not offer a background agent."
)


def spawn_blocked_feedback() -> str:
    """The tool-error text matching WHY the spawn was blocked.

    One function so the classic and realtime paths cannot teach the model two
    different next moves for the same block.
    """
    if coding_mode_blocks_spawn():
        return SPAWN_BLOCKED_CODING_MODE_FEEDBACK
    return SPAWN_BLOCKED_MODEL_FEEDBACK


__all__ = [
    "OFFER_WINDOW",
    "SPAWN_BLOCKED_CODING_MODE_FEEDBACK",
    "SPAWN_BLOCKED_MODEL_FEEDBACK",
    "SPAWN_VEHICLE_TOOL_NAMES",
    "DelegationOfferWindow",
    "coding_mode_blocks_spawn",
    "llm_spawn_allowed",
    "names_spawn_vehicle",
    "spawn_blocked_feedback",
    "spawn_vehicle_spans",
]
