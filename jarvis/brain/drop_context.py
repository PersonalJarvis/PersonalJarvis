"""Capture dragged-and-dropped OS content as SILENT conversation context.

A user drops files / images / text onto the Jarvis presence (the in-app dock or
the floating overlay). This module is the **shared, surface-agnostic intake**:
it classifies the dropped items, composes one bounded, human-readable context
note, and (for images) returns the multimodal :class:`ImageBlock` payloads so the
brain can later *see* a dropped picture.

**A drop never triggers a brain turn.** The user keeps the normal speaking flow;
``ingest_drop`` hands the content to ``brain.add_dropped_context``, which keeps
the text in the conversation context (history) and parks images for the NEXT real
user turn. Jarvis only reacts when the user next speaks or types — a drop while
idle is remembered for next time, a drop mid-flow joins the running context.

No FastAPI / no Tk imports here — both the web route and the desktop overlay call
this module.
"""
from __future__ import annotations

import base64
import mimetypes
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from jarvis.core.protocols import ImageBlock

#: ``MessageSent.source_layer`` stamped on a drag-drop turn. Mirrored in
#: ``jarvis.brain.manager._NON_SPAWN_SOURCE_LAYERS`` so a dropped file is
#: *reacted to / discussed* inline, never auto-force-spawned into a worker
#: (AP-5/AP-14, anti-doom-loop). Parity test in tests/unit/brain/test_routing.py.
DROP_SOURCE_LAYER = "ui.drop"

# Defaults (overridable) — bound the per-file text and the whole directive so a
# huge drop cannot blow the token budget or the WS broadcast circuit-breaker.
_DEFAULT_MAX_TEXT_CHARS = 8_000
_DEFAULT_MAX_TOTAL_CHARS = 12_000
# Per-image budget before it ships to the brain. Anthropic caps a single image at
# 5 MB; 4 MB keeps headroom. Over-budget images are downscaled/JPEG-encoded by
# ``cap_image_b64`` (best-effort, never raises, no-op when already small).
_DEFAULT_MAX_IMAGE_BYTES = 4 * 1024 * 1024

# Extensions we confidently inline as UTF-8 text even when the OS hands us a
# generic/forged ``application/octet-stream`` MIME (drag-drop MIME is unreliable).
_TEXT_EXTS = frozenset({
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".xml", ".html", ".htm", ".css", ".svg",
    ".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".kt",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".rb", ".php", ".swift",
    ".sh", ".bash", ".zsh", ".ps1", ".bat", ".sql", ".r", ".lua", ".pl",
})

# MIME prefixes/values we treat as inline text regardless of extension.
_TEXTUAL_MIME_PREFIXES = ("text/",)
_TEXTUAL_MIMES = frozenset({
    "application/json", "application/xml", "application/javascript",
    "application/x-yaml", "application/yaml", "application/toml",
    "application/x-sh", "application/sql",
})


@dataclass(frozen=True, slots=True)
class DroppedItem:
    """One dropped item normalised to (name, MIME, raw bytes).

    The web route builds these from multipart ``UploadFile``s; the desktop
    overlay builds them by reading dropped paths. ``name`` is a display
    file name (no directory component is required).
    """

    name: str
    mime: str
    data: bytes


#: Default total byte cap when reading dropped file paths (overlay drop). Mirrors
#: the web route's multipart cap so both surfaces are bounded the same way.
_DEFAULT_MAX_TOTAL_BYTES = 25 * 1024 * 1024


def items_from_paths(
    paths: Sequence[str],
    *,
    max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES,
) -> list[DroppedItem]:
    """Read dropped file ``paths`` into :class:`DroppedItem`s (overlay drop).

    The native overlay drop hands file *paths*; this reads them (bounded by a
    total byte cap, MIME guessed from the name) so they feed the same
    ``ingest_drop`` as the web dock's multipart bytes. Missing/unreadable paths
    and files that would breach the cap are skipped, never raised.
    """
    items: list[DroppedItem] = []
    total = 0
    for raw in paths:
        try:
            p = Path(raw)
            if not p.is_file():
                continue
            size = p.stat().st_size
            if total + size > max_total_bytes:
                continue
            data = p.read_bytes()
        except OSError:
            continue
        # Exact re-check on the bytes actually read (closes the stat→read TOCTOU
        # window: a file that grew between stat and read can't breach the cap).
        if total + len(data) > max_total_bytes:
            continue
        total += len(data)
        name = p.name or "file"
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        items.append(DroppedItem(name=name, mime=mime, data=data))
    return items


def _suffix(name: str) -> str:
    return PurePosixPath(name.replace("\\", "/")).suffix.lower()


def _is_image(item: DroppedItem) -> bool:
    return item.mime.lower().startswith("image/")


