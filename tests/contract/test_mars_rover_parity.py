"""Rover states agree across SQL, Python, HTTP and localized client controls."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi import FastAPI

from jarvis.cli_ctl.dynamic import build_api_group
from jarvis.society.mars.navigation_models import RideState
from jarvis.society.mars.navigation_store import _SCHEMA
from jarvis.ui.web.mars_routes import router

ROOT = Path(__file__).resolve().parents[2]


def test_ride_state_vocabulary_matches_every_boundary():
    states = {state.value for state in RideState}
    with sqlite3.connect(":memory:") as conn:
        conn.executescript(_SCHEMA)
        for state in states:
            conn.execute(
                "INSERT INTO rover_rides(ride_id,agent_id,vehicle_id,state,record) "
                "VALUES(?,?,?,?,?)",
                (state, "agent", "vehicle", state, "{}"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO rover_rides(ride_id,agent_id,vehicle_id,state,record) "
                "VALUES(?,?,?,?,?)",
                ("invalid", "agent", "vehicle", "animation_completed", "{}"),
            )
    app = FastAPI()
    app.include_router(router)
    assert set(app.openapi()["components"]["schemas"]["RideState"]["enum"]) == states
    source = (
        ROOT / "jarvis/ui/web/frontend/src/components/society/mars/navigationApi.ts"
    ).read_text("utf-8")
    declaration = re.search(r"RIDE_STATES = \[(.*?)\] as const", source)
    assert declaration is not None
    assert set(re.findall(r'"([a-z_]+)"', declaration.group(1))) == states
    for locale in ("en", "de", "es"):
        messages = json.loads(
            (ROOT / f"jarvis/ui/web/frontend/src/i18n/locales/society/{locale}.json").read_text(
                "utf-8"
            )
        )["society"]["mars"]
        assert all(messages[f"rover_state_{state}"] for state in states)
        assert messages["rover_scope"] and messages["rover_uncertain"]


def test_every_rover_action_is_discoverable_and_correctly_scoped_from_cli():
    app = FastAPI()
    app.include_router(router)
    calls = []

    def run(method, path, params, body, **kwargs):
        calls.append((method, path, body))
        return {"ok": True}

    cli = build_api_group(app.openapi(), run)
    for action in ("reserve", "board", "travel", "cancel", "exit"):
        payload = {"request_id": "same-attempt"}
        if action == "reserve":
            payload["vehicle_id"] = "outpost-rover"
        if action == "travel":
            payload["destination_dock_id"] = "outpost-b"
        args = [
            "mars",
            f"{action}-mars-rover",
            "--agent_id",
            "one",
            "--json-body",
            json.dumps(payload),
            "--yes",
        ]
        if action != "reserve":
            args.extend(["--ride_id", "ride-one"])
        result = CliRunner().invoke(cli, args)
        assert result.exit_code == 0, result.output
        suffix = "" if action == "reserve" else f"/ride-one/{action}"
        assert calls[-1] == ("post", "/api/society/mars/agents/one/rides" + suffix, payload)
