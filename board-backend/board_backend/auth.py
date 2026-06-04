"""Auth-Dependencies fuer das Backend.

Drei Gates:

1. ``require_admin_token`` — fuer ``/identity/register``. Constant-time-Vergleich
   gegen ``settings.admin_token``. Plus Rate-Limit (10/min/IP).
2. ``require_signed_request`` — fuer ``/sync``, ``/me``: prueft Pubkey ist
   registriert, verifiziert Signatur, prueft Replay-Window.
3. PII-Filter: implizit via Pydantic-Schema ``extra='forbid'`` (Plan §C-Sec).

Die Sig-Verify-Reihenfolge ist:
``schema-validate → pubkey-registered? → signature-valid? → ts within window?``

Schema und Pubkey-Lookup sind billig; die Crypto erst danach.
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
# Settings + RateLimit aus app.state ziehen
# ----------------------------------------------------------------------

def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session(request: Request):
    factory = request.app.state.session_factory
    return session_dep(factory)


def get_register_rate_limiter(request: Request) -> RateLimiter:
    """Lazy-init pro App-Instance."""
    rl = getattr(request.app.state, "register_rl", None)
    if rl is None:
        s: Settings = request.app.state.settings
        rl = RateLimiter(max_per_minute=s.register_rate_limit_per_minute)
        request.app.state.register_rl = rl
    return rl


# ----------------------------------------------------------------------
# Admin-Token-Gate
# ----------------------------------------------------------------------

def require_admin_token(
    request: Request,
    x_admin_token: str = Header(..., alias="X-Admin-Token"),
    settings: Settings = Depends(get_settings),
    rl: RateLimiter = Depends(get_register_rate_limiter),
) -> None:
    """Constant-time-Vergleich + Rate-Limit pro Client-IP."""
    client_ip = (request.client.host if request.client else "unknown")
    if not rl.allow(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
        )
    expected = settings.admin_token
    if not expected or not hmac.compare_digest(x_admin_token, expected):
        # Konsistent 401 — keine Side-Channel-Information ueber Token-Laenge.
        raise HTTPException(status_code=401, detail="invalid admin token")


# ----------------------------------------------------------------------
# Signed-Request-Gate
# ----------------------------------------------------------------------

class SignedAuth:
    """Container, den signed Routes per ``Depends`` bekommen.

    Liefert die geparsten Daten + die ``Identity`` aus der DB. Routes
    arbeiten dann auf ``auth.identity`` und ``auth.payload``.
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
    """Signaturpruefung + Replay-Schutz.

    Liest den raw-body, parsed JSON, prueft Pubkey ist registriert,
    verifiziert Sig, und vergleicht ``payload.ts_ms`` mit Server-Now.
    """
    body_bytes = await request.body()
    try:
        payload = json.loads(body_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid json body: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be a JSON object")

    # 1. Pubkey muss registriert sein
    factory = request.app.state.session_factory
    with factory() as session:
        ident = session.get(Identity, x_pubkey)
        if ident is None:
            log.info("rejected unsigned-request: unknown pubkey %s...", x_pubkey[:8])
            raise HTTPException(status_code=401, detail="pubkey not registered")
        # Detach so the dep-result can be used outside the session block
        session.expunge(ident)

    # 2. Signatur-Verify
    if not verify_with_recanonicalize(
        pubkey_hex=x_pubkey,
        signature_hex=x_jarvis_sig,
        parsed_payload=payload,
    ):
        raise HTTPException(status_code=401, detail="invalid signature")

    # 3. Replay-Schutz
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


# Re-export fuer routes-Module
__all__ = [
    "SignedAuth",
    "get_settings",
    "get_session",
    "require_admin_token",
    "require_signed_request",
]


def get_db(request: Request) -> Session:
    """Convenience: session-per-call (nicht via FastAPI-yield-Dep, weil wir
    sie in den Routes oft nur fuer eine kurze Transaktion brauchen).
    """
    factory = request.app.state.session_factory
    return factory()


def get_owner_identity(session: Session) -> Identity:
    """Liefert die einzige ``Identity``-Row dieses Backends.

    Phase-C-Decision-2: Single-Tenant. Wenn keine oder mehrere Rows
    existieren, raisen wir 503 — der Container ist dann fehl-konfiguriert.
    """
    from sqlalchemy import select  # local import — vermeidet Zyklus
    rows = session.execute(select(Identity)).scalars().all()
    if not rows:
        raise HTTPException(status_code=503, detail="no identity registered yet")
    if len(rows) > 1:
        raise HTTPException(status_code=503, detail="multi-identity backend unsupported")
    return rows[0]


# ----------------------------------------------------------------------
# Federation-Variant: signed but pubkey is NOT in identity-Table
# (a friend's backend talking to us, NOT our own client)
# ----------------------------------------------------------------------

class FederationAuth:
    """Container fuer signed inbound Federation-Requests.

    Anders als ``SignedAuth`` lookuped diese Variant den Pubkey NICHT in
    der Identity-Tabelle — der Caller ist ein Friend-Backend, das einen
    eigenen Pubkey hat. Wir verifizieren nur Sig + Replay-Window. Wer
    der Caller wirklich ist, bestimmt der Endpoint anhand der ``friends``-
    Tabelle (z.B. „nur friends duerfen dem feed pullen").
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
    """Wie ``require_signed_request``, aber **ohne** Identity-Pflicht.

    Eingesetzt fuer ``/federation/feed``, ``/federation/reactions/inbound``,
    ``/federation/identity/{pubkey}`` DELETE — alles Calls von Friend-
    Backends, deren Pubkey wir gar nicht selbst registriert haben.
    """
    body_bytes = await request.body()
    try:
        payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid json body: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be a JSON object")

    # Pubkey-Format pruefen (Sig-Verify wuerde sonst mit ValueError sterben).
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
