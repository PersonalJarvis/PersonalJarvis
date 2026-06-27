"""REST-API fuer das User-Profile-System (Curator).

Endpoints:
    GET    /api/profile                          → UserProfile-Meta + People-Liste + Review-Count
    GET    /api/profile/raw                       → rohe USER.md (Text) fuer Live-Anzeige
    PUT    /api/profile/raw                       → hand-edited USER.md zurueckschreiben (atomic)
    GET    /api/profile/reviews                  → Pending-Review-Queue des Curators
    POST   /api/profile/reviews/{idx}/accept     → Candidate akzeptieren (via Merger.apply)
    POST   /api/profile/reviews/{idx}/reject     → Candidate verwerfen

Wird vom WebServer in `_build_app()` eingehaengt:

    from .profile_routes import router as profile_router
    app.include_router(profile_router)

Abhaengigkeiten liegen auf ``app.state.brain``:
- ``brain._user_profile`` (UserProfile | None)
- ``brain._people``       (PersonStore | None)
- ``brain._curator``      (Curator | None)

Falls einer der drei ``None`` ist (z.B. MockBrain im Headless-Mode), liefern
wir einen 503 mit einer freundlichen Message — die UI zeigt dann einen
Empty-State statt eines roten Fehler-Badges.
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

# Single source of truth for the writable field vocabulary + field shapes,
# shared with the brain's update_profile tool so the inline editor and the brain
# can never drift on what is writable (the BUG-008 enum-drift class). The parity
# with the UI's CLUSTER_FIELD_KEYS is pinned by test_profile_update.py.
from jarvis.plugins.tool.profile_update import (
    _BOOL_FIELDS,
    _CANONICAL_FIELDS,
    _LIST_FIELDS,
    _coerce_bool,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profile", tags=["profile"])


# ----------------------------------------------------------------------
# Avatar storage — a profile picture for the "Who are you?" hero.
# ----------------------------------------------------------------------
#
# Intentionally decoupled from the Curator / USER.md: the avatar is a pure
# file artifact that lives under ``user_data_dir()/data`` so it works even
# when the profile subsystem is in its 503 (Mock/Headless) state. It is served
# through a dedicated FileResponse endpoint because the ``/assets`` static
# mount only exposes the built frontend bundle — never the user data dir.
#
# Stored under a single fixed basename ``profile_avatar.<ext>`` so there is at
# most one avatar; uploading a new one replaces the old, regardless of format.

# Format (detected by Pillow from the real magic bytes — NOT the request
# Content-Type, which a client can forge) → on-disk extension.
_AVATAR_FORMAT_EXT: dict[str, str] = {
    "PNG": ".png",
    "JPEG": ".jpg",
    "WEBP": ".webp",
    "GIF": ".gif",
}
# Extension → media type for the GET response.
_AVATAR_EXT_MEDIA: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_AVATAR_STEM = "profile_avatar"
_MAX_AVATAR_BYTES = 8 * 1024 * 1024  # 8 MB — generous for a portrait, caps abuse.


def _avatar_dir() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "data"


def _find_avatar() -> Path | None:
    """Return the current avatar file, or None if none is stored."""
    d = _avatar_dir()
    if not d.is_dir():
        return None
    for p in sorted(d.glob(f"{_AVATAR_STEM}.*")):
        if p.is_file() and p.suffix.lower() in _AVATAR_EXT_MEDIA:
            return p
    return None


def _has_avatar() -> bool:
    return _find_avatar() is not None


# ----------------------------------------------------------------------
# Helpers: Dependencies aus app.state.brain ziehen
# ----------------------------------------------------------------------


def _get_brain(request: Request) -> Any:
    """Holt den Brain-Container (BrainManager o.ae.) aus der App-State."""
    brain = getattr(request.app.state, "brain", None)
    if brain is None:
        raise HTTPException(
            status_code=503,
            detail="Brain noch nicht initialisiert — das Profil-System braucht "
                   "einen aktiven BrainManager.",
        )
    return brain


def _require_curator(request: Request):
    brain = _get_brain(request)
    curator = getattr(brain, "_curator", None)
    if curator is None:
        raise HTTPException(
            status_code=503,
            detail="Der Curator laeuft in dieser Session nicht — evtl. "
                   "Mock-Brain oder ein Provider ohne Memory-Integration.",
        )
    return curator


def _require_profile(request: Request):
    brain = _get_brain(request)
    profile = getattr(brain, "_user_profile", None)
    if profile is None:
        raise HTTPException(
            status_code=503,
            detail="USER.md ist noch nicht geladen — Workspace fehlt vermutlich.",
        )
    return profile


def _get_people(request: Request):
    """PersonStore ist optional — None wird zu leerer Liste."""
    brain = _get_brain(request)
    return getattr(brain, "_people", None)


# ----------------------------------------------------------------------
# Serialization
# ----------------------------------------------------------------------


def _person_to_dict(person: Any) -> dict[str, Any]:
    """Minimales Person-Summary fuer die UI-Liste."""
    return {
        "name": person.name,
        "relationship": person.relationship,
        "aliases": list(person.aliases),
        "slug": person.path.stem,
    }


def _candidate_to_dict(cand: Any, reason: str, idx: int) -> dict[str, Any]:
    """Serialisiert einen Review-Candidate (Extractor.Candidate) + Review-Grund."""
    value = cand.value
    # Listen/Dicts lassen wir durch — FastAPI JSONkodiert automatisch.
    return {
        "idx": idx,
        "subject": cand.subject,
        "is_person": cand.is_person,
        "person_name": cand.person_name,
        "cluster": cand.cluster,
        "field": cand.field,
        "value": value,
        "operation": cand.operation,
        "confidence": round(float(cand.confidence), 3),
        "evidence": cand.evidence,
        "relationship": cand.relationship,
        "reason": reason,
    }


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------


@router.get("")
async def get_profile(request: Request) -> dict[str, Any]:
    """Liefert das komplette Snapshot fuer die Profil-View.

    Shape (stabil fuer Frontend):
        {
          "user":   { "name": str|null, "meta": dict, "path": str },
          "people": [ { name, relationship, aliases, slug }, ... ],
          "reviews_count": int
        }
    """
    profile = _require_profile(request)
    people_store = _get_people(request)
    brain = _get_brain(request)
    curator = getattr(brain, "_curator", None)

    people_list: list[dict[str, Any]] = []
    if people_store is not None:
        try:
            people_list = [_person_to_dict(p) for p in people_store.list_all()]
        except Exception as exc:  # noqa: BLE001
            log.warning("People.list_all() fehlgeschlagen: %s", exc)
            people_list = []

    reviews_count = 0
    if curator is not None:
        try:
            reviews_count = len(curator.pending_reviews())
        except Exception as exc:  # noqa: BLE001
            log.warning("Curator.pending_reviews() fehlgeschlagen: %s", exc)

    return {
        "user": {
            "name": profile.name,
            "meta": profile.meta,
            "path": str(profile.path),
        },
        "people": people_list,
        "reviews_count": reviews_count,
        # Lets the HeroBand render the portrait vs. the placeholder without a
        # second round-trip / image-onError flash.
        "has_avatar": _has_avatar(),
    }


# ----------------------------------------------------------------------
# Avatar endpoints — upload / serve / delete the profile picture.
# ----------------------------------------------------------------------


@router.get("/avatar")
async def get_avatar(request: Request) -> Response:
    """Serve the stored avatar bytes, or 404 if none is set.

    ``no-store`` so a replace/delete is reflected immediately — an avatar is
    volatile, and a cached image would show a stale portrait after swapping.
    """
    path = _find_avatar()
    if path is None:
        raise HTTPException(status_code=404, detail="No avatar set.")
    media = _AVATAR_EXT_MEDIA.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(
        str(path),
        media_type=media,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.post("/avatar")
async def post_avatar(
    request: Request, file: UploadFile = File(...)  # noqa: B008 — FastAPI dependency default
) -> dict[str, Any]:
    """Upload (or replace) the profile picture.

    The bytes are validated as a real image via Pillow (magic-byte decode), so a
    forged Content-Type cannot smuggle arbitrary content onto disk. The on-disk
    extension is derived from the *detected* format, not the upload filename.
    Write is atomic (tempfile + os.replace); any previously stored avatar in a
    different format is removed so exactly one ``profile_avatar.*`` survives.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(data) > _MAX_AVATAR_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Image too large (max {_MAX_AVATAR_BYTES // (1024 * 1024)} MB).",
        )

    # Validate + detect format from the actual bytes (defense against a forged
    # Content-Type). verify() consumes the stream, so re-open to read .format.
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(data)) as probe2:
            fmt = (probe2.format or "").upper()
    except Exception as exc:  # noqa: BLE001 — any decode failure = not an image
        raise HTTPException(
            status_code=400,
            detail="File is not a valid image (PNG, JPEG, WebP or GIF expected).",
        ) from exc

    ext = _AVATAR_FORMAT_EXT.get(fmt)
    if ext is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image format '{fmt or 'unknown'}'. "
            "Use PNG, JPEG, WebP or GIF.",
        )

    dir_ = _avatar_dir()
    try:
        dir_.mkdir(parents=True, exist_ok=True)
        target = dir_ / f"{_AVATAR_STEM}{ext}"
        fd, tmp_path = tempfile.mkstemp(prefix=".avatar.", suffix=ext, dir=str(dir_))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp_path, target)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        # Drop any avatar stored under a different extension so only one remains.
        for old in dir_.glob(f"{_AVATAR_STEM}.*"):
            if old.resolve() != target.resolve():
                try:
                    old.unlink()
                except OSError:
                    pass
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("avatar write failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"ok": True, "has_avatar": True, "format": fmt}


@router.delete("/avatar")
async def delete_avatar(request: Request) -> dict[str, Any]:
    """Remove the avatar. Idempotent — deleting a missing avatar is a no-op 200."""
    removed = False
    dir_ = _avatar_dir()
    if dir_.is_dir():
        for p in dir_.glob(f"{_AVATAR_STEM}.*"):
            try:
                p.unlink()
                removed = True
            except OSError as exc:
                log.warning("avatar delete failed for %s: %s", p, exc)
    return {"ok": True, "removed": removed, "has_avatar": False}


@router.get("/raw")
async def get_raw(request: Request) -> dict[str, Any]:
    """Liefert die rohe USER.md als Text fuer Live-Anzeige in der UI.

    Shape:
        {
          "content": str,        # gesamter Markdown-Inhalt
          "path": str,           # absoluter Pfad (Anzeige-Zweck)
          "mtime_ms": int|null,  # Filesystem-Modification-Time (UI-Cache-Bust)
          "size_bytes": int      # Anzeige-Hilfe
        }

    Live-Sync: Sobald der Curator schreibt, publisht der Merger ein
    ``ProfileUpdated``-Event auf den Bus, das via WebSocket an die UI gestreamt
    wird. Das Frontend horcht darauf und invalidiert seinen Query-Cache, was
    diesen Endpoint erneut aufruft — Datei-Inhalt ist damit immer aktuell.
    """
    profile = _require_profile(request)
    path = profile.path
    try:
        content = path.read_text(encoding="utf-8")
        stat = path.stat()
    except FileNotFoundError:
        return {
            "content": "",
            "path": str(path),
            "mtime_ms": None,
            "size_bytes": 0,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("USER.md lesen fehlgeschlagen: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "content": content,
        "path": str(path),
        "mtime_ms": int(stat.st_mtime * 1000),
        "size_bytes": stat.st_size,
    }


class RawWriteBody(BaseModel):
    """Body for PUT /api/profile/raw — the full new USER.md text.

    ``mtime_ms`` is the modification time the client saw when it loaded the
    file (from GET /api/profile/raw). It powers an optimistic-concurrency
    guard: if the file changed in the meantime (a Curator merge, a parallel
    edit), we refuse the write with 409 instead of silently clobbering it.
    """

    content: str
    mtime_ms: int | None = None


@router.put("/raw")
async def put_raw(request: Request, body: RawWriteBody) -> dict[str, Any]:
    """Persists a hand-edited USER.md back to disk.

    The text is written verbatim (NOT re-rendered through write_frontmatter) so
    the user keeps full control over both the YAML frontmatter and the markdown
    body. Write is atomic (tempfile + os.replace) and UTF-8 without BOM —
    matching ``UserProfile.save()`` so the Curator never sees a half-written or
    BOM-corrupted file (BUG-018).

    After a successful write we ``reload()`` the in-memory profile so the
    cluster cards (GET /api/profile) reflect the edit immediately. Frontmatter
    parsing is lenient (malformed YAML → empty meta, never a crash), so a broken
    edit degrades to empty clusters with a ``frontmatter_ok: false`` warning
    rather than corrupting the system.
    """
    profile = _require_profile(request)
    path = profile.path

    # Optimistic concurrency — don't overwrite a file that changed under us.
    if body.mtime_ms is not None and path.exists():
        try:
            current_ms = int(path.stat().st_mtime * 1000)
        except OSError:
            current_ms = None
        # Small tolerance absorbs sub-second filesystem rounding; a real
        # Curator write moves the mtime by far more than this.
        if current_ms is not None and abs(current_ms - body.mtime_ms) > 1500:
            raise HTTPException(
                status_code=409,
                detail="USER.md changed in the meantime (Curator merge or a "
                       "parallel edit). Reload and re-apply your changes.",
            )

    text = body.content
    try:
        dir_ = path.parent
        dir_.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".USER.md.", suffix=".tmp", dir=str(dir_))
        try:
            # newline="" writes the LF the textarea sent verbatim — no CRLF
            # translation, no BOM (utf-8). Round-trips cleanly with get_raw.
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as exc:  # noqa: BLE001
        log.warning("USER.md schreiben fehlgeschlagen: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Re-parse the in-memory profile so cluster cards stay in sync with the file.
    reparsed = True
    try:
        profile.reload()
    except Exception as exc:  # noqa: BLE001
        log.warning("USER.md reload nach Edit fehlgeschlagen: %s", exc)
        reparsed = False

    # Heuristic: frontmatter delimiters present but nothing parsed out → almost
    # always malformed YAML. Surface it so the UI can warn the user.
    frontmatter_ok = True
    if text.lstrip().startswith("---") and not profile.meta:
        frontmatter_ok = False

    try:
        stat = path.stat()
        mtime_ms = int(stat.st_mtime * 1000)
        size_bytes = stat.st_size
    except OSError:
        mtime_ms = None
        size_bytes = len(text.encode("utf-8"))

    return {
        "ok": True,
        "path": str(path),
        "mtime_ms": mtime_ms,
        "size_bytes": size_bytes,
        "reparsed": reparsed,
        "frontmatter_ok": frontmatter_ok,
    }


class FieldEditBody(BaseModel):
    """Body for PATCH /api/profile/field — edit one structured profile field.

    ``operation`` decides what happens:
    - ``set``    — overwrite a scalar field (Chef → König). Rejected on list fields.
    - ``clear``  — empty any field so it reads back as "not known yet".
    - ``append`` — add one item to a list field (a chip). Rejected on scalars.
    - ``remove`` — drop one item from a list field (the chip 'x').

    ``value`` is required for set/append/remove, ignored for clear.
    """

    cluster: str
    field: str
    operation: Literal["set", "clear", "append", "remove"]
    value: Any = None


@router.patch("/field")
async def patch_field(request: Request, body: FieldEditBody) -> dict[str, Any]:
    """Inline single-field edit for the Profile view (the pencil per field).

    Mutates the SAME live ``UserProfile`` the brain renders from, persists it
    atomically to USER.md, logs an audit observation, and emits ``ProfileUpdated``
    so any other open view live-syncs. This is the manual-edit sibling of the
    brain's ``update_profile`` tool; they share the canonical field allow-list so
    the editor can never write a field the matrix / brain don't know about.
    """
    profile = _require_profile(request)

    cluster = body.cluster.strip().lower()
    field = body.field.strip().lower()
    op = body.operation
    value = body.value

    # Validate against the shared allow-list — never write an unknown field.
    if cluster not in _CANONICAL_FIELDS:
        raise HTTPException(status_code=400, detail=f"Unknown cluster {cluster!r}.")
    if field not in _CANONICAL_FIELDS[cluster]:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown field {field!r} for cluster {cluster!r}.",
        )

    is_list = (cluster, field) in _LIST_FIELDS

    # The operation must match the field shape (a set on a list would clobber it
    # into a scalar; an append on a scalar makes no sense).
    if op in ("append", "remove") and not is_list:
        raise HTTPException(
            status_code=400,
            detail=f"{cluster}.{field} is not a list field — use set or clear.",
        )
    if op == "set" and is_list:
        raise HTTPException(
            status_code=400,
            detail=f"{cluster}.{field} is a list field — use append or remove.",
        )

    # value is mandatory except for clear.
    if op in ("set", "append", "remove"):
        if value is None or (isinstance(value, str) and not value.strip()):
            raise HTTPException(status_code=400, detail="Missing 'value'.")

    # Coerce the boolean fields (emoji_ok) from the string the UI may send.
    if op == "set" and (cluster, field) in _BOOL_FIELDS:
        coerced = _coerce_bool(value)
        if coerced is None:
            raise HTTPException(
                status_code=400,
                detail=f"{cluster}.{field} expects a boolean (true/false).",
            )
        value = coerced
    if isinstance(value, str):
        value = value.strip()

    try:
        if op == "set":
            changed = profile.set(cluster, field, value)
        elif op == "clear":
            changed = profile.clear(cluster, field)
        elif op == "append":
            changed = profile.append_list(cluster, field, value)
        else:  # remove
            changed = profile.remove_list_item(cluster, field, value)
    except ValueError as exc:  # defensive — allow-list already guards this
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if changed:
        # Audit trail (best-effort — never block the write) mirroring update_profile.
        try:
            label = f"{cluster}.{field}"
            if op == "clear":
                obs_value = "(cleared)"
            elif op == "remove":
                obs_value = f"(removed {value})"
            else:
                obs_value = str(value)
            profile.append_observation(label, obs_value, "manual edit via profile UI")
        except Exception:  # noqa: BLE001
            log.debug("profile field edit: append_observation failed", exc_info=True)

        try:
            profile.save()
        except Exception as exc:  # noqa: BLE001
            log.warning("profile field edit: save failed: %s", exc)
            raise HTTPException(
                status_code=500, detail=f"Could not persist profile: {exc}"
            ) from exc

        # Live-sync any other open view (best-effort — a bus hiccup must never
        # fail the already-persisted write).
        bus = getattr(_get_brain(request), "_bus", None)
        if bus is not None:
            try:
                from jarvis.core.events import ProfileUpdated

                await bus.publish(
                    ProfileUpdated(
                        subject="user", cluster=cluster, field=field,
                        operation=op, confidence=1.0,
                        evidence="manual edit via profile UI",
                    )
                )
            except Exception:  # noqa: BLE001
                log.debug("profile field edit: ProfileUpdated publish failed", exc_info=True)

    return {
        "ok": True,
        "changed": changed,
        "cluster": cluster,
        "field": field,
        "operation": op,
        "value": profile.get(cluster, field),
    }


@router.get("/reviews")
async def get_reviews(request: Request) -> dict[str, Any]:
    """Liefert die Pending-Review-Queue des Curators.

    Jeder Eintrag bekommt seinen Index in der Queue — der ist fuer
    accept/reject der stabile Identifier innerhalb einer UI-Sitzung.
    Wenn jemand parallel etwas akzeptiert, kann der Index veralten —
    das Frontend sollte nach accept/reject neu fetchen.
    """
    curator = _require_curator(request)
    try:
        pending = curator.pending_reviews()
    except Exception as exc:  # noqa: BLE001
        log.warning("Review-Queue lesen fehlgeschlagen: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    reviews = [
        _candidate_to_dict(cand, reason, idx)
        for idx, (cand, reason) in enumerate(pending)
    ]
    return {"reviews": reviews, "total": len(reviews)}


@router.post("/reviews/{idx}/accept")
async def accept_review(idx: int, request: Request) -> dict[str, Any]:
    """Akzeptiert einen Kandidaten: Merger anwenden + aus Queue entfernen."""
    curator = _require_curator(request)
    queue = curator._review_queue  # noqa: SLF001 — internal, aber Public-API laut Task.
    if idx < 0 or idx >= len(queue):
        raise HTTPException(
            status_code=404,
            detail=f"Kein Review-Eintrag mit Index {idx} (Queue-Size={len(queue)}).",
        )
    cand, _reason = queue[idx]
    try:
        # Merger ist async — wir awaiten direkt.
        report = await curator._merger.apply([cand])  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        log.warning("Merger.apply fehlgeschlagen: %s", exc)
        raise HTTPException(status_code=500, detail=f"Merge-Fehler: {exc}") from exc

    # Aus der Queue entfernen — nach erfolgreichem (oder teilweise erfolgreichem)
    # Merge. Failures bleiben in den Logs, die UI zeigt danach einen frischen
    # State via Refetch.
    try:
        del queue[idx]
    except IndexError:
        # Race — ein anderer Request hat bereits entfernt. Nicht tragisch.
        pass

    return {
        "ok": True,
        "applied": report.applied,
        "skipped": report.skipped,
        "failed": report.failed,
        "details": list(report.details),
    }


@router.post("/reviews/{idx}/reject")
async def reject_review(idx: int, request: Request) -> dict[str, Any]:
    """Verwirft einen Kandidaten: nur aus Queue droppen, kein Merge."""
    curator = _require_curator(request)
    queue = curator._review_queue  # noqa: SLF001
    if idx < 0 or idx >= len(queue):
        raise HTTPException(
            status_code=404,
            detail=f"Kein Review-Eintrag mit Index {idx} (Queue-Size={len(queue)}).",
        )
    cand, _reason = queue[idx]
    try:
        del queue[idx]
    except IndexError:
        pass

    return {
        "ok": True,
        "dropped": {
            "subject": cand.subject,
            "field": cand.field,
        },
    }
