"""What was learned is kept (C14) — the errand-to-wiki write-back.

When an errand ends, two kinds of knowledge deserve to outlive it: facts the
gather phase established as DURABLE (a preference, a relation, a standing
arrangement) with confidence the gate accepted, and everything the user
answered in the clarification round — those came from their head and are
authoritative by definition. Both go through the single existing wiki funnel
(``ingest_wiki_text``); the curator and its sensitive-content block remain the
only judges of page placement, and this module never touches vault files.

The success test, from the plan: no question is ever asked in two different
errands — because the first answer became a wiki fact the next gather phase
finds.
"""

from __future__ import annotations

import logging

from .context_gate import MIN_ACTIONABLE_CONFIDENCE, confidence_view
from .schema import TERMINAL_STATES, Errand, ErrandState

log = logging.getLogger(__name__)


def learnings_note(errand: Errand) -> str | None:
    """The wiki-ingest text for one finished errand, or None when there is
    nothing worth keeping.

    Pure and deliberately strict:
    - only TERMINAL errands, and never CANCELLED ones — "stop" also means
      stop touching my things;
    - only facts marked durable AND at or above the gate's own threshold: a
      fact too uncertain to act on is far too uncertain to write down;
    - user answers always qualify — they are 10/10 by definition.
    """
    if errand.state not in TERMINAL_STATES or errand.state is ErrandState.CANCELLED:
        return None

    keepable = [f for f in errand.facts if f.durable and f.confidence >= MIN_ACTIONABLE_CONFIDENCE]
    lines: list[str] = []
    if keepable:
        lines.append(f'Learned while running the errand "{errand.goal}":')
        lines.extend(
            f"- {f.statement} (confidence {confidence_view(f.confidence)},"
            f" from {f.source or 'a tool lookup'})"
            for f in keepable
        )
    if errand.answers.strip() and errand.asked_questions:
        lines.append("The user was asked and answered directly (authoritative):")
        lines.extend(f"- Asked: {q}" for q in errand.asked_questions)
        lines.append(f"- Answered, verbatim: {errand.answers.strip()}")

    return "\n".join(lines) if lines else None


async def keep_learnings(errand: Errand) -> None:
    """Push the note through the wiki funnel. Never raises, never blocks a run.

    Imports are deferred so merely wiring the keeper costs nothing at startup
    (nothing heavy on the boot path), and a process without a running curator
    degrades to a quiet, logged no-op instead of an error.
    """
    note = learnings_note(errand)
    if note is None:
        return
    try:
        from jarvis.memory.wiki.ingest_service import ingest_wiki_text
        from jarvis.memory.wiki.integration import get_running_curator

        curator = get_running_curator()
        if curator is None:
            log.debug("errand keeper: no running curator — learnings not persisted")
            return
        outcome = await ingest_wiki_text(
            curator=curator, text=note, source=f"errand:{errand.id[:8]}"
        )
        if outcome.success:
            log.info(
                "errand %s: kept %d learning line(s) in the wiki", errand.id, note.count("\n") + 1
            )
        else:
            log.info("errand %s: wiki declined the learnings (%s)", errand.id, outcome.error_code)
    except Exception:  # noqa: BLE001 — keeping notes must never break an outcome
        log.warning("errand keeper failed", exc_info=True)


__all__ = ["keep_learnings", "learnings_note"]
