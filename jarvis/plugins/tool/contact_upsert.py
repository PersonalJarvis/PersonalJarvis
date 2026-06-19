"""``contact-upsert`` tool — deterministic voice/chat write into the contact book.

Chunk B (Brain integration). Router-tier, write semantics.

Why this tool exists
--------------------
The user can manage contacts in the desktop UI, but the plan's v1 also supports
managing them by voice: "merk dir, Christophs Nummer ist …" / "Christophs E-Mail
ist …". The brain fills the structured args from the utterance and this tool
writes them deterministically via ``ContactStore.upsert`` (Contract 1). No LLM
call here, no free-form parsing — the brain already did the extraction.

Risk tier and confirmation
--------------------------
``monitor``: it mutates a contact file, so it is not ``safe``; but it must run
without a confirmation nag on every "merk dir" (anti-confirmation-fatigue), so it
is not ``ask`` either. Exactly the ``wiki-ingest`` precedent. The write surface
is the ``ContactStore``'s own atomic writer (Contract 1, owned by Chunk A);
deletion stays UI-only in v1.

Contract dependency
-------------------
Consumes Contract 1 via a lazy ``store_resolver``. Until Chunk A merges (or if
the store fails to build) the resolver returns ``None`` and the tool degrades to
a clean "contacts unavailable" error.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from jarvis.core.protocols import ToolResult

log = logging.getLogger(__name__)

# Optional fields the brain may fill from the utterance. ``name`` is required
# and handled separately; these are the data fields — at least one must be
# present or the write is a no-op (and we reject it so the brain cannot claim a
# save it did not make).
_DATA_FIELDS: tuple[str, ...] = ("relationship", "email", "phone", "address", "note")


class ContactUpsertTool:
    """Router-tier deterministic writer for the user-curated contact book."""

    name: str = "contact-upsert"
    description: str = (
        "Create or update a saved contact. Use this when the user tells you a "
        "person's details to remember — 'merk dir Christophs Nummer ist …', "
        "'Lauras E-Mail ist …', 'speichere Toms Adresse'. Fill the structured "
        "fields you can extract from what the user said; an existing contact "
        "with the same name is updated in place, a new one is created. The "
        "write happens silently (no confirmation needed). Deletion is UI-only."
    )
    risk_tier: str = "monitor"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The contact's name, e.g. 'Christoph'. Required.",
            },
            "relationship": {
                "type": "string",
                "description": (
                    "Optional relationship label, e.g. 'family', 'friend', "
                    "'colleague', 'partner', 'acquaintance', 'other'."
                ),
            },
            "email": {
                "type": "string",
                "description": "Optional e-mail address to add/set.",
            },
            "phone": {
                "type": "string",
                "description": "Optional phone number to add/set (any format).",
            },
            "address": {
                "type": "string",
                "description": "Optional postal address as the user said it.",
            },
            "note": {
                "type": "string",
                "description": "Optional short free-text note about the person.",
            },
        },
        "required": ["name"],
    }
    input_examples: list[dict[str, Any]] = [
        {"name": "Christoph", "phone": "+49 151 12345678"},
        {"name": "Laura", "email": "laura@example.com", "relationship": "colleague"},
    ]

    def __init__(self, *, store_resolver: Callable[[], Any]) -> None:
        self._resolve_store = store_resolver

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        name = str(args.get("name", "")).strip()
        if not name:
            return ToolResult(success=False, output="", error="missing 'name' argument")

        # Collect the non-empty data fields the brain extracted.
        fields: dict[str, Any] = {}
        for key in _DATA_FIELDS:
            value = args.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                fields[key] = text

        if not fields:
            return ToolResult(
                success=False,
                output="",
                error=(
                    "nothing to save — give at least one of: relationship, email, "
                    "phone, address, note"
                ),
            )

        store = self._resolve_store()
        if store is None:
            log.warning("contact-upsert: no ContactStore available (Chunk A not merged?)")
            return ToolResult(
                success=False,
                output="",
                error=(
                    "contacts are not available yet — open the Contacts section "
                    "to add people first"
                ),
            )

        # Privacy: the field VALUES are logged at DEBUG only; INFO carries the
        # name + which fields changed, never the data itself.
        log.info("contact-upsert: writing %r (fields=%s)", name, sorted(fields))
        log.debug("contact-upsert: %r fields=%r", name, fields)
        try:
            contact = store.upsert(name=name, **fields)
        except Exception as exc:  # noqa: BLE001 — a store error must not crash the turn
            log.warning("contact-upsert: store.upsert raised %s", exc)
            return ToolResult(success=False, output="", error=f"could not save contact: {exc}")

        saved_name = getattr(contact, "name", name)
        return ToolResult(
            success=True,
            output=f"Saved contact {saved_name} ({', '.join(sorted(fields))}).",
        )
