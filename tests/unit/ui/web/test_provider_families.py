"""The API Keys page's company grouping is derived from the catalog, never kept by hand."""

from __future__ import annotations

from jarvis.core import config as cfg_mod
from jarvis.ui.web.provider_families import (
    LOCAL_FAMILY,
    SUBSCRIPTION_FAMILY,
    build_families,
    family_of,
)
from jarvis.ui.web.provider_spec import PROVIDERS, provider_billing


def _families(present: set[str] | None = None) -> dict[str, dict]:
    held = present or set()
    return {f["id"]: f for f in build_families(secret_present=lambda slot: slot in held)}


def test_every_visible_provider_belongs_to_exactly_one_family() -> None:
    families = _families()
    members = [pid for fam in families.values() for pid in fam["provider_ids"]]
    visible = [spec.id for spec in PROVIDERS if not spec.hidden]
    assert sorted(members) == sorted(visible)
    assert len(members) == len(set(members))


def test_subscription_logins_join_the_company_that_issues_them() -> None:
    families = _families()
    for spec in PROVIDERS:
        if spec.hidden or spec.auth_mode not in SUBSCRIPTION_FAMILY:
            continue
        family = families[SUBSCRIPTION_FAMILY[spec.auth_mode]]
        assert spec.id in family["provider_ids"]
        assert family["subscription"] is not None
        assert family["subscription"]["kind"] == spec.auth_mode


def test_keyed_cards_follow_their_credential_chain() -> None:
    for spec in PROVIDERS:
        if spec.hidden or spec.auth_mode != "api_key":
            continue
        scopes = [cfg_mod.secret_slot_scope(slot) for slot in spec.secret_keys]
        expected = next((s.family for s in scopes if s is not None), spec.id)
        assert family_of(spec) == expected, spec.id


def test_local_cards_share_one_family_without_a_key() -> None:
    families = _families()
    local = families[LOCAL_FAMILY]
    assert local["local"] is True
    assert local["key_slot"] is None
    for spec in PROVIDERS:
        if not spec.hidden and provider_billing(spec) == "local":
            assert spec.id in local["provider_ids"]


def test_family_key_is_the_primary_slot_and_reports_presence_only() -> None:
    families = _families({"anthropic_api_key"})
    claude = families["claude-api"]
    assert claude["key_slot"] == cfg_mod.secret_family_primary_slot("claude-api")
    assert claude["key_present"] is True
    assert families["openai"]["key_present"] is False
    # Presence flags only: no payload field may carry a secret value.
    assert all(isinstance(v, (str, bool, list, dict, type(None))) for v in claude.values())


def test_separate_keys_list_only_scoped_slots_that_hold_a_key() -> None:
    scoped = cfg_mod.secret_family_scoped_slots("gemini")
    assert scoped, "gemini has scoped slots (realtime, agents)"
    families = _families({"gemini_api_key", scoped[0]})
    separate = [entry["slot"] for entry in families["gemini"]["separate_keys"]]
    assert separate == [scoped[0]]


def test_agent_ids_use_the_worker_slug() -> None:
    families = _families()
    assert "openai-codex" in families["openai"]["agent_ids"]
