"""Turn a picture into words and a recording into a transcript.

The stage that finally makes a photo library searchable by what is IN it,
rather than only by when and where it was taken. It runs behind everything
else, one item at a time, and it is allowed to achieve nothing: an install
with no vision-capable provider and no speech recognition keeps its photos
findable by filename, folder and capture date, and drains this queue the day
a capable provider appears.

Three rules it does not bend:

* **Capability, never a provider name (AP-21).** The chain is filtered on
  ``supports_vision`` / the presence of a container-transcription method. A
  new provider becomes eligible by declaring the capability, and nothing here
  needs to learn its name.
* **A model that cannot see must never be asked to describe.** This is the
  one failure that would be worse than doing nothing: a text-only model handed
  an invisible image will happily invent a plausible photo, and that fiction
  would be indexed as memory. So the chain is filtered up front AND the prompt
  gives the model an explicit way to say it sees nothing, which is validated.
* **Off every hot path (AP-26 / AP-9).** Imports are lazy, the work happens in
  the pipeline's own worker, and nothing here is reachable from boot or from a
  voice turn.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

__all__ = [
    "CANNOT_SEE_MARKER",
    "NO_TEXT_MARKER",
    "PROGRAM_DIR_NAMES",
    "EnrichResult",
    "ImageReading",
    "describe_image",
    "image_prompt",
    "parse_image_answer",
    "skip_reason_for_image",
    "transcribe_recording",
    "vision_chain",
]

#: The exact token a model is told to answer when it received no picture.
#: Its presence in a reply is treated as "no description", never as content —
#: the difference between an honest gap and an indexed hallucination.
CANNOT_SEE_MARKER = "NO_IMAGE_RECEIVED"

#: Bytes of one media file handed to a provider. Above this the item keeps its
#: honest reason instead of an upload that would be refused anyway.
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_AUDIO_BYTES = 24 * 1024 * 1024

#: Characters kept from a description or transcript.
MAX_DESCRIPTION_CHARS = 4000

#: Characters kept from ONE picture's transcription + description together.
#: Larger than a description cap on purpose: a screenshot of a page carries a
#: page of words, and cutting it to caption length would discard exactly the
#: content this stage exists to recover.
MAX_READING_CHARS = 24_000
MAX_TRANSCRIPT_CHARS = 40_000

_DEFAULT_TIMEOUT_S = 90.0

_VISION_SYSTEM = (
    "You read and describe images for a personal search index. Transcribe "
    "what is written, and describe only what is actually visible."
)

#: The exact token a model answers in the TEXT block when a picture carries no
#: writing. Without one, a model narrates a sunset into the transcript slot and
#: an invented caption becomes indexed content.
NO_TEXT_MARKER = "NO_TEXT_IN_IMAGE"

#: Block headings of the answer. TEXT comes FIRST on purpose: a reply that
#: runs into the token ceiling loses its tail, and the tail must never be the
#: transcription — that is the part no other stage can reconstruct.
_TEXT_HEADING = "TEXT:"
_DESCRIPTION_HEADING = "DESCRIPTION:"


def image_prompt() -> str:
    """What the model is asked of one picture: read it first, then describe it.

    A description alone ("a screenshot of an application window") is
    unsearchable by anything its owner would actually type; for screenshots,
    scans and photographed notes the writing IS the content. So the answer is
    two blocks, transcription first.
    """
    return (
        "This picture belongs to someone's personal archive. Answer in exactly "
        "two blocks, in this order.\n\n"
        f"{_TEXT_HEADING}\n"
        "Every word visible in the image, transcribed VERBATIM - keep the "
        "original language, spelling, line breaks and reading order. Include "
        "menus, labels, captions, handwriting and numbers. Do not summarise, "
        "translate or correct anything. If the picture contains no readable "
        f"writing at all, put exactly {NO_TEXT_MARKER} here and nothing else.\n\n"
        f"{_DESCRIPTION_HEADING}\n"
        "One or two sentences: what kind of picture it is (photo, screenshot, "
        "scan, diagram), the setting, the notable objects, roughly how many "
        "people are in it and what they are doing.\n\n"
        "Do not guess names of people or places, and never invent detail you "
        "cannot see. Write the description in English; leave the transcription "
        "in its own language.\n\n"
        f"If no image reached you, reply with exactly {CANNOT_SEE_MARKER} and "
        "nothing else."
    )


#: Pictures below this are decoration, not content: icons, sprites, spacers,
#: cache thumbnails. Each one would still cost a full model call.
MIN_IMAGE_BYTES = 20 * 1024

#: Recordings below this hold no speech worth a model call. 16 kB is well
#: under a second of 16-bit mono PCM at any rate a microphone actually uses,
#: so it clears notification blips and truncated fragments without reaching
#: anything a person would call a recording. Deliberately far more permissive
#: than the image floor: a genuinely short voice note is still a voice note.
MIN_RECORDING_BYTES = 16 * 1024

#: Path SEGMENTS that mean "this file belongs to a program, not to the user".
#: Matched segment-wise and case-blind, so ``my-database-notes`` survives while
#: ``data/`` does not. The measured motivation: one real Desktop held 218,419
#: wake-word debug clips under ``data/wake_debug`` - the image side has the
#: same shape in caches and build output.
PROGRAM_DIR_NAMES: frozenset[str] = frozenset(
    {
        "data",
        "cache",
        ".cache",
        "caches",
        "logs",
        "dist",
        "build",
        "target",
        "out",
        "node_modules",
        "__pycache__",
        "site-packages",
        "appdata",
        "thumbnails",
        "coverage",
    }
)
# Deliberately NOT here: "tmp" / "temp". Every downloaded photo passes through
# a temp folder on some machine, and on this one every test path lives under
# one — a rule that broad stops being a noise filter and starts being the
# reason nothing is ever described.


def _program_folder_of(name: str) -> str:
    """The first program-folder segment in *name*, or ``""``.

    Segment-wise and case-blind, and never the last component, so a file
    called ``data.wav`` is judged by where it lives rather than by its name.
    """
    parts = [p.strip().lower() for p in str(name).replace("\\", "/").split("/")]
    for segment in parts[:-1]:  # the filename itself is never a folder
        if segment in PROGRAM_DIR_NAMES:
            return segment
    return ""


def skip_reason_for_image(name: str, *, size_bytes: int) -> str:
    """Why this picture is not worth a model call, or ``""`` to read it.

    The cheap gate in front of the expensive stage. Deliberately only two
    rules — size and provenance — because both are decidable from the item
    itself, offline, without opening the file.
    """
    if size_bytes < MIN_IMAGE_BYTES:
        return (
            f"the picture is too small to hold readable content "
            f"({size_bytes} bytes); icons and thumbnails are skipped"
        )
    segment = _program_folder_of(name)
    if segment:
        return (
            f"it sits in a program folder ({segment}/), which holds "
            "generated files rather than your own pictures"
        )
    return ""


def skip_reason_for_recording(name: str, *, size_bytes: int) -> str:
    """Why this recording is not worth a model call, or ``""`` to read it.

    The audio counterpart of :func:`skip_reason_for_image`, and the reason it
    now exists: the provenance rule above was written FROM the 218 419
    wake-word debug clips one real machine holds under ``data/wake_debug``,
    and then applied to pictures only — the exact files that motivated it went
    on queuing a transcription each, 220 520 model calls deep (measured live
    2026-07-27). A cheap gate that skips the case it was built for is not a
    gate.

    Two rules, both decidable offline from the item alone:

    * **Provenance** — a program folder holds a machine's own output. An app's
      debug dump, a cache, a build directory: none of it is something anyone
      recorded, and transcribing it produces noise at the cost of the corpus.
    * **Size** — below :data:`MIN_RECORDING_BYTES` there is less than a second
      of audio at any sane rate, which is a fragment rather than a recording.

    Deliberately NOT a rule: the file's own name. Real recordings are named
    anything at all, and a name-shaped filter would be exactly the kind of
    spelling-based gate that keeps producing both false skips and false
    passes (AP-27 makes the same argument about wake words).
    """
    segment = _program_folder_of(name)
    if segment:
        return (
            f"it sits in a program folder ({segment}/), which holds files a "
            "program wrote rather than anything you recorded"
        )
    if size_bytes < MIN_RECORDING_BYTES:
        return (
            f"the recording is too short to hold speech ({size_bytes} bytes); "
            "clips this small are skipped"
        )
    return ""


@dataclass(slots=True)
class ImageReading:
    """What one picture turned out to say, split from how it looks."""

    text: str = ""
    description: str = ""

    def as_body(self) -> str:
        """The indexed body: what was READ first, then what it looks like."""
        return "\n\n".join(part for part in (self.text, self.description) if part)


def parse_image_answer(answer: str) -> ImageReading:
    """Split the two-block reply; tolerate a model that drops the scaffolding.

    An answer with no headings is treated as a description rather than as a
    transcription: mistaking prose for verbatim text would quietly file a
    model's paraphrase as if it were the words on the page.
    """
    raw = str(answer or "").strip()
    if not raw:
        return ImageReading()
    upper = raw.upper()
    text_at = upper.find(_TEXT_HEADING)
    desc_at = upper.find(_DESCRIPTION_HEADING)
    if text_at < 0 and desc_at < 0:
        return ImageReading(description=_clean(raw))
    if text_at < 0:
        preamble = _clean(raw[:desc_at])
        body = _clean(raw[desc_at + len(_DESCRIPTION_HEADING) :])
        return ImageReading(description=_join(preamble, body))
    text_end = desc_at if desc_at > text_at else len(raw)
    text = _clean(raw[text_at + len(_TEXT_HEADING) : text_end])
    described = _clean(raw[desc_at + len(_DESCRIPTION_HEADING) :]) if desc_at > text_at else ""
    # Anything ahead of the first heading is description a model wrote before
    # following the format. Dropping it would silently lose the whole
    # description for every model that answers description-first.
    description = _join(_clean(raw[:text_at]), described)
    if NO_TEXT_MARKER in text.upper():
        text = ""
    return ImageReading(text=text, description=description)


def _join(*parts: str) -> str:
    return "\n\n".join(part for part in parts if part)


#: MIME types by media kind, from the filename. Providers need one, and a
#: wrong one is rejected by some APIs, so an unknown suffix falls back to the
#: family's most permissive type rather than to a guess.
_IMAGE_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".avif": "image/avif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


@dataclass(slots=True)
class EnrichResult:
    """What one enrichment attempt produced, and honestly why when nothing.

    ``retryable`` separates "this build cannot do it" (install a key, try
    again later) from "this file will never yield anything" (a corrupt photo),
    so a queue does not spin forever on the second.
    """

    text: str = ""
    ok: bool = False
    reason: str = ""
    provider: str = ""
    retryable: bool = True
    meta: dict[str, Any] = field(default_factory=dict)


def _suffix(filename: str) -> str:
    from pathlib import Path  # noqa: PLC0415 — lazy, keeps import cost off boot

    return Path(filename).suffix.lower()


def _image_mime(filename: str) -> str:
    return _IMAGE_MIME.get(_suffix(filename), "image/jpeg")


# ---------------------------------------------------------------------------
# Pictures
# ---------------------------------------------------------------------------


def vision_chain(cfg: Any, registry: Any) -> list[tuple[str, str | None]]:
    """The provider chain filtered down to those that can actually see.

    Built on the same key-aware, cross-family chain the distillation stage
    uses (AP-22), then narrowed by the ``supports_vision`` capability read off
    the provider CLASS — cheap, and it needs no credentials to evaluate.

    A provider whose vision support is only decided at runtime is trusted to
    declare a truthful default on its class; the prompt's explicit
    "I saw no image" escape is the second line of defence for exactly that
    case, so a wrong declaration costs a wasted call, never a fabricated
    description.
    """
    from jarvis.memory.wiki.provider_chain import (  # noqa: PLC0415 — lazy (AP-26)
        build_wiki_provider_chain,
        credential_ready_wiki_providers,
    )

    ultrawiki = getattr(cfg, "ultrawiki", None)
    configured = str(getattr(ultrawiki, "distill_provider", "") or "").strip()
    primary = configured or str(getattr(getattr(cfg, "brain", None), "primary", "") or "")

    available = set(registry.available())
    chain = build_wiki_provider_chain(
        primary=primary,
        model_override="",
        available=available,
        credential_ready=credential_ready_wiki_providers(available=available, config=cfg),
    )
    seeing: list[tuple[str, str | None]] = []
    for name, model in chain:
        supported, vision_model = _vision_model(registry, name, model)
        if supported:
            seeing.append((name, vision_model))
    return seeing


def _can_see(registry: Any, name: str) -> bool:
    """Whether a provider declares vision support. Never raises."""
    try:
        provider_class = registry.get_class(name)
    except Exception:  # noqa: BLE001 — an unloadable provider simply cannot see
        return False
    return bool(getattr(provider_class, "supports_vision", False))


def _vision_model(registry: Any, name: str, model: str | None) -> tuple[bool, str | None]:
    """Return a seeing model, rescuing a known-blind gateway default.

    Provider capability is the first gate. When the cached catalog additionally
    knows that this exact model is text-only, use the provider's fastest seeing
    sibling. Unknown modality stays fail-open because direct provider catalogs
    commonly omit it; a known-blind model with no sibling is excluded.
    """
    if not _can_see(registry, name):
        return False, None
    if not model:
        return True, model
    try:
        from jarvis.brain.model_catalog import (  # noqa: PLC0415 — lazy (AP-26)
            model_capabilities,
            pick_fast_vision_model,
        )

        if model_capabilities(name, model)["vision"] is False:
            sibling = pick_fast_vision_model(name)
            return sibling is not None, sibling
    except Exception:  # noqa: BLE001 — missing catalog keeps class capability
        log.debug("UltraWiki vision-model probe failed for %s", name, exc_info=True)
    return True, model


async def describe_image(
    data: bytes,
    *,
    filename: str,
    cfg: Any,
    registry: Any = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> EnrichResult:
    """One picture as searchable prose, or an honest reason why not."""
    if not data:
        return EnrichResult(reason="the file is empty", retryable=False)
    if len(data) > MAX_IMAGE_BYTES:
        return EnrichResult(
            reason=(
                f"the picture is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB, "
                "which is more than a provider will accept"
            ),
            retryable=False,
        )

    if registry is None:
        from jarvis.brain.provider_registry import (  # noqa: PLC0415 — lazy (AP-26)
            BrainProviderRegistry,
        )

        registry = BrainProviderRegistry()

    chain = vision_chain(cfg, registry)
    if not chain:
        return EnrichResult(
            reason=(
                "no configured provider can read images; connect one that "
                "does and this picture is described automatically"
            )
        )

    from jarvis.brain.streaming import aggregate  # noqa: PLC0415 — lazy (AP-26)
    from jarvis.core.protocols import (  # noqa: PLC0415 — lazy (AP-26)
        BrainMessage,
        BrainRequest,
        ImageBlock,
    )
    from jarvis.memory.wiki.provider_chain import (  # noqa: PLC0415 — lazy (AP-26)
        complete_with_fallback,
    )

    request = BrainRequest(
        messages=(
            BrainMessage(
                role="user",
                content=image_prompt(),
                images=(
                    ImageBlock(
                        mime=_image_mime(filename),
                        data_b64=base64.b64encode(data).decode("ascii"),
                    ),
                ),
            ),
        ),
        system=_VISION_SYSTEM,
        temperature=0.1,  # description, not creativity
        # A full screen of text is several thousand tokens; the old 900 was
        # sized for a caption and would cut a transcription off mid-sentence.
        max_tokens=4000,
        stream=True,
    )

    result = await complete_with_fallback(
        registry=registry,
        chain=chain,
        request=request,
        timeout_s=timeout_s,
        label="UltraWikiImageDescriber",
        aggregate=aggregate,
        validate=_validate_description,
        # Optional media enrichment has its own pause reason. It must neither
        # paint/clear the normal Markdown Wiki's global health strip nor demote
        # a provider for text work after a modality-specific image failure.
        record_health=False,
        failure_scope="ultrawiki-media",
    )
    if result is None:
        return EnrichResult(reason="no provider that can read images returned a usable description")
    aggregated, provider = result
    reading = parse_image_answer(str(getattr(aggregated, "text", "") or ""))
    # The transcription is bounded like a document, not like a caption: a
    # screenshot of a page legitimately carries more words than a description
    # ever would, and truncating it to caption length would lose the content
    # this stage exists to recover.
    body = reading.as_body()[:MAX_READING_CHARS]
    return EnrichResult(
        text=body,
        ok=True,
        provider=provider,
        meta={
            "read_chars": len(reading.text),
            "described_chars": len(reading.description),
        },
    )


def _validate_description(aggregated: Any) -> str | None:
    """Reject a non-answer so the chain moves on to the next provider.

    The ``NO_IMAGE_RECEIVED`` check is the load-bearing one: it is how a
    provider that took the call but never saw the picture is caught before its
    invented description is stored as a memory.
    """
    text = _clean(str(getattr(aggregated, "text", "") or ""))
    if not text or len(text) < 15:
        return "empty or trivial image description"
    if CANNOT_SEE_MARKER in text.upper():
        return "provider reported that no image reached it"
    return None


def _clean(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines()).strip()


# ---------------------------------------------------------------------------
# Recordings
# ---------------------------------------------------------------------------


async def transcribe_recording(
    data: bytes,
    *,
    filename: str,
    cfg: Any,
    stt: Any = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> EnrichResult:
    """A voice note or video soundtrack as text, or an honest reason why not.

    Uses the OPTIONAL ``transcribe_container`` capability: a provider that can
    take an encoded file (m4a, opus, mp3, mp4) rather than the raw PCM frames
    the live microphone path produces. Decoding those formats ourselves would
    mean a media library on every install, including headless servers that
    have no use for one — so the capability is asked for, and its absence is
    reported plainly instead of worked around.
    """
    if not data:
        return EnrichResult(reason="the file is empty", retryable=False)
    if len(data) > MAX_AUDIO_BYTES:
        return EnrichResult(
            reason=(
                f"the recording is larger than {MAX_AUDIO_BYTES // (1024 * 1024)} MB, "
                "which is more than a transcription provider will accept"
            ),
            retryable=False,
        )

    if stt is None:
        stt = await _resolve_stt(cfg)
    if stt is None:
        return EnrichResult(
            reason=(
                "no speech recognition is configured; set one up and this "
                "recording is transcribed automatically"
            )
        )

    transcribe = getattr(stt, "transcribe_container", None)
    if not callable(transcribe):
        configured = getattr(stt, "provider_name", None) or type(stt).__name__
        return EnrichResult(
            reason=(
                f"the configured speech recognition ({configured}) reads live "
                "microphone audio but cannot take an audio FILE; a provider "
                "that can is needed to transcribe this recording"
            )
        )

    import asyncio  # noqa: PLC0415 — lazy

    try:
        transcript = await asyncio.wait_for(transcribe(data, filename=filename), timeout=timeout_s)
    except TimeoutError:
        return EnrichResult(reason="transcription timed out")
    except Exception as exc:  # noqa: BLE001 — one file never breaks the queue
        log.debug("media enrich: transcription failed for %s", filename, exc_info=True)
        return EnrichResult(reason=f"transcription failed ({type(exc).__name__}: {exc})")

    text = _clean(str(getattr(transcript, "text", "") or transcript or ""))
    if not text:
        return EnrichResult(
            reason="the recording holds no recognisable speech",
            retryable=False,
        )
    return EnrichResult(
        text=text[:MAX_TRANSCRIPT_CHARS],
        ok=True,
        provider=type(stt).__name__,
    )


async def _resolve_stt(cfg: Any) -> Any:
    """The configured speech-recognition provider, or ``None``. Never raises.

    Built fresh rather than borrowed from the live voice pipeline on purpose:
    a background transcription must never contend with the microphone path for
    a native engine (AP-24 — a shared ctranslate2 model called concurrently
    hangs unrecoverably).
    """
    from jarvis.core import registry  # noqa: PLC0415 — lazy (AP-26)

    name = str(getattr(getattr(cfg, "stt", None), "provider", "") or "").strip()
    if not name:
        return None
    try:
        provider_class = registry.load("jarvis.stt", name)
    except Exception:  # noqa: BLE001 — an absent provider is simply absent
        log.debug("media enrich: STT provider %s unavailable", name, exc_info=True)
        return None
    # A provider that cannot take a file is not worth constructing: doing so
    # can load a multi-gigabyte model for a call that will be refused anyway.
    if not callable(getattr(provider_class, "transcribe_container", None)):
        return _Uncapable(provider_class.__name__)
    try:
        return provider_class(cfg)
    except Exception:  # noqa: BLE001 — a provider that will not build is absent
        log.debug("media enrich: STT provider %s did not build", name, exc_info=True)
        return None


class _Uncapable:
    """Stands in for a provider that exists but cannot read an audio file.

    Named rather than ``None`` so the reason a recording stays untranscribed
    says WHICH provider is configured — "no speech recognition" would be a
    lie when one is set up and simply reads live audio only.
    """

    def __init__(self, provider_name: str) -> None:
        self.provider_name = provider_name

    def __repr__(self) -> str:  # pragma: no cover — diagnostics only
        return f"<uncapable STT provider {self.provider_name}>"
