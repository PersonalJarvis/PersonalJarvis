"""Auth dependencies for the backend.

Three gates:

1. ``require_admin_token`` — for ``/identity/register``. Constant-time
   comparison against ``settings.admin_token``. Plus a rate limit (10/min/IP).
2. ``require_signed_request`` — for ``/sync``, ``/me``: checks that the
   pubkey is registered, verifies the signature, checks the replay window.
3. PII filter: implicit via the Pydantic schema ``extra='forbid'`` (Plan §C-Sec).

The signature-verify order is:
``schema-validate → pubkey-registered? → signature-valid? → ts within window?``

Schema and pubkey lookup are cheap; the crypto only runs after that.
"""
from __future__ import annotations

import hmac
import json
import logging
import time

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from .config import Settings
from .crypto import verify_with_recanonicalize
from .db import session_dep
from .models import Identity
from .rate_limit import RateLimiter

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Pull settings + rate limit from app.state
# ----------------------------------------------------------------------

def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session(request: Request):
    factory = request.app.state.session_factory
    return session_dep(factory)


def get_register_rate_limiter(request: Request) -> RateLimiter:
    """Lazy init per app instance."""
    rl = getattr(request.app.state, "register_rl", None)
    if rl is None:
        s: Settings = request.app.state.settings
        rl = RateLimiter(max_per_minute=s.register_rate_limit_per_minute)
        request.app.state.register_rl = rl
    return rl


# ----------------------------------------------------------------------
# Admin token gate
# ----------------------------------------------------------------------

def require_admin_token(
    request: Request,
    x_admin_token: str = Header(..., alias="X-Admin-Token"),
    settings: Settings = Depends(get_settings),
    rl: RateLimiter = Depends(get_register_rate_limiter),
) -> None:
    """Constant-time comparison + rate limit per client IP."""
    client_ip = (request.client.host if request.client else "unknown")
    if not rl.allow(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
        )
    expected = settings.admin_token
    if not expected or not hmac.compare_digest(x_admin_token, expected):
        # Consistent 401 — no side-channel information about token length.
        raise HTTPException(status_code=401, detail="invalid admin token")


# ----------------------------------------------------------------------
# Signed-request gate
# ----------------------------------------------------------------------

class SignedAuth:
    """Container that signed routes receive via ``Depends``.

    Provides the parsed data + the ``Identity`` from the DB. Routes
    then work on ``auth.identity`` and ``auth.payload``.
    """

    def __init__(self, *, identity: Identity, payload: dict, body_bytes: bytes) -> None:
        self.identity = identity
        self.payload = payload
        self.body_bytes = body_bytes


async def require_signed_request(
    request: Request,
    x_pubkey: str = Header(..., alias="X-Pubkey"),
    x_jarvis_sig: str = Header(..., alias="X-Jarvis-Sig"),
    settings: Settings = Depends(get_settings),
) -> SignedAuth:
    """Signature check + replay protection.

    Reads the raw body, parses JSON, checks that the pubkey is
    registered, verifies the signature, and compares ``payload.ts_ms``
    to the server's current time.
    """
    body_bytes = await request.body()
    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid json body: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be a JSON object")

    # 1. Pubkey must be registered
    factory = request.app.state.session_factory
    with factory() as session:
        ident = session.get(Identity, x_pubkey)
        if ident is None:
            log.info("rejected unsigned-request: unknown pubkey %s...", x_pubkey[:8])
            raise HTTPException(status_code=401, detail="pubkey not registered")
        # Detach so the dep-result can be used outside the session block
        session.expunge(ident)

    # 2. Signature verify
    if not verify_with_recanonicalize(
        pubkey_hex=x_pubkey,
        signature_hex=x_jarvis_sig,
        parsed_payload=payload,
    ):
        raise HTTPException(status_code=401, detail="invalid signature")

    # 3. Replay protection
    ts_ms = payload.get("ts_ms")
    if not isinstance(ts_ms, int):
        raise HTTPException(status_code=400, detail="payload.ts_ms missing")
    now_ms = int(time.time() * 1000)
    drift_ms = abs(now_ms - ts_ms)
    if drift_ms > settings.replay_window_seconds * 1000:
        raise HTTPException(
            status_code=401,
            detail=f"timestamp out of replay window ({drift_ms} ms drift)",
        )

    return SignedAuth(identity=ident, payload=payload, body_bytes=body_bytes)


