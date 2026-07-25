"""The "is my knowledge base actually working?" checklist.

Why this module exists (2026-07-25 forensic): a user connected seven
integrations, activated Ultra mode, approved every source — and the knowledge
base stayed empty for days. Every individual surface was telling the truth and
the whole was still unreadable:

* the sources list showed three sources, all "approved" (permission granted —
  which reads as "done"),
* the pipeline said *"Everything ingested so far is fully processed"* (true,
  and catastrophically misleading: nothing had ever been fetched, so there was
  nothing to process),
* the settings said every capability slot was ready (also true),
* and the Plugins store said GitHub was connected (true again — but the
  UltraWiki side has no pull adapter for it, so it can never yield an item).

Nothing was broken. The user simply had no way to see that ONE step —
starting the first import — had never happened. Diagnosing it took a database
query, which is exactly what a settings screen exists to prevent.

So this module answers one question in one place, in the order a person would
ask it: is the mode on, can it store, can it read anything, has it read
anything, can it answer, and is it still busy. Every check carries a state, a
plain-English sentence, and — when the user can fix it — the action that does.

Design rules:

* **A check never guesses.** It reports what the store and the slot probes
  actually say; an unknown answer is ``attention`` with the reason, never a
  cheerful default.
* **Blocked ≠ broken.** A source whose integration has no pull adapter is
  ``blocked`` with an honest explanation, not an error the user could fix by
  clicking harder.
* **Working is its own state.** A first import running or a backlog draining
  is progress, and must not read as a fault.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = [
    "CheckState",
    "HealthCheck",
    "build_health",
]

#: ``ok`` — nothing to do. ``working`` — busy, will resolve itself.
#: ``attention`` — the user can fix this, and the action says how.
#: ``blocked`` — genuinely not possible yet; clicking cannot help.
CheckState = Literal["ok", "working", "attention", "blocked"]

#: Rank for the overall verdict: the worst check wins, in this order.
_SEVERITY: dict[str, int] = {"ok": 0, "working": 1, "attention": 2, "blocked": 3}


def _names(rows: list[dict[str, Any]], limit: int = 3) -> tuple[str, bool]:
    """A readable "A, B and C" plus whether the verb needs a plural.

    Worth the six lines: these sentences are the whole point of the checklist,
    and "GitHub are connected" undoes the care taken writing them.
    """
    labels = [str(r.get("label") or r.get("id") or "?") for r in rows[:limit]]
    extra = len(rows) - len(labels)
    if extra > 0:
        joined = ", ".join(labels) + f" and {extra} more"
    elif len(labels) > 1:
        joined = ", ".join(labels[:-1]) + f" and {labels[-1]}"
    else:
        joined = labels[0] if labels else ""
    return joined, len(rows) != 1


@dataclass(slots=True)
class HealthCheck:
    """One row of the checklist."""

    id: str
    title: str
    state: CheckState
    detail: str
    #: What the UI should offer, e.g. ``{"kind": "sync_all"}``. ``None`` = nothing
    #: to click: the check is fine, busy, or genuinely blocked.
    action: dict[str, Any] | None = None
    #: Free-form numbers the UI may render (counts, ETA seconds).
    facts: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "state": self.state,
            "detail": self.detail,
            "action": self.action,
            "facts": self.facts,
        }


def _mode_check(status: dict[str, Any]) -> HealthCheck:
    if status.get("enabled"):
        return HealthCheck(
            id="mode",
            title="Ultra mode is on",
            state="ok",
            detail="The Wiki section answers from the UltraWiki store.",
        )
    return HealthCheck(
        id="mode",
        title="Ultra mode is off",
        state="attention",
        detail=(
            "The normal wiki is answering. Nothing is lost — switching on "
            "picks up exactly where you left off."
        ),
        action={"kind": "enable_mode"},
    )


def _storage_check(status: dict[str, Any]) -> HealthCheck:
    if not status.get("started"):
        return HealthCheck(
            id="storage",
            title="Storage is not open",
            state="blocked",
            detail=(
                "The knowledge store could not be opened, so nothing can be "
                "saved or read. The app log says why."
            ),
        )
    in_use = str(status.get("backend_in_use") or "sqlite")
    configured = str(status.get("db_backend") or "sqlite")
    if configured != in_use:
        # The store degraded to the local floor — the wiki works, but not
        # where the user thinks it does, and that is worth saying out loud.
        return HealthCheck(
            id="storage",
            title=f"Storage fell back to {in_use}",
            state="attention",
            detail=(
                f"You chose {configured}, but it could not be reached, so the "
                f"local {in_use} store is answering. Your data is safe here."
            ),
            action={"kind": "open_settings", "tab": "storage"},
            facts={"configured": configured, "in_use": in_use},
        )
    where = "in a local file on this machine" if in_use == "sqlite" else f"in {in_use}"
    return HealthCheck(
        id="storage",
        title="Storage is working",
        state="ok",
        detail=f"Everything is stored {where}.",
        facts={"in_use": in_use},
    )


def _sources_check(
    status: dict[str, Any], candidates: list[dict[str, Any]]
) -> HealthCheck:
    """The check that would have caught the 2026-07-25 forensic in one glance."""
    sources = list(status.get("sources") or [])
    # A source whose integration has no reader can never import, so offering
    # "start the import" for it would send the user to press a button that
    # provably does nothing. Those belong to the integrations check, which
    # explains WHY, not here among the fixable ones.
    unreadable = {
        str(c.get("id"))
        for c in candidates
        if not c.get("has_pull_adapter", False) and c.get("id")
    }

    def _can_import(source: dict[str, Any]) -> bool:
        integration = str(source.get("integration_id") or "")
        return not (integration and integration in unreadable)

    if not sources:
        return HealthCheck(
            id="sources",
            title="No sources yet",
            state="attention",
            detail="Nothing is connected to read from. Add a source to begin.",
            action={"kind": "open_sources"},
        )

    approved = [s for s in sources if s.get("consent") == "approved"]
    pending = [s for s in sources if s.get("consent") == "pending"]
    importable = [s for s in approved if _can_import(s)]
    never_imported = [
        s
        for s in importable
        if not s.get("last_sync_at") and not s.get("active_job")
    ]
    importing = [s for s in approved if s.get("active_job")]
    failed = [s for s in approved if s.get("last_error")]
    facts = {
        "total": len(sources),
        "approved": len(approved),
        "pending": len(pending),
        "never_imported": len(never_imported),
        "importing": len(importing),
        "failed": len(failed),
        "no_reader": len(approved) - len(importable),
    }

    if failed:
        names, _plural = _names(failed)
        return HealthCheck(
            id="sources",
            title=f"{len(failed)} source(s) failed to import",
            state="attention",
            detail=f"{names} stopped with an error. Opening the source shows it.",
            action={"kind": "open_sources"},
            facts=facts,
        )
    if importing:
        return HealthCheck(
            id="sources",
            title=f"Importing from {len(importing)} source(s)",
            state="working",
            detail="The first read is running. Items appear as they arrive.",
            facts=facts,
        )
    if never_imported:
        # THE defect this checklist was written for: approving grants
        # permission, it does not fetch. Before auto-import on approval landed,
        # this state was invisible and looked exactly like "all good".
        names, plural = _names(never_imported)
        return HealthCheck(
            id="sources",
            title=f"{len(never_imported)} approved source(s) never imported",
            state="attention",
            detail=(
                f"{names} {'are' if plural else 'is'} allowed to be read but "
                f"{'have' if plural else 'has'} never actually been read. "
                "Start the import and the contents come in."
            ),
            action={"kind": "sync_all"},
            facts=facts,
        )
    if pending and not approved:
        return HealthCheck(
            id="sources",
            title="No source is approved yet",
            state="attention",
            detail=(
                "Nothing is read before you approve it. Approve a source and "
                "its import starts right away."
            ),
            action={"kind": "open_sources"},
            facts=facts,
        )
    if not importable:
        return HealthCheck(
            id="sources",
            title="No source can be imported yet",
            state="blocked",
            detail=(
                "Every approved source belongs to an app UltraWiki cannot "
                "read yet. Adding a folder or the built-in wiki works today."
            ),
            action={"kind": "open_sources"},
            facts=facts,
        )
    return HealthCheck(
        id="sources",
        title=f"{len(importable)} source(s) imported",
        state="ok",
        detail="Every source that can be read has been read at least once.",
        facts=facts,
    )


def _content_check(status: dict[str, Any]) -> HealthCheck:
    counts = dict(status.get("counts") or {})
    total = int(counts.get("total") or 0)
    if total == 0:
        return HealthCheck(
            id="content",
            title="The knowledge base is empty",
            state="attention",
            detail=(
                "Nothing has been stored yet. Import a source and its "
                "contents land here."
            ),
            action={"kind": "sync_all"},
            facts={"total": 0},
        )
    searchable = int(counts.get("keyword_indexed") or 0) + int(
        counts.get("embedded") or 0
    ) + int(counts.get("distilled") or 0)
    return HealthCheck(
        id="content",
        title=f"{total:,} item(s) stored".replace(",", " "),
        state="ok",
        detail=f"{searchable:,} of them are already searchable.".replace(",", " "),
        facts={"total": total, "searchable": searchable, **counts},
    )


def _search_check(status: dict[str, Any]) -> HealthCheck:
    legs = dict(status.get("search_legs") or {})
    keyword = dict(legs.get("keyword") or {})
    vector = dict(legs.get("vector") or {})
    counts = dict(status.get("counts") or {})
    keyword_ready = bool(keyword.get("available"))
    vector_ready = bool(vector.get("available"))
    facts = {"keyword": keyword_ready, "vector": vector_ready}

    if not keyword_ready:
        return HealthCheck(
            id="search",
            title="Search cannot answer",
            state="blocked",
            detail=str(
                keyword.get("reason")
                or "The keyword index is unavailable, so nothing can be found."
            ),
            facts=facts,
        )
    if int(counts.get("keyword_indexed") or 0) + int(counts.get("embedded") or 0) + int(
        counts.get("distilled") or 0
    ) == 0:
        return HealthCheck(
            id="search",
            title="Nothing to search yet",
            state="working",
            detail=(
                "Search is ready, but no item has finished processing. The "
                "first results appear within a minute of an import."
            ),
            facts=facts,
        )
    if not vector_ready:
        # Keyword-only is a real, working configuration — not a failure.
        return HealthCheck(
            id="search",
            title="Search works (keyword only)",
            state="attention",
            detail=(
                str(vector.get("reason") or "Semantic search is off.")
                + " Exact words are found; questions phrased differently are not."
            ),
            action={"kind": "open_settings", "tab": "embedding"},
            facts=facts,
        )
    return HealthCheck(
        id="search",
        title="Search works",
        state="ok",
        detail="Both exact words and meaning are searchable.",
        facts=facts,
    )


def _processing_check(status: dict[str, Any]) -> HealthCheck:
    counts = dict(status.get("counts") or {})
    pipeline = dict(status.get("pipeline") or {})
    captured = int(counts.get("captured") or 0)
    failed = int(counts.get("failed") or 0)
    state = str(pipeline.get("state") or "")
    facts = {"backlog": captured, "failed": failed, "pipeline_state": state}

    if failed:
        return HealthCheck(
            id="processing",
            title=f"{failed} item(s) could not be processed",
            state="attention",
            detail=(
                "They stay stored and keyword-searchable; only their "
                "summaries are missing. Retrying usually clears them."
            ),
            facts=facts,
        )
    if captured:
        return HealthCheck(
            id="processing",
            title=f"{captured:,} item(s) still being processed".replace(",", " "),
            state="working",
            detail=(
                "Summarising and indexing run in the background. Everything "
                "already processed is searchable now — you do not have to wait."
            ),
            facts=facts,
        )
    if state == "paused":
        return HealthCheck(
            id="processing",
            title="Background processing is paused",
            state="attention",
            detail=str(pipeline.get("reason") or "The pipeline is not running."),
            facts=facts,
        )
    return HealthCheck(
        id="processing",
        title="Everything is processed",
        state="ok",
        detail="No backlog. New items are picked up automatically.",
        facts=facts,
    )


def _integrations_check(
    status: dict[str, Any], candidates: list[dict[str, Any]]
) -> HealthCheck:
    """Connected-but-unreadable integrations, stated plainly.

    The 2026-07-25 forensic again: seven integrations showed "connected" in the
    Plugins store, and the user reasonably expected their contents to arrive.
    UltraWiki has no pull adapter for any of them, so they never could. That is
    a missing capability, not a misconfiguration — saying so beats letting
    someone hunt for the switch that would fix it.
    """
    pending = [c for c in candidates if not c.get("has_pull_adapter", False)]
    ready = [c for c in candidates if c.get("has_pull_adapter", False)]
    facts = {
        "connected": len(candidates),
        "readable": len(ready),
        "pending": len(pending),
    }
    if not candidates:
        return HealthCheck(
            id="integrations",
            title="No connected apps to read from",
            state="ok",
            detail=(
                "Local sources are enough to start. Connecting apps in the "
                "Plugins store adds more later."
            ),
            facts=facts,
        )
    if not ready:
        names, plural = _names(pending, limit=4)
        return HealthCheck(
            id="integrations",
            title=f"{len(pending)} connected app(s) cannot be imported yet",
            state="blocked",
            detail=(
                f"{names} {'are' if plural else 'is'} connected to Jarvis, but "
                f"UltraWiki has no reader for {'them' if plural else 'it'} "
                "yet, so nothing comes in from there. Local sources and "
                "folders work today."
            ),
            facts=facts,
        )
    return HealthCheck(
        id="integrations",
        title=f"{len(ready)} connected app(s) can be imported",
        state="ok",
        detail="Add them as sources to pull their contents in.",
        facts=facts,
    )


def build_health(
    status: dict[str, Any], candidates: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """The full checklist plus one overall verdict.

    Ordered the way a person asks the question: is it on, can it store, what
    can it read, has it read anything, can it answer, is it still busy.
    """
    checks = [
        _mode_check(status),
        _storage_check(status),
        _integrations_check(status, list(candidates or [])),
        _sources_check(status, list(candidates or [])),
        _content_check(status),
        _search_check(status),
        _processing_check(status),
    ]
    worst = max((_SEVERITY[c.state] for c in checks), default=0)
    overall = next(k for k, v in _SEVERITY.items() if v == worst)
    # The headline answers the only question that matters at a glance: can I
    # use this right now? A draining backlog or an unreadable integration does
    # NOT make the answer no — the rest of the corpus still answers.
    usable = any(
        c.id == "search" and c.state in ("ok", "attention") for c in checks
    ) and any(c.id == "content" and c.state == "ok" for c in checks)
    return {
        "overall": overall,
        "usable": usable,
        "checks": [c.as_dict() for c in checks],
    }
