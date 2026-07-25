"""One entry point for "which skill, if any, owns this utterance?".

Every consumer — the brain's per-turn probe, ``POST /api/skills/match-test``,
and the offline routing eval — calls :func:`evaluate_match` and reads the same
:class:`MatchDecision`. That is the point: a Test-Match panel that re-implements
the decision eventually disagrees with the brain, and a disagreeing debugger is
worse than none.

This module owns the *vocabulary* (bands, candidate and decision records) and
the *orchestration*. It does not own a scoring algorithm — the deterministic
relevance scorer lives in :mod:`jarvis.skills.relevance` and is consulted only
when the author-written regex triggers miss.

Layering, in evaluation order:

1. **Author regex triggers** (:mod:`jarvis.skills.trigger_matcher`) — absolute
   precedence. A hit is not scored and not second-guessed; it is the skill
   author stating verbatim what the user says. Keeping this first is what makes
   the relevance layer additive: no currently-working utterance changes
   behaviour.
2. **Deterministic relevance** — the paraphrase channel. Wired in a later
   phase; until then this module reports ``NONE`` on a trigger miss, which is
   exactly today's behaviour, so the eval harness can baseline the *current*
   system honestly before the algorithm exists.

Guards are NOT applied here. :func:`evaluate_match` answers "what matched";
:mod:`jarvis.skills.guards` answers "may it capture the turn". Callers run both
and report the ladder, so a suppressed match stays visible as *suppressed*
rather than vanishing — the single biggest reason "my skill never fires" has
been undebuggable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Band vocabulary (closed set — mirrored in TypeScript, pinned by a parity test)
# ---------------------------------------------------------------------------

BAND_FIRE = "fire"
BAND_NARROW = "narrow"
BAND_NONE = "none"

#: Ordered strongest → weakest. Order is meaningful: :func:`band_at_least`
#: compares by index.
MATCH_BANDS: tuple[str, ...] = (BAND_FIRE, BAND_NARROW, BAND_NONE)

Band = Literal["fire", "narrow", "none"]

#: Where a decision came from. ``trigger`` is the author's regex, ``relevance``
#: the deterministic scorer, ``none`` means nothing matched.
SOURCE_TRIGGER = "trigger"
SOURCE_RELEVANCE = "relevance"
SOURCE_NONE = "none"

MATCH_SOURCES: tuple[str, ...] = (SOURCE_TRIGGER, SOURCE_RELEVANCE, SOURCE_NONE)

#: Score reported for an author-regex hit. A trigger match is absolute, not
#: ranked — this value exists so the record has a number, and must never be
#: compared against a relevance score (different scales entirely). Read
#: ``candidate.source`` before reading ``candidate.score``.
TRIGGER_MATCH_SCORE = 1.0


def band_at_least(band: str, floor: str) -> bool:
    """True when ``band`` is at least as strong as ``floor``.

    ``band_at_least("fire", "narrow")`` is True; the reverse is False. Unknown
    strings compare as the weakest band, so a typo fails closed.
    """
    def _rank(value: str) -> int:
        try:
            return MATCH_BANDS.index(value)
        except ValueError:
            return len(MATCH_BANDS)
    return _rank(band) <= _rank(floor)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MatchCandidate:
    """One ranked skill.

    ``evidence`` is the RAW span from the utterance that caused the match —
    untouched, not normalized. Downstream guards ``re.escape`` it against the
    original text, so a transliterated token would silently blind them on every
    umlaut word.
    """

    skill_name: str
    score: float = 0.0
    band: str = BAND_NONE
    source: str = SOURCE_NONE
    evidence: str = ""
    reason: str = ""
    #: ``(field, contribution)`` pairs — what made this skill score. Empty for a
    #: trigger hit, which has no per-field breakdown.
    signals: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True, slots=True)
class MatchDecision:
    """The full outcome of one match evaluation.

    ``band`` is the decision; ``candidates`` is the ranked field behind it, kept
    even when the band is ``none`` so the Test-Match panel can show *near
    misses* — "nothing matched" and "three skills nearly matched" are very
    different diagnoses and the log must distinguish them.
    """

    band: str = BAND_NONE
    source: str = SOURCE_NONE
    top: MatchCandidate | None = None
    candidates: tuple[MatchCandidate, ...] = ()
    #: Gap between the best and the runner-up. ``0.0`` when there is no
    #: runner-up or the decision came from a trigger.
    margin: float = 0.0
    elapsed_us: int = 0

    @property
    def fired(self) -> bool:
        """True when this decision alone would capture the turn (pre-guards)."""
        return self.band == BAND_FIRE and self.top is not None


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def _trigger_decision(registry: Any, user_text: str, lang: str) -> MatchDecision | None:
    """Author-regex pass. ``None`` on a miss, so the caller can fall through."""
    try:
        from jarvis.skills.trigger_matcher import TriggerMatcher

        hit = TriggerMatcher(registry).match_voice_with_match(user_text, lang=lang)
    except Exception:  # noqa: BLE001 — matching must never break a turn
        return None
    if hit is None:
        return None
    skill, match = hit
    evidence = match.group(0) if match is not None else ""
    candidate = MatchCandidate(
        skill_name=str(getattr(skill, "name", "")),
        score=TRIGGER_MATCH_SCORE,
        band=BAND_FIRE,
        source=SOURCE_TRIGGER,
        evidence=evidence,
        reason=f"voice trigger matched {evidence!r}",
    )
    return MatchDecision(
        band=BAND_FIRE,
        source=SOURCE_TRIGGER,
        top=candidate,
        candidates=(candidate,),
    )


def _relevance_decision(
    registry: Any,
    user_text: str,
    *,
    limit: int,
    fire_threshold: float | None = None,
    hint_threshold: float | None = None,
) -> MatchDecision:
    """Deterministic relevance pass — the paraphrase channel.

    Bands are applied HERE rather than inside the scorer, so
    :mod:`jarvis.skills.relevance` stays a pure ranking function that knows
    nothing about capture policy, and the dependency stays one-directional.
    """
    try:
        from jarvis.skills.relevance import get_index

        index = get_index(registry)
        ranking = index.rank(user_text, limit=limit)
    except Exception:  # noqa: BLE001 — matching must never break a turn
        return MatchDecision()
    if ranking.top is None:
        return MatchDecision()
    if fire_threshold is not None or hint_threshold is not None:
        ranking = replace(
            ranking,
            fire_threshold=(
                fire_threshold if fire_threshold is not None else ranking.fire_threshold
            ),
            hint_threshold=(
                hint_threshold if hint_threshold is not None else ranking.hint_threshold
            ),
        )

    candidates = tuple(
        MatchCandidate(
            skill_name=scored.name,
            score=round(scored.score, 6),
            band=_band_for(scored.score, ranking),
            source=SOURCE_RELEVANCE,
            evidence=scored.evidence,
            reason=scored.reason,
            signals=(
                ("token", round(scored.token_score, 6)),
                ("trigram", round(scored.trigram_score, 6)),
            ),
        )
        for scored in ranking.ranked
    )
    top = candidates[0]
    # FIRE additionally requires a CLEAR winner. Two near-tied skills are
    # genuine ambiguity ("send a message" → slack or discord?) and must degrade
    # so the model disambiguates from a short list instead of the matcher
    # guessing and hijacking the turn.
    band = top.band if (top.band != BAND_FIRE or ranking.clear_winner) else BAND_NARROW
    return MatchDecision(
        band=band,
        source=SOURCE_RELEVANCE,
        top=top if band != BAND_NONE else None,
        candidates=candidates,
        margin=round(ranking.margin, 6),
    )


def _band_for(score: float, ranking: Any) -> str:
    if score >= ranking.fire_threshold:
        return BAND_FIRE
    if score >= ranking.hint_threshold:
        return BAND_NARROW
    return BAND_NONE


def evaluate_match(
    registry: Any,
    user_text: str,
    *,
    lang: str = "auto",
    limit: int = 5,
    use_relevance: bool = True,
    fire_threshold: float | None = None,
    hint_threshold: float | None = None,
) -> MatchDecision:
    """Decide which skill, if any, owns ``user_text``.

    Pure CPU: no LLM call, no disk access, no network (AP-9/AP-11). Never
    raises — a broken skill subsystem degrades to "nothing matched" rather than
    breaking the turn.

    Args:
        registry: The live ``SkillRegistry`` (duck-typed).
        user_text: The raw utterance, untouched.
        lang: Language hint forwarded to the trigger matcher; ``"auto"``
            considers every language's triggers.
        limit: How many ranked candidates to keep for diagnostics.
        use_relevance: When False, only the author's regex triggers run — the
            exact pre-relevance behaviour, which is what the kill switch buys.
        fire_threshold: Override the corpus-derived FIRE cut-off.
        hint_threshold: Override the corpus-derived NARROW cut-off.

    Returns:
        A :class:`MatchDecision`. Guards are the caller's job — see
        :func:`jarvis.skills.guards.evaluate_guards`.
    """
    started = time.perf_counter_ns()

    def _stamp(decision: MatchDecision) -> MatchDecision:
        elapsed = max(0, (time.perf_counter_ns() - started) // 1_000)
        return MatchDecision(
            band=decision.band,
            source=decision.source,
            top=decision.top,
            candidates=decision.candidates,
            margin=decision.margin,
            elapsed_us=elapsed,
        )

    if not user_text or registry is None:
        return _stamp(MatchDecision())

    trigger = _trigger_decision(registry, user_text, lang)
    if trigger is not None:
        return _stamp(trigger)

    if not use_relevance:
        return _stamp(MatchDecision())
    return _stamp(
        _relevance_decision(
            registry,
            user_text,
            limit=limit,
            fire_threshold=fire_threshold,
            hint_threshold=hint_threshold,
        )
    )


__all__ = [
    "BAND_FIRE",
    "BAND_NARROW",
    "BAND_NONE",
    "MATCH_BANDS",
    "MATCH_SOURCES",
    "SOURCE_NONE",
    "SOURCE_RELEVANCE",
    "SOURCE_TRIGGER",
    "TRIGGER_MATCH_SCORE",
    "Band",
    "MatchCandidate",
    "MatchDecision",
    "band_at_least",
    "evaluate_match",
]
