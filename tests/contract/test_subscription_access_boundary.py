"""A selected subscription cannot recover through a metered API credential."""

from types import SimpleNamespace

import pytest

from jarvis.plugins.realtime.openai_live import OpenAILiveProvider
from jarvis.plugins.realtime.openai_subscription_live import OpenAISubscriptionLiveProvider
from jarvis.realtime import factory


@pytest.fixture
def access_registry(monkeypatch):
    from jarvis.live import subscription_auth

    state = SimpleNamespace(key_reads=[], auth_failure=False, connected=True)
    plugins = {"plan-fixture": OpenAISubscriptionLiveProvider, "api-fixture": OpenAILiveProvider}
    monkeypatch.setattr(factory, "list_plugins", lambda _group: list(plugins))
    monkeypatch.setattr(factory, "load", lambda _group, name, **_kwargs: plugins[name])

    def key_reader(candidates):
        state.key_reads.append(candidates)
        return "fixture-key"

    class Account:
        @classmethod
        def from_runtime_config(cls, _cfg):
            if state.auth_failure:
                raise subscription_auth.SubscriptionAuthError("Selected account unavailable")
            return cls()

        def status_snapshot(self):
            return {"connected": state.connected}

        async def credentials(self, **_kwargs):
            raise AssertionError("Factory discovery must not run inference or refresh login")

    monkeypatch.setattr(factory, "get_secret_any", key_reader)
    monkeypatch.setattr(subscription_auth, "SubscriptionAuth", Account)
    return state


def config(primary, fallback=""):
    return SimpleNamespace(brain=SimpleNamespace(realtime=SimpleNamespace(
        provider=primary, fallback_provider=fallback, fallback_provider_2="",
    )))


def test_subscription_only_profile_never_reads_ambient_api_keys(access_registry):
    candidates = factory._provider_candidates(config("plan-fixture"))
    assert len(candidates) == 1
    assert candidates[0].client_managed_delegation
    assert access_registry.key_reads == []


@pytest.mark.parametrize("failure", ["constructor", "signed_out"])
def test_failed_subscription_cannot_use_a_retained_api_fallback(access_registry, failure):
    access_registry.auth_failure = failure == "constructor"
    access_registry.connected = failure != "signed_out"
    candidates = factory._provider_candidates(config("plan-fixture", "api-fixture"))
    assert candidates == []
    assert access_registry.key_reads == []


def test_api_mode_keeps_its_credential_selection(access_registry):
    candidates = factory._provider_candidates(config("api-fixture"))
    assert len(candidates) == 1
    assert candidates[0].name == "openai-live"
    assert access_registry.key_reads


@pytest.mark.parametrize("failure", ["unregistered", "broken"])
def test_unavailable_primary_never_drops_into_saved_api_chain(
    access_registry, monkeypatch, failure,
):
    primary = "missing-fixture" if failure == "unregistered" else "plan-fixture"
    if failure == "broken":
        original = factory.load

        def load(group, name, **kwargs):
            if name == primary:
                raise ImportError("Selected plugin unavailable")
            return original(group, name, **kwargs)

        monkeypatch.setattr(factory, "load", load)
    assert factory._provider_candidates(config(primary, "api-fixture")) == []
    assert access_registry.key_reads == []
