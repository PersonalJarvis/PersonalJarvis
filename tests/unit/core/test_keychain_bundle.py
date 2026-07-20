"""BUG-103: collapse every macOS Keychain item into ONE, so an unsigned venv
python triggers exactly one "Always Allow" prompt per boot instead of one per
credential slot.

These tests use a fake inner backend (dict-based, records every call) and
never touch a real keyring/Keychain, so they run identically on every host
OS -- the wrapper class itself has no platform gate; that gate lives only in
``jarvis/core/config.py``, which wraps the platform backend on darwin only.
"""

from __future__ import annotations

import base64
import json

import pytest
from keyring.errors import PasswordDeleteError

from jarvis.core.keychain_bundle import (
    VAULT_ACCOUNT,
    DarwinBundleKeyringBackend,
    SecurityCliVault,
    SecurityCliVaultError,
)

SERVICE = "personal-jarvis"


def _b64(payload: dict[str, str]) -> str:
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


class FakeSecurityCli:
    """In-memory stand-in for the ``security`` CLI vault store."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, ...]] = []
        self.fail_reads = False
        self.fail_writes = False

    def read(self, service: str, account: str) -> str | None:
        self.calls.append(("read", service, account))
        if self.fail_reads:
            raise SecurityCliVaultError("simulated CLI read failure")
        return self.items.get((service, account))

    def write(self, service: str, account: str, value: str) -> None:
        self.calls.append(("write", service, account))
        if self.fail_writes:
            raise SecurityCliVaultError("simulated CLI write failure")
        self.items[(service, account)] = value

    def delete(self, service: str, account: str) -> None:
        self.calls.append(("delete", service, account))
        self.items.pop((service, account), None)


class FakeInnerBackend:
    """Minimal per-item keyring backend, matching real backend semantics:
    ``get_password`` returns ``None`` for a missing item, ``delete_password``
    RAISES ``keyring.errors.PasswordDeleteError`` when the item is already
    absent (as every real backend Jarvis relies on does -- see the
    ``PasswordDeleteError`` handling in ``config.delete_secret``)."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, ...]] = []

    def get_password(self, service: str, key: str) -> str | None:
        self.calls.append(("get", service, key))
        return self.values.get((service, key))

    def set_password(self, service: str, key: str, value: str) -> None:
        self.calls.append(("set", service, key))
        self.values[(service, key)] = value

    def delete_password(self, service: str, key: str) -> None:
        self.calls.append(("delete", service, key))
        if (service, key) not in self.values:
            raise PasswordDeleteError(f"no such credential for {service}:{key}")
        del self.values[(service, key)]


@pytest.fixture
def inner() -> FakeInnerBackend:
    return FakeInnerBackend()


@pytest.fixture
def bundle(inner: FakeInnerBackend) -> DarwinBundleKeyringBackend:
    return DarwinBundleKeyringBackend(inner)


# ---------------------------------------------------------------------------
# One vault item total; bundle-served reads never touch inner per-key.
# ---------------------------------------------------------------------------


def test_multiple_sets_produce_exactly_one_inner_item(
    bundle: DarwinBundleKeyringBackend, inner: FakeInnerBackend
) -> None:
    bundle.set_password(SERVICE, "anthropic_api_key", "sk-ant-1")
    bundle.set_password(SERVICE, "groq_api_key", "gsk-2")
    bundle.set_password(SERVICE, "openrouter_api_key", "or-3")

    stored_items = {k for k in inner.values if k[0] == SERVICE}
    assert stored_items == {(SERVICE, VAULT_ACCOUNT)}, (
        "every secret must collapse into the single vault item, never a "
        "per-key inner item"
    )


def test_get_is_served_from_bundle_without_per_key_inner_reads(
    bundle: DarwinBundleKeyringBackend, inner: FakeInnerBackend
) -> None:
    bundle.set_password(SERVICE, "anthropic_api_key", "sk-ant-1")
    inner.calls.clear()

    assert bundle.get_password(SERVICE, "anthropic_api_key") == "sk-ant-1"

    # Only the vault item may be read; no per-key inner get.
    assert all(call[2] == VAULT_ACCOUNT for call in inner.calls if call[0] == "get")


def test_bundle_survives_across_wrapper_instances_via_inner_store(
    inner: FakeInnerBackend,
) -> None:
    first = DarwinBundleKeyringBackend(inner)
    first.set_password(SERVICE, "groq_api_key", "gsk-secret")

    second = DarwinBundleKeyringBackend(inner)
    assert second.get_password(SERVICE, "groq_api_key") == "gsk-secret"


