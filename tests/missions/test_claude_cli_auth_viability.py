"""Claude CLI worker viability across native and legacy credential stores."""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from jarvis import claude_auth_state
from jarvis.claude_auth import ClaudeAuthService, ClaudeAuthStatus
from jarvis.missions import init as missions_init


@pytest.fixture(autouse=True)
def _reset_auth_dead() -> Iterator[None]:
    claude_auth_state.clear_claude_auth_dead()
    yield
    claude_auth_state.clear_claude_auth_dead()


def _patch_no_file_or_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jarvis.missions.isolation.env.live_claude_oauth_status",
        lambda: "absent",
    )
    monkeypatch.setattr(
        "jarvis.missions.isolation.env.read_live_claude_oauth_token",
        lambda: None,
    )
    monkeypatch.setattr(
        "jarvis.core.config.get_jarvis_agent_secret",
        lambda _provider: None,
    )


def test_native_subscription_is_viable_with_safe_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_no_file_or_key(monkeypatch)
    monkeypatch.setattr(
        ClaudeAuthService,
        "status",
        lambda self: ClaudeAuthStatus(
            installed=True,
            connected=True,
            mode="subscription",
            binary_path="/opt/claude",
            user_email="user@example.com",
            subscription_type="max",
        ),
    )
    monkeypatch.setattr(
        "jarvis.claude_auth.claude_cli_supports_safe_mode",
        lambda _prefix: True,
    )

    assert missions_init._claude_cli_auth_viable() is True


def test_native_subscription_without_safe_mode_uses_cross_family_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_no_file_or_key(monkeypatch)
    monkeypatch.setattr(
        ClaudeAuthService,
        "status",
        lambda self: ClaudeAuthStatus(
            installed=True,
            connected=True,
            mode="subscription",
            binary_path="/opt/claude",
        ),
    )
    monkeypatch.setattr(
        "jarvis.claude_auth.claude_cli_supports_safe_mode",
        lambda _prefix: False,
    )

    assert missions_init._claude_cli_auth_viable() is False


def test_legacy_file_token_remains_viable_without_cli_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jarvis.missions.isolation.env.live_claude_oauth_status",
        lambda: "valid",
    )
    monkeypatch.setattr(
        "jarvis.missions.isolation.env.read_live_claude_oauth_token",
        lambda: "sk-ant-oat01-test-token",
    )

    assert missions_init._claude_cli_auth_viable() is True
