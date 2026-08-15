"""The publish routes: sign-in flow, validation, submit mapping — no network."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from jarvis.marketplace import publish
from jarvis.marketplace.auth.base import AuthSession, FlowResult
from jarvis.marketplace.token_store import Tokens
from jarvis.ui.web import marketplace_publish_routes as routes


class FakeStore:
    tokens: Tokens | None = None

    def __init__(self) -> None:  # each route call constructs one; share state
        pass

    def load(self, key: str) -> Tokens | None:
        return FakeStore.tokens

    def save(self, key: str, tokens: Tokens) -> None:
        FakeStore.tokens = tokens

    def delete(self, key: str) -> None:
        FakeStore.tokens = None


class FakeHandler:
    """Device flow that approves instantly."""

    async def start(self, spec: object) -> AuthSession:
        return AuthSession(
            flow_id="flow-1",
            plugin_id=publish.PUBLISHER_TOKEN_ID,
            kind="device_flow",
            user_code="ABCD-1234",
            verification_uri="https://github.com/login/device",
            verification_uri_complete=None,
            expires_at_ms=0,
            interval=1,
        )

    async def await_completion(self, session: AuthSession) -> FlowResult:
        return FlowResult(tokens=Tokens(access="tok"), error=None)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> httpx.AsyncClient:
    FakeStore.tokens = None
    monkeypatch.setattr(routes, "TokenStore", FakeStore)
    monkeypatch.setattr(publish, "publish_endpoint", lambda: "https://pj.example/api/submit")
    monkeypatch.setattr(publish, "make_device_handler", lambda: FakeHandler())

    async def fake_fetch_identity(tokens: Tokens, transport: object = None) -> dict[str, Any]:
        return {"login": "octocat", "avatar_url": None}

    monkeypatch.setattr(publish, "fetch_identity", fake_fetch_identity)
    app = FastAPI()
    app.include_router(routes.router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_signin_start_then_poll_connects(client: httpx.AsyncClient) -> None:
    async with client:
        started = (await client.post("/api/marketplace/publish/signin/start")).json()
        assert started["user_code"] == "ABCD-1234"
        # The completion task runs on the same loop; yield until it settles.
        for _ in range(50):
            poll = (
                await client.get(f"/api/marketplace/publish/signin/poll/{started['flow_id']}")
            ).json()
            if poll["status"] != "pending":
                break
            await asyncio.sleep(0)
        assert poll == {"status": "connected", "login": "octocat", "avatar_url": None}
    assert FakeStore.tokens is not None and FakeStore.tokens.access == "tok"


@pytest.mark.asyncio
async def test_identity_reports_disabled_deployment(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(publish, "publish_endpoint", lambda: "")
    monkeypatch.setattr(publish, "publish_wallpaper_endpoint", lambda: "")
    async with client:
        state = (await client.get("/api/marketplace/publish/identity")).json()
    assert state == {"enabled": False, "wallpapers_enabled": False, "signed_in": False}


@pytest.mark.asyncio
async def test_the_two_lanes_are_reported_separately(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fork may run one lane without the other, and each surface hides its
    own button accordingly — so one flag cannot speak for both."""
    monkeypatch.setattr(publish, "publish_endpoint", lambda: "")
    monkeypatch.setattr(publish, "publish_wallpaper_endpoint", lambda: "https://pj.example/w")
    async with client:
        state = (await client.get("/api/marketplace/publish/identity")).json()
    assert state["enabled"] is False
    assert state["wallpapers_enabled"] is True
    # Still reports sign-in state: the wallpaper lane needs the same identity.
    assert "signed_in" in state


@pytest.mark.asyncio
async def test_sign_out_drops_the_token(client: httpx.AsyncClient) -> None:
    FakeStore.tokens = Tokens(access="tok")
    async with client:
        resp = await client.delete("/api/marketplace/publish/identity")
    assert resp.status_code == 200
    assert FakeStore.tokens is None


@pytest.mark.asyncio
async def test_validate_reports_every_field(client: httpx.AsyncClient) -> None:
    async with client:
        resp = await client.post(
            "/api/marketplace/publish/validate",
            json={"kind": "skill", "name": "UPPER", "version": "nope"},
        )
    body = resp.json()
    assert body["ok"] is False
    fields = {e["field"] for e in body["errors"]}
    assert {"name", "version", "title", "description", "skill_md"} <= fields


@pytest.mark.asyncio
async def test_submit_maps_endpoint_refusals(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refusing_submit(normalized: dict[str, Any], store: object = None) -> dict[str, Any]:
        raise publish.SubmitError(409, "name is owned by octocat", "name")

    monkeypatch.setattr(publish, "submit", refusing_submit)
    async with client:
        resp = await client.post(
            "/api/marketplace/publish/submit",
            json={
                "kind": "skill",
                "name": "taken-name",
                "version": "1.0.0",
                "title": "T",
                "description": "D",
                "skill_md": "---\nname: taken-name\ndescription: D\n---\nBody",
            },
        )
    assert resp.status_code == 409
    assert resp.json()["detail"] == {"error": "name is owned by octocat", "field": "name"}


@pytest.mark.asyncio
async def test_submit_happy_path(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ok_submit(normalized: dict[str, Any], store: object = None) -> dict[str, Any]:
        return {"pr_url": "https://github.com/pr/9", "submission_path": "submissions/x.json"}

    monkeypatch.setattr(publish, "submit", ok_submit)
    async with client:
        resp = await client.post(
            "/api/marketplace/publish/submit",
            json={
                "kind": "skill",
                "name": "fresh-name",
                "version": "1.0.0",
                "title": "T",
                "description": "D",
                "skill_md": "---\nname: fresh-name\ndescription: D\n---\nBody",
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["pr_url"] == "https://github.com/pr/9"
    assert body["name"] == "fresh-name" and body["version"] == "1.0.0"
    # The line the author hands to everyone else. Computed by the one standard
    # module, so it cannot drift from what `jarvis marketplace install` accepts.
    assert body["install"] == {
        "cli": "jarvis marketplace install fresh-name",
        "runner": "uvx --from personal-jarvis jarvis marketplace install fresh-name",
        "prompt": 'Install the "fresh-name" skill from the community marketplace.',
    }


@pytest.mark.asyncio
async def test_submit_client_side_validation_answers_422(client: httpx.AsyncClient) -> None:
    async with client:
        resp = await client.post(
            "/api/marketplace/publish/submit",
            json={"kind": "skill", "name": "UPPER", "version": "1.0.0"},
        )
    assert resp.status_code == 422
    assert resp.json()["detail"]["field"] == "name"
