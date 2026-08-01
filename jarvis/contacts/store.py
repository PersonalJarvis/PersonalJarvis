"""``Contact`` + ``ContactStore`` — the user-curated address book (Contract 1).

Mirrors :mod:`jarvis.memory.people` (one ``<slug>.md`` per record, YAML
frontmatter + Markdown body, atomic ``tempfile``+``os.replace`` writes) but for a
**separate, fully user-managed** store under ``user_data_dir()/data/contacts/`` —
distinct from the Curator's auto-extracted ``data/workspace/people/``.

Contract 1 (FROZEN — Chunk B consumes these exact signatures):

    list_all() -> list[Contact]
    get(slug) -> Contact | None
    find_by_alias(query) -> Contact | None
    upsert(*, name, relationship=None, email=None, phone=None,
           address=None, note=None) -> Contact
    delete(slug) -> bool
    render_for_prompt(*, max_chars=800) -> str

``upsert`` is the deterministic *voice-write* path (the brain fills the args from
an utterance; it merges a single email/phone into the record). The richer CRUD UI
goes through the additive :meth:`ContactStore.put` / :meth:`ContactStore.update`
methods — adding methods does not break the frozen contract.

Frontmatter layout::

    identity:
      name: Christoph Meyer
      aliases: [Chris]
    relationship: friend            # optional; one of jarvis.contacts.schema
    contact:
      emails: [christoph@example.com]
      phones: ['+4915123456789']    # E.164-normalised (separators stripped)
      address: {street, postal_code, city, country}
    last_updated: 2026-06-02T12:00:00+00:00

    <the ~300-word Markdown README is the body>
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis.core.paths import user_data_dir
from jarvis.memory.frontmatter import parse_frontmatter, write_frontmatter
from jarvis.memory.workspace import person_slug

from .notify import notify_contact_changed
from .schema import normalize_relationship

# A pragmatic, dependency-free e-mail check (cloud-first base install stays light —
# no `email-validator`/`libphonenumber`). Good enough to reject obvious garbage
# before it lands in a file; the UI is the authority for what the user wants.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NON_DIGIT_RE = re.compile(r"\D")

#: The only address sub-keys we persist (anything else is dropped).
_ADDRESS_KEYS: tuple[str, ...] = ("street", "postal_code", "city", "country")


def _validate_email(raw: str) -> str:
    """Return a trimmed e-mail or raise ``ValueError``."""
    s = (raw or "").strip()
    if not _EMAIL_RE.match(s):
        raise ValueError(f"Invalid e-mail address: {raw!r}.")
    return s


def _normalize_phone(raw: str) -> str:
    """Best-effort E.164 normalisation: keep a leading ``+``, strip separators.

    - ``"+49 151 2345-6789"`` → ``"+4915123456789"``
    - ``"0049 151 234"``      → ``"+49151234"`` (``00`` international prefix → ``+``)
    - ``"(030) 12 34 56"``    → ``"030123456"`` (no country code given → digits only)

    A value with no digits at all is rejected (``ValueError``). This is
    deliberately *not* full libphonenumber parsing — that is a heavy dependency
    the €5-VPS base install must not carry.
    """
    s = (raw or "").strip()
    digits = _NON_DIGIT_RE.sub("", s)
    if not digits:
        raise ValueError(f"Phone number has no digits: {raw!r}.")
    if s.startswith("+"):
        return "+" + digits
    if s.startswith("00"):
        return "+" + digits[2:]
    return digits


@dataclass
class Contact:
    """A single contact (``contacts/<slug>.md``)."""

    path: Path
    _meta: dict[str, Any] = field(default_factory=dict)
    _body: str = ""

    @classmethod
    def load(cls, path: Path) -> Contact:
        text = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        return cls(path=path, _meta=meta, _body=body)

    # --- identity ------------------------------------------------------
    @property
    def slug(self) -> str:
        return self.path.stem

    @property
    def name(self) -> str:
        return (self._meta.get("identity", {}) or {}).get("name", self.path.stem)

    @property
    def aliases(self) -> list[str]:
        return list((self._meta.get("identity", {}) or {}).get("aliases", []) or [])

    @property
    def relationship(self) -> str | None:
        return self._meta.get("relationship") or None

    # --- contact info --------------------------------------------------
    @property
    def _contact(self) -> dict[str, Any]:
        return self._meta.get("contact", {}) or {}

    @property
    def emails(self) -> list[str]:
        return list(self._contact.get("emails", []) or [])

    @property
    def phones(self) -> list[str]:
        return list(self._contact.get("phones", []) or [])

    @property
    def address(self) -> dict[str, str]:
        return dict(self._contact.get("address", {}) or {})

    @property
    def note_md(self) -> str:
        return self._body

    @property
    def primary_email(self) -> str | None:
        emails = self.emails
        return emails[0] if emails else None

    @property
    def primary_phone(self) -> str | None:
        phones = self.phones
        return phones[0] if phones else None

    # --- serialisation (wire shape for the REST routes / Chunk B) ------
    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "aliases": self.aliases,
            "relationship": self.relationship,
            "emails": self.emails,
            "phones": self.phones,
            "address": self.address,
            "note": self.note_md,
            "primary_email": self.primary_email,
            "primary_phone": self.primary_phone,
            "last_updated": self._meta.get("last_updated"),
        }

    def to_summary(self) -> dict[str, Any]:
        """Compact shape for the list view (no README, no full address)."""
        return {
            "slug": self.slug,
            "name": self.name,
            "aliases": self.aliases,
            "relationship": self.relationship,
            "primary_email": self.primary_email,
            "primary_phone": self.primary_phone,
            "email_count": len(self.emails),
            "phone_count": len(self.phones),
        }


@dataclass
class ContactStore:
    """Manages ``user_data_dir()/data/contacts/`` (one ``<slug>.md`` per contact).

    ``base_dir`` is injectable for tests; it is **not** part of the frozen
    Contract 1 (only the public methods are). When omitted it resolves to the
    canonical app-data location at construction time.
    """

    base_dir: Path | None = None

    def __post_init__(self) -> None:
        if self.base_dir is None:
            self.base_dir = user_data_dir() / "data" / "contacts"
        else:
            self.base_dir = Path(self.base_dir)

    # ------------------------------------------------------------------
    # Paths / IO
    # ------------------------------------------------------------------
    def _path(self, slug: str) -> Path:
        return self.base_dir / f"{slug}.md"

    def _iter_paths(self) -> list[Path]:
        if not self.base_dir.exists():
            return []
        return sorted(p for p in self.base_dir.glob("*.md") if p.is_file())

    def _unique_slug(self, name: str) -> str:
        base = person_slug(name)
        candidate = base
        i = 2
        while self._path(candidate).exists():
            candidate = f"{base}-{i}"
            i += 1
        return candidate

    def _write(self, slug: str, meta: dict[str, Any], body: str) -> None:
        """Atomic write: tempfile in the contacts dir → ``os.replace``."""
        path = self._path(slug)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        text = write_frontmatter(meta, body)
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(self.base_dir)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Contract 1 — read
    # ------------------------------------------------------------------
    def list_all(self) -> list[Contact]:
        contacts = [Contact.load(p) for p in self._iter_paths()]
        contacts.sort(key=lambda c: c.name.lower())
        return contacts

    def get(self, slug: str) -> Contact | None:
        path = self._path(slug)
        if not path.exists():
            return None
        return Contact.load(path)

    def find_by_alias(self, query: str) -> Contact | None:
        slug = person_slug(query)
        direct = self.get(slug)
        if direct is not None:
            return direct
        q_lower = (query or "").strip().lower()
        if not q_lower:
            return None
        for c in self.list_all():
            if c.name.lower() == q_lower:
                return c
            if any(a.lower() == q_lower for a in c.aliases):
                return c
        return None

    # ------------------------------------------------------------------
    # Contract 1 — deterministic voice-write (merge one email/phone)
    # ------------------------------------------------------------------
    def upsert(
        self,
        *,
        name: str,
        relationship: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        address: dict[str, Any] | None = None,
        note: str | None = None,
    ) -> Contact:
        rel = normalize_relationship(relationship)
        new_email = _validate_email(email) if email else None
        new_phone = _normalize_phone(phone) if phone else None

        existing = self.find_by_alias(name)
        if existing is not None:
            emails = list(existing.emails)
            if new_email and new_email.lower() not in [e.lower() for e in emails]:
                emails.append(new_email)
            phones = list(existing.phones)
            if new_phone and new_phone not in phones:
                phones.append(new_phone)
            addr = dict(existing.address)
            if address:
                addr.update({k: v for k, v in address.items() if v})
            return self.put(
                slug=existing.slug,
                name=existing.name,
                aliases=existing.aliases,
                relationship=rel if rel is not None else existing.relationship,
                emails=emails,
                phones=phones,
                address=addr,
                note=note if note is not None else existing.note_md,
            )

        return self.put(
            name=name,
            relationship=rel,
            emails=[new_email] if new_email else [],
            phones=[new_phone] if new_phone else [],
            address=address or {},
            note=note or "",
        )

    # ------------------------------------------------------------------
    # Additive — full-record create/replace (CRUD UI routes use these)
    # ------------------------------------------------------------------
    def put(
        self,
        *,
        slug: str | None = None,
        name: str,
        aliases: list[str] | None = None,
        relationship: str | None = None,
        emails: list[str] | None = None,
        phones: list[str] | None = None,
        address: dict[str, Any] | None = None,
        note: str | None = None,
    ) -> Contact:
        """Create (``slug=None``) or replace-in-place a full contact record."""
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("A contact requires a non-empty name.")
        rel = normalize_relationship(relationship)

        clean_emails: list[str] = []
        for e in emails or []:
            ce = _validate_email(e)
            if ce.lower() not in [x.lower() for x in clean_emails]:
                clean_emails.append(ce)

        clean_phones: list[str] = []
        for p in phones or []:
            cp = _normalize_phone(p)
            if cp not in clean_phones:
                clean_phones.append(cp)

        clean_aliases: list[str] = []
        for a in aliases or []:
            av = (a or "").strip()
            if av and av != clean_name and av not in clean_aliases:
                clean_aliases.append(av)

        addr = {
            k: str(v).strip()
            for k, v in (address or {}).items()
            if k in _ADDRESS_KEYS and v and str(v).strip()
        }

        created = slug is None
        if slug is None:
            slug = self._unique_slug(clean_name)

        meta: dict[str, Any] = {"identity": {"name": clean_name, "aliases": clean_aliases}}
        if rel is not None:
            meta["relationship"] = rel
        meta["contact"] = {"emails": clean_emails, "phones": clean_phones, "address": addr}
        meta["last_updated"] = datetime.now(UTC).isoformat(timespec="seconds")

        body = (note or "").strip()
        body = f"{body}\n" if body else ""
        self._write(slug, meta, body)
        notify_contact_changed("created" if created else "updated", slug, clean_name)
        return Contact.load(self._path(slug))

    def update(self, slug: str, **fields: Any) -> Contact | None:
        """Partial update for PATCH: only the provided (non-``None``) fields
        overwrite the current record. Returns ``None`` if the slug is unknown.
        """
        current = self.get(slug)
        if current is None:
            return None
        merged: dict[str, Any] = {
            "name": current.name,
            "aliases": current.aliases,
            "relationship": current.relationship,
            "emails": current.emails,
            "phones": current.phones,
            "address": current.address,
            "note": current.note_md,
        }
        for key, value in fields.items():
            if value is not None:
                merged[key] = value
        return self.put(slug=slug, **merged)

    # ------------------------------------------------------------------
    # Contract 1 — delete + prompt block
    # ------------------------------------------------------------------
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

    def render_for_prompt(self, *, max_chars: int = 800) -> str:
        """Compact ``## Contacts`` block: names + relationship only.

        Detail (emails/phones/address/README) is fetched on demand via the
        ``contact-lookup`` tool (Chunk B) — never injected into every prompt.
        """
        contacts = self.list_all()
        if not contacts:
            return ""
        parts = ["## Contacts"]
        for c in contacts:
            aliases = c.aliases
            tag = f" (aka {', '.join(aliases)})" if aliases else ""
            rel = c.relationship
            if rel:
                parts.append(f"- **{c.name}**{tag} — {rel}")
            else:
                parts.append(f"- **{c.name}**{tag}")
        out = "\n".join(parts)
        if len(out) > max_chars:
            out = out[: max_chars - 1] + "…"
        return out
