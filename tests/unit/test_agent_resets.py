"""Reset receipts, account boundaries and unknown-versus-empty inventory."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis import agent_resets
from jarvis.agent_accounts import AgentAccount
from tests.fakes.fake_reset_rpc import FakeResetRpc


@pytest.fixture
def account(tmp_path: Path) -> AgentAccount:
    (tmp_path / "auth.json").write_text(json.dumps({"tokens": {"account_id": "seat-1"}}))
    return AgentAccount("codex:test", "codex", "Test seat", tmp_path)


def payload(count=2, details=None):
    return {
        "rateLimitResetCredits": {"availableCount": count, "credits": details},
        "rateLimits": {
            "limitId": "codex",
            "planType": "pro",
            "secondary": {
                "usedPercent": 81,
                "windowDurationMins": 10080,
                "resetsAt": 1900000000,
            },
        },
    }


@pytest.fixture
def rpc(monkeypatch):
    from jarvis.agent_chat import native_control, runner_cli

    fake = FakeResetRpc(payload())
    monkeypatch.setattr(native_control, "GoalRpc", fake.factory)
    monkeypatch.setattr(runner_cli, "codex_argv_prefix", lambda: ["fake-codex"])
    return fake


@pytest.mark.parametrize("count,rows,can_redeem", [(0, [], False), (2, None, True), (2, [], False)])
def test_authoritative_count_and_missing_details(account, count, rows, can_redeem):
    result = agent_resets._snapshot(account, payload(count, rows), "key")
    assert result["available_count"] == count
    assert result["credits"] == rows
    assert result["can_redeem"] is can_redeem
    assert result["usage"]["windows"][0]["kind"] == "weekly"


@pytest.mark.parametrize("summary", [None, {}, {"availableCount": True}, {"availableCount": -1}])
def test_missing_or_invalid_inventory_is_not_zero(account, summary):
    result = agent_resets._snapshot(account, {"rateLimitResetCredits": summary}, "key")
    assert result["status"] == "unavailable"
    assert result["available_count"] is None
    assert not result["can_redeem"]


@pytest.mark.parametrize(
    "status,kind,expiry,enabled",
    [
        ("available", "codexRateLimits", None, True),
        ("redeemed", "codexRateLimits", None, False),
        ("available", "unknown", None, False),
        ("available", "codexRateLimits", 1, False),
        ("available", "codexRateLimits", "bad", False),
    ],
)
def test_only_known_available_unexpired_details_can_be_used(account, status, kind, expiry, enabled):
    result = agent_resets._snapshot(
        account,
        payload(
            5,
            [
                {
                    "id": "credit-1",
                    "status": status,
                    "resetType": kind,
                    "expiresAt": expiry,
                    "title": {"malformed": True},
                }
            ],
        ),
        "key",
    )
    assert result["available_count"] == 5  # Details may be capped.
    assert result["can_redeem"] is enabled
    assert result["credits"][0]["title"] is None


@pytest.mark.asyncio
async def test_read_uses_selected_seat_and_never_starts_a_turn(account, rpc, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-inherit")
    monkeypatch.setenv("CODEX_HOME", "wrong-seat")
    result = await agent_resets.read_resets(account)
    assert result["status"] == "ok"
    assert rpc.options[1]["CODEX_HOME"] == str(account.config_dir)
    assert "OPENAI_API_KEY" not in rpc.options[1]
    assert 'cli_auth_credentials_store="file"' in rpc.options[0]
    assert [method for method, _ in rpc.calls] == [
        "initialize",
        "initialized",
        "account/read",
        "account/rateLimits/read",
    ]
    assert rpc.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["reset", "alreadyRedeemed", "nothingToReset", "noCredit"])
async def test_receipt_and_same_key_replay_without_inventory_precheck(account, rpc, outcome):
    rpc.outcome = outcome
    key = agent_resets._identity(account, rpc.account)
    for _ in range(2):
        result = await agent_resets.consume_reset(
            account, attempt="same-uuid", credit_id="credit-1", account_key=key
        )
        assert result["outcome"] == outcome
    redemptions = [params for method, params in rpc.calls if method.endswith("/consume")]
    assert redemptions == [{"idempotencyKey": "same-uuid", "creditId": "credit-1"}] * 2
    assert rpc.calls.index(("account/rateLimits/read", {})) > rpc.calls.index(
        ("account/rateLimitResetCredit/consume", redemptions[0])
    )


@pytest.mark.asyncio
async def test_changed_login_cannot_consume(account, rpc):
    with pytest.raises(agent_resets.ResetError) as caught:
        await agent_resets.consume_reset(account, attempt="uuid", credit_id=None, account_key="old")
    assert caught.value.code == "account_changed"
    assert not any(method.endswith("/consume") for method, _ in rpc.calls)
    assert rpc.closed


@pytest.mark.asyncio
async def test_refresh_failure_preserves_confirmed_receipt(account, rpc):
    rpc.failure = "account/rateLimits/read"
    result = await agent_resets.consume_reset(
        account,
        attempt="uuid",
        credit_id=None,
        account_key=agent_resets._identity(account, rpc.account),
    )
    assert result["outcome"] == "reset"
    assert result["resets"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_lost_response_never_retries_and_does_not_leak_error(account, rpc):
    rpc.failure = "account/rateLimitResetCredit/consume"
    with pytest.raises(agent_resets.ResetError) as caught:
        await agent_resets.consume_reset(
            account,
            attempt="uuid",
            credit_id=None,
            account_key=agent_resets._identity(account, rpc.account),
        )
    assert "private" not in str(caught.value)
    assert len([m for m, _ in rpc.calls if m.endswith("/consume")]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,status",
    [("claude", "browser"), ("grok-build", "unsupported"), ("future", "unsupported")],
)
async def test_unsupported_providers_do_not_invent_inventory(account, rpc, provider, status):
    other = AgentAccount(provider, provider, "Test", account.config_dir)
    result = await agent_resets.read_resets(other)
    assert result["status"] == status
    assert result["available_count"] is None
    assert rpc.calls == []
    with pytest.raises(agent_resets.ResetError):
        await agent_resets.consume_reset(other, attempt="uuid", credit_id=None, account_key="key")


def test_routes_validate_confirmation_and_account_and_mark_cli_dangerous(account, rpc, monkeypatch):
    from jarvis.ui.web import agent_accounts_routes as routes

    monkeypatch.setattr(
        routes.agent_accounts, "resolve", lambda value: account if value == account.id else None
    )
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    url = f"/api/agent-accounts/{account.id}/resets"
    inventory = client.get(url).json()
    request = {
        "confirmed": False,
        "idempotency_key": "f453a2b8-8ad7-449f-b7c9-27a1f3483934",
        "account_key": inventory["account_key"],
    }
    assert client.post(url + "/consume", json=request).status_code == 422
    assert client.post(url + "/consume", json={**request, "confirmed": "true"}).status_code == 422
    assert client.get("/api/agent-accounts/missing/resets").status_code == 404
    assert not any(m.endswith("/consume") for m, _ in rpc.calls)
    response = client.post(url + "/consume", json={**request, "confirmed": True})
    assert response.status_code == 200
    assert response.json()["outcome"] == "reset"
    schema = app.openapi()["paths"]["/api/agent-accounts/{account_id}/resets/consume"]["post"]
    assert schema["x-jarvis-dangerous"] is True


def test_frontend_reset_contract_matches_serialized_inventory_and_request(account):
    from jarvis.ui.web.agent_accounts_routes import ResetConsumeRequest

    source = (
        (
            Path(__file__).resolve().parents[2]
            / "jarvis/ui/web/frontend/src/lib/agentResetsApi.ts"
        )
        .resolve()
        .read_text(encoding="utf-8")
    )

    def fields(name):
        body = re.search(rf"export interface {name} \{{(.*?)\n\}}", source, re.S).group(1)
        return set(re.findall(r"^  (\w+):", body, re.M))

    assert fields("AccountResets") == set(agent_resets._empty(account, "unsupported"))
    assert fields("ResetAttempt") == set(ResetConsumeRequest.model_fields) - {"confirmed"}
    snapshot = agent_resets._snapshot(
        account,
        payload(1, [{"id": "one", "status": "available", "resetType": "codexRateLimits"}]),
        "key",
    )
    assert fields("ResetCredit") == set(snapshot["credits"][0])
