"""Local proxy to the board federation backend (Phase D).

Background: the frontend runs in the browser and has no access to the
privkey in the credential manager. Instead, frontend components call
these routes; the local Jarvis server signs the calls and forwards
them to the configured backend.

Security constraint: only a whitelist of backend paths is reachable.
Passing arbitrary paths through would undermine the federation
auth model.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from jarvis.board.aggregator import BoardAggregator  # noqa: F401 — keep symbol
from jarvis.board.sync import (
    _KeyringBackend,
    _load_or_create_privkey,
    _resolve_admin_token,
)
try:
    from board_backend.crypto import canonical_json, sign
except ModuleNotFoundError:
    # `board_backend` is a SEPARATE, optional package (the Board-federation
    # backend). The base app MUST import this module without it (cloud-first:
    # a fresh `pip install .` has no board_backend), so the whole server still
    # boots. The federation routes then return a clear 503 when actually called,
    # instead of crashing the server at import time.
    def _federation_unavailable(*_a: Any, **_k: Any) -> Any:  # type: ignore[misc]
        raise HTTPException(
            status_code=503,
            detail="Board federation is unavailable — the optional 'board_backend' package is not installed.",
        )

    canonical_json = _federation_unavailable  # type: ignore[assignment]
    sign = _federation_unavailable  # type: ignore[assignment]

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/board/federation", tags=["board-federation"])


# Whitelist of allowed backend endpoints. The frontend calls the matching
# proxy endpoint, which sends the request on to the backend.
ALLOWED_GET_PATHS = frozenset({
    "/api/v1/me",
    "/api/v1/friends",
    "/api/v1/activities",
    "/api/v1/federation/feed",
})
ALLOWED_POST_PATHS = frozenset({
    "/api/v1/activities",
    "/api/v1/reactions",
    "/api/v1/stories",
})

# PATCH paths have a path parameter — we match via prefix.
ALLOWED_PATCH_PREFIXES = ("/api/v1/friends/",)


def _backend_url(request: Request) -> str:
    cfg = request.app.state.config
    url = cfg.board.federation.backend_url
    if not url:
        raise HTTPException(status_code=503, detail="board.federation.backend_url not configured")
    return url.rstrip("/")


def _signed_headers(privkey_hex: str, pubkey_hex: str, payload: dict) -> dict[str, str]:
    sig = sign(payload, privkey_hex=privkey_hex)
    return {
        "Content-Type": "application/json",
        "X-Pubkey": pubkey_hex,
        "X-Jarvis-Sig": sig,
    }


# ----------------------------------------------------------------------
# Status — what do we know about our federation state?
# ----------------------------------------------------------------------

class FederationStatusResponse(BaseModel):
    enabled: bool
    backend_url: str
    pubkey: str | None


@router.get("/status", response_model=FederationStatusResponse)
def status(request: Request) -> FederationStatusResponse:
    cfg = request.app.state.config
    fed = cfg.board.federation
    pubkey: str | None = None
    if fed.enabled:
        try:
            kr = _KeyringBackend()
            _, pubkey = _load_or_create_privkey(kr)
        except Exception:  # noqa: BLE001
            log.exception("could not derive pubkey for status")
    return FederationStatusResponse(
        enabled=fed.enabled,
        backend_url=fed.backend_url,
        pubkey=pubkey,
    )


# ----------------------------------------------------------------------
# Pair — admin-token flow
# ----------------------------------------------------------------------

class PairInitiateProxyResponse(BaseModel):
    token: str
    url: str
    expires_at: str


@router.post("/pair/initiate", response_model=PairInitiateProxyResponse)
async def pair_initiate(request: Request) -> PairInitiateProxyResponse:
    backend = _backend_url(request)
    kr = _KeyringBackend()
    admin = _resolve_admin_token(kr)
    if not admin:
        raise HTTPException(status_code=503, detail="admin token not in keyring")
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.post(
            f"{backend}/api/v1/pair/initiate",
            json={}, headers={"X-Admin-Token": admin},
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    body = resp.json()
    return PairInitiateProxyResponse(
        token=body["token"],
        url=body["url"],
        expires_at=body["expires_at"],
    )


class PairAcceptProxyRequest(BaseModel):
    pair_url: str             # the URL received from the friend, incl. ?token=...


@router.post("/pair/accept-from-friend")
async def pair_accept_from_friend(
    request: Request, payload: PairAcceptProxyRequest,
) -> dict[str, Any]:
    """We are the friend — we send our pubkey + display name to the
    sender's owner backend.

    We extract the owner base URL and the token from ``pair_url``.
    """
    from urllib.parse import urlparse, parse_qs

    parsed = urlparse(payload.pair_url)
    if not parsed.scheme or not parsed.netloc:
        raise HTTPException(status_code=400, detail="invalid pair url")
    qs = parse_qs(parsed.query)
    token_list = qs.get("token") or []
    if not token_list:
        raise HTTPException(status_code=400, detail="pair url has no token")
    token = token_list[0]
    target_owner_url = f"{parsed.scheme}://{parsed.netloc}"

    kr = _KeyringBackend()
    privkey, pubkey = _load_or_create_privkey(kr)
    cfg = request.app.state.config
    display = cfg.board.federation.display_name or "Jarvis-User"

    own_backend = _backend_url(request)
    body = {
        "token": token,
        "friend_pubkey": pubkey,
        "friend_url": own_backend,
        "friend_display_name": display,
    }
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.post(f"{target_owner_url}/api/v1/pair/accept", json=body)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        accept_resp = resp.json()

        # Bidirectional: we now register A as our friend too.
        # That would happen via OUR backend's pair/initiate → A sends an
        # accept back — too complex. Pragma: we add A directly into our
        # own backend via an admin-only "manual add" route that we add
        # here. (Future: A could automatically accept back in a
        # two-phase handshake.)
        admin = _resolve_admin_token(kr)
        if admin:
            await c.post(
                f"{own_backend}/api/v1/pair/initiate",
                json={}, headers={"X-Admin-Token": admin},
            )
            # The initiate token is consumed by the friend, who mirrors this
            # action. For MVP: we return the accept response and the user
            # initiates the second pair step themselves if needed.
        return accept_resp


# ----------------------------------------------------------------------
# Generic GET proxy
# ----------------------------------------------------------------------

@router.get("/get")
async def proxy_get(request: Request,
                    path: str = Query(...),
                    sort: str | None = Query(None)) -> Any:
    if path not in ALLOWED_GET_PATHS:
        raise HTTPException(status_code=400, detail=f"path not allowed: {path}")
    backend = _backend_url(request)
    kr = _KeyringBackend()
    privkey, pubkey = _load_or_create_privkey(kr)
    payload = {"ts_ms": int(time.time() * 1000)}
    body = canonical_json(payload)
    headers = _signed_headers(privkey, pubkey, payload)
    async with httpx.AsyncClient(timeout=10.0) as c:
        params = {"sort": sort} if sort else {}
        resp = await c.request(
            "GET", f"{backend}{path}", content=body, params=params, headers=headers,
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


class ProxyPostRequest(BaseModel):
    path: str
    body: dict[str, Any]


@router.post("/post")
async def proxy_post(request: Request, payload: ProxyPostRequest) -> Any:
    if payload.path not in ALLOWED_POST_PATHS:
        raise HTTPException(status_code=400, detail=f"path not allowed: {payload.path}")
    backend = _backend_url(request)
    kr = _KeyringBackend()
    privkey, pubkey = _load_or_create_privkey(kr)
    full_body = {**payload.body, "ts_ms": int(time.time() * 1000)}
    body_bytes = canonical_json(full_body)
    headers = _signed_headers(privkey, pubkey, full_body)
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.post(f"{backend}{payload.path}", content=body_bytes, headers=headers)
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


class ProxyPatchRequest(BaseModel):
    path: str
    body: dict[str, Any]


@router.patch("/patch")
async def proxy_patch(request: Request, payload: ProxyPatchRequest) -> Any:
    if not any(payload.path.startswith(p) for p in ALLOWED_PATCH_PREFIXES):
        raise HTTPException(status_code=400, detail=f"path not allowed: {payload.path}")
    backend = _backend_url(request)
    kr = _KeyringBackend()
    privkey, pubkey = _load_or_create_privkey(kr)
    full_body = {**payload.body, "ts_ms": int(time.time() * 1000)}
    body_bytes = canonical_json(full_body)
    headers = _signed_headers(privkey, pubkey, full_body)
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.request(
            "PATCH", f"{backend}{payload.path}",
            content=body_bytes, headers=headers,
        )
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


# ----------------------------------------------------------------------
# Disconnect — local-only mode
# ----------------------------------------------------------------------

@router.post("/disconnect")
def disconnect(request: Request) -> dict[str, str]:
    """Sets board.federation.enabled=false at runtime (in-memory).

    For a permanent change, the user has to edit jarvis.toml — this route
    is a ``local-only-mode`` button for quick diagnostic sessions. Plan §0:
    the user must be able to disconnect without breaking layers A/B.
    """
    cfg = request.app.state.config
    cfg.board.federation.enabled = False
    return {"status": "disconnected", "note": "in-memory only — edit jarvis.toml for persistence"}
