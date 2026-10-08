"""Claude subscription runs retain OpenClaw tools, policy, and conversations."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.agent_runtimes.base import RuntimeTurn
from jarvis.agent_runtimes.model_map import KEY_ENV_VAR, ModelRoute
from jarvis.agent_runtimes.openclaw import OpenClawRuntime, session_key

_GATEWAY_TOKEN = "test-" + "gateway"


def _turn(tmp_path: Path, **overrides) -> RuntimeTurn:
    values = {
        "agent_id": "subscription-agent",
        "agent_name": "Subscription agent",
        "session_id": "society:subscription-agent",
        "workspace": tmp_path / "workspace",
        "route": ModelRoute(
            provider="claude-api",
            model="claude-sonnet-4-6",
            base_url="",
            transport="claude_cli",
            api_key=None,
            claude_binary=str(tmp_path / "cli" / "claude"),
            claude_config_dir=str(tmp_path / "claude-account"),
        ),
        "resume": None,
        "auto_approve": True,
        "mcp_url": "http://127.0.0.1:47820/api/control/mcp/",
        "control_key": "test-control-token",
    }
    values.update(overrides)
    return RuntimeTurn(**values)


def test_subscription_uses_native_runtime_without_http_or_fallback(tmp_path):
    config = OpenClawRuntime().config_for(_turn(tmp_path), port=4321, token=_GATEWAY_TOKEN)
    defaults = config["agents"]["defaults"]
    model = "anthropic/claude-sonnet-4-6"
    assert defaults["model"] == {"primary": model}
    assert defaults["models"] == {model: {"agentRuntime": {"id": "claude-cli"}}}
    assert "models" not in config
    serialized = json.dumps(config)
    assert KEY_ENV_VAR not in serialized
    assert "apiKey" not in serialized
    assert "baseUrl" not in serialized
    assert "test-control-token" not in serialized


@pytest.mark.parametrize("auto_approve", [False, True])
def test_subscription_keeps_jarvis_mcp_and_host_tool_approval(tmp_path, auto_approve):
    turn = _turn(tmp_path, auto_approve=auto_approve)
    config = OpenClawRuntime().config_for(turn, port=4321, token=_GATEWAY_TOKEN)
    assert config["mcp"]["servers"]["jarvis"]["url"] == turn.mcp_url
    headers = config["mcp"]["servers"]["jarvis"]["headers"]
    assert headers["X-Jarvis-Chat-Session"] == turn.session_id
    assert headers["Authorization"] == "Bearer ${JARVIS_CONTROL_API_KEY}"
    assert config["tools"]["exec"] == {"mode": "full" if auto_approve else "ask"}
    assert {"browser", "message", "automations"} <= set(config["tools"]["deny"])
    assert "anthropic" not in config["plugins"]["deny"]
    assert config["tools"]["toolSearch"] is False


def test_subscription_respects_denied_capabilities_and_disables_background_work(tmp_path):
    turn = _turn(tmp_path, denied_native=frozenset({"shell", "web"}))
    config = OpenClawRuntime().config_for(turn, port=4321, token=_GATEWAY_TOKEN)
    assert config["tools"]["exec"] == {"mode": "full"}
    assert {"exec", "process", "browser", "web_search", "web_fetch"} <= set(
        config["tools"]["deny"]
    )
    assert config["agents"]["defaults"]["heartbeat"] == {"every": "0m"}
    assert config["cron"]["enabled"] is False
    assert config["plugins"]["slots"]["memory"] == "none"
    assert config["plugins"]["entries"]["memory-core"]["enabled"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [None, "previous-native-session"])
async def test_native_account_and_binary_reach_gateway_while_acp_keeps_session(
    tmp_path, monkeypatch, resume
):
    from jarvis.agent_runtimes import openclaw

    monkeypatch.setenv("ANTHROPIC_API_KEY", "unselected-api-credential")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "unselected-auth-token")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "unselected-oauth-token")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "wrong-account"))
    home = tmp_path / "openclaw-home"
    monkeypatch.setattr(openclaw, "agent_home", lambda *args: home)
    monkeypatch.setattr(openclaw, "_gateway_token", lambda path: "test-gateway")
    captured = {}
    gateway = SimpleNamespace(port=4321, last_used=0.0, in_use=1)
    runtime = OpenClawRuntime()

    async def ensure_gateway(turn, key, home, token, launcher, env):
        captured.update(env)
        return gateway

    monkeypatch.setattr(runtime, "_ensure_gateway", ensure_gateway)
    released = []
    turn = _turn(tmp_path, resume=resume)
    launch = await runtime._launch(
        turn, turn.agent_id, ["openclaw"], lambda: released.append(True)
    )
    assert captured["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude-account")
    assert captured["PATH"].split(os.pathsep)[0] == str(tmp_path / "cli")
    assert captured["JARVIS_CONTROL_API_KEY"] == turn.control_key
    assert not {
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", KEY_ENV_VAR
    } & captured.keys()
    assert launch.argv[:2] == ["openclaw", "acp"]
    assert ("--reset-session" in launch.argv) is (resume is None)
    assert launch.vendor_session == session_key(turn.agent_id, turn.session_id)
    launch.release()
    assert gateway.in_use == 0 and released == [True]


def test_explicit_api_route_still_uses_jarvis_gateway(tmp_path):
    route = ModelRoute(
        provider="claude-api", model="claude-sonnet-4-6",
        base_url="http://127.0.0.1:47820/api/runtime-gateway/v1",
        transport="chat_completions", api_key="test-runtime-token",
    )
    config = OpenClawRuntime().config_for(
        _turn(tmp_path, route=route), port=4321, token=_GATEWAY_TOKEN
    )
    assert config["models"]["providers"]["jarvis"]["baseUrl"] == route.base_url
    assert config["models"]["providers"]["jarvis"]["apiKey"] == "${JARVIS_RUNTIME_API_KEY}"
    assert config["agents"]["defaults"]["model"]["primary"] == "jarvis/claude-sonnet-4-6"
