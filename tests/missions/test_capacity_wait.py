"""WAITING_CAPACITY: a mission without worker capacity is parked, never failed
(missions/capacity.py).

Pins the user's rules (2026-10-04, revised 2026-10-07):
- a subscription install moves between connected subscriptions for free,
  never to a per-token key without consent,
- a CLI that is merely installed does not make a subscription install
  (a key-only user keeps the cross-family chain, AP-22),
- an exhausted quota parks the mission with a checkpoint and a clear message,
- a parked mission survives restarts and the age-based cleanup.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from jarvis.missions import capacity
from jarvis.missions import init as mi
from jarvis.missions.capacity import (
    CHECKPOINT_NAME,
    WorkerCapacityUnavailable,
    pinned_to_subscription,
)
from jarvis.missions.events import MissionWaitingCapacity
from jarvis.missions.kontrollierer.decomposer import MissionPlan, Step
from jarvis.missions.manager import MissionManager
from jarvis.missions.recovery import startup_recover
from jarvis.missions.state_machine import MissionState, is_terminal, transition
from jarvis.missions.voice.readback import MAX_VOICE_CHARS, render_capacity_wait
from jarvis.missions.workers.api_agent_worker import ApiAgentWorker
from jarvis.missions.workers.claude_direct_worker import ClaudeDirectWorker
from jarvis.missions.workers.codex_direct_worker import CodexDirectWorker
from tests.fakes.fake_mission_runtime import (
    ApprovingCritic,
    FakeMissionWorker,
    make_kontrollierer,
)

#: The real login probe — the missions conftest stubs it for every test.
_REAL_LOGIN_PROBE = mi._positive_subscription_login

# --- State machine ------------------------------------------------------------


def test_waiting_capacity_is_not_terminal() -> None:
    assert not is_terminal(MissionState.WAITING_CAPACITY)


@pytest.mark.parametrize(
    "src", [MissionState.RUNNING, MissionState.CRITIQUING, MissionState.LOOPING]
)
def test_active_states_can_park(src: MissionState) -> None:
    assert transition(src, MissionState.WAITING_CAPACITY)


@pytest.mark.parametrize(
    "dst", [MissionState.RUNNING, MissionState.CANCELLED, MissionState.FAILED]
)
def test_parked_mission_can_resume_or_end(dst: MissionState) -> None:
    assert transition(MissionState.WAITING_CAPACITY, dst)


# --- Pinning signal -----------------------------------------------------------


def test_positive_login_pins() -> None:
    assert pinned_to_subscription(login_probe=lambda: True) is True


def test_key_only_install_is_not_pinned() -> None:
    """No subscription ever seen: the single-key fallback chain stays (AP-22)."""
    assert pinned_to_subscription() is False
    assert pinned_to_subscription(login_probe=lambda: False) is False


def test_connected_subscription_pins_every_worker() -> None:
    from jarvis.brain import background_policy

    background_policy.note_connected("claude-cli")
    assert pinned_to_subscription(login_probe=lambda: False) is True


def test_unreadable_signal_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object) -> bool:
        raise OSError("marker unreadable")

    monkeypatch.setattr("jarvis.brain.background_policy.subscription_mode", _boom)
    assert pinned_to_subscription(login_probe=lambda: False) is True


def test_a_failing_login_probe_is_not_a_login() -> None:
    def _boom() -> bool:
        raise OSError("probe crashed")

    assert pinned_to_subscription(login_probe=_boom) is False


def test_installed_but_logged_out_claude_cli_does_not_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding 1: a key-only user with Claude Code installed but never logged
    in is NOT a subscription install — never parked forever."""
    monkeypatch.setattr(
        "jarvis.missions.workers.claude_direct_worker._resolve_claude_binary",
        lambda: "/usr/local/bin/claude",
    )
    monkeypatch.setattr(mi, "_claude_subscription_login_state", lambda: None)
    monkeypatch.setattr(
        "jarvis.missions.workers.codex_direct_worker._codex_oauth_available", lambda: False
    )
    monkeypatch.setattr(mi, "_positive_subscription_login", _REAL_LOGIN_PROBE)
    assert mi.install_is_pinned() is False
    monkeypatch.setattr(mi, "_claude_subscription_login_state", lambda: True)
    assert mi.install_is_pinned() is True


