"""Obsidian-vault connector: stream every markdown note of a vault.

Walks ``ctx.config['root']`` recursively for ``*.md`` files, skipping
hidden directories (``.obsidian``, ``.trash``, and any other dot-prefixed
directory). Yields one :class:`~jarvis.ultrawiki.types.RawItem` per note:

- ``external_id`` — POSIX relative path inside the vault (stable across
  runs; a rename is a delete + add, resolved by the reconcile pass).
- ``title`` — first ``# `` heading, falling back to the file name.
- ``timestamp_utc`` — file modification time.
- ``permalink`` — ``file://`` URI of the note.

Cursor / checkpoint / deletion semantics are inherited from
:mod:`jarvis.ultrawiki.connectors.local_folder` and documented there:
the incremental cursor is the highest ``st_mtime_ns`` seen (as a string,
advanced by the runtime from ``metadata["mtime_ns"]``), and deletions are
detected only by the runtime's stored-vs-yielded ``external_id``
comparison during a FULL backfill — a file walk cannot see files that no
longer exist, so no tombstone items are emitted here.
"""

from __future__ import annotations

from pathlib import Path

from jarvis.ultrawiki.connectors.local_folder import (
    NOISE_DIR_NAMES,
    LocalFolderConnector,
    first_h1_heading,
)
from jarvis.ultrawiki.types import ConnectorContext


class ObsidianVaultConnector(LocalFolderConnector):
    """Markdown-only local walk with Obsidian-specific skip rules."""

    id = "obsidian-vault"
    label = "Obsidian Vault"
    # auth / capabilities inherited: LOCAL_PATH, backfill + CURSOR + deletes.

    DEFAULT_EXTENSIONS = (".md",)
    # The vault's own internals ON TOP of the shared noise list — a vault kept
    # inside a project folder still must not import its node_modules.
    SKIP_DIR_NAMES = NOISE_DIR_NAMES | {".obsidian", ".trash"}

    def _extensions(self, ctx: ConnectorContext) -> tuple[str, ...]:
        # An Obsidian vault is markdown by definition; ignore any
        # 'extensions' config to keep the source honest.
        return self.DEFAULT_EXTENSIONS

    def _title_for(self, path: Path, body: str) -> str:
        return first_h1_heading(body) or path.stem


__all__ = ["ObsidianVaultConnector"]
