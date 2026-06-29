"""Unit tests for the ``update_profile`` router-tier tool.

The tool is the deterministic, brain-driven writer for the structured USER.md
profile (the five clusters the Knowledge matrix + the per-turn system prompt
read). It replaces the soft-disabled legacy background Curator without a
parallel extractor (no "two diverging notebooks" drift). These tests pin the
contract: scalar set, list append + dedupe, the canonical field allow-list, the
do-not-record privacy gate, the ProfileUpdated emit for live UI sync, and the
missing-profile fallback.
"""
from __future__ import annotations

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import ProfileUpdated
from jarvis.memory.user_profile import UserProfile
from jarvis.plugins.tool.profile_update import (
    _BOOL_FIELDS,
    _CANONICAL_FIELDS,
    _LIST_FIELDS,
    UpdateProfileTool,
)


def test_canonical_fields_match_matrix_ui():
    """The tool's writable fields MUST equal the Knowledge-matrix UI's
    CLUSTER_FIELD_KEYS (jarvis/ui/web/frontend/src/views/ProfileView.tsx).

    If the tool can write a field the matrix never renders, the brain learns a
    fact that silently never appears — the BUG-008 multi-layer-enum-drift class.
    This mirror is the Python-side guard; ProfileView.tsx is the authority.
    Update BOTH together (and this expectation) when adding/removing a field.
    """
    matrix_fields = {
        "identity": {
            "name", "preferred_address", "pronouns", "primary_language",
            "languages", "timezone", "devices",
        },
        "communication": {"directness", "formality", "verbosity", "humor_types", "emoji_ok"},
        "work_style": {"focus_mode", "planning_horizon"},
        "values": {"top_values", "pet_peeves", "motivations"},
        "relationship": {"feedback_pref"},
    }
    actual = {cluster: set(fields) for cluster, fields in _CANONICAL_FIELDS.items()}
    assert actual == matrix_fields, (
        "update_profile _CANONICAL_FIELDS drifted from ProfileView.tsx "
        "CLUSTER_FIELD_KEYS — a writable-but-invisible field is silent enum drift."
    )


def test_list_and_bool_field_shapes_match_ui_editor():
    """The list/bool field shapes MUST mirror ledger.ts (LIST_FIELD_KEYS /
    BOOL_FIELD_KEYS), which drives the inline Profile editor: a list field is
    edited as chips (append/remove), a bool as a yes/no toggle. If these drift,
    the editor sends an operation the PATCH /api/profile/field endpoint rejects
    with 400 (e.g. a "set" on a field the UI thinks is scalar). Keep this in sync
    with jarvis/ui/web/frontend/src/views/profile/ledger.ts.
    """
    list_field_names = {field for _cluster, field in _LIST_FIELDS}
    assert list_field_names == {
        "languages", "devices", "humor_types", "top_values", "pet_peeves", "motivations",
    }
    bool_field_names = {field for _cluster, field in _BOOL_FIELDS}
    assert bool_field_names == {"emoji_ok"}
    # Every list/bool field must be a real, writable field.
    for cluster, field in _LIST_FIELDS | _BOOL_FIELDS:
        assert field in _CANONICAL_FIELDS[cluster]

_SEED = """---
schema_version: 1
subject_type: user
identity:
  name: Ruben
  preferred_address: Chef
  languages:
  - English
communication:
  emoji_ok: false
values:
  top_values:
  - Pizza
---

# Ueber den User

## Observations ueber Zeit

<!-- curator:observations:start -->
<!-- curator:observations:end -->
"""


def _load(tmp_path):
    p = tmp_path / "USER.md"
    p.write_text(_SEED, encoding="utf-8")
    return p, UserProfile.load(p)


def _tool(profile, bus=None):
    return UpdateProfileTool(profile_resolver=lambda: profile, bus=bus or EventBus())


def _capturing_bus():
    bus = EventBus()
    events: list[ProfileUpdated] = []

    async def _cap(e: ProfileUpdated) -> None:
        events.append(e)

    bus.subscribe(ProfileUpdated, _cap)
    return bus, events


