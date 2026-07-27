"""What a dropped file actually CONTAINS, worked out before an agent is briefed.

Dropping a screenshot on the prompt bar and dropping it on a pane are two
different products. The pane types a path and stops there, which is right for
"look at this file": the coding agent opens it itself. But a *screenshot* handed
to a coding agent is frequently a dead end — several of the CLIs this app drives
are text-only, and the ones that can see an image still have to be told to look
at it. The user meanwhile has dropped a picture of a broken layout and typed
"fix this", and what reaches the agent is a path and a pronoun.

So this module reads the drop first. An image gets described by a model that can
actually see it; a document gets its text pulled out; and the result is folded
into the prompt as context the agent can work from *whether or not* it can open
the file itself. The file reference still ships alongside — a vision-capable
agent should look at the original, and the description is the floor, not a
replacement.

Three rules this module is built around:

* **Capability, never a provider name** (AP-21). The describing model is chosen
  by ``supports_vision`` alone. A text-only provider is skipped rather than
  handed a picture, because it does not refuse one: it answers about the
  filename and the sentence around it, confidently, and that description is
  worse than none at all.
* **Degrade out loud** (§3). A downloader with one text-only key gets the
  extracted documents, the file references, and an honest line saying the image
  was not described. Nothing here is load-bearing enough to break a drop.
* **Never raise.** Every failure path returns an analysis carrying a note. The
  user dropped a file and typed an instruction; losing that to a provider
  timeout would be the worst outcome available.
"""
from __future__ import annotations

import asyncio
import base64
from collections.abc import Sequence
from dataclasses import dataclass

from loguru import logger

from jarvis.brain.drop_context import (
    DroppedItem,
    extract_pdf_text,
    is_image,
    is_pdf,
    is_textual,
)

#: Whole-analysis budget. Generous, because it runs while the user is still
#: typing their instruction — the drop is analysed the moment it lands, not at
#: send time, so this rarely sits on anybody's critical path. On expiry the
#: analyses collected so far ship and the rest carry a note.
ANALYSIS_TIMEOUT_S = 60.0

#: How many images one drop may spend model calls on. Dropping a folder of
#: twenty screenshots is a real thing to do by accident; the rest are still
#: attached and referenced, just not described.
MAX_IMAGES_ANALYZED = 4

#: Per-file extracted text. Enough for a spec or a stack trace, far short of
#: pasting a whole book into the brief.
MAX_TEXT_CHARS = 6_000

#: Everything this module contributes to one prompt, across all files.
MAX_TOTAL_CHARS = 20_000

#: Per-image payload before it ships to the model. Mirrors the drop intake's
#: budget; ``cap_image_b64`` downscales over-budget images and never raises.
MAX_IMAGE_BYTES = 4 * 1024 * 1024

#: How a described image is labelled, and how an extracted document is. Used by
#: the UI and by the prompt blueprint, so the two cannot disagree about what
#: kind of thing an attachment is.
KIND_IMAGE = "image"
KIND_TEXT = "text"
KIND_PDF = "pdf"
KIND_OTHER = "other"

#: ``described_by`` values — which layer produced ``detail``.
BY_VISION = "vision"
BY_EXTRACTION = "extraction"
BY_NONE = "none"

# What the describing model is asked for. Written for the reader it actually
# has: a coding agent that will act on this description and cannot see the
# picture. Hence "quote text verbatim" — a paraphrased error message cannot be
# grepped for, and grepping for it is the first thing the agent will want to do.
_VISION_SYSTEM = """\
You describe an image for a coding agent that cannot see it. The user dropped \
this image into a coding session, so the description IS the agent's only view \
of it. Be specific and complete; a vague description wastes the agent's turn.

Cover, in whatever order suits the image:
- What the image is: a UI screenshot, a terminal, an error dialog, a diagram, \
a photo, a design mockup.
- Every piece of readable text, QUOTED VERBATIM — error messages, stack \
traces, file paths, line numbers, labels, button text, code. Exact wording \
matters more than anything else here, because the agent will search the \
repository for these strings.
- The layout, when the image shows an interface: what sits where, how elements \
are arranged and aligned, spacing, sizing, colours, and anything visibly \
broken — overlapping, cut off, misaligned, overflowing, or unreadable.
- The state it shows: what appears selected, focused, disabled, loading, \
errored, or empty.

Write plain prose and short lists. Describe only what is visible: do not guess \
at the cause, do not propose a fix, and do not describe anything the image \
does not show.\
"""


