"""Per-user token store: issue / verify / list / revoke.

Security contract (§6):
    - The plaintext token is returned exactly once, at issue time. Only the
      SHA-256 hex of the plaintext is persisted; the plaintext is unrecoverable
      afterwards.
    - :meth:`TokenStore.verify` compares with :func:`hmac.compare_digest`
      (constant-time) and fails closed for missing / unknown / revoked tokens.
    - Revocation is instant: a revoked token's ``revoked_at`` is set and every
      subsequent :meth:`verify` returns ``None``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from uuid import uuid4

from .store import Store

# Issued plaintext tokens carry a recognizable, non-secret prefix so they can
# be spotted in client config; the entropy lives in the random suffix.
_TOKEN_PREFIX = "kp_"  # noqa: S105 — a public label prefix, not a secret
_TOKEN_ENTROPY_BYTES = 32  # 256 bits


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IssuedToken:
    """The one-time result of issuing a token."""

    id: str
    label: str
    plaintext: str


class TokenStore:
    def __init__(self, store: Store) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Issue
    # ------------------------------------------------------------------

    def issue(self, label: str) -> IssuedToken:
        """Create a new token; return its plaintext exactly once."""
        token_id = str(uuid4())
        plaintext = _TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_ENTROPY_BYTES)
        self._store.execute(
            "INSERT INTO tokens (id, label, token_sha256, created_at, "
            "revoked_at) VALUES (?, ?, ?, ?, NULL)",
            (token_id, label, _sha256_hex(plaintext), int(time.time())),
        )
        return IssuedToken(id=token_id, label=label, plaintext=plaintext)

    # ------------------------------------------------------------------
    # Verify (fail-closed)
    # ------------------------------------------------------------------

    def verify(self, plaintext: str | None) -> str | None:
        """Return the token id for a valid, non-revoked token, else ``None``.

        The lookup is by the SHA-256 hash of the presented token, so the real
        timing surface is the indexed DB equality match — not constant-time on
        its own. We hash the token (the secret) before it touches the query, so
        the comparison is over hash digests, not the raw secret. The
        :func:`hmac.compare_digest` below is a belt-and-suspenders check on the
        returned hash, NOT a standalone timing-attack guarantee.
        """
        if not plaintext:
            return None
        candidate_hash = _sha256_hex(plaintext)
        row = self._store.query_one(
            "SELECT id, token_sha256, revoked_at FROM tokens "
            "WHERE token_sha256 = ?",
            (candidate_hash,),
        )
        if row is None:
            return None
        if not hmac.compare_digest(str(row["token_sha256"]), candidate_hash):
            return None
        if row["revoked_at"] is not None:
            return None
        return str(row["id"])

    # ------------------------------------------------------------------
    # List / revoke
    # ------------------------------------------------------------------

    def list(self) -> list[dict[str, object]]:
        """All tokens (active and revoked), newest first. No plaintext."""
        rows = self._store.query_all(
            "SELECT id, label, token_sha256, created_at, revoked_at "
            "FROM tokens ORDER BY created_at DESC, id DESC"
        )
        return [dict(r) for r in rows]

    def revoke(self, token_id: str) -> bool:
        """Revoke a token by id. Returns ``False`` if the id is unknown.

        Idempotent: re-revoking an already-revoked token returns ``True``
        (it remains revoked) but does not move the timestamp.
        """
        cur = self._store.execute(
            "UPDATE tokens SET revoked_at = ? "
            "WHERE id = ? AND revoked_at IS NULL",
            (int(time.time()), token_id),
        )
        if cur.rowcount > 0:
            return True
        # Either unknown id, or already revoked — distinguish the two.
        exists = self._store.query_one(
            "SELECT 1 FROM tokens WHERE id = ?", (token_id,)
        )
        return exists is not None
