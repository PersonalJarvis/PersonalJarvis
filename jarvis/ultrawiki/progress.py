"""The one honest answer to "how far along is the knowledge base?".

Why this module exists (2026-07-26 forensic). A single screenshot of the Wiki
section showed, two centimetres apart:

* the import strip: ``Processing (3237 pending)``,
* the checklist: ``Everything is processed. No backlog.``

Both were computed correctly. They simply did not compute the same thing:
:func:`jarvis.ultrawiki.service.UltraWikiService._pipeline_state` counted
``captured + keyword_indexed + embedded`` as the backlog, while
``health._processing_check`` counted ``captured`` alone. One concept, derived
twice, no test that could see the disagreement — the exact multi-layer drift
class the repo keeps re-learning (BUG-008).

So "how far along" is now derived **once**, here, and every surface reads the
result instead of adding up buckets of its own.

The second half of the same defect was arithmetic, not wiring.
``uw_items.state`` is a state machine and ``store.counts()`` is a
``GROUP BY state``, so the five numbers are **mutually exclusive buckets** —
an item sits in exactly one. Printing them under cumulative-sounding labels
produced "Keyword-searchable 0" on a corpus where all 4 712 items were
keyword-searchable (they had simply moved past that bucket), and made the
numbers appear to run backwards as work progressed. Everything this module
reports is therefore **cumulative**: how many items have *reached* a
milestone, never how many are parked on it right now.

Deliberately prose-free. It returns numbers and stable ids; the sentences are
composed in the UI from translated strings, so the same model serves every
locale.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

__all__ = [
    "MILESTONES",
    "NEXT_STEP_BY_BUCKET",
    "ProgressState",
    "STAGE_ORDER",
    "build_progress",
]

#: The pipeline buckets in the order an item travels through them. ``failed``
#: is not here: it is a dead end reachable from any of these, not a stage.
STAGE_ORDER: tuple[str, ...] = ("captured", "keyword_indexed", "embedded", "distilled")

#: What an item sitting in a bucket is *waiting for*. An ``embedded`` item is
#: not "doing embedding" — it is done with that and queued for distillation.
#: Getting this backwards is how a progress bar ends up one stage optimistic.
NEXT_STEP_BY_BUCKET: dict[str, str] = {
    "captured": "indexing",
    "keyword_indexed": "embedding",
    "embedded": "summarising",
}

#: The cumulative milestones the UI draws, outermost first. Each is a superset
#: of the next, which is what makes a nested bar honest.
MILESTONES: tuple[str, ...] = ("stored", "searchable", "summarised")

#: ``empty`` — nothing stored yet. ``working`` — items still queued.
#: ``done`` — every stored item reached the end of the pipeline.
ProgressState = Literal["empty", "working", "done"]


def _int(counts: Mapping[str, Any], key: str) -> int:
    """A bucket count, tolerant of a missing or malformed status payload.

    ``/api/ultrawiki/status`` answers with ``"counts": {}`` while the service
    is still starting, and a progress model that raised there would take the
    whole settings screen down with it.
    """
    try:
        return max(0, int(counts.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def build_progress(counts: Mapping[str, Any] | None) -> dict[str, Any]:
    """Turn the per-stage buckets into the cumulative view everything reads.

    Args:
        counts: the ``counts`` block of the UltraWiki status payload — the
            five mutually exclusive bucket sizes.

    Returns:
        A dict with, in cumulative terms:

        ``total``
            every live item, whatever state it is in.
        ``searchable``
            items proven past the keyword-index stage. Findable by exact
            words right now.
        ``summarised``
            items that reached the last stage and have a distillation.
        ``waiting``
            items still queued for some stage — the ONE backlog number.
        ``failed``
            dead-lettered items. Excluded from ``searchable`` on purpose: an
            item can dead-letter at any stage, and the counts no longer say
            which, so claiming it is searchable would be a guess.
        ``next_step``
            what the largest waiting bucket is queued for, or ``None``.
        ``buckets`` / ``waiting_by_bucket``
            the raw state-machine numbers, for surfaces that genuinely want
            the state machine (the Contents filter does).
    """
    src: Mapping[str, Any] = counts or {}
    buckets = {name: _int(src, name) for name in STAGE_ORDER}
    failed = _int(src, "failed")
    total = sum(buckets.values()) + failed

    # Cumulative, so an item counts toward every milestone it has passed.
    searchable = buckets["keyword_indexed"] + buckets["embedded"] + buckets["distilled"]
    summarised = buckets["distilled"]

    waiting_by_bucket = {
        name: buckets[name] for name in NEXT_STEP_BY_BUCKET if buckets[name]
    }
    waiting = sum(waiting_by_bucket.values())

    # Which step the queue is mostly stuck behind. Biggest bucket wins; a tie
    # goes to the earliest stage, because that is the one holding the rest up.
    next_step: str | None = None
    if waiting_by_bucket:
        busiest = max(
            waiting_by_bucket,
            key=lambda name: (waiting_by_bucket[name], -STAGE_ORDER.index(name)),
        )
        next_step = NEXT_STEP_BY_BUCKET[busiest]

    state: ProgressState = "empty" if total == 0 else ("working" if waiting else "done")

    return {
        "state": state,
        "total": total,
        "searchable": searchable,
        "summarised": summarised,
        "waiting": waiting,
        "failed": failed,
        "next_step": next_step,
        "waiting_by_bucket": waiting_by_bucket,
        "buckets": {**buckets, "failed": failed},
        # Ready to draw: each milestone with the count that reached it and its
        # share of the corpus, so no client divides by zero on an empty store.
        "milestones": [
            {
                "id": name,
                "reached": reached,
                "share": (reached / total) if total else 0.0,
            }
            for name, reached in (
                ("stored", total),
                ("searchable", searchable),
                ("summarised", summarised),
            )
        ],
    }
