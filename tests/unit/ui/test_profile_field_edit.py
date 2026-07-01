"""Tests for PATCH /api/profile/field — inline single-field editing from the
Profile view (the pencil-per-field UI).

Contract (see jarvis/ui/web/profile_routes.py::patch_field):
- ``set`` overwrites a scalar field in place (boss → king)
- ``clear`` empties any field so it reads back as "not known yet"
- ``append`` / ``remove`` add/drop one item of a list field (the chips)
- the canonical field allow-list (shared with update_profile) is enforced
- list-only vs scalar-only operations are rejected with 400
- a ProfileUpdated event is emitted for live UI sync
- the change is persisted atomically to USER.md
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core.events import ProfileUpdated
from jarvis.memory.user_profile import UserProfile
from jarvis.ui.web.profile_routes import router

INITIAL = """\
---
identity:
  name: Alex
  preferred_address: Chef
  languages:
    - English
communication:
  emoji_ok: false
values:
  top_values:
    - Pizza
---

# USER.md

## Observations over time

<!-- curator:observations:start -->
<!-- curator:observations:end -->
"""


class _CapturingBus:
    """Records published events; the endpoint only needs .publish."""

    def __init__(self) -> None:
        self.published: list[object] = []

    async def publish(self, event: object) -> None:
        self.published.append(event)


@pytest.fixture()
def ctx(tmp_path: Path):
    user_md = tmp_path / "USER.md"
    user_md.write_text(INITIAL, encoding="utf-8")
    profile = UserProfile.load(user_md)
    bus = _CapturingBus()

    app = FastAPI()
    app.include_router(router)
    app.state.brain = types.SimpleNamespace(
        _user_profile=profile,
        _curator=None,
        _people=None,
        _bus=bus,
    )
    return types.SimpleNamespace(client=TestClient(app), profile=profile, bus=bus, path=user_md)


def _patch(client: TestClient, **body):
    return client.patch("/api/profile/field", json=body)


# ----------------------------------------------------------------------
# set — overwrite a scalar
# ----------------------------------------------------------------------

def test_set_scalar_overwrites_chef_to_koenig(ctx) -> None:
    res = _patch(ctx.client, cluster="identity", field="preferred_address",
                 operation="set", value="König")  # i18n-allow
    assert res.status_code == 200, res.text
    assert res.json()["changed"] is True
    # In-memory + the read endpoint reflect it.
    assert ctx.profile.get("identity", "preferred_address") == "König"  # i18n-allow
    meta = ctx.client.get("/api/profile").json()["user"]["meta"]
    assert meta["identity"]["preferred_address"] == "König"  # i18n-allow


def test_set_scalar_persists_to_disk(ctx) -> None:
    _patch(ctx.client, cluster="identity", field="name", operation="set", value="Paul")
    # A fresh load from disk sees the write (atomic persist happened).
    assert UserProfile.load(ctx.path).get("identity", "name") == "Paul"


def test_set_emits_profile_updated(ctx) -> None:
    _patch(ctx.client, cluster="identity", field="name", operation="set", value="Paul")
    events = [e for e in ctx.bus.published if isinstance(e, ProfileUpdated)]
    assert len(events) == 1
    assert events[0].cluster == "identity"
    assert events[0].field == "name"
    assert events[0].operation == "set"


def test_set_same_value_reports_no_change(ctx) -> None:
    res = _patch(ctx.client, cluster="identity", field="name",
                 operation="set", value="Alex")
    assert res.status_code == 200
    assert res.json()["changed"] is False


def test_set_bool_field_coerces_string(ctx) -> None:
    res = _patch(ctx.client, cluster="communication", field="emoji_ok",
                 operation="set", value="true")
    assert res.status_code == 200, res.text
    assert ctx.profile.get("communication", "emoji_ok") is True


# ----------------------------------------------------------------------
# clear — empty a field
# ----------------------------------------------------------------------

def test_clear_scalar_field(ctx) -> None:
    res = _patch(ctx.client, cluster="identity", field="preferred_address",
                 operation="clear")
    assert res.status_code == 200, res.text
    assert ctx.profile.get("identity", "preferred_address") is None


def test_clear_list_field(ctx) -> None:
    res = _patch(ctx.client, cluster="values", field="top_values", operation="clear")
    assert res.status_code == 200, res.text
    assert not ctx.profile.get("values", "top_values")


# ----------------------------------------------------------------------
# append / remove — list items (the chips)
# ----------------------------------------------------------------------

def test_append_list_item(ctx) -> None:
    res = _patch(ctx.client, cluster="identity", field="languages",
                 operation="append", value="Deutsch")
    assert res.status_code == 200, res.text
    assert ctx.profile.get("identity", "languages") == ["English", "Deutsch"]


def test_append_duplicate_reports_no_change(ctx) -> None:
    res = _patch(ctx.client, cluster="identity", field="languages",
                 operation="append", value="English")
    assert res.status_code == 200
    assert res.json()["changed"] is False
    assert ctx.profile.get("identity", "languages") == ["English"]


def test_remove_list_item(ctx) -> None:
    ctx.profile.append_list("identity", "languages", "Deutsch")
    ctx.profile.save()
    res = _patch(ctx.client, cluster="identity", field="languages",
                 operation="remove", value="English")
    assert res.status_code == 200, res.text
    assert ctx.profile.get("identity", "languages") == ["Deutsch"]


# ----------------------------------------------------------------------
# validation
# ----------------------------------------------------------------------

def test_unknown_cluster_rejected(ctx) -> None:
    res = _patch(ctx.client, cluster="hobbies", field="name",
                 operation="set", value="x")
    assert res.status_code == 400


def test_unknown_field_rejected(ctx) -> None:
    res = _patch(ctx.client, cluster="identity", field="shoe_size",
                 operation="set", value="44")
    assert res.status_code == 400


def test_set_on_list_field_rejected(ctx) -> None:
    # A list field must use append/remove, never set (would clobber the list).
    res = _patch(ctx.client, cluster="identity", field="languages",
                 operation="set", value="Deutsch")
    assert res.status_code == 400


def test_append_on_scalar_field_rejected(ctx) -> None:
    res = _patch(ctx.client, cluster="identity", field="name",
                 operation="append", value="X")
    assert res.status_code == 400


def test_set_without_value_rejected(ctx) -> None:
    res = _patch(ctx.client, cluster="identity", field="name", operation="set")
    assert res.status_code == 400


def test_invalid_operation_rejected(ctx) -> None:
    res = _patch(ctx.client, cluster="identity", field="name",
                 operation="frobnicate", value="x")
    assert res.status_code == 422  # Pydantic Literal rejects it before the handler
