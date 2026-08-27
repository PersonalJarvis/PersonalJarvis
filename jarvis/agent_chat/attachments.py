"""Files dropped, pasted or picked into a chat composer.

The typed chats — the front page and the Agentic IDE's chat mode — take a
sentence and nothing else. Someone with a screenshot of a broken layout has to
describe it in words, which is the one thing a screenshot exists to avoid. The
IDE's TERMINAL panes have taken dropped files since 2026-08 (``drops`` +
``drop_analysis``); the chats were never wired to any of it, so a drop landed
on a bare ``<textarea>`` and the browser navigated away to the file.

This module is that wiring, and it is deliberately thin: everything hard was
already solved one layer down and is reused rather than rewritten.

* :mod:`jarvis.agentic_ide.drops` writes the bytes into the chat's own working
  directory (``.jarvis/drops``, self-ignoring) and hands back a reference the
  agent can open.
* :mod:`jarvis.agentic_ide.drop_analysis` has a vision-capable model describe
  an image and extracts a document's text.
* :func:`jarvis.agentic_ide.prompt_blueprint.attachment_block` lays the result
  out for whatever answers the turn.

Why the description travels with the message rather than the file alone: a
chat can be answered by a coding CLI, by a provider's own API loop, or by
Jarvis' brain, and several of those cannot open an image at all. Sending only
a path means the person drops a picture, types "what is wrong here", and the
model receives a filename and a pronoun. The description is what makes the
gesture work on every seat instead of the maintainer's.

Import-light on purpose (AP-26): the heavy halves are imported inside the
functions, so a boot that never sees an attachment pays nothing for this.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "MAX_TOTAL_BYTES",
    "AttachmentError",
    "compose",
    "ingest",
    "to_analysis",
]

#: Total cap for one attach gesture, shared with the terminal drop path so the
#: two surfaces cannot disagree about what is too large.
MAX_TOTAL_BYTES = 100 * 1024 * 1024


class AttachmentError(RuntimeError):
    """The attach carried nothing usable, or the copies could not be written."""


async def ingest(
    cwd: str | Path,
    *,
    paths: list[str] | None = None,
    uploads: list[tuple[str, bytes]] | None = None,
    provider: str = "",
) -> list[Any]:
    """Store what was dropped in ``cwd`` and read what is in it.

    ``paths`` are real locations the browser managed to hand over (an Explorer
    or Finder drag usually carries them, and inside the desktop shell the host
    resolves them for every drop). ``uploads`` are ``(name, bytes)`` for
    everything with no path at all — a pasted screenshot, an image dragged off
    a web page.

    A path already inside ``cwd`` is referenced where it lies; anything else is
    copied in, because an agent's reach outside its working directory is
    neither guaranteed nor silent.

    Returns ``drop_analysis.DropAnalysis`` entries — one per file, in the order
    they arrived. Raises :class:`AttachmentError` when nothing usable was
    carried; an analysis that fails costs the description and nothing else, so
    the file still travels.
    """
    from jarvis.agentic_ide import drops

    folder = Path(cwd).expanduser()  # noqa: ASYNC240 — string work; the stat below is threaded
    if not await asyncio.to_thread(folder.is_dir):
        raise AttachmentError(f"Not a folder: {folder}")

    references: list[str] = []
    # (name, bytes, reference) for everything attached — the reference travels
    # with the bytes so a description can never drift onto another file.
    readable: list[tuple[str, bytes, str]] = []
    to_copy: list[tuple[str, bytes]] = []

    for raw in paths or []:
        candidate = (raw or "").strip()
        if not candidate:
            continue
        inside = drops.within_workspace(candidate, folder)
        if inside is not None:
            reference = drops.reference(inside, agent=provider)
            references.append(reference)
            try:
                body = await asyncio.to_thread((folder / inside).read_bytes)
            except OSError as exc:
                # The reference still ships; the file simply goes undescribed.
                log.info("chat attach: %r not readable for analysis (%s)", inside, exc)
            else:
                readable.append((Path(inside).name, body, reference))
            continue
        # expanduser() is string work; the read itself goes to a worker thread
        # (a dropped file may live on a slow network share).
        resolved = Path(candidate).expanduser()  # noqa: ASYNC240
        try:
            data = await asyncio.to_thread(resolved.read_bytes)
        except OSError as exc:
            log.info("chat attach: unreadable dropped path %r (%s)", candidate, exc)
            continue
        to_copy.append((resolved.name, data))

    total = sum(len(data) for _name, data in to_copy)
    for name, data in uploads or []:
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            megabytes = MAX_TOTAL_BYTES // (1024 * 1024)
            raise AttachmentError(f"That attachment is too large (max {megabytes} MB in total).")
        if data:
            to_copy.append((name or "file", data))

    # ``store`` skips empty entries silently, so they are dropped HERE instead —
    # that keeps ``stored[i]`` paired with ``to_copy[i]`` positionally.
    to_copy = [(name, data) for name, data in to_copy if data]

    if to_copy:
        try:
            stored = await asyncio.to_thread(drops.store, folder, to_copy)
        except drops.DropError as exc:
            raise AttachmentError(str(exc)) from exc
        for item, (_original, data) in zip(stored, to_copy, strict=True):
            reference = drops.reference(item.relative_path, agent=provider)
            references.append(reference)
            readable.append((item.name, data, reference))

    if not references:
        raise AttachmentError("That attachment carried nothing this chat could use.")

    return await _analyze(readable)


async def _analyze(readable: list[tuple[str, bytes, str]]) -> list[Any]:
    """Describe images and extract documents. Never raises — this is a bonus.

    The files are already stored and referenced by the time this runs, so a
    provider outage here must cost the description and nothing more.
    """
    import mimetypes

    from jarvis.agentic_ide import drop_analysis
    from jarvis.brain.drop_context import DroppedItem

    if not readable:
        return []
    items = [
        (
            DroppedItem(
                name=name,
                mime=mimetypes.guess_type(name)[0] or "application/octet-stream",
                data=data,
            ),
            reference,
        )
        for name, data, reference in readable
    ]
    try:
        return await drop_analysis.analyze(items)
    except Exception as exc:  # noqa: BLE001 — the attach itself already succeeded
        log.info("chat attach: analysis failed (%s)", exc)
        return []


def to_analysis(raw: list[dict[str, Any]] | None) -> list[Any]:
    """Rebuild the analyses a composer hands back on the wire.

    The client holds what :func:`ingest` returned while the person finishes
    typing, then posts it with the message. It is request data, so nothing here
    may assume a shape: a malformed entry becomes an empty-detail attachment
    rather than a 500 on a message somebody is waiting to send.
    """
    from jarvis.agentic_ide import drop_analysis

    out: list[Any] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        item = drop_analysis.DropAnalysis.from_dict(entry)
        if item.name:
            out.append(item)
    return out


def compose(text: str, attachments: list[Any] | None) -> str:
    """The message as the turn will actually receive it.

    The person's sentence first, then what was attached — the sentence is the
    task, the files are context for it, and a model reads the instruction
    better when it is not buried under a page of extracted PDF.

    Returns ``text`` unchanged when nothing is attached, so an ordinary message
    is never reshaped by a feature it did not use.
    """
    from jarvis.agentic_ide.prompt_blueprint import attachment_block

    said = (text or "").strip()
    block = attachment_block(list(attachments or []))
    if not block:
        return said
    lead = (
        "Files the user attached to this message are below. Their contents are "
        "given in full because the file itself may not be openable from here — "
        "read them as part of the message."
    )
    return "\n\n".join(part for part in (said, lead, block) if part)