def _is_textual(item: DroppedItem) -> bool:
    mime = item.mime.lower()
    if mime.startswith(_TEXTUAL_MIME_PREFIXES) or mime in _TEXTUAL_MIMES:
        return True
    return _suffix(item.name) in _TEXT_EXTS


def _is_pdf(item: DroppedItem) -> bool:
    return item.mime.lower() == "application/pdf" or _suffix(item.name) == ".pdf"


def _extract_pdf_text(data: bytes, *, max_chars: int) -> str:
    """Best-effort PDF text extraction; empty string when unavailable.

    ``pypdf`` is optional — never a hard dependency. Any failure (missing lib,
    encrypted/garbled PDF) degrades to "" so the caller falls back to a name note.
    """
    try:
        import io

        from pypdf import PdfReader  # optional

        reader = PdfReader(io.BytesIO(data))
        parts: list[str] = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
            if sum(len(p) for p in parts) >= max_chars:
                break
        return "\n".join(parts).strip()[:max_chars]
    except Exception:  # noqa: BLE001 — optional, best-effort
        return ""


def classify_and_compose(
    items: Sequence[DroppedItem],
    dragged_text: str | None = None,
    *,
    max_text_chars: int = _DEFAULT_MAX_TEXT_CHARS,
    max_total_chars: int = _DEFAULT_MAX_TOTAL_CHARS,
    max_image_bytes: int = _DEFAULT_MAX_IMAGE_BYTES,
) -> tuple[str, tuple[ImageBlock, ...]]:
    """Classify dropped content into ``(directive_text, images)``.

    ``directive_text`` is ``""`` (and ``images`` empty) when nothing usable was
    dropped — the caller then dispatches no turn. The directive is English (an
    artifact); the brain replies in the user's conversation language via the
    normal output-language path.
    """
    notes: list[str] = []
    images: list[ImageBlock] = []
    names: list[str] = []

    for item in items:
        if not item.name:
            continue
        names.append(item.name)
        if _is_image(item):
            # Cap the per-image payload before it ships (Anthropic's 5 MB/image
            # limit). cap_image_b64 is a no-op when small, downscales otherwise,
            # and never raises — falling back to the original on any failure.
            from jarvis.vision.image_budget import cap_image_b64

            mime, data_b64 = cap_image_b64(
                item.mime or "image/png",
                base64.b64encode(item.data).decode("ascii"),
                max_image_bytes,
            )
            images.append(ImageBlock(mime=mime, data_b64=data_b64))
            notes.append(f"- {item.name} — image attached for you to see")
        elif _is_textual(item):
            body = item.data.decode("utf-8", errors="replace")[:max_text_chars]
            notes.append(f"- {item.name}:\n```\n{body}\n```")
        elif _is_pdf(item):
            body = _extract_pdf_text(item.data, max_chars=max_text_chars)
            if body:
                notes.append(f"- {item.name} (PDF):\n```\n{body}\n```")
            else:
                notes.append(
                    f"- {item.name} — PDF (text not extracted; ask me what you "
                    f"want and I'll open it)"
                )
        else:
            notes.append(
                f"- {item.name} — file ({item.mime or 'unknown'}, {len(item.data)} bytes)"
            )

    if dragged_text and dragged_text.strip():
        notes.append(f"- dragged text:\n```\n{dragged_text.strip()[:max_text_chars]}\n```")

    if not notes and not images:
        return "", ()

    dropped_list = ", ".join(names) if names else "some content"
    header = (
        f"\U0001F4CE (Context — I dropped this into our conversation: {dropped_list}.)"
    )
    instruction = (
        "Keep this in mind for whatever I say next — there's no need to react to "
        "it on its own."
    )
    directive = "\n".join([header, *notes, instruction]).strip()
    if len(directive) > max_total_chars:
        directive = directive[: max_total_chars - 2].rstrip() + " …"
    return directive, tuple(images)


async def ingest_drop(
    *,
    bus: Any = None,  # kept for call-site stability; the silent flow needs no bus
    brain: Any,
    thread_id: str = "default",  # noqa: ARG001 — kept for call-site stability
    items: Sequence[DroppedItem],
    dragged_text: str | None = None,
    trace_id: UUID | None = None,  # noqa: ARG001 — kept for call-site stability
) -> bool:
    """Capture dropped content as SILENT conversation context — never a turn.

    A drop must NOT make Jarvis think on its own (the user keeps the normal
    speaking flow). We stash the classified content on the brain via
    ``add_dropped_context``: the text joins the conversation context and any
    images are parked for the NEXT real user turn. Jarvis only reacts when the
    user next speaks/types. Returns ``True`` when something was captured,
    ``False`` for an empty drop or when no brain is available to hold context.
    """
    directive, images = classify_and_compose(items, dragged_text)
    if not directive:
        return False
    add = getattr(brain, "add_dropped_context", None)
    if not callable(add):
        return False
    add(directive, images)
    return True
