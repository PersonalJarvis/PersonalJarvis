"""SocialIdentity — this instance's key pair, its invite codes, its envelopes.

Why a key pair and not an account
---------------------------------
Two people running Personal Jarvis have no shared server to be accounts on.
Anything that identifies them therefore has to be something they carry
themselves. An Ed25519 key pair does exactly that: the public half *is* the
identity (``node_id``), the private half proves it, and nobody has to be asked
for permission to create one.

That choice decides three other things:

- **Invites are self-certifying.** An invite code carries the issuer's public
  key and a signature over its own contents. Verifying it needs no lookup and
  no trusted third party — if the signature checks out, the code was written by
  whoever holds that key. A tampered code fails locally, before it reaches the
  friend registry.
- **Requests authenticate themselves.** Every peer request travels as a signed
  envelope. The receiver checks the signature against the node id it already
  has on file for that friend; an unknown or mismatched key is a 403 before any
  handler runs.
- **Losing the key loses the identity.** So it is stored through the same
  keyring → ENV → file path as every other credential
  (:func:`jarvis.core.config.get_secret`), which is the path a user can recover
  from in-app on any OS, including a headless server with no keyring.

Crypto comes from ``cryptography``, which is a base dependency on every
platform. The optional ``board_backend``/PyNaCl stack is deliberately not used
here: Socials has to work on a plain ``pip install .``.
"""

from __future__ import annotations

import base64
import json
import logging
import secrets as _secrets
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

#: Credential slot for the private key (hex). ENV fallback: ``SOCIALS_NODE_PRIVKEY``.
PRIVKEY_SECRET_KEY = "socials_node_privkey"  # noqa: S105 — a slot name, not a key

#: Invite codes carry this prefix so a user can recognise one in a chat window
#: and so a paste of the wrong string fails with a clear message.
INVITE_PREFIX = "PJS1"

#: How long an invite stays valid. Long enough to send by mail, short enough
#: that an old code in a chat log is not a standing key to your friends list.
INVITE_TTL_NS = 7 * 24 * 60 * 60 * 1_000_000_000

#: Signed envelopes older or newer than this are rejected. Replay protection
#: proper is per-endpoint (message ids are deduped); this bounds the window.
MAX_ENVELOPE_SKEW_NS = 5 * 60 * 1_000_000_000


class IdentityError(RuntimeError):
    """The identity could not be loaded, created, or used."""


class EnvelopeError(RuntimeError):
    """A signed envelope was malformed, expired, or failed verification."""


class InviteError(RuntimeError):
    """An invite code was malformed, expired, or failed verification."""


