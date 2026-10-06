"""What Jarvis writes for Hermes and OpenClaw, and how it routes models."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.agent_runtimes import RUNNER_RUNTIMES, driver
from jarvis.agent_runtimes.base import RuntimeTurn, child_env, parse_version, persona_text
from jarvis.agent_runtimes.hermes import HermesRuntime
from jarvis.agent_runtimes.model_map import (
    KEY_ENV_VAR,
    ModelRoute,
    RouteUnavailable,
    route_for,
    supports,
)
from jarvis.agent_runtimes.openclaw import (
    SESSION_KEY,
    OpenClawRuntime,
    _node_supported,
    session_key,
)
from jarvis.core.config import override_provider_secrets

_SECRET = "sk-test-" + "123"  # a fake key for assertions
_TOKEN = "gw-" + "token"


def _route(*, key: str | None = _SECRET, transport: str = "chat_completions") -> ModelRoute:
    return ModelRoute(
        provider="openai",
        model="gpt-5.2",
        base_url="https://api.openai.com/v1",
        transport=transport,  # type: ignore[arg-type]
        api_key=key,
    )


def _turn(tmp_path: Path, **overrides) -> RuntimeTurn:
    values = {
        "agent_id": "hermit",
        "agent_name": "Hermit",
        "session_id": "society:hermit",
        "workspace": tmp_path,
        "route": _route(),
        "resume": None,
        "auto_approve": True,
        "mcp_url": "http://127.0.0.1:47820/api/control/mcp/",
        "control_key": "control-key-xyz",
    }
    values.update(overrides)
    return RuntimeTurn(**values)


def test_runners_map_to_their_runtimes():
    assert RUNNER_RUNTIMES == {"hermes-cli": "hermes", "openclaw-cli": "openclaw"}
    assert isinstance(driver("hermes"), HermesRuntime)
    assert isinstance(driver("openclaw"), OpenClawRuntime)
    with pytest.raises(KeyError):
        driver("skynet")


# ------------------------------------------------------------------ Hermes


def test_hermes_config_reads_the_key_from_the_environment(tmp_path):
    config = HermesRuntime().config_for(_turn(tmp_path))
    text = json.dumps(config)
    assert _SECRET not in text and "control-key-xyz" not in text
    assert config["model"]["provider"] == "custom:jarvis"
    assert config["model"]["default"] == "gpt-5.2"
    provider = config["providers"]["jarvis"]
    assert provider["key_env"] == KEY_ENV_VAR
    assert provider["api"] == "https://api.openai.com/v1"
    assert provider["transport"] == "chat_completions"
    assert config["terminal"]["cwd"] == str(tmp_path)


def test_hermes_self_learning_extras_stay_off(tmp_path):
    config = HermesRuntime().config_for(_turn(tmp_path))
    assert config["memory"]["memory_enabled"] is False
    assert config["memory"]["nudge_interval"] == 0
    assert config["curator"]["enabled"] is False
    assert config["auxiliary"]["background_review"]["enabled"] is False
    assert config["skills"]["creation_nudge_interval"] == 0


def test_hermes_offers_jarvis_tools_directly(tmp_path):
    """Never deferred behind Hermes' tool search (a 9B model missed them live)."""
    assert HermesRuntime().config_for(_turn(tmp_path))["tools"]["tool_search"] is False


def test_hermes_approvals_follow_the_chat_stance_and_never_the_guardian(tmp_path):
    runtime = HermesRuntime()
    assert runtime.config_for(_turn(tmp_path))["approvals"]["mode"] == "off"
    asked = runtime.config_for(_turn(tmp_path, auto_approve=False))
    assert asked["approvals"]["mode"] == "manual"


def test_hermes_denied_shell_disables_its_terminal(tmp_path):
    config = HermesRuntime().config_for(_turn(tmp_path, denied_native=frozenset({"shell"})))
    assert set(config["agent"]["disabled_toolsets"]) == {"terminal", "code_execution"}


def test_a_keyless_local_model_writes_no_key_reference(tmp_path):
    route = ModelRoute("ollama", "qwen3", "http://127.0.0.1:11434/v1", "chat_completions", None)
    config = HermesRuntime().config_for(_turn(tmp_path, route=route))
    assert "key_env" not in config["providers"]["jarvis"]
    assert route.env() == {}


# ---------------------------------------------------------------- OpenClaw


def test_openclaw_config_keeps_both_keys_out_of_the_file(tmp_path):
    config = OpenClawRuntime().config_for(_turn(tmp_path), port=4321, token=_TOKEN)
    text = json.dumps(config)
    assert _SECRET not in text and "control-key-xyz" not in text
    provider = config["models"]["providers"]["jarvis"]
    assert provider["apiKey"] == "${JARVIS_RUNTIME_API_KEY}"
    assert provider["api"] == "openai-completions"
    headers = config["mcp"]["servers"]["jarvis"]["headers"]
    assert headers["Authorization"] == "Bearer ${JARVIS_CONTROL_API_KEY}"
    assert headers["X-Jarvis-Chat-Session"] == "society:hermit"


def test_openclaw_never_runs_background_turns_or_persona_files(tmp_path):
    config = OpenClawRuntime().config_for(_turn(tmp_path), port=4321, token=_TOKEN)
    defaults = config["agents"]["defaults"]
    assert defaults["heartbeat"] == {"every": "0m"}
    assert defaults["skipBootstrap"] is True
    assert defaults["contextInjection"] == "never"
    assert defaults["model"]["primary"] == "jarvis/gpt-5.2"
    assert config["session"]["reset"]["mode"] == "none"
    assert "automations" in config["tools"]["deny"]
    assert config["gateway"]["bind"] == "loopback"


