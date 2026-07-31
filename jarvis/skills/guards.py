"""Skill-capture guards — ONE definition, shared by every consumer.

A "guard" answers a single question: *may this skill capture this turn?* Until
now each answer lived inline in ``jarvis/brain/manager.py`` as a private method
or a module-level helper, which meant the REST layer and any eval harness had
to re-implement them — and a re-implementation eventually lies. That is the
whole reason this module exists: ``manager.py``, ``POST /api/skills/match-test``
and the offline routing eval all import the SAME functions, so the panel can
never disagree with what the brain actually did.

Design constraints (all load-bearing):

* **Zero ``jarvis.*`` imports.** ``jarvis.brain`` already imports
  ``jarvis.skills``; importing back would close a cycle. Guards that need a
  brain-side verdict (the local-action gate, the force-spawn heuristic) receive
  it as plain data from the caller instead of reaching for it. Everything here
  is pure: same inputs, same verdict, no IO, no clock, no globals.
* **Duck-typed skills.** ``skill`` is read via ``getattr`` exactly as
  ``prompt_injection.py`` and ``local_search.py`` already do, so a test fake or
  a dict-ish stub works without importing the schema.
* **A closed veto vocabulary.** Every string a guard can return is a member of
  :data:`VETO_REASONS`. A guard inventing an unlisted reason renders as a blank
  cell in the UI — the silent version of a multi-layer drift bug — so a parity
  test asserts the set matches the TypeScript union.

Behaviour is deliberately identical to the inline originals; this module is a
move, not a redesign. The one improvement is resolution: the old
``_skill_is_blocked`` folded "no frontmatter" and "block tier" into a single
boolean, and callers could not tell which fired. Here they are distinct
reasons with the same verdict.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Veto vocabulary (closed set — mirrored in TypeScript, pinned by a parity test)
# ---------------------------------------------------------------------------

VETO_DEFINITIONAL = "definitional_question"
VETO_NO_FRONTMATTER = "no_frontmatter"
VETO_BLOCK_TIER = "block_tier"
VETO_DRAFT_STATE = "draft_state"
VETO_DISABLED_STATE = "disabled_state"
VETO_LOCAL_ACTION = "local_action_gate"
VETO_EXPLICIT_HEAVY = "explicit_heavy_request"
VETO_DISPATCHING_CLASS = "class_dispatching"
VETO_AUTO_FIRE_NEVER = "auto_fire_never"
VETO_BAND_BELOW_FLOOR = "band_below_floor"
VETO_KILL_SWITCH = "kill_switch"
VETO_SHADOW_MODE = "shadow_mode"

VETO_REASONS: frozenset[str] = frozenset({
    VETO_DEFINITIONAL,
    VETO_NO_FRONTMATTER,
    VETO_BLOCK_TIER,
    VETO_DRAFT_STATE,
    VETO_DISABLED_STATE,
    VETO_LOCAL_ACTION,
    VETO_EXPLICIT_HEAVY,
    VETO_DISPATCHING_CLASS,
    VETO_AUTO_FIRE_NEVER,
    VETO_BAND_BELOW_FLOOR,
    VETO_KILL_SWITCH,
    VETO_SHADOW_MODE,
})

#: Guard identifiers, in the order the ladder evaluates them. Exposed so the
#: Test-Match panel can render a stable ladder even when nothing vetoes.
GUARD_ORDER: tuple[str, ...] = (
    "frontmatter_present",
    "lifecycle_state",
    "risk_block_tier",
    "definitional_question",
    "explicit_heavy_request",
    "local_action_gate",
)

Verdict = Literal["pass", "veto", "skipped"]


# ---------------------------------------------------------------------------
# Definitional-question guard (moved verbatim from manager.py:869-896)
# ---------------------------------------------------------------------------

# A bare-name plugin trigger ("Discord", "Stripe", "GitHub") fires on ANY
# mention — including when the app is merely the SUBJECT of a knowledge
# question ("was ist GitHub?", "what is Stripe?"). Such a turn must be
# ANSWERED, not captured by the skill (live skill-routing eval 2026-06-24: both
# negative controls over-fired here). Precision over recall: suppress ONLY when
# the matched trigger token is the predicate of a copula in a definitional
# opener — so a real data request that merely starts with "was ist" ("was ist
# in meinem Posteingang?", token "posteingang") is NOT suppressed, because there
# the token does not sit directly after the copula (the "in"/"mein" data context
# breaks the predicate run).
_DEFINITIONAL_OPENER_RE = re.compile(
    r"^\s*(?:was\s+(?:ist|sind|bedeutet|war|waren)"  # i18n-allow: speech-input matching data
    r"|wof[üu]r\s+(?:ist|steht|braucht|nutzt|benutzt|verwendet)"  # i18n-allow: speech input
    r"|erkl[äa]r(?:e|st|en)?\b"  # i18n-allow: speech input
    r"|what(?:'s|\s+is|\s+are|\s+does)\b"
    r"|tell\s+me\s+(?:about|what)\b"
    r")",
    re.IGNORECASE,
)
# Copula → (optional adverb, but NOT a data-context word) → optional article →
# the matched token. ``%s`` is filled with the re.escape'd trigger token.
# Words that mark a DATA context rather than a definition, and so break the
# predicate run ("was ist IN MEINEM Posteingang" is a request, not a question
# about what an inbox is).
_DATA_CONTEXT = (
    "in|auf|an|aus|von|mein|meine|meinem|meinen|my|on|from|of"  # i18n-allow: speech input
)
_DEFINITIONAL_PREDICATE_TMPL = (
    r"\b(?:ist|sind|war|waren|is|are|was|were|bedeutet|means)\s+"  # i18n-allow: speech input
    rf"(?:(?!\b(?:{_DATA_CONTEXT})\b)"
    r"\w+\s+){0,2}"
    r"(?:ein(?:e|er|en)?\s+|a\s+|an\s+|the\s+)?%s\b"
)

# Compiled predicate patterns are cached per token: the token comes from a
# bounded vocabulary (trigger literals / skill names), so the cache cannot grow
# with user input the way a per-utterance cache would.
_PREDICATE_CACHE: dict[str, re.Pattern[str]] = {}
_PREDICATE_CACHE_MAX = 512


def is_definitional_question_about(user_text: str, token: str) -> bool:
    """True when ``user_text`` is a definitional question whose subject IS the
    matched trigger ``token`` — so firing that skill would be wrong.

    ``token`` MUST be the raw span as it appeared in ``user_text`` (the regex
    below does ``re.escape`` + ``\\b`` against the untouched text). Handing it a
    normalized/transliterated token silently disables the guard for every
    umlaut word.
    """
    if not user_text or not token:
        return False
    if not _DEFINITIONAL_OPENER_RE.match(user_text):
        return False
    pat = _PREDICATE_CACHE.get(token)
    if pat is None:
        pat = re.compile(_DEFINITIONAL_PREDICATE_TMPL % re.escape(token), re.IGNORECASE)
        if len(_PREDICATE_CACHE) >= _PREDICATE_CACHE_MAX:
            _PREDICATE_CACHE.clear()
        _PREDICATE_CACHE[token] = pat
    return bool(pat.search(user_text))


# ---------------------------------------------------------------------------
# Skill-state guards
# ---------------------------------------------------------------------------

#: Lifecycle states a skill may capture a turn in. Mirrors
#: ``TriggerMatcher._is_matchable`` and ``SkillRegistry.list_active``.
MATCHABLE_STATES: frozenset[str] = frozenset({"active", "validated"})


def _state_value(skill: Any) -> str:
    """Lifecycle state as a plain lowercase string, for enum or str alike."""
    state = getattr(skill, "state", None)
    return str(getattr(state, "value", state) or "").lower()


def _risk_tier(skill: Any) -> str:
    """``risk_policy.default_tier`` as a plain string, "" when unreadable."""
    frontmatter = getattr(skill, "frontmatter", None)
    if frontmatter is None:
        return ""
    policy = getattr(frontmatter, "risk_policy", None)
    tier = getattr(policy, "default_tier", None)
    return str(getattr(tier, "value", tier) or "").lower()


def frontmatter_veto(skill: Any) -> str | None:
    """Veto a skill whose frontmatter failed to parse.

    Mirrors the ``fm is None -> True`` branch of the old ``_skill_is_blocked``:
    without frontmatter there is no risk policy to consult, so the safe reading
    is "blocked". Reported separately from :func:`block_tier_veto` purely so the
    Test-Match panel can say *which* of the two fired.
    """
    return None if getattr(skill, "frontmatter", None) is not None else VETO_NO_FRONTMATTER


def block_tier_veto(skill: Any) -> str | None:
    """Veto a ``risk_policy.default_tier: block`` skill.

    A block-tier skill must never capture a turn — it stays visible in the
    listing and in UI search, and ``run-skill`` refuses it at execution time.
    """
    if getattr(skill, "frontmatter", None) is None:
        return None  # frontmatter_veto owns that case
    return VETO_BLOCK_TIER if _risk_tier(skill) == "block" else None


def lifecycle_veto(skill: Any) -> str | None:
    """Veto a DRAFT or DISABLED skill (AP-15).

    DRAFT skills are either parse failures or agent-authored drafts awaiting
    review; DISABLED skills were explicitly turned off. Neither may fire
    automatically, ever, by any path.
    """
    state = _state_value(skill)
    if state in MATCHABLE_STATES:
        return None
    if state == "disabled":
        return VETO_DISABLED_STATE
    return VETO_DRAFT_STATE


# ---------------------------------------------------------------------------
# Brain-side guards — verdicts arrive as plain data (no reverse import)
# ---------------------------------------------------------------------------

#: Local-action gate modes that claim a turn outright. A DIRECT open or a
#: COMPUTER_USE plan owns the turn; a pure dispatch (``None`` / ``UNSUPPORTED``)
#: leaves it to the skill.
LOCAL_ACTION_CLAIMING_MODES: frozenset[str] = frozenset({"direct", "computer_use"})


def local_action_veto(local_action_mode: str | None) -> str | None:
    """Veto when the deterministic desktop-control gate claims the turn.

    Sibling of the explicit-heavy guard: a plugin skill that merely keyword-
    matched an APP NAME ("Discord", "Spotify") must not suppress Computer-Use,
    the universal GUI integration. Live bug 2026-06-21: ``plugin-discord``
    matched the bare word "Discord", and the tool-less deep brain then claimed
    it had no Discord access.

    ``local_action_mode`` is the caller's already-computed
    ``LocalActionMode.value`` (or ``None`` when the gate did not claim). Passed
    in rather than computed here so this module keeps zero ``jarvis.*`` imports.
    """
    mode = (local_action_mode or "").lower()
    return VETO_LOCAL_ACTION if mode in LOCAL_ACTION_CLAIMING_MODES else None


def explicit_heavy_veto(is_explicit_heavy: bool) -> str | None:
    """Veto when the utterance names a heavy-work vehicle (AD-S9).

    An utterance that names the execution vehicle belongs to the mission path,
    not the inline skill prompt. Forensic example:
    "spawne einen Sub-Agent für Gmail".  <!-- i18n-allow: forensic quote -->

    The predicate is computed by the caller (it depends on live brain state:
    the force-spawn pattern is built from the registered tool surface).
    """
    return VETO_EXPLICIT_HEAVY if is_explicit_heavy else None


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GuardResult:
    """One guard's outcome, for the Test-Match ladder."""

    guard: str
    verdict: Verdict
    detail: str = ""


