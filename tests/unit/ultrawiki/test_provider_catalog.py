"""Invariants of the UltraWiki provider catalog.

The catalog exists because the settings UI used to list providers it gave the
user no way to connect. These tests pin the properties that keep that defect
from coming back — above all: every credential field the catalog tells the UI
to render must be a slot the secrets API will actually accept.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import get_args

import pytest

from jarvis.core.config_writer import ULTRAWIKI_SLOT_KEYS
from jarvis.setup.wizard import SECRETS
from jarvis.ultrawiki import embeddings as embeddings_mod
from jarvis.ultrawiki import provider_catalog as catalog
from jarvis.ultrawiki import rerank as rerank_mod

_ROOT = Path(__file__).resolve().parents[3]


def test_every_catalog_secret_slot_is_writable_through_the_secrets_api():
    """A rendered key field the API refuses to save is a dead end.

    ``POST /api/secrets/{key}`` gates on the wizard allowlist, so a catalog
    entry naming a slot outside it would render an input box whose Save button
    always 404s — the exact shape of the bug this catalog was written to fix.
    """
    allowlisted = {spec.key for spec in SECRETS}
    unwritable = catalog.all_secret_keys() - allowlisted
    assert not unwritable, (
        f"catalog references secret slots the secrets API cannot write: "
        f"{sorted(unwritable)}"
    )


def test_embedding_catalog_matches_the_backend_registry():
    """Presentation and runtime must agree on which embedding providers exist.

    A catalog row without a backend is an option that silently does nothing; a
    backend without a catalog row is a capability the user cannot reach.
    """
    catalog_ids = {spec.id for spec in catalog.EMBEDDING_PROVIDERS}
    assert catalog_ids == set(embeddings_mod.EMBEDDING_BACKENDS)


def test_rerank_catalog_matches_the_backend_registry():
    catalog_ids = {spec.id for spec in catalog.RERANK_PROVIDERS}
    assert catalog_ids == set(rerank_mod.RERANK_BACKENDS)


def test_embedding_default_models_match_the_backend_defaults():
    """The placeholder the card shows is the model the backend really uses."""
    for spec in catalog.EMBEDDING_PROVIDERS:
        assert spec.default_model == embeddings_mod.DEFAULT_MODELS[spec.id], spec.id


@pytest.mark.parametrize("slot", catalog.SLOT_NAMES)
def test_every_slot_has_providers_and_unique_ids(slot):
    specs = catalog.catalog_for_slot(slot)
    assert specs, f"slot {slot} has no providers"
    ids = [spec.id for spec in specs]
    assert len(ids) == len(set(ids)), f"duplicate provider ids in slot {slot}: {ids}"
    for spec in specs:
        assert spec.slot == slot
        assert spec.label.strip()
        assert spec.credential_help.strip()


def test_a_credentialed_provider_always_names_its_secret_slot():
    """An api_key/connection_string card with no slot renders an inert field."""
    for slot in catalog.SLOT_NAMES:
        for spec in catalog.catalog_for_slot(slot):
            if spec.auth_mode in ("api_key", "connection_string", "managed_link"):
                assert spec.secret_keys, f"{slot}/{spec.id} declares no secret slot"
            else:
                assert spec.auth_mode == "none" or (
                    spec.auth_mode in catalog.SUBSCRIPTION_AUTH_MODES
                    and not spec.secret_keys
                )


def test_distillation_exposes_every_subscription_brain_as_its_own_card():
    subscription_rows = {
        spec.id: spec.auth_mode
        for spec in catalog.DISTILL_PROVIDERS
        if spec.auth_mode in catalog.SUBSCRIPTION_AUTH_MODES
    }
    assert subscription_rows == {
        "codex": "codex",
        "antigravity": "antigravity",
        "claude-cli": "claude_cli",
    }


def test_auth_modes_match_typescript_and_the_ui_labels():
    """Python producer, TypeScript consumer, and UI labels stay in lockstep."""
    python_modes = set(get_args(catalog.UltraWikiAuthMode))
    api_source = (
        _ROOT
        / "jarvis/ui/web/frontend/src/lib/ultrawikiApi.ts"
    ).read_text(encoding="utf-8")
    block = re.search(
        r"export type UltraWikiAuthMode\s*=\s*(.*?);",
        api_source,
        re.DOTALL,
    )
    assert block is not None
    typescript_modes = set(re.findall(r'"([a-z_]+)"', block.group(1)))

    english = json.loads(
        (
            _ROOT
            / "jarvis/ui/web/frontend/src/i18n/locales/en.json"
        ).read_text(encoding="utf-8")
    )
    label_modes = {
        key.removeprefix("auth_")
        for key in english["ultrawiki"]["card"]
        if key.startswith("auth_")
    }

    assert python_modes == typescript_modes == label_modes


def test_storage_presets_resolve_to_the_two_functional_backends_only():
    """Named presets stay presentation; the store keeps exactly two code paths.

    Guards against the enum-drift class (AP-4 / BUG-008): adding "Supabase" as
    a THIRD functional backend value would have to be mirrored through Python,
    SQL, Pydantic, TS and the UI. It resolves to ``postgres`` instead.
    """
    for spec in catalog.STORAGE_PROVIDERS:
        assert spec.db_backend in ("sqlite", "postgres"), spec.id
    assert catalog.storage_backend_of("sqlite") == "sqlite"
    assert catalog.storage_backend_of("supabase") == "postgres"
    assert catalog.storage_backend_of("neon") == "postgres"
    assert catalog.storage_backend_of("postgres") == "postgres"


def test_an_unknown_storage_preset_degrades_to_the_local_floor():
    """A config from a newer build must not leave the store without a backend."""
    assert catalog.storage_backend_of("some-future-cloud") == "sqlite"
    assert catalog.storage_backend_of("") == "sqlite"


def test_storage_provider_is_a_writable_config_slot():
    """The preset has to survive a restart, so the writer must accept the key."""
    assert "storage_provider" in ULTRAWIKI_SLOT_KEYS


def test_only_non_storage_slots_leave_db_backend_unset():
    for slot in ("embedding", "distill", "rerank"):
        for spec in catalog.catalog_for_slot(slot):
            assert spec.db_backend is None, f"{slot}/{spec.id}"


def test_get_provider_spec_is_slot_scoped():
    """Voyage exists in two slots; a lookup must not cross them."""
    assert catalog.get_provider_spec("rerank", "voyage") is not None
    assert catalog.get_provider_spec("embedding", "voyage") is not None
    assert catalog.get_provider_spec("rerank", "mistral") is None
    assert catalog.get_provider_spec("storage", "voyage") is None
    assert catalog.get_provider_spec("nonsense", "voyage") is None


def test_at_most_one_recommended_provider_per_slot():
    """Two recommendations is the same as none — the badge must stay a signal."""
    for slot in catalog.SLOT_NAMES:
        recommended = [s.id for s in catalog.catalog_for_slot(slot) if s.recommended]
        assert len(recommended) <= 1, f"slot {slot} recommends {recommended}"


def test_local_providers_carry_a_base_url_default():
    """A local provider without a reachable default is unconfigurable by click."""
    for slot in catalog.SLOT_NAMES:
        for spec in catalog.catalog_for_slot(slot):
            if spec.supports_base_url:
                assert spec.default_base_url, f"{slot}/{spec.id}"
