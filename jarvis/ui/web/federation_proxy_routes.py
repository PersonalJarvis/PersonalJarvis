"""Lokaler Proxy zum Board-Federation-Backend (Phase D).

Hintergrund: Frontend laeuft im Browser, hat keinen Zugriff auf den
Privkey im Credential Manager. Stattdessen rufen Frontend-Komponenten
diese Routen, der lokale Jarvis-Server signiert die Calls und leitet
sie an das konfigurierte Backend weiter.

Sicherheits-Constraint: nur eine Whitelist von Backend-Pfaden ist
erreichbar. Beliebige Pfade durchschleusen wuerde das Federation-
Auth-Modell aushebeln.
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


# Whitelist der erlaubten Backend-Endpunkte. Frontend ruft den entsprechenden
# Proxy-Endpunkt, dieser sendet an das Backend.
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

# PATCH-Pfade haben einen Pfadparameter — wir matchen via Prefix.
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
# Status — was wissen wir ueber unseren Federation-State?
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
# Pair — admin-token-fluss
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
    pair_url: str             # Der vom Friend erhaltene URL inkl. ?token=...


@router.post("/pair/accept-from-friend")
async def pair_accept_from_friend(
    request: Request, payload: PairAcceptProxyRequest,
) -> dict[str, Any]:
    """Wir sind der Friend — wir senden unseren Pubkey + Display-Name zum
    Owner-Backend des Senders.

    Aus ``pair_url`` extrahieren wir die Owner-Base-URL und den Token.
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

        # Bidirektional: wir registrieren A jetzt als unseren Friend.
        # Das passiert via UNSEREM Backend's pair/initiate → A schickt accept
        # zurueck — too komplex. Pragma: wir tragen A direkt in unseren
        # eigenen Backend ein via einer admin-only "manual add"-Route, die
        # wir hier hinzufuegen. (Zukunft: A koennte automatisch zurueck
        # accepten in einem zwei-phasen-handshake.)
        admin = _resolve_admin_token(kr)
        if admin:
            await c.post(
                f"{own_backend}/api/v1/pair/initiate",
                json={}, headers={"X-Admin-Token": admin},
            )
            # Initiate-Token wird verbraucht beim Friend, der diese Aktion
            # spiegelt. Fuer MVP: wir return die accept-resp und der User
            # initiiert ggf. selbst den zweiten Pair-Schritt.
        return accept_resp


# ----------------------------------------------------------------------
# Generic GET-Proxy
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
# Disconnect — Local-Only-Mode
# ----------------------------------------------------------------------

@router.post("/disconnect")
def disconnect(request: Request) -> dict[str, str]:
    """Setzt board.federation.enabled=false zur Laufzeit (in-memory).

    Fuer eine permanente Aenderung muss der User die jarvis.toml editieren —
    diese Route ist eine ``Local-Only-Mode``-Schaltflaeche fuer schnelle
    Diagnose-Sessions. Plan §0: User muss disconnecten koennen ohne dass
    Layer A/B kaputtgehen.
    """
    cfg = request.app.state.config
    cfg.board.federation.enabled = False
    return {"status": "disconnected", "note": "in-memory only — edit jarvis.toml for persistence"}
