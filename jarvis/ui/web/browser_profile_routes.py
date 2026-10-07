"""Saved browser identities and the authenticated local Chrome connection."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import zipfile
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .society_routes import _runtime
from .surface_security import chrome_transport_allowed

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/society", tags=["browser-profiles"])


class ProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["managed", "chrome"]
    allowed_domains: list[str] = Field(default_factory=list, max_length=100)


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    allowed_domains: list[str] | None = Field(default=None, max_length=100)


class SharingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["all", "selected"]
    agent_ids: list[str] = Field(default_factory=list, max_length=10000)


class BindingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["inherit", "own", "profile"]
    profile_id: str | None = Field(default=None, max_length=80)


async def _snapshot(rt: Any) -> dict:
    agents = [{"agent_id": a.agent_id, "name": a.name} for a in await rt.roster.list()]
    live = rt.browser.live
    connected = {
        s.profile_binding.profile_id
        for s in live.sessions.values()
        if not s.closed and getattr(s, "profile_binding", None)
    }
    result = await asyncio.to_thread(live.profiles.snapshot, agents, connected)
    for row in result["profiles"]:
        if row["kind"] == "chrome":
            row["connected"] = live.chrome.connected(row["id"])
    return result


async def _mutation(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


async def _configuration(live: Any, fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return await live.configure_profiles(fn, *args, **kwargs)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/browser/profiles")
async def list_browser_profiles(request: Request) -> dict:
    """List profiles and effective assignments without launching a browser."""
    return await _snapshot(await _runtime(request))


@router.post("/browser/profiles")
async def create_browser_profile(body: ProfileBody, request: Request) -> dict:
    """Create a browser identity without changing existing agent assignments."""
    rt = await _runtime(request)
    row = await _mutation(
        rt.browser.live.profiles.create, body.name, body.kind, body.allowed_domains
    )
    return {**row, "connected": False, "agent_ids": [], "is_default": False}


@router.patch("/browser/profiles/{profile_id}")
async def update_browser_profile(profile_id: str, body: ProfileUpdate, request: Request) -> dict:
    """Update the name or permitted websites and invalidate old sessions."""
    rt = await _runtime(request)
    await _configuration(
        rt.browser.live,
        rt.browser.live.profiles.update,
        profile_id,
        name=body.name,
        domains=body.allowed_domains,
    )
    return await _snapshot(rt)


@router.put("/browser/profiles/{profile_id}/sharing")
async def share_browser_profile(profile_id: str, body: SharingBody, request: Request) -> dict:
    """Share with all current and future agents, or exactly the selected agents."""
    rt = await _runtime(request)
    agents = await rt.roster.list()
    await _configuration(
        rt.browser.live,
        rt.browser.live.profiles.share,
        profile_id,
        body.scope,
        body.agent_ids,
        {a.agent_id for a in agents},
    )
    return await _snapshot(rt)


@router.put("/agents/{agent_id}/browser/profile")
async def assign_agent_browser_profile(agent_id: str, body: BindingBody, request: Request) -> dict:
    """Set an agent override or let the agent inherit the shared default."""
    rt = await _runtime(request)
    agent = await rt.roster.resolve(agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    await _configuration(
        rt.browser.live,
        rt.browser.live.profiles.assign,
        agent.agent_id,
        body.mode,
        body.profile_id,
        agent_ids={agent.agent_id},
    )
    return await _snapshot(rt)


@router.delete("/browser/profiles/{profile_id}", openapi_extra={"x-jarvis-dangerous": True})
async def remove_browser_profile(profile_id: str, request: Request) -> dict:
    """Revoke a profile without deleting any browser cookies or personal Chrome data."""
    rt = await _runtime(request)

    async def revoke() -> None:
        await _configuration(rt.browser.live, rt.browser.live.profiles.remove, profile_id)

    await rt.browser.live.chrome.change_credentials(profile_id, revoke)
    return await _snapshot(rt)


@router.post("/browser/profiles/{profile_id}/pair")
async def create_chrome_pairing(profile_id: str, request: Request) -> Response:
    """Create a single-use five-minute code for the local Chrome extension."""
    rt = await _runtime(request)
    code = await _mutation(rt.browser.live.profiles.pair_code, profile_id)
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    return Response(
        json.dumps(
            {"pairing_code": code, "expires_in": 300, "server_url": f"http://127.0.0.1:{port}"}
        ),
        media_type="application/json",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/browser/extension.zip")
def download_chrome_extension() -> Response:
    """Download the extension for local installation in a chosen Chrome profile."""
    folder = Path(__file__).parents[2] / "assets" / "chrome-extension"
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.iterdir()):
            if path.is_file() and path.suffix in {
                ".js",
                ".mjs",
                ".json",
                ".html",
                ".css",
                ".png",
                ".svg",
            }:
                archive.write(path, path.name)
    return Response(
        data.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="jarvis-chrome-extension.zip"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/browser/chrome/pair")
async def pair_chrome_extension(request: Request) -> Response:
    """Consume a local pairing code without accepting the app control key."""
    if not chrome_transport_allowed(request.scope):
        raise HTTPException(403, "Chrome pairing requires a direct local extension connection")
    raw = await request.body()
    if len(raw) > 2048:
        raise HTTPException(400, "Invalid pairing request")
    try:
        value = json.loads(raw)
        code, installation = value["code"], value["installation_id"]
        if not isinstance(code, str) or not isinstance(installation, str):
            raise ValueError("Invalid pairing request")
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "Invalid pairing request") from None
    rt = await _runtime(request)
    profile_id = await _mutation(rt.browser.live.profiles.pairing_profile, code)

    async def pair() -> dict:
        return await _mutation(rt.browser.live.profiles.pair, code, installation)

    result = await rt.browser.live.chrome.change_credentials(profile_id, pair)
    return Response(
        json.dumps(result), media_type="application/json", headers={"Cache-Control": "no-store"}
    )


class _BoundedSocket:
    def __init__(self, socket: WebSocket) -> None:
        self.socket = socket

    async def receive_json(self) -> Any:
        raw = await self.socket.receive_text()
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("Chrome message exceeds the size limit")
        return json.loads(raw)

    async def send_json(self, value: dict) -> None:
        await self.socket.send_json(value)

    async def close(self, code: int = 1000) -> None:
        await self.socket.close(code=code)


@router.websocket("/browser/chrome/connect")
async def connect_chrome_extension(websocket: WebSocket) -> None:
    if not chrome_transport_allowed(websocket.scope):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    try:
        async with asyncio.timeout(5):
            raw = await websocket.receive_text()
        if len(raw) > 2048:
            raise ValueError("Invalid Chrome handshake")
        value = json.loads(raw)
        fields = [value.get(key) for key in ("profile_id", "token", "installation_id")]
        if not all(isinstance(v, str) and len(v) <= 128 for v in fields):
            raise ValueError("Invalid Chrome handshake")
        rt = await _runtime(websocket)

        async def authorize() -> bool:
            return await asyncio.to_thread(rt.browser.live.profiles.authenticate, *fields)

        await rt.browser.live.chrome.attach(
            fields[0],
            _BoundedSocket(websocket),
            authorize=authorize,
        )
    except Exception:
        # Handshakes carry credentials: log no payload or exception text.
        log.debug("Chrome connection ended before authentication or during transport")
        try:
            await websocket.close(code=4401)
        except RuntimeError:
            pass  # Starlette already closed this disconnected socket.
