"""Navigation vocabulary stays aligned from SQL storage through the public UI."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI

from jarvis.society.mars.navigation_models import NavigationState
from jarvis.society.mars.navigation_store import _SCHEMA
from jarvis.ui.web.mars_routes import router

ROOT = Path(__file__).resolve().parents[2]


def test_navigation_states_match_sql_openapi_typescript_and_localized_controls():
    states = {state.value for state in NavigationState}
    with sqlite3.connect(":memory:") as conn:
        conn.executescript(_SCHEMA)
        for index, state in enumerate(sorted(states)):
            conn.execute(
                "INSERT INTO navigation_commands(command_id,agent_id,fingerprint,state,record) "
                "VALUES(?,?,?,?,?)", (str(index), "actor", "fingerprint", state, "{}")
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO navigation_commands(command_id,agent_id,fingerprint,state,record) "
                "VALUES('bad','actor','fingerprint','completed','{}')"
            )
    app = FastAPI()
    app.include_router(router)
    components = app.openapi()["components"]["schemas"]
    assert set(components["NavigationState"]["enum"]) == states
    modes = components["PedestrianMoveCommand"]["properties"]["mode"]
    assert modes.get("const", modes.get("enum")) in ("pedestrian", ["pedestrian"])
    source = (ROOT / "jarvis/ui/web/frontend/src/components/society/mars/navigationApi.ts").read_text("utf-8")
    declaration = re.search(r"NAVIGATION_STATES = \[(.*?)\] as const", source)
    assert declaration is not None
    assert set(re.findall(r'"([a-z_]+)"', declaration.group(1))) == states
    for language in ("en", "de", "es"):
        locale = json.loads((ROOT / f"jarvis/ui/web/frontend/src/i18n/locales/society/{language}.json").read_text("utf-8"))["society"]["mars"]
        assert all(locale.get(f"move_state_{state}") for state in states)
        assert locale["stopped_in_place"] and locale["visit_scope"]
