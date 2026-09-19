"""Publisher-only OAuth broker. Never mounted in the desktop application.

Run behind HTTPS with one worker, access logging disabled, persistent encrypted
storage, and publisher credentials available only to this server process.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from jarvis.marketplace.auth.base import pkce_pair


@dataclass(frozen=True)
class BrokerProvider:
    authorization_url: str
    token_url: str
    client_id: str
    secret_key: str
    scopes: tuple[str, ...]
    basic_auth: bool = False
    pkce: bool = True
    resource: str | None = None
    bot_permissions: int | None = None


def https_base(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("A publisher HTTPS broker address is required")
    return value.rstrip("/")


def desktop_callback(value: str) -> str:
    """Only a local desktop can receive the second, browser-delivered proof."""
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        port = None
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or not port
        or parsed.path != "/oauth/broker"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(422, "A numeric loopback desktop callback is required")
    return value


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


class StartRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=80)
    challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    loopback_uri: str = Field(max_length=128)
    client_state: str = Field(pattern=r"^[A-Za-z0-9_-]{32,128}$")


class RedeemRequest(BaseModel):
    flow_id: str = Field(min_length=32, max_length=128)
    verifier: str = Field(pattern=r"^[A-Za-z0-9._~-]{43,128}$")
    handoff_code: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{43}$")


class GrantRequest(BaseModel):
    handle: str = Field(min_length=32, max_length=128)
    provider: str = Field(min_length=1, max_length=80)


def create_broker_app(
    *,
    base_url: str,
    database: Path,
    encryption_key: bytes,
    providers: dict[str, BrokerProvider],
    secret_reader,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Explicit server factory; credentials and deployment are never inferred."""
    base = https_base(base_url)
    for config in providers.values():
        https_base(config.authorization_url)
        https_base(config.token_url)
    cipher = Fernet(encryption_key)
    db = sqlite3.connect(database, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        "CREATE TABLE IF NOT EXISTS flows "
        "(id TEXT PRIMARY KEY, state TEXT UNIQUE, expires REAL, payload BLOB)"
    )
    db.execute("CREATE TABLE IF NOT EXISTS grants (id TEXT PRIMARY KEY, payload BLOB)")
    db.commit()
    lock = asyncio.Lock()
    refresh_locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            db.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def pack(value: dict) -> bytes:
        return cipher.encrypt(json.dumps(value).encode())

    def unpack(value: bytes) -> dict:
        return json.loads(cipher.decrypt(value))

    @app.middleware("http")
    async def private_responses(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    # Validation errors must not echo request input, which can be a grant handle.
    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({"detail": "Invalid broker request"}, status_code=422)

    def provider(name: str) -> BrokerProvider:
        config = providers.get(name)
        if config is None:
            raise HTTPException(409, "Publisher registration is not available")
        return config

    async def token_request(name: str, body: dict, *, bound_client: str | None = None) -> dict:
        config = provider(name)
        if bound_client is not None and bound_client != config.client_id:
            raise HTTPException(409, "Issuing client is unavailable; reconnect")
        secret = secret_reader(config.secret_key)
        if not secret:
            raise HTTPException(503, "Publisher registration is unavailable")
        body = {**body, "client_id": config.client_id}
        auth = None
        if config.basic_auth:
            auth = httpx.BasicAuth(config.client_id, secret)
        else:
            body["client_secret"] = secret
        if config.resource:
            body["resource"] = config.resource
        try:
            async with httpx.AsyncClient(
                timeout=20, follow_redirects=False, transport=transport
            ) as client:
                if auth is not None:
                    response = await client.post(config.token_url, data=body, auth=auth)
                else:
                    response = await client.post(config.token_url, data=body)
            if response.status_code != 200:
                # Only recognize a fixed revocation marker; never forward the body.
                try:
                    invalid = response.json().get("error") == "invalid_grant"
                except (ValueError, AttributeError):
                    invalid = False
                raise HTTPException(
                    401 if invalid else 502,
                    "Authorization expired" if invalid else "Provider request failed",
                )
            data = response.json()
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("access_token"), str)
                or not data["access_token"]
            ):
                raise HTTPException(502, "Provider returned no usable grant")
            ttl = max(0, int(data.get("expires_in", 3600)))
            return {
                "access": data["access_token"],
                "refresh": data.get("refresh_token"),
                "expires": time.time() + ttl,
                "client_id": config.client_id,
                "provider": name,
            }
        except (httpx.HTTPError, ValueError, TypeError):
            raise HTTPException(502, "Provider temporarily unavailable") from None

    def public_grant(grant: dict, handle: str) -> dict:
        return {
            "state": "connected",
            "access_token": grant["access"],
            "refresh_handle": handle,
            "expires_in": max(0, int(grant["expires"] - time.time())),
            "client_id": grant["client_id"],
        }

    @app.post("/start")
    async def start(body: StartRequest):
        config = provider(body.provider)
        loopback = desktop_callback(body.loopback_uri)
        if not secret_reader(config.secret_key):
            raise HTTPException(503, "Publisher registration is unavailable")
        fid, state = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()
        redirect = base + "/callback"
        params = {
            "response_type": "code",
            "client_id": config.client_id,
            "redirect_uri": redirect,
            "state": state,
            "scope": " ".join(config.scopes),
        }
        if not config.scopes:
            params.pop("scope")
        if config.pkce:
            params.update(code_challenge=challenge, code_challenge_method="S256")
        if config.resource:
            params["resource"] = config.resource
        if config.bot_permissions is not None:
            params["permissions"] = str(config.bot_permissions)
        flow = {
            "provider": body.provider,
            "client_id": config.client_id,
            "challenge": body.challenge,
            "verifier": verifier,
            "redirect": redirect,
            "status": "pending",
            "loopback": loopback,
            "client_state": body.client_state,
        }
        async with lock:
            db.execute("DELETE FROM flows WHERE expires < ?", (time.time(),))
            if db.execute("SELECT count(*) FROM flows").fetchone()[0] >= 1000:
                raise HTTPException(429, "Broker busy; retry later")
            db.execute(
                "INSERT INTO flows VALUES (?, ?, ?, ?)",
                (_digest(fid), _digest(state), time.time() + 300, pack(flow)),
            )
            db.commit()
        return {
            "flow_id": fid,
            "authorization_url": config.authorization_url + "?" + urlencode(params),
            "expires_in": 300,
        }

    @app.get("/callback", response_class=HTMLResponse)
    async def callback(state: str = "", code: str = "", error: str = ""):
        async with lock:
            row = db.execute(
                "SELECT id, payload FROM flows WHERE state=? AND expires>?",
                (_digest(state), time.time()),
            ).fetchone()
            if not row:
                raise HTTPException(400, "Unknown or expired authorization")
            flow = unpack(row[1])
            # Consume provider state BEFORE exchange, including denied callbacks.
            db.execute("UPDATE flows SET state=NULL WHERE id=?", (row[0],))
            db.commit()
        if error or not code:
            flow["status"] = "denied"
        else:
            config = provider(flow["provider"])
            body = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": flow["redirect"],
            }
            if config.pkce:
                body["code_verifier"] = flow["verifier"]
            try:
                flow["grant"] = await token_request(
                    flow["provider"], body, bound_client=flow["client_id"]
                )
                flow["status"] = "complete"
            except HTTPException:
                flow["status"] = "failed"
        handoff = secrets.token_urlsafe(32)
        flow["handoff_digest"] = _digest(handoff)
        async with lock:
            db.execute(
                "UPDATE flows SET payload=?, expires=MIN(expires, ?) WHERE id=?",
                (pack(flow), time.time() + 90, row[0]),
            )
            db.commit()
        # No credential travels through the browser: only a one-use code bound
        # to both this desktop's loopback listener and its original verifier.
        params = {"state": flow["client_state"]}
        if flow["status"] == "complete":
            params["code"] = handoff
        else:
            params["error"] = "access_denied" if flow["status"] == "denied" else "server_error"
        return RedirectResponse(flow["loopback"] + "?" + urlencode(params), status_code=302)

    @app.post("/redeem")
    async def redeem(body: RedeemRequest):
        async with lock:
            row = db.execute(
                "SELECT payload FROM flows WHERE id=? AND expires>?",
                (_digest(body.flow_id), time.time()),
            ).fetchone()
            if not row:
                raise HTTPException(410, "Authorization expired; reconnect")
            flow = unpack(row[0])
            if not secrets.compare_digest(flow["challenge"], _challenge(body.verifier)):
                raise HTTPException(403, "Invalid authorization proof")
            if flow["status"] == "pending":
                return {"state": "pending"}
            if flow["status"] == "complete" and (
                not body.handoff_code
                or not secrets.compare_digest(flow["handoff_digest"], _digest(body.handoff_code))
            ):
                raise HTTPException(403, "Desktop browser handoff proof is required")
            db.execute("DELETE FROM flows WHERE id=?", (_digest(body.flow_id),))
            if flow["status"] != "complete":
                db.commit()
                return {"state": "error", "error": flow["status"]}
            handle = secrets.token_urlsafe(48)
            db.execute("INSERT INTO grants VALUES (?, ?)", (_digest(handle), pack(flow["grant"])))
            db.commit()
            return public_grant(flow["grant"], handle)

    @app.post("/cancel")
    async def cancel(body: RedeemRequest):
        async with lock:
            row = db.execute(
                "SELECT payload FROM flows WHERE id=?", (_digest(body.flow_id),)
            ).fetchone()
            if row and secrets.compare_digest(
                unpack(row[0])["challenge"], _challenge(body.verifier)
            ):
                db.execute("DELETE FROM flows WHERE id=?", (_digest(body.flow_id),))
                db.commit()
        return {"state": "cancelled"}

    @app.post("/refresh")
    async def refresh(body: GrantRequest):
        identity = _digest(body.handle)
        async with lock:
            if not db.execute("SELECT 1 FROM grants WHERE id=?", (identity,)).fetchone():
                raise HTTPException(401, "Authorization expired")
            flight = refresh_locks.setdefault(identity, asyncio.Lock())
        # Serialize one grant, never all providers behind slow network I/O.
        async with flight:
            async with lock:
                row = db.execute("SELECT payload FROM grants WHERE id=?", (identity,)).fetchone()
                if not row:
                    raise HTTPException(401, "Authorization expired")
                grant = unpack(row[0])
            if grant["provider"] != body.provider:
                raise HTTPException(403, "Grant belongs to another provider")
            if grant["expires"] <= time.time() + 60:
                if not grant.get("refresh"):
                    raise HTTPException(401, "Authorization expired; reconnect")
                renewed = await token_request(
                    body.provider,
                    {"grant_type": "refresh_token", "refresh_token": grant["refresh"]},
                    bound_client=grant["client_id"],
                )
                renewed["refresh"] = renewed["refresh"] or grant["refresh"]
                grant = renewed
                async with lock:
                    # A disconnect during refresh must never resurrect a grant.
                    updated = db.execute(
                        "UPDATE grants SET payload=? WHERE id=?", (pack(grant), identity)
                    )
                    db.commit()
                    if not updated.rowcount:
                        raise HTTPException(401, "Authorization expired")
            return public_grant(grant, body.handle)

    @app.post("/disconnect")
    async def disconnect(body: GrantRequest):
        async with lock:
            row = db.execute(
                "SELECT payload FROM grants WHERE id=?", (_digest(body.handle),)
            ).fetchone()
            if row and unpack(row[0])["provider"] == body.provider:
                db.execute("DELETE FROM grants WHERE id=?", (_digest(body.handle),))
                db.commit()
                refresh_locks.pop(_digest(body.handle), None)
        return {"state": "disconnected"}

    return app


