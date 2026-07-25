"""Five-layer drift guard (AP-4 / BUG-008): the SQL CHECK value lists must
equal the canonical enum value sets in ``jarvis/ultrawiki/types.py`` —
for the frozen SQLite schema AND the derived Postgres DDL."""

from __future__ import annotations

import re
from pathlib import Path

import jarvis.ultrawiki
from jarvis.ultrawiki.store import PostgresStore
from jarvis.ultrawiki.types import STATE_ORDER, ConsentState, DocType, ItemState

SCHEMA_SQL = (Path(jarvis.ultrawiki.__file__).parent / "schema.sql").read_text(
    encoding="utf-8"
)
POSTGRES_DDL = "\n".join(PostgresStore.ddl_statements())


def check_values(sql: str, column: str) -> set[str]:
    """Extract the quoted value list of ``CHECK (<column> IN (...))``."""
    match = re.search(rf"CHECK\s*\(\s*{column}\s+IN\s*\(([^)]*)\)", sql, re.IGNORECASE)
    assert match is not None, f"no CHECK list found for column {column!r}"
    return set(re.findall(r"'([^']+)'", match.group(1)))


def test_sqlite_state_check_matches_item_state_enum():
    assert check_values(SCHEMA_SQL, "state") == {state.value for state in ItemState}


def test_sqlite_consent_check_matches_consent_state_enum():
    assert check_values(SCHEMA_SQL, "consent") == {
        consent.value for consent in ConsentState
    }


def test_sqlite_doc_type_check_matches_doc_type_enum():
    assert check_values(SCHEMA_SQL, "doc_type") == {doc.value for doc in DocType}


def test_postgres_ddl_check_lists_match_enums():
    assert check_values(POSTGRES_DDL, "state") == {state.value for state in ItemState}
    assert check_values(POSTGRES_DDL, "consent") == {
        consent.value for consent in ConsentState
    }
    assert check_values(POSTGRES_DDL, "doc_type") == {doc.value for doc in DocType}


def test_state_order_is_the_forward_ladder_without_failed():
    """The ladder covers every good state exactly once; ``failed`` sits
    outside it (dead-letter, never a claim target)."""
    ladder = {state.value for state in STATE_ORDER}
    assert ladder == {state.value for state in ItemState} - {ItemState.FAILED.value}
    assert len(STATE_ORDER) == len(set(STATE_ORDER))
    assert ItemState.FAILED not in STATE_ORDER
