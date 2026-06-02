"""``contact-lookup`` tool — resolve a name/alias to a stored contact's details.

Chunk B (Brain integration). Router-tier, read-only.

Why this tool exists
--------------------
The brain only sees a compact name-index of the contact book in its system
prompt (``ContactStore.render_for_prompt`` injects names + relationship, not the
details — that would bloat the prompt). When the user names a person for an
action ("schreib eine Mail an Christoph", "ruf Christoph an", "was ist Christophs
Adresse"), the brain calls this tool to fetch the full record — e-mails, phones,
address, README — then chains into ``gmail`` or ``call-contact``.

Contract dependency
-------------------
Consumes Contract 1 (``jarvis.contacts.store.ContactStore``, owned by Chunk A) via
a lazy ``store_resolver`` callable. The resolver returns ``None`` until Chunk A is
merged (or when the store fails to build) — the tool then degrades to a clean
"contacts unavailable" error instead of crashing the turn (mirrors the
``wiki-ingest`` curator-resolver pattern).

Placement rule
--------------
Router-tier only. Never include in any worker tool-set — a ``contact-lookup`` is
a direct safe-gated read, never a spawn (AP-5/AP-14). Risk tier ``safe``: a pure
read with no side effect, so the brain calls it without a confirmation nag.

Privacy
-------
The looked-up name is logged at INFO; the resolved e-mail/phone/address values
are logged at DEBUG only, never at INFO (matches the wiki-recall privacy rule).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from jarvis.core.protocols import ToolResult

log = logging.getLogger(__name__)


class ContactLookupTool:
    """Router-tier resolver: name/alias -> a contact's e-mails/phones/address."""

    name: str = "contact-lookup"
    description: str = (
        "Look up a saved contact by name or alias and return their e-mail "
        "address(es), phone number(s), postal address and notes. Call this "
        "FIRST whenever the user names a person for an action — e.g. 'schreib "
        "eine Mail an Christoph', 'ruf Christoph an', 'was ist Lauras Nummer' — "
        "then chain into the gmail or call-contact tool with the resolved "
        "details. Read-only; it never sends or changes anything."
    )
    risk_tier: str = "safe"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "The contact's name or alias as the user said it, e.g. "
                    "'Christoph' or 'mum'. Matching is case-insensitive."
                ),
            },
        },
        "required": ["name"],
    }
    input_examples: list[dict[str, Any]] = [
        {"name": "Christoph"},
        {"name": "mum"},
    ]

    def __init__(self, *, store_resolver: Callable[[], Any]) -> None:
        # Lazy resolver so the ContactStore can be built (or be absent, until
        # Chunk A lands) without coupling this tool to construction order.
        self._resolve_store = store_resolver

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        name = str(args.get("name", "")).strip()
        if not name:
            return ToolResult(success=False, output="", error="missing 'name' argument")

        store = self._resolve_store()
        if store is None:
            log.warning("contact-lookup: no ContactStore available (Chunk A not merged?)")
            return ToolResult(
                success=False,
                output="",
                error=(
                    "contacts are not available yet — open the Contacts section "
                    "to add people first"
                ),
            )

        log.info("contact-lookup: resolving %r", name)
        try:
            contact = store.find_by_alias(name)
        except Exception as exc:  # noqa: BLE001 — a store error must not crash the turn
            log.warning("contact-lookup: store.find_by_alias raised %s", exc)
            return ToolResult(success=False, output="", error="contact lookup failed")

        if contact is None:
            log.info("contact-lookup: no match for %r", name)
            return ToolResult(
                success=False,
                output="",
                error=f"no contact named {name!r} — add them in the Contacts section",
            )

        return ToolResult(success=True, output=_render_contact(contact))


def _render_contact(contact: Any) -> str:
    """Render a contact into a compact, prompt-friendly block.

    Tolerant of the Contract-1 shape: reads ``.name``/``.relationship``/
    ``.emails``/``.phones``/``.address``/``.note_md`` and the
    ``primary_email``/``primary_phone`` helpers, but never assumes any are
    present (a partially-populated stub or record degrades gracefully).
    """
    lines: list[str] = []
    name = getattr(contact, "name", None) or "(unnamed)"
    relationship = getattr(contact, "relationship", None)
    header = f"# {name}"
    if relationship:
        header += f" ({relationship})"
    lines.append(header)

    emails = list(getattr(contact, "emails", []) or [])
    if emails:
        lines.append("E-mail: " + ", ".join(emails))

    phones = list(getattr(contact, "phones", []) or [])
    if phones:
        lines.append("Phone: " + ", ".join(phones))

    address = getattr(contact, "address", None) or {}
    if isinstance(address, dict):
        parts = [
            str(address[key])
            for key in ("street", "postal_code", "city", "country")
            if address.get(key)
        ]
        if parts:
            lines.append("Address: " + ", ".join(parts))

    note = getattr(contact, "note_md", None)
    if note:
        lines.append("")
        lines.append(str(note).strip())

    log.debug("contact-lookup: served %s (emails=%d phones=%d)", name, len(emails), len(phones))
    return "\n".join(lines)