@pytest.mark.asyncio
async def test_set_scalar_field_persists_and_emits(tmp_path):
    path, profile = _load(tmp_path)
    bus, events = _capturing_bus()
    tool = _tool(profile, bus)

    res = await tool.execute(
        {"cluster": "identity", "field": "preferred_address", "value": "Boss",
         "evidence": "Call me Boss from now on."},
        ctx=None,
    )

    assert res.success
    assert profile.get("identity", "preferred_address") == "Boss"
    # Persisted to disk (a fresh load sees it).
    assert UserProfile.load(path).get("identity", "preferred_address") == "Boss"
    # Live event for the UI.
    assert len(events) == 1
    assert events[0].subject == "user"
    assert events[0].cluster == "identity"
    assert events[0].field == "preferred_address"
    assert events[0].operation == "set"


@pytest.mark.asyncio
async def test_append_list_field_dedupes(tmp_path):
    _path, profile = _load(tmp_path)
    bus, events = _capturing_bus()
    tool = _tool(profile, bus)

    first = await tool.execute(
        {"cluster": "values", "field": "top_values", "value": "Sushi"}, ctx=None
    )
    assert first.success
    assert "Sushi" in profile.get("values", "top_values")
    assert profile.get("values", "top_values") == ["Pizza", "Sushi"]
    assert len(events) == 1
    assert events[0].operation == "append"

    # Re-appending the same value is a no-op: success, but no change, no event.
    again = await tool.execute(
        {"cluster": "values", "field": "top_values", "value": "Sushi"}, ctx=None
    )
    assert again.success
    assert profile.get("values", "top_values") == ["Pizza", "Sushi"]
    assert len(events) == 1  # still one — no duplicate event


@pytest.mark.asyncio
async def test_unknown_cluster_is_rejected(tmp_path):
    _path, profile = _load(tmp_path)
    res = await _tool(profile).execute(
        {"cluster": "hobbies", "field": "name", "value": "x"}, ctx=None
    )
    assert not res.success
    assert "cluster" in (res.error or "").lower()


@pytest.mark.asyncio
async def test_unknown_field_is_rejected(tmp_path):
    _path, profile = _load(tmp_path)
    res = await _tool(profile).execute(
        {"cluster": "identity", "field": "shoe_size", "value": "44"}, ctx=None
    )
    assert not res.success
    assert "field" in (res.error or "").lower()


@pytest.mark.asyncio
async def test_do_not_record_category_is_declined(tmp_path):
    _path, profile = _load(tmp_path)
    bus, events = _capturing_bus()
    res = await _tool(profile, bus).execute(
        {"cluster": "values", "field": "top_values",
         "value": "meine politische Partei", "evidence": "Ich waehle Partei X."},
        ctx=None,
    )
    # Declined, but NOT an error (so the brain does not retry/claim failure).
    assert res.success
    assert "Pizza" == profile.get("values", "top_values")[0]
    assert "meine politische Partei" not in profile.get("values", "top_values")
    assert len(events) == 0


@pytest.mark.asyncio
async def test_boolean_field_coerced_from_string(tmp_path):
    _path, profile = _load(tmp_path)
    res = await _tool(profile).execute(
        {"cluster": "communication", "field": "emoji_ok", "value": "true"}, ctx=None
    )
    assert res.success
    assert profile.get("communication", "emoji_ok") is True


@pytest.mark.asyncio
async def test_missing_profile_returns_error(tmp_path):
    tool = UpdateProfileTool(profile_resolver=lambda: None, bus=EventBus())
    res = await tool.execute(
        {"cluster": "identity", "field": "name", "value": "X"}, ctx=None
    )
    assert not res.success
    assert "profile" in (res.error or "").lower()


@pytest.mark.asyncio
async def test_scalar_field_can_be_overwritten(tmp_path):
    _path, profile = _load(tmp_path)
    res = await _tool(profile).execute(
        {"cluster": "identity", "field": "primary_language", "value": "German"}, ctx=None
    )
    assert res.success
    assert profile.get("identity", "primary_language") == "German"


@pytest.mark.asyncio
async def test_list_field_ignores_set_operation_and_appends(tmp_path):
    """Even if the model passes operation=set on a list field, we append (never
    clobber the list into a scalar)."""
    _path, profile = _load(tmp_path)
    res = await _tool(profile).execute(
        {"cluster": "identity", "field": "languages", "value": "German",
         "operation": "set"},
        ctx=None,
    )
    assert res.success
    assert profile.get("identity", "languages") == ["English", "German"]
