"""Unit tests for the ``contact-upsert`` router tool (Chunk B).

``contact-upsert`` is the deterministic voice-write path: "merk dir, Christophs
Nummer ist …" -> the brain fills structured args and the tool writes via
``ContactStore.upsert`` (Contract 1). Risk tier ``monitor`` — a logged write,
run without a confirmation nag (anti-confirmation-fatigue), mirroring
``wiki-ingest``. Deletion stays UI-only in v1.

Contract 1 is stubbed here (a fake store recording the upsert kwargs) so B is
testable before Chunk A merges.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from jarvis.plugins.tool.contact_upsert import ContactUpsertTool


@dataclass
class _FakeContact:
    name: str
    slug: str = "christoph"
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)


class _RecordingStore:
    """Records upsert() kwargs and returns a fake Contact (Contract 1 shape)."""

    def __init__(self) -> None:
        self.upserts: list[dict[str, Any]] = []

    def upsert(
        self,
        *,
        name: str,
        relationship: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        address: Any = None,
        note: str | None = None,
    ) -> _FakeContact:
        kwargs = {
            "name": name,
            "relationship": relationship,
            "email": email,
            "phone": phone,
            "address": address,
            "note": note,
        }
        self.upserts.append(kwargs)
        return _FakeContact(
            name=name,
            emails=[email] if email else [],
            phones=[phone] if phone else [],
        )


def _tool(store: Any) -> ContactUpsertTool:
    return ContactUpsertTool(store_resolver=lambda: store)


def test_risk_tier_is_monitor() -> None:
    """A logged write with no confirm nag — monitor, not safe (it mutates) and
    not ask (anti-confirmation-fatigue for a voice 'merk dir')."""
    assert ContactUpsertTool(store_resolver=lambda: None).risk_tier == "monitor"


def test_name_is_contact_upsert() -> None:
    assert ContactUpsertTool(store_resolver=lambda: None).name == "contact-upsert"


@pytest.mark.asyncio
async def test_upsert_writes_via_store_contract() -> None:
    """The tool forwards structured args verbatim to ``ContactStore.upsert``."""
    store = _RecordingStore()
    result = await _tool(store).execute(
        {"name": "Christoph", "phone": "+4915112345678", "relationship": "friend"},
        None,
    )
    assert result.success is True
    assert len(store.upserts) == 1
    call = store.upserts[0]
    assert call["name"] == "Christoph"
    assert call["phone"] == "+4915112345678"
    assert call["relationship"] == "friend"


@pytest.mark.asyncio
async def test_upsert_passes_optional_fields_through() -> None:
    store = _RecordingStore()
    await _tool(store).execute(
        {
            "name": "Laura",
            "email": "laura@example.com",
            "address": "Hauptstr. 1, Berlin",
            "note": "colleague from work",
        },
        None,
    )
    call = store.upserts[0]
    assert call["email"] == "laura@example.com"
    assert call["address"] == "Hauptstr. 1, Berlin"
    assert call["note"] == "colleague from work"


@pytest.mark.asyncio
async def test_missing_name_is_an_error_and_does_not_write() -> None:
    store = _RecordingStore()
    result = await _tool(store).execute({"phone": "+4915112345678"}, None)
    assert result.success is False
    assert result.error
    assert store.upserts == []


@pytest.mark.asyncio
async def test_no_fields_to_write_is_an_error() -> None:
    """A name with no contact data at all is a no-op write — reject it so the
    brain does not claim it saved something it did not."""
    store = _RecordingStore()
    result = await _tool(store).execute({"name": "Christoph"}, None)
    assert result.success is False
    assert store.upserts == []


@pytest.mark.asyncio
async def test_store_unavailable_degrades_gracefully() -> None:
    result = await ContactUpsertTool(store_resolver=lambda: None).execute(
        {"name": "Christoph", "phone": "+4915112345678"}, None
    )
    assert result.success is False
    assert result.error
