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
handed to sub-agents; base data stays in ``data/contacts/`` and is fetched on
demand via the ``contact-lookup`` tool.

Writes go through the shared :class:`~jarvis.memory.wiki.atomic_writer.AtomicWriter`
so backups, the secret guard, and the ``wiki_fts`` search index stay correct —
``wiki-recall`` finds a new person page immediately.

Spec: ``docs/superpowers/specs/2026-06-10-contact-wiki-mirror-design.md``.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis.memory.frontmatter import parse_frontmatter, write_frontmatter
from jarvis.memory.wiki.protocols import PageUpdate

log = logging.getLogger(__name__)

MANAGED_BLOCK_START = "<!-- contact-mirror:start -->"
MANAGED_BLOCK_END = "<!-- contact-mirror:end -->"

#: Vault folder for mirrored person pages (documented in schema.md).
PEOPLE_DIR = "people"


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
    meta["slug"] = contact.slug  # schema-required; must match the filename stem
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
    # sync handlers).
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
            log.warning(
                "contact_mirror: reconcile could not list contacts", exc_info=True
            )
            return 0
        healed = 0
        for contact in contacts:
            try:
                if await self.sync(contact.slug, _retry=False):
                    healed += 1
            except Exception:  # noqa: BLE001
                log.warning(
                    "contact_mirror: reconcile failed for %r",
                    contact.slug,
                    exc_info=True,
                )
        if healed:
            log.info("contact_mirror: reconciliation healed %d person page(s)", healed)
        return healed


def wire_contact_mirror(
    *,
    bus: Any,
    vault_root: Path,
    writer: Any,
    repo: Any,
    store: Any | None = None,
    retry_delay_s: float = 35.0,
) -> tuple[ContactWikiMirror, Callable[[], None]]:
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