def canonical_bytes(payload: Any) -> bytes:
    """Deterministic JSON encoding — the exact bytes a signature covers.

    Sorted keys, no incidental whitespace, UTF-8. Two instances must produce
    byte-identical output for the same object or every signature fails.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _now_ns() -> int:
    return time.time_ns()


def _ed25519() -> Any:
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except Exception as exc:  # noqa: BLE001
        raise IdentityError(
            "The 'cryptography' package is required for Personal Jarvis Socials."
        ) from exc
    return ed25519


@dataclass(frozen=True, slots=True)
class InvitePayload:
    """The decoded, signature-verified contents of an invite code."""

    node_id: str
    display_name: str
    endpoint: str | None
    issued_at_ns: int
    expires_at_ns: int
    nonce: str

    @property
    def is_expired(self) -> bool:
        return self.expires_at_ns > 0 and _now_ns() > self.expires_at_ns

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "display_name": self.display_name,
            "endpoint": self.endpoint,
            "issued_at_ns": self.issued_at_ns,
            "expires_at_ns": self.expires_at_ns,
            "nonce": self.nonce,
        }


class SocialIdentity:
    """The local node's key pair plus everything that is signed with it.

    Construct with :meth:`load_or_create` in application code; the explicit
    constructor takes a private key so tests can build a deterministic pair
    without touching the user's credential store.
    """

    def __init__(self, private_key_hex: str) -> None:
        ed25519 = _ed25519()
        try:
            raw = bytes.fromhex(private_key_hex.strip())
            self._private = ed25519.Ed25519PrivateKey.from_private_bytes(raw)
        except (ValueError, TypeError) as exc:
            raise IdentityError("Stored socials private key is not usable.") from exc
        self._private_hex = private_key_hex.strip()

    # -- construction ----------------------------------------------------

    @classmethod
    def generate(cls) -> SocialIdentity:
        ed25519 = _ed25519()
        key = ed25519.Ed25519PrivateKey.generate()
        from cryptography.hazmat.primitives import serialization

        raw = key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return cls(raw.hex())

    @classmethod
    def load_or_create(cls, *, persist: bool = True) -> SocialIdentity:
        """Read the stored key, or mint one and store it.

        A corrupt stored value is replaced rather than raised on: the identity
        is regenerable (friends re-pair), and refusing to boot the social
        section over an unreadable credential would be the worse failure.
        """
        from jarvis.core.config import get_secret, set_secret

        stored = None
        try:
            stored = get_secret(PRIVKEY_SECRET_KEY)
        except Exception:  # noqa: BLE001 — an unreadable store means "no key yet"
            log.debug("socials identity: credential store unreadable", exc_info=True)
        if stored:
            try:
                return cls(stored)
            except IdentityError:
                log.warning("socials identity: stored key unusable — minting a fresh one")

        identity = cls.generate()
        if persist:
            try:
                if not set_secret(PRIVKEY_SECRET_KEY, identity.private_key_hex):
                    log.warning(
                        "socials identity: key could not be persisted — it will "
                        "change on the next start and friends must re-pair"
                    )
            except Exception:  # noqa: BLE001
                log.warning("socials identity: persisting the key failed", exc_info=True)
        return identity

    # -- identity --------------------------------------------------------

    @property
    def private_key_hex(self) -> str:
        return self._private_hex

    @property
    def node_id(self) -> str:
        """The public key, hex — the identity a friend records for this node."""
        from cryptography.hazmat.primitives import serialization

        raw = self._private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return raw.hex()

    @property
    def short_id(self) -> str:
        """A human-comparable fingerprint, e.g. ``4f3c-2b91-08ad``.

        Two people confirming they paired with each other read this aloud; a
        64-character hex string is not something anyone checks honestly.
        """
        node = self.node_id
        return "-".join(node[i : i + 4] for i in range(0, 12, 4))

    # -- signing ---------------------------------------------------------

    def sign(self, payload: Any) -> str:
        """Sign the canonical encoding of ``payload``; returns hex."""
        return self._private.sign(canonical_bytes(payload)).hex()

    def seal(self, body: dict[str, Any], *, issued_at_ns: int | None = None) -> dict[str, Any]:
        """Wrap ``body`` in a signed envelope ready to POST to a peer."""
        envelope = {
            "node": self.node_id,
            "issued_at_ns": int(issued_at_ns if issued_at_ns is not None else _now_ns()),
            "body": body,
        }
        envelope["sig"] = self.sign(
            {k: envelope[k] for k in ("node", "issued_at_ns", "body")}
        )
        return envelope

    # -- invites ---------------------------------------------------------

    def create_invite(
        self,
        *,
        display_name: str,
        endpoint: str | None = None,
        ttl_ns: int = INVITE_TTL_NS,
        now_ns: int | None = None,
    ) -> str:
        """Mint a signed, shareable invite code.

        The code is the whole handshake: it names the issuing node, how to
        reach it (when the user supplied a reachable address), and proves both
        with a signature. The recipient pastes it and is done.
        """
        issued = int(now_ns if now_ns is not None else _now_ns())
        payload = {
            "v": 1,
            "node": self.node_id,
            "name": str(display_name or "")[:120],
            "endpoint": _clean_endpoint(endpoint),
            "issued_at_ns": issued,
            "expires_at_ns": issued + max(0, int(ttl_ns)),
            "nonce": _secrets.token_hex(8),
        }
        raw = canonical_bytes(payload)
        signature = self._private.sign(raw)
        return f"{INVITE_PREFIX}.{_b64e(raw)}.{_b64e(signature)}"


def verify_envelope(
    envelope: Any,
    *,
    now_ns: int | None = None,
    max_skew_ns: int = MAX_ENVELOPE_SKEW_NS,
) -> tuple[str, dict[str, Any]]:
    """Verify a signed envelope. Returns ``(node_id, body)``.

    Raises :class:`EnvelopeError` on anything that does not check out — a
    malformed shape, a clock too far off, or a bad signature. The caller still
    has to decide whether that node id is a friend; this function only proves
    who wrote the request, never that they are welcome.
    """
    if not isinstance(envelope, dict):
        raise EnvelopeError("Envelope is not an object.")
    node = envelope.get("node")
    signature = envelope.get("sig")
    body = envelope.get("body")
    issued = envelope.get("issued_at_ns")
    if not isinstance(node, str) or not isinstance(signature, str):
        raise EnvelopeError("Envelope is missing 'node' or 'sig'.")
    if not isinstance(body, dict):
        raise EnvelopeError("Envelope body must be an object.")
    try:
        issued_ns = int(issued)
    except (TypeError, ValueError) as exc:
        raise EnvelopeError("Envelope 'issued_at_ns' is not a number.") from exc

    now = int(now_ns if now_ns is not None else _now_ns())
    if max_skew_ns > 0 and abs(now - issued_ns) > max_skew_ns:
        raise EnvelopeError("Envelope timestamp is outside the accepted window.")

    if not verify_signature(
        node,
        {"node": node, "issued_at_ns": issued_ns, "body": body},
        signature,
    ):
        raise EnvelopeError("Envelope signature does not verify.")
    return node, body


def verify_signature(node_id: str, payload: Any, signature_hex: str) -> bool:
    """True when ``signature_hex`` is a valid signature by ``node_id``."""
    ed25519 = _ed25519()
    try:
        public = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(node_id))
        public.verify(bytes.fromhex(signature_hex), canonical_bytes(payload))
    except Exception:  # noqa: BLE001 — every failure mode is "not verified"
        return False
    return True


def decode_invite(code: str, *, now_ns: int | None = None) -> InvitePayload:
    """Parse and verify an invite code.

    Raises :class:`InviteError` with a message meant for a person: this is the
    one place in Socials where a user pastes a string by hand, so "that code has
    expired" has to be distinguishable from "that is not an invite code".
    """
    text = (code or "").strip()
    if not text:
        raise InviteError("No invite code was given.")
    parts = text.split(".")
    if len(parts) != 3 or parts[0] != INVITE_PREFIX:
        raise InviteError("That does not look like a Personal Jarvis invite code.")
    try:
        raw = _b64d(parts[1])
        signature = _b64d(parts[2])
    except Exception as exc:  # noqa: BLE001
        raise InviteError("The invite code is damaged — copy it again in full.") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise InviteError("The invite code is damaged — copy it again in full.") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise InviteError("This invite code was made by an incompatible version.")

    node = payload.get("node")
    if not isinstance(node, str) or not node:
        raise InviteError("The invite code names no node.")

    ed25519 = _ed25519()
    try:
        public = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(node))
        # Verified against the exact bytes that were encoded, not against a
        # re-serialised copy — re-encoding is how a signature check quietly
        # starts depending on the local JSON implementation.
        public.verify(signature, raw)
    except Exception as exc:  # noqa: BLE001
        raise InviteError("The invite code's signature does not match.") from exc

    invite = InvitePayload(
        node_id=node,
        display_name=str(payload.get("name") or "")[:120],
        endpoint=_clean_endpoint(payload.get("endpoint")),
        issued_at_ns=int(payload.get("issued_at_ns") or 0),
        expires_at_ns=int(payload.get("expires_at_ns") or 0),
        nonce=str(payload.get("nonce") or ""),
    )
    now = int(now_ns if now_ns is not None else _now_ns())
    if invite.expires_at_ns > 0 and now > invite.expires_at_ns:
        raise InviteError("This invite code has expired — ask for a fresh one.")
    return invite


def _clean_endpoint(value: Any) -> str | None:
    """Normalise a peer base URL, or ``None`` when there is nothing usable.

    Only ``http``/``https`` survive. An invite is pasted from a chat window, so
    this is the boundary where a ``file://`` or ``javascript:`` string has to
    stop rather than be handed to an HTTP client later.
    """
    if not value:
        return None
    text = str(value).strip().rstrip("/")
    if not text:
        return None
    lowered = text.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        return None
    return text


__all__ = [
    "INVITE_PREFIX",
    "INVITE_TTL_NS",
    "MAX_ENVELOPE_SKEW_NS",
    "PRIVKEY_SECRET_KEY",
    "EnvelopeError",
    "IdentityError",
    "InviteError",
    "InvitePayload",
    "SocialIdentity",
    "canonical_bytes",
    "decode_invite",
    "verify_envelope",
    "verify_signature",
]
