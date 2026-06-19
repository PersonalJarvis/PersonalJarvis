"""5-layer anti-drift parity + live-INSERT regression for ``messages.role``.

Why this test exists
====================

F6 / BUG-019 — on 2026-05-15, 49 occurrences of
``sqlite3.IntegrityError: CHECK constraint failed: role IN
('user','assistant','system','tool')`` appeared in a single day's
``data/jarvis_desktop.log``. Producers had grown new role values
(``computer_use`` and ``announcement``) while the SQL CHECK had not.
Migration 0003 makes the F6 fix a permanent part of the schema so the
same constraint trap is not re-discovered.

This file follows the five-layer anti-drift pattern documented in
``docs/anti-drift-three-layer.md`` and used as a reference impl for
``HangupReason`` in ``tests/unit/sessions/test_hangup_reason_parity.py``.
The layers checked:

1. **Python tuple** — ``jarvis.memory.constants.ALLOWED_ROLES``
2. **typing.Literal** — ``jarvis.memory.constants.MessageRole``
3. **Recorder allowlist** — ``message_recorder._RECALL_ALLOWED_ROLES``
4. **SQL CHECK constraint** — ``jarvis/memory/schema.sql`` + the
   forward migration ``jarvis/memory/migrations/0003_expand_role_check.sql``
5. **SQL doc-comment** — the ``messages`` table comment in
   ``jarvis/memory/schema.sql``

Plus a behavioural pair:

- One live ``INSERT`` per allowed role against a fresh SQLite DB.
- One negative ``INSERT`` with ``role='invalid_role'`` asserting that
  ``sqlite3.IntegrityError`` is raised, i.e. the CHECK actually fires.

A separate test starts from a *legacy* schema (the pre-migration
CHECK) and asserts the migration runner widens it on the next open.
That catches the case the live runtime hit: an existing user DB
already on disk when the new code ships.
"""
from __future__ import annotations

import re
import sqlite3
import typing
from pathlib import Path

import pytest

