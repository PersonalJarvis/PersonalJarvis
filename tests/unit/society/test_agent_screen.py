"""Society lifecycle for isolated per-agent screen leases."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.agent_screen.protocol import AgentScreenUnavailable
from jarvis.society.runtime import SocietyRuntime


class FakeScreenSession:
    def __init__(
        self,
        *,
        screen_id: str,
        owner: str,
        purpose: str,
        isolated: bool = True,
    ) -> None:
        self.screen_id = screen_id
        self.kind = "xvfb"
        self.owner = owner
        self.purpose = purpose
        self.isolated = isolated
        self.hidden = True
        self.alive_value = True

    def alive(self) -> bool:
        return self.alive_value


class FakeScreenManager:
    def __init__(self, *, available: bool = True, isolated: bool = True) -> None:
        self.available = available
        self.isolated = isolated
        self.acquire_calls: list[tuple[str, str, bool]] = []
        self.release_calls: list[str] = []
        self._next = 0

    def select_provider(self):
        if self.available:
            return SimpleNamespace(kind="xvfb"), ""
        return None, "no isolated provider"

    async def acquire(
        self,
        owner: str,
        *,
        purpose: str = "",
        require_isolated: bool = True,
    ):
        self._next += 1
        self.acquire_calls.append((owner, purpose, require_isolated))
        session = FakeScreenSession(
            screen_id=f"screen-{self._next}",
            owner=owner,
            purpose=purpose,
            isolated=self.isolated,
        )
        return SimpleNamespace(
            session=session,
            owner=owner,
            screen_id=session.screen_id,
        )

    async def release(self, lease) -> None:
        self.release_calls.append(lease.screen_id)
        lease.session.alive_value = False


async def _runtime(tmp_path, manager: FakeScreenManager) -> SocietyRuntime:
    rt = SocietyRuntime(
        tmp_path,
        seed_starter_team=False,
        agent_screen_manager=lambda: manager,
    )
    await rt.ensure_started()
    return rt


async def test_agent_screen_status_is_read_only_and_lease_is_idempotent(tmp_path):
    manager = FakeScreenManager()
    rt = await _runtime(tmp_path, manager)
    try:
        agent, _ = await rt.roster.create(name="Scout")

        status = await rt.agent_screen_status(agent.agent_id)
        assert status == {
            "available": True,
            "blocked_reason": None,
            "active": None,
        }
        assert manager.acquire_calls == []

        first = await rt.open_agent_screen(agent.agent_id, purpose="browser errand")
        second = await rt.open_agent_screen(agent.agent_id, purpose="ignored duplicate")
        assert first == second
        assert first["owner"] == f"society:{agent.agent_id}"
        assert first["purpose"] == "browser errand"
        assert first["isolated"] is True
        assert manager.acquire_calls == [
            (f"society:{agent.agent_id}", "browser errand", True)
        ]

        active = await rt.agent_screen_status(agent.agent_id)
        assert active["active"] == first
        assert await rt.close_agent_screen(agent.agent_id) is True
        assert await rt.close_agent_screen(agent.agent_id) is False
        assert manager.release_calls == ["screen-1"]
    finally:
        await rt.close()


async def test_agent_screen_respects_kill_switch_and_paused_state(tmp_path):
    manager = FakeScreenManager()
    rt = await _runtime(tmp_path, manager)
    try:
        agent, _ = await rt.roster.create(name="Scout")

        await rt.store.set_kill_switch(True)
        with pytest.raises(PermissionError, match="kill switch"):
            await rt.open_agent_screen(agent.agent_id)
        assert manager.acquire_calls == []

        await rt.store.set_kill_switch(False)
        await rt.roster.update(agent.agent_id, {"state": "paused"})
        with pytest.raises(PermissionError, match="paused"):
            await rt.open_agent_screen(agent.agent_id)
        assert manager.acquire_calls == []
    finally:
        await rt.close()


async def test_society_rejects_non_isolated_screen_even_if_manager_regresses(tmp_path):
    manager = FakeScreenManager(isolated=False)
    rt = await _runtime(tmp_path, manager)
    try:
        agent, _ = await rt.roster.create(name="Scout")
        with pytest.raises(AgentScreenUnavailable, match="not isolated"):
            await rt.open_agent_screen(agent.agent_id)
        assert manager.acquire_calls == [
            (f"society:{agent.agent_id}", "Scout isolated screen", True)
        ]
        assert manager.release_calls == ["screen-1"]
    finally:
        await rt.close()


async def test_runtime_close_releases_all_society_screen_leases(tmp_path):
    manager = FakeScreenManager()
    rt = await _runtime(tmp_path, manager)
    scout, _ = await rt.roster.create(name="Scout")
    archivist, _ = await rt.roster.create(name="Archivist")
    await rt.open_agent_screen(scout.agent_id)
    await rt.open_agent_screen(archivist.agent_id)

    await rt.close()

    assert sorted(manager.release_calls) == ["screen-1", "screen-2"]
