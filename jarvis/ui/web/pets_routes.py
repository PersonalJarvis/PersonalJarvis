"""REST API for the desktop pets — the ``pet`` overlay style (docs/pets.md).

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET    /api/pets                   → active pet, look settings, visibility,
                                         and every pet (built-in + user-created).
    GET    /api/pets/template.png      → the blank sprite-sheet template.
    GET    /api/pets/{id}/sheet.png    → a pet's sprite sheet.
    PUT    /api/pets/active            → pick the active pet (or "none"); saved
                                         to ``[ui] pet_id`` and applied live.
    PUT    /api/pets/settings          → size, status bubble, always-on strip; saved, applied.
    POST   /api/pets/visibility        → hide / show the pet (this run only).
    POST   /api/pets                   → create a pet from an uploaded sheet.
    DELETE /api/pets/{id}              → delete a user-created pet.

Under the CLI-first contract every action here is also a
``jarvis api pets <op>`` command. Every change that lands is announced as
``PetChanged`` so every open settings page redraws from one event.

Instances: both desktop instances read ONE ``jarvis.toml``, and only the
default one draws the overlay (``jarvis.core.instance``). The two writes of
the shared overlay settings — ``PUT /active`` and ``PUT /settings`` — answer
409 on a dev instance for the same reason ``PUT /api/settings/overlay-style``
does: a pick there would silently change the default app's pet on its next
restart. User-created pets live in each instance's own data directory, so
creating and deleting them is allowed everywhere; visibility is runtime-only.

The pet engine (``jarvis.ui.pets``) decodes images with Pillow, so it is
imported inside the handlers: nothing here loads on the boot path (AP-26).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from starlette.responses import Response
from starlette.types import Message, Receive

from jarvis.core.config import clamp_pet_scale, normalize_pet_id
from jarvis.ui.pets.states import DEFAULT_PET_ID, NO_PET_ID, PET_STATES

log = logging.getLogger(__name__)

#: Ceiling of a whole request body on this router: the 2 MB sheet plus the
#: manifest and the form overhead. Checked BEFORE the multipart parser runs,
#: because Starlette spools an entire upload to disk before a handler (or a
#: FastAPI dependency) ever sees it — a per-handler read cap alone would still
#: accept a multi-gigabyte body.
MAX_REQUEST_BYTES = 3 * 1024 * 1024

_TOO_LARGE = "The upload is too large (the sprite sheet may be at most 2 MB)."


def _limited_receive(receive: Receive, limit: int) -> Receive:
    """Wrap ``receive`` so a body without ``Content-Length`` stops at ``limit``."""
    seen = 0

    async def counting() -> Message:
        nonlocal seen
        message = await receive()
        if message.get("type") == "http.request":
            seen += len(message.get("body", b""))
            if seen > limit:
                raise HTTPException(status_code=413, detail=_TOO_LARGE)
        return message

    return counting


class _BodyLimitRoute(APIRoute):
    """An ``APIRoute`` that refuses an oversized body before parsing it.

    A declared ``Content-Length`` above :data:`MAX_REQUEST_BYTES` is answered
    with 413 at once; a chunked body without one is counted while it streams
    and cut off at the same ceiling.
    """

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def limited(request: Request) -> Response:
            if request.method in ("POST", "PUT", "PATCH"):
                declared = request.headers.get("content-length")
                if declared is None:
                    request = Request(
                        request.scope, _limited_receive(request.receive, MAX_REQUEST_BYTES)
                    )
                else:
                    try:
                        size = int(declared)
                    except ValueError:
                        raise HTTPException(
                            status_code=400, detail="Invalid Content-Length header."
                        ) from None
                    if size > MAX_REQUEST_BYTES:
                        raise HTTPException(status_code=413, detail=_TOO_LARGE)
            return await handler(request)

        return limited


router = APIRouter(prefix="/api/pets", tags=["pets"], route_class=_BodyLimitRoute)

#: A user-created pet's sheet never changes: a new upload gets a new id.
_IMMUTABLE_CACHE = {"Cache-Control": "public, max-age=31536000, immutable"}
#: A built-in sheet can change with an app update under the same URL, so the
#: browser revalidates it (FileResponse answers with an ETag).
_REVALIDATE_CACHE = {"Cache-Control": "no-cache"}
_NOSNIFF = {"X-Content-Type-Options": "nosniff"}


class ActiveBody(BaseModel):
    pet_id: str = Field(..., min_length=1, max_length=40)


class SettingsBody(BaseModel):
    """All optional; only the ones sent are changed."""

    scale: float | None = Field(default=None, description="Size multiplier, 0.5–2.0")
    bubble: bool | None = Field(default=None, description="Show the status bubble")
    strip_always: bool | None = Field(
        default=None, description="Keep the control strip up even at rest"
    )
    preview: bool = Field(
        default=False,
        description=(
            "Apply live only: nothing is written and no PetChanged goes out. "
            "The size slider sends this while it is dragged and a normal save on release."
        ),
    )


class VisibilityBody(BaseModel):
    visible: bool


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ui(request: Request) -> Any | None:
    cfg = getattr(request.app.state, "config", None) or getattr(request.app.state, "cfg", None)
    return getattr(cfg, "ui", None)


def _desktop(request: Request) -> Any | None:
    return getattr(request.app.state, "desktop_app", None)


def _configured_pet_id(request: Request) -> str:
    return normalize_pet_id(getattr(_ui(request), "pet_id", None)) or DEFAULT_PET_ID


def _configured_scale(request: Request) -> float:
    return clamp_pet_scale(getattr(_ui(request), "pet_scale", 1.0))


def _configured_bubble(request: Request) -> bool:
    return bool(getattr(_ui(request), "pet_bubble", True))


def _configured_strip_always(request: Request) -> bool:
    return bool(getattr(_ui(request), "pet_strip_always", False))


def _pet_visible(request: Request) -> bool:
    """Runtime visibility, asked of the running DesktopApp (True headless)."""
    getter = getattr(_desktop(request), "pet_visible", None)
    if not callable(getter):
        return True
    try:
        return bool(getter())
    except Exception:  # noqa: BLE001 — a status read must never 500
        log.debug("desktop_app.pet_visible() failed; reporting visible", exc_info=True)
        return True


def _config_path() -> Any:
    """The ``jarvis.toml`` this process loaded (honours ``JARVIS_CONFIG``)."""
    from jarvis.core.config import resolve_config_path

    return resolve_config_path()


def _set_ui_value(request: Request, field: str, value: object) -> None:
    """Best-effort in-memory update so a later read agrees before a restart."""
    ui = _ui(request)
    if ui is None:
        return
    try:
        setattr(ui, field, value)
    except Exception as exc:  # noqa: BLE001 — a frozen model is not an error
        log.debug("in-memory ui.%s update skipped: %s", field, exc)


def _refuse_on_secondary_instance() -> None:
    from jarvis.core.instance import current_instance

    instance = current_instance()
    if not instance.owns_ambient_duties:
        raise HTTPException(
            status_code=409,
            detail=(
                f"The desktop pet belongs to the default app; the {instance.label} "
                "instance draws no overlay and does not change the shared setting. "
                "Change the pet in the regular Personal Jarvis app."
            ),
        )


def _pet_exists(pet_id: str) -> bool:
    """``none`` always exists; any other id must be a pet folder on disk."""
    if pet_id == NO_PET_ID:
        return True
    from jarvis.ui.pets import loader

    return loader.find_pet_dir(pet_id) is not None


def _pet_json(manifest: Any) -> dict[str, Any]:
    """One pet as the settings page needs it.

    ``animations`` is resolved for EVERY state (a state the manifest leaves
    out already carries its fallback's row), so no client has to repeat the
    fallback table.
    """
    animations: dict[str, dict[str, Any]] = {}
    for state in PET_STATES:
        _resolved, spec = manifest.spec_for(state)
        animations[state] = {
            "row": spec.row,
            "frames": spec.frames,
            "fps": spec.fps,
            "loop": spec.loop,
            # 0 / 1 when the row has no accent (every cell on every loop).
            "accent_frames": int(getattr(spec, "accent_frames", 0)),
            "accent_every": int(getattr(spec, "accent_every", 1)),
        }
    return {
        "id": manifest.id,
        "name": manifest.name,
        "description": manifest.description,
        "builtin": bool(manifest.builtin),
        "frame_size": manifest.frame_size,
        "animations": animations,
        "sheet_url": f"/api/pets/{manifest.id}/sheet.png",
    }


async def _apply(request: Request, method: str, *args: Any) -> tuple[bool, str]:
    """Call a DesktopApp pet method off the loop; ``(applied_live, detail)``.

    Headless (no DesktopApp, or one without the method) is not an error: the
    value is saved and the overlay reads it at its next start.
    """
    apply = getattr(_desktop(request), method, None)
    if not callable(apply):
        return False, ""
    try:
        result = await asyncio.to_thread(apply, *args)
    except Exception as exc:  # noqa: BLE001 — never fail a saved change on an apply hiccup
        from jarvis.ui.web.error_text import LOG_HINT

        log.warning("pet live-apply %s failed (saved; applies on restart): %s", method, exc)
        return False, f"The change is saved but could not be applied live. {LOG_HINT}"
    applied = bool(result.get("applied_live")) if isinstance(result, dict) else bool(result)
    return applied, ""


async def _publish_changed(request: Request, pet_id: str, *, source: str) -> None:
    bus = getattr(request.app.state, "bus", None)
    if bus is None:
        return
    from jarvis.core.events import PetChanged

    try:
        await bus.publish(
            PetChanged(
                pet_id=pet_id,
                scale=_configured_scale(request),
                bubble=_configured_bubble(request),
                visible=_pet_visible(request),
                source=source,
            )
        )
    except Exception as exc:  # noqa: BLE001 — a bus hiccup must not fail the change
        log.warning("PetChanged publish failed: %s", exc)


def _state(request: Request, active: str) -> dict[str, Any]:
    return {
        "active": active,
        "scale": _configured_scale(request),
        "bubble": _configured_bubble(request),
        "strip_always": _configured_strip_always(request),
        "visible": _pet_visible(request),
    }


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


@router.get("")
def list_pets(request: Request) -> dict[str, Any]:
    """The active pet, its look, whether it is showing, and every pet."""
    from jarvis.ui.pets import loader

    manifests = loader.list_pets()
    known = {m.id for m in manifests}
    configured = _configured_pet_id(request)
    # An id whose folder is gone (a deleted or broken pet) shows the default
    # pet on screen, so the page must say the same.
    active = configured if configured == NO_PET_ID or configured in known else DEFAULT_PET_ID
    return {**_state(request, active), "pets": [_pet_json(m) for m in manifests]}


#: The blank sprite-sheet template "Create pet" offers for download: seven
#: tinted state rows in ``PET_STATES`` order, eight 48 px cells each.
_TEMPLATE_PNG = Path(__file__).resolve().parents[1] / "pets" / "template" / "template.png"


@router.get("/template.png", response_model=None)
def pet_template() -> FileResponse:
    """The sprite-sheet template for a user-drawn pet.

    Registered before the ``/{pet_id}/...`` routes so no pet id can shadow it.
    Ships with the app and changes only with an app update, so the browser
    revalidates it like a built-in sheet.
    """
    if not _TEMPLATE_PNG.is_file():
        raise HTTPException(status_code=404, detail="The template is not installed.")
    return FileResponse(
        str(_TEMPLATE_PNG),
        media_type="image/png",
        filename="pet-template.png",
        headers={**_NOSNIFF, **_REVALIDATE_CACHE},
    )


@router.get("/{pet_id}/sheet.png", response_model=None)
def pet_sheet(pet_id: str) -> FileResponse:
    """A pet's sprite sheet. The id is shape-checked before any path is built."""
    from jarvis.ui.pets import loader
    from jarvis.ui.pets.manifest import PetManifestError

    normalized = normalize_pet_id(pet_id)
    found = loader.find_pet_dir(normalized) if normalized else None
    if found is None:
        raise HTTPException(status_code=404, detail="Unknown pet.")
    folder, builtin = found
    try:
        manifest = loader.read_manifest(folder, builtin=builtin)
    except PetManifestError as exc:
        log.warning("pet %r sheet not served: %s", normalized, exc)
        raise HTTPException(status_code=404, detail="Unknown pet.") from exc
    return FileResponse(
        str(folder / manifest.sheet),
        media_type="image/png",
        headers={**_NOSNIFF, **(_REVALIDATE_CACHE if builtin else _IMMUTABLE_CACHE)},
    )


@router.put("/active")
async def put_active(body: ActiveBody, request: Request) -> dict[str, Any]:
    """Pick the active pet: save ``[ui] pet_id``, swap the figure live."""
    pet_id = normalize_pet_id(body.pet_id)
    if pet_id is None:
        raise HTTPException(status_code=400, detail=f"'{body.pet_id.strip()}' is not a pet id.")
    if not await asyncio.to_thread(_pet_exists, pet_id):
        raise HTTPException(status_code=404, detail=f"There is no pet '{pet_id}'.")
    _refuse_on_secondary_instance()

    _set_ui_value(request, "pet_id", pet_id)
    persisted = False
    try:
        from jarvis.core import config_writer

        await asyncio.to_thread(config_writer.set_pet_id, pet_id, path=_config_path())
        persisted = True
    except Exception as exc:  # noqa: BLE001 — the live apply is still worth trying
        log.warning("pet_id persist failed (live apply still attempted): %s", exc)

    applied_live, detail = await _apply(request, "set_pet", pet_id)
    await _publish_changed(request, pet_id, source="settings")
    return {
        "ok": True,
        **_state(request, pet_id),
        "persisted": persisted,
        "applied_live": applied_live,
        "detail": detail,
    }


@router.put("/settings")
async def put_settings(body: SettingsBody, request: Request) -> dict[str, Any]:
    """Change the pet's size, status bubble or always-on strip; saved and applied live."""
    if body.scale is None and body.bubble is None and body.strip_always is None:
        raise HTTPException(
            status_code=400, detail="Send at least one of 'scale', 'bubble', 'strip_always'."
        )
    _refuse_on_secondary_instance()

    from jarvis.core import config_writer

    # Memory, then the desktop, then disk: the pet resizes without waiting
    # for the locked TOML write behind it.
    if body.scale is not None:
        _set_ui_value(request, "pet_scale", clamp_pet_scale(body.scale))
    if body.bubble is not None:
        _set_ui_value(request, "pet_bubble", bool(body.bubble))
    if body.strip_always is not None:
        _set_ui_value(request, "pet_strip_always", bool(body.strip_always))

    applied_live, detail = await _apply(
        request,
        "set_pet_look",
        _configured_scale(request),
        _configured_bubble(request),
        _configured_strip_always(request),
    )
    active = _configured_pet_id(request)
    if body.preview:
        # One step of a slider drag: the release that follows saves and announces it.
        return {
            "ok": True,
            **_state(request, active),
            "persisted": False,
            "applied_live": applied_live,
            "detail": detail,
        }

    writes: list[tuple[str, Callable[..., None], object]] = []
    if body.scale is not None:
        writes.append(("pet_scale", config_writer.set_pet_scale, _configured_scale(request)))
    if body.bubble is not None:
        writes.append(("pet_bubble", config_writer.set_pet_bubble, bool(body.bubble)))
    if body.strip_always is not None:
        writes.append(
            ("pet_strip_always", config_writer.set_pet_strip_always, bool(body.strip_always))
        )
    persisted = True
    for key, write, value in writes:
        try:
            await asyncio.to_thread(write, value, path=_config_path())
        except Exception as exc:  # noqa: BLE001 — already applied live; the response reports it
            persisted = False
            log.warning("%s persist failed (applied live, lost on restart): %s", key, exc)

    await _publish_changed(request, active, source="settings")
    return {
        "ok": True,
        **_state(request, active),
        "persisted": persisted,
        "applied_live": applied_live,
        "detail": detail,
    }


@router.post("/visibility")
async def post_visibility(body: VisibilityBody, request: Request) -> dict[str, Any]:
    """Hide or show the pet for this run. Nothing is saved: the next start shows it."""
    applied_live, detail = await _apply(request, "set_pet_visible", bool(body.visible))
    active = _configured_pet_id(request)
    await _publish_changed(request, active, source="settings")
    return {
        "ok": True,
        **_state(request, active),
        "applied_live": applied_live,
        "detail": detail,
    }


@router.post("", status_code=201)
async def create_pet(
    sheet: UploadFile = File(...),  # noqa: B008 — FastAPI dependency default
    name: str = Form(""),
    description: str = Form(""),
    manifest: str | None = Form(None),
    frame_size: str | None = Form(None),
) -> dict[str, Any]:
    """Create a pet from an uploaded sprite sheet (and optional ``pet.json``).

    The sheet is read with a ceiling rather than whole — one byte over the
    limit is enough for the store to refuse it. Every refusal is a 400 whose
    detail is the store's own user-readable sentence.
    """
    from jarvis.ui.pets import loader
    from jarvis.ui.pets.manifest import MAX_SHEET_BYTES, PetManifestError
    from jarvis.ui.pets.store import PetStore

    size: int | None = None
    if frame_size is not None and frame_size.strip():
        try:
            size = int(frame_size.strip())
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"'{frame_size.strip()}' is not a frame size."
            ) from None

    data = await sheet.read(MAX_SHEET_BYTES + 1)
    store = PetStore(loader.custom_root())
    try:
        created = await asyncio.to_thread(
            store.add,
            sheet_bytes=data,
            name=name,
            description=description,
            manifest_json=manifest,
            frame_size=size,
        )
    except PetManifestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("Pet %r created (%s).", created.id, created.name)
    return _pet_json(created)


