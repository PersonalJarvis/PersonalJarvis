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
    monkeypatch.setattr(
        model_map, "claude_subscription_status",
        lambda account="": SimpleNamespace(binary_path="claude", config_dir="test-account"),
    )


def _login(monkeypatch, *, live: bool) -> list[tuple[str, str]]:
    asked: list[tuple[str, str]] = []

    def uses_native_claude(provider: str, account_id: str = "") -> bool:
        asked.append((provider, account_id))
        return live

    monkeypatch.setattr(model_map, "uses_native_claude", uses_native_claude)
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


def test_native_review_pins_the_account_environment_through_plugin_construction(
    monkeypatch, tmp_path, claude_code_installed
):
    from jarvis.brain import resolver
    from jarvis.plugins.brain.claude_cli import ClaudeCliBrain

    _login(monkeypatch, live=True)
    asked = []
    selected_dir = str(tmp_path / "selected-account")
    selected_binary = str(tmp_path / "selected-cli")

    def selected_status(account):
        asked.append(account)
        return SimpleNamespace(binary_path=selected_binary, config_dir=selected_dir)

    monkeypatch.setattr(model_map, "claude_subscription_status", selected_status)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unselected-api-credential")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "unselected-auth-credential")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "unselected-oauth-credential")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "wrong-account"))
    seat = seat_brain.agent_seat(None, _agent(account_id="claude-second"))
    assert asked == ["claude-second"]
    assert seat.account_id == "claude-second"
    assert seat.cli_binary == selected_binary
    assert dict(seat.spawn_env)["CLAUDE_CONFIG_DIR"] == selected_dir
    assert not {
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"
    } & dict(seat.spawn_env).keys()

    class Registry:
        def available(self):
            return ["claude-cli"]

        def get_class(self, name):
            assert name == "claude-cli"
            return ClaudeCliBrain

        def instantiate(self, name, **kwargs):
            assert name == "claude-cli"
            return ClaudeCliBrain(**kwargs)

    monkeypatch.setattr(resolver, "_get_registry", Registry)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "new-active-account"))
    brain = seat_brain._build(None, seat, "test-review")
    assert brain._cli_binary == selected_binary
    assert dict(brain._spawn_env)["CLAUDE_CONFIG_DIR"] == selected_dir
    assert brain._structured_prompts is True
    assert brain._model == "claude-opus-5"


@pytest.mark.parametrize("missing_account", [False, True])
def test_unavailable_selected_native_review_never_falls_back(
    monkeypatch, claude_code_installed, missing_account
):
    _login(monkeypatch, live=True)

    def unavailable(account):
        if missing_account:
            raise model_map.RouteUnavailable("The selected account no longer exists")
        return None

    monkeypatch.setattr(model_map, "claude_subscription_status", unavailable)
    with pytest.raises(seat_brain.SeatUnavailable):
        seat_brain.agent_seat(None, _agent(account_id="claude-second"))
