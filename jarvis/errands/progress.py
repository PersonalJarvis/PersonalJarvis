"""Progress detection — the bound that replaces the step budget (C2).

An errand has no step limit and no time limit (C1). That is only safe because
"still working" is *measurable* rather than assumed: the run stops when nothing
is moving any more, not when a counter runs out.

The measure is deliberately **evidence, not prose**. A language model will
narrate a fresh-sounding sentence on every round while achieving nothing at all
— "let me try a different approach" is free to generate and costs a full round.
So a leg counts as progress when it produced a new fact about the world, and
not when it merely produced new words about the world.

Two signals, both cheap and deterministic (no model call — a stall check that
needed the model would burn the very tokens it exists to protect):

1. **No new evidence** across N consecutive legs. The general case: the run is
   busy but nothing is landing.
2. **Identical fingerprint** across N consecutive legs. The tight loop: the
   same tools, the same result, over and over. Caught by the same threshold but
   usually visible sooner, because a repeat is exactly identical.

The canonical case the maintainer named is bot detection flagging the session:
no amount of retrying changes it, and the honest move is to stop and say so.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from .schema import StepRecord

#: Consecutive fruitless legs before an errand is declared stalled. Three is a
#: real attempt at a different route (C4 runs on every refusal, so a stall of
#: three has already survived three re-checks) without being an expensive way
#: to discover a wall that was obvious after the first bounce.
STALL_THRESHOLD: Final[int] = 3

_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", (text or "").strip().lower())


def fingerprint(
    *,
    tools_used: Sequence[str],
    evidence_details: Sequence[str],
    summary: str,
) -> str:
    """A stable digest of what a leg actually achieved.

    Evidence dominates: two legs that gathered the same facts through the same
    tools are the same leg, however differently the model described them. The
    summary is included only as a tiebreaker so genuinely different work with
    no evidence yet (early exploration) is not instantly called a repeat.
    """
    payload = "|".join(
        [
            ",".join(sorted(_normalize(t) for t in tools_used)),
            ",".join(sorted(_normalize(d) for d in evidence_details)),
            _normalize(summary)[:200],
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class ProgressVerdict:
    """Whether the run is still getting somewhere, and why not if it is not."""

    stalled: bool
    reason: str = ""
    fruitless_legs: int = 0


def assess(steps: Sequence[StepRecord], *, threshold: int = STALL_THRESHOLD) -> ProgressVerdict:
    """Judge the run so far.

    Never raises and never calls a model: this runs after every leg, and a
    stall check that can fail is a stall check that lets a runaway run.
    """
    if len(steps) < threshold:
        return ProgressVerdict(stalled=False)

    recent = list(steps[-threshold:])

    # Signal 2 first — an exact repeat is the more specific diagnosis, and
    # saying "the same thing three times" is more useful to the user than the
    # generic "nothing landed".
    prints = {s.fingerprint for s in recent if s.fingerprint}
    if len(prints) == 1 and len(recent) == threshold:
        return ProgressVerdict(
            stalled=True,
            reason=(
                f"The last {threshold} attempts did exactly the same thing with "
                "the same result — repeating it again will not change anything."
            ),
            fruitless_legs=threshold,
        )

    # Signal 1: count back over legs that produced no new fact at all.
    fruitless = 0
    for step in reversed(steps):
        if step.evidence:
            break
        fruitless += 1
    if fruitless >= threshold:
        return ProgressVerdict(
            stalled=True,
            reason=(
                f"The last {fruitless} steps produced no new information — the "
                "run is busy but nothing is landing."
            ),
            fruitless_legs=fruitless,
        )

    return ProgressVerdict(stalled=False, fruitless_legs=fruitless)


__all__ = ["STALL_THRESHOLD", "ProgressVerdict", "assess", "fingerprint"]