# --- Worker factory: pinned resolution ----------------------------------------


def _set_claude_world(
    monkeypatch: pytest.MonkeyPatch, *, cooldown: bool = False
) -> None:
    monkeypatch.setattr(
        "jarvis.claude_quota_state.claude_in_quota_cooldown", lambda **_k: cooldown
    )


def test_pinned_claude_runs_claude_without_backend_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_claude_world(monkeypatch)
    worker = mi._pinned_claude_worker(None, binary_present=True, login=True)
    assert isinstance(worker, ClaudeDirectWorker)
    assert worker.backend_fallback is False


def test_pinned_claude_in_quota_cooldown_parks(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_claude_world(monkeypatch, cooldown=True)
    with pytest.raises(WorkerCapacityUnavailable) as exc:
        mi._pinned_claude_worker(None, binary_present=True, login=True)
    assert exc.value.reason == "provider_quota"
    assert exc.value.provider == "claude"


@pytest.mark.parametrize("login", [False, None])
def test_pinned_claude_without_live_login_parks(
    monkeypatch: pytest.MonkeyPatch, login: bool | None
) -> None:
    """A dead or absent subscription login never falls back to the API key."""
    _set_claude_world(monkeypatch)
    with pytest.raises(WorkerCapacityUnavailable) as exc:
        mi._pinned_claude_worker(None, binary_present=True, login=login)
    assert exc.value.reason == "provider_auth"


def test_pinned_claude_without_cli_does_not_use_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_claude_world(monkeypatch)
    with pytest.raises(WorkerCapacityUnavailable) as exc:
        mi._pinned_claude_worker(None, binary_present=False, login=None)
    assert exc.value.reason == "provider_unavailable"


@pytest.mark.parametrize(
    "oauth,reauth,capped,reason",
    [
        (True, True, False, "provider_auth"),
        (True, False, True, "provider_quota"),
        (False, False, False, "provider_auth"),  # no ChatGPT login: never on the key
    ],
)
def test_pinned_codex_without_capacity_raises(
    monkeypatch: pytest.MonkeyPatch, oauth: bool, reauth: bool, capped: bool, reason: str
) -> None:
    monkeypatch.setattr(
        "jarvis.missions.workers.codex_direct_worker._codex_oauth_available", lambda: oauth
    )
    monkeypatch.setattr("jarvis.codex_auth_state.codex_needs_reauth", lambda: reauth)
    monkeypatch.setattr(
        "jarvis.codex_quota_state.codex_in_quota_cooldown", lambda **_k: capped
    )
    with pytest.raises(WorkerCapacityUnavailable) as exc:
        mi._pinned_codex_worker(None)
    assert exc.value.reason == reason
    assert exc.value.provider == "codex"


def test_pinned_codex_runs_codex_without_backend_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jarvis.missions.workers.codex_direct_worker._codex_oauth_available", lambda: True
    )
    monkeypatch.setattr("jarvis.codex_auth_state.codex_needs_reauth", lambda: False)
    monkeypatch.setattr(
        "jarvis.codex_quota_state.codex_in_quota_cooldown", lambda **_k: False
    )
    worker = mi._pinned_codex_worker(None)
    assert isinstance(worker, CodexDirectWorker)
    assert worker.backend_fallback is False


class _Subscriptions:
    """Both subscription families' offline state, scripted."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.claude_login: bool | None = True
        self.claude_cooldown = False
        self.codex_oauth = True
        self.codex_cooldown = False
        mp = monkeypatch
        mp.setattr(
            "jarvis.missions.workers.claude_direct_worker._resolve_claude_binary",
            lambda: "/usr/local/bin/claude",
        )
        mp.setattr(mi, "_claude_subscription_login_state", lambda: self.claude_login)
        mp.setattr(
            "jarvis.claude_quota_state.claude_in_quota_cooldown",
            lambda **_k: self.claude_cooldown,
        )
        mp.setattr(
            "jarvis.missions.workers.codex_direct_worker._codex_oauth_available",
            lambda: self.codex_oauth,
        )
        mp.setattr("jarvis.codex_auth_state.codex_needs_reauth", lambda: False)
        mp.setattr(
            "jarvis.codex_quota_state.codex_in_quota_cooldown",
            lambda **_k: self.codex_cooldown,
        )


def test_spent_claude_moves_to_the_codex_subscription_for_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subs = _Subscriptions(monkeypatch)
    subs.claude_cooldown = True
    worker = mi._subscription_worker("claude", None)
    assert isinstance(worker, CodexDirectWorker)
    assert worker.backend_fallback is False


def test_spent_codex_moves_to_the_claude_subscription_for_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old codex -> Claude Max path, restored (subscription to subscription)."""
    subs = _Subscriptions(monkeypatch)
    subs.codex_cooldown = True
    worker = mi._subscription_worker("codex", None)
    assert isinstance(worker, ClaudeDirectWorker)
    assert worker.backend_fallback is False


