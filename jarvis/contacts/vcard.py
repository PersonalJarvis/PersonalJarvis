"""Minimal vCard import/export for the contact book — stdlib only.

vCard is a line-folded key/value grammar, not a format that needs a parser
library (``jarvis.ultrawiki.connectors.export_import`` applies the same
reasoning): unfold continuation lines, split each property at its first
colon, done. Export writes vCard 3.0 — the flavor Google/Apple/Outlook all
accept — with CRLF line endings per the RFC. Base64 blobs (photos, keys) are
dropped on import; the contact model has no photo field, so none are written.

Import is merge-not-clobber: a card whose name resolves to an existing
contact (``ContactStore.find_by_alias``) unions the list fields and only
fills scalar fields that are currently empty — a re-import never overwrites
curated data. Invalid e-mails/phones/birthdays inside a card are dropped
value-by-value instead of failing the batch.
"""

from __future__ import annotations

import logging
from typing import Any

from .store import _EMAIL_RE, ContactStore, _normalize_phone, _validate_birthday

log = logging.getLogger(__name__)

#: Properties whose (potentially huge) base64 payload is never useful here.
_BLOB_PROPERTIES = frozenset({"PHOTO", "LOGO", "SOUND", "KEY"})

_MAX_ERRORS_REPORTED = 10


