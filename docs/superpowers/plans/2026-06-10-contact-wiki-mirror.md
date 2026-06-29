# Contact → Wiki Person-Page Mirror Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every contact in the user-curated address book gets a guaranteed, deterministically rendered wiki person page (`people/<slug>.md`) that stays in sync on create/edit, is archived on delete, and is healed by a boot-time reconciliation — with phones/emails/addresses deliberately kept out of the vault.

**Architecture:** A module-level notification seam in `jarvis/contacts/notify.py` is the single choke point: `ContactStore.put()`/`delete()` call it after every successful write (covers REST routes AND the `contact-upsert` voice tool, which both funnel through the store). The wiki bootstrap registers a sink that publishes a new frozen `ContactChanged` event on the `EventBus`; an async subscriber (`ContactWikiMirror`) renders the page through the existing `AtomicWriter` (which keeps the FTS5 search index in sync, so `wiki-recall` finds the page). A managed block between HTML-comment markers separates mirror-owned base data from curator/human-owned learned content.

**Tech Stack:** Python 3.11, dataclasses, asyncio, existing `AtomicWriter`/`MarkdownPageRepository`/`EventBus`, pytest (asyncio_mode=auto), fakes not mocks.

**Spec:** `docs/superpowers/specs/2026-06-10-contact-wiki-mirror-design.md`

**Deviation from spec (documented back per house rule):** emission happens at the `ContactStore` level (single choke point), not separately in the REST routes and the tool — both write paths go through the store, so one hook covers both plus any future path. Task 9 amends the spec.

**Key API facts (verified):**
- `EventBus.publish` is async; typed handlers MUST be `async def` (sync handlers are silently swallowed — known house gotcha).
- `Event` base: `trace_id`/`timestamp_ns` have default factories, `source_layer: str = ""` (`jarvis/core/events.py:31-36`). All subclass fields need defaults (frozen, slots).
- `AtomicWriter(vault_root, backup_dir, *, concurrent_edit_lock_seconds=...)`; `await writer.apply(updates, repo=repo)` → `WriteResult(applied, skipped_due_to_recent_edit, failed_validation, backup_path, blocked_pii)`. It maintains the `wiki_fts` index for applied/archived pages.
- **30-second concurrent-edit lock:** a page written <30 s ago is SKIPPED on the next apply. The mirror itself triggers this on rapid successive edits → the mirror must retry once after a delay (injectable for tests).
- `PageUpdate(target_path, operation, new_body, rename_from=None, reason="")`, operation ∈ create|update|rename|archive (`jarvis/memory/wiki/protocols.py:39-51`). Archive op moves the page out of the live vault (see `_do_archive`, `jarvis/memory/wiki/atomic_writer.py:464` — read it once before Task 5 to confirm the destination folder name for the test assertion).
- `MarkdownPageRepository()` from `jarvis/memory/wiki/page.py` is the real repo used by tests (`tests/unit/memory/wiki/test_consolidator.py:36,123`).
- `ContactStore(base_dir=tmp_path)` is test-injectable; `put(slug=None → create)`, `update()` and `upsert()` both funnel into `put(slug=...)`, `delete(slug)` unlinks.
- `parse_frontmatter(text) -> (meta, body)` / `write_frontmatter(meta, body) -> str` from `jarvis.memory.frontmatter` (already used by the contact store).
- `bootstrap_wiki_integration` (`jarvis/memory/wiki/integration.py:186`) has `bus`, `repo`, `vault_path`; the curator's `AtomicWriter` is built inside `_build_curator` (`integration.py:673-680`) — Task 7 lifts it out so mirror + curator share one writer (one serial lock, one FTS connection).
- `WikiIntegrationHandle` (`integration.py:91`) is a dataclass with optional fields + `shutdown()`.

**Conventions that apply:** English-only artifacts; TDD; `pwsh`/`powershell scripts/preflight.ps1` already green; commit only files this plan touches (parallel sessions have unrelated dirty files — NEVER `git add -A`); `jarvis/core/events.py` is dirty from a parallel session — append the new event class at a clean location and stage only hunks belonging to this feature is NOT possible with plain `git add`, so: events.py is currently modified — coordinate by appending the class and committing the whole file is forbidden; instead use `git add -p`-free approach: verify with `git diff jarvis/core/events.py` that the only NEW hunk is ours; if foreign hunks exist, commit must wait — see Task 2 Step 5 note.

---

### Task 1: Contact-change notification seam (`jarvis/contacts/notify.py`)

