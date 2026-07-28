"""Five-layer drift guard for the identity layer (AP-4 / BUG-008).

The identity value sets cross Python -> SQLite DDL -> Postgres DDL. Each of
those is a place where a hand-retyped list can silently diverge, and the
symptom is the nastiest kind: a value the code produces gets rejected by a
CHECK constraint on ONE backend, months later, on a user's machine. The
canonical lists live in ``jarvis/ultrawiki/identity.py``; everything else is
asserted equal to them here.
"""

from __future__ import annotations

import re
from pathlib import Path

import jarvis.ultrawiki
from jarvis.ultrawiki.identity import (
    MERGEABLE_TIERS,
    EntityKind,
    IdentifierKind,
    QueueStatus,
)
from jarvis.ultrawiki.store import PostgresStore

MIGRATION_SQL = (
    Path(jarvis.ultrawiki.__file__).parent / "migrations" / "0003_identity.sql"
).read_text(encoding="utf-8")
POSTGRES_DDL = "\n".join(PostgresStore.ddl_statements())


def check_values(sql: str, table: str, column: str) -> set[str]:
    """Extract the quoted value list of ``CHECK (<column> IN (...))`` from the
    statement that creates ``table``."""
    start = sql.index(f"uw_{table} (")
    fragment = sql[start:]
    match = re.search(
        rf"CHECK\s*\(\s*{column}\s+IN\s*\(([^)]*)\)", fragment, re.IGNORECASE
    )
    assert match is not None, f"no CHECK list for uw_{table}.{column}"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def test_sqlite_entity_kind_check_matches_the_enum():
    assert check_values(MIGRATION_SQL, "entities", "kind") == {
        kind.value for kind in EntityKind
    }


def test_sqlite_identifier_kind_check_matches_the_enum():
    assert check_values(MIGRATION_SQL, "identifiers", "kind") == {
        kind.value for kind in IdentifierKind
    }


def test_sqlite_queue_status_check_matches_the_enum():
    assert check_values(MIGRATION_SQL, "confirm_queue", "status") == {
        status.value for status in QueueStatus
    }


def test_sqlite_merge_tier_check_excludes_the_weak_tier():
    """A weak match writes nothing at all, so a weak merge row must be
    impossible rather than merely unusual."""
    assert check_values(MIGRATION_SQL, "merge_log", "tier") == {
        tier.value for tier in MERGEABLE_TIERS
    }


def test_postgres_identity_check_lists_match_the_enums():
    assert check_values(POSTGRES_DDL, "entities", "kind") == {
        kind.value for kind in EntityKind
    }
    assert check_values(POSTGRES_DDL, "identifiers", "kind") == {
        kind.value for kind in IdentifierKind
    }
    assert check_values(POSTGRES_DDL, "confirm_queue", "status") == {
        status.value for status in QueueStatus
    }
    assert check_values(POSTGRES_DDL, "merge_log", "tier") == {
        tier.value for tier in MERGEABLE_TIERS
    }


def test_both_dialects_declare_the_same_identity_tables_and_indexes():
    for table in ("entities", "identifiers", "confirm_queue", "merge_log"):
        assert f"uw_{table} (" in MIGRATION_SQL
        assert f"uw_{table} (" in POSTGRES_DDL
    for index in (
        "idx_uw_entities_source_ref",
        "idx_uw_identifiers_unique",
        "idx_uw_identifiers_value",
        "idx_uw_identifiers_len",
        "idx_uw_confirm_queue_status",
        "idx_uw_merge_log_winner",
    ):
        assert index in MIGRATION_SQL
        assert index in POSTGRES_DDL


def test_migration_is_numbered_after_the_current_head():
    """Forward-only: a new file never renumbers or rewrites a shipped one.

    The numbers must stay gapless and strictly ascending, and the identity
    layer must keep the slot it shipped in — later features append, they never
    renumber a file thousands of installs have already applied.
    """
    directory = Path(jarvis.ultrawiki.__file__).parent / "migrations"
    numbers = sorted(
        int(path.name[:4])
        for path in directory.glob("*.sql")
        if re.match(r"^\d{4}_", path.name)
    )
    assert numbers == list(range(1, len(numbers) + 1))
    assert [path.name for path in directory.glob("0003_*.sql")] == [
        "0003_identity.sql"
    ]
