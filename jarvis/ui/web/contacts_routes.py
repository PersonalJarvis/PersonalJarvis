"""REST API for the Contacts section — the user-curated address book.

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET    /api/contacts          → {"contacts": [summary, ...]} sorted by name.
    GET    /api/contacts/export   → the whole book as a ``.vcf`` download.
    POST   /api/contacts/import   → merge vCard text into the book.
    GET    /api/contacts/{slug}   → full contact dict; 404 if unknown.
    POST   /api/contacts          → create one (server derives the slug); 201.
    PATCH  /api/contacts/{slug}   → partial edit; returns the full contact; 404.
    DELETE /api/contacts/{slug}   → remove (idempotent → 200, {"removed": bool}).

NOTE: ``/export`` MUST stay registered before ``GET /{slug}`` — Starlette
matches routes in registration order, so the dynamic slug route would
otherwise swallow it (as a 404 for the slug "export").

Storage is one ``<slug>.md`` file per contact under
``user_data_dir()/data/contacts/`` (YAML frontmatter + Markdown README), written
atomically by :class:`jarvis.contacts.store.ContactStore`. Like the Socials/avatar
endpoints it has **no Brain dependency**, so it works headless / with MockBrain.

This router only exposes the store's Contract-1 + CRUD surface over HTTP; the
``contact-lookup`` / ``contact-upsert`` / ``call-contact`` tools (the voice path)
are Chunk B and live elsewhere. Validation: the ``relationship`` field is a
Pydantic ``Literal`` (unknown value → 422); malformed e-mail/phone and empty name
surface as the store's ``ValueError`` → 400; an oversized README → 400.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from jarvis.contacts.schema import Relationship
from jarvis.contacts.store import ContactStore
from jarvis.contacts.vcard import contacts_to_vcf, import_records, parse_vcf

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/contacts", tags=["contacts"])

# Serializes read-modify-write across the per-request ContactStore instances.
# Endpoints are sync ``def`` (FastAPI threadpool), so a threading.Lock fits.
_LOCK = threading.Lock()

# ~300-word README → a few thousand chars; the cap is a generous abuse guard
# (keeps a single file from growing unbounded), not a hard word limit.
_MAX_NOTE_LEN = 16_000
_MAX_NAME_LEN = 200
# A 2 MB .vcf is ~4000 richly-filled cards — plenty for a personal book,
# small enough that a mistaken photo-laden export cannot wedge the server.
_MAX_IMPORT_LEN = 2_000_000


def _store() -> ContactStore:
    # Constructed per request; reads user_data_dir() at call time so the
    # LOCALAPPDATA-based test sandbox works (mirrors socials_routes).
    return ContactStore()


# ----------------------------------------------------------------------
# Request models
# ----------------------------------------------------------------------


class AddressModel(BaseModel):
    street: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str | None = None


class ContactCreate(BaseModel):
    name: str
    aliases: list[str] = []
    relationship: Relationship | None = None
    favorite: bool = False
    birthday: str | None = None
    organization: str | None = None
    role: str | None = None
    urls: list[str] = []
    tags: list[str] = []
    emails: list[str] = []
    phones: list[str] = []
    address: AddressModel | None = None
    note: str | None = None


class ContactUpdate(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None
    relationship: Relationship | None = None
    favorite: bool | None = None
    birthday: str | None = None
    organization: str | None = None
    role: str | None = None
    urls: list[str] | None = None
    tags: list[str] | None = None
    emails: list[str] | None = None
    phones: list[str] | None = None
    address: AddressModel | None = None
    note: str | None = None


class ImportBody(BaseModel):
    """Raw ``.vcf`` file content (the UI reads the picked file as text)."""

    vcf: str


# ----------------------------------------------------------------------
# Validation helpers
# ----------------------------------------------------------------------


def _check_note(note: str | None) -> None:
    if note is not None and len(note) > _MAX_NOTE_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"README is too long (max {_MAX_NOTE_LEN} characters).",
        )


def _check_name(name: str | None) -> None:
    if name is not None and len(name) > _MAX_NAME_LEN:
        raise HTTPException(
            status_code=400, detail=f"Name is too long (max {_MAX_NAME_LEN} characters)."
        )


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------


@router.get("")
def list_contacts() -> dict[str, Any]:
    with _LOCK:
        contacts = _store().list_all()
    return {"contacts": [c.to_summary() for c in contacts]}


# Registered BEFORE GET /{slug} on purpose — see the module docstring NOTE.
@router.get("/export")
def export_contacts() -> Response:
    with _LOCK:
        vcf = contacts_to_vcf(_store().list_all())
    return Response(
        content=vcf,
        media_type="text/vcard; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="contacts.vcf"'},
    )


@router.post("/import")
def import_contacts(body: ImportBody) -> dict[str, Any]:
    if len(body.vcf) > _MAX_IMPORT_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"vCard payload too large (max {_MAX_IMPORT_LEN} characters).",
        )
    records = parse_vcf(body.vcf)
    if not records:
        raise HTTPException(
            status_code=400, detail="No vCard records found in the payload."
        )
    with _LOCK:
        stats = import_records(_store(), records)
    return {"ok": True, **stats}


@router.get("/{slug}")
def get_contact(slug: str) -> dict[str, Any]:
    with _LOCK:
        contact = _store().get(slug)
    if contact is None:
        raise HTTPException(status_code=404, detail=f"No contact with slug {slug!r}.")
    return contact.to_dict()


@router.post("", status_code=201)
def create_contact(body: ContactCreate) -> dict[str, Any]:
    _check_name(body.name)
    _check_note(body.note)
    address = body.address.model_dump(exclude_none=True) if body.address else None
    try:
        with _LOCK:
            contact = _store().put(
                name=body.name,
                aliases=body.aliases,
                relationship=body.relationship,
                favorite=body.favorite,
                birthday=body.birthday,
                organization=body.organization,
                role=body.role,
                urls=body.urls,
                tags=body.tags,
                emails=body.emails,
                phones=body.phones,
                address=address,
                note=body.note,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return contact.to_dict()


@router.patch("/{slug}")
def update_contact(slug: str, body: ContactUpdate) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)
    _check_name(fields.get("name"))
    _check_note(fields.get("note"))
    if "address" in fields and body.address is not None:
        fields["address"] = body.address.model_dump(exclude_none=True)
    try:
        with _LOCK:
            contact = _store().update(slug, **fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if contact is None:
        raise HTTPException(status_code=404, detail=f"No contact with slug {slug!r}.")
    return contact.to_dict()


@router.delete("/{slug}")
def delete_contact(slug: str) -> dict[str, Any]:
    with _LOCK:
        removed = _store().delete(slug)
    return {"ok": True, "removed": removed}
