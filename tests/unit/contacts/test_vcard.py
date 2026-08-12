"""Tests for the vCard import/export module (jarvis/contacts/vcard.py).

Pins the contract the REST import/export endpoints rely on:
- export → parse round-trips every field the contact model carries,
- import merges into existing contacts instead of clobbering them,
- broken values (bad e-mail/phone/birthday) are dropped value-by-value,
- vCard mechanics: line unfolding, TEXT escaping, base64 blob skipping,
  compact BDAY normalisation, N-fallback when FN is missing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.contacts.store import ContactStore
from jarvis.contacts.vcard import (
    contact_to_vcard,
    contacts_to_vcf,
    import_records,
    parse_vcf,
)


@pytest.fixture()
def store(tmp_path: Path) -> ContactStore:
    return ContactStore(base_dir=tmp_path / "contacts")


def _full_contact(store: ContactStore):
    return store.put(
        name="Christoph Meyer",
        aliases=["Chris"],
        relationship="friend",
        favorite=True,
        birthday="1990-04-12",
        organization="ACME GmbH",
        role="CTO",
        urls=["https://example.com"],
        tags=["tennis", "uni"],
        emails=["christoph@example.com"],
        phones=["+49 151 2345 6789"],
        address={
            "street": "Main St 1",
            "postal_code": "10115",
            "city": "Berlin",
            "country": "DE",
        },
        note="My oldest friend.\nLikes tennis, obviously.",
    )


# ----------------------------------------------------------------------
# Export
# ----------------------------------------------------------------------


def test_export_contains_every_field(store: ContactStore) -> None:
    card = contact_to_vcard(_full_contact(store))
    assert card.startswith("BEGIN:VCARD")
    assert card.endswith("END:VCARD")
    assert "VERSION:3.0" in card
    assert "FN:Christoph Meyer" in card
    assert "NICKNAME:Chris" in card
    assert "ORG:ACME GmbH" in card
    assert "TITLE:CTO" in card
    assert "BDAY:1990-04-12" in card
    assert "EMAIL;TYPE=INTERNET:christoph@example.com" in card
    assert "TEL;TYPE=VOICE:+4915123456789" in card
    assert "URL:https://example.com" in card
    assert "CATEGORIES:tennis,uni" in card
    # The note's newline is escaped, commas escaped (TEXT escaping).
    assert "NOTE:My oldest friend.\\nLikes tennis\\, obviously." in card
    # Address components land in the right ADR slots.
    assert "ADR;TYPE=HOME:;;Main St 1;Berlin;;10115;DE" in card


def test_export_empty_book_is_empty_string(store: ContactStore) -> None:
    assert contacts_to_vcf(store.list_all()) == ""


# ----------------------------------------------------------------------
# Round-trip
# ----------------------------------------------------------------------


def test_export_then_import_roundtrips(store: ContactStore, tmp_path: Path) -> None:
    _full_contact(store)
    vcf = contacts_to_vcf(store.list_all())

    other = ContactStore(base_dir=tmp_path / "other")
    stats = import_records(other, parse_vcf(vcf))
    assert stats == {"created": 1, "updated": 0, "skipped": 0, "errors": []}

    c = other.get("christoph_meyer")
    assert c is not None
    assert c.aliases == ["Chris"]
    assert c.birthday == "1990-04-12"
    assert c.organization == "ACME GmbH"
    assert c.role == "CTO"
    assert c.urls == ["https://example.com"]
    assert c.tags == ["tennis", "uni"]
    assert c.emails == ["christoph@example.com"]
    assert c.phones == ["+4915123456789"]
    assert c.address["city"] == "Berlin"
    assert c.address["postal_code"] == "10115"
    assert "Likes tennis, obviously." in c.note_md


# ----------------------------------------------------------------------
# Parse mechanics
# ----------------------------------------------------------------------


def test_parse_unfolds_continuation_lines() -> None:
    vcf = "BEGIN:VCARD\nVERSION:3.0\nFN:Anna Long\n NAME Continued\nEND:VCARD\n"
    records = parse_vcf(vcf)
    assert records[0]["name"] == "Anna LongNAME Continued"


def test_parse_falls_back_to_n_when_fn_missing() -> None:
    vcf = "BEGIN:VCARD\nN:Meyer;Christoph;;;\nEND:VCARD\n"
    assert parse_vcf(vcf)[0]["name"] == "Christoph Meyer"


def test_parse_skips_blobs_and_cards_without_name() -> None:
    vcf = (
        "BEGIN:VCARD\nPHOTO;ENCODING=b:AAAA\nEND:VCARD\n"
        "BEGIN:VCARD\nFN:Real Person\nPHOTO;ENCODING=b:AAAA\nEND:VCARD\n"
    )
    records = parse_vcf(vcf)
    assert len(records) == 1
    assert records[0]["name"] == "Real Person"


def test_parse_normalises_compact_bday_and_drops_invalid() -> None:
    vcf = "BEGIN:VCARD\nFN:A\nBDAY:19900412\nEND:VCARD\n"
    assert parse_vcf(vcf)[0]["birthday"] == "1990-04-12"
    vcf_bad = "BEGIN:VCARD\nFN:B\nBDAY:--0412\nEND:VCARD\n"
    assert parse_vcf(vcf_bad)[0]["birthday"] is None


def test_parse_strips_group_prefixes() -> None:
    vcf = "BEGIN:VCARD\nFN:A\nitem1.TEL;TYPE=CELL:+49151\nEND:VCARD\n"
    assert parse_vcf(vcf)[0]["phones"] == ["+49151"]


# ----------------------------------------------------------------------
# Import merge semantics
# ----------------------------------------------------------------------


def test_import_merges_into_existing_without_clobbering(store: ContactStore) -> None:
    store.put(
        name="Christoph Meyer",
        relationship="friend",
        favorite=True,
        organization="Existing Org",
        emails=["old@example.com"],
        note="Curated bio.",
    )
    vcf = (
        "BEGIN:VCARD\nFN:Christoph Meyer\nORG:Imported Org\n"
        "EMAIL:new@example.com\nNOTE:Imported note\nCATEGORIES:import\nEND:VCARD\n"
    )
    stats = import_records(store, parse_vcf(vcf))
    assert stats["updated"] == 1 and stats["created"] == 0

    c = store.get("christoph_meyer")
    assert c is not None
    # Lists union; scalars only fill when empty; curated data survives.
    assert c.emails == ["old@example.com", "new@example.com"]
    assert c.organization == "Existing Org"
    assert c.note_md.strip() == "Curated bio."
    assert c.tags == ["import"]
    assert c.favorite is True
    assert c.relationship == "friend"


def test_import_drops_invalid_values_but_keeps_the_record(store: ContactStore) -> None:
    vcf = (
        "BEGIN:VCARD\nFN:Messy Card\nEMAIL:not-an-email\nEMAIL:ok@example.com\n"
        "TEL:abc\nTEL:+49 151 1\nEND:VCARD\n"
    )
    stats = import_records(store, parse_vcf(vcf))
    assert stats["created"] == 1
    c = store.get("messy_card")
    assert c is not None
    assert c.emails == ["ok@example.com"]
    assert c.phones == ["+491511"]
