"""What the corner card can do besides opening the editor.

The card (a PySide6 window in the indicator sidecar) shows Copy, Save and
Copy text on hover. The sidecar only reports the press; the work happens here
in the main process, on the held appshot itself:

* **Copy** goes through :mod:`jarvis.platform.clipboard_image`, which owns the
  clipboard natively on every OS. A Qt clipboard in the sidecar would lose the
  picture on Linux the moment the sidecar quits.
* **Save** writes a PNG into ``~/Downloads``, like every other save in the app.
* **Copy text** puts the appshot's scrubbed on-screen text (``ui_text``) on
  the clipboard — CleanShot's upload chip, swapped for what an assistant app
  reads off the screen anyway.

And when the editor closes, :func:`return_to_corner` slides the (edited)
appshot back into the corner, so it stays at hand — CleanShot X's Quick Access
Overlay. Every function here returns a result and never raises.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Card wording — app chrome, so it follows ``[ui].language`` like the hint.
_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "edit": "Edit",
        "copy": "Copy",
        "save": "Save",
        "close": "Close",
        "pin": "Keep on screen",
        "unpin": "Unpin",
        "copy_text": "Copy text",
        "copied": "Copied",
        "saved": "Saved to Downloads",
        "failed": "That did not work",
        "gone": "No longer kept",
        "text_copied": "Text copied",
        "no_text": "No text found",
    },
    "de": {
        "edit": "Bearbeiten",  # i18n-allow: product UI string
        "copy": "Kopieren",  # i18n-allow: product UI string
        "save": "Speichern",  # i18n-allow: product UI string
        "close": "Schließen",  # i18n-allow: product UI string
        "pin": "Am Bildschirm halten",  # i18n-allow: product UI string
        "unpin": "Nicht mehr halten",  # i18n-allow: product UI string
        "copy_text": "Text kopieren",  # i18n-allow: product UI string
        "copied": "Kopiert",  # i18n-allow: product UI string
        "saved": "In Downloads gespeichert",  # i18n-allow: product UI string
        "failed": "Das hat nicht geklappt",  # i18n-allow: product UI string
        "gone": "Nicht mehr aufbewahrt",  # i18n-allow: product UI string
        "text_copied": "Text kopiert",  # i18n-allow: product UI string
        "no_text": "Kein Text gefunden",  # i18n-allow: product UI string
    },
    "es": {
        "edit": "Editar",  # i18n-allow: product UI string
        "copy": "Copiar",  # i18n-allow: product UI string
        "save": "Guardar",  # i18n-allow: product UI string
        "close": "Cerrar",  # i18n-allow: product UI string
        "pin": "Mantener en pantalla",  # i18n-allow: product UI string
        "unpin": "Dejar de fijar",  # i18n-allow: product UI string
        "copy_text": "Copiar texto",  # i18n-allow: product UI string
        "copied": "Copiado",  # i18n-allow: product UI string
        "saved": "Guardado en Descargas",  # i18n-allow: product UI string
        "failed": "No ha funcionado",  # i18n-allow: product UI string
        "gone": "Ya no se conserva",  # i18n-allow: product UI string
        "text_copied": "Texto copiado",  # i18n-allow: product UI string
        "no_text": "No se encontró texto",  # i18n-allow: product UI string
    },
}


def card_labels(config: Any) -> dict[str, str]:
    language = str(getattr(getattr(config, "ui", None), "language", "") or "").lower()[:2]
    return dict(_LABELS.get(language, _LABELS["en"]))


def card_rest_ms(config: Any) -> int:
    """How long the card stays untouched; ``0`` = until the user closes it."""
    seconds = getattr(getattr(config, "appshot", None), "card_seconds", 6)
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        value = 6
    return max(0, value) * 1000


def as_png(image: bytes) -> bytes:
    """The held picture (JPEG or PNG) as PNG bytes."""
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return image
    from PIL import Image  # noqa: PLC0415

    with Image.open(io.BytesIO(image)) as picture:
        out = io.BytesIO()
        picture.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def save_to_downloads(png: bytes, *, folder: Path | None = None, now: float | None = None) -> Path:
    """Write ``png`` into Downloads under a fresh name and return the path."""
    target = folder or (Path.home() / "Downloads")
    target.mkdir(parents=True, exist_ok=True)
    moment = time.time() if now is None else now
    stem = time.strftime("appshot-%Y%m%d-%H%M%S", time.localtime(moment))
    path = target / f"{stem}.png"
    counter = 1
    while path.exists():
        counter += 1
        path = target / f"{stem}-{counter}.png"
    path.write_bytes(png)
    return path


async def run_card_action(action: str) -> str:
    """Do ``copy``, ``save`` or ``copy_text`` on the held appshot; the card's status line."""
    from jarvis.appshot.store import get_store  # noqa: PLC0415
    from jarvis.core.config import load_config  # noqa: PLC0415

    config = await asyncio.to_thread(load_config)
    labels = card_labels(config)
    shot = get_store().latest()
    if shot is None:
        return labels["gone"]
    try:
        if action == "copy_text":
            text = (shot.ui_text or "").strip()
            if not text:
                return labels["no_text"]
            from jarvis.platform.clipboard import write_text  # noqa: PLC0415

            ok = await asyncio.to_thread(write_text, text)
            return labels["text_copied"] if ok else labels["failed"]
        png = await asyncio.to_thread(as_png, shot.image)
        if action == "copy":
            from jarvis.platform.clipboard_image import write_png  # noqa: PLC0415

            ok = await asyncio.to_thread(write_png, png)
            return labels["copied"] if ok else labels["failed"]
        if action == "save":
            path = await asyncio.to_thread(save_to_downloads, png)
            log.info("appshot: card saved %s", path.name)
            return labels["saved"]
    except Exception:  # noqa: BLE001 - the card says so; nothing else depends on it
        log.warning("appshot: card action %r failed", action, exc_info=True)
        return labels["failed"]
    return labels["failed"]


async def return_to_corner(fly_from: list[float] | None = None) -> bool:
    """Bring the held appshot back into the corner card. ``False`` = not shown.

    ``fly_from`` (``[x, y, w, h]``, global logical pixels) is where the
    editor showed the picture: it then flies from there into the corner,
    like after the shutter. Without it the card slides in from the edge.
    """
    try:
        from jarvis.appshot.effect import card_hint, thumbnail_from_image  # noqa: PLC0415
        from jarvis.appshot.store import get_store  # noqa: PLC0415
        from jarvis.core.config import load_config  # noqa: PLC0415
        from jarvis.cu.indicator.controller import get_indicator_controller  # noqa: PLC0415

        controller = get_indicator_controller()
        shot = get_store().latest()
        if controller is None or shot is None:
            return False
        config = await asyncio.to_thread(load_config)
        if not bool(getattr(getattr(config, "appshot", None), "effect", True)):
            return False
        thumb = await asyncio.to_thread(thumbnail_from_image, shot.image)
        keep = float(config.screen_context.deck_preview_s) > 0
        return bool(
            await controller.show_card(
                thumb_b64=base64.b64encode(thumb).decode("ascii"),
                image_b64=base64.b64encode(shot.image).decode("ascii") if keep else "",
                hint=card_hint(config),
                rest_ms=card_rest_ms(config),
                labels=card_labels(config),
                fly_from=fly_from,
            )
        )
    except Exception:  # noqa: BLE001 - the card is a convenience, never a failure
        log.warning("appshot: could not return the appshot to the corner", exc_info=True)
        return False


__all__ = [
    "as_png",
    "card_labels",
    "card_rest_ms",
    "return_to_corner",
    "run_card_action",
    "save_to_downloads",
]
