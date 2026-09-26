"""Installed-state preparation is offline, additive and opt-in for team storage."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from jarvis.core.swarm_types import TaskSpec, TeamCreate
from jarvis.swarm.runtime import prepare_install, swarm_root
from jarvis.swarm.store import SCHEMA_VERSION, TeamRegistry


def test_normal_install_checks_real_bundle_without_creating_swarm_storage(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
    ordinary = tmp_path / "ordinary-memory.sqlite3"
    with sqlite3.connect(ordinary) as connection:
        connection.execute("CREATE TABLE memory (content TEXT)")
        connection.execute("INSERT INTO memory VALUES ('Existing ordinary agent memory')")
    before = ordinary.read_bytes()
    config = SimpleNamespace(memory=SimpleNamespace(data_dir=tmp_path), swarm_enabled=False)
    assert prepare_install(config) == {"local_ready": True, "migrated_teams": 0}
    assert prepare_install(config) == {"local_ready": True, "migrated_teams": 0}
    assert not (tmp_path / "swarm").exists()
    assert ordinary.read_bytes() == before


def test_normal_install_reports_corrupt_bundle_without_creating_storage(tmp_path, monkeypatch):
    from jarvis.swarm import sandbox

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    corrupt = tmp_path / "corrupt.wasm"
    corrupt.write_bytes(b"This is not the bundled runtime")
    monkeypatch.setattr(sandbox, "QUICKJS_PATH", corrupt)
    with pytest.raises(RuntimeError, match="integrity check failed"):
        prepare_install()
    assert not (tmp_path / "swarm").exists()


def test_prepare_install_migrates_existing_team_once_and_preserves_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    registry = TeamRegistry(tmp_path / "swarm")
    team = registry.create(
        TeamCreate(
            name="Retained team",
            goal="Preserve prior work",
            request_key="existing-install",
            tasks=[
                TaskSpec(
                    id="retained",
                    title="Retained task",
                    description="Preserve this task",
                    acceptance="Its identity and description remain unchanged",
                )
            ],
        )
    )
    store = registry.open(team["id"])
    before_tasks = store.records("tasks")
    with sqlite3.connect(store.path) as connection:
        record = json.loads(connection.execute("SELECT record FROM team").fetchone()[0])
        record.pop("acceptance", None)
        record.pop("network_bytes", None)
        connection.execute("UPDATE team SET record=?", (json.dumps(record),))
        connection.execute("PRAGMA user_version=1")
    first = prepare_install(verify_sandbox=False)
    second = prepare_install(verify_sandbox=False)
    assert first == second == {"local_ready": True, "migrated_teams": 1}
    restored = registry.open(team["id"])
    assert restored.get()["lead_id"] == team["lead_id"]
    assert restored.get()["acceptance"] == team["goal"]
    assert restored.get()["network_bytes"] == "0"
    assert restored.records("tasks") == before_tasks
    events = restored.records("events", limit=200)
    assert sum(event["kind"] == "storage.migrated" for event in events) == 1
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_data_directory_environment_overrides_configured_directory(tmp_path, monkeypatch):
    configured = tmp_path / "configured"
    selected = tmp_path / "selected"
    monkeypatch.setenv("JARVIS_DATA_DIR", str(selected))
    config = SimpleNamespace(memory=SimpleNamespace(data_dir=configured))
    assert swarm_root(config) == selected / "swarm"
    assert prepare_install(config, verify_sandbox=False)["migrated_teams"] == 0
    assert not configured.exists() and not selected.exists()
