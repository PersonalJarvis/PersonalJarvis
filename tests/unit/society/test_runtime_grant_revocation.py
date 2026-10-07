"""A runtime agent's gateway grants end with the seat they were made for."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agent_runtimes import gateway
from jarvis.society.roster import Roster
from jarvis.society.store import SocietyStore


class FakeGateway:
    """Records revocations; ``fail`` makes the next call raise."""

    def __init__(self) -> None:
        self.revoked: list[str] = []
        self.fail = False

    def revoke_agent(self, agent_id: str) -> int:
        if self.fail:
            self.fail = False
            raise RuntimeError("gateway gone")
        self.revoked.append(agent_id)
        return 1


@pytest.fixture
async def roster(tmp_path: Path):
    store = SocietyStore(tmp_path / "society.db")
    await store.open()
    try:
        yield Roster(store)
    finally:
        await store.close()


@pytest.fixture
def fake(monkeypatch) -> FakeGateway:
    fake = FakeGateway()
    monkeypatch.setattr(gateway, "revoke_agent", fake.revoke_agent, raising=False)
    return fake


async def test_a_new_provider_model_or_account_revokes_the_old_grants(roster, fake):
    await roster.create(name="Hermit", runtime="hermes", provider="openai", model="gpt-5.5")
    await roster.update("hermit", {"model": "gpt-5.2"})
    await roster.update("hermit", {"provider": "ollama", "model": "qwen3:8b"})
    await roster.update("hermit", {"account_id": "api-key"})
    assert fake.revoked == ["hermit", "hermit", "hermit"]


async def test_archiving_a_runtime_agent_revokes_its_grants(roster, fake):
    await roster.create(name="Claw", runtime="openclaw", provider="openai", model="gpt-5.5")
    await roster.archive("claw")
    assert fake.revoked == ["claw"]


async def test_other_edits_and_jarvis_agents_keep_their_grants(roster, fake):
    await roster.create(name="Hermit", runtime="hermes", provider="openai", model="gpt-5.5")
    await roster.update("hermit", {"title": "Scout", "model": "gpt-5.5"})
    await roster.create(name="Plain", provider="openai", model="gpt-5.5")
    await roster.update("plain", {"model": "gpt-5.2"})
    await roster.archive("plain")
    assert fake.revoked == []


async def test_a_failed_revocation_never_undoes_the_roster_change(roster, fake):
    await roster.create(name="Hermit", runtime="hermes", provider="openai", model="gpt-5.5")
    fake.fail = True
    updated = await roster.update("hermit", {"model": "gpt-5.2"})
    assert updated.model == "gpt-5.2"
    assert fake.revoked == []
