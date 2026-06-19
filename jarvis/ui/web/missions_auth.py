"""Kurzlebiger In-Memory-Token-Store fuer Mission-WebSocket-Auth.

Localhost-only Setup. Kein Secret-Management noetig (Single-User-Browser):
- Beim Server-Start wird **kein** Token vorgeneriert; jeder Browser holt
  sich seinen eigenen via ``GET /api/missions/auth/token``.
- Tokens leben im Modul-Speicher und werden beim Restart verworfen.
- ``validate_token()`` ist O(1) Set-Lookup.

Bewusst keine JWT-Lib, keine Cookies, keine Sessions — der WS-Handshake
trennt Auth (hello-Frame) von Frame-Validation, das reicht fuer den
Single-User-Localhost-Threat-Model.
"""
from __future__ import annotations

import secrets
from typing import Final

from fastapi import APIRouter

# Modul-globaler Token-Store. Set fuer O(1) Lookup. Wird durch Restart
# resetted — fuer den Localhost-Single-User-Use-Case ausreichend.
_TOKENS: set[str] = set()
_TOKEN_BYTES: Final[int] = 32  # 256 bit Entropie


def issue_token() -> str:
    """Generiert einen URL-safe Token und speichert ihn als gueltig."""
    tok = secrets.token_urlsafe(_TOKEN_BYTES)
    _TOKENS.add(tok)
    return tok


def validate_token(tok: str) -> bool:
    """True wenn der Token im Store ist (kein Drift, keine Expiration)."""
    if not tok:
        return False
    return tok in _TOKENS


def revoke_token(tok: str) -> None:
    """Best-effort entfernt den Token. Idempotent."""
    _TOKENS.discard(tok)


def reset_tokens() -> None:
    """Test-Hook: leert den Store komplett."""
    _TOKENS.clear()


router = APIRouter(prefix="/api/missions/auth", tags=["missions-auth"])


@router.get("/token")
async def get_token() -> dict[str, str]:
    """Liefert einen frischen Mission-Token. Browser haelt ihn im Memory."""
    return {"token": issue_token()}
