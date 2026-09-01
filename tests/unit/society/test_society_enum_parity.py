"""AP-4: every society enum is spelled identically in Python, SQL and TypeScript.

The values cross Python → SQLite CHECK → Pydantic → TS → UI. Instead of
trusting four hand-kept lists, this test reads the SQL and TS files as text
and pins them to the Python enums.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from jarvis.society import events

_ROOT = Path(__file__).resolve().parents[3]
_SQL = _ROOT / "jarvis" / "society" / "society_schema.sql"
_TS = _ROOT / "jarvis" / "ui" / "web" / "frontend" / "src" / "lib" / "societyApi.ts"

# (python enum, SQL column whose CHECK lists it, TS const name). ``state`` is
# pinned in the society_agents block only — rooms and approvals have their own.
_PINS = [
    (events.MsgType, "msg_type", "MSG_TYPES"),
    (events.Tier, "tier", "TIERS"),
    (events.AgentState, "society_agents.state", "AGENT_STATES"),
    (events.Checkpoint, "checkpoint", "CHECKPOINTS"),
    (events.PermissionCeiling, "permission_ceiling", "PERMISSION_CEILINGS"),
    (events.GrantMode, "grant_mode", "GRANT_MODES"),
    (events.KnowledgeScope, "knowledge_scope", "KNOWLEDGE_SCOPES"),
    (events.KnowledgeOrigin, "origin", "KNOWLEDGE_ORIGINS"),
    (events.RoomState, None, "ROOM_STATES"),
    (events.ApprovalState, None, "APPROVAL_STATES"),
    (events.RunState, None, "RUN_STATES"),
]


def _sql_checks(column: str) -> list[set[str]]:
    text = _SQL.read_text(encoding="utf-8")
    if "." in column:
        table, column = column.split(".", 1)
        block = re.search(rf"CREATE TABLE IF NOT EXISTS {table}\s*\((.*?)\);", text, re.S)
        assert block, f"table {table} not in schema"
        text = block.group(1)
    pattern = re.compile(rf"CHECK\s*\(\s*{column}\s+IN\s*\(([^)]*)\)", re.S)
    return [set(re.findall(r"'([^']+)'", m)) for m in pattern.findall(text)]


def _ts_const(name: str) -> set[str]:
    text = _TS.read_text(encoding="utf-8")
    match = re.search(rf"export const {name}\s*=\s*\[([^\]]*)\]\s*as const;", text, re.S)
    assert match, f"societyApi.ts must export {name}"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


@pytest.mark.parametrize(("enum", "column", "ts_name"), _PINS, ids=[p[2] for p in _PINS])
def test_python_sql_and_typescript_agree(enum, column, ts_name):
    py = {str(m) for m in enum}
    assert _ts_const(ts_name) == py, f"{ts_name} drifted from {enum.__name__}"
    if column is not None:
        checks = _sql_checks(column)
        assert checks, f"no CHECK for column {column}"
        for check in checks:
            assert check == py, f"SQL CHECK on {column} drifted from {enum.__name__}"


def test_room_and_approval_states_are_checked_in_sql():
    """These two columns are both called ``state`` in their tables, so they are
    pinned by table rather than by column name."""
    text = _SQL.read_text(encoding="utf-8")
    rooms = re.search(r"society_rooms\s*\((.*?)\);", text, re.S)
    approvals = re.search(r"approvals\s*\((.*?)\);", text, re.S)
    assert rooms and approvals
    state_list = re.compile(r"state.*?IN\s*\(([^)]*)\)", re.S)
    room_match = state_list.search(rooms.group(1))
    approval_match = state_list.search(approvals.group(1))
    assert room_match and approval_match
    room_states = set(re.findall(r"'([a-z]+)'", room_match.group(1)))
    approval_states = set(re.findall(r"'([a-z]+)'", approval_match.group(1)))
    assert room_states == {str(m) for m in events.RoomState}
    assert approval_states == {str(m) for m in events.ApprovalState}


def test_pydantic_envelope_uses_the_enum():
    field = events.SocietyEnvelope.model_fields["msg_type"]
    assert field.annotation is events.MsgType
