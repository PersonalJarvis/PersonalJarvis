"""The picker must not offer a login that runtime startup already rejects."""

from types import SimpleNamespace

import pytest

from jarvis.agent_runtimes import model_map, provider_errors
from jarvis.core import config


@pytest.fixture
def claude_only(monkeypatch):
    monkeypatch.setattr(model_map, "_ENDPOINTS", {"claude-api": model_map._ENDPOINTS["claude-api"]})
    monkeypatch.setattr(model_map, "claude_login_token", lambda: "fixture-login")
    monkeypatch.setattr(config, "get_jarvis_agent_secret", lambda provider: None)
    monkeypatch.setattr(
        config, "resolve_provider_endpoint", lambda *args, **kwargs: SimpleNamespace(
            credential=None, base_url="https://api.anthropic.com", via_proxy=False,
        ),
    )
    monkeypatch.setattr(provider_errors, "_REPORTS", {})


@pytest.mark.parametrize("extra", [
    {"is_enabled": False}, {"is_enabled": True, "spend_limit_reached": True},
])
def test_blocked_login_is_absent_from_every_picker_list(claude_only, monkeypatch, extra):
    calls = []

    def usage(token):
        calls.append(token)
        return {"extra_usage": extra}

    monkeypatch.setattr(provider_errors, "_claude_usage", usage)
    assert model_map.usable_providers(None) == []
    assert model_map.login_providers() == []
    assert model_map.access_choices() == {}
    assert calls == ["fixture-login"]
    with pytest.raises(model_map.RouteUnavailable, match="Extra Usage"):
        model_map.route_for(None, "claude-api", "fixture-model", account_id="subscription")


def test_blocked_login_does_not_hide_a_separate_api_key(claude_only, monkeypatch):
    monkeypatch.setattr(config, "get_jarvis_agent_secret", lambda provider: "fixture-api-key")
    monkeypatch.setattr(provider_errors, "_claude_usage", lambda token: {
        "extra_usage": {"is_enabled": False},
    })
    assert model_map.usable_providers(None) == ["claude-api"]
    assert model_map.login_providers() == []
    assert model_map.access_choices() == {"claude-api": ["api"]}
    with pytest.raises(model_map.RouteUnavailable):
        model_map.route_for(None, "claude-api", "fixture-model", account_id="subscription")


@pytest.mark.parametrize("usage", [None, {}, {"extra_usage": {"is_enabled": True}}])
def test_unknown_or_enabled_usage_does_not_invent_a_block(claude_only, monkeypatch, usage):
    monkeypatch.setattr(provider_errors, "_claude_usage", lambda token: usage)
    assert model_map.usable_providers(None) == ["claude-api"]
    assert model_map.login_providers() == ["claude-api"]
    assert model_map.access_choices() == {"claude-api": ["subscription"]}


def test_missing_login_never_requests_usage(claude_only, monkeypatch):
    monkeypatch.setattr(model_map, "claude_login_token", lambda: None)

    def unexpected(token):
        pytest.fail("A disconnected account must not request usage")

    monkeypatch.setattr(provider_errors, "_claude_usage", unexpected)
    assert model_map.usable_providers(None) == []
    assert model_map.login_providers() == []
    assert model_map.access_choices() == {}


def test_usage_outage_is_cached_briefly_and_recovers(claude_only, monkeypatch):
    import time

    now = [100.0]
    calls = []

    def unavailable(token):
        calls.append(token)
        return None

    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr(provider_errors, "_claude_usage", unavailable)
    assert model_map.usable_providers(None) == ["claude-api"]
    assert model_map.login_providers() == ["claude-api"]
    assert model_map.access_choices() == {"claude-api": ["subscription"]}
    assert calls == ["fixture-login"]

    now[0] += 11.0
    monkeypatch.setattr(provider_errors, "_claude_usage", lambda token: {
        "extra_usage": {"is_enabled": False},
    })
    assert model_map.usable_providers(None) == []


def test_picker_recovers_after_extra_usage_is_enabled(claude_only, monkeypatch):
    import time

    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr(provider_errors, "_claude_usage", lambda token: {
        "extra_usage": {"is_enabled": False},
    })
    assert model_map.usable_providers(None) == []
    now[0] += 121.0
    monkeypatch.setattr(provider_errors, "_claude_usage", lambda token: {
        "extra_usage": {"is_enabled": True},
    })
    assert model_map.usable_providers(None) == ["claude-api"]
    assert model_map.access_choices() == {"claude-api": ["subscription"]}
