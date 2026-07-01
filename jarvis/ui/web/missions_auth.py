"""Short-lived in-memory token store for mission WebSocket auth.

Localhost-only setup. No secret management needed (single-user browser):
- **No** token is pre-generated at server start; every browser fetches
  its own via ``GET /api/missions/auth/token``.
- Tokens live in module memory and are discarded on restart.
- ``validate_token()`` is an O(1) set lookup.

Deliberately no JWT library, no cookies, no sessions — the WS handshake
separates auth (hello frame) from frame validation, which is enough for the
single-user-localhost threat model.
"""
from __future__ import annotations

import os
import secrets
from typing import Final

from fastapi import APIRouter

# Module-global token store. A set for O(1) lookup. Reset on
# restart — sufficient for the localhost single-user use case.
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

    The fast-boot desktop path mints a RAW ``secrets.token_urlsafe`` and injects
    it into ``window.__JARVIS_TOKEN`` without ever issuing it through
    ``GET /token`` — so without this it fails ``validate_token`` (close 4401) on
    every token-gated WebSocket. That is exactly what hung the "Make It Yours"
    workspace PTY terminals forever on "connecting".
    """
    if tok:
        _TOKENS.add(tok)


def register_session_token_from_env(env_var: str) -> str | None:
    """Read the desktop session token from ``env_var`` and register it as valid.

    Called once at server build with ``cfg.ui.auth_token_env`` so the injected
    ``window.__JARVIS_TOKEN`` passes ``validate_token``. Returns the token when
    present, else ``None`` (headless / browser-only boots inject no token and
    fetch a fresh one via ``GET /token`` instead).
    """
    tok = os.environ.get(env_var)
    if tok:
        _TOKENS.add(tok)
        return tok
    return None


def revoke_token(tok: str) -> None:
    """Best-effort removal of the token. Idempotent."""
    _TOKENS.discard(tok)


def reset_tokens() -> None:
    """Test hook: clears the store entirely."""
    _TOKENS.clear()


router = APIRouter(prefix="/api/missions/auth", tags=["missions-auth"])


@router.get("/token")
async def get_token() -> dict[str, str]:
    """Returns a fresh mission token. The browser holds it in memory."""
    return {"token": issue_token()}