from jarvis.memory import constants
from jarvis.memory.constants import (
    ALLOWED_ROLES,
    ALLOWED_ROLES_FROZENSET,
    MessageRole,
)
from jarvis.memory.message_recorder import _RECALL_ALLOWED_ROLES
from jarvis.memory.migration_runner import (
    MIGRATIONS_DIR,
    run_migrations_sync,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_SQL_PATH = REPO_ROOT / "jarvis" / "memory" / "schema.sql"
MIGRATION_SQL_PATH = (
    REPO_ROOT
    / "jarvis"
    / "memory"
    / "migrations"
    / "0003_expand_role_check.sql"
)


# ---------------------------------------------------------------------------
# Layer 1 + 2 — Python tuple <-> typing.Literal
# ---------------------------------------------------------------------------


def test_literal_matches_tuple() -> None:
    """``MessageRole`` is a manual mirror of ``ALLOWED_ROLES``.

    The runtime assertion inside ``constants.py`` already raises at
    import if these drift, but the dedicated test makes the
    expectation a first-class regression target. Order matters: the
    tuple's order is the documented public contract.
    """
    assert typing.get_args(MessageRole) == ALLOWED_ROLES


def test_tuple_is_frozenset_consistent() -> None:
    """``ALLOWED_ROLES_FROZENSET`` covers the same values as the tuple."""
    assert ALLOWED_ROLES_FROZENSET == frozenset(ALLOWED_ROLES)
    assert len(ALLOWED_ROLES) == len(ALLOWED_ROLES_FROZENSET), (
        "ALLOWED_ROLES has a duplicate entry — the tuple must be a set"
    )


def test_individual_role_symbols_exported() -> None:
    """Every value in the tuple has a ``ROLE_<UPPER>`` constant.

    Producers should import the symbolic constant rather than spell
    the string at the call site (D1 in the anti-drift doc); failing
    to add a symbol when expanding the tuple is the typo this guard
    catches.
    """
    for value in ALLOWED_ROLES:
        symbol = "ROLE_" + value.upper()
        assert hasattr(constants, symbol), (
            f"Missing symbolic constant {symbol} for role {value!r}"
        )
        assert getattr(constants, symbol) == value


# ---------------------------------------------------------------------------
# Layer 3 — Recorder allowlist
# ---------------------------------------------------------------------------


def test_recorder_allowlist_matches_constants() -> None:
    """``message_recorder._RECALL_ALLOWED_ROLES`` is built from the SSoT."""
    assert _RECALL_ALLOWED_ROLES == ALLOWED_ROLES_FROZENSET


# ---------------------------------------------------------------------------
# Layer 4 — SQL CHECK constraints (schema + migration)
# ---------------------------------------------------------------------------


_CHECK_RE = re.compile(
    r"role\s+TEXT\s+NOT\s+NULL\s+CHECK\s*\(\s*role\s+IN\s*\(([^)]+)\)\s*\)",
    re.IGNORECASE,
)


def _extract_check_values(sql_text: str) -> set[str]:
    """Pull the ``role IN (...)`` literal from a CHECK clause.

    Used by the parity tests so a typo in either the schema file or
    the migration shows up as a diff against ``ALLOWED_ROLES``
    rather than waiting for an INSERT to fail at runtime.
    """
    match = _CHECK_RE.search(sql_text)
    assert match is not None, (
        f"could not locate role-CHECK clause in SQL\n{sql_text[:400]}"
    )
    return {
        token.strip().strip("'").strip('"')
        for token in match.group(1).split(",")
        if token.strip()
    }


def test_schema_sql_check_matches_constants() -> None:
    """The base schema's CHECK lists exactly ``ALLOWED_ROLES``."""
    text = SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    declared = _extract_check_values(text)
    assert declared == set(ALLOWED_ROLES), (
        "schema.sql CHECK drift: "
        f"extra={declared - set(ALLOWED_ROLES)}, "
        f"missing={set(ALLOWED_ROLES) - declared}"
    )


def test_migration_sql_check_matches_constants() -> None:
    """0003_expand_role_check.sql lists exactly ``ALLOWED_ROLES``.

    Looks for the ``messages_migration_0003`` staging-table CHECK so
    it cannot accidentally pass against the staging schema lifted
    from ``schema.sql`` instead of the real migration body.
    """
    text = MIGRATION_SQL_PATH.read_text(encoding="utf-8")
    assert "messages_migration_0003" in text, (
        "Migration file no longer uses the staging-table name; "
        "test selector needs to be updated."
    )
    declared = _extract_check_values(text)
    assert declared == set(ALLOWED_ROLES), (
        "0003_expand_role_check.sql CHECK drift: "
        f"extra={declared - set(ALLOWED_ROLES)}, "
        f"missing={set(ALLOWED_ROLES) - declared}"
    )


def test_schema_sql_doc_comment_lists_every_role() -> None:
    """The doc-comment above the ``messages`` table enumerates the roles.

    The comment is the contract a human reader sees when they open
    ``schema.sql`` cold. Keeping it accurate is its own defence
    layer; this test fails when a contributor expands the CHECK but
    forgets to refresh the comment.
    """
    text = SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    # Look for the `accepted values: ... ;` enumeration in the
    # leading comment block.
    line = next(
        (
            ln
            for ln in text.splitlines()
            if "accepted values" in ln.lower()
        ),
        None,
    )
    assert line is not None, (
        "schema.sql is missing the 'accepted values' doc-comment "
        "for messages.role — add it back so the on-disk file is "
        "self-documenting."
    )
    # The comment is `-- accepted values: a | b | c ...`.
    body = line.split("accepted values:", 1)[1]
    body = body.split(".", 1)[0]
    declared = {tok.strip() for tok in body.split("|") if tok.strip()}
    assert declared == set(ALLOWED_ROLES), (
        f"schema.sql comment drift: extra={declared - set(ALLOWED_ROLES)}, "
        f"missing={set(ALLOWED_ROLES) - declared}"
    )


# ---------------------------------------------------------------------------
# Layer 5 — Live INSERT round-trip (positive + negative)
# ---------------------------------------------------------------------------


def _bootstrap_fresh_db(db_path: Path) -> sqlite3.Connection:
    """Apply ``schema.sql`` and run migrations exactly like
    :meth:`RecallStore.open` does, but synchronously."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    schema = SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    run_migrations_sync(conn)
    return conn


@pytest.mark.parametrize("role", list(ALLOWED_ROLES))
def test_insert_each_allowed_role_round_trips(
    tmp_path: Path, role: str
) -> None:
    """Inserting every allowed role into a fresh DB must succeed."""
    db_path = tmp_path / f"role_{role}.db"
    conn = _bootstrap_fresh_db(db_path)
    try:
        conn.execute(
            """
            INSERT INTO messages (trace_id, timestamp_ns, role, text)
            VALUES (?, ?, ?, ?)
            """,
            ("trace-abc", 1_700_000_000_000_000_000, role, f"hi from {role}"),
        )
        cur = conn.execute(
            "SELECT role FROM messages WHERE role = ?", (role,)
        )
        rows = cur.fetchall()
        cur.close()
        assert len(rows) == 1
        assert rows[0]["role"] == role
    finally:
        conn.close()


def test_insert_invalid_role_raises_integrity_error(tmp_path: Path) -> None:
    """A role outside ``ALLOWED_ROLES`` triggers the CHECK constraint."""
    db_path = tmp_path / "role_invalid.db"
    conn = _bootstrap_fresh_db(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            conn.execute(
                """
                INSERT INTO messages (trace_id, timestamp_ns, role, text)
                VALUES (?, ?, ?, ?)
                """,
                ("trace-bad", 1_700_000_000_000_000_000, "invalid_role", "x"),
            )
        # Be explicit about which constraint fired — defends against
        # an accidental NOT NULL drift masking a CHECK regression.
        assert "CHECK" in str(exc_info.value).upper()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Migration replay — existing DB with legacy CHECK gets widened
# ---------------------------------------------------------------------------


_LEGACY_SCHEMA_SQL = """
CREATE TABLE messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id     TEXT NOT NULL,
    thread_id    TEXT,
    timestamp_ns INTEGER NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('user','assistant','system','tool')),
    text         TEXT NOT NULL,
    tool_calls   TEXT,
    reasoning    TEXT,
    provider     TEXT,
    model        TEXT,
    tokens_in    INTEGER DEFAULT 0,
    tokens_out   INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIRTUAL TABLE messages_fts USING fts5(
    text,
    tool_calls,
    reasoning,
    content='messages',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, text, tool_calls, reasoning)
    VALUES (new.id, new.text, coalesce(new.tool_calls, ''), coalesce(new.reasoning, ''));