**Files:**
- Create: `jarvis/contacts/notify.py`
- Test: `tests/unit/contacts/test_notify.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the contact-change notification seam (jarvis/contacts/notify.py)."""
from __future__ import annotations

import pytest

from jarvis.contacts import notify


@pytest.fixture(autouse=True)
def _clean_sink():
    notify.clear_contact_change_sink()
    yield
    notify.clear_contact_change_sink()


def test_actions_vocabulary_is_frozen():
    assert notify.CONTACT_CHANGE_ACTIONS == ("created", "updated", "deleted")


def test_notify_without_sink_is_noop():
    notify.notify_contact_changed("created", "chris", "Chris")  # must not raise


def test_sink_receives_notification():
    seen: list[tuple[str, str, str]] = []
    notify.set_contact_change_sink(lambda a, s, n: seen.append((a, s, n)))
    notify.notify_contact_changed("created", "chris", "Chris")
    assert seen == [("created", "chris", "Chris")]


def test_sink_error_is_swallowed():
    def boom(action: str, slug: str, name: str) -> None:
        raise RuntimeError("sink exploded")

    notify.set_contact_change_sink(boom)
    notify.notify_contact_changed("updated", "chris", "Chris")  # must not raise


def test_unknown_action_is_dropped():
    seen: list[str] = []
    notify.set_contact_change_sink(lambda a, s, n: seen.append(a))
    notify.notify_contact_changed("renamed", "chris", "Chris")
    assert seen == []


def test_clear_sink_stops_delivery():
    seen: list[str] = []
    notify.set_contact_change_sink(lambda a, s, n: seen.append(a))
    notify.clear_contact_change_sink()
    notify.notify_contact_changed("created", "chris", "Chris")
    assert seen == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/contacts/test_notify.py -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'jarvis.contacts.notify'` (if `tests/unit/contacts/` lacks an `__init__.py` and other test dirs have one, mirror the convention).

- [ ] **Step 3: Write the implementation**

```python
"""Contact-change notification seam — the single choke point for contact writes.

The contacts package must not import its consumers (wiki mirror today, anything
else tomorrow) — the dependency points the other way (lateral integration goes
through the EventBus). A consumer registers a sink at bootstrap; the
``ContactStore`` calls :func:`notify_contact_changed` after every successful
write. With no sink registered (unit tests, wiki disabled, headless minimal
boot) the call is a zero-overhead no-op, and a sink error must never fail the
contact write itself.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

log = logging.getLogger(__name__)

#: Single source of truth for the ``ContactChanged.action`` vocabulary.
#: Python-only wire format today; apply the five-layer anti-drift pattern
#: (docs/anti-drift-three-layer.md) before this ever crosses SQL/TS/UI.
CONTACT_CHANGE_ACTIONS: tuple[str, ...] = ("created", "updated", "deleted")

#: ``(action, slug, name)`` — action is one of :data:`CONTACT_CHANGE_ACTIONS`.
ContactChangeSink = Callable[[str, str, str], None]

_sink: ContactChangeSink | None = None


def set_contact_change_sink(sink: ContactChangeSink) -> None:
    """Register the process-wide sink (last registration wins)."""
    global _sink
    _sink = sink


def clear_contact_change_sink() -> None:
    global _sink
    _sink = None


def notify_contact_changed(action: str, slug: str, name: str) -> None:
    """Best-effort fan-out after a successful contact write."""
    if action not in CONTACT_CHANGE_ACTIONS:
        log.warning("contacts.notify: unknown action %r dropped (slug=%r)", action, slug)
        return
    sink = _sink
    if sink is None:
        return
    try:
        sink(action, slug, name)
    except Exception:  # noqa: BLE001 — a consumer error must never fail the write
        log.warning(
            "contacts.notify: sink failed for %s %r", action, slug, exc_info=True
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/contacts/test_notify.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add jarvis/contacts/notify.py tests/unit/contacts/test_notify.py
git commit -m "feat(contacts): contact-change notification seam"
```

---

### Task 2: `ContactChanged` event

**Files:**
- Modify: `jarvis/core/events.py` (append near the other domain events, e.g. after `ProfileUpdated` around line 224-240)
- Test: `tests/unit/contacts/test_contact_changed_event.py`

- [ ] **Step 1: Write the failing test**

```python
"""ContactChanged event contract: frozen, defaults, action vocabulary parity."""
from __future__ import annotations

import dataclasses

import pytest

from jarvis.contacts.notify import CONTACT_CHANGE_ACTIONS
from jarvis.core.events import ContactChanged, Event


def test_contact_changed_is_frozen_event_with_trace():
    evt = ContactChanged(action="created", slug="chris", name="Chris")
    assert isinstance(evt, Event)
    assert evt.trace_id is not None
    assert evt.timestamp_ns > 0
    with pytest.raises(dataclasses.FrozenInstanceError):
        evt.slug = "other"  # type: ignore[misc]


def test_contact_changed_action_vocabulary_documented():
    # The docstring names the owning vocabulary so the two sites cannot drift
    # silently — and the canonical actions construct cleanly.
    for action in CONTACT_CHANGE_ACTIONS:
        assert ContactChanged(action=action, slug="s", name="N").action == action
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/contacts/test_contact_changed_event.py -v`
Expected: FAIL with `ImportError: cannot import name 'ContactChanged'`.

- [ ] **Step 3: Add the event class**

Append in `jarvis/core/events.py` directly after the `ProfileUpdated` class:

