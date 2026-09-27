"""Portable station boundary contract using a labeled test-only executor.

This proves renderer independence and durable command semantics, not a real
provider journey, desktop-close persistence, or availability on untested hardware.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from fastapi import FastAPI

from jarvis.core.protocols import MarsStationExecutor
from jarvis.society.mars.models import CommandState, StationCommand
from jarvis.society.mars.service import MarsStationService
from jarvis.society.mars.store import MarsStore


class TestOnlyExecutor:
    """Deterministic fixture behind exactly the production protocol boundary."""

    __test__ = False

    def __init__(self):
        self.accepted = []

    async def authorize(self, *, agent_id: str, capability_id: str) -> None:
        assert agent_id == "fixture-agent" and capability_id == "communication-draft"

    async def dispatch(self, *, agent_id: str, command_id: str, trace_id: str, draft: str) -> dict:
        self.accepted.append(command_id)
        return {"state": "active", "task_ref": "fixture-task:one"}

    async def inspect(
        self, *, agent_id: str, command_id: str, trace_id: str, task_ref: str | None
    ) -> dict:
        assert task_ref == "fixture-task:one"
        return {"state": "completed", "task_ref": task_ref, "result_ref": "fixture-result:one"}

    async def cancel(
        self, *, agent_id: str, command_id: str, trace_id: str, task_ref: str | None
    ) -> dict:
        return {"state": "canceled", "task_ref": task_ref}


async def test_station_protocol_runs_without_renderer_and_replays_result(tmp_path):
    executor: MarsStationExecutor = TestOnlyExecutor()
    service = MarsStationService(MarsStore(tmp_path / "mars.db"), executor)
    try:
        command = StationCommand(
            request_id="fixture-request", draft="A test-only communication draft."
        )
        created = await service.submit("fixture-agent", command)
        # One claim/dispatch step, then inspection. The background wake may also run;
        # duplicate reconcile calls still cannot duplicate the trusted dispatch.
        await service.reconcile()
        await service.reconcile()
        snapshot = await service.snapshot()
        assert snapshot.commands[0].command_id == created.command_id
        assert snapshot.commands[0].state is CommandState.COMPLETED
        assert snapshot.commands[0].result_ref == "fixture-result:one"
        assert executor.accepted == [created.command_id]
        await service.close()
        await service.start()
        assert (await service.snapshot()).commands[0].result_ref == "fixture-result:one"
    finally:
        await service.close()


def test_station_state_vocabulary_matches_sql_and_no_graphics_imports():
    root = Path(__file__).parents[2] / "jarvis" / "society" / "mars"
    sql = (root / "schema.sql").read_text("utf-8")
    for state in CommandState:
        assert f"'{state.value}'" in sql
    for name in ("__init__.py", "models.py", "store.py", "service.py"):
        tree = ast.parse((root / name).read_text("utf-8"))
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        assert not any(
            module.startswith(("bpy", "OpenGL", "webview", "pygame", "three")) for module in modules
        )


def test_state_contract_matches_all_five_layers_and_localized_ui():
    from jarvis.ui.web.mars_routes import router

    root = Path(__file__).parents[2]
    states = {state.value for state in CommandState}
    sql = (root / "jarvis/society/mars/schema.sql").read_text("utf-8")
    checks = re.findall(r"CHECK\s*\(\s*state\s+IN\s*\(([^)]+)\)", sql, re.I)
    assert checks
    assert all(set(re.findall(r"'([^']+)'", check)) == states for check in checks)
    app = FastAPI()
    app.include_router(router)
    schemas = app.openapi()["components"]["schemas"]
    assert set(schemas["CommandState"]["enum"]) == states
    assert "draft" not in schemas["CommandRecord"]["properties"]
    frontend = root / "jarvis/ui/web/frontend/src"
    source = (frontend / "components/society/mars/api.ts").read_text("utf-8")
    block = re.search(r"COMMAND_STATES\s*=\s*\[([^]]+)\]", source)
    assert block and set(re.findall(r'"([^"]+)"', block[1])) == states
    for locale in ("en", "de", "es"):
        labels = json.loads((frontend / f"i18n/locales/society/{locale}.json").read_text("utf-8"))
        assert all(labels["society"]["mars"].get(f"state_{state}") for state in states)
