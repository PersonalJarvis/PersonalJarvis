"""Admin password hashing: salted scrypt, legacy SHA-256 still accepted."""

from __future__ import annotations

from jarvis.core.admin_password import (
    check_admin_pass,
    hash_admin_password,
    is_legacy_hash,
    verify_admin_password,
)
from jarvis.core.config import SecurityConfig

# SHA-256 hex of "legacy-pass" -- the pre-scrypt stored format.
_LEGACY = "c4ebc632c03529cf9f121d07055ee260606f7907540be42424cd2af15a0bf342"


def test_new_hash_is_salted_scrypt() -> None:
    first = hash_admin_password("hunter2")
    second = hash_admin_password("hunter2")
    assert first.startswith("scrypt$")
    assert first != second  # random salt
    assert "hunter2" not in first
    assert not is_legacy_hash(first)


def test_verify_accepts_right_and_rejects_wrong_password() -> None:
    stored = hash_admin_password("hunter2")
    assert verify_admin_password("hunter2", stored)
    assert not verify_admin_password("hunter3", stored)


def test_empty_inputs_lock_admin_mode() -> None:
    stored = hash_admin_password("x")
    assert not verify_admin_password("", stored)
    assert not verify_admin_password(None, stored)
    assert not verify_admin_password("x", "")
    assert not check_admin_pass("x", None)


def test_legacy_sha256_hash_still_verifies() -> None:
    assert is_legacy_hash(_LEGACY)
    assert verify_admin_password("legacy-pass", _LEGACY)
    assert verify_admin_password("legacy-pass", _LEGACY.upper())
    assert not verify_admin_password("other", _LEGACY)


def test_malformed_or_hostile_hash_is_rejected() -> None:
    assert not verify_admin_password("x", "scrypt$abc$8$1$00$00")
    assert not verify_admin_password("x", "bcrypt$whatever")
    # Work factor far above the cap must not be computed.
    assert not verify_admin_password("x", "scrypt$1073741824$8$1$00ff$00ff")
    assert not verify_admin_password("x", "scrypt$16384$8$1$$")


def test_check_admin_pass_reads_security_config() -> None:
    cfg = SecurityConfig(admin_password_hash=hash_admin_password("pw"))
    assert check_admin_pass("pw", cfg)
    assert not check_admin_pass("nope", cfg)
    assert not check_admin_pass("pw", SecurityConfig())