```python
@dataclass(frozen=True, slots=True)
class ContactChanged(Event):
    """A contact in the user-curated address book was written or removed.

    Emitted (via ``jarvis.contacts.notify``) after every successful
    ``ContactStore`` write. ``action`` vocabulary is owned by
    ``jarvis.contacts.notify.CONTACT_CHANGE_ACTIONS``:
    ``created`` | ``updated`` | ``deleted``.
    Consumed by the wiki contact mirror (deterministic person-page sync).
    """
    action: str = ""
    slug: str = ""
    name: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/contacts/test_contact_changed_event.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit (parallel-session guard)**

`jarvis/core/events.py` is dirty from a parallel session. Before staging, run
`git diff jarvis/core/events.py` and inspect. If the ONLY change is the new
`ContactChanged` hunk, stage and commit the file. If foreign hunks exist, stage
selectively (`git add -p` is unavailable in this harness — instead: `git stash push -- jarvis/core/events.py` is FORBIDDEN with parallel sessions; the safe fallback is `git diff > nul` review and committing the file anyway is FORBIDDEN). Resolution: if foreign hunks exist, commit ONLY the test file now and fold the events.py hunk into the Task 9 final commit with an explicit note in the commit body listing the foreign hunks left unstaged — or, if the foreign hunks are themselves complete and green, leave events.py uncommitted and proceed; the feature commits stay clean either way.

```bash
git add tests/unit/contacts/test_contact_changed_event.py
git diff jarvis/core/events.py   # review: ours-only → also add events.py
git add jarvis/core/events.py    # ONLY if ours-only (see above)
git commit -m "feat(events): ContactChanged event for contact write fan-out"
```

---

### Task 3: `ContactStore` emits notifications

**Files:**
- Modify: `jarvis/contacts/store.py` (import + `put()` + `delete()`)
- Test: `tests/unit/contacts/test_store_notify.py`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/contacts/test_store_notify.py -v`
Expected: the `emits` tests FAIL (`seen == []`); `test_delete_missing_emits_nothing` and `test_failed_put_emits_nothing` may already pass.

- [ ] **Step 3: Implement emission in the store**

In `jarvis/contacts/store.py`, add the import below the existing relative import (`from .schema import normalize_relationship`):

```python
from .notify import notify_contact_changed
```

In `put()` (currently `jarvis/contacts/store.py:323-377`): record whether this is a create, then notify after the successful write. Replace

```python
        if slug is None:
            slug = self._unique_slug(clean_name)
```

with

```python
        created = slug is None
        if slug is None:
            slug = self._unique_slug(clean_name)
```

and replace the tail

```python
        self._write(slug, meta, body)
        return Contact.load(self._path(slug))
```

with

```python
        self._write(slug, meta, body)
        notify_contact_changed("created" if created else "updated", slug, clean_name)
        return Contact.load(self._path(slug))
```

In `delete()` (currently `jarvis/contacts/store.py:403-409`): capture the name before unlinking, notify only on a real removal:

```python
    def delete(self, slug: str) -> bool:
        existing = self.get(slug)
        path = self._path(slug)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        notify_contact_changed(
            "deleted", slug, existing.name if existing is not None else slug
        )
        return True
```

- [ ] **Step 4: Run the new tests plus the existing contact suites**

Run: `pytest tests/unit/contacts/ tests/unit/ui/web/test_contacts_routes.py -v` (if the routes test file has a different name, find it with `pytest tests/unit/ui/web/ -k contact --collect-only -q` and run that).
Expected: all pass (no sink registered in existing tests → no-op).

- [ ] **Step 5: Commit**

```bash
git add jarvis/contacts/store.py tests/unit/contacts/test_store_notify.py
git commit -m "feat(contacts): emit change notifications from ContactStore writes"
```

---

### Task 4: Page rendering (managed block, preservation, PII exclusion)

**Files:**
- Create: `jarvis/memory/wiki/contact_mirror.py` (rendering functions only in this task)
- Test: `tests/unit/memory/wiki/test_contact_mirror_render.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Rendering for the contact mirror: managed block, preservation, PII exclusion."""
from __future__ import annotations

from jarvis.contacts.store import ContactStore
from jarvis.memory.frontmatter import parse_frontmatter
from jarvis.memory.wiki.contact_mirror import (
    MANAGED_BLOCK_END,
    MANAGED_BLOCK_START,
    extract_managed_block,
    render_managed_block,
    render_person_page,
)


def _contact(tmp_path, **kwargs):
    store = ContactStore(base_dir=tmp_path)
    defaults = {"name": "Christoph Meyer"}
    defaults.update(kwargs)
    return store.put(**defaults)


def test_managed_block_contains_base_data(tmp_path):
    contact = _contact(
        tmp_path,
        aliases=["Chris", "Chrissi"],
        relationship="friend",
        note="Met at the climbing gym.",
    )
    block = render_managed_block(contact)
    assert block.startswith(MANAGED_BLOCK_START)
    assert block.endswith(MANAGED_BLOCK_END)
    for expected in ("Christoph Meyer", "Chris", "Chrissi", "friend", "climbing gym"):
        assert expected in block


def test_managed_block_excludes_pii(tmp_path):
    contact = _contact(
        tmp_path,
        emails=["christoph@example.com"],
        phones=["+49 151 2345678"],
        address={"street": "Musterweg 1", "city": "Berlin"},
    )
    page = render_person_page(contact, existing_text=None)
    assert "christoph@example.com" not in page
    assert "2345678" not in page
    assert "Musterweg" not in page
    assert "Berlin" not in page


def test_fresh_page_has_person_frontmatter(tmp_path):
    contact = _contact(tmp_path, relationship="colleague", aliases=["CM"])
    page = render_person_page(contact, existing_text=None)
    meta, body = parse_frontmatter(page)
    assert meta["type"] == "person"
    assert meta["contact_slug"] == contact.slug
    assert meta["relationship"] == "colleague"
    assert meta["aliases"] == ["CM"]
    assert extract_managed_block(page) == render_managed_block(contact)


def test_resync_preserves_content_outside_block(tmp_path):
    contact = _contact(tmp_path, note="First note.")
    first = render_person_page(contact, existing_text=None)
    learned = "## Learned in conversation\n\n- Birthday is in August\n"
    edited = first + "\n" + learned
    contact2 = _contact(tmp_path, name="Christoph Meyer 2", note="Second note.")
    page = render_person_page(contact2, existing_text=edited)
    assert "Second note." in page
    assert "First note." not in page
    assert "Birthday is in August" in page


def test_resync_preserves_foreign_frontmatter_keys(tmp_path):
    contact = _contact(tmp_path)
    first = render_person_page(contact, existing_text=None)
    meta, body = parse_frontmatter(first)
    curated = first.replace("type: person", "type: person\ntags: [vip]", 1)
    page = render_person_page(contact, existing_text=curated)
    meta2, _ = parse_frontmatter(page)
    assert meta2.get("tags") == ["vip"]


def test_page_without_markers_keeps_full_body(tmp_path):
    contact = _contact(tmp_path)
    legacy = "---\ntype: person\n---\n\nHand-written page about Christoph.\n"
    page = render_person_page(contact, existing_text=legacy)
    assert "Hand-written page about Christoph." in page
    assert extract_managed_block(page) == render_managed_block(contact)
```

