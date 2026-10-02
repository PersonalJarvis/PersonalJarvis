"""Live surfaces expose account-specific pickers without using legacy setters."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.live.config import LiveConfig
from jarvis.ui.web.provider_spec import PROVIDERS


@pytest.fixture
def live_catalog(monkeypatch):
    from jarvis.brain import model_catalog
    from jarvis.live import subscription_auth, subscription_reasoning
    from jarvis.ui.web import live_routes

    state = SimpleNamespace(api_calls=[], accounts=[], clients=[], fail=False)

    class ApiCatalog:
        async def list_models(self, provider):
            state.api_calls.append(provider)
            return SimpleNamespace(
                models=[SimpleNamespace(id="api-thinking", label="API thinking")],
                source="fixture-api",
            )

    class Account:
        def __init__(self, account_id):
            self.account_id = account_id
            self.closed = False
            state.accounts.append(self)

        async def credentials(self):
            if state.fail:
                raise subscription_auth.SubscriptionAuthError("Account unavailable")
            return SimpleNamespace(account_id=self.account_id)

        async def aclose(self):
            self.closed = True

    class AccountCatalog:
        def __init__(self, *, credentials):
            self.credentials = credentials
            self.closed = False
            state.clients.append(self)

        async def list_models(self):
            selected = await self.credentials()
            return [{"id": "plan-thinking", "label": selected.account_id,
                     "efforts": ["low", "high"], "default_effort": "low"}]

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(model_catalog, "shared_catalog", ApiCatalog)
    monkeypatch.setattr(subscription_auth, "SubscriptionAuth", Account)
    monkeypatch.setattr(subscription_reasoning, "SubscriptionReasoning", AccountCatalog)
    app = FastAPI()
    app.include_router(live_routes.router)
    with TestClient(app) as client:
        yield client, state


@pytest.mark.parametrize(
    "spec", [spec for spec in PROVIDERS if spec.configuration_surface == "live"],
    ids=lambda spec: spec.id,
)
def test_each_live_surface_has_its_authoritative_catalog(live_catalog, spec):
    client, state = live_catalog
    mode = {"api_key": "api_key", "codex": "chatgpt_subscription"}[spec.auth_mode]
    response = client.get("/api/live/options", params={
        "auth_mode": mode, "account_id": "selected-account",
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["models"] and payload["voices"]
    default_voice = LiveConfig(auth_mode=mode).for_session().voice
    assert default_voice in payload["voices"]
    if mode == "chatgpt_subscription":
        assert payload["source"] == mode
        assert payload["models"] == [{"id": "plan-thinking", "label": "selected-account",
                                      "efforts": ["low", "high"], "default_effort": "low"}]
        assert state.accounts[0].account_id == "selected-account"
        assert state.accounts[0].closed and state.clients[0].closed
        assert state.api_calls == []
    else:
        assert payload["models"][0]["id"] == "api-thinking"
        assert state.api_calls == ["openai"]
        assert state.accounts == []


def test_subscription_catalog_failure_never_uses_api_catalog(live_catalog):
    client, state = live_catalog
    state.fail = True
    response = client.get("/api/live/options", params={
        "auth_mode": "chatgpt_subscription", "account_id": "selected-account",
    })
    assert response.status_code == 409
    assert response.json()["detail"] == "Connect an eligible ChatGPT account to load its models."
    assert state.api_calls == []
    assert state.accounts[0].closed and state.clients[0].closed


def test_invalid_catalog_access_is_rejected_before_credentials(live_catalog):
    client, state = live_catalog
    assert client.get("/api/live/options?auth_mode=unknown").status_code == 422
    assert state.api_calls == [] and state.accounts == []
