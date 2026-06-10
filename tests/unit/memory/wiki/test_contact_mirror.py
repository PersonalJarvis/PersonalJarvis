"""ContactWikiMirror: sync/archive/reconcile through the real AtomicWriter."""
from __future__ import annotations

import pytest

from jarvis.contacts.store import ContactStore
from jarvis.memory.wiki.atomic_writer import AtomicWriter
from jarvis.memory.wiki.contact_mirror import ContactWikiMirror
from jarvis.memory.wiki.page import MarkdownPageRepository


@pytest.fixture()
def env(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    store = ContactStore(base_dir=tmp_path / "contacts")
    writer = AtomicWriter(
        vault_root=vault,
        backup_dir=tmp_path / "backups",
        concurrent_edit_lock_seconds=0.0,  # rapid test writes must not be skipped
    )
    mirror = ContactWikiMirror(
        vault_root=vault,
        writer=writer,
        repo=MarkdownPageRepository(),
        store=store,
        retry_delay_s=0.05,
    )
    return vault, store, mirror


async def test_sync_creates_person_page(env):
    vault, store, mirror = env
    contact = store.put(name="Christoph Meyer", relationship="friend")
    assert await mirror.sync(contact.slug) is True
    page = vault / "people" / f"{contact.slug}.md"
    assert page.exists()
    text = page.read_text(encoding="utf-8")
    assert "Christoph Meyer" in text and "friend" in text


async def test_sync_unknown_slug_is_noop(env):
    vault, store, mirror = env
    assert await mirror.sync("ghost") is False
    assert not (vault / "people" / "ghost.md").exists()


async def test_sync_twice_second_is_noop(env):
    vault, store, mirror = env
    contact = store.put(name="Laura")
    assert await mirror.sync(contact.slug) is True
    assert await mirror.sync(contact.slug) is False  # block unchanged → no write


async def test_resync_after_edit_preserves_learned_content(env):
    vault, store, mirror = env
    contact = store.put(name="Christoph Meyer", note="First note.")
    await mirror.sync(contact.slug)
    page = vault / "people" / f"{contact.slug}.md"
    page.write_text(
        page.read_text(encoding="utf-8") + "\n- Learned: birthday in August\n",
        encoding="utf-8",
    )
    store.update(contact.slug, note="Second note.")
    assert await mirror.sync(contact.slug) is True
    text = page.read_text(encoding="utf-8")
    assert "Second note." in text
    assert "birthday in August" in text


async def test_archive_moves_page_to_archive(env):
    vault, store, mirror = env
    contact = store.put(name="Tom")
    await mirror.sync(contact.slug)
    assert await mirror.archive(contact.slug) is True
    assert not (vault / "people" / f"{contact.slug}.md").exists()
    archived = list((vault / "_archive").rglob(f"{contact.slug}*.md"))
    assert archived, "archived page must survive somewhere under _archive/"


async def test_archive_missing_page_is_noop(env):
    vault, store, mirror = env
    assert await mirror.archive("ghost") is False


async def test_reconcile_heals_missing_and_is_idempotent(env):
    vault, store, mirror = env
    store.put(name="Christoph Meyer")
    store.put(name="Laura")
    assert await mirror.reconcile_all() == 2
    assert await mirror.reconcile_all() == 0


async def test_on_contact_changed_routes_actions(env):
    vault, store, mirror = env
    from jarvis.core.events import ContactChanged

    contact = store.put(name="Laura")
    await mirror.on_contact_changed(
        ContactChanged(action="created", slug=contact.slug, name="Laura")
    )
    page = vault / "people" / f"{contact.slug}.md"
    assert page.exists()
    store.delete(contact.slug)
    await mirror.on_contact_changed(
        ContactChanged(action="deleted", slug=contact.slug, name="Laura")
    )
    assert not page.exists()


async def test_skip_due_to_recent_edit_retries_once(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    store = ContactStore(base_dir=tmp_path / "contacts")
    writer = AtomicWriter(
        vault_root=vault,
        backup_dir=tmp_path / "backups",
        concurrent_edit_lock_seconds=0.2,
    )
    mirror = ContactWikiMirror(
        vault_root=vault,
        writer=writer,
        repo=MarkdownPageRepository(),
        store=store,
        retry_delay_s=0.3,
    )
    contact = store.put(name="Laura", note="v1")
    assert await mirror.sync(contact.slug) is True
    store.update(contact.slug, note="v2")
    assert await mirror.sync(contact.slug) is True  # skipped → waits → retried
    text = (vault / "people" / f"{contact.slug}.md").read_text(encoding="utf-8")
    assert "v2" in text