Note: `test_resync_preserves_foreign_frontmatter_keys` relies on the exact
serialisation of `write_frontmatter` — if the `str.replace` anchor does not
match, build the curated text via `write_frontmatter({**meta, "tags": ["vip"]}, body)` instead.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/memory/wiki/test_contact_mirror_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jarvis.memory.wiki.contact_mirror'`.

- [ ] **Step 3: Implement the rendering functions**

Create `jarvis/memory/wiki/contact_mirror.py`:

```python
"""Deterministic Contact → Wiki person-page mirror.

Every saved contact gets a guaranteed page at ``people/<contact-slug>.md`` in
the vault — rendered from a template, **no LLM involved**. The page has two
owners, separated by HTML-comment markers:

- The block between :data:`MANAGED_BLOCK_START` and :data:`MANAGED_BLOCK_END`
  belongs to the mirror. It is rewritten verbatim on every sync.
- Everything outside the block (curator-learned facts, manual notes) is
  preserved byte-for-byte — clobbering it would be the classic restore-trap
  bug class (docs/BUGS.md).

Privacy rule (spec §Non-Goals): phone numbers, e-mail addresses and street
addresses are deliberately NOT mirrored. The vault is broadly searched and
handed to Jarvis-Agents; base data stays in ``data/contacts/`` and is fetched on
demand via the ``contact-lookup`` tool.

Writes go through the shared :class:`~jarvis.memory.wiki.atomic_writer.AtomicWriter`
so backups, the secret guard, and the ``wiki_fts`` search index stay correct —
``wiki-recall`` finds a new person page immediately.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from jarvis.memory.frontmatter import parse_frontmatter, write_frontmatter

log = logging.getLogger(__name__)

MANAGED_BLOCK_START = "<!-- contact-mirror:start -->"
MANAGED_BLOCK_END = "<!-- contact-mirror:end -->"

#: Vault folder for mirrored person pages (documented in schema.md).
PEOPLE_DIR = "people"

#: Frontmatter keys the mirror owns (rewritten on every sync); all other
#: keys (curator tags etc.) are preserved.
_MIRROR_META_KEYS = ("type", "contact_slug", "relationship", "aliases", "last_synced")


def render_managed_block(contact: Any) -> str:
    """Render the mirror-owned block from a ``jarvis.contacts.store.Contact``.

    Deliberately excludes emails/phones/address (privacy rule, see module
    docstring). Deterministic: equal contact data → equal block, which is what
    the reconciliation no-op check compares.
    """
    lines = [MANAGED_BLOCK_START, f"# {contact.name}", ""]
    if contact.aliases:
        lines.append(f"- **Aliases:** {', '.join(contact.aliases)}")
    if contact.relationship:
        lines.append(f"- **Relationship:** {contact.relationship}")
    lines.append(
        "- Saved contact from the address book. Phone, e-mail and street "
        "address are kept out of the wiki on purpose — resolve them on "
        "demand with the `contact-lookup` tool."
    )
    note = (contact.note_md or "").strip()
    if note:
        lines += ["", "## Contact README", "", note]
    lines.append(MANAGED_BLOCK_END)
    return "\n".join(lines)


def extract_managed_block(page_text: str) -> str | None:
    """Return the managed block (markers included) or ``None`` if absent."""
    _, body = parse_frontmatter(page_text)
    start = body.find(MANAGED_BLOCK_START)
    end = body.find(MANAGED_BLOCK_END)
    if start == -1 or end == -1 or end < start:
        return None
    return body[start : end + len(MANAGED_BLOCK_END)]


def render_person_page(contact: Any, existing_text: str | None) -> str:
    """Render the full page, preserving everything outside the managed block.

    ``existing_text`` is the current on-disk page (or ``None`` for a fresh
    page). Foreign frontmatter keys survive; mirror-owned keys are rewritten.
    A pre-existing page without markers (hand-written or curator-created
    before the contact existed) keeps its full body below the new block.
    """
    meta: dict[str, Any] = {}
    prefix = ""
    suffix = ""
    if existing_text:
        old_meta, old_body = parse_frontmatter(existing_text)
        meta = dict(old_meta or {})
        start = old_body.find(MANAGED_BLOCK_START)
        end = old_body.find(MANAGED_BLOCK_END)
        if start != -1 and end != -1 and end >= start:
            prefix = old_body[:start].strip("\n")
            suffix = old_body[end + len(MANAGED_BLOCK_END) :].strip("\n")
        else:
            suffix = old_body.strip("\n")

    meta["type"] = "person"
    meta["contact_slug"] = contact.slug
    meta["relationship"] = contact.relationship or ""
    meta["aliases"] = list(contact.aliases)
    meta["last_synced"] = datetime.now(UTC).isoformat(timespec="seconds")

    parts: list[str] = []
    if prefix:
        parts.append(prefix)
    parts.append(render_managed_block(contact))
    if suffix:
        parts.append(suffix)
    body = "\n\n".join(parts) + "\n"
    return write_frontmatter(meta, body)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/memory/wiki/test_contact_mirror_render.py -v`
