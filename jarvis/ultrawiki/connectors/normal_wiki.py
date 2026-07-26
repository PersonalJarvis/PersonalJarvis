"""Normal-wiki connector: read the built-in wiki vault as an UltraWiki source.

UltraWiki treats the zero-dependency wiki's Obsidian vault as a READ-ONLY
source (design doc 01, "why the normal wiki survives untouched"). The
vault root resolves through the one canonical resolver
(:func:`jarvis.memory.wiki.vault_root.resolve_vault_root`) — never through
``Path.cwd()`` — honoring an explicit ``ctx.config['vault_root']`` when
the runtime passes one.

Only pages under the four interactive page-kind directories
(``entities/``, ``concepts/``, ``projects/``, ``sessions/``) are yielded:

- ``external_id`` — ``"<kind>/<slug>"`` (e.g. ``"entities/viktoria"``).
- ``title`` — frontmatter ``title:`` value, else the first ``# `` heading,
  else the slug.
- ``permalink`` — ``file://`` URI of the page.

Cursor / checkpoint / deletion semantics are inherited from
:mod:`jarvis.ultrawiki.connectors.local_folder` (mtime-ns cursor;
deletions detected by the runtime's reconcile pass during full backfill).
"""

from __future__ import annotations

import logging
from pathlib import Path

from jarvis.ultrawiki.connectors.local_folder import (
    LocalFolderConnector,
    first_h1_heading,
)
from jarvis.ultrawiki.types import AuthKind, ConnectorContext

log = logging.getLogger(__name__)

_FRONTMATTER_SCAN_LINES = 50


def _frontmatter_title(body: str) -> str:
    """Extract a ``title:`` value from a leading YAML frontmatter block."""
    if not body.startswith("---"):
        return ""
    for line in body.splitlines()[1:_FRONTMATTER_SCAN_LINES]:
        if line.strip() == "---":
            break
        if line.startswith("title:"):
            return line.partition(":")[2].strip().strip("\"'")
    return ""


class NormalWikiConnector(LocalFolderConnector):
    """Read-only stream of the built-in wiki's vault pages."""

    id = "normal-wiki"
    label = "Built-in Wiki"
    auth = AuthKind.NONE  # the vault root resolves from config, no user path needed

    DEFAULT_EXTENSIONS = (".md",)
    PAGE_KINDS: tuple[str, ...] = ("entities", "concepts", "projects", "sessions")

    def _resolve_root(self, ctx: ConnectorContext) -> Path | None:
        # Lazy import: keeps 'import jarvis.ultrawiki.connectors' boot-cheap
        # (AP-26) — vault_root pulls in jarvis.core.paths.
        from jarvis.memory.wiki.vault_root import resolve_vault_root

        resolution = resolve_vault_root(ctx.config.get("vault_root"))
        root = resolution.path
        if not root.is_dir():
            log.warning(
                "%s: wiki vault root %s does not exist; yielding nothing",
                self.id,
                root,
            )
            return None
        return root

    def _extensions(self, ctx: ConnectorContext) -> tuple[str, ...]:
        return self.DEFAULT_EXTENSIONS

    def _sorted_files(
        self,
        root: Path,
        extensions: tuple[str, ...],
        skip_dirs: frozenset[str] | None = None,
    ) -> list[Path]:
        # skip_dirs is accepted for the base-class contract and deliberately
        # unused: this walk visits only the four page-kind directories, so
        # there is no unrelated tree a user could need to exclude.
        matches: list[Path] = []
        for kind in self.PAGE_KINDS:
            sub = root / kind
            if not sub.is_dir():
                continue
            for path in sub.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in extensions:
                    continue
                rel_parts = path.relative_to(root).parts
                if any(part.startswith(".") for part in rel_parts):
                    continue
                matches.append(path)
        # Sorted by external_id (suffix STRIPPED), because that is what the
        # backfill checkpoint compares against. Sorting by the path WITH '.md'
        # orders 'entities/foo-bar.md' before 'entities/foo.md' ('-' < '.')
        # while the ids order 'entities/foo' before 'entities/foo-bar' — a
        # resume from 'entities/foo-bar' would then skip 'entities/foo'
        # entirely.
        matches.sort(key=lambda p: self._external_id_for(root, p))
        return matches

    def _external_id_for(self, root: Path, path: Path) -> str:
        return path.relative_to(root).with_suffix("").as_posix()

    def _title_for(self, path: Path, body: str) -> str:
        return _frontmatter_title(body) or first_h1_heading(body) or path.stem


__all__ = ["NormalWikiConnector"]
