"""No automatic escape from the Claude subscription to a stored Anthropic key.

User rule (2026-10-06): under NO condition may a mission whose Claude
subscription is spent, logged out or unreadable run on a stored per-token
Anthropic API key on its own — not on the first run, not on any resume. Only
an explicit per-mission approval may bill a key.

These tests drive the REAL worker factory and env builder from
``bootstrap_missions``. Every credential is a fake string; no secret store is
read.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.missions import init as mi
from jarvis.missions.capacity import WorkerCapacityUnavailable
from jarvis.missions.kontrollierer.decomposer import MissionPlan, Step
from jarvis.missions.manager import MissionManager
from jarvis.missions.state_machine import MissionState
from jarvis.missions.workers.api_agent_worker import ApiAgentWorker
from jarvis.missions.workers.claude_direct_worker import ClaudeDirectWorker
from tests.fakes.fake_mission_runtime import FakePaidOption, make_kontrollierer

FAKE_API_KEY = "sk-ant-api03-FAKE-NOT-A-SECRET"
FAKE_OAUTH = "sk-ant-oat01-FAKE-NOT-A-SECRET"


class World:
    """The probes the factory and env builder read, pinned to a known state."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.login: bool | None = None  # None = logged out / probe unreadable
        self.cooldown = False
        self.live_oauth: str | None = None
        self.native_subscription: object | None = None
        mp = monkeypatch
        mp.setattr(mi, "_live_subagent_provider", lambda _snapshot: "claude-api")
        mp.setattr(
            "jarvis.missions.workers.claude_direct_worker._resolve_claude_binary",
            lambda: "/usr/local/bin/claude",
        )
        mp.setattr(mi, "_claude_subscription_login_state", lambda: self.login)
        mp.setattr(
            "jarvis.claude_quota_state.claude_in_quota_cooldown",
            lambda **_k: self.cooldown,
        )
        # Every paid / cross-family door is open, so only the policy can close it.
        mp.setattr(mi, "_claude_cli_auth_viable", lambda: True)
        mp.setattr(mi, "_api_key_family_viable", lambda _p: True)
        mp.setattr(
            "jarvis.missions.workers.codex_direct_worker._codex_oauth_available",
            lambda: True,
        )
        mp.setattr("jarvis.codex_auth_state.codex_needs_reauth", lambda: False)
        mp.setattr("jarvis.codex_quota_state.codex_in_quota_cooldown", lambda **_k: False)
        mp.setattr("jarvis.core.config.get_jarvis_agent_secret", lambda _p: FAKE_API_KEY)
        mp.setattr(mi, "read_live_claude_oauth_token", lambda: self.live_oauth)
        mp.setattr(
            "jarvis.claude_auth.usable_native_claude_subscription",
            lambda: self.native_subscription,
        )
        mp.setattr(mi, "_assemble_worker_mcp_servers", lambda **_k: ())


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    return World(monkeypatch)


@pytest.fixture
async def stack(tmp_path: Path, world: World):
    result = await mi.bootstrap_missions(
        db_path=tmp_path / "missions.db", isolation_root=tmp_path / "sub-agents"
    )
    yield result
    await mi.shutdown_missions(result)


def _step() -> Step:
    return Step(slug="task", prompt="write the report")


# --- The factory never escapes to the key -----------------------------------------


@pytest.mark.parametrize(
    "login,cooldown,reason",
    [
        (None, False, "provider_auth"),  # logged out / unreadable login
        (False, False, "provider_auth"),  # login proven dead
        (None, True, "provider_quota"),  # spent window AND unreadable login
        (True, True, "provider_quota"),  # spent Claude Pro window
    ],
)
async def test_spent_or_unreadable_subscription_parks_instead_of_using_the_key(
    stack: dict[str, Any], world: World, login: bool | None, cooldown: bool, reason: str
) -> None:
    world.login, world.cooldown = login, cooldown
    factory = stack["kontrollierer"]._worker_factory

    with pytest.raises(WorkerCapacityUnavailable) as exc:
        factory(_step())

    assert (exc.value.reason, exc.value.provider) == (reason, "claude")


async def test_live_subscription_runs_claude_without_any_fallback(
    stack: dict[str, Any], world: World
) -> None:
    world.login = True
    worker = stack["kontrollierer"]._worker_factory(_step())
    assert isinstance(worker, ClaudeDirectWorker)
    assert worker.backend_fallback is False
    assert not isinstance(worker, ApiAgentWorker)


# --- The CLI environment never carries a per-token key ----------------------------


@pytest.mark.parametrize("native", [None, object()])
async def test_cli_env_never_carries_a_classic_api_key(
    stack: dict[str, Any], world: World, tmp_path: Path, native: object | None
) -> None:
    world.native_subscription = native  # with or without a readable login
    env = stack["kontrollierer"]._env_builder(tmp_path / "mission")
    assert env.get("ANTHROPIC_API_KEY") != FAKE_API_KEY
    assert FAKE_API_KEY not in env.values()


async def test_cli_env_keeps_the_subscription_login(
    stack: dict[str, Any], world: World, tmp_path: Path
) -> None:
    world.live_oauth = FAKE_OAUTH
    env = stack["kontrollierer"]._env_builder(tmp_path / "mission")
    assert FAKE_OAUTH in env.values()
    assert FAKE_API_KEY not in env.values()


def test_key_filter_rule() -> None:
    assert mi._anthropic_key_for_cli_env(FAKE_API_KEY, cli_present=True) is None
    assert mi._anthropic_key_for_cli_env(FAKE_OAUTH, cli_present=True) == FAKE_OAUTH
    assert mi._anthropic_key_for_cli_env(None, cli_present=True) is None
    # Without the CLI there is no subscription to protect; the key is untouched.
    assert mi._anthropic_key_for_cli_env(FAKE_API_KEY, cli_present=False) == FAKE_API_KEY


# --- First run and every resume stay parked until approval -------------------------


@pytest.fixture
async def manager(tmp_missions_db: Path):
    m = MissionManager(tmp_missions_db)
    await m.start()
    yield m
    await m.stop()


async def test_no_resume_ever_falls_back_to_the_key(
    stack: dict[str, Any], world: World, manager: MissionManager, tmp_path: Path
) -> None:
    """Park on a logged-out login, then run resume passes in every non-live
    state: the mission stays parked and no paid worker is ever built."""
    paid = FakePaidOption(None)
    real_factory = stack["kontrollierer"]._worker_factory
    built: list[Any] = []

    def _factory(step: Step) -> Any:
        worker = real_factory(step)
        built.append(worker)
        return worker

    plan = MissionPlan(steps=[_step()], n_workers=1)
    k = make_kontrollierer(manager, tmp_path / "k", plan, _factory, paid_option=paid)
    mission_id = await manager.dispatch(prompt="write the report")
    assert await k.run_mission(mission_id) == MissionState.WAITING_CAPACITY

    for login, cooldown in [(None, False), (False, False), (True, True), (None, True)]:
        world.login, world.cooldown = login, cooldown
        assert await k.resume_waiting_missions() == []
        view = await manager.mission(mission_id)
        assert view is not None and view.state == MissionState.WAITING_CAPACITY

    assert built == []  # no worker of any kind, and certainly no key worker
    assert paid.workers == []