Expected: 6 passed. If `write_frontmatter`/`parse_frontmatter` round-tripping
surprises (e.g. empty-string relationship serialisation), adjust the
implementation — not the privacy/preservation assertions.

- [ ] **Step 5: Commit**

```bash
git add jarvis/memory/wiki/contact_mirror.py tests/unit/memory/wiki/test_contact_mirror_render.py
git commit -m "feat(wiki): contact mirror page rendering with managed block"
```

---

### Task 5: `ContactWikiMirror` — sync, archive, reconcile, skip-retry

**Files:**
- Modify: `jarvis/memory/wiki/contact_mirror.py` (append the class)
- Test: `tests/unit/memory/wiki/test_contact_mirror.py`

**Pre-step:** read `jarvis/memory/wiki/atomic_writer.py:464-476` (`_do_archive`) once and note the archive destination folder (expected: `_archive/` inside the vault). Use the actual folder name in `test_archive_moves_page_to_archive`.

- [ ] **Step 1: Write the failing tests**

```python
"""ContactWikiMirror: sync/archive/reconcile through the real AtomicWriter."""
from __future__ import annotations

import asyncio
from pathlib import Path

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
    # _do_archive destination — confirm folder name against atomic_writer.py:464
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/memory/wiki/test_contact_mirror.py -v`
Expected: FAIL with `ImportError: cannot import name 'ContactWikiMirror'`.

- [ ] **Step 3: Implement the class** (append to `jarvis/memory/wiki/contact_mirror.py`)

Add to the imports at the top:

```python
import asyncio
from pathlib import Path

from jarvis.memory.wiki.protocols import PageUpdate
```

Append the class:

```python
class ContactWikiMirror:
    """Async EventBus consumer that keeps ``people/`` in sync with contacts.

    All writes go through the shared :class:`AtomicWriter` (serial lock,
    backups, secret guard, FTS index). The writer's 30-second concurrent-edit
    lock will SKIP a page the mirror itself wrote moments ago — a rapid
    second edit would silently stay stale until the next boot reconciliation.
    ``sync`` therefore retries exactly once after ``retry_delay_s`` (which is
    why the default sits just above the lock window).
    """

    def __init__(
        self,
        *,
        vault_root: Path,
        writer: Any,
        repo: Any,
        store: Any,
        retry_delay_s: float = 35.0,
    ) -> None:
        self._vault_root = Path(vault_root)
        self._writer = writer
        self._repo = repo
        self._store = store
        self._retry_delay_s = float(retry_delay_s)

    # ------------------------------------------------------------------
    # EventBus handler — MUST stay ``async def`` (the bus silently drops
    # sync handlers; see project memory on OrbBusBridge).
    # ------------------------------------------------------------------
    async def on_contact_changed(self, event: Any) -> None:
        try:
            if getattr(event, "action", "") == "deleted":
                await self.archive(event.slug)
            else:
                await self.sync(event.slug)
        except Exception:  # noqa: BLE001 — a mirror error must never escape the bus
            log.warning(
                "contact_mirror: handling %s for %r failed",
                getattr(event, "action", "?"),
                getattr(event, "slug", "?"),
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------
    def _page_path(self, slug: str) -> Path:
        return self._vault_root / PEOPLE_DIR / f"{slug}.md"

    async def sync(self, slug: str, *, _retry: bool = True) -> bool:
        """Create/refresh the person page. Returns ``True`` if a write landed."""
        contact = await asyncio.to_thread(self._store.get, slug)
        if contact is None:
            return False
        path = self._page_path(slug)
        existing: str | None = None
        if path.exists():
            existing = await asyncio.to_thread(path.read_text, encoding="utf-8")
            if extract_managed_block(existing) == render_managed_block(contact):
                return False  # up to date — avoid write/FTS/backup churn
        update = PageUpdate(
            target_path=path,
            operation="create" if existing is None else "update",
            new_body=render_person_page(contact, existing),
            reason=f"contact mirror sync for {slug}",
        )
        result = await self._writer.apply([update], repo=self._repo)
        if any(p.name == path.name for p in result.applied):
            return True
        if any(p.name == path.name for p in result.skipped_due_to_recent_edit):
            if _retry:
                log.info(
                    "contact_mirror: %s hit the concurrent-edit lock — "
                    "retrying once in %.1fs",
                    path.name,
                    self._retry_delay_s,
                )
                await asyncio.sleep(self._retry_delay_s)
                return await self.sync(slug, _retry=False)
            log.warning(
                "contact_mirror: %s still locked after retry — boot "
                "reconciliation will heal it",
                path.name,
            )
        return False

    async def archive(self, slug: str) -> bool:
        """Archive the person page (never destroys learned content)."""
        path = self._page_path(slug)
        if not path.exists():
            return False
        update = PageUpdate(
            target_path=path,
            operation="archive",
            new_body="",
            reason=f"contact {slug} deleted from the address book",
        )
        result = await self._writer.apply([update], repo=self._repo)
        return any(p.name == path.name for p in result.applied)

    async def reconcile_all(self) -> int:
        """Boot-time self-heal: sync every contact; returns pages written."""
        try:
            contacts = await asyncio.to_thread(self._store.list_all)
        except Exception:  # noqa: BLE001
            log.warning("contact_mirror: reconcile could not list contacts", exc_info=True)
            return 0
        healed = 0
        for contact in contacts:
            try:
                if await self.sync(contact.slug, _retry=False):
                    healed += 1
            except Exception:  # noqa: BLE001
                log.warning(
                    "contact_mirror: reconcile failed for %r", contact.slug, exc_info=True
                )
        if healed:
            log.info("contact_mirror: reconciliation healed %d person page(s)", healed)
        return healed
```

