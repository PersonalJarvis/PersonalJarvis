"""Command-line entry point for the wiki curator.

Usage::

    # Ingest a single markdown file (or any text file)
    python -m jarvis.memory.wiki.cli ingest <path-to-source-file>

    # Custom vault location (defaults to wiki/obsidian-vault/)
    python -m jarvis.memory.wiki.cli ingest <source> --vault <path>

    # Dry-run flag — prints the LLM's proposed updates but never writes
    python -m jarvis.memory.wiki.cli ingest <source> --dry-run

The CLI is a thin wiring layer. It instantiates the four B1 components
with real paths, hands them to a :class:`WikiCurator`, calls
``ingest()``, and prints the resulting :class:`WriteResult` in a
human-readable form. There is no business logic here — every decision
lives in one of the four components.

Designed for ad-hoc smoke tests and one-off ingests. Phase B5 will wire
the curator into the runtime bus so the voice path can call it without
the CLI; this entry point stays available as the manual-testing
surface.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Windows console encoding (cp1252 by default) breaks on em-dashes and
# arrows; reconfigure to UTF-8 so user-visible output renders properly.
# Same pattern as scripts/wiki_migrate_v0_to_v1.py and jarvis/__main__.py.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_VAULT = REPO_ROOT / "data" / "workspace"
DEFAULT_BACKUP_DIR = REPO_ROOT / "data" / "backups"
DEFAULT_DB = REPO_ROOT / "data" / "jarvis.db"


def _build_curator(vault_root: Path):
    """Wire the real four B1 components into a :class:`WikiCurator`.

    Imports are local so ``--help`` and argparse failures do not pay
    the startup cost of pulling in the brain provider registry.
    """
    from jarvis.core.config import load_config

    from .atomic_writer import AtomicWriter as AtomicWriterImpl
    from .curator import WikiCurator
    from .curator_llm import WikiCuratorLLM
    from .log_writer import LogWriter
    from .page import MarkdownPageRepository
    from .vault_index import VaultIndex as VaultIndexImpl

    cfg = load_config()
    repo = MarkdownPageRepository()
    vault = VaultIndexImpl(repo=repo)
    writer = AtomicWriterImpl(
        vault_root=vault_root,
        backup_dir=DEFAULT_BACKUP_DIR,
    )
    llm = WikiCuratorLLM(
        config=cfg,
        schema_path=vault_root / "schema.md",
        log_path=vault_root / "log.md",
    )
    log_writer = LogWriter(log_path=vault_root / "log.md")

    return WikiCurator(
        repo=repo,
        vault=vault,
        writer=writer,
        llm=llm,
        log_writer=log_writer,
        vault_root=vault_root,
    )


def _run_reindex(vault_root: Path, db_path: Path) -> int:
    """Body of the ``reindex`` subcommand.

    Opens (or creates) ``data/jarvis.db``, ensures the ``wiki_fts`` schema,
    walks the vault, and upserts every page.  Prints the indexed count.
    Returns 0 on success, 1 on error.
    """
    import sqlite3

    from .fts_index import ensure_schema, index_vault

    if not vault_root.is_dir():
        print(f"ERROR: vault not found: {vault_root}", file=sys.stderr)
        return 1

    db_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
    except sqlite3.Error as exc:
        print(f"ERROR: cannot open DB {db_path}: {exc}", file=sys.stderr)
        return 1

    try:
        ensure_schema(conn)
        count = index_vault(vault_root, conn)
    except RuntimeError as exc:
        # FTS5 not available — raised by ensure_schema.
        print(f"ERROR: {exc}", file=sys.stderr)
        conn.close()
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: indexing failed: {exc}", file=sys.stderr)
        conn.close()
        return 1

    conn.close()
    print(f"Indexed {count} page(s) from {vault_root}  →  {db_path}")
    return 0


async def _run_ingest(
    source_path: Path,
    vault_root: Path,
    *,
    dry_run: bool,
) -> int:
    """Async body of the ``ingest`` subcommand."""
    if not source_path.is_file():
        print(f"ERROR: source file not found: {source_path}", file=sys.stderr)
        return 2

    if not (vault_root / "schema.md").is_file():
        print(
            f"ERROR: vault at {vault_root} has no schema.md — run B0 bootstrap first.",
            file=sys.stderr,
        )
        return 2

    source_content = source_path.read_text(encoding="utf-8")
    source_label = f"cli-ingest:{source_path.name}"

    print(f"{'DRY-RUN' if dry_run else 'INGEST'}  {source_path}  →  {vault_root}")
    print(f"        source-label: {source_label}")
    print(f"        size:         {len(source_content)} chars")
    print()

    # Scan the vault before we hand it to the curator — the LLM ranker
    # needs the in-memory index to pick relevant slugs.
    curator = _build_curator(vault_root)
    await curator._vault.scan(vault_root)    # noqa: SLF001 — CLI wiring

    if dry_run:
        # Same path as ingest but stops after the LLM step. Useful for
        # eyeballing what the brain would propose without touching disk.
        updates = await curator._llm.propose_updates(    # noqa: SLF001
            source_content,
            source_label,
            repo=curator._repo,                          # noqa: SLF001
            vault=curator._vault,                        # noqa: SLF001
        )
        if not updates:
            print("LLM returned no proposed updates (salience filter or empty source).")
            return 0
        print(f"LLM proposed {len(updates)} update(s):")
        for upd in updates:
            print(f"  {upd.operation:8s}  {upd.target_path}")
            if upd.reason:
                print(f"            reason: {upd.reason}")
        return 0

    result = await curator.ingest(source_content, source_label)

    print(f"RESULT  backup: {result.backup_path}")
    print(f"        applied: {len(result.applied)}")
    for p in result.applied:
        print(f"          + {p}")
    if result.skipped_due_to_recent_edit:
        print(f"        skipped (30s recent-edit lock): {len(result.skipped_due_to_recent_edit)}")
        for p in result.skipped_due_to_recent_edit:
            print(f"          ~ {p}")
    if result.failed_validation:
        print(f"        rolled back (validation): {len(result.failed_validation)}")
        for p in result.failed_validation:
            print(f"          x {p}")
    if not (result.applied or result.skipped_due_to_recent_edit or result.failed_validation):
        print("        (LLM returned no updates — nothing written)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m jarvis.memory.wiki.cli",
        description="Personal Jarvis wiki curator — command-line entry point.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_reindex = subparsers.add_parser(
        "reindex",
        help="Rebuild the FTS5 search index for the entire vault.",
    )
    p_reindex.add_argument(
        "--vault",
        type=Path,
        default=DEFAULT_VAULT,
        help=f"Vault root (default: {DEFAULT_VAULT}).",
    )
    p_reindex.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"SQLite DB path (default: {DEFAULT_DB}).",
    )
    p_reindex.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )

    p_ingest = subparsers.add_parser(
        "ingest",
        help="Ingest one source file into the wiki vault.",
    )
    p_ingest.add_argument(
        "source",
        type=Path,
        help="Path to the markdown / text file to ingest.",
    )
    p_ingest.add_argument(
        "--vault",
        type=Path,
        default=DEFAULT_VAULT,
        help=f"Vault root (default: {DEFAULT_VAULT}).",
    )
    p_ingest.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the LLM's proposed updates without writing anything.",
    )
    p_ingest.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    if args.command == "reindex":
        return _run_reindex(args.vault.resolve(), args.db.resolve())

    if args.command == "ingest":
        return asyncio.run(
            _run_ingest(
                args.source.resolve(),
                args.vault.resolve(),
                dry_run=bool(args.dry_run),
            )
        )

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