def test_openclaw_exec_mode_follows_stance_and_grants(tmp_path):
    runtime = OpenClawRuntime()
    full = runtime.config_for(_turn(tmp_path), port=1, token=_TOKEN)
    ask = runtime.config_for(_turn(tmp_path, auto_approve=False), port=1, token=_TOKEN)
    denied = runtime.config_for(
        _turn(tmp_path, denied_native=frozenset({"shell", "web"})), port=1, token=_TOKEN
    )
    assert full["tools"]["exec"]["mode"] == "full"
    assert ask["tools"]["exec"]["mode"] == "ask"
    assert denied["tools"]["exec"]["mode"] == "deny"
    assert "browser" in denied["tools"]["deny"]


def test_openclaw_anthropic_route_uses_the_messages_adapter(tmp_path):
    route = _route(transport="anthropic_messages")
    config = OpenClawRuntime().config_for(_turn(tmp_path, route=route), port=1, token=_TOKEN)
    assert config["models"]["providers"]["jarvis"]["api"] == "anthropic-messages"


def test_openclaw_without_tools_writes_no_mcp_server(tmp_path):
    config = OpenClawRuntime().config_for(
        _turn(tmp_path, mcp_url=None, control_key=None), port=1, token=_TOKEN
    )
    assert "mcp" not in config


def test_a_routine_run_never_shares_the_main_conversation():
    assert session_key("hermit", "society:hermit") == SESSION_KEY
    run = session_key("hermit", "society:hermit:routine:task-1:abc")
    assert run.startswith("agent:main:run-") and run != SESSION_KEY
    assert run == session_key("hermit", "society:hermit:routine:task-1:abc")


def test_openclaw_node_engine_range():
    assert _node_supported((24, 21, 0))
    assert _node_supported((26, 1, 0))
    assert not _node_supported((24, 13, 0))
    assert not _node_supported((25, 0, 0))
    assert not _node_supported(None)


# ------------------------------------------------------------------ shared


def test_child_env_drops_other_providers_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leak")
    monkeypatch.setenv("GITHUB_TOKEN", "leak")
    env = child_env({"EXTRA": "1"})
    assert "ANTHROPIC_API_KEY" not in env and "GITHUB_TOKEN" not in env
    assert env["EXTRA"] == "1" and env["PYTHONIOENCODING"] == "utf-8"


def test_version_parsing_reads_both_numbering_schemes():
    assert parse_version("Hermes Agent v0.20.6 (2026.8.27)") == (0, 20, 6)
    assert parse_version("OpenClaw 2026.9.8 (fc23bc8)") == (2026, 9, 8)
    assert parse_version("garbage") is None


def test_the_persona_defers_to_the_jarvis_identity():
    text = persona_text("Hermit")
    assert "Hermit" in text and "<jarvis_identity>" in text


# --------------------------------------------------------------- model map


def _cfg(**providers) -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(
            providers={k: SimpleNamespace(**v) for k, v in providers.items()}
        )
    )


def test_subscription_seats_are_not_offered():
    assert supports("openai") and supports("claude-api") and supports("ollama")
    assert not supports("openai-codex") and not supports("grok-build")
    with pytest.raises(RouteUnavailable):
        route_for(_cfg(), "openai-codex", "gpt-5.5")


def test_an_api_key_provider_routes_with_its_saved_key():
    with override_provider_secrets({"openai": _SECRET}):
        route = route_for(_cfg(), "openai", "gpt-5.2")
    assert route.base_url == "https://api.openai.com/v1"
    assert route.api_key == _SECRET and route.env() == {KEY_ENV_VAR: _SECRET}


def test_a_missing_key_is_reported_in_plain_words():
    with override_provider_secrets({"openrouter": None}), pytest.raises(RouteUnavailable) as err:
        route_for(_cfg(), "openrouter", "some/model")
    assert "API key" in str(err.value)


def test_claude_routes_over_the_messages_api():
    with override_provider_secrets({"claude-api": _SECRET}):
        route = route_for(_cfg(), "claude-api", "claude-sonnet-5")
    assert route.transport == "anthropic_messages"
    assert route.base_url == "https://api.anthropic.com"


def test_hermes_restore_finds_the_key_under_its_host_name():
    from jarvis.agent_runtimes.hermes import _restore_key_aliases

    assert _restore_key_aliases("https://api.openai.com/v1", "k") == {"OPENAI_API_KEY": "k"}
    assert _restore_key_aliases("https://openrouter.ai/api/v1", "k") == {"OPENROUTER_API_KEY": "k"}
    assert _restore_key_aliases("https://api.x.ai/v1", "k") == {"X_API_KEY": "k"}
    assert _restore_key_aliases("https://api.anthropic.com", "k") == {"ANTHROPIC_API_KEY": "k"}
    assert _restore_key_aliases("https://integrate.api.nvidia.com/v1", "k") == {
        "NVIDIA_API_KEY": "k"
    }
    assert _restore_key_aliases("http://127.0.0.1:11434/v1", "k") == {}
    assert _restore_key_aliases("https://api.openai.com/v1", None) == {}


def test_hermes_model_block_names_the_endpoint_for_session_restore(tmp_path):
    keyed = HermesRuntime().config_for(_turn(tmp_path))["model"]
    assert keyed["base_url"] == "https://api.openai.com/v1" and "api_key" not in keyed
    route = ModelRoute("ollama", "qwen3", "http://127.0.0.1:11434/v1", "chat_completions", None)
    local = HermesRuntime().config_for(_turn(tmp_path, route=route))["model"]
    assert local["api_key"] == "no-key-required"
