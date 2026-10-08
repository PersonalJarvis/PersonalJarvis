"""A Hermes / OpenClaw agent's memory reviews run on the auth its chat uses."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.agent_chat import service
from jarvis.agent_runtimes import model_map
from jarvis.society import seat_brain


def _agent(runtime: str = "hermes", account_id: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        provider="claude-api",
        model="claude-opus-5",
        effort="",
        account_id=account_id,
        runtime=runtime,
    )


@pytest.fixture
def claude_code_installed(monkeypatch):
    monkeypatch.setattr(service, "_claude_cli_installed", lambda: True)


def _login(monkeypatch, *, live: bool) -> list[tuple[str, str]]:
    asked: list[tuple[str, str]] = []

    def login_token_for(provider: str, account_id: str = "") -> str | None:
        asked.append((provider, account_id))
        return "sk-ant-oat-test" if live else None

    monkeypatch.setattr(model_map, "login_token_for", login_token_for)
    return asked


def test_an_agent_whose_chat_bills_the_key_reviews_on_the_key(monkeypatch, claude_code_installed):
    _login(monkeypatch, live=False)
    seat = seat_brain.agent_seat(None, _agent())
    # Before: Claude Code answered (the plan) while the chat billed the API key.
    assert seat.keyed
    assert seat.provider == "claude-api" and seat.model == "claude-opus-5"


def test_an_agent_on_the_claude_login_reviews_through_claude_code(
    monkeypatch, claude_code_installed
):
    asked = _login(monkeypatch, live=True)
    seat = seat_brain.agent_seat(None, _agent(account_id="subscription"))
    assert seat.runner == "claude-cli"
    assert asked == [("claude-api", "subscription")]


def test_a_login_seat_without_claude_code_is_unavailable_never_a_key(monkeypatch):
    _login(monkeypatch, live=True)
    monkeypatch.setattr(service, "_claude_cli_installed", lambda: False)
    with pytest.raises(seat_brain.SeatUnavailable):
        seat_brain.agent_seat(None, _agent())


def test_a_jarvis_agent_keeps_its_runner(monkeypatch, claude_code_installed):
    asked = _login(monkeypatch, live=False)
    seat = seat_brain.agent_seat(None, _agent(runtime="jarvis"))
    assert seat.runner == "claude-cli"
    assert asked == []