def test_no_subscription_with_capacity_raises_the_configured_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subs = _Subscriptions(monkeypatch)
    subs.claude_cooldown = True
    subs.codex_oauth = False
    with pytest.raises(WorkerCapacityUnavailable) as exc:
        mi._subscription_worker("claude", None)
    assert (exc.value.reason, exc.value.provider) == ("provider_quota", "claude")


def test_pinned_api_provider_without_key_does_not_cross_to_a_metered_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mi, "_api_key_family_viable", lambda _p: False)
    monkeypatch.setattr(mi, "_assemble_worker_mcp_servers", lambda **_k: ())
    subs = _Subscriptions(monkeypatch)
    subs.claude_login = None
    subs.codex_oauth = False

    def _forbidden(*_a: object, **_k: object) -> None:
        raise AssertionError("a pinned mission must not cross provider families")

    monkeypatch.setattr(mi, "_cross_family_last_resort_worker", _forbidden)
    with pytest.raises(WorkerCapacityUnavailable) as exc:
        mi._resolve_api_agent_worker("openrouter", "task", pinned=True)
    assert exc.value.provider == "openrouter"


def test_pinned_api_provider_without_key_may_use_a_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mi, "_api_key_family_viable", lambda _p: False)
    monkeypatch.setattr(mi, "_assemble_worker_mcp_servers", lambda **_k: ())
    _Subscriptions(monkeypatch)
    worker = mi._resolve_api_agent_worker("openrouter", "task", pinned=True)
    assert isinstance(worker, ClaudeDirectWorker)