@dataclass(frozen=True, slots=True)
class DropAnalysis:
    """One dropped file, and what was learned from its contents."""

    name: str
    """The file's display name."""

    reference: str
    """How the agent should refer to the file (``@path`` or a quoted path)."""

    kind: str
    """One of ``image`` / ``text`` / ``pdf`` / ``other``."""

    detail: str = ""
    """The vision description or the extracted text. Empty when neither ran."""

    described_by: str = BY_NONE
    """``vision``, ``extraction``, or ``none`` — which layer produced ``detail``."""

    note: str = ""
    """Why ``detail`` is empty or partial. Empty on the happy path."""

    def to_dict(self) -> dict[str, str]:
        """Wire shape — the same keys the frontend and the CLI receive."""
        return {
            "name": self.name,
            "reference": self.reference,
            "kind": self.kind,
            "detail": self.detail,
            "described_by": self.described_by,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> DropAnalysis:
        """Rebuild from the wire, tolerating anything missing or mistyped.

        The caller is a JSON request body, so nothing here may assume a shape.
        A malformed attachment becomes an empty-detail one rather than a 500 on
        a prompt the user is waiting to send.
        """

        def _text(key: str) -> str:
            value = raw.get(key) if isinstance(raw, dict) else None
            return value.strip() if isinstance(value, str) else ""

        kind = _text("kind") or KIND_OTHER
        return cls(
            name=_text("name") or "file",
            reference=_text("reference"),
            kind=kind if kind in {KIND_IMAGE, KIND_TEXT, KIND_PDF, KIND_OTHER} else KIND_OTHER,
            detail=_text("detail")[:MAX_TEXT_CHARS],
            described_by=_text("described_by") or BY_NONE,
            note=_text("note"),
        )


def _kind_of(item: DroppedItem) -> str:
    """Which of the four buckets this drop falls in. One register, shared."""
    if is_image(item):
        return KIND_IMAGE
    if is_textual(item):
        return KIND_TEXT
    if is_pdf(item):
        return KIND_PDF
    return KIND_OTHER


def _resolve_vision_brain():  # noqa: ANN202 - Brain | None, avoid an import cycle
    """The model that describes pictures, or None when nothing can see.

    Resolution lives in ``jarvis.brain.resolver`` so this module never grows its
    own idea of which provider to use. Any failure is None, not an exception.
    """
    try:
        from jarvis.brain.resolver import resolve_vision_brain
        from jarvis.core.config import load_config

        return resolve_vision_brain(load_config())
    except Exception as exc:  # noqa: BLE001 - a blind analysis is still an analysis
        logger.info("Agentic IDE drop analysis: no vision brain ({})", exc)
        return None


async def _describe_image(brain, item: DroppedItem) -> str:  # noqa: ANN001 - Brain
    """One bounded call that turns a picture into words. Raises on failure."""
    from jarvis.core.protocols import BrainMessage, BrainRequest, ImageBlock
    from jarvis.vision.image_budget import cap_image_b64

    mime, data_b64 = cap_image_b64(
        item.mime or "image/png",
        base64.b64encode(item.data).decode("ascii"),
        MAX_IMAGE_BYTES,
    )
    request = BrainRequest(
        messages=(
            BrainMessage(
                role="user",
                content=(
                    f"Describe this image ({item.name}) for the coding agent, "
                    "following your instructions."
                ),
                images=(ImageBlock(mime=mime, data_b64=data_b64),),
            ),
        ),
        system=_VISION_SYSTEM,
        # Description, not interpretation: the same picture should not produce a
        # different account on the second drop.
        temperature=0.2,
        max_tokens=2000,
        stream=True,
    )
    chunks: list[str] = []
    async for delta in brain.complete(request):
        if delta.content:
            chunks.append(delta.content)
    return "".join(chunks).strip()[:MAX_TEXT_CHARS]


async def _analyze_image(brain, item: DroppedItem, reference: str) -> DropAnalysis:  # noqa: ANN001
    """Describe one image, degrading to an honest note on any failure."""
    if brain is None:
        return DropAnalysis(
            name=item.name,
            reference=reference,
            kind=KIND_IMAGE,
            note=(
                "No provider that can see images is reachable, so this one was "
                "attached but not described."
            ),
        )
    try:
        text = await _describe_image(brain, item)
    except Exception as exc:  # noqa: BLE001 - a failed description is not a failed drop
        logger.info("Agentic IDE drop analysis: {} not described ({})", item.name, exc)
        return DropAnalysis(
            name=item.name,
            reference=reference,
            kind=KIND_IMAGE,
            note=f"Could not be described ({type(exc).__name__}); it is attached as a file.",
        )
    if not text:
        return DropAnalysis(
            name=item.name,
            reference=reference,
            kind=KIND_IMAGE,
            note="The description came back empty; it is attached as a file.",
        )
    return DropAnalysis(
        name=item.name,
        reference=reference,
        kind=KIND_IMAGE,
        detail=text,
        described_by=BY_VISION,
    )


def _analyze_document(item: DroppedItem, reference: str) -> DropAnalysis:
    """Pull the text out of a document. Pure CPU/decode work — no model."""
    kind = _kind_of(item)
    if kind == KIND_TEXT:
        body = item.data.decode("utf-8", errors="replace").strip()[:MAX_TEXT_CHARS]
        if body:
            return DropAnalysis(
                name=item.name,
                reference=reference,
                kind=KIND_TEXT,
                detail=body,
                described_by=BY_EXTRACTION,
            )
        return DropAnalysis(
            name=item.name, reference=reference, kind=KIND_TEXT, note="The file is empty."
        )
    if kind == KIND_PDF:
        body = extract_pdf_text(item.data, max_chars=MAX_TEXT_CHARS).strip()
        if body:
            return DropAnalysis(
                name=item.name,
                reference=reference,
                kind=KIND_PDF,
                detail=body,
                described_by=BY_EXTRACTION,
            )
        return DropAnalysis(
            name=item.name,
            reference=reference,
            kind=KIND_PDF,
            note=(
                "No text could be extracted (it may be a scan, or encrypted); "
                "it is attached as a file."
            ),
        )
    return DropAnalysis(
        name=item.name,
        reference=reference,
        kind=KIND_OTHER,
        note=(
            f"Binary file ({item.mime or 'unknown type'}, {len(item.data)} bytes) — "
            "attached for the agent to open."
        ),
    )


def _trim_to_budget(results: list[DropAnalysis]) -> list[DropAnalysis]:
    """Keep the whole analysis under ``MAX_TOTAL_CHARS``.

    Trimming is per-file and says so in the note, rather than dropping files
    off the end: an attachment that silently vanished between the chip the user
    saw and the prompt the agent got is the kind of quiet loss this codebase
    keeps paying for.
    """
    budget = MAX_TOTAL_CHARS
    trimmed: list[DropAnalysis] = []
    for item in results:
        if not item.detail:
            trimmed.append(item)
            continue
        if len(item.detail) <= budget:
            budget -= len(item.detail)
            trimmed.append(item)
            continue
        keep = max(0, budget)
        budget = 0
        trimmed.append(
            DropAnalysis(
                name=item.name,
                reference=item.reference,
                kind=item.kind,
                detail=item.detail[:keep].rstrip(),
                described_by=item.described_by if keep else BY_NONE,
                note=(
                    "Shortened to fit the prompt — the whole file is attached."
                    if keep
                    else "Too long to include here — the whole file is attached."
                ),
            )
        )
    return trimmed


async def analyze(
    items: Sequence[tuple[DroppedItem, str]],
    *,
    timeout_s: float = ANALYSIS_TIMEOUT_S,
    brain=None,  # noqa: ANN001 - Brain, avoid an import cycle
) -> list[DropAnalysis]:
    """Work out what each dropped file contains.

    ``items`` pairs each dropped file with the reference string the agent will
    use for it, so the analysis and the path the agent opens can never drift
    apart.

    Images are described concurrently by one vision-capable model; documents are
    extracted on worker threads. ``brain`` pins the describing model (tests, and
    a caller that already resolved one); leaving it None resolves one and returns
    honest notes when none can see.

    Never raises.
    """
    if not items:
        return []

    image_indexes = [i for i, pair in enumerate(items) if _kind_of(pair[0]) == KIND_IMAGE]
    describable = set(image_indexes[:MAX_IMAGES_ANALYZED])
    overflow = set(image_indexes[MAX_IMAGES_ANALYZED:])

    # Resolving a brain touches config and the provider registry; skip it
    # entirely for a drop of nothing but documents.
    writer = brain if brain is not None else (_resolve_vision_brain() if describable else None)

    results: list[DropAnalysis | None] = [None] * len(items)

    for index, (item, reference) in enumerate(items):
        if index in overflow:
            results[index] = DropAnalysis(
                name=item.name,
                reference=reference,
                kind=KIND_IMAGE,
                note=(
                    f"Not described — only the first {MAX_IMAGES_ANALYZED} images of a "
                    "drop are; it is attached as a file."
                ),
            )

    async def _run_image(index: int, item: DroppedItem, reference: str) -> None:
        results[index] = await _analyze_image(writer, item, reference)

    async def _run_document(index: int, item: DroppedItem, reference: str) -> None:
        # Decoding a 25 MB file (and pypdf on a big PDF) is real CPU work; it
        # does not belong on the event loop that is also serving the UI.
        results[index] = await asyncio.to_thread(_analyze_document, item, reference)

    tasks = [
        _run_image(index, item, reference)
        if index in describable
        else _run_document(index, item, reference)
        for index, (item, reference) in enumerate(items)
        if index not in overflow
    ]

    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True), timeout=timeout_s
        )
    except TimeoutError:
        logger.info(
            "Agentic IDE drop analysis: timed out after {:g}s — shipping what finished",
            timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 - analysis must never break a drop
        logger.info("Agentic IDE drop analysis failed: {}", exc)

    # Anything still unfilled either timed out or raised inside gather. It gets
    # the same honest treatment as any other undescribed file.
    for index, (item, reference) in enumerate(items):
        if results[index] is None:
            results[index] = DropAnalysis(
                name=item.name,
                reference=reference,
                kind=_kind_of(item),
                note="Analysis did not finish in time; it is attached as a file.",
            )

    return _trim_to_budget([r for r in results if r is not None])


__all__ = [
    "ANALYSIS_TIMEOUT_S",
    "BY_EXTRACTION",
    "BY_NONE",
    "BY_VISION",
    "KIND_IMAGE",
    "KIND_OTHER",
    "KIND_PDF",
    "KIND_TEXT",
    "MAX_IMAGES_ANALYZED",
    "MAX_TEXT_CHARS",
    "MAX_TOTAL_CHARS",
    "DropAnalysis",
    "analyze",
]
