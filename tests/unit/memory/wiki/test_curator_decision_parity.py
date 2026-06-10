"""Five-layer parity for the Wave-2 journal vocab (BUG-008 defense).

Layers under test: Python tuple (constants.py) <-> Pydantic/typing Literal
(constants.py) <-> SQL CHECK constraint (0005_wiki_candidate_journal.sql).
These strings surface to the UI only as telemetry counter names, so there is
deliberately no TypeScript layer — this test is the single drift guard.
"""
from __future__ import annotations

import re
from pathlib import Path

from jarvis.memory.wiki.constants import (
    CANDIDATE_STATUSES,
    CURATOR_DECISIONS,
    CandidateStatus,
    CuratorDecision,
)

_MIGRATION = (
    Path(__file__).resolve().parents[4]
    / "jarvis" / "memory" / "migrations" / "0005_wiki_candidate_journal.sql"
)


def _check_values(sql: str, column: str) -> set[str]:
    """Extract the quoted values of the CHECK (...) IN (...) list for a column."""
    m = re.search(
        column + r"\s+IN\s*\(([^)]*)\)",
        sql,
        flags=re.IGNORECASE,
    )
    assert m, f"no CHECK IN-list found for column {column!r}"
    return set(re.findall(r"'([^']+)'", m.group(1)))


def test_python_tuple_matches_literal() -> None:
    assert set(CANDIDATE_STATUSES) == set(CandidateStatus.__args__)  # type: ignore[attr-defined]
    assert set(CURATOR_DECISIONS) == set(CuratorDecision.__args__)  # type: ignore[attr-defined]


def test_sql_check_matches_python_tuples() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert _check_values(sql, "status") == set(CANDIDATE_STATUSES)
    assert _check_values(sql, "decision") == set(CURATOR_DECISIONS)


def test_telemetry_counter_names_follow_decisions() -> None:
    """Counter naming contract: wiki_consolidator_<decision> for every decision
    except noop-free shortcuts — all four decisions get a counter (B8)."""
    expected = {f"wiki_consolidator_{d}" for d in CURATOR_DECISIONS}
    assert expected == {
        "wiki_consolidator_add",
        "wiki_consolidator_update",
        "wiki_consolidator_noop",
        "wiki_consolidator_invalidate",
    }