Implementation note: `result.applied` paths are resolved absolute paths;
comparing by `p.name` sidesteps Windows `Path.resolve()` case quirks. If the
archive operation reports the *archived destination* in `applied` instead of
the source path, relax the archive return check to `not path.exists()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/memory/wiki/test_contact_mirror.py tests/unit/memory/wiki/test_contact_mirror_render.py -v`
Expected: all pass (the retry test takes ~0.5 s by design).

- [ ] **Step 5: Commit**

```bash
git add jarvis/memory/wiki/contact_mirror.py tests/unit/memory/wiki/test_contact_mirror.py
git commit -m "feat(wiki): ContactWikiMirror sync/archive/reconcile via AtomicWriter"
```

---

### Task 6: `wire_contact_mirror` — bus subscription + store→bus sink

**Files:**
- Modify: `jarvis/memory/wiki/contact_mirror.py` (append the wiring function)
- Test: `tests/unit/memory/wiki/test_contact_mirror_wiring.py`

- [ ] **Step 1: Write the failing tests**

```python
"""End-to-end wiring: ContactStore write → notify sink → bus → mirror → page."""
from __future__ import annotations

import asyncio

import pytest

from jarvis.contacts import notify
from jarvis.contacts.store import ContactStore
from jarvis.core.bus import EventBus
from jarvis.memory.wiki.atomic_writer import AtomicWriter
from jarvis.memory.wiki.contact_mirror import wire_contact_mirror
from jarvis.memory.wiki.page import MarkdownPageRepository


@pytest.fixture(autouse=True)
def _clean_sink():
    notify.clear_contact_change_sink()
    yield
    notify.clear_contact_change_sink()


async def _wait_for(predicate, timeout_s: float = 2.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


async def test_contact_write_lands_as_person_page(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    store = ContactStore(base_dir=tmp_path / "contacts")
    writer = AtomicWriter(
        vault_root=vault,
        backup_dir=tmp_path / "backups",
        concurrent_edit_lock_seconds=0.0,
    )
    bus = EventBus()
    mirror, cleanup = wire_contact_mirror(
        bus=bus,
        vault_root=vault,
        writer=writer,
        repo=MarkdownPageRepository(),
        store=store,
    )
    try:
        contact = store.put(name="Christoph Meyer", relationship="friend")
        page = vault / "people" / f"{contact.slug}.md"
        assert await _wait_for(page.exists)
        store.delete(contact.slug)
        assert await _wait_for(lambda: not page.exists())
    finally:
        cleanup()


async def test_cleanup_detaches_everything(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    store = ContactStore(base_dir=tmp_path / "contacts")
    writer = AtomicWriter(
        vault_root=vault,
        backup_dir=tmp_path / "backups",
        concurrent_edit_lock_seconds=0.0,
    )
    bus = EventBus()
    mirror, cleanup = wire_contact_mirror(
        bus=bus, vault_root=vault, writer=writer,
        repo=MarkdownPageRepository(), store=store,
    )
    cleanup()
    contact = store.put(name="Laura")
    await asyncio.sleep(0.1)
    assert not (vault / "people" / f"{contact.slug}.md").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/memory/wiki/test_contact_mirror_wiring.py -v`
Expected: FAIL with `ImportError: cannot import name 'wire_contact_mirror'`.

- [ ] **Step 3: Implement** (append to `jarvis/memory/wiki/contact_mirror.py`)

Add to the imports: `from collections.abc import Callable`.

