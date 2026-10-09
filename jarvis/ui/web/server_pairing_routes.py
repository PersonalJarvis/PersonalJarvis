"""One-time server pairing and authenticated access to independent Jarvis instances."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from jarvis.computers.pairing_errors import PairingError
from jarvis.ui.web.surface_security import append_session_cookie, is_secure_or_loopback

router = APIRouter(prefix="/api/computers/pairing", tags=["computer-pairing"])


def get_pairing() -> Any:
    """Keep credential storage and HTTP-client imports off the boot path."""
    from jarvis.computers.pairing import get_pairing as service

    return service()


class CodeBody(BaseModel):
    code: str = Field(min_length=1, max_length=128, repr=False)
    name: str = Field(default="Jarvis client", min_length=1, max_length=120)


class ConnectBody(BaseModel):
    host: str = Field(min_length=1, max_length=512)
    code: str = Field(min_length=1, max_length=128, repr=False)


class TicketBody(BaseModel):
    ticket: str = Field(min_length=43, max_length=43, repr=False)


def _secure(request: Request) -> None:
    if not is_secure_or_loopback(request.scope):
        raise HTTPException(403, "Server pairing requires HTTPS or a direct loopback connection.")


def _credential(request: Request) -> str:
    _secure(request)
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip() or len(value) > 128:
        raise HTTPException(401, "A paired server credential is required.")
    return value.strip()


def _failure(exc: PairingError) -> HTTPException:
    return HTTPException(exc.status, {"message": str(exc), "kind": "pairing"})


def _json(value: Any) -> JSONResponse:
    return JSONResponse(value, headers={"Cache-Control": "no-store"})


@router.post("/code", operation_id="computer_pairing_code")
def create_pairing_code(request: Request) -> JSONResponse:
    """Create a five-minute, single-use code granting access to this Jarvis instance."""
    _secure(request)
    try:
        return _json(get_pairing().issue_code())
    except PairingError as exc:
        raise _failure(exc) from exc


@router.post("/redeem", operation_id="computer_pairing_redeem")
def redeem_pairing_code(request: Request, body: CodeBody) -> JSONResponse:
    """Exchange a one-use code for a revocable server credential."""
    _secure(request)
    try:
        return _json(get_pairing().redeem(body.code, body.name).model_dump())
    except PairingError as exc:
        raise _failure(exc) from exc


@router.get("/status", operation_id="computer_pairing_status")
def paired_server_status(request: Request) -> JSONResponse:
    """Prove this server accepts the saved pairing credential without calling a model."""
    try:
        service = get_pairing()
        service.authenticate(_credential(request))
        return _json(service.identity().model_dump())
    except PairingError as exc:
        raise _failure(exc) from exc


@router.post("/launch", operation_id="computer_pairing_launch")
def create_server_ticket(request: Request) -> JSONResponse:
    """Create a single-use browser opening ticket, valid for one minute."""
    try:
        return _json({"ticket": get_pairing().issue_ticket(_credential(request))})
    except PairingError as exc:
        raise _failure(exc) from exc


@router.post("/disconnect", operation_id="computer_pairing_disconnect")
def disconnect_paired_client(request: Request) -> JSONResponse:
    """Revoke the presented client credential and its unredeemed opening links."""
    try:
        service = get_pairing()
        grant = service.authenticate(_credential(request))
        service.revoke(grant.id)
        return _json({"removed": True})
    except PairingError as exc:
        raise _failure(exc) from exc


@router.get("/clients", operation_id="computer_pairing_clients")
def list_paired_clients() -> JSONResponse:
    """List clients authorized to open this instance, without their credentials."""
    return _json({"clients": get_pairing().clients()})


@router.delete("/clients/{client_id}", operation_id="computer_pairing_revoke")
def revoke_paired_client(client_id: str) -> JSONResponse:
    """Revoke a client's future access and pending opening links."""
    get_pairing().revoke(client_id)
    return _json({"removed": True})


@router.get("/servers", operation_id="computer_servers_list")
def list_paired_servers() -> JSONResponse:
    """List saved independent Jarvis servers without their credentials."""
    return _json({"servers": [s.model_dump() for s in get_pairing().servers()]})


@router.post("/servers", operation_id="computer_servers_add")
async def connect_server(request: Request, body: ConnectBody) -> JSONResponse:
    """Pair with a server and save its validated connection in the local keyring."""
    _secure(request)
    try:
        return _json((await get_pairing().add(body.host, body.code)).model_dump())
    except PairingError as exc:
        raise _failure(exc) from exc


@router.post("/servers/{server_id}/check", operation_id="computer_servers_check")
async def check_server(server_id: str) -> JSONResponse:
    """Check one saved server with its own credential."""
    try:
        return _json((await get_pairing().check(server_id)).model_dump())
    except PairingError as exc:
        raise _failure(exc) from exc


@router.post("/servers/{server_id}/open", operation_id="computer_servers_open")
async def open_server(request: Request, server_id: str) -> JSONResponse:
    """Return a one-use link opening the server's authenticated Jarvis UI."""
    _secure(request)
    try:
        return _json({"url": await get_pairing().launch(server_id)})
    except PairingError as exc:
        raise _failure(exc) from exc


@router.delete("/servers/{server_id}", operation_id="computer_servers_remove")
async def remove_server(server_id: str) -> JSONResponse:
    """Revoke the remote credential, then remove the saved connection locally."""
    try:
        await get_pairing().remove(server_id)
        return _json({"removed": True})
    except PairingError as exc:
        raise _failure(exc) from exc


# The ticket travels only in the fragment, never in access logs or Referer.
# This self-contained page makes the final exchange at the server's own origin.
_ENTER_SCRIPT = """const ticket=location.hash.slice(1);
history.replaceState(null,'',location.pathname);
fetch(location.pathname,{
method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticket})
}).then(r=>{if(!r.ok)throw Error();location.replace('/');})
.catch(()=>{document.getElementById('state').textContent=
'This link expired or could not connect. Open the server again from Computers.';});"""


@router.get("/enter", operation_id="computer_pairing_enter_page", response_class=HTMLResponse)
def enter_server_page(request: Request) -> HTMLResponse:
    """Exchange a fragment-only ticket from the server's own browser origin."""
    import base64
    import hashlib

    _secure(request)
    script_hash = base64.b64encode(hashlib.sha256(_ENTER_SCRIPT.encode()).digest()).decode()
    return HTMLResponse(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="color-scheme" content="light dark">'
        '<title>Opening Jarvis</title><p id="state">Opening your Jarvis server…</p>'
        f"<script>{_ENTER_SCRIPT}</script></html>",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                f"default-src 'none'; script-src 'sha256-{script_hash}'; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
            ),
        },
    )


@router.post("/enter", operation_id="computer_pairing_enter")
def enter_server(request: Request, body: TicketBody) -> JSONResponse:
    """Consume an opening ticket and mint the normal HttpOnly UI session cookie."""
    from jarvis.ui.web.missions_auth import issue_token

    _secure(request)
    try:
        get_pairing().consume_ticket(body.ticket)
    except PairingError as exc:
        raise _failure(exc) from exc
    return append_session_cookie(
        _json({"connected": True}), issue_token(), request.url.scheme == "https"
    )
