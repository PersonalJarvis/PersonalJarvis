"""Self-documentation page ``memory.md`` (Wave-2 B7).

A root-level ``type: meta`` page (precedent: ``schema.md``) that explains
how the two-stage memory works and shows its live status — page counts,
journal backlog, decision totals, recently updated pages. DETERMINISTIC:
rendered from counters and directory listings, never by an LLM — so it
can never be truncated, hallucinated, or expensive.

Refreshed (a) once at bootstrap and (b) after every consolidator run via
``Consolidator.on_run_complete``. Written through the shared guarded
pipeline (``WikiCurator.apply_external_updates``, AP-3) like every other
vault write.
"""
from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from jarvis.memory.wiki.protocols import PageUpdate
from jarvis.memory.wiki.telemetry import telemetry

if TYPE_CHECKING:
    from jarvis.memory.wiki.curator import WikiCurator
    from jarvis.memory.wiki.journal import CandidateJournal

log = logging.getLogger(__name__)

PAGE_NAME = "memory.md"

_PAGE_DIRS = ("entities", "concepts", "projects", "sessions")

_EXPLAINER = """\
## How my memory works

My long-term memory is this Obsidian vault. It grows from conversation in
two stages:

1. **Extractor (Stage 1).** After a conversation turn, a small background
   model pulls out candidate facts (people, preferences, projects,
   decisions) and appends them to a durable journal. It never edits pages.
2. **Consolidator (Stage 2).** In batches, a judge compares each candidate
   against the full bodies of the most related existing pages and decides:
   *add* a new page, *update* an existing one in place, *noop* when the
   vault already knows it, or *invalidate* a contradicted page (marked
   `valid_until` + `superseded-by` — never deleted).

Every write is guarded: backup snapshot, secret/PII refusal, schema
validation with rollback, and the create-or-refuse wikilink rule (no
dangling links). The contract all editors follow is `schema.md`.
"""


def render_memory_page(
    *,
    vault_root: Path,
    backlog_count: int,
    telemetry_snapshot: dict[str, int],
    now: _dt.datetime | None = None,
) -> str:
    """Render the full ``memory.md`` markdown. Pure aside from dir listing."""
    stamp = (now or _dt.datetime.now()).strftime("%Y-%m-%d %H:%M")
    today = stamp[:10]

    counts: dict[str, int] = {}
    for directory in _PAGE_DIRS:
        d = Path(vault_root) / directory
        counts[directory] = (
            sum(1 for p in d.glob("*.md") if not p.name.startswith("."))
            if d.is_dir() else 0
        )

    def _c(name: str) -> int:
        return int(telemetry_snapshot.get(name, 0))

    recent = _recently_updated(Path(vault_root), limit=10)
    recent_lines = (
        "\n".join(f"- [[{rel}]]" for rel in recent)
        if recent
        else "- (no pages yet)"
    )

    return (
        "---\n"
        "type: meta\n"
        "purpose: memory-self-documentation\n"
        f"updated: {today}\n"
        "---\n"
        "\n"
        "# My Memory\n"
        "\n"
        f"{_EXPLAINER}"
        "\n"
        "## Live status\n"
        "\n"
        f"- Last refreshed: {stamp}\n"
        f"- Pages: {counts['entities']} entities, {counts['concepts']} concepts, "
        f"{counts['projects']} projects, {counts['sessions']} sessions\n"
        f"- Candidate journal backlog: {backlog_count} pending\n"
        f"- Facts extracted so far: {_c('wiki_candidates_extracted')}\n"
        f"- Consolidator decisions: {_c('wiki_consolidator_add')} added, "
        f"{_c('wiki_consolidator_update')} updated, "
        f"{_c('wiki_consolidator_noop')} already known, "
        f"{_c('wiki_consolidator_invalidate')} superseded "
        f"({_c('wiki_consolidator_runs')} runs)\n"
        f"- Writes refused: {_c('wiki_writes_blocked_pii')} secret-shaped, "
        f"{_c('wiki_writes_blocked_truncated')} truncated, "
        f"{_c('wiki_links_refused_dangling')} dangling links demoted\n"
        "\n"
        "## Recently updated\n"
        "\n"
        f"{recent_lines}\n"
    )


def _recently_updated(vault_root: Path, *, limit: int) -> list[str]:
    """Newest non-archive pages by mtime, as vault-relative slugs."""
    candidates: list[tuple[float, str]] = []
    for directory in _PAGE_DIRS:
        d = vault_root / directory
        if not d.is_dir():
            continue
        for p in d.glob("*.md"):
            if p.name.startswith("."):
                continue
            try:
                candidates.append(
                    (p.stat().st_mtime, f"{directory}/{p.stem}")
                )
            except OSError:
                continue
    candidates.sort(reverse=True)
    return [rel for _, rel in candidates[:limit]]


async def refresh_memory_page(
    *,
    curator: WikiCurator,
    vault_root: Path,
    journal: CandidateJournal | None,
) -> bool:
    """Render + write ``memory.md``. Returns True when the write landed.

    Best-effort by contract: every failure is logged and swallowed — the
    self-documentation page must never break boot or a consolidator run.
    """
    try:
        vault_root = Path(vault_root)
        backlog = 0
        if journal is not None:
            try:
                backlog = journal.backlog_count()
            except Exception:  # noqa: BLE001
                backlog = 0
        # The renderer globs/stats the page dirs — disk I/O, so keep it off
        # the event loop even though a vault is typically a few hundred files.
        import asyncio

        body = await asyncio.to_thread(
            render_memory_page,
            vault_root=vault_root,
            backlog_count=backlog,
            telemetry_snapshot=telemetry.snapshot(),
        )
        exists = (vault_root / PAGE_NAME).is_file()
        update = PageUpdate(
            target_path=Path(PAGE_NAME),
            operation="update" if exists else "create",
            new_body=body,
            reason="self-documentation refresh",
        )
        result = await curator.apply_external_updates(
            [update], source_label="self-doc:memory.md", verb="update",
        )
        return bool(result.applied)
    except Exception as exc:  # noqa: BLE001
        log.warning("self_doc: refresh failed: %s", exc)
        return False


__all__ = ["PAGE_NAME", "refresh_memory_page", "render_memory_page"]