# ----------------------------------------------------------------------
# TEXT value escaping (RFC 2426 / 6350)
# ----------------------------------------------------------------------


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def _unescape(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt in ("n", "N"):
                out.append("\n")
            else:
                out.append(nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


# ----------------------------------------------------------------------
# Export
# ----------------------------------------------------------------------


def contact_to_vcard(contact: Any) -> str:
    """Render one ``jarvis.contacts.store.Contact`` as a vCard 3.0 block."""
    lines: list[str] = ["BEGIN:VCARD", "VERSION:3.0"]
    name = contact.name
    lines.append(f"FN:{_escape(name)}")
    # Best-effort structured name: last word = family name, rest = given.
    words = name.split()
    family = words[-1] if len(words) > 1 else ""
    given = " ".join(words[:-1]) if len(words) > 1 else name
    lines.append(f"N:{_escape(family)};{_escape(given)};;;")
    if contact.aliases:
        lines.append("NICKNAME:" + ",".join(_escape(a) for a in contact.aliases))
    if contact.organization:
        lines.append(f"ORG:{_escape(contact.organization)}")
    if contact.role:
        lines.append(f"TITLE:{_escape(contact.role)}")
    if contact.birthday:
        lines.append(f"BDAY:{contact.birthday}")
    for email in contact.emails:
        lines.append(f"EMAIL;TYPE=INTERNET:{_escape(email)}")
    for phone in contact.phones:
        lines.append(f"TEL;TYPE=VOICE:{_escape(phone)}")
    addr = contact.address
    if addr:
        # ADR components: PO box; extended; street; locality; region; postal; country.
        lines.append(
            "ADR;TYPE=HOME:;;{};{};;{};{}".format(
                _escape(addr.get("street", "")),
                _escape(addr.get("city", "")),
                _escape(addr.get("postal_code", "")),
                _escape(addr.get("country", "")),
            )
        )
    for url in contact.urls:
        lines.append(f"URL:{_escape(url)}")
    if contact.tags:
        lines.append("CATEGORIES:" + ",".join(_escape(t) for t in contact.tags))
    note = contact.note_md.strip()
    if note:
        lines.append(f"NOTE:{_escape(note)}")
    last_updated = contact.to_dict().get("last_updated")
    if last_updated:
        lines.append(f"REV:{last_updated}")
    lines.append("END:VCARD")
    return "\r\n".join(lines)


def contacts_to_vcf(contacts: list[Any]) -> str:
    """The whole book as one ``.vcf`` payload (trailing CRLF included)."""
    if not contacts:
        return ""
    return "\r\n".join(contact_to_vcard(c) for c in contacts) + "\r\n"


# ----------------------------------------------------------------------
# Import — parse
# ----------------------------------------------------------------------


def _unfold_lines(text: str) -> list[str]:
    """vCard line unfolding: a leading space/tab continues the line before."""
    out: list[str] = []
    current = ""
    for raw in text.splitlines():
        if raw[:1] in (" ", "\t") and current:
            current += raw[1:]
            continue
        if current:
            out.append(current)
        current = raw
    if current:
        out.append(current)
    return out


def _parse_bday(value: str) -> str | None:
    """Normalise a BDAY value to ISO ``YYYY-MM-DD`` or return ``None``.

    Accepts ``1990-04-12`` and the compact ``19900412``; the year-less
    vCard 4.0 form (``--0412``) has no ISO-date equivalent and is dropped.
    """
    v = value.strip()
    if len(v) == 8 and v.isdigit():
        v = f"{v[:4]}-{v[4:6]}-{v[6:]}"
    try:
        return _validate_birthday(v)
    except ValueError:
        # Unparseable BDAY is dropped, not fatal — the rest of the card still imports.
        return None


def parse_vcf(text: str) -> list[dict[str, Any]]:
    """Parse ``.vcf`` text into ``ContactStore.put``-shaped record dicts.

    Unknown properties are ignored; a card without FN falls back to a
    reassembled N; a card with neither yields no record.
    """
    records: list[dict[str, Any]] = []
    card: dict[str, Any] | None = None
    n_fallback = ""
    for line in _unfold_lines(text):
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper == "BEGIN:VCARD":
            card = {
                "name": "",
                "aliases": [],
                "emails": [],
                "phones": [],
                "urls": [],
                "tags": [],
                "address": {},
                "birthday": None,
                "organization": None,
                "role": None,
                "note": None,
            }
            n_fallback = ""
            continue
        if upper == "END:VCARD":
            if card is not None:
                if not card["name"] and n_fallback:
                    card["name"] = n_fallback
                if card["name"]:
                    records.append(card)
            card = None
            continue
        if card is None:
            continue
        key, sep, value = stripped.partition(":")
        if not sep or not value.strip():
            continue
        # "item1.TEL;TYPE=CELL" → property "TEL" (group prefix + params off).
        prop = key.split(";", 1)[0].split(".")[-1].strip().upper()
        value = value.strip()
        if prop in _BLOB_PROPERTIES:
            continue
        if prop == "FN" and not card["name"]:
            card["name"] = _unescape(value)
        elif prop == "N" and not n_fallback:
            parts = [_unescape(p) for p in value.split(";")]
            family = parts[0] if parts else ""
            given = parts[1] if len(parts) > 1 else ""
            n_fallback = " ".join(p for p in (given, family) if p).strip()
        elif prop == "NICKNAME":
            card["aliases"].extend(_unescape(a).strip() for a in value.split(",") if a.strip())
        elif prop == "EMAIL":
            card["emails"].append(_unescape(value))
        elif prop == "TEL":
            card["phones"].append(_unescape(value))
        elif prop == "URL":
            card["urls"].append(_unescape(value))
        elif prop == "CATEGORIES":
            card["tags"].extend(_unescape(t).strip() for t in value.split(",") if t.strip())
        elif prop == "ADR" and not card["address"]:
            parts = [_unescape(p).strip() for p in value.split(";")]
            parts += [""] * (7 - len(parts))
            card["address"] = {
                "street": parts[2],
                "city": parts[3],
                "postal_code": parts[5],
                "country": parts[6],
            }
        elif prop == "BDAY" and card["birthday"] is None:
            card["birthday"] = _parse_bday(value)
        elif prop == "ORG" and card["organization"] is None:
            card["organization"] = _unescape(value.split(";", 1)[0]).strip() or None
        elif prop == "TITLE" and card["role"] is None:
            card["role"] = _unescape(value).strip() or None
        elif prop == "NOTE" and card["note"] is None:
            card["note"] = _unescape(value)
    return records


# ----------------------------------------------------------------------
# Import — merge into the store
# ----------------------------------------------------------------------


def _union(existing: list[str], incoming: list[str]) -> list[str]:
    """Case-insensitive list union; the existing spelling/order wins."""
    out = list(existing)
    seen = {v.lower() for v in existing}
    for v in incoming:
        s = v.strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def import_records(store: ContactStore, records: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge parsed vCard records into the store.

    Returns ``{"created": n, "updated": n, "skipped": n, "errors": [...]}``.
    A record failing as a whole is counted, never raised — one broken card
    must not abort a 500-card import.
    """
    created = updated = skipped = 0
    errors: list[str] = []
    for rec in records:
        name = (rec.get("name") or "").strip()
        if not name:
            skipped += 1
            continue
        # Value-level sanitising: drop what the store would reject.
        emails = [e.strip() for e in rec.get("emails", []) if _EMAIL_RE.match(e.strip())]
        phones = []
        for p in rec.get("phones", []):
            try:
                phones.append(_normalize_phone(p))
            except ValueError:
                # Field-level sanitising: an unparseable number is dropped,
                # not the whole record.
                pass
        try:
            existing = store.find_by_alias(name)
            if existing is None:
                store.put(
                    name=name,
                    aliases=rec.get("aliases") or [],
                    birthday=rec.get("birthday"),
                    organization=rec.get("organization"),
                    role=rec.get("role"),
                    urls=rec.get("urls") or [],
                    tags=rec.get("tags") or [],
                    emails=emails,
                    phones=phones,
                    address=rec.get("address") or {},
                    note=rec.get("note"),
                )
                created += 1
            else:
                addr = dict(existing.address) or (rec.get("address") or {})
                store.put(
                    slug=existing.slug,
                    name=existing.name,
                    aliases=_union(existing.aliases, rec.get("aliases") or []),
                    relationship=existing.relationship,
                    favorite=existing.favorite,
                    birthday=existing.birthday or rec.get("birthday"),
                    organization=existing.organization or rec.get("organization"),
                    role=existing.role or rec.get("role"),
                    urls=_union(existing.urls, rec.get("urls") or []),
                    tags=_union(existing.tags, rec.get("tags") or []),
                    emails=_union(existing.emails, emails),
                    phones=_union(existing.phones, phones),
                    address=addr,
                    note=existing.note_md if existing.note_md.strip() else rec.get("note"),
                )
                updated += 1
        except ValueError as exc:
            skipped += 1
            if len(errors) < _MAX_ERRORS_REPORTED:
                errors.append(f"{name}: {exc}")
            log.warning("vcard import: skipped %r: %s", name, exc)
    return {"created": created, "updated": updated, "skipped": skipped, "errors": errors}
