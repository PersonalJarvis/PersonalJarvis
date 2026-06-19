# Contact → Wiki Person-Page Mirror — Design

**Date:** 2026-06-10
**Status:** Approved by maintainer (option A, full sync, boot reconciliation, PII kept out of vault)

## Problem

The Contacts section (`jarvis/contacts/` + `ContactsView`) and the Knowledge Wiki
(`jarvis/memory/wiki/`) are two disconnected person memories. Creating a contact
(e.g. "Christoph Meyer") leaves the wiki blind: `wiki-recall`, sub-agents, and the
conversation curator know nothing about the person. Conversely, facts the wiki
learns about a person never join up with the user-curated contact.

## Goals

1. Every contact is **guaranteed** a dedicated wiki person page — deterministic,
   template-rendered, no LLM in the write path.
2. **Full sync:** create → page created; edit → managed block refreshed (learned
   content preserved); delete → page archived to `_archive/`, never destroyed.
3. **Boot reconciliation:** on startup, contacts without a page (or with a stale
   managed block) are healed in the background. Self-healing covers pre-existing
   contacts and any missed write.
4. Wiki search (`wiki-recall`) and the curator find the page, so all agents "know"
   the person and future person-facts land on the same page.

## Non-Goals

- **No PII in the vault:** phones, emails, and street addresses are NOT mirrored.
  They stay in `data/contacts/<slug>.md` and are fetched on demand via
  `contact-lookup`. Mirrored fields: name, aliases, relationship, README note.
- No reverse sync (wiki → contact) in this iteration.
- No UI changes; the existing Contacts dialog is untouched.

## Architecture (Approach A — event-based, chosen over direct call / curator ingest)

Lateral communication follows the house rule: typed events on the `EventBus`.

### 1. New event: `ContactChanged`

`jarvis/core/events.py` — frozen dataclass with `trace_id` + `timestamp_ns`, plus:

- `action: str` — one of `created` / `updated` / `deleted`. Vocabulary lives as a
  single-source tuple in `jarvis/contacts/` (Python-only wire format today; if it
  ever crosses SQL/TS, apply the five-layer anti-drift pattern).
- `slug: str`, `name: str`.

### 2. Emission point (single choke point — amended during implementation)

Both write paths (REST routes and the `contact-upsert` voice tool) funnel into
`ContactStore.put()` / `delete()`, so emission lives there: after every
successful write the store calls `jarvis.contacts.notify.notify_contact_changed`,
a module-level sink registered during wiki bootstrap (no sink → zero-overhead
no-op; a sink error never fails the contact write). The sink publishes the
frozen `ContactChanged` event thread-safely onto the running loop (REST routes
run in FastAPI's threadpool).

Implementation addendum: the page schema (`jarvis/memory/wiki/page.py`) gained
a first-class `person` page type (`people/` → `person`, required keys
`type` + `slug`, filename-slug parity) because the `AtomicWriter` validates
every write via `PageRepository.parse` and would roll back an unknown type.

### 3. Mirror subscriber: `jarvis/memory/wiki/contact_mirror.py`

- Subscribed during `bootstrap_wiki_integration` (handler is `async def` — sync
  handlers are silently swallowed by the bus).
- Renders `people/<slug>.md` in the vault:
  - YAML frontmatter: `type: person`, `relationship`, `aliases`, `contact_slug`,
    `last_synced`.
  - A **managed block** between `<!-- contact-mirror:start -->` and
    `<!-- contact-mirror:end -->` containing name, aliases, relationship, and the
    contact README note. Only this block is owned by the mirror.
  - Everything outside the managed block (curator-learned facts, manual notes)
    is preserved verbatim on every sync.
- Writes are atomic (tempfile + `os.replace`), consistent with the vault's
  `AtomicWriter` discipline; respects the vault concurrent-edit lock.
- Delete action moves the page to `_archive/` (collision-safe rename).
- Never runs on the voice critical path (AP-9): the subscriber does file I/O in
  a background task.

### 4. Boot reconciliation

Part of `contact_mirror.py`, kicked off as a background task from
`bootstrap_wiki_integration`: iterate `ContactStore.list_all()`, create missing
pages, refresh stale managed blocks (compare rendered block vs. on-disk block).
Runs once per boot; errors are logged, never fatal.

### 5. Curator/schema awareness

`wiki/obsidian-vault/schema.md` (curator prompt input) documents the `people/`
folder: person facts about a known contact target `people/<slug>.md` below the
managed block, instead of spawning duplicate `entities/` pages.

## Error handling

- Mirror failures never propagate to the contact write or the voice turn
  (EventBus `_safe_dispatch` + defensive try/except in the handler).
- Wiki disabled / curator stack absent → event is emitted but no subscriber
  exists; contact writes behave exactly as today (cloud-first degradation).
- Reconciliation is idempotent; a crash mid-run is healed on the next boot.

## Testing

- Unit: page rendering, managed-block preservation (content above/below survives
  a re-sync), archive-on-delete, reconciliation (missing page, stale block,
  up-to-date no-op), PII exclusion (no phone/email/address in rendered output).
- Unit: event emission from REST routes and `contact-upsert` tool (fakes, not
  mocks, per house convention).
- Integration: bootstrap subscribes the handler; `ContactChanged` ends in a page
  on disk; `wiki-recall`'s `VaultSearch` finds the new page.