```python
def wire_contact_mirror(
    *,
    bus: Any,
    vault_root: Path,
    writer: Any,
    repo: Any,
    store: Any | None = None,
    retry_delay_s: float = 35.0,
) -> "tuple[ContactWikiMirror, Callable[[], None]]":
    """Wire the full chain: store notify-sink → ``ContactChanged`` → mirror.

    Must be called from inside the running event loop (the bootstrap is
    async). The sink may fire from FastAPI's threadpool (sync REST routes),
    so it schedules ``bus.publish`` thread-safely onto the captured loop.
    Returns ``(mirror, cleanup)``; ``cleanup`` detaches sink + subscription.
    """
    from jarvis.contacts.notify import (
        clear_contact_change_sink,
        set_contact_change_sink,
    )
    from jarvis.contacts.store import ContactStore
    from jarvis.core.events import ContactChanged

    if store is None:
        store = ContactStore()
    mirror = ContactWikiMirror(
        vault_root=vault_root,
        writer=writer,
        repo=repo,
        store=store,
        retry_delay_s=retry_delay_s,
    )
    bus.subscribe(ContactChanged, mirror.on_contact_changed)
    loop = asyncio.get_running_loop()

    def _sink(action: str, slug: str, name: str) -> None:
        event = ContactChanged(
            action=action, slug=slug, name=name, source_layer="contacts"
        )
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            loop.create_task(bus.publish(event))
        else:
            asyncio.run_coroutine_threadsafe(bus.publish(event), loop)

    set_contact_change_sink(_sink)

    def _cleanup() -> None:
        clear_contact_change_sink()
        try:
            bus.unsubscribe(ContactChanged, mirror.on_contact_changed)
        except Exception:  # noqa: BLE001
            log.debug("contact_mirror: unsubscribe failed; already detached")

    return mirror, _cleanup
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/memory/wiki/test_contact_mirror_wiring.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add jarvis/memory/wiki/contact_mirror.py tests/unit/memory/wiki/test_contact_mirror_wiring.py
git commit -m "feat(wiki): wire contact mirror into bus + contact notify sink"
```

---

### Task 7: Bootstrap integration + handle teardown

**Files:**
- Modify: `jarvis/memory/wiki/integration.py`
  - `_build_curator` (line 662): accept a shared writer
  - `bootstrap_wiki_integration` (line 186): build writer, wire mirror, start reconciliation task
  - `WikiIntegrationHandle` (line 91): new fields + shutdown steps
- Test: `tests/unit/memory/wiki/test_contact_mirror_bootstrap.py`

- [ ] **Step 1: Write the failing test**

```python
"""Bootstrap regression: handle teardown tolerates the new mirror fields."""
from __future__ import annotations

from jarvis.memory.wiki.integration import WikiIntegrationHandle


async def test_noop_handle_shutdown_with_mirror_fields():
    handle = WikiIntegrationHandle(
        _unsubscribe_idle=lambda: None,
        _worker_stop=None,
    )
    await handle.shutdown()  # must not raise with all-default mirror fields


async def test_handle_runs_contact_mirror_cleanup():
    calls: list[str] = []
    handle = WikiIntegrationHandle(
        _unsubscribe_idle=lambda: None,
        _worker_stop=None,
        _contact_mirror_cleanup=lambda: calls.append("cleanup"),
    )
    await handle.shutdown()
    assert calls == ["cleanup"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/memory/wiki/test_contact_mirror_bootstrap.py -v`
Expected: second test FAILS with `TypeError: ... unexpected keyword argument '_contact_mirror_cleanup'`.

- [ ] **Step 3: Implement the bootstrap changes**

3a. `_build_curator` — share the writer. Change the signature and writer line:

```python
def _build_curator(
    *,
    repo: "PageRepository",
    vault_root: Path,
    brain_caller: Callable[[str, str], Awaitable[str]] | None,
    writer: Any | None = None,
) -> Any:
```

and replace

```python
    backup_dir = vault_root.parent / "wiki-backups"
    writer = AtomicWriter(vault_root=vault_root, backup_dir=backup_dir)
```

with

```python
    if writer is None:
        backup_dir = vault_root.parent / "wiki-backups"
        writer = AtomicWriter(vault_root=vault_root, backup_dir=backup_dir)
```

3b. In `bootstrap_wiki_integration`, build the shared writer before the curator
(replace the current `curator = _build_curator(...)` call around line 241):

```python
    from jarvis.memory.wiki.atomic_writer import AtomicWriter

    shared_writer = AtomicWriter(
        vault_root=vault_path, backup_dir=vault_path.parent / "wiki-backups"
    )
    curator = _build_curator(
        repo=repo,
        vault_root=vault_path,
        brain_caller=brain_caller,
        writer=shared_writer,
    )
```

3c. Right after `_set_running_curator(curator)` (line 249), wire the mirror —
fully guarded, never boot-fatal, never on the voice path (AP-9):

```python
    # ------------------------------------------------------------------
    # Contact → person-page mirror (deterministic, no LLM). Spec:
    # docs/superpowers/specs/2026-06-10-contact-wiki-mirror-design.md
    # ------------------------------------------------------------------
    contact_mirror_cleanup: Callable[[], None] | None = None
    contact_reconcile_task: "asyncio.Task[Any] | None" = None
    try:
        from jarvis.memory.wiki.contact_mirror import wire_contact_mirror

        contact_mirror, contact_mirror_cleanup = wire_contact_mirror(
            bus=bus, vault_root=vault_path, writer=shared_writer, repo=repo,
        )
        contact_reconcile_task = asyncio.create_task(
            contact_mirror.reconcile_all(), name="contact-mirror-reconcile"
        )
        log.info("wiki_integration: contact mirror wired (people/ pages)")
    except Exception as exc:  # noqa: BLE001 — contacts absent ≠ wiki broken
        log.warning("wiki_integration: contact mirror not wired — %s", exc)
```