@dataclass(frozen=True, slots=True)
class GuardLadder:
    """Every guard's outcome plus the first veto, if any.

    ``vetoed_by`` is ``""`` when the skill may capture the turn. Guards after
    the first veto are still reported, with verdict ``skipped``, so the panel
    shows a stable ladder instead of a list that shrinks as vetoes fire.
    """

    results: tuple[GuardResult, ...] = ()
    vetoed_by: str = ""

    @property
    def passed(self) -> bool:
        return not self.vetoed_by


def evaluate_guards(
    skill: Any,
    *,
    user_text: str = "",
    evidence: str = "",
    local_action_mode: str | None = None,
    is_explicit_heavy: bool = False,
) -> GuardLadder:
    """Run every capture guard and report the full ladder.

    Args:
        skill: The candidate skill (duck-typed: ``state``, ``frontmatter``).
        user_text: The raw utterance, untouched — the definitional guard
            matches against it directly.
        evidence: The RAW matched span from the utterance (a regex trigger's
            ``group(0)``, or the relevance winner's matched surface). Must not
            be normalized, or the definitional guard goes blind on umlauts.
        local_action_mode: ``LocalActionMode.value`` when the deterministic
            desktop gate claimed the turn, else ``None``.
        is_explicit_heavy: Caller's AD-S9 verdict.

    Returns:
        A :class:`GuardLadder`. ``ladder.passed`` is the single boolean the
        brain needs; ``ladder.results`` is what the Test-Match panel renders.
    """
    checks: tuple[tuple[str, str | None], ...] = (
        ("frontmatter_present", frontmatter_veto(skill)),
        ("lifecycle_state", lifecycle_veto(skill)),
        ("risk_block_tier", block_tier_veto(skill)),
        ("definitional_question", (
            VETO_DEFINITIONAL
            if is_definitional_question_about(user_text, evidence)
            else None
        )),
        ("explicit_heavy_request", explicit_heavy_veto(is_explicit_heavy)),
        ("local_action_gate", local_action_veto(local_action_mode)),
    )

    results: list[GuardResult] = []
    vetoed_by = ""
    for name, reason in checks:
        if vetoed_by:
            results.append(GuardResult(guard=name, verdict="skipped"))
            continue
        if reason is None:
            results.append(GuardResult(guard=name, verdict="pass"))
            continue
        vetoed_by = reason
        results.append(GuardResult(guard=name, verdict="veto", detail=reason))
    return GuardLadder(results=tuple(results), vetoed_by=vetoed_by)


__all__ = [
    "GUARD_ORDER",
    "LOCAL_ACTION_CLAIMING_MODES",
    "MATCHABLE_STATES",
    "VETO_AUTO_FIRE_NEVER",
    "VETO_BAND_BELOW_FLOOR",
    "VETO_BLOCK_TIER",
    "VETO_DEFINITIONAL",
    "VETO_DISABLED_STATE",
    "VETO_DISPATCHING_CLASS",
    "VETO_DRAFT_STATE",
    "VETO_EXPLICIT_HEAVY",
    "VETO_KILL_SWITCH",
    "VETO_LOCAL_ACTION",
    "VETO_NO_FRONTMATTER",
    "VETO_REASONS",
    "VETO_SHADOW_MODE",
    "GuardLadder",
    "GuardResult",
    "block_tier_veto",
    "evaluate_guards",
    "explicit_heavy_veto",
    "frontmatter_veto",
    "is_definitional_question_about",
    "lifecycle_veto",
    "local_action_veto",
]