def test_pinned_api_provider_with_key_runs_on_its_own_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user picked that API provider themselves — running it is no switch."""
    monkeypatch.setattr(mi, "_api_key_family_viable", lambda _p: True)
    monkeypatch.setattr(mi, "_assemble_worker_mcp_servers", lambda **_k: ())
    worker = mi._resolve_api_agent_worker("openrouter", "task", pinned=True)
    assert isinstance(worker, ApiAgentWorker)
    assert worker.provider == "openrouter"


def test_last_resort_skips_metered_keys_when_not_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jarvis.missions.workers.claude_direct_worker._resolve_claude_binary",
        lambda: None,
    )
    monkeypatch.setattr(
        "jarvis.missions.workers.codex_direct_worker._codex_oauth_available",
        lambda: False,
    )
    monkeypatch.setattr(mi, "_api_key_family_viable", lambda _p: True)
    monkeypatch.setattr(mi, "_assemble_worker_mcp_servers", lambda **_k: ())
    assert mi._cross_family_last_resort_worker("t", allow_metered=False) is None
    # Unpinned (key-only install) behaviour is unchanged.
    assert isinstance(mi._cross_family_last_resort_worker("t"), ApiAgentWorker)


# --- Workers honour backend_fallback=False ------------------------------------


@pytest.mark.parametrize("cls", [ClaudeDirectWorker, CodexDirectWorker])
async def test_pinned_worker_disables_its_cross_backend_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cls: type
) -> None:
    seen: dict[str, Any] = {}

    async def _fake_spawn_bound(self: Any, prompt: str, **kwargs: Any) -> AsyncIterator[Any]:
        seen.update(kwargs)
        if False:  # pragma: no cover - makes this an async generator
            yield None

    monkeypatch.setattr(cls, "_spawn_bound", _fake_spawn_bound)
    worker = cls(backend_fallback=False)

    class _Binding:
        def close(self) -> None:
            return None

    async for _ev in worker.spawn(
        "p",
        worktree=tmp_path,
        env={},
        job=None,
        worker_id="w",
        log_dir=tmp_path,
        _broker_binding=_Binding(),
    ):
        pass
    assert seen["allow_backend_fallback"] is False


# --- Orchestrator: parking with a checkpoint ----------------------------------


@pytest.fixture
async def manager(tmp_missions_db: Path):
    m = MissionManager(tmp_missions_db)
    await m.start()
    yield m
    await m.stop()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


async def _events(manager: MissionManager, mid: str) -> list[Any]:
    return [e.payload for e in await manager.store.events_for_mission(mid)]


async def test_spent_quota_parks_mission_with_checkpoint(
    manager: MissionManager, tmp_path: Path
) -> None:
    worker = FakeMissionWorker(quota=True)
    plan = MissionPlan(steps=[Step(slug="task", prompt="do it")], n_workers=1)
    k = make_kontrollierer(manager, tmp_path, plan, lambda _s: worker, ApprovingCritic())
    mid = await manager.dispatch(prompt="write the report")

    state = await k.run_mission(mid)

    assert state == MissionState.WAITING_CAPACITY
    view = await manager.mission(mid)
    assert view is not None and view.state == MissionState.WAITING_CAPACITY
    payloads = await _events(manager, mid)
    assert not any(p.event_type == "MissionFailed" for p in payloads)
    waits = [p for p in payloads if isinstance(p, MissionWaitingCapacity)]
    assert len(waits) == 1
    wait = waits[0]
    assert wait.reason == "provider_quota"
    assert (wait.steps_done, wait.steps_total) == (0, 1)
    checkpoint = Path(wait.checkpoint_path)
    assert checkpoint.name == CHECKPOINT_NAME
    data = _read_json(checkpoint)
    assert data["version"] == capacity.CHECKPOINT_VERSION
    assert data["prompt"] == "write the report"
    assert data["steps"][0]["prompt"] == "do it"
    assert data["steps"][0]["done"] is False


async def test_factory_refusal_parks_without_spawning(
    manager: MissionManager, tmp_path: Path
) -> None:
    def _factory(_step: Step) -> Any:
        raise WorkerCapacityUnavailable("provider_auth", "codex")

    plan = MissionPlan(steps=[Step(slug="task", prompt="do it")], n_workers=1)
    critic = ApprovingCritic()
    k = make_kontrollierer(manager, tmp_path, plan, _factory, critic)
    mid = await manager.dispatch(prompt="p")

    assert await k.run_mission(mid) == MissionState.WAITING_CAPACITY
    assert critic.calls == 0
    wait = next(
        p for p in await _events(manager, mid) if isinstance(p, MissionWaitingCapacity)
    )
    assert (wait.reason, wait.provider) == ("provider_auth", "codex")


async def test_finished_steps_are_kept_and_counted(
    manager: MissionManager, tmp_path: Path
) -> None:
    ok, blocked = Step(slug="first", prompt="one"), Step(slug="second", prompt="two")

    def _factory(step: Step) -> Any:
        if step.task_id == blocked.task_id:
            raise WorkerCapacityUnavailable("provider_quota", "claude")
        return FakeMissionWorker()

    plan = MissionPlan(steps=[ok, blocked], n_workers=1)
    k = make_kontrollierer(manager, tmp_path, plan, _factory, ApprovingCritic())
    mid = await manager.dispatch(prompt="p")

    assert await k.run_mission(mid) == MissionState.WAITING_CAPACITY
    wait = next(
        p for p in await _events(manager, mid) if isinstance(p, MissionWaitingCapacity)
    )
    assert (wait.steps_done, wait.steps_total) == (1, 2)
    data = _read_json(Path(wait.checkpoint_path))
    assert [s["done"] for s in data["steps"]] == [True, False]


async def test_key_only_install_never_parks_on_a_spent_quota(
    manager: MissionManager, tmp_path: Path
) -> None:
    """Finding 4: the final-iteration provider_quota park is a subscription
    install's rule; a key-only install fails (or crosses) as before."""
    from tests.fakes.fake_mission_runtime import make_policy

    worker = FakeMissionWorker(quota=True)
    plan = MissionPlan(steps=[Step(slug="task", prompt="do it")], n_workers=1)
    policy, _pinned, _paid = make_policy(tmp_path, pinned=False)
    k = make_kontrollierer(
        manager, tmp_path, plan, lambda _s: worker, ApprovingCritic(), capacity_policy=policy
    )
    mid = await manager.dispatch(prompt="write the report")

    assert await k.run_mission(mid) == MissionState.FAILED
    assert not any(isinstance(p, MissionWaitingCapacity) for p in await _events(manager, mid))


