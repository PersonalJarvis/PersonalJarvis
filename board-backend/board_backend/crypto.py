"""Ed25519-Crypto + Canonical-JSON.

Pattern:
- Client kanonisiert ``payload_dict`` zu Bytes via ``canonical_json``.
- Client signiert die Bytes mit seinem Privkey, sendet ``X-Pubkey`` (hex)
  + ``X-Jarvis-Sig`` (hex) als Header und das **rohe** JSON als Body.
- Server liest den raw-Body, kanonisiert nochmal (Schutz gegen
  Whitespace-Differenzen) und prueft die Signatur.

Die Re-Kanonisierung auf Server-Seite ist wichtig: HTTP-Frameworks
duerfen den raw-body nicht modifizieren, aber Reverse-Proxies (Caddy,
nginx) tun's manchmal an Whitespace. Mit ``canonical_json`` auf beiden
Seiten ist die Signatur unter Whitespace-Veraenderungen stabil.
"""
from __future__ import annotations

import json
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey


def canonical_json(payload: Any) -> bytes:
    """Deterministische, signatur-freundliche JSON-Serialisierung.

    - sortierte Keys auf jeder Ebene
    - keine ueberfluessigen Whitespaces
    - UTF-8, kein ASCII-Escape (Sonderzeichen bleiben als UTF-8)
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def generate_keypair() -> tuple[str, str]:
    """Erzeugt ein neues Ed25519-Paar.

    Returns
    -------
    (privkey_hex, pubkey_hex)
        Beide 64 Zeichen hex (32 Bytes raw).
    """
    sk = SigningKey.generate()
    return sk.encode().hex(), sk.verify_key.encode().hex()


def sign(payload: Any, *, privkey_hex: str) -> str:
    """Signiert ``payload`` (dict oder bereits-encoded bytes).

    Returns
    -------
    str
        128-Zeichen Hex (64 Bytes raw signature).
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
    """Prueft ``body`` gegen ``signature_hex`` mit ``pubkey_hex``.

    ``body`` ist der raw HTTP-Body (Bytes). Caller koennen vor dem Aufruf
    optional via ``re_canonicalize=True`` re-kanonisieren — das machen
    wir hier nicht automatisch, weil ``body`` schon canonical sein sollte.
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
    """Verify-Variante, die den geparsten Payload re-kanonisiert.

    Sicherer Default fuer den HTTP-Pfad: wir muessen den Body sowieso
    parsen, um Schema-Validation und Replay-Check zu machen — und der
    Re-Canonicalize-Schritt isoliert uns gegen Body-Manipulation durch
    Reverse-Proxies (LF/CRLF, trailing whitespace etc.).

    WICHTIG: Eine Tampering-Attacke, die nach dem Signieren ein Feld
    aendert, wird trotzdem erkannt — der re-kanonisierte Payload hat
    dann andere Bytes als beim Signieren, und die Signatur stimmt nicht
    mehr.
    """
    body = canonical_json(parsed_payload)
    return verify(pubkey_hex=pubkey_hex, signature_hex=signature_hex, body=body)
