"""Native runtime subscriptions use the selected CLI account, never its bearer."""

from types import SimpleNamespace

import pytest

from jarvis import agent_accounts
from jarvis.agent_runtimes import model_map
from jarvis.claude_auth import ClaudeAuthService, ClaudeCliAuthSnapshot


@pytest.mark.parametrize("account_id", ["", "subscription", "claude-work"])
def test_selected_native_account_is_probed_in_the_same_sanitized_environment(
    monkeypatch, account_id,
):
    account = SimpleNamespace(id="claude-work", platform="claude")
    asked = []
    monkeypatch.setattr(agent_accounts, "active_account", lambda platform: account)
    monkeypatch.setattr(agent_accounts, "resolve", lambda requested: account)

    def spawn(platform, selected, *, base):
        assert (platform, selected) == ("claude", "claude-work")
        return {**base, "CLAUDE_CONFIG_DIR": "/native/work-account"}

    def probe(self, binary, *, env):
        asked.append(env)
        return ClaudeCliAuthSnapshot(logged_in=True, auth_method="claude.ai")

    monkeypatch.setattr(agent_accounts, "spawn_env", spawn)
    monkeypatch.setattr(ClaudeAuthService, "_resolve_binary", lambda self: "/bin/claude")
    monkeypatch.setattr(ClaudeAuthService, "_probe_cli_auth", probe)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unselected-value")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "unselected-value")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/native/different-account")
    status = model_map.claude_subscription_status(account_id)
    assert status.config_dir == "/native/work-account"
    assert asked[0]["CLAUDE_CONFIG_DIR"] == status.config_dir
    assert "ANTHROPIC_API_KEY" not in asked[0]
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in asked[0]


def test_a_deleted_or_wrong_platform_account_never_borrows_another_login(monkeypatch):
    for selected in (None, SimpleNamespace(platform="codex")):
        monkeypatch.setattr(
            agent_accounts, "resolve", lambda requested, selected=selected: selected,
        )
        with pytest.raises(model_map.RouteUnavailable, match="no longer exists"):
            model_map.claude_subscription_status("selected-account")


def test_a_named_claude_account_never_defaults_to_a_saved_api_key(monkeypatch):
    monkeypatch.setattr(model_map, "_saved_key", lambda *args: "saved-value")
    assert model_map.uses_native_claude("claude-api", "claude-work")
    assert not model_map.uses_native_claude("claude-api", "api-key")
    assert not model_map.uses_native_claude("claude-api", "")


@pytest.mark.parametrize("method", ["api_key", "unknown", None])
def test_native_auth_never_treats_a_key_or_unknown_mode_as_subscription(monkeypatch, method):
    account = SimpleNamespace(id="builtin", platform="claude")
    monkeypatch.setattr(agent_accounts, "active_account", lambda platform: account)
    monkeypatch.setattr(agent_accounts, "spawn_env", lambda *args, base: base)
    monkeypatch.setattr(ClaudeAuthService, "_resolve_binary", lambda self: "/bin/claude")
    monkeypatch.setattr(
        ClaudeAuthService, "_probe_cli_auth",
        lambda *args, **kwargs: ClaudeCliAuthSnapshot(logged_in=True, auth_method=method),
    )
    assert model_map.claude_subscription_status() is None


def test_explicit_auth_environment_repairs_missing_posix_user_without_mutating_it(monkeypatch):
    from jarvis import claude_auth

    monkeypatch.setattr(claude_auth, "os", SimpleNamespace(name="posix", environ={}))
    monkeypatch.setattr(claude_auth.getpass, "getuser", lambda: "native-user")
    source = {"CLAUDE_CONFIG_DIR": "/native/selected"}
    repaired = claude_auth._cli_credential_env(source)
    assert repaired == {"CLAUDE_CONFIG_DIR": "/native/selected", "USER": "native-user"}
    assert source == {"CLAUDE_CONFIG_DIR": "/native/selected"}


def test_native_route_passes_the_repaired_posix_user_to_the_runtime(monkeypatch):
    from jarvis import claude_auth

    monkeypatch.delenv("USER", raising=False)
    monkeypatch.setattr(claude_auth, "os", SimpleNamespace(name="posix", environ={}))
    monkeypatch.setattr(claude_auth.getpass, "getuser", lambda: "native-user")
    route = model_map.ModelRoute(
        "claude-api", "sonnet", "", "claude_cli", None,
        claude_binary="claude", claude_config_dir="/native/selected",
    )
    assert route.env() == {"CLAUDE_CONFIG_DIR": "/native/selected", "USER": "native-user"}
