"""ContactStore fires the notify seam on every successful write."""
from __future__ import annotations

import pytest

from jarvis.contacts import notify
from jarvis.contacts.store import ContactStore


@pytest.fixture(autouse=True)
def _clean_sink():
    notify.clear_contact_change_sink()
    yield
    notify.clear_contact_change_sink()


@pytest.fixture()
def seen():
    events: list[tuple[str, str, str]] = []
    notify.set_contact_change_sink(lambda a, s, n: events.append((a, s, n)))
    return events


def test_put_create_emits_created(tmp_path, seen):
    store = ContactStore(base_dir=tmp_path)
    contact = store.put(name="Christoph Meyer")
    assert seen == [("created", contact.slug, "Christoph Meyer")]


def test_update_emits_updated(tmp_path, seen):
    store = ContactStore(base_dir=tmp_path)
    contact = store.put(name="Christoph Meyer")
    store.update(contact.slug, note="Old friend from school.")
    assert seen[-1] == ("updated", contact.slug, "Christoph Meyer")


def test_upsert_existing_emits_updated(tmp_path, seen):
    store = ContactStore(base_dir=tmp_path)
    store.put(name="Christoph Meyer", aliases=["Chris"])
    store.upsert(name="Chris", phone="+49 151 2345678")
    assert seen[-1][0] == "updated"


def test_upsert_new_emits_created(tmp_path, seen):
    store = ContactStore(base_dir=tmp_path)
    store.upsert(name="Laura", email="laura@example.com")
    assert seen[-1][0] == "created"


def test_delete_emits_deleted_with_name(tmp_path, seen):
    store = ContactStore(base_dir=tmp_path)
    contact = store.put(name="Christoph Meyer")
    assert store.delete(contact.slug) is True
    assert seen[-1] == ("deleted", contact.slug, "Christoph Meyer")


def test_delete_missing_emits_nothing(tmp_path, seen):
    store = ContactStore(base_dir=tmp_path)
    assert store.delete("ghost") is False
    assert seen == []


def test_failed_put_emits_nothing(tmp_path, seen):
    store = ContactStore(base_dir=tmp_path)
    with pytest.raises(ValueError):
        store.put(name="Broken", emails=["not-an-email"])
    assert seen == []
