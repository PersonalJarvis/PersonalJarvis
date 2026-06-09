"""Authentication for the Jarvis Control API (``/api/control/*``).

A per-user Bearer key (see ``jarvis.core.control_key``) is the security boundary
for the Control API — NOT the localhost binding. This keeps the existing
same-origin desktop UI routes (``/api/settings/*`` etc.) untouched (zero
regression) while the new control surface, which external local agents (Codex
CLI, Claude Code) drive, is key-gated.

- ``require_control_key`` — every control route. Bearer required, constant-time
  compared. Never logs the presented or stored key.
- ``require_control_key_or_loopback`` — the key-reveal / rotate endpoints. A
  same-host (loopback) request is allowed so the desktop Settings panel can
  fetch/rotate the key before the user possesses it; a remote caller still needs
  the Bearer. A loopback process can already read the keyring/0600 file, so this
  is not a new exposure.
- ``assert_bind_safe`` — fail-closed boot check (cloud-first): never expose a
  non-loopback bind without a key.
"""
from __future__ import annotations

from fastapi import HTTPException, Request, status

from jarvis.core import control_key as ck

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_UNAUTHORIZED = {
    "status_code": status.HTTP_401_UNAUTHORIZED,
    "detail": "Invalid or missing Jarvis Control API key.",
    "headers": {"WWW-Authenticate": "Bearer"},
}


def _bearer_token(request: Request) -> str | None:
    """Extract a non-empty Bearer token, or ``None``. Never logs it."""
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return None


def _is_loopback(request: Request) -> bool:
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    return host in _LOOPBACK_HOSTS


async def require_control_key(request: Request) -> None:
    """FastAPI dependency: require a valid Bearer control key (401 otherwise).

    Loopback does NOT bypass this — a local agent on desktop must present the
    key, otherwise the key would be meaningless for the very callers it exists
    to gate.
    """
    if not ck.verify_control_key(_bearer_token(request)):
        raise HTTPException(**_UNAUTHORIZED)


async def require_control_key_or_loopback(request: Request) -> None:
    """Looser dependency for key-reveal / rotate: allow a same-host request OR a
    valid Bearer. Lets the desktop Settings panel bootstrap the key; remote
    callers still need it.
    """
    if _is_loopback(request) or ck.verify_control_key(_bearer_token(request)):
        return
    raise HTTPException(**_UNAUTHORIZED)


def assert_bind_safe(host: str, key: str | None) -> None:
    """Fail-closed: refuse a non-loopback bind without a control key.

    On a VPS the key is the only boundary — binding ``0.0.0.0`` with no key
    would expose an unauthenticated control surface. Desktop (127.0.0.1) needs
    no key to bind. Called at the bind site before uvicorn starts.
    """
    if host and host not in _LOOPBACK_HOSTS and not key:
        raise RuntimeError(
            "Refusing to bind the Jarvis Control API to a non-loopback address "
            f"({host!r}) without a control key. The API key is the security "
            "boundary on a VPS — generate one first (control_key.ensure_control_key)."
        )