# ---------------------------------------------------------------------------
# Legacy per-key migration.
# ---------------------------------------------------------------------------


def test_legacy_item_migrates_into_bundle_and_is_deleted(
    bundle: DarwinBundleKeyringBackend, inner: FakeInnerBackend
) -> None:
    # Pre-seed a legacy per-key item, as if written before this wrapper existed.
    inner.values[(SERVICE, "telegram_bot_token")] = "123:legacy"

    first_read = bundle.get_password(SERVICE, "telegram_bot_token")
    assert first_read == "123:legacy"

    # The legacy item must be gone and the value now lives in the vault item.
    assert (SERVICE, "telegram_bot_token") not in inner.values
    assert (SERVICE, VAULT_ACCOUNT) in inner.values

    inner.calls.clear()
    second_read = bundle.get_password(SERVICE, "telegram_bot_token")
    assert second_read == "123:legacy"
    # Fully served from the cache/bundle -- no inner call of any kind.
    assert inner.calls == []


def test_legacy_migration_failure_still_returns_the_read_value(
    bundle: DarwinBundleKeyringBackend, inner: FakeInnerBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    inner.values[(SERVICE, "discord_bot_token")] = "legacy-token"

    def _boom(self, service: str, key: str, value: str) -> None:
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr(FakeInnerBackend, "set_password", _boom)

    # Migration fails (the inner set_password raises), but the read must
    # still succeed and return the legacy value -- fail open, never lose it.
    assert bundle.get_password(SERVICE, "discord_bot_token") == "legacy-token"


def test_vault_account_itself_is_never_migrated_into_itself(
    bundle: DarwinBundleKeyringBackend, inner: FakeInnerBackend
) -> None:
    inner.values[(SERVICE, VAULT_ACCOUNT)] = "not-json-but-irrelevant-here"

    # Reading the vault account name directly must go straight to inner,
    # never be treated as a regular bundled key.
    assert bundle.get_password(SERVICE, VAULT_ACCOUNT) == "not-json-but-irrelevant-here"


# ---------------------------------------------------------------------------
# delete_password contract.
# ---------------------------------------------------------------------------


def test_delete_removes_from_vault_and_raises_when_absent(
    bundle: DarwinBundleKeyringBackend, inner: FakeInnerBackend
) -> None:
    bundle.set_password(SERVICE, "groq_api_key", "gsk-secret")

    bundle.delete_password(SERVICE, "groq_api_key")
    assert bundle.get_password(SERVICE, "groq_api_key") is None

    with pytest.raises(PasswordDeleteError):
        bundle.delete_password(SERVICE, "groq_api_key")


def test_delete_also_best_effort_removes_a_legacy_item(
    bundle: DarwinBundleKeyringBackend, inner: FakeInnerBackend
) -> None:
    # A key that exists both in the bundle AND (still) as a legacy per-item
    # entry (e.g. a previous migration attempt that failed to delete it).
    bundle.set_password(SERVICE, "groq_api_key", "gsk-secret")
    inner.values[(SERVICE, "groq_api_key")] = "legacy-leftover"

    bundle.delete_password(SERVICE, "groq_api_key")

    assert (SERVICE, "groq_api_key") not in inner.values
    with pytest.raises(PasswordDeleteError):
        bundle.delete_password(SERVICE, "groq_api_key")


def test_delete_of_legacy_only_key_succeeds_without_bundle_entry(
    bundle: DarwinBundleKeyringBackend, inner: FakeInnerBackend
) -> None:
    inner.values[(SERVICE, "legacy_only_key")] = "legacy-value"

    # Not migrated yet (never read), only present as a legacy item -- delete
    # must still succeed via the best-effort inner delete.
    bundle.delete_password(SERVICE, "legacy_only_key")

    assert (SERVICE, "legacy_only_key") not in inner.values


# ---------------------------------------------------------------------------
# Malformed vault JSON.
# ---------------------------------------------------------------------------


def test_malformed_vault_json_delegates_to_inner_and_is_never_overwritten(
    bundle: DarwinBundleKeyringBackend, inner: FakeInnerBackend
) -> None:
    inner.values[(SERVICE, VAULT_ACCOUNT)] = "{not valid json"

    # A get for some unrelated key must delegate straight to inner instead of
    # crashing or silently returning None from a "loaded" empty bundle.
    assert bundle.get_password(SERVICE, "anthropic_api_key") is None
    assert inner.values[(SERVICE, VAULT_ACCOUNT)] == "{not valid json", (
        "the malformed vault item must never be destroyed/overwritten"
    )

    # A set for some other key must also go straight to the inner backend as
    # a per-item write (bundle disabled for the rest of the process), not
    # attempt to parse/rewrite the malformed vault item.
    bundle.set_password(SERVICE, "anthropic_api_key", "sk-ant-new")
    assert inner.values[(SERVICE, "anthropic_api_key")] == "sk-ant-new"
    assert inner.values[(SERVICE, VAULT_ACCOUNT)] == "{not valid json"


def test_malformed_vault_json_non_object_also_disables_bundle(
    bundle: DarwinBundleKeyringBackend, inner: FakeInnerBackend
) -> None:
    # Valid JSON, but not an object -- must be rejected exactly like invalid
    # JSON (a list has no key/value semantics to merge into).
    inner.values[(SERVICE, VAULT_ACCOUNT)] = "[1, 2, 3]"

    assert bundle.get_password(SERVICE, "anthropic_api_key") is None
    assert inner.values[(SERVICE, VAULT_ACCOUNT)] == "[1, 2, 3]"


# ---------------------------------------------------------------------------
# Probe lifecycle (mirrors the set/get/delete probe in config.py's recovery path).
# ---------------------------------------------------------------------------


def test_probe_lifecycle_passes_through_wrapper(
    bundle: DarwinBundleKeyringBackend, inner: FakeInnerBackend
) -> None:
    probe_key = "__jarvis_backend_probe__deadbeef"
    probe_value = "probe-value-123"

    bundle.set_password(SERVICE, probe_key, probe_value)
    assert bundle.get_password(SERVICE, probe_key) == probe_value

    bundle.delete_password(SERVICE, probe_key)
    assert bundle.get_password(SERVICE, probe_key) is None


# ---------------------------------------------------------------------------
# security-CLI vault store (BUG-103 v2: partition-list-proof zero-dialog path).
# ---------------------------------------------------------------------------


@pytest.fixture
def cli() -> FakeSecurityCli:
    return FakeSecurityCli()


@pytest.fixture
def cli_bundle(
    inner: FakeInnerBackend, cli: FakeSecurityCli
) -> DarwinBundleKeyringBackend:
    return DarwinBundleKeyringBackend(inner, cli=cli)


def test_cli_saves_write_base64_vault_and_never_touch_inner(
    cli_bundle: DarwinBundleKeyringBackend,
    inner: FakeInnerBackend,
    cli: FakeSecurityCli,
) -> None:
    cli_bundle.set_password(SERVICE, "anthropic_api_key", "sk-ant-1")
    cli_bundle.set_password(SERVICE, "groq_api_key", "gsk-2")

    assert inner.values == {}, "with a working CLI the inner keyring stays untouched"
    raw = cli.items[(SERVICE, VAULT_ACCOUNT)]
    decoded = json.loads(base64.b64decode(raw, validate=True))
    assert decoded == {"anthropic_api_key": "sk-ant-1", "groq_api_key": "gsk-2"}


def test_cli_base64_vault_roundtrips_across_wrapper_instances(
    inner: FakeInnerBackend, cli: FakeSecurityCli
) -> None:
    first = DarwinBundleKeyringBackend(inner, cli=cli)
    first.set_password(SERVICE, "groq_api_key", "gsk-secret")

    second = DarwinBundleKeyringBackend(inner, cli=cli)
    assert second.get_password(SERVICE, "groq_api_key") == "gsk-secret"


def test_legacy_plain_json_vault_read_via_cli_is_upgraded_to_base64(
    cli_bundle: DarwinBundleKeyringBackend, cli: FakeSecurityCli
) -> None:
    # A vault item written by the in-process keyring path (plain JSON, per-app
    # ACL) that the CLI can read after the user's one-time consent.
    cli.items[(SERVICE, VAULT_ACCOUNT)] = json.dumps({"telegram_bot_token": "123:t"})

    assert cli_bundle.get_password(SERVICE, "telegram_bot_token") == "123:t"

    raw = cli.items[(SERVICE, VAULT_ACCOUNT)]
    decoded = json.loads(base64.b64decode(raw, validate=True))
    assert decoded == {"telegram_bot_token": "123:t"}, (
        "the plain-JSON vault must be rewritten base64-encoded through the "
        "CLI (fresh -A ACL) on first read"
    )


def test_cli_write_failure_falls_back_to_inner_without_data_loss(
    cli_bundle: DarwinBundleKeyringBackend,
    inner: FakeInnerBackend,
    cli: FakeSecurityCli,
) -> None:
    cli.fail_writes = True

    cli_bundle.set_password(SERVICE, "openrouter_api_key", "or-3")

    assert json.loads(inner.values[(SERVICE, VAULT_ACCOUNT)]) == {
        "openrouter_api_key": "or-3"
    }
    assert cli_bundle.get_password(SERVICE, "openrouter_api_key") == "or-3"


def test_cli_read_failure_falls_back_to_inner_read(
    cli_bundle: DarwinBundleKeyringBackend,
    inner: FakeInnerBackend,
    cli: FakeSecurityCli,
) -> None:
    inner.values[(SERVICE, VAULT_ACCOUNT)] = json.dumps({"groq_api_key": "gsk-4"})
    cli.fail_reads = True

    assert cli_bundle.get_password(SERVICE, "groq_api_key") == "gsk-4"


def test_base64_vault_readable_even_without_cli(
    inner: FakeInnerBackend,
) -> None:
    # A CLI-written vault read by a wrapper WITHOUT a CLI store (e.g. the
    # security binary vanished): the base64 format must still parse.
    inner.values[(SERVICE, VAULT_ACCOUNT)] = _b64({"anthropic_api_key": "sk-ant-9"})

    bundle = DarwinBundleKeyringBackend(inner)
    assert bundle.get_password(SERVICE, "anthropic_api_key") == "sk-ant-9"


def test_garbage_that_is_neither_json_nor_base64_json_disables_bundle(
    cli_bundle: DarwinBundleKeyringBackend, cli: FakeSecurityCli
) -> None:
    cli.items[(SERVICE, VAULT_ACCOUNT)] = "%%% definitely not a vault %%%"

    assert cli_bundle.get_password(SERVICE, "anthropic_api_key") is None
    assert cli.items[(SERVICE, VAULT_ACCOUNT)] == "%%% definitely not a vault %%%", (
        "a malformed vault item must never be destroyed or overwritten"
    )


def test_legacy_per_key_item_migrates_into_cli_vault(
    cli_bundle: DarwinBundleKeyringBackend,
    inner: FakeInnerBackend,
    cli: FakeSecurityCli,
) -> None:
    inner.values[(SERVICE, "discord_bot_token")] = "legacy-token"

    assert cli_bundle.get_password(SERVICE, "discord_bot_token") == "legacy-token"

    assert (SERVICE, "discord_bot_token") not in inner.values
    decoded = json.loads(
        base64.b64decode(cli.items[(SERVICE, VAULT_ACCOUNT)], validate=True)
    )
    assert decoded == {"discord_bot_token": "legacy-token"}


def test_probe_lifecycle_passes_through_cli_vault(
    cli_bundle: DarwinBundleKeyringBackend, cli: FakeSecurityCli
) -> None:
    probe_key = "__jarvis_backend_probe__cafebabe"
    probe_value = "probe-value-456"

    cli_bundle.set_password(SERVICE, probe_key, probe_value)
    assert cli_bundle.get_password(SERVICE, probe_key) == probe_value

    cli_bundle.delete_password(SERVICE, probe_key)
    assert cli_bundle.get_password(SERVICE, probe_key) is None


# ---------------------------------------------------------------------------
# SecurityCliVault interpolation guards (no subprocess involved).
# ---------------------------------------------------------------------------


def test_security_cli_vault_rejects_unsafe_service_and_account_tokens() -> None:
    vault = SecurityCliVault()
    with pytest.raises(SecurityCliVaultError):
        vault.read("bad service with spaces", VAULT_ACCOUNT)
    with pytest.raises(SecurityCliVaultError):
        vault.read(SERVICE, "account'with'quotes")


def test_security_cli_vault_rejects_non_base64_payloads() -> None:
    vault = SecurityCliVault()
    with pytest.raises(SecurityCliVaultError):
        vault.write(SERVICE, VAULT_ACCOUNT, '{"raw": "json is not allowed"}')


# ---------------------------------------------------------------------------
# The wrapper must be installable through the real ``keyring`` module.
# ---------------------------------------------------------------------------


def test_cli_error_messages_redact_base64_payload_runs() -> None:
    """The write command's input line carries the WHOLE encoded vault; a CLI
    error echoing its input must never leak it into an exception/log line."""
    from jarvis.core.keychain_bundle import _redact

    secret_b64 = base64.b64encode(b'{"anthropic_api_key": "sk-ant-tops3cret"}')
    stderr = f"security: parse error near {secret_b64.decode()} on line 1"
    assert secret_b64.decode() not in _redact(stderr)
    assert "<redacted>" in _redact(stderr)


def test_mutations_refresh_from_store_so_concurrent_writes_survive(
    inner: FakeInnerBackend, cli: FakeSecurityCli
) -> None:
    """Two wrapper instances (two processes) writing DIFFERENT keys: the
    second write must pick up the first one's key from the store instead of
    clobbering the vault with its own stale snapshot."""
    first = DarwinBundleKeyringBackend(inner, cli=cli)
    second = DarwinBundleKeyringBackend(inner, cli=cli)

    # Both processes load the (empty) vault first.
    assert first.get_password(SERVICE, "anthropic_api_key") is None
    assert second.get_password(SERVICE, "groq_api_key") is None

    first.set_password(SERVICE, "anthropic_api_key", "sk-ant-1")
    second.set_password(SERVICE, "groq_api_key", "gsk-2")

    decoded = json.loads(
        base64.b64decode(cli.items[(SERVICE, VAULT_ACCOUNT)], validate=True)
    )
    assert decoded == {"anthropic_api_key": "sk-ant-1", "groq_api_key": "gsk-2"}, (
        "the second writer must refresh-load before mutating, not overwrite "
        "the vault with its stale process-local snapshot"
    )


def test_ensure_keyring_backend_wires_the_cli_store_on_darwin(
    inner: FakeInnerBackend, cli: FakeSecurityCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production wiring itself: on darwin, ``_ensure_keyring_backend``
    must install the wrapper WITH the security-CLI store. Dropping the
    ``cli=`` kwarg (or the factory silently returning ``None``) would
    reopen BUG-103 while every direct-construction test stays green."""
    import sys

    import keyring
    import keyring.backend

    from jarvis.core import config as cfg
    from jarvis.core import keychain_bundle as kb

    class _FakePlatformBackend(keyring.backend.KeyringBackend):
        priority = 5  # type: ignore[assignment]

        def get_password(self, service: str, key: str) -> str | None:
            return inner.get_password(service, key)

        def set_password(self, service: str, key: str, value: str) -> None:
            inner.set_password(service, key, value)

        def delete_password(self, service: str, key: str) -> None:
            inner.delete_password(service, key)

    original = keyring.get_keyring()
    try:
        keyring.set_keyring(_FakePlatformBackend())
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(kb, "darwin_security_cli_vault", lambda: cli)
        monkeypatch.setattr(cfg, "_KEYRING_BACKEND_READY", False)

        cfg._ensure_keyring_backend()

        installed = keyring.get_keyring()
        assert isinstance(installed, DarwinBundleKeyringBackend)
        assert installed._cli is cli
    finally:
        keyring.set_keyring(original)


def test_wrapper_is_accepted_by_keyring_set_keyring(
    inner: FakeInnerBackend,
) -> None:
    """``keyring.set_keyring`` type-checks against ``KeyringBackend`` and
    REJECTS plain classes with ``TypeError``. The boot path swallows that
    error fail-open, so a rejected wrapper silently leaves the raw per-item
    backend active and the whole BUG-103 collapse never engages -- exactly
    the regression that shipped in the first version of this module. This
    test installs the wrapper through the real ``keyring`` API and proves
    reads are actually served through it."""
    import keyring

    original = keyring.get_keyring()
    try:
        keyring.set_keyring(DarwinBundleKeyringBackend(inner))
        keyring.set_password(SERVICE, "anthropic_api_key", "sk-ant-real-path")
        assert keyring.get_password(SERVICE, "anthropic_api_key") == "sk-ant-real-path"
        assert {k for k in inner.values if k[0] == SERVICE} == {
            (SERVICE, VAULT_ACCOUNT)
        }, "the write must land in the single vault item, not a per-key item"
    finally:
        keyring.set_keyring(original)
