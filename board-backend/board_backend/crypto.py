"""Ed25519 crypto + canonical JSON.

Pattern:
- The client canonicalizes ``payload_dict`` to bytes via ``canonical_json``.
- The client signs the bytes with its privkey, sends ``X-Pubkey`` (hex)
  + ``X-Jarvis-Sig`` (hex) as headers and the **raw** JSON as the body.
- The server reads the raw body, re-canonicalizes it (protection against
  whitespace differences), and checks the signature.

Re-canonicalizing on the server side matters: HTTP frameworks aren't
supposed to modify the raw body, but reverse proxies (Caddy, nginx)
sometimes touch whitespace. With ``canonical_json`` on both sides, the
signature stays stable across whitespace changes.
"""
from __future__ import annotations

import json
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey


def canonical_json(payload: Any) -> bytes:
    """Deterministic, signature-friendly JSON serialization.

    - sorted keys at every level
    - no superfluous whitespace
    - UTF-8, no ASCII escaping (special characters stay as UTF-8)
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


#: Payload field that binds a signature to ONE endpoint: ``"<METHOD> <path>"``.
#: The signature covers only the body, so without it a signed body is valid
#: on every route whose schema accepts it — and the owner's reactions are
#: forwarded to friends verbatim, so a friend could replay one at the owner's
#: own backend (``GET /api/v1/activities``) and read the private feed.
AUDIENCE_FIELD = "aud"

# A reaction is signed once by the owner's client, sent to the owner's
# backend, and delivered unchanged to the friend's inbound route. Its audience
# names that final hop, so both backends accept it and nothing else does.
_AUDIENCE_ALIASES = {
    "POST /api/v1/reactions": "POST /api/v1/federation/reactions/inbound",
}


def signed_audience(method: str, path: str) -> str:
    """The ``aud`` value a signed request to ``method path`` must carry.

    ``path`` is the route path without query string or deployment prefix.
    """
    key = f"{method.upper()} {path}"
    return _AUDIENCE_ALIASES.get(key, key)


def generate_keypair() -> tuple[str, str]:
    """Generates a new Ed25519 keypair.

    Returns
    -------
    (privkey_hex, pubkey_hex)
        Both 64 hex characters (32 raw bytes).
    """
    sk = SigningKey.generate()
    return sk.encode().hex(), sk.verify_key.encode().hex()


def sign(payload: Any, *, privkey_hex: str) -> str:
    """Signs ``payload`` (dict or already-encoded bytes).

    Returns
    -------
    str
        128 hex characters (64 raw signature bytes).
    """
    sk = SigningKey(bytes.fromhex(privkey_hex))
    body = payload if isinstance(payload, (bytes, bytearray)) else canonical_json(payload)
    sig = sk.sign(body).signature
    return sig.hex()


def verify(
    *,
    pubkey_hex: str,
    signature_hex: str,
    body: bytes,
) -> bool:
    """Checks ``body`` against ``signature_hex`` using ``pubkey_hex``.

    ``body`` is the raw HTTP body (bytes). Callers can optionally
    re-canonicalize before calling this via ``re_canonicalize=True`` —
    we don't do that automatically here, since ``body`` should already
    be canonical.
    """
    try:
        vk = VerifyKey(bytes.fromhex(pubkey_hex))
        vk.verify(body, bytes.fromhex(signature_hex))
        return True
    except (BadSignatureError, ValueError):
        return False


def verify_with_recanonicalize(
    *,
    pubkey_hex: str,
    signature_hex: str,
    parsed_payload: Any,
) -> bool:
    """Verify variant that re-canonicalizes the parsed payload.

    The safe default for the HTTP path: we have to parse the body
    anyway to do schema validation and the replay check — and the
    re-canonicalize step isolates us from body manipulation by
    reverse proxies (LF/CRLF, trailing whitespace, etc.).

    IMPORTANT: a tampering attack that changes a field after signing is
    still detected — the re-canonicalized payload then has different
    bytes than at signing time, and the signature no longer matches.
    """
    body = canonical_json(parsed_payload)
    return verify(pubkey_hex=pubkey_hex, signature_hex=signature_hex, body=body)