3d. `WikiIntegrationHandle`: add two fields after `_journal`:

```python
    # Contact → person-page mirror: detach callback + boot reconciliation task.
    _contact_mirror_cleanup: "Callable[[], None] | None" = field(default=None)
    _contact_reconcile_task: "asyncio.Task[Any] | None" = field(default=None)
```

and add to `shutdown()` (right after the `_unsubscribe_idle` block, before the
voice-bridge stop):

```python
        # Detach the contact mirror (sink + bus subscription) and stop a
        # still-running boot reconciliation.
        if self._contact_mirror_cleanup is not None:
            try:
                self._contact_mirror_cleanup()
            except Exception:  # noqa: BLE001
                log.debug("wiki_integration: contact mirror cleanup failed; continuing")
            self._contact_mirror_cleanup = None
        if self._contact_reconcile_task is not None:
            self._contact_reconcile_task.cancel()
            self._contact_reconcile_task = None
```

3e. Thread the two values into BOTH `WikiIntegrationHandle(...)` constructions
at the end of `bootstrap_wiki_integration` (the success-path return only — the
early `config.enabled=False` return keeps defaults):

```python
        _contact_mirror_cleanup=contact_mirror_cleanup,
        _contact_reconcile_task=contact_reconcile_task,
```

- [ ] **Step 4: Run the tests + the existing integration suite**

Run: `pytest tests/unit/memory/wiki/ -v`
Expected: all pass, including pre-existing `test_integration*`/bootstrap tests.

- [ ] **Step 5: Commit**

```bash
git add jarvis/memory/wiki/integration.py tests/unit/memory/wiki/test_contact_mirror_bootstrap.py
git commit -m "feat(wiki): bootstrap wires contact mirror with shared writer + teardown"
```

---

### Task 8: schema.md — teach the curator about `people/`

**Files:**
- Modify: `jarvis/memory/wiki/templates/schema.md` (canonical, ships with the package)
- Modify: `wiki/obsidian-vault/schema.md` (the live vault — only seeded when absent, so it must be edited too)

- [ ] **Step 1: Read both files** and find the folder-taxonomy section (the one describing `entities/`, `concepts/`, ...). Match its exact formatting style.

- [ ] **Step 2: Add a `people/` section to BOTH files** (adapt wording to the file's existing style, keep the content):

```markdown
## people/

One page per saved contact from the address book, named `people/<contact-slug>.md`.
The block between `<!-- contact-mirror:start -->` and `<!-- contact-mirror:end -->`
is machine-managed (rewritten on every contact edit) — never edit or move it.
New facts learned about a known contact belong on that person's existing
`people/` page, BELOW the managed block — do not create a duplicate page under
`entities/` for someone who already has a `people/` page.
Phone numbers, e-mail addresses and street addresses must never be written to
the wiki; they live in the address book and are resolved on demand via the
`contact-lookup` tool.
```

- [ ] **Step 3: Sanity check** — `pytest tests/unit/memory/wiki/ -q` still green (some tests read schema.md).

- [ ] **Step 4: Commit**

```bash
git add jarvis/memory/wiki/templates/schema.md wiki/obsidian-vault/schema.md
git commit -m "docs(wiki): document people/ person pages + managed block in schema"
```

---

### Task 9: Full verification + spec amendment

- [ ] **Step 1: Run the affected suites**

Run: `pytest tests/unit/contacts/ tests/unit/memory/wiki/ tests/unit/ui/web/ -q`
Expected: all green (note pre-existing failures NOT caused by this change — verify by `git stash`-free reasoning: run the failing test on the previous commit only if in doubt, e.g. `git worktree` — do not stash with parallel sessions active).

- [ ] **Step 2: Lint**

Run: `ruff check jarvis/contacts/ jarvis/memory/wiki/contact_mirror.py jarvis/memory/wiki/integration.py jarvis/core/events.py && ruff format --check jarvis/contacts/notify.py jarvis/memory/wiki/contact_mirror.py`
Expected: clean.

- [ ] **Step 3: Amend the spec** — in `docs/superpowers/specs/2026-06-10-contact-wiki-mirror-design.md` §"2. Emission points", replace the routes/tool wording with the implemented store-level choke point:

```markdown
### 2. Emission point (single choke point)

Both write paths (REST routes and the `contact-upsert` voice tool) funnel into
`ContactStore.put()` / `delete()`, so emission lives there: after every
successful write the store calls `jarvis.contacts.notify.notify_contact_changed`,
a module-level sink registered during wiki bootstrap (no sink → no-op). The
sink publishes the frozen `ContactChanged` event thread-safely onto the
running loop (REST routes run in FastAPI's threadpool).
```

- [ ] **Step 4: Final commit**

```bash
git add docs/superpowers/specs/2026-06-10-contact-wiki-mirror-design.md docs/superpowers/plans/2026-06-10-contact-wiki-mirror.md
git commit -m "docs(specs): contact mirror — store-level emission amendment + plan"
```

- [ ] **Step 5: Live smoke note** — the running app needs a restart to pick up the new code (editable install, BUG-006 class). After restart: create a contact in the UI → `wiki/obsidian-vault/people/<slug>.md` must appear; ask Jarvis "was weißt du über <name>?" → `wiki-recall` should hit the page. <!-- i18n-allow: quoted German voice-trigger example -->
