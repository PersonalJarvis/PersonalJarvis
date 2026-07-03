"""REST API for the STT dictionary — custom vocabulary + misrecognition fixes.

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET    /api/dictionary        → {"entries": [entry, ...]} in insertion order.
    POST   /api/dictionary        → create one entry; 201 (400 on duplicate).
    PATCH  /api/dictionary/{id}   → partial edit; returns the entry; 404.
    DELETE /api/dictionary/{id}   → remove (idempotent → 200, {"removed": bool}).

An entry is one canonical ``word`` plus optional ``misheard`` variants — see
``jarvis/speech/stt_dictionary.py``. Storage is the atomic JSON sidecar under
``user_data_dir()/data/``; corrections apply on the NEXT utterance (the
corrector live-reloads on file change), so no restart is required.

Like the Contacts/Socials endpoints this router has **no Brain dependency**,
so it works headless / with MockBrain (open-source universality, CLAUDE.md §3).
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from jarvis.speech.stt_dictionary import DictionaryStore

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dictionary", tags=["dictionary"])

# Serializes read-modify-write across the per-request DictionaryStore
# instances. Endpoints are sync ``def`` (FastAPI threadpool), so a
# threading.Lock fits (same pattern as contacts_routes).
_LOCK = threading.Lock()


def _store() -> DictionaryStore:
    # Constructed per request; resolves user_data_dir() at call time so the
    # LOCALAPPDATA-based test sandbox works (mirrors contacts_routes).
    return DictionaryStore()


# ----------------------------------------------------------------------
# Request models
# ----------------------------------------------------------------------


class EntryCreate(BaseModel):
    word: str
    misheard: list[str] = []


class EntryUpdate(BaseModel):
    word: str | None = None
    misheard: list[str] | None = None


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------


@router.get("")
def list_entries() -> dict[str, Any]:
    with _LOCK:
        entries = _store().list_all()
    return {"entries": [e.to_dict() for e in entries]}


@router.post("", status_code=201)
def create_entry(body: EntryCreate) -> dict[str, Any]:
    try:
        with _LOCK:
            entry = _store().add(body.word, body.misheard)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("STT dictionary: added %r (%d misheard).", entry.word, len(entry.misheard))
    return entry.to_dict()


@router.patch("/{entry_id}")
def update_entry(entry_id: str, body: EntryUpdate) -> dict[str, Any]:
    fields = body.model_dump(exclude_unset=True)
    try:
        with _LOCK:
            entry = _store().update(
                entry_id,
                word=fields.get("word"),
                misheard=fields.get("misheard"),
                misheard_set="misheard" in fields,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if entry is None:
        raise HTTPException(
            status_code=404, detail=f"No dictionary entry with id {entry_id!r}."
        )
    return entry.to_dict()


@router.delete("/{entry_id}")
def delete_entry(entry_id: str) -> dict[str, Any]:
    with _LOCK:
        removed = _store().delete(entry_id)
    return {"ok": True, "removed": removed}