@router.delete("/{pet_id}", openapi_extra={"x-jarvis-dangerous": True})
async def delete_pet(pet_id: str, request: Request) -> dict[str, Any]:
    """Delete a user-created pet. Deleting the active one switches to the default."""
    from jarvis.ui.pets import loader
    from jarvis.ui.pets.manifest import USER_ID_RE
    from jarvis.ui.pets.store import PetStore

    normalized = normalize_pet_id(pet_id)
    if normalized is None:
        raise HTTPException(status_code=404, detail="Unknown pet.")
    if not USER_ID_RE.match(normalized):
        raise HTTPException(
            status_code=400, detail="Built-in pets cannot be deleted, only pets you created."
        )
    store = PetStore(loader.custom_root())
    if not await asyncio.to_thread(store.delete, normalized):
        raise HTTPException(status_code=404, detail="Unknown pet.")
    log.info("Pet %r deleted.", normalized)

    active = _configured_pet_id(request)
    if active != normalized:
        return {"ok": True, "deleted": normalized, "active": active}

    # The pet on screen is gone: fall back to the default pet. On a secondary
    # instance the shared setting is not this instance's to change (its pets
    # live in its own data directory, so the default app's pick is unaffected).
    from jarvis.core.instance import current_instance

    if current_instance().owns_ambient_duties:
        _set_ui_value(request, "pet_id", DEFAULT_PET_ID)
        try:
            from jarvis.core import config_writer

            await asyncio.to_thread(config_writer.set_pet_id, DEFAULT_PET_ID, path=_config_path())
        except Exception as exc:  # noqa: BLE001 — the live apply is still worth trying
            log.warning("pet_id fallback persist failed: %s", exc)
        await _apply(request, "set_pet", DEFAULT_PET_ID)
        await _publish_changed(request, DEFAULT_PET_ID, source="pet_deleted")
    return {"ok": True, "deleted": normalized, "active": DEFAULT_PET_ID}