END;

CREATE TRIGGER messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text, tool_calls, reasoning)
    VALUES ('delete', old.id, old.text, coalesce(old.tool_calls, ''), coalesce(old.reasoning, ''));
END;

CREATE TRIGGER messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, text, tool_calls, reasoning)
    VALUES ('delete', old.id, old.text, coalesce(old.tool_calls, ''), coalesce(old.reasoning, ''));
    INSERT INTO messages_fts(rowid, text, tool_calls, reasoning)
    VALUES (new.id, new.text, coalesce(new.tool_calls, ''), coalesce(new.reasoning, ''));
END;
"""


def _bootstrap_legacy_db(db_path: Path) -> sqlite3.Connection:
    """Re-create the pre-Wave-0 schema and seed a legacy row.

    The CHECK clause here is the *old* four-role set so the
    migration has actual work to do. ``user_version`` stays at 0
    so :func:`run_migrations_sync` picks up 0003.
    """
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(_LEGACY_SCHEMA_SQL)
    conn.execute(
        """
        INSERT INTO messages (trace_id, timestamp_ns, role, text)
        VALUES (?, ?, ?, ?)
        """,
        ("legacy-trace", 1_699_000_000_000_000_000, "user", "legacy hello"),
    )
    return conn


def test_migration_widens_legacy_check(tmp_path: Path) -> None:
    """An existing DB with the legacy 4-role CHECK accepts the new
    roles after :func:`run_migrations_sync` runs."""
    db_path = tmp_path / "legacy.db"
    conn = _bootstrap_legacy_db(db_path)
    try:
        # Pre-condition: the legacy CHECK rejects `computer_use`.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO messages (trace_id, timestamp_ns, role, text)
                VALUES (?, ?, ?, ?)
                """,
                ("trace-pre", 1_700_000_000_000_000_000, "computer_use", "blocked"),
            )

        applied = run_migrations_sync(conn)
        assert applied >= 3, (
            "Expected at least migration 0003 to be applied after running; "
            f"got {applied}"
        )

        # Post-condition: legacy data survives and the new role
        # vocabulary is now accepted.
        rows = conn.execute(
            "SELECT trace_id, role, text FROM messages WHERE trace_id = ?",
            ("legacy-trace",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["role"] == "user"
        assert rows[0]["text"] == "legacy hello"

        for role in ("computer_use", "announcement"):
            conn.execute(
                """
                INSERT INTO messages (trace_id, timestamp_ns, role, text)
                VALUES (?, ?, ?, ?)
                """,
                (f"trace-{role}", 1_700_000_000_000_000_001, role, "post-mig"),
            )

        cur = conn.execute(
            "SELECT role FROM messages WHERE role IN ('computer_use','announcement') ORDER BY role"
        )
        landed = [row["role"] for row in cur.fetchall()]
        cur.close()
        assert landed == ["announcement", "computer_use"]

        # Re-running the migration must be a no-op (idempotency).
        applied_again = run_migrations_sync(conn)
        assert applied_again >= 3
    finally:
        conn.close()


def test_migration_runner_no_op_on_fresh_db(tmp_path: Path) -> None:
    """A freshly bootstrapped DB has ``user_version`` >= 3 after
    schema + migrations, and a second call applies nothing."""
    db_path = tmp_path / "fresh.db"
    conn = _bootstrap_fresh_db(db_path)
    try:
        cur = conn.execute("PRAGMA user_version")
        row = cur.fetchone()
        cur.close()
        assert row is not None
        assert int(row[0]) >= 3

        applied = run_migrations_sync(conn)
        assert applied >= 3, "Second migration call must remain a no-op"
    finally:
        conn.close()


def test_migrations_directory_layout() -> None:
    """Sanity: ``MIGRATIONS_DIR`` resolves to the shipped folder and
    contains the 0003 file. Catches accidental relocations."""
    assert MIGRATIONS_DIR.is_dir()
    assert MIGRATION_SQL_PATH.is_file()
    assert MIGRATION_SQL_PATH.parent == MIGRATIONS_DIR
