"""Process-local tokens for authenticated UI and mission WebSocket sessions.

Tokens are minted only behind the global Host/Origin/credential boundary, live
in memory, and disappear on restart. Mission sockets keep their narrower
hello-frame check as defense in depth; the main UI session itself is carried in
an HttpOnly cookie managed by :mod:`jarvis.ui.web.surface_security`.
"""
from __future__ import annotations

import secrets
from typing import Final

from fastapi import APIRouter
from fastapi.responses import JSONResponse

# Module-global token store. A set for O(1) lookup. Reset on
# restart — sufficient for a process-scoped browser session.
_TOKENS: set[str] = set()
_TOKEN_BYTES: Final[int] = 32  # 256 bits of entropy


def issue_token() -> str:
    """Generates a URL-safe token and stores it as valid."""
    tok = secrets.token_urlsafe(_TOKEN_BYTES)
    _TOKENS.add(tok)
    return tok


def validate_token(tok: str) -> bool:
    """True if the token is in the store (no drift, no expiration)."""
    if not tok:
        return False
    return tok in _TOKENS


def register_token(tok: str) -> None:
    """Register an externally-minted token as valid. Idempotent.

    The fast-boot boundary can mint an authenticated HttpOnly session before the
    full app exists. Registering that issued session preserves it at handoff.
    """
    if tok:
        _TOKENS.add(tok)


def revoke_token(tok: str) -> None:
    """Best-effort removal of the token. Idempotent."""
    _TOKENS.discard(tok)


def reset_tokens() -> None:
    """Test hook: clears the store entirely."""
    _TOKENS.clear()


router = APIRouter(prefix="/api/missions/auth", tags=["missions-auth"])


@router.get("/token")
async def get_token() -> JSONResponse:
    """Return a fresh in-memory mission token without allowing caching."""
    return JSONResponse(
        {"token": issue_token()},
        headers={"Cache-Control": "no-store"},
    )
