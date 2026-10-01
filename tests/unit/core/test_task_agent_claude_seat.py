"""The Claude Agents slot answers on the subscription when one is signed in.

Live 2026-09-29: Agents showed Claude Max as active, yet the front-page chat
ran ``claude-api`` through the brain runner and replied with a stale
Anthropic key's 401. Missions already run that slot on the subscription.
"""

from __future__ import annotations

import pytest

from jarvis.core import task_agent


@pytest.fixture(autouse=True)
def _fresh_login_cache(monkeypatch):
    monkeypatch.setattr(task_agent, "_claude_login", None)


def test_claude_slot_uses_the_cli_when_the_subscription_is_signed_in(monkeypatch) -> None:
    monkeypatch.setattr(task_agent, "_claude_subscription_ready", lambda: True)

    assert task_agent.subscription_seat("claude-api") == ("claude-api", "claude-cli")


def test_claude_slot_stays_on_the_api_row_without_a_subscription(monkeypatch) -> None:
    monkeypatch.setattr(task_agent, "_claude_subscription_ready", lambda: False)

    assert task_agent.subscription_seat("claude-api") is None


def test_other_seats_never_probe_the_claude_login(monkeypatch) -> None:
    def _unexpected() -> bool:
        raise AssertionError("only the Claude slot may probe the Claude login")

    monkeypatch.setattr(task_agent, "_claude_subscription_ready", _unexpected)

    assert task_agent.subscription_seat("codex") == ("openai-codex", "codex-cli")
    assert task_agent.subscription_seat("openai") is None


def test_login_probe_is_cached(monkeypatch) -> None:
    calls = []

    class _Status:
        connected = True
        mode = "subscription"

    class _Service:
        def status(self):
            calls.append(1)
            return _Status()

    import jarvis.claude_auth as claude_auth

    monkeypatch.setattr(claude_auth, "ClaudeAuthService", _Service)

    assert task_agent._claude_subscription_ready() is True
    assert task_agent._claude_subscription_ready() is True
    assert len(calls) == 1


def test_unreadable_login_means_no_subscription(monkeypatch) -> None:
    class _Broken:
        def status(self):
            raise OSError("claude binary vanished")

    import jarvis.claude_auth as claude_auth

    monkeypatch.setattr(claude_auth, "ClaudeAuthService", _Broken)

    assert task_agent._claude_subscription_ready() is False


async def test_claude_seat_probe_runs_off_the_event_loop(monkeypatch) -> None:
    """#251: the cold login probe runs up to two blocking ``claude``
    subprocesses; on the loop it froze voice, WebSockets and the IDE."""
    import threading

    loop_thread = threading.get_ident()
    probe_threads: list[int] = []

    def _probe() -> bool:
        probe_threads.append(threading.get_ident())
        return True

    monkeypatch.setattr(task_agent, "_claude_subscription_ready", _probe)

    seat = await task_agent.subscription_seat_off_loop("claude-api")

    assert seat == ("claude-api", "claude-cli")
    assert probe_threads and probe_threads[0] != loop_thread


async def test_other_seats_resolve_inline_without_a_thread(monkeypatch) -> None:
    async def _no_thread(*_a, **_k):
        raise AssertionError("a dict lookup needs no worker thread")

    monkeypatch.setattr(task_agent.asyncio, "to_thread", _no_thread)

    assert await task_agent.subscription_seat_off_loop("codex") == ("openai-codex", "codex-cli")
