"""An agent's runtime (jarvis / hermes / openclaw) from roster to chat runner."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.agent_chat.service import resolve_runner, session_runner
from jarvis.agent_chat.store import AgentChatStore
from jarvis.society.chat_binding import ensure_session
from jarvis.society.events import AgentRuntime
from jarvis.society.roster import Roster, RosterError
from jarvis.society.runtime import SocietyRuntime
from jarvis.society.store import SocietyStore


@pytest.fixture
async def roster(tmp_path: Path):
    store = SocietyStore(tmp_path / "society.db")
    await store.open()
    try:
        yield Roster(store)
    finally:
        await store.close()


async def test_new_agents_run_on_jarvis_by_default(roster: Roster):
    scout, _ = await roster.create(name="Scout")
    assert scout.runtime is AgentRuntime.JARVIS
    assert scout.to_dict()["runtime"] == "jarvis"


async def test_runtime_is_chosen_at_create_and_then_fixed(roster: Roster):
    hermit, _ = await roster.create(name="Hermit", runtime="hermes")
    assert hermit.runtime is AgentRuntime.HERMES
    for other in ("openclaw", "jarvis", "skynet"):
        with pytest.raises(RosterError):
            await roster.update("hermit", {"runtime": other})
    # Naming the runtime it already has is not a change.
    same = await roster.update("hermit", {"runtime": "hermes", "title": "Scout"})
    assert same.runtime is AgentRuntime.HERMES and same.title == "Scout"


async def test_the_lead_always_runs_on_jarvis(roster: Roster):
    jarvis, _ = await roster.create(name="Jarvis", tier="lead")
    with pytest.raises(RosterError):
        await roster.update(jarvis.agent_id, {"runtime": "hermes"})
    with pytest.raises(RosterError):
        await roster.create(name="Other", tier="lead", runtime="hermes")


async def test_external_runtime_stays_on_this_computer(roster: Roster, monkeypatch):
    from jarvis.society import roster as roster_mod

    monkeypatch.setattr(roster_mod, "_validate_computer", lambda value: value or None)
    with pytest.raises(RosterError):
        await roster.create(name="Both", runtime="openclaw", computer_id="box-1")
    local, _ = await roster.create(name="Local", runtime="hermes")
    with pytest.raises(RosterError):
        await roster.update(local.agent_id, {"computer_id": "box-1"})


async def test_old_society_database_gains_the_runtime_column(tmp_path: Path):
    db = tmp_path / "society.db"
    store = SocietyStore(db)
    await store.open()
    await store.close()
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE society_agents DROP COLUMN runtime")
    conn.commit()
    conn.close()
    store = SocietyStore(db)
    await store.open()
    try:
        agent, _ = await Roster(store).create(name="Scout")
        assert agent.runtime is AgentRuntime.JARVIS
    finally:
        await store.close()


def test_runtime_picks_the_runner_only_for_society_agents():
    assert resolve_runner("openai", surface="society", runtime="hermes") == "hermes-cli"
    assert resolve_runner("openai", surface="society", runtime="openclaw") == "openclaw-cli"
    assert resolve_runner("openai", surface="society", runtime="") == resolve_runner(
        "openai", surface="society"
    )
    # Runtimes belong to society agents; no other chat surface reads one.
    assert resolve_runner("openai", surface="jarvis", runtime="hermes") == resolve_runner(
        "openai", surface="jarvis"
    )


def test_chat_store_keeps_the_runtime(tmp_path: Path):
    store = AgentChatStore(tmp_path / "agent_chat.db")
    session = store.create_session(
        provider="openai",
        model="gpt-5.2",
        effort="",
        cwd=str(tmp_path),
        surface="society",
        session_id="society:hermit",
        runtime="hermes",
    )
    assert session.runtime == "hermes"
    assert session_runner(session) == "hermes-cli"
    store.close()


def test_old_chat_database_gains_the_runtime_column(tmp_path: Path):
    db = tmp_path / "agent_chat.db"
    AgentChatStore(db).close()
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE agent_chat_sessions DROP COLUMN runtime")
    conn.commit()
    conn.close()
    store = AgentChatStore(db)
    session = store.create_session(provider="openai", model="", effort="", cwd=str(tmp_path))
    assert session.runtime == ""
    store.close()


class _Svc:
    def __init__(self, store: AgentChatStore) -> None:
        self.store = store

    def is_running(self, _session_id: str) -> bool:
        return False


async def test_a_new_hermes_agent_chat_runs_on_hermes(tmp_path: Path):
    cfg = SimpleNamespace(memory=SimpleNamespace(data_dir=str(tmp_path / "data")))
    rt = SocietyRuntime(tmp_path, seed_starter_team=False, cfg=lambda: cfg)
    await rt.ensure_started()
    svc = _Svc(AgentChatStore(tmp_path / "agent_chat.db"))
    try:
        agent, _ = await rt.roster.create(
            name="Hermit", provider="openai", model="gpt-5.2", runtime="hermes"
        )
        session = ensure_session(svc, cfg, agent)
        assert session.runtime == "hermes"
        assert session_runner(session) == "hermes-cli"
        jarvis_agent, _ = await rt.roster.create(name="Plain", provider="openai")
        assert ensure_session(svc, cfg, jarvis_agent).runtime == ""
    finally:
        svc.store.close()
        await rt.close()
