"""The session's ``surface`` — one enum in three languages, and the migration.

``"jarvis"`` is the front page's chat (Jarvis with a keyboard), ``"agent"`` a
plain coding-agent session (the Agentic IDE's chat mode). The value crosses
Python -> SQLite -> Pydantic -> TypeScript, so the four spellings are pinned
against each other here (AP-4) instead of trusted to stay in step by hand.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import get_args

from jarvis.agent_chat import store as store_mod
from jarvis.agent_chat.store import DEFAULT_SURFACE, SURFACES, AgentChatStore
from jarvis.ui.web import agent_chat_routes

_ROOT = Path(__file__).resolve().parents[3]
_TS_API = _ROOT / "jarvis" / "ui" / "web" / "frontend" / "src" / "lib" / "agentChatApi.ts"


def test_python_and_pydantic_spell_the_surface_the_same():
    assert set(get_args(agent_chat_routes.SurfaceName)) == set(SURFACES)
    body_field = agent_chat_routes.CreateSessionBody.model_fields["surface"]
    assert set(get_args(body_field.annotation)) == set(SURFACES)
    assert body_field.default == DEFAULT_SURFACE


def test_typescript_spells_the_surface_the_same():
    """The TS union is read off the file text: no build step, no node."""
    if not _TS_API.is_file():
        import pytest

        pytest.skip("frontend sources not present")
    text = _TS_API.read_text(encoding="utf-8")
    match = re.search(r"export type AgentChatSurface\s*=\s*([^;]+);", text)
    assert match, "agentChatApi.ts must export `AgentChatSurface`"
    members = set(re.findall(r'"([a-z-]+)"', match.group(1)))
    assert members == set(SURFACES)


def test_old_rows_read_as_agent_sessions(tmp_path: Path):
    """A database from before the column: every row was a coding session."""
    db = tmp_path / "agent_chat.db"
    column = ",\n    surface          TEXT NOT NULL DEFAULT 'agent'"
    old_schema = store_mod._SCHEMA.replace(column, "")
    assert "surface" not in old_schema, "the old schema must not know the column"
    conn = sqlite3.connect(db)
    conn.executescript(old_schema)
    conn.execute(
        "INSERT INTO agent_chat_sessions (session_id, provider, created_ms, updated_ms) "
        "VALUES ('old1', 'openai', 1, 1)"
    )
    conn.commit()
    conn.close()

    store = AgentChatStore(db)
    old = store.get_session("old1")
    assert old is not None and old.surface == "agent"
    # And the migration is idempotent: reopening does not add the column twice.
    store.close()
    again = AgentChatStore(db)
    assert again.get_session("old1") is not None
    again.close()


def test_the_store_lists_one_surface_at_a_time():
    store = AgentChatStore(":memory:")
    jarvis = store.create_session(
        provider="openai", model="m", effort="", cwd=".", surface="jarvis"
    )
    agent = store.create_session(provider="openai", model="m", effort="", cwd=".")
    assert agent.surface == "agent"
    assert [s.session_id for s in store.list_sessions(surface="jarvis")] == [jarvis.session_id]
    assert [s.session_id for s in store.list_sessions(surface="agent")] == [agent.session_id]
    assert {s.session_id for s in store.list_sessions()} == {jarvis.session_id, agent.session_id}
    assert jarvis.to_dict()["surface"] == "jarvis"


def test_a_session_never_changes_its_surface():
    store = AgentChatStore(":memory:")
    s = store.create_session(provider="openai", model="m", effort="", cwd=".", surface="jarvis")
    store.update_session(s.session_id, surface="agent", title="renamed")
    after = store.get_session(s.session_id)
    assert after is not None and after.surface == "jarvis" and after.title == "renamed"


def test_an_unknown_surface_is_refused():
    import pytest

    store = AgentChatStore(":memory:")
    with pytest.raises(ValueError):
        store.create_session(provider="openai", model="m", effort="", cwd=".", surface="voice")
