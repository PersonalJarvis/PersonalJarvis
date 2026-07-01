#!/usr/bin/env python
"""Migrate the legacy flat-file workspace to the Karpathy-style wiki layout.

Reads the old layout:

    <source>/
    ├── USER.md
    ├── SOUL.md
    ├── MEMORY.md          (optional)
    └── people/
        └── <name>.md      (one file per person)

And produces the new layout:

    <target>/
    ├── schema.md          (must exist before migrating — created in B0.1)
    ├── index.md           (existing — updated with new entity links)
    ├── log.md             (existing — appended)
    └── entities/
        ├── ruben.md       (from USER.md)
        ├── jarvis-persona.md  (from SOUL.md)
        └── <name>.md      (one per people/*.md)

The script is **idempotent** and **non-destructive**:

- A timestamped tar backup of the source files is written to
  ``data/backups/wiki-migrate-<YYYYMMDDHHMMSS>.tar.gz`` before any writes.
- If a target entity page already exists, it is left alone (no overwrite).
- The legacy source files stay on disk — the migration only *copies* into
  the new layout. The legacy Curator-Merger keeps working against the
  originals until Phase B4 removes it.

Usage::

    # See what would happen without writing anything
    python scripts/wiki_migrate_v0_to_v1.py --dry-run

    # Actually do it
    python scripts/wiki_migrate_v0_to_v1.py --apply

    # Custom source / target paths (default: data/workspace/)
    python scripts/wiki_migrate_v0_to_v1.py --source PATH --target PATH --apply
"""
from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Windows console defaults to cp1252; arrows and em-dashes crash there.
# Same pattern as jarvis/__main__.py — convention from CLAUDE.md.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VAULT = REPO_ROOT / "data" / "workspace"
DEFAULT_BACKUP_DIR = REPO_ROOT / "data" / "backups"


# ---------------------------------------------------------------------------
# Slug + frontmatter helpers
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Map a free-form name to a kebab-case ASCII slug."""
    s = name.strip().lower()
    s = (
        s.replace("ä", "ae")  # i18n-allow
        .replace("ö", "oe")  # i18n-allow
        .replace("ü", "ue")  # i18n-allow
        .replace("ß", "ss")  # i18n-allow
    )
    s = _SLUG_RE.sub("-", s).strip("-")
    return s or "unnamed"


@dataclass(frozen=True)
class LegacyDoc:
    """A parsed legacy workspace file."""

    path: Path
    frontmatter: dict[str, str]
    body: str

    @property
    def name(self) -> str:
        """Best-effort canonical name: frontmatter ``name`` or filename stem."""
        return self.frontmatter.get("name") or self.path.stem


def parse_legacy_doc(path: Path) -> LegacyDoc:
    """Parse a legacy markdown file into frontmatter dict + body string.

    Tolerates files that have no frontmatter — those return an empty
    frontmatter dict and the entire file content as body.
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return LegacyDoc(path=path, frontmatter={}, body=text)
    # Find the closing ``---`` on its own line.
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        # Malformed — treat as no frontmatter rather than crashing.
        return LegacyDoc(path=path, frontmatter={}, body=text)
    raw_fm, body = parts[0].lstrip("---\n"), parts[1]
    fm: dict[str, str] = {}
    for line in raw_fm.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return LegacyDoc(path=path, frontmatter=fm, body=body.lstrip("\n"))


# ---------------------------------------------------------------------------
# Mapping legacy files to entity pages
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MigrationPlan:
    """One source-file → target-page mapping."""

    source: Path
    target: Path
    entity_kind: str
    canonical_name: str

    @property
    def slug(self) -> str:
        return self.target.stem


def plan_migrations(source_dir: Path, target_dir: Path) -> list[MigrationPlan]:
    """Build the list of file moves the migration intends to perform."""
    plans: list[MigrationPlan] = []
    entities_dir = target_dir / "entities"

    # USER.md → entities/ruben.md (or whatever the user's name is)
    user_md = source_dir / "USER.md"
    if user_md.exists():
        doc = parse_legacy_doc(user_md)
        name = doc.frontmatter.get("name") or "ruben"
        plans.append(
            MigrationPlan(
                source=user_md,
                target=entities_dir / f"{slugify(name)}.md",
                entity_kind="person",
                canonical_name=name,
            )
        )

    # SOUL.md → entities/jarvis-persona.md
    soul_md = source_dir / "SOUL.md"
    if soul_md.exists():
        plans.append(
            MigrationPlan(
                source=soul_md,
                target=entities_dir / "jarvis-persona.md",
                entity_kind="tool",
                canonical_name="Jarvis (persona)",
            )
        )

    # people/*.md → entities/<slug>.md per file
    people_dir = source_dir / "people"
    if people_dir.is_dir():
        for person_md in sorted(people_dir.glob("*.md")):
            doc = parse_legacy_doc(person_md)
            name = doc.frontmatter.get("name") or person_md.stem
            plans.append(
                MigrationPlan(
                    source=person_md,
                    target=entities_dir / f"{slugify(name)}.md",
                    entity_kind="person",
                    canonical_name=name,
                )
            )

    return plans