async def test_parked_mission_can_be_cancelled(
    manager: MissionManager, tmp_path: Path
) -> None:
    def _factory(_step: Step) -> Any:
        raise WorkerCapacityUnavailable("provider_quota", "claude")

    plan = MissionPlan(steps=[Step(slug="task", prompt="x")], n_workers=1)
    k = make_kontrollierer(manager, tmp_path, plan, _factory, ApprovingCritic())
    mid = await manager.dispatch(prompt="p")
    await k.run_mission(mid)

    await manager.transition_state(mid, MissionState.CANCELLED, reason="ui_cancel")
    view = await manager.mission(mid)
    assert view is not None and view.state == MissionState.CANCELLED
    # The cancel route then tells the runner; the checkpoint goes with it.
    mission_dir = tmp_path / "missions" / f"mission_{mid[:13]}"
    assert (mission_dir / CHECKPOINT_NAME).is_file()
    k.cancel_running_mission(mid)
    assert not (mission_dir / CHECKPOINT_NAME).exists()


# --- Restart and cleanup leave a parked mission alone -------------------------


async def test_recovery_never_sweeps_a_parked_mission(
    manager: MissionManager, tmp_path: Path
) -> None:
    def _factory(_step: Step) -> Any:
        raise WorkerCapacityUnavailable("provider_quota", "claude")

    plan = MissionPlan(steps=[Step(slug="task", prompt="x")], n_workers=1)
    k = make_kontrollierer(manager, tmp_path, plan, _factory, ApprovingCritic())
    mid = await manager.dispatch(prompt="p")
    await k.run_mission(mid)

    far_future = 10**15
    swept = await startup_recover(
        manager.store, now=far_future, owner_alive_fn=lambda _pid, _start: False
    )

    assert mid not in swept
    view = await manager.mission(mid)
    assert view is not None and view.state == MissionState.WAITING_CAPACITY


def test_cleanup_keeps_a_checkpointed_mission(tmp_path: Path) -> None:
    from jarvis.missions.cleanup import _holds_deliverables

    entry = tmp_path / "mission_019f0000-0000"
    entry.mkdir()
    assert _holds_deliverables(entry) is False
    capacity.write_checkpoint(entry, {"mission_id": "m"})
    assert _holds_deliverables(entry) is True


# --- Readback -----------------------------------------------------------------


@pytest.mark.parametrize("lang", ["de", "en"])
def test_readback_says_why_progress_and_options(lang: str) -> None:
    text = render_capacity_wait(
        reason="provider_quota",
        provider="claude",
        steps_done=1,
        steps_total=3,
        files_saved=2,
        checkpoint_saved=True,
        language=lang,  # type: ignore[arg-type]
    )
    assert "Claude" in text
    assert "1" in text and "3" in text and "2" in text
    assert "API" in text
    assert len(text) <= MAX_VOICE_CHARS


def test_readback_keeps_the_options_under_the_length_cap() -> None:
    text = render_capacity_wait(
        reason="provider_unavailable",
        provider="a-very-long-provider-slug-" * 4,
        steps_done=123,
        steps_total=456,
        files_saved=789,
        checkpoint_saved=True,
        language="de",
    )
    assert len(text) <= MAX_VOICE_CHARS
    assert text.endswith("Artefakte.")  # i18n-allow: German TTS phrase under test


@pytest.mark.parametrize("lang", ["de", "en"])
@pytest.mark.parametrize("reason", ["paid_daily_cap_reached", "paid_consent_revoked"])
def test_readback_names_the_new_paid_reasons(lang: str, reason: str) -> None:
    from jarvis.missions.voice.readback import CAPACITY_WAIT_PHRASES

    text = render_capacity_wait(
        reason=reason,
        provider="claude-api",
        steps_done=0,
        steps_total=1,
        files_saved=0,
        checkpoint_saved=True,
        language=lang,  # type: ignore[arg-type]
    )
    head = CAPACITY_WAIT_PHRASES[lang][reason].format(provider="Claude")
    assert text.startswith(head)
