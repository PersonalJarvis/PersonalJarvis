"""In-memory ring of recent skill-match decisions.

The reason "my skill never fires" has been undebuggable is not that the system
is silent — it is that every veto is a ``log.info`` nobody reads. Four separate
sites in ``BrainManager`` log a suppressed match and then move on
(``manager.py`` around the definitional guard, the block-tier check, the AD-S9
stand-down and the local-action stand-down). None of it is queryable, so the
only available diagnosis was "I said something and nothing happened".

This module is the answer, and it is deliberately the cheapest possible one:

* A process-local ``collections.deque`` with a fixed maximum. Appending is a
  handful of nanoseconds and touches no disk, so it is safe to call on every
  turn from the voice path (AP-9/AP-11).
* Dies with the process. That is fine — the durable copy rides the bus as a
  frozen event and lands in the flight recorder, which already buffers and
  flushes at most once per second.

Privacy split, and it is load-bearing: the ring keeps a short utterance
PREVIEW, because a maintainer cannot debug "why didn't it fire" without seeing
what they said. The bus event carries only a hash, so nothing that a
public-release scrub would have to chase ends up in
``data/flight_recorder/*.jsonl``.
"""
from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, field
from typing import Any

#: How many decisions to keep. Small on purpose: this answers "what just
#: happened", not "what happened last week" — that is the recorder's job.
MAX_ENTRIES = 200

#: Utterance preview length kept in memory.
PREVIEW_CHARS = 80


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    """One ranked candidate as it appeared in a decision."""

    skill_name: str
    score: float = 0.0
    band: str = "none"
    reason: str = ""


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """One match evaluation, exactly as the brain saw it."""

    utterance_preview: str = ""
    utterance_hash: str = ""
    lang: str = ""
    source: str = "none"
    band: str = "none"
    winner: str = ""
    autofire_class: str = ""
    #: Empty when nothing vetoed. This single field is the feature — it turns
    #: "nothing happened" into "the definitional guard suppressed plugin-github".
    vetoed_by: str = ""
    fired: bool = False
    shadow: bool = False
    dry_run: bool = False
    elapsed_us: int = 0
    candidates: tuple[CandidateRecord, ...] = field(default_factory=tuple)


_RING: deque[DecisionRecord] = deque(maxlen=MAX_ENTRIES)


def utterance_hash(text: str) -> str:
    """Short stable hash — what travels on the bus instead of the text."""
    digest = hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()
    return digest[:12]


def record(
    *,
    utterance: str,
    decision: Any,
    lang: str = "auto",
    vetoed_by: str = "",
    autofire_class: str = "",
    fired: bool = False,
    shadow: bool = False,
    dry_run: bool = False,
) -> DecisionRecord:
    """Append a decision to the ring and return it. Never raises.

    Synchronous by design: a deque append is cheaper than the branch that would
    decide whether to defer it.
    """
    try:
        candidates = tuple(
            CandidateRecord(
                skill_name=getattr(c, "skill_name", ""),
                score=float(getattr(c, "score", 0.0)),
                band=str(getattr(c, "band", "none")),
                reason=str(getattr(c, "reason", "")),
            )
            for c in (getattr(decision, "candidates", ()) or ())
        )
        top = getattr(decision, "top", None)
        entry = DecisionRecord(
            utterance_preview=(utterance or "")[:PREVIEW_CHARS],
            utterance_hash=utterance_hash(utterance),
            lang=lang,
            source=str(getattr(decision, "source", "none")),
            band=str(getattr(decision, "band", "none")),
            winner=str(getattr(top, "skill_name", "") if top is not None else ""),
            autofire_class=autofire_class,
            vetoed_by=vetoed_by,
            fired=fired,
            shadow=shadow,
            dry_run=dry_run,
            elapsed_us=int(getattr(decision, "elapsed_us", 0) or 0),
            candidates=candidates,
        )
    except Exception:  # noqa: BLE001 — diagnostics must never break a turn
        entry = DecisionRecord(
            utterance_preview=(utterance or "")[:PREVIEW_CHARS],
            utterance_hash=utterance_hash(utterance),
            vetoed_by=vetoed_by,
        )
    _RING.append(entry)
    return entry


def recent(
    limit: int = 50, *, skill: str = "", fired: bool | None = None
) -> list[DecisionRecord]:
    """Most recent decisions first, optionally filtered."""
    out: list[DecisionRecord] = []
    for entry in reversed(_RING):
        if skill and entry.winner != skill:
            continue
        if fired is not None and entry.fired is not fired:
            continue
        out.append(entry)
        if len(out) >= max(1, limit):
            break
    return out


def clear() -> None:
    """Drop the ring (tests, and an explicit user reset)."""
    _RING.clear()


def size() -> int:
    return len(_RING)


__all__ = [
    "MAX_ENTRIES",
    "PREVIEW_CHARS",
    "CandidateRecord",
    "DecisionRecord",
    "clear",
    "recent",
    "record",
    "size",
    "utterance_hash",
]