def render_entity_page(plan: MigrationPlan, legacy: LegacyDoc, today: str) -> str:
    """Render a new entity page that quotes the legacy body verbatim.

    The migration intentionally does NOT attempt to restructure the
    legacy body into the schema's `Summary` / `Facts` / `Relationships`
    sections. That is the WikiCurator's job in Phase B1. Here we only
    move the bytes into a valid entity-page shell so nothing is lost
    and the curator has a starting point to work from.
    """
    aliases_line = ""
    if "aliases" in legacy.frontmatter:
        aliases_line = legacy.frontmatter["aliases"]

    lines = [
        "---",
        "type: entity",
        f"entity_kind: {plan.entity_kind}",
        f"slug: {plan.slug}",
        f"aliases: [{aliases_line}]" if aliases_line else "aliases: []",
        f"created: {today}",
        f"updated: {today}",
        f"migrated_from: {plan.source.name}",
        "---",
        "",
        f"# {plan.canonical_name}",
        "",
        "## Summary",
        "",
        "_TODO — first WikiCurator run will populate this from the legacy body below._",
        "",
        "## Facts",
        "",
        "_TODO — facts to be extracted from legacy content by the WikiCurator._",
        "",
        "## Relationships",
        "",
        "_TODO — cross-references to be added by the WikiCurator._",
        "",
        "## Sources",
        "",
        f"- Migrated verbatim from `{plan.source.name}` on {today}.",
        "- See `data/backups/wiki-migrate-*.tar.gz` for the pre-migration copy.",
        "",
        "## Legacy content (verbatim, awaiting curation)",
        "",
        legacy.body.rstrip(),
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Backup + write
# ---------------------------------------------------------------------------

def make_backup(source_dir: Path, backup_dir: Path) -> Path:
    """Tar the legacy source files into ``data/backups/wiki-migrate-<ts>.tar.gz``."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y%m%d%H%M%S")
    out = backup_dir / f"wiki-migrate-{ts}.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        for item in ("USER.md", "SOUL.md", "MEMORY.md"):
            p = source_dir / item
            if p.exists():
                tar.add(p, arcname=f"workspace/{item}")
        people = source_dir / "people"
        if people.is_dir():
            tar.add(people, arcname="workspace/people")
    return out


def append_log(target_dir: Path, today_iso: str, plans: list[MigrationPlan]) -> None:
    """Append one migration entry to ``log.md`` listing every page touched."""
    log_path = target_dir / "log.md"
    page_links = ", ".join(f"[[entities/{p.slug}]]" for p in plans)
    entry = [
        "",
        f"## [{today_iso}] migrate | legacy flat workspace → v1 layout",
        "",
        f"- pages touched: {page_links}",
        "- source: scripts/wiki_migrate_v0_to_v1.py",
        f"- summary: migrated {len(plans)} legacy files into entity pages. "
        "Bodies copied verbatim into a 'Legacy content (awaiting curation)' "
        "section; the WikiCurator (B1) will restructure them into proper "
        "Summary/Facts/Relationships shape on first ingest run.",
    ]
    with log_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(entry) + "\n")


def append_index(target_dir: Path, plans: list[MigrationPlan]) -> None:
    """Replace the empty 'Entities' section in index.md with the new pages."""
    index_path = target_dir / "index.md"
    text = index_path.read_text(encoding="utf-8")
    entity_lines = "\n".join(
        f"- [[entities/{p.slug}]] — {p.canonical_name}" for p in plans
    )
    new_block = f"## Entities\n\n{entity_lines}\n"
    text = re.sub(
        r"## Entities\n\n\*[^*]*\*\n\n\(empty[^\)]*\)\n",
        new_block,
        text,
        count=1,
    )
    index_path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_VAULT,
        help="Legacy flat workspace directory (default: data/workspace/)",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_VAULT,
        help="Vault root that already contains schema.md (default: same as --source)",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=DEFAULT_BACKUP_DIR,
        help="Where to write the pre-migration tar (default: data/backups/)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write files. Without this flag the script is a dry-run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit dry-run flag (default when --apply is not set).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    source = args.source.resolve()
    target = args.target.resolve()

    if not (target / "schema.md").exists():
        print(
            f"ERROR: {target}/schema.md not found — run B0.1 first.",
            file=sys.stderr,
        )
        return 2

    plans = plan_migrations(source, target)
    if not plans:
        print(f"No legacy files found under {source} — nothing to migrate.")
        return 0

    apply = bool(args.apply) and not bool(args.dry_run)

    today = _dt.date.today().isoformat()
    today_iso = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    print(f"{'APPLY' if apply else 'DRY-RUN'} — migration plan ({len(plans)} files):\n")
    skipped: list[MigrationPlan] = []
    todo: list[MigrationPlan] = []
    for plan in plans:
        if plan.target.exists():
            print(f"  SKIP   {plan.source.relative_to(source)} "
                  f"→ {plan.target.relative_to(target)} (already exists)")
            skipped.append(plan)
        else:
            print(f"  CREATE {plan.source.relative_to(source)} "
                  f"→ {plan.target.relative_to(target)} ({plan.entity_kind})")
            todo.append(plan)

    if not todo:
        print("\nNothing to do — every target already exists. Migration is idempotent.")
        return 0

    if not apply:
        print("\nDry-run only. Re-run with --apply to actually write.")
        return 0

    # Backup before any writes.
    backup_path = make_backup(source, args.backup_dir)
    print(f"\nBackup written: {backup_path}")

    # Ensure target dirs exist.
    (target / "entities").mkdir(parents=True, exist_ok=True)

    for plan in todo:
        legacy = parse_legacy_doc(plan.source)
        rendered = render_entity_page(plan, legacy, today)
        plan.target.write_text(rendered, encoding="utf-8")
        print(f"  wrote  {plan.target.relative_to(target)}")

    # Append a single log entry covering every page touched.
    append_log(target, today_iso, todo)
    append_index(target, todo)
    print(f"\nLog + index updated. Migration complete: {len(todo)} pages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
