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


__all__ = [
    "OFFER_WINDOW",
    "SPAWN_BLOCKED_MODEL_FEEDBACK",
    "SPAWN_VEHICLE_TOOL_NAMES",
    "DelegationOfferWindow",
    "llm_spawn_allowed",
]
