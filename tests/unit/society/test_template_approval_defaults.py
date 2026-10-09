"""Imported agents use the same approval default as manually created agents."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.society.runtime import SocietyRuntime
from jarvis.ui.web.society_routes import router


@pytest.mark.parametrize("wrapped", [False, True])
def test_import_uses_bypass_and_preserves_explicit_rules(tmp_path: Path, wrapped: bool):
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    app = FastAPI()
    app.include_router(router)
    app.state.society_factory = lambda: runtime
    template = {
        "schema": 1,
        "name": "Imported helper",
        "instructions": "Maintain the task list.",
        "require_approval": ["plugin:gmail:send"],
        "denies": ["core:shell"],
    }
    payload = {"kind": "agent", "agent": template} if wrapped else template
    with TestClient(app) as client:
        manual = client.post("/api/society/agents", json={"name": "Manual helper"})
        assert manual.status_code == 200, manual.text
        imported = client.post("/api/society/templates/install", json={"template": payload})
        assert imported.status_code == 200, imported.text
        agent = imported.json()["agent"]
        assert agent["approval_mode"] == manual.json()["agent"]["approval_mode"] == "bypass"
        assert agent["approval_rules"]["require_approval"] == ["plugin:gmail:send"]
        assert agent["denies"] == ["core:shell"]
        saved = client.get(f"/api/society/agents/{agent['agent_id']}").json()["agent"]
        assert saved["approval_mode"] == "bypass"
