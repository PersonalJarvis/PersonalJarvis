"""Admin password hashing and verification for ``security.admin_password_hash``.

The admin password gates sensitive UI actions (built-in skill edits, self-mod
backup restore). It is stored as a salted scrypt digest -- a memory-hard KDF
from the standard library, so no extra dependency and identical behaviour on
every OS.

Stored format (one string, ``$``-separated)::

    scrypt$<n>$<r>$<p>$<salt_hex>$<digest_hex>

Create one with::

    python -m jarvis.core.admin_password

A bare 64-character SHA-256 hex digest (the pre-2026-09 format) is still
accepted so an existing config keeps working, but it is deprecated: it has no
salt and no work factor, so a leaked config makes the password cheap to
brute-force. Re-hash with the command above.
"""

from __future__ import annotations

import getpass
import hashlib
import hmac
import logging
import re
import secrets
import sys

log = logging.getLogger(__name__)

SCHEME = "scrypt"
# Interactive-login strength (~16 MiB, well under OpenSSL's default maxmem).
_N = 2**14
_R = 8
_P = 1
_SALT_BYTES = 16
_DKLEN = 32
# Upper bounds applied to a stored hash so a hand-edited config cannot make a
# single check allocate gigabytes.
_MAX_N = 2**20
_MAX_R = 32
_MAX_P = 16

_LEGACY_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    # maxmem must cover 128 * n * r (plus slack); the bounds above cap it.
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=dklen,
        maxmem=256 * n * r + 1024 * 1024,
    )


def hash_admin_password(password: str) -> str:
    """Return the storable scrypt hash string for *password*."""
    if not password:
        raise ValueError("admin password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _scrypt(password, salt, _N, _R, _P, _DKLEN)
    return f"{SCHEME}${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def is_legacy_hash(stored: str) -> bool:
    """True when *stored* is the deprecated unsalted SHA-256 hex format."""
    return bool(_LEGACY_SHA256_RE.match(stored or ""))


def _verify_legacy_sha256(provided: str, stored: str) -> bool:
    # Deprecated format, kept only so existing configs do not lock their
    # owner out. New hashes are always scrypt (see hash_admin_password).
    log.warning(
        "security.admin_password_hash uses the deprecated unsalted SHA-256 "
        "format; re-hash it with `python -m jarvis.core.admin_password`."
    )
    computed = hashlib.sha256(provided.encode("utf-8")).hexdigest()
    return hmac.compare_digest(computed, stored.lower())


def verify_admin_password(provided: str | None, stored: str | None) -> bool:
    """Constant-time check of *provided* against the stored hash string.

    Empty stored hash or empty password -> False (admin mode locked). A
    malformed stored hash also returns False and logs why, never raises.
    """
    if not stored or not provided:
        return False
    if is_legacy_hash(stored):
        return _verify_legacy_sha256(provided, stored)
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != SCHEME:
        log.warning("security.admin_password_hash has an unrecognised format; admin mode locked.")
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = bytes.fromhex(parts[4])
        expected = bytes.fromhex(parts[5])
    except ValueError:
        log.warning("security.admin_password_hash is malformed; admin mode locked.")
        return False
    if not (1 < n <= _MAX_N and n & (n - 1) == 0 and 0 < r <= _MAX_R and 0 < p <= _MAX_P):
        log.warning(
            "security.admin_password_hash has out-of-range scrypt parameters; admin mode locked."
        )
        return False
    if not salt or not expected:
        log.warning("security.admin_password_hash has an empty salt or digest; admin mode locked.")
        return False
    computed = _scrypt(provided, salt, n, r, p, len(expected))
    return hmac.compare_digest(computed, expected)


def check_admin_pass(provided: str | None, security_cfg: object) -> bool:
    """Check *provided* against ``security_cfg.admin_password_hash``."""
    if security_cfg is None:
        return False
    return verify_admin_password(provided, getattr(security_cfg, "admin_password_hash", ""))


def main() -> int:
    """Prompt for a password and print the value for ``admin_password_hash``."""
    first = getpass.getpass("New admin password: ")
    second = getpass.getpass("Repeat: ")
    if not first or first != second:
        print("Passwords are empty or do not match.", file=sys.stderr)
        return 1
    print(hash_admin_password(first))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
