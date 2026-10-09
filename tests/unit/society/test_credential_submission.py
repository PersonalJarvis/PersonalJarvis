"""Credential validation, safe failures and recovery with fake storage and HTTP."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from jarvis.agent_chat.credential_requests import CredentialRequestBusy
from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.service import AgentChatService
from jarvis.society import credentials
from jarvis.society.credentials import CredentialError, CredentialVault
from jarvis.ui.web.agent_chat_routes import router
from tests.fakes.fake_secret_store import FakeSecretStore
from tests.unit.society.test_agent_credentials import _SPEC, SECRET, _service_with_turn


def _discord(monkeypatch: pytest.MonkeyPatch, handler):
    client_type = httpx.AsyncClient
    seen = []

    def respond(request):
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    return seen


@pytest.mark.parametrize("status,body,code", [
    (401, {"message": SECRET}, "invalid_token"),
    (403, {"message": SECRET}, "invalid_token"),
    (429, {"message": SECRET}, "network_error"),
    (503, {"message": SECRET}, "network_error"),
    (302, {}, "network_error"),
    (200, {"id": "123", "bot": False}, "invalid_token"),
    (200, {"bot": True}, "invalid_token"),
])
async def test_discord_failure_does_not_store_or_echo_the_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status, body, code,
) -> None:
    vault = CredentialVault(tmp_path, secrets=FakeSecretStore())
    monkeypatch.setattr(credentials, "current_vault", lambda: vault)
    seen = _discord(monkeypatch, lambda _req: httpx.Response(
        status, json=body, headers={"Location": "https://untrusted.invalid/token"},
    ))
    with pytest.raises(CredentialError) as caught:
        await credentials.save_requested_credential("ada", "DISCORD_BOT_TOKEN", SECRET)
    assert caught.value.code == code
    assert SECRET not in str(caught.value)
    assert vault.values("ada") == {} and vault.list("ada") == []
    assert len(seen) == 1  # No redirect or automatic retry with the secret.
    assert str(seen[0].url) == "https://discord.com/api/v10/users/@me"


async def test_discord_success_checks_bot_identity_then_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = FakeSecretStore()
    vault = CredentialVault(tmp_path, secrets=secrets)
    monkeypatch.setattr(credentials, "current_vault", lambda: vault)
    seen = _discord(monkeypatch, lambda _req: httpx.Response(200, json={"id": "123", "bot": True}))
    await credentials.save_requested_credential("ada", "DISCORD_BOT_TOKEN", f" {SECRET} ")
    assert seen[0].headers["authorization"] == f"Bot {SECRET}"
    assert CredentialVault(tmp_path, secrets=secrets).values("ada") == {"DISCORD_BOT_TOKEN": SECRET}
    index = await asyncio.to_thread(lambda: (tmp_path / "ada.json").read_text(encoding="utf-8"))
    assert SECRET not in index


@pytest.mark.parametrize("failure,code", [
    (httpx.ConnectError, "network_error"),
    (httpx.ReadTimeout, "validation_timeout"),
])
async def test_discord_transport_errors_are_safe_and_retryable(
    monkeypatch: pytest.MonkeyPatch, failure, code,
) -> None:
    def respond(request):
        raise failure(SECRET, request=request)

    _discord(monkeypatch, respond)
    with pytest.raises(CredentialError) as caught:
        await credentials.validate_token("DISCORD_BOT_TOKEN", SECRET)
    assert caught.value.code == code and SECRET not in str(caught.value)


async def test_arbitrary_credentials_never_select_a_validation_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def never(_request):
        raise AssertionError("An unknown service must not receive the secret")

    seen = _discord(monkeypatch, never)
    await credentials.validate_token("CUSTOM_API_KEY", SECRET, label="https://untrusted.invalid")
    assert seen == []


def test_storage_backend_errors_and_failed_index_commit_restore_previous_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = FakeSecretStore()
    vault = CredentialVault(tmp_path, secrets=secrets)
    vault.store("ada", "API_KEY", "previous-safe-value")

    def failed_index(*_args):
        raise OSError(f"disk failure containing {SECRET}")

    monkeypatch.setattr(vault, "_write_index", failed_index)
    with pytest.raises(CredentialError) as caught:
        vault.store("ada", "API_KEY", SECRET)
    assert caught.value.code == "storage_failed" and SECRET not in str(caught.value)
    assert vault.values("ada") == {"API_KEY": "previous-safe-value"}
    assert CredentialVault(tmp_path, secrets=secrets).values("ada") == vault.values("ada")


def test_a_store_that_claims_success_without_saving_is_not_accepted(tmp_path: Path) -> None:
    class LosingStore(FakeSecretStore):
        def set(self, slot: str, value: str) -> bool:
            return True

    vault = CredentialVault(tmp_path, secrets=LosingStore())
    with pytest.raises(CredentialError, match="could not be stored"):
        vault.store("ada", "API_KEY", SECRET)
    assert vault.values("ada") == {} and vault.list("ada") == []


async def test_pending_field_survives_restart_and_saved_retry_does_not_write_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = CredentialVault(tmp_path / "vault", secrets=FakeSecretStore())
    monkeypatch.setattr(credentials, "current_vault", lambda: vault)
    first, sid, turn = await _service_with_turn(tmp_path)
    rid = await first.open_credential_request(sid, _SPEC, lambda _value: None)
    turn.cancel()
    first._cancel_questions(sid)
    fresh = AgentChatService(first.store)
    assert await fresh.submit_credential(sid, rid, SECRET)
    assert vault.values("ada") == {"GITHUB_TOKEN": SECRET}
    restarted = AgentChatService(first.store)
    assert await restarted.submit_credential(sid, rid, "different-value")
    assert vault.values("ada") == {"GITHUB_TOKEN": SECRET}
    assert not await restarted.submit_credential("society:bob", rid, SECRET)
    assert SECRET not in json.dumps(first.store.list_events(sid))


@pytest.mark.parametrize("status", ["cancelled", "declined", "timeout"])
async def test_historical_closed_requests_are_never_reopened(
    tmp_path: Path, status: str,
) -> None:
    first, sid, turn = await _service_with_turn(tmp_path)
    rid = await first.open_credential_request(sid, _SPEC, lambda _value: None)
    await first._emit(sid, make_event("credential_resolved", {"request_id": rid, "status": status}))
    fresh = AgentChatService(first.store)
    assert not await fresh.submit_credential(sid, rid, SECRET)
    assert fresh.pending_credential_requests(sid) == []
    turn.cancel()


async def test_disconnected_submit_finishes_once_and_cannot_race_decline(tmp_path: Path) -> None:
    svc, sid, turn = await _service_with_turn(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    saved = []

    async def save(value):
        entered.set()
        await release.wait()
        saved.append(value)

    rid = await svc.open_credential_request(sid, _SPEC, save)
    caller = asyncio.create_task(svc.submit_credential(sid, rid, SECRET))
    await entered.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    with pytest.raises(CredentialRequestBusy):
        await svc.decline_credential(sid, rid)
    with pytest.raises(CredentialRequestBusy):
        await svc.submit_credential(sid, rid, SECRET)
    svc._cancel_questions(sid)
    release.set()
    assert await svc.wait_credential_request(sid, rid, 1) == "saved"
    assert await svc.submit_credential(sid, rid, SECRET)
    assert saved == [SECRET]
    turn.cancel()


async def test_repeated_requests_reuse_the_existing_field(tmp_path: Path) -> None:
    svc, sid, turn = await _service_with_turn(tmp_path)
    rid = await svc.open_credential_request(sid, _SPEC, lambda _value: None)
    svc._cancel_questions(sid)
    assert await svc.open_credential_request(sid, _SPEC, lambda _value: None) == rid
    assert svc.pending_credential_requests(sid) == [rid]
    turn.cancel()


@pytest.mark.parametrize("failure,code,status", [
    (CredentialError("The token was rejected."), "invalid_token", 400),
    (CredentialError("Network unavailable.", code="network_error"), "network_error", 503),
    (CredentialError("Timed out.", code="validation_timeout"), "validation_timeout", 504),
    (OSError(SECRET), "storage_failed", 503),
])
async def test_route_failures_keep_field_open_and_success_has_receipt(
    tmp_path: Path, caplog, failure, code, status,
) -> None:
    svc, sid, turn = await _service_with_turn(tmp_path)
    failing = True
    saved = []

    def save(value):
        if failing:
            raise failure
        saved.append(value)

    rid = await svc.open_credential_request(sid, _SPEC, save)
    app = FastAPI()
    app.state.agent_chat = svc
    app.include_router(router)
    path = f"/api/agent-chat/sessions/{sid}/credentials/{rid}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        failed = await client.post(path, json={"value": SECRET})
        assert failed.status_code == status
        assert failed.json()["detail"]["code"] == code
        assert SECRET not in failed.text + caplog.text
        assert svc.pending_credential_requests(sid) == [rid]
        failing = False
        success = await client.post(path, json={"value": SECRET})
        assert success.json() == {"ok": True, "request_id": rid, "status": "saved"}
        assert saved == [SECRET]
    turn.cancel()


async def test_non_string_value_is_not_echoed_in_validation_error(tmp_path: Path) -> None:
    svc, sid, turn = await _service_with_turn(tmp_path)
    rid = await svc.open_credential_request(sid, _SPEC, lambda _value: None)
    app = FastAPI()
    app.state.agent_chat = svc
    app.include_router(router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post(
            f"/api/agent-chat/sessions/{sid}/credentials/{rid}", json={"value": {"secret": SECRET}},
        )
    assert response.status_code == 400 and SECRET not in response.text
    assert svc.pending_credential_requests(sid) == [rid]
    turn.cancel()


async def test_failed_save_after_legacy_timeout_keeps_field_open(tmp_path: Path) -> None:
    svc, sid, turn = await _service_with_turn(tmp_path)

    async def save(_value):
        await asyncio.sleep(0.02)
        raise CredentialError("The token was rejected.")

    rid = await svc.open_credential_request(sid, _SPEC, save, timeout_s=0.001)
    with pytest.raises(CredentialError):
        await svc.submit_credential(sid, rid, SECRET)
    assert svc.pending_credential_requests(sid) == [rid]
    assert not any(e["kind"] == "credential_resolved" for e in svc.store.list_events(sid))
    turn.cancel()
