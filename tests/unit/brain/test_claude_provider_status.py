"""The provider catalog and Claude connection card agree without model calls."""

from types import SimpleNamespace

import pytest

from jarvis import claude_auth
from jarvis.brain import app_control
from jarvis.ui.web.provider_spec import get_spec


@pytest.mark.parametrize(
    ("connected", "key", "api_key_present"),
    [(True, None, False), (False, None, False),
     (True, "fixture-api-key", True), (False, "sk-ant-oat-fixture", False)],
)
def test_claude_catalog_uses_connection_status(monkeypatch, connected, key, api_key_present):
    calls = []

    class AuthService:
        def __init__(self, binary_path, **kwargs):
            calls.append((binary_path, kwargs))

        def status(self):
            return SimpleNamespace(connected=connected)

    monkeypatch.setattr(claude_auth, "ClaudeAuthService", AuthService)
    monkeypatch.setattr(app_control.cfg_mod, "get_jarvis_agent_secret", lambda provider: key)
    assert app_control.is_credential_present(get_spec("claude-cli"), "fixture-claude") is connected
    assert calls == [("fixture-claude", {"api_key_present": api_key_present})]


def test_claude_catalog_survives_failed_status_without_leaking_secrets(monkeypatch, caplog):
    def unavailable(*args, **kwargs):
        raise OSError("private-credential-detail")

    monkeypatch.setattr(app_control.cfg_mod, "get_jarvis_agent_secret", lambda provider: None)
    monkeypatch.setattr(claude_auth, "ClaudeAuthService", unavailable)
    with caplog.at_level("DEBUG", logger=app_control.__name__):
        assert not app_control.is_credential_present(get_spec("claude-cli"))
    assert "OSError" in caplog.text
    assert "private-credential-detail" not in caplog.text


def test_unreadable_keyring_does_not_hide_native_login(monkeypatch, caplog):
    def unavailable(provider):
        raise OSError("private-keyring-detail")

    class AuthService:
        def __init__(self, binary_path, *, api_key_present):
            assert not api_key_present

        def status(self):
            return SimpleNamespace(connected=True)

    monkeypatch.setattr(app_control.cfg_mod, "get_jarvis_agent_secret", unavailable)
    monkeypatch.setattr(claude_auth, "ClaudeAuthService", AuthService)
    with caplog.at_level("DEBUG", logger=app_control.__name__):
        assert app_control.is_credential_present(get_spec("claude-cli"))
    assert "private-keyring-detail" not in caplog.text
