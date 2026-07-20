"""BUG-103: collapse every macOS Keychain item into ONE, so an unsigned venv
python triggers exactly one "Always Allow" prompt per boot instead of one per
credential slot.

These tests use a fake inner backend (dict-based, records every call) and
never touch a real keyring/Keychain, so they run identically on every host
OS -- the wrapper class itself has no platform gate; that gate lives only in
``jarvis/core/config.py``, which wraps the platform backend on darwin only.
"""

from __future__ import annotations

import pytest
from keyring.errors import PasswordDeleteError

from jarvis.core.keychain_bundle import VAULT_ACCOUNT, DarwinBundleKeyringBackend

SERVICE = "personal-jarvis"


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
