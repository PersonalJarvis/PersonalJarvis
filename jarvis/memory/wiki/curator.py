"""``WikiCurator`` — the Phase B1 Wave-2 orchestrator.

Wires the four parallel-built B1 components into one ingest pipeline:

    new source content
        │
        ▼
    CuratorLLM.propose_updates    (Instance D — "what should change?")
        │
        ▼  list[PageUpdate]
    AtomicWriter.apply             (Instance C — "write it safely")
        │
        ▼  WriteResult
    LogWriter.append_log_entry     (Instance B — "record what happened")
        │
        ▼
    return WriteResult

The curator owns no domain logic of its own. It is a composer: every
substantive decision (which pages to touch, whether to skip a write,
how to roll back, how to render the log entry) lives in the dependency
it delegates to. That makes each piece independently swappable —
Instance C can be replaced with an alternative writer, Instance D with
an alternative LLM, without touching this file.

See ``docs/phase-b1-wiki-curator/README.md`` Part 6 for the wave-2 plan
and ``docs/adr/0013-knowledge-wiki-architecture.md`` for the long-term
context.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .log_writer import LogWriter
from .protocols import (
    AtomicWriter,
    CuratorLLM,
    PageRepository,
    PageUpdate,
    VaultIndex,
    WriteResult,
)
from .session_links import (
    _WIKILINK_RE,
    SlugIndex,
    rewrite_body_links,
    strip_dangling_wikilinks,
)
from .telemetry import telemetry

log = logging.getLogger(__name__)


def _wikilink_for(path: Path, vault_root: Path) -> str:
    """Render a vault-relative wikilink for the ``log.md`` entry.

    ``vault_root/entities/ruben.md`` → ``[[entities/ruben]]``. The
    log writer accepts plain strings and rounds them through unchanged
    so we hand it the already-rendered link form here.
    """
    try:
        rel = path.resolve().relative_to(vault_root.resolve())
    except ValueError:
        # Pathological case: an update targeted somewhere outside the
        # vault. The atomic writer rejects that, but if we ever see it
        # here, surface the absolute path verbatim so the log still
        # tells the truth.
        return f"[[{path}]]"
    return f"[[{rel.with_suffix('').as_posix()}]]"


def _empty_result(backup_dir: Path) -> WriteResult:
    """Return a ``WriteResult`` for the salience-filter-zero case.

    The curator-LLM may legitimately return ``[]`` for smalltalk or
    content-free sources. The writer is never called in that path, so
    no backup exists either. We synthesise a result with the documented
    shape (three empty lists, ``backup_path`` pointing at the directory
    where backups would have landed). Callers check the three lists
    first; the path is informational only.
    """
    return WriteResult(
        applied=[],
        skipped_due_to_recent_edit=[],
        failed_validation=[],
        backup_path=backup_dir,
    )


class WikiCurator:
    """Top-level wiki-update orchestrator.

    Instances are cheap to construct and stateless from the caller's
    point of view — call ``ingest`` multiple times against the same
    instance.

    Parameters
    ----------
    repo:
        :class:`PageRepository` — parses + renders single pages. Passed
        through to the writer for post-write validation and to the
        curator-LLM so it can re-parse vault snapshots if needed.
    vault:
        :class:`VaultIndex` — whole-vault read view. Handed to the
        curator-LLM for the keyword-overlap top-slugs ranker.
    writer:
        :class:`AtomicWriter` — performs the backup + write + validate
        + rollback pipeline. Owns the vault's write surface entirely.
    llm:
        :class:`CuratorLLM` — proposes ``list[PageUpdate]`` from a
        source. Returns ``[]`` when the source is not salient.
    log_writer:
        :class:`LogWriter` — appends one ``log.md`` entry per
        successful ingest. Skipped when zero updates land.
    vault_root:
        Used for the log entry's ``[[entities/...]]``-style wikilinks
        and to synthesise a ``WriteResult`` when the LLM returns an
        empty proposal (the writer was never called, so no backup
        was taken).
    """

    def __init__(
        self,
        *,
        repo: PageRepository,
        vault: VaultIndex,
        writer: AtomicWriter,
        llm: CuratorLLM,
        log_writer: LogWriter,
        vault_root: Path,
    ) -> None:
        self._repo = repo
        self._vault = vault
        self._writer = writer
        self._llm = llm
        self._log = log_writer
        self._vault_root = Path(vault_root).resolve()

    async def ingest(
        self,
        source_content: str,
        source_label: str,
    ) -> WriteResult:
        """Run the full ingest pipeline once.

        Returns a :class:`WriteResult` no matter what. An empty result
        (all three lists empty) means the LLM decided nothing should
        change — that is a normal outcome, not a failure. Inspect
        ``applied`` / ``skipped_due_to_recent_edit`` / ``failed_validation``
        to learn what actually happened.
        """
        # ----- 1. ask the LLM which pages to touch ---------------------
        updates: list[PageUpdate] = await self._llm.propose_updates(
            source_content,
            source_label,
            repo=self._repo,
            vault=self._vault,
        )

        if not updates:
            log.debug(
                "WikiCurator: LLM proposed no updates for %r (salience filter or empty source)",
                source_label,
            )
            return _empty_result(self._writer.backup_manager.backup_dir)

        return await self.apply_external_updates(
            updates, source_label=source_label, verb="ingest",
        )

    async def apply_external_updates(
        self,
        updates: list[PageUpdate],
        *,
        source_label: str,
        verb: str = "merge",
        all_or_nothing: bool = False,
    ) -> WriteResult:
        """Apply pre-built updates through the full guarded write pipeline.

        The shared write surface for the legacy ``ingest`` path AND the
        Wave-2 Stage-2 consolidator / profile / self-doc writers: anchors
        vault-relative targets, enforces the create-or-refuse link rule,
        writes via the AtomicWriter (backup → secret guard → validate →
        rollback → FTS upsert), and chronicles applied writes in ``log.md``
        under ``verb`` (one of the schema.md log verbs — consolidation runs
        use ``merge``). ``all_or_nothing=True`` makes this call one write
        transaction: a lock conflict, secret block, write failure, or schema
        failure leaves every page in its pre-call state.
        """
        # The LLM is taught (via schema.md) to emit vault-relative targets
        # like "entities/ruben.md". Python's Path() treats that as a
        # relative path, which the atomic writer then resolves against the
        # process CWD and rejects as out-of-vault. Anchor every relative
        # target to the vault root here, so the writer always sees an
        # absolute path it can validate.
        updates = [self._anchor_to_vault(u) for u in updates]

        if not updates:
            return _empty_result(self._writer.backup_manager.backup_dir)

        # ----- 1b. enforce the schema's create-or-refuse link rule -----
        # schema.md:148 — a [[wikilink]] that resolves to no existing page
        # (or no page created in THIS same batch) must be demoted to plain
        # text, never left dangling. Deterministic, regex only, no LLM,
        # no I/O — mirrors the session-rollup post-pass.
        updates = self._demote_dangling_links(updates)

        # ----- 2. hand the proposal to the writer ----------------------
        # The writer takes the snapshot, applies each update via
        # tempfile+rename, re-validates each written page through repo,
        # and rolls back individual pages that fail validation.
        if all_or_nothing:
            result = await self._writer.apply(
                updates,
                repo=self._repo,
                all_or_nothing=True,
            )
        else:
            # Keep the legacy call shape for injected writer implementations
            # that predate the optional transaction keyword.
            result = await self._writer.apply(updates, repo=self._repo)

        # ----- 3. log only when at least one write actually landed -----
        # No applied pages → nothing to chronicle. The empty case can
        # happen when every update hit the 30s-concurrent-edit lock or
        # every page failed validation; in both situations the writer
        # already logged the details internally.
        if result.applied:
            try:
                await self._log.append_log_entry(
                    verb=verb,
                    subject=source_label,
                    pages_touched=[
                        _wikilink_for(p, self._vault_root) for p in result.applied
                    ],
                    source=source_label,
                    summary=self._summarise(updates, result),
                )
            except Exception as exc:  # noqa: BLE001
                # The page transaction has already landed. A secondary audit
                # log failure must not make the journal retry and potentially
                # duplicate the same fact; retain the authoritative writer
                # result and surface the auxiliary failure in logs.
                log.warning(
                    "WikiCurator: page write succeeded but log append failed: %s",
                    type(exc).__name__,
                )

        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _anchor_to_vault(self, upd: PageUpdate) -> PageUpdate:
        """Resolve a vault-relative ``target_path`` against the vault root.

        The schema instructs the LLM to use vault-relative paths like
        ``entities/ruben.md``. A bare relative ``Path`` resolves against
        the process CWD when the writer normalises it, which is wrong
        — the file must end up inside the vault. If the LLM ever emits
        an already-absolute path, we leave it untouched (the writer's
        out-of-vault assertion will then either accept or reject it on
        its own merits).
        """
        target = upd.target_path
        if not target.is_absolute():
            target = (self._vault_root / target).resolve()

        rename_from = upd.rename_from
        if rename_from is not None and not rename_from.is_absolute():
            rename_from = (self._vault_root / rename_from).resolve()

        if target == upd.target_path and rename_from == upd.rename_from:
            return upd  # already absolute — no rewrite needed

        return PageUpdate(
            target_path=target,
            operation=upd.operation,
            new_body=upd.new_body,
            rename_from=rename_from,
            reason=upd.reason,
        )

    def _demote_dangling_links(
        self, updates: list[PageUpdate]
    ) -> list[PageUpdate]:
        """Rewrite each update's body so no ``[[wikilink]]`` is left dangling.

        For every update, strips token-truncated ``[[`` fragments and then
        canonicalises resolvable links / demotes unresolvable ones to plain
        text (``session_links.rewrite_body_links``). "Resolvable" means the
        target maps to an existing durable vault page OR to a page being
        created/renamed in THIS same batch — the schema's "create the
        missing page during the same ingest" arm of the rule. Returns a new
        list of ``PageUpdate`` objects with cleaned bodies; updates whose
        body did not change are passed through unmodified. Increments
        ``wiki_links_refused_dangling`` once per demoted link.

        Pure: regex only, no LLM call, no disk write (AP-9/AP-11).
        """
        index = self._build_batch_slug_index(updates)
        cleaned: list[PageUpdate] = []
        for upd in updates:
            before_links = len(_WIKILINK_RE.findall(upd.new_body))
            body = strip_dangling_wikilinks(upd.new_body)
            body, _resolved = rewrite_body_links(body, index)
            # Every closed link either survived as [[...]] (resolved) or was
            # demoted to plain text; the difference is the refusal count.
            after_links = len(_WIKILINK_RE.findall(body))
            refused = before_links - after_links
            if refused > 0:
                telemetry.inc("wiki_links_refused_dangling", refused)
            if body == upd.new_body:
                cleaned.append(upd)
                continue
            cleaned.append(
                PageUpdate(
                    target_path=upd.target_path,
                    operation=upd.operation,
                    new_body=body,
                    rename_from=upd.rename_from,
                    reason=upd.reason,
                )
            )
        return cleaned

    def _build_batch_slug_index(self, updates: list[PageUpdate]) -> SlugIndex:
        """Build a :class:`SlugIndex` of every page a link may resolve to.

        Combines (a) the durable pages already on disk
        (``entities/`` ``concepts/`` ``projects/`` ``sessions/``) with
        (b) the slugs of pages this batch creates or renames into existence,
        so a sibling page born in the same ingest counts as "existing" and
        its link is preserved rather than refused. Slug is the filename stem
        relative to the vault root; the directory is its first path segment.
        """
        pages: list[tuple[str, str, list[str]]] = []
        for directory in ("entities", "concepts", "projects", "sessions"):
            page_dir = self._vault_root / directory
            if not page_dir.is_dir():
                continue
            for md_path in sorted(page_dir.glob("*.md")):
                if md_path.name.startswith("."):
                    continue
                pages.append((directory, md_path.stem, []))
        # Same-batch creations/renames resolve as if they already exist.
        for upd in updates:
            if upd.operation not in ("create", "rename"):
                continue
            try:
                rel = upd.target_path.resolve().relative_to(self._vault_root)
            except ValueError:
                continue
            parts = rel.with_suffix("").parts
            if len(parts) >= 2:
                pages.append((parts[0], parts[-1], []))
        return SlugIndex.from_pages(pages)

    def _summarise(
        self,
        updates: list[PageUpdate],
        result: WriteResult,
    ) -> str:
        """Compose a 1-2 sentence summary for the log entry.

        Counts the operations and includes any non-applied paths so
        the log is self-explanatory weeks later. Stays well under
        the schema's "summary: <2-3 sentences>" budget.
        """
        # Map applied paths back to operations for the count.
        applied_set = {p.resolve() for p in result.applied}
        op_counts: dict[str, int] = {}
        for upd in updates:
            if upd.target_path.resolve() in applied_set:
                op_counts[upd.operation] = op_counts.get(upd.operation, 0) + 1

        if not op_counts:
            return f"Ingested '{result_label_of(result)}': no pages applied."

        parts = [f"{n} {op}" for op, n in sorted(op_counts.items())]
        body = "; ".join(parts)
        skipped = len(result.skipped_due_to_recent_edit)
        failed = len(result.failed_validation)
        tail_bits: list[str] = []
        if skipped:
            tail_bits.append(f"{skipped} skipped (recent-edit lock)")
        if failed:
            tail_bits.append(f"{failed} rolled back (validation)")
        tail = f" Plus: {', '.join(tail_bits)}." if tail_bits else ""
        return f"Applied {body}.{tail}"


def result_label_of(result: WriteResult) -> str:
    """Best-effort short identifier for a ``WriteResult``.

    Used inside :meth:`WikiCurator._summarise` when no updates were
    applied. Exposed at module level so tests can re-render the same
    label without poking at the curator's internals.
    """
    return result.backup_path.name if result.backup_path else "no-backup"


__all__ = ["WikiCurator"]
