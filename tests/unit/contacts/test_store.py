"""Unit tests for ``ContactStore`` (Chunk A — Contract 1, frozen surface).

Contract 1 (jarvis/contacts/store.py — do NOT change these signatures, Chunk B
consumes them):

    list_all() -> list[Contact]
    get(slug) -> Contact | None
    find_by_alias(query) -> Contact | None
    upsert(*, name, relationship=None, email=None, phone=None, address=None,
           note=None) -> Contact          # deterministic voice-write (merge one)
    delete(slug) -> bool
    render_for_prompt(*, max_chars=800) -> str

Contact exposes: .name .slug .aliases .relationship .emails[] .phones[]
                 .address(dict) .note_md (+ .primary_email / .primary_phone)

Storage is one ``<slug>.md`` per contact (YAML frontmatter + Markdown body),
written atomically. Tests use an injected ``base_dir`` (a tmp dir), which is not
part of the frozen contract — only the methods above are.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.contacts.store import Contact, ContactStore


@pytest.fixture()
def store(tmp_path: Path) -> ContactStore:
    return ContactStore(base_dir=tmp_path / "contacts")


# ----------------------------------------------------------------------
# upsert + round-trip
# ----------------------------------------------------------------------


def test_upsert_creates_and_roundtrips(store: ContactStore) -> None:
    c = store.upsert(
        name="Christoph Meyer",
        relationship="friend",
        email="christoph@example.com",
        phone="+49 151 2345 6789",
        note="My oldest friend.",
    )
    assert isinstance(c, Contact)
    assert c.name == "Christoph Meyer"
    assert c.slug == "christoph_meyer"
    assert c.relationship == "friend"
    assert c.emails == ["christoph@example.com"]
    assert c.phones == ["+4915123456789"]  # E.164-normalised (separators stripped)
    assert c.note_md.strip() == "My oldest friend."

    # Survives a fresh store instance (real file persistence).
    fresh = ContactStore(base_dir=store.base_dir)
    again = fresh.get("christoph_meyer")
    assert again is not None
    assert again.primary_email == "christoph@example.com"
    assert again.primary_phone == "+4915123456789"


def test_upsert_twice_updates_same_record_and_merges(store: ContactStore) -> None:
    store.upsert(name="Christoph", email="a@example.com", phone="+49 1")
    store.upsert(name="Christoph", email="b@example.com", phone="+49 2")
    contacts = store.list_all()
    assert len(contacts) == 1  # same person, not a duplicate
    c = contacts[0]
    assert c.emails == ["a@example.com", "b@example.com"]
    assert c.phones == ["+491", "+492"]


def test_upsert_does_not_duplicate_same_email_case_insensitive(store: ContactStore) -> None:
    store.upsert(name="Anna", email="anna@example.com")
    store.upsert(name="Anna", email="ANNA@example.com")
    c = store.get("anna")
    assert c is not None
    assert c.emails == ["anna@example.com"]


def test_upsert_sets_address_dict(store: ContactStore) -> None:
    c = store.upsert(
        name="Bob",
        address={
            "street": "Main St 1",
            "postal_code": "10115",
            "city": "Berlin",
            "country": "DE",
        },
    )
    assert c.address["city"] == "Berlin"
    assert store.get("bob").address["postal_code"] == "10115"


def test_upsert_rejects_unknown_relationship(store: ContactStore) -> None:
    with pytest.raises(ValueError):
        store.upsert(name="X", relationship="enemy")


def test_upsert_rejects_malformed_email(store: ContactStore) -> None:
    with pytest.raises(ValueError):
        store.upsert(name="X", email="not-an-email")


def test_upsert_rejects_phone_without_digits(store: ContactStore) -> None:
    with pytest.raises(ValueError):
        store.upsert(name="X", phone="abc")


# ----------------------------------------------------------------------
# find_by_alias
# ----------------------------------------------------------------------


def test_find_by_alias_matches_name_case_insensitive(store: ContactStore) -> None:
    store.upsert(name="Christoph Meyer", relationship="friend")
    assert store.find_by_alias("christoph meyer") is not None
    assert store.find_by_alias("CHRISTOPH MEYER").slug == "christoph_meyer"


def test_find_by_alias_matches_alias(store: ContactStore) -> None:
    store.put(name="Christoph Meyer", aliases=["Chris", "Chrissi"])
    found = store.find_by_alias("chrissi")
    assert found is not None
    assert found.name == "Christoph Meyer"


def test_find_by_alias_unknown_returns_none(store: ContactStore) -> None:
    store.upsert(name="Anna")
    assert store.find_by_alias("Zoe") is None


# ----------------------------------------------------------------------
# put (full-record create/replace, used by the CRUD routes)
# ----------------------------------------------------------------------


def test_put_creates_full_record(store: ContactStore) -> None:
    c = store.put(
        name="Dana Smith",
        aliases=["Dan"],
        relationship="colleague",
        emails=["dana@work.com", "dana@home.com"],
        phones=["+49 30 1", "+49 30 2"],
        address={"city": "Hamburg"},
        note="Works in the Hamburg office.",
    )
    assert c.slug == "dana_smith"
    assert c.aliases == ["Dan"]
    assert c.emails == ["dana@work.com", "dana@home.com"]
    assert c.phones == ["+49301", "+49302"]
    assert c.address["city"] == "Hamburg"
    assert "Hamburg office" in c.note_md


def test_put_with_explicit_slug_replaces_in_place(store: ContactStore) -> None:
    created = store.put(name="Eve", emails=["eve@example.com"])
    replaced = store.put(slug=created.slug, name="Eve Adams", emails=["eve.adams@example.com"])
    assert replaced.slug == created.slug
    assert store.get(created.slug).name == "Eve Adams"
    assert store.get(created.slug).emails == ["eve.adams@example.com"]
    assert len(store.list_all()) == 1


def test_put_two_same_name_contacts_get_unique_slugs(store: ContactStore) -> None:
    a = store.put(name="John")
    b = store.put(name="John")
    assert a.slug != b.slug
    assert len(store.list_all()) == 2


# ----------------------------------------------------------------------
# delete
# ----------------------------------------------------------------------


def test_delete_returns_true_then_false(store: ContactStore) -> None:
    store.upsert(name="Temp")
    assert store.delete("temp") is True
    assert store.get("temp") is None
    assert store.delete("temp") is False


# ----------------------------------------------------------------------
# render_for_prompt
# ----------------------------------------------------------------------


def test_render_for_prompt_lists_names_and_relationship(store: ContactStore) -> None:
    store.upsert(name="Christoph", relationship="friend")
    store.upsert(name="Laura", relationship="partner")
    block = store.render_for_prompt()
    assert "Christoph" in block
    assert "Laura" in block
    assert "friend" in block
    assert "partner" in block
    # Detail (emails/phones) is fetched on demand, not injected into the prompt.
    store.upsert(name="Christoph", email="secret@example.com")
    assert "secret@example.com" not in store.render_for_prompt()


def test_render_for_prompt_empty_when_no_contacts(store: ContactStore) -> None:
    assert store.render_for_prompt() == ""


def test_render_for_prompt_respects_max_chars(store: ContactStore) -> None:
    for i in range(50):
        store.upsert(name=f"Person {i}", relationship="other")
    out = store.render_for_prompt(max_chars=200)
    assert len(out) <= 200


# ----------------------------------------------------------------------
# slug + helpers
# ----------------------------------------------------------------------


def test_slug_handles_umlauts(store: ContactStore) -> None:
    c = store.upsert(name="Jörg Müller")
    assert c.slug == "joerg_mueller"


def test_primary_helpers_none_when_empty(store: ContactStore) -> None:
    c = store.upsert(name="NoContactInfo")
    assert c.primary_email is None
    assert c.primary_phone is None
