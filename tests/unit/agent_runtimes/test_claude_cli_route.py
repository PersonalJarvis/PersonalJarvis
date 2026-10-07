"""A Claude subscription on OpenClaw runs through the person's own Claude Code."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.agent_runtimes import model_map
from jarvis.agent_runtimes.base import RuntimeTurn
from jarvis.agent_runtimes.model_map import KEY_ENV_VAR, ModelRoute
from jarvis.agent_runtimes.openclaw import OpenClawRuntime

CLAUDE_BIN = str(Path("C:/tools/claude.cmd"))


@pytest.fixture
def claude_login(monkeypatch: pytest.MonkeyPatch) -> None:
    """A live Claude Code login and an installed ``claude``; no API key saved."""
    import jarvis.core.binary_lookup as binary_lookup
    import jarvis.core.path_augment as path_augment
    from jarvis import agent_accounts

    monkeypatch.setattr(model_map, "claude_login_token", lambda: "login-bearer")
    monkeypatch.setattr(model_map, "_saved_key", lambda provider, endpoint: None)
    monkeypatch.setattr(
        binary_lookup, "which", lambda name: CLAUDE_BIN if name == "claude" else None
    )
    monkeypatch.setattr(path_augment, "ensure_cli_paths", lambda: [])
    monkeypatch.setattr(
        agent_accounts, "active_account", lambda platform: SimpleNamespace(id="claude:default")
    )
    monkeypatch.setattr(
        agent_accounts,
        "env_overrides",
        lambda platform, account_id: {"CLAUDE_CONFIG_DIR": "C:/accounts/claude-default"},
    )


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(brain=SimpleNamespace(providers={}))


def test_openclaw_runs_the_subscription_on_claude_code(claude_login: None) -> None:
    route = model_map.route_for(
        _cfg(),
        "claude-api",
        "claude-opus-5-5",
        agent_id="ada",
        account_id="subscription",
        runtime="openclaw",
    )
    assert route.transport == "claude_cli" and route.api_key is None
    env = route.env()
    assert env["CLAUDE_CONFIG_DIR"] == "C:/accounts/claude-default"
    assert env["PATH"].startswith(str(Path(CLAUDE_BIN).parent))
    assert KEY_ENV_VAR not in env
    assert model_map.cli_subscriptions() == {"openclaw": ["claude-api"]}


def test_hermes_and_an_api_key_never_take_the_claude_code_route(
    claude_login: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for runtime, account in (("hermes", "subscription"), ("openclaw", "api-key")):
        with pytest.raises(model_map.RouteUnavailable):
            # Hermes: the login is refused as Extra Usage or has no key; the
            # API-key pin has no key saved. Neither becomes a Claude Code turn.
            monkeypatch.setattr(
                model_map,
                "_check_login_billing",
                lambda *a: (_ for _ in ()).throw(model_map.RouteUnavailable("extra usage off")),
            )
            route = model_map.route_for(
                _cfg(), "claude-api", "claude-opus-5-5", account_id=account, runtime=runtime
            )
            assert route.transport != "claude_cli"


def test_no_claude_code_means_no_route(claude_login: None, monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.core.binary_lookup as binary_lookup

    monkeypatch.setattr(binary_lookup, "which", lambda name: None)
    with pytest.raises(model_map.RouteUnavailable, match="Claude Code is not installed"):
        model_map.route_for(_cfg(), "claude-api", "claude-opus-5-5", runtime="openclaw")


def test_the_openclaw_config_selects_its_claude_cli_backend(tmp_path: Path) -> None:
    route = ModelRoute(
        provider="claude-api",
        model="claude-opus-5-5",
        base_url="",
        transport="claude_cli",
        api_key=None,
    )
    turn = RuntimeTurn(
        agent_id="ada",
        agent_name="Ada",
        session_id="society:ada",
        workspace=tmp_path,
        route=route,
        resume=None,
        auto_approve=False,
        mcp_url="http://127.0.0.1:47820/api/control/mcp/",
        control_key="control-key",
    )
    config = OpenClawRuntime().config_for(turn, port=4321, token="gw-token")  # noqa: S106 — fixture
    assert "models" not in config  # no Jarvis provider: Claude Code answers itself
    defaults = config["agents"]["defaults"]
    assert defaults["model"] == {"primary": "anthropic/claude-opus-5-5"}
    assert defaults["models"] == {
        "anthropic/claude-opus-5-5": {"agentRuntime": {"id": "claude-cli"}}
    }
    assert "jarvis" in config["mcp"]["servers"]  # Jarvis' tools still arrive
