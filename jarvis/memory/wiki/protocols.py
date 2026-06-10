"""Wiki-Curator inter-instance Protocols (binding contract for Phase B1).

Four parallel instances (A page model, B vault index, C atomic writer,
D curator LLM) implement these Protocols. Wave-2 integration wires them
together via dependency injection. The Protocols are the *only* shared
import surface between the instances.

Owned by Instance A. See ``docs/phase-b1-wiki-curator/README.md`` Part 4.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class WikiPage:
    """A single wiki page, parsed.

    Owned by Instance A. Instances B/C/D consume it read-only.
    ``body`` is the verbatim markdown body after the closing ``---``
    of the frontmatter. ``wikilinks`` is the order-preserved tuple
    of outgoing ``[[targets]]`` in canonical form (duplicates allowed).
    ``is_schema_valid`` is ``False`` when the page does not match the
    schema (missing frontmatter, type mismatch, etc.) — the page is
    still returned, never raised.
    """
    path: Path                              # absolute path in the vault
    page_type: str                          # entity | concept | project | session | meta
    slug: str
    frontmatter: dict[str, str]
    body: str
    wikilinks: tuple[str, ...]              # outgoing [[targets]] in canonical form
    is_schema_valid: bool


@dataclass(frozen=True, slots=True)
class PageUpdate:
    """One proposed change to one page.

    Owned by Instance A (as a data type). Produced by Instance D.
    Consumed by Instance C. ``new_body`` holds the full rendered
    page content including frontmatter — Instance C writes it
    verbatim. ``rename_from`` is only set when ``operation == "rename"``.
    """
    target_path: Path                       # where the page lives (or will live)
    operation: str                          # create | update | rename | archive
    new_body: str                           # full new body (frontmatter + sections)
    rename_from: Path | None = None         # only set when operation == "rename"
    reason: str = ""                        # short human-readable why


@dataclass(frozen=True, slots=True)
class WriteResult:
    """Returned by AtomicWriter.apply()."""
    applied: list[Path]                     # pages that were successfully written
    skipped_due_to_recent_edit: list[Path]  # the 30s-lock case
    failed_validation: list[Path]           # pages that the writer rolled back
    backup_path: Path                       # the tar of the pre-write state
    blocked_pii: list[Path] = field(default_factory=list)  # refused: secret/PII body (AP-2)


@runtime_checkable
class PageRepository(Protocol):
    """Instance A's interface. Read-only over a path or a string.

    Implementations parse a markdown file (or string) into a ``WikiPage``,
    extract wikilinks, validate against the schema. Pure functions —
    no disk writes, no LLM calls.
    """
    async def load(self, path: Path) -> WikiPage: ...
    async def parse(self, raw_markdown: str, path: Path) -> WikiPage: ...
    def render(self, page: WikiPage) -> str: ...
    def resolve_wikilink(
        self, link: str, vault_root: Path
    ) -> Path | None: ...   # None when broken


@runtime_checkable
class VaultIndex(Protocol):
    """Instance B's interface. Whole-vault view, read-only.

    Implementations scan the vault, build an in-memory index of
    ``{slug -> path}``, list pages by type, render ``index.md``,
    append ``log.md``. Does NOT modify entity/concept/project pages —
    only ``index.md`` and ``log.md``.
    """
    async def scan(self, vault_root: Path) -> None: ...
    def pages_by_type(self, page_type: str) -> list[WikiPage]: ...
    def find_by_slug(self, slug: str) -> WikiPage | None: ...
    def backlinks_to(self, slug: str) -> list[WikiPage]: ...
    async def render_index_md(self) -> str: ...
    async def append_log_entry(
        self, verb: str, subject: str, pages_touched: list[str],
        source: str, summary: str,
    ) -> None: ...


@runtime_checkable
class AtomicWriter(Protocol):
    """Instance C's interface. The only path that writes pages to disk.

    Implementations receive a list of ``PageUpdate`` objects, take a
    vault snapshot (tar to ``wiki-backups/wiki-<ts>.tar.gz``), apply
    updates via tempfile+rename, validate the resulting pages via the
    ``PageRepository``, roll back on any failure.

    Honours the 30-second concurrent-edit lock: any update whose target
    path was modified within the last 30 seconds is skipped and reported
    as a soft failure (not an exception).
    """
    async def apply(
        self, updates: list[PageUpdate], *,
        repo: PageRepository,
    ) -> WriteResult: ...


@runtime_checkable
class CuratorLLM(Protocol):
    """Instance D's interface. The intelligence layer.

    Given a source (some new content to ingest) and a snapshot of
    the current vault, returns a list of ``PageUpdate`` objects. May
    return an empty list when salience filtering decides nothing should
    change. Never touches disk; never calls the writer.
    """
    async def propose_updates(
        self, source_content: str, source_label: str,
        *,
        repo: PageRepository, vault: VaultIndex,
    ) -> list[PageUpdate]: ...


__all__ = [
    "WikiPage",
    "PageUpdate",
    "WriteResult",
    "PageRepository",
    "VaultIndex",
    "AtomicWriter",
    "CuratorLLM",
]
