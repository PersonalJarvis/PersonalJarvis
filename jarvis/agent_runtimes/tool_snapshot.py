"""Discard obsolete eager tool snapshots when a Hermes conversation resumes."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path

log = logging.getLogger(__name__)


def refresh_tool_search_cache(home: Path, session_id: str | None) -> bool:
    """Invalidate only the resumed session's compiled tool catalog, not history.

    Hermes pins tool schemas across resumes, including tools now deferred by
    configuration. Keeping that eager pin alongside the discovery tools defeats
    tool search. Both the older inline name list and the content-addressed
    snapshot are caches; a NULL pin makes Hermes rebuild from its current config.
    Call while the runtime's profile slot is held, before starting its process.
    An unfamiliar native schema degrades to the existing cache, never a lost chat.
    """
    database = home / "state.db"
    if not session_id or not database.is_file():
        return False
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=rw", uri=True,
                                     timeout=1.0)) as connection, connection:
            row = connection.execute(
                "SELECT tool_names FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if not row or not row[0]:
                return False
            saved = row[0]
            if saved.lstrip().startswith(("[", "{")):
                snapshot = json.loads(saved)
            else:
                cached = connection.execute(
                    "SELECT prompt FROM system_prompts WHERE hash = ?", (saved,)
                ).fetchone()
                if not cached:
                    return False
                snapshot = json.loads(cached[0])
            tools = snapshot.get("tools", []) if isinstance(snapshot, dict) else snapshot
            if not isinstance(tools, list):
                return False
            names = [
                tool if isinstance(tool, str) else tool.get("function", {}).get("name", "")
                for tool in tools if isinstance(tool, (str, dict))
            ]
            if not any(str(name).startswith("mcp__jarvis__") for name in names):
                return False
            changed = connection.execute(
                "UPDATE sessions SET tool_names = NULL WHERE id = ? AND tool_names = ?",
                (session_id, saved),
            ).rowcount
        if changed:
            log.info("Hermes: discarded the resumed conversation's eager tool cache")
        return bool(changed)
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        log.warning("Hermes: could not refresh the native tool cache; retaining it", exc_info=True)
        return False
