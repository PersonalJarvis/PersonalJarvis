"""Unit tests for the ``contact-lookup`` router tool (Chunk B).

Chunk B is the integrator: it consumes Contract 1 (``ContactStore``, owned by
Chunk A). Until Chunk A lands, B builds against the frozen interface and stubs
it here — a tiny fake store + fake contact whose shape mirrors the contract
exactly (``find_by_alias`` + ``.name/.emails/.phones/.address/.note_md`` and the
``primary_email``/``primary_phone`` helpers).

``contact-lookup`` is the read-only resolver: name/alias -> the contact's
e-mails, phones, address and README. Risk tier ``safe`` (a pure read, the brain
calls it freely before e-mailing or calling by name).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from jarvis.plugins.tool.contact_lookup import ContactLookupTool


# --------------------------------------------------------------------------- #
# Contract-1 stubs (frozen interface; real impl owned by Chunk A)
# --------------------------------------------------------------------------- #
@dataclass
class _FakeContact:
    name: str
    slug: str = "christoph"
    aliases: tuple[str, ...] = ()
    relationship: str | None = None
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    address: dict[str, str] = field(default_factory=dict)
    note_md: str = ""

    @property
    def primary_email(self) -> str | None:
        return self.emails[0] if self.emails else None

    @property
    def primary_phone(self) -> str | None:
        return self.phones[0] if self.phones else None


class _FakeStore:
    def __init__(self, contacts: list[_FakeContact]) -> None:
        self._by_name = {c.name.strip().lower(): c for c in contacts}

    def find_by_alias(self, query: str) -> _FakeContact | None:
        return self._by_name.get((query or "").strip().lower())


def _tool(store: Any) -> ContactLookupTool:
    return ContactLookupTool(store_resolver=lambda: store)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_risk_tier_is_safe() -> None:
    """A pure read must be safe-tier so the brain calls it without a confirm
    nag (anti-confirmation-fatigue; the read has no side effect)."""
    assert ContactLookupTool(store_resolver=lambda: None).risk_tier == "safe"


def test_name_is_contact_lookup() -> None:
    assert ContactLookupTool(store_resolver=lambda: None).name == "contact-lookup"


@pytest.mark.asyncio
async def test_lookup_returns_contact_details() -> None:
    """A resolvable name returns the contact's e-mail, phone and address so the
    brain can chain into gmail / call-contact."""
    store = _FakeStore([
        _FakeContact(
            name="Christoph",
            relationship="friend",
            emails=["christoph@example.com"],
            phones=["+4915112345678"],
            address={"street": "Hauptstr. 1", "city": "Berlin"},
            note_md="Old university friend.",
        )
    ])
    result = await _tool(store).execute({"name": "Christoph"}, None)

    assert result.success is True
    out = result.output
    assert "Christoph" in out
    assert "christoph@example.com" in out
    assert "+4915112345678" in out
    assert "Berlin" in out


@pytest.mark.asyncio
async def test_lookup_resolves_via_alias_case_insensitive() -> None:
    """Resolution delegates to ``find_by_alias`` (Contract 1) — a lower-case
    query still resolves the stored contact."""
    store = _FakeStore([_FakeContact(name="Christoph", emails=["c@example.com"])])
    result = await _tool(store).execute({"name": "christoph"}, None)
    assert result.success is True
    assert "c@example.com" in result.output


@pytest.mark.asyncio
async def test_missing_name_argument_is_an_error() -> None:
    result = await _tool(_FakeStore([])).execute({}, None)
    assert result.success is False
    assert result.error


@pytest.mark.asyncio
async def test_unknown_contact_returns_clean_not_found() -> None:
    """An unknown name must fail cleanly (no exception) so the brain can tell
    the user the contact is not in the book."""
    store = _FakeStore([_FakeContact(name="Christoph")])
    result = await _tool(store).execute({"name": "Mallory"}, None)
    assert result.success is False
    assert "Mallory" in (result.error or "") or "not" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_store_unavailable_degrades_gracefully() -> None:
    """When Chunk A is not merged the resolver returns None — the tool must
    degrade to a clean error, never crash the boot or the turn."""
    result = await ContactLookupTool(store_resolver=lambda: None).execute(
        {"name": "Christoph"}, None
    )
    assert result.success is False
    assert result.error
