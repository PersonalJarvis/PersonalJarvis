"""An unattended scheduled turn never bills a per-token key on its own.

User rule (2026-10-06): once a subscription is connected, a run nobody started
(a schedule, a trigger, a webhook) runs only on a subscription or a local
model; with none usable it is deferred, never moved onto an API key. A run the
person started ("Run now") and an install without any subscription keep the
existing chain. Every login is a fake probe; no provider is called.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain import background_policy
from jarvis.brain import manager as manager_module
from jarvis.brain.background_policy import BackgroundDeferred
from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.model_selection import ModelSelection, use_operation_model
from jarvis.core.protocols import CapacityDeferred, ToolResult, started_by_user


class _NullExecutor:
    async def execute(self, *a: Any, **kw: Any) -> ToolResult:
        return ToolResult(success=True, output="ok")


class _Turns:
    """Records which provider each attempt was built for; never calls one."""

    def __init__(self, mgr: BrainManager, monkeypatch: pytest.MonkeyPatch) -> None:
        self.built: list[str] = []
        self.fail: dict[str, str] = {}

        def _get_brain(name: str, _model: Any = None) -> Any:
            self.built.append(name)
            return SimpleNamespace(name=name)

        def _build_dispatcher(brain: Any, **_: Any) -> Any:
            fail = self.fail.get(brain.name)

            class _Dispatcher:
                async def dispatch(self, _text: str, **_kw: Any) -> Any:
                    if fail:
                        raise RuntimeError(fail)
                    return SimpleNamespace(text=f"answered by {brain.name}")

            return _Dispatcher()

        monkeypatch.setattr(mgr, "_get_brain", _get_brain)
        monkeypatch.setattr(mgr, "_build_dispatcher", _build_dispatcher)


def _manager() -> BrainManager:
    return BrainManager(
        config=JarvisConfig(),
        bus=EventBus(),
        tools={},
        tool_executor=_NullExecutor(),  # type: ignore[arg-type]
    )


def _chain(mgr: BrainManager, monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    monkeypatch.setattr(mgr, "_task_provider_chain", lambda _intent: [(n, None) for n in names])


def _logins(monkeypatch: pytest.MonkeyPatch, **states: bool | None) -> None:
    """Fake subscription logins: ``codex=True`` = signed in, False = signed out."""
    monkeypatch.setattr(background_policy, "_probe_override", lambda provider: states.get(provider))


async def _no_seat(_provider: str) -> None:
    return None


@pytest.fixture
def mgr(monkeypatch: pytest.MonkeyPatch) -> BrainManager:
    m = _manager()
    # No subscription CLI seat answers off the loop; the chain decides.
    monkeypatch.setattr("jarvis.core.task_agent.subscription_seat_off_loop", _no_seat)
    return m


# --- No subscription connected: the single-key install keeps working ----------


async def test_without_a_subscription_the_unattended_chain_is_unchanged(
    mgr: BrainManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    turns = _Turns(mgr, monkeypatch)
    _chain(mgr, monkeypatch, "openai", "gemini")

    assert await mgr.run_task(prompt="daily digest") == "answered by openai"
    assert turns.built == ["openai"]


# --- Subscription connected: unattended turns stay on subscriptions -----------


async def test_unattended_turn_skips_the_key_and_runs_on_the_subscription(
    mgr: BrainManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    turns = _Turns(mgr, monkeypatch)
    _logins(monkeypatch, codex=True)
    _chain(mgr, monkeypatch, "openai", "codex")

    assert await mgr.run_task(prompt="daily digest") == "answered by codex"
    assert turns.built == ["codex"]


async def test_unattended_turn_runs_on_a_local_model(
    mgr: BrainManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    turns = _Turns(mgr, monkeypatch)
    _logins(monkeypatch, codex=False)
    background_policy.note_connected("codex")  # a subscription was connected earlier
    _chain(mgr, monkeypatch, "openai", "ollama")

    assert await mgr.run_task(prompt="daily digest") == "answered by ollama"
    assert turns.built == ["ollama"]


@pytest.mark.parametrize("codex_login", [False, None])
async def test_unattended_turn_defers_when_only_keys_remain(
    mgr: BrainManager, monkeypatch: pytest.MonkeyPatch, codex_login: bool | None
) -> None:
    """A spent or signed-out subscription: no key is billed, the run is deferred."""
    turns = _Turns(mgr, monkeypatch)
    _logins(monkeypatch, codex=codex_login)
    background_policy.note_connected("codex")
    _chain(mgr, monkeypatch, "openai", "gemini", "codex")

    with pytest.raises(BackgroundDeferred) as exc:
        await mgr.run_task(prompt="daily digest")

    assert isinstance(exc.value, CapacityDeferred)
    assert "approval" in str(exc.value)
    assert turns.built == []


async def test_a_spent_subscription_does_not_fall_through_to_a_key(
    mgr: BrainManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    turns = _Turns(mgr, monkeypatch)
    turns.fail["codex"] = "429 rate limit: usage window exhausted"
    _logins(monkeypatch, codex=True)
    _chain(mgr, monkeypatch, "codex", "openai", "gemini")

    with pytest.raises(RuntimeError):
        await mgr.run_task(prompt="daily digest")

    assert turns.built == ["codex"]


# --- A run the person started keeps the existing chain ------------------------


async def test_run_now_by_the_person_keeps_the_existing_chain(
    mgr: BrainManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    turns = _Turns(mgr, monkeypatch)
    _logins(monkeypatch, codex=False)
    background_policy.note_connected("codex")
    _chain(mgr, monkeypatch, "openai", "gemini")

    with started_by_user():
        assert await mgr.run_task(prompt="daily digest") == "answered by openai"
    assert turns.built == ["openai"]


# --- An explicit agent selection ---------------------------------------------


async def test_claude_slot_never_bills_its_api_row_unattended_when_the_cli_is_installed(
    mgr: BrainManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Claude slot falls back to its per-token row when the CLI login is not
    ready; with the CLI installed that row is never billed on its own."""
    turns = _Turns(mgr, monkeypatch)
    monkeypatch.setattr(manager_module, "_claude_cli_installed", lambda: True)

    with use_operation_model(ModelSelection("claude-api", "claude-sonnet", "")):
        with pytest.raises(BackgroundDeferred):
            await mgr.run_task(prompt="daily digest")
        assert turns.built == []

        with started_by_user():
            assert await mgr.run_task(prompt="daily digest") == "answered by claude-api"
    assert turns.built == ["claude-api"]


async def test_claude_slot_without_the_cli_is_the_users_api_choice(
    mgr: BrainManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    turns = _Turns(mgr, monkeypatch)
    monkeypatch.setattr(manager_module, "_claude_cli_installed", lambda: False)

    with use_operation_model(ModelSelection("claude-api", "claude-sonnet", "")):
        assert await mgr.run_task(prompt="daily digest") == "answered by claude-api"
    assert turns.built == ["claude-api"]


async def test_an_explicitly_selected_agent_is_kept(
    mgr: BrainManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The person picked this agent for tasks; the policy filters only the
    automatic chain, it never overrides an explicit choice."""
    turns = _Turns(mgr, monkeypatch)
    _logins(monkeypatch, codex=True)

    with use_operation_model(ModelSelection("openai", "gpt-x", "")):
        assert await mgr.run_task(prompt="daily digest") == "answered by openai"
    assert turns.built == ["openai"]
