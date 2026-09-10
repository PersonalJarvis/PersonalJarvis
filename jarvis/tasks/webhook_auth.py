"""Per-routine credentials in the portable secret store, never in TaskSpec."""

import hashlib
import hmac
import re
import secrets
import threading
from typing import Any

from jarvis.core.config import get_secret, set_secret

_KEY_LOCK = threading.Lock()


def _slot(row: dict[str, Any]) -> str:
    return f"routine_webhook_{row['id']}_{row['created_at_ns']}"


def connection_token(row: dict[str, Any], *, rotate: bool = False) -> str:
    with _KEY_LOCK:
        name = _slot(row)
        token = None if rotate else get_secret(name)
        if token:
            return token
        token = secrets.token_urlsafe(32)
        if not set_secret(name, token):
            raise RuntimeError("Webhook credential could not be saved; try again in the app")
        if get_secret(name) != token:
            raise RuntimeError("Webhook credential could not be verified")
        return token


def verify_token(row: dict[str, Any], token: str) -> bool:
    expected = get_secret(_slot(row))
    return bool(expected and token and token.isascii() and secrets.compare_digest(expected, token))


def verify_signature(row: dict[str, Any], body: bytes, signature: str) -> bool:
    """Verify GitHub-compatible HMAC-SHA256 over the exact received bytes."""
    if not re.fullmatch(r"sha256=[a-fA-F0-9]{64}", signature):
        return False
    key = get_secret(_slot(row))
    if not key:
        return False
    digest = hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest("sha256=" + digest, signature.lower())