def main() -> None:
    """Run only on the publisher host behind its HTTPS reverse proxy."""
    import os

    import uvicorn

    from jarvis.core.config import get_secret
    from jarvis.marketplace.catalog import OAuthPkceLoopbackAuth
    from jarvis.marketplace.catalog_data import load_catalog
    from jarvis.marketplace.connect_helpers import is_placeholder_client_id

    base = os.environ.get("JARVIS_OAUTH_BROKER_BASE_URL", "")
    database = os.environ.get("JARVIS_OAUTH_BROKER_DATABASE", "")
    key = get_secret("oauth_broker_encryption_key", "OAUTH_BROKER_ENCRYPTION_KEY")
    if not key or not database:
        raise SystemExit("Publisher encryption key and persistent database path are required")
    providers = {}
    for spec in load_catalog().plugins:
        auth = spec.auth
        if not isinstance(auth, OAuthPkceLoopbackAuth) or auth.client_kind != "broker":
            continue
        family = spec.oauth_client_family or spec.id
        client = get_secret(
            f"publisher_{family}_oauth_client_id", f"PUBLISHER_{family.upper()}_OAUTH_CLIENT_ID"
        )
        if is_placeholder_client_id(client):
            continue
        scopes = tuple(auth.scopes)
        if spec.id == "discord":
            scopes += ("bot", "applications.commands")
        providers[spec.id] = BrokerProvider(
            authorization_url=auth.authorization_url,
            token_url=auth.token_url,
            client_id=client or "",
            secret_key=f"publisher_{family}_oauth_client_secret",
            scopes=scopes,
            basic_auth=auth.client_auth_method == "client_secret_basic",
            pkce=spec.id in {"asana", "figma"},
            resource=auth.resource,
            bot_permissions=68608 if spec.id == "discord" else None,
        )

    app = create_broker_app(
        base_url=base,
        database=Path(database),
        encryption_key=key.encode(),
        providers=providers,
        secret_reader=lambda name: get_secret(name, name.upper()),
    )
    # One process owns the SQLite transactions and refresh single-flight lock.
    # The reverse proxy must also omit query strings and authorization bodies.
    uvicorn.run(app, host="127.0.0.1", port=8799, workers=1, access_log=False)


if __name__ == "__main__":
    main()