# Re-export for the routes module
__all__ = [
    "SignedAuth",
    "get_settings",
    "get_session",
    "require_admin_token",
    "require_signed_request",
]


def get_db(request: Request) -> Session:
    """Convenience: session-per-call (not via a FastAPI yield-dep, since
    routes often only need it for a short transaction).
    """
    factory = request.app.state.session_factory
    return factory()


def get_owner_identity(session: Session) -> Identity:
    """Returns this backend's single ``Identity`` row.

    Phase-C-Decision-2: single-tenant. If there are zero or multiple
    rows, we raise 503 — the container is then misconfigured.
    """
    from sqlalchemy import select  # local import — avoids a cycle
    rows = session.execute(select(Identity)).scalars().all()
    if not rows:
        raise HTTPException(status_code=503, detail="no identity registered yet")
    if len(rows) > 1:
        raise HTTPException(status_code=503, detail="multi-identity backend unsupported")
    return rows[0]


# ----------------------------------------------------------------------
# Federation variant: signed but pubkey is NOT in the identity table
# (a friend's backend talking to us, NOT our own client)
# ----------------------------------------------------------------------

class FederationAuth:
    """Container for signed inbound federation requests.

    Unlike ``SignedAuth``, this variant does NOT look up the pubkey in
    the identity table — the caller is a friend's backend that has its
    own pubkey. We only verify the signature + replay window. Who the
    caller really is is decided by the endpoint based on the ``friends``
    table (e.g. "only friends may pull the feed").
    """

    def __init__(self, *, viewer_pubkey: str, payload: dict, body_bytes: bytes) -> None:
        self.viewer_pubkey = viewer_pubkey
        self.payload = payload
        self.body_bytes = body_bytes


async def require_federation_signed(
    request: Request,
    x_pubkey: str = Header(..., alias="X-Pubkey"),
    x_jarvis_sig: str = Header(..., alias="X-Jarvis-Sig"),
    settings: Settings = Depends(get_settings),
) -> FederationAuth:
    """Like ``require_signed_request``, but **without** an identity requirement.

    Used for ``/federation/feed``, ``/federation/reactions/inbound``,
    ``/federation/identity/{pubkey}`` DELETE — all calls from friend
    backends whose pubkey we never registered ourselves.
    """
    body_bytes = await request.body()
    try:
        payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid json body: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be a JSON object")

    # Check the pubkey format (otherwise sig-verify would die with a ValueError).
    if len(x_pubkey) != 64 or not all(c in "0123456789abcdef" for c in x_pubkey.lower()):
        raise HTTPException(status_code=401, detail="invalid pubkey format")

    if not verify_with_recanonicalize(
        pubkey_hex=x_pubkey,
        signature_hex=x_jarvis_sig,
        parsed_payload=payload,
    ):
        raise HTTPException(status_code=401, detail="invalid signature")

    ts_ms = payload.get("ts_ms")
    if not isinstance(ts_ms, int):
        raise HTTPException(status_code=400, detail="payload.ts_ms missing")
    now_ms = int(time.time() * 1000)
    drift_ms = abs(now_ms - ts_ms)
    if drift_ms > settings.replay_window_seconds * 1000:
        raise HTTPException(
            status_code=401,
            detail=f"timestamp out of replay window ({drift_ms} ms drift)",
        )

    return FederationAuth(viewer_pubkey=x_pubkey, payload=payload, body_bytes=body_bytes)
