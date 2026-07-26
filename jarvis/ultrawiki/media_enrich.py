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
    "EnrichResult",
    "describe_image",
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
MAX_TRANSCRIPT_CHARS = 40_000

_DEFAULT_TIMEOUT_S = 90.0

_VISION_SYSTEM = (
    "You describe images for a personal search index. Write plainly and only "
    "about what is actually visible."
)

_VISION_PROMPT = (
    "Describe this image so its owner can find it again by searching in their "
    "own words months from now.\n\n"
    "Cover, in one short paragraph: what kind of picture it is (photo, "
    "screenshot, document scan, diagram), the setting, the notable objects, "
    "roughly how many people are in it and what they are doing, and the mood "
    "or occasion if it is evident. Then, if the image contains readable text, "
    "add a line 'Text:' followed by that text verbatim.\n\n"
    "Do not guess names of people or places. Do not invent details you cannot "
    "see. Write in English.\n\n"
    f"If no image reached you, reply with exactly {CANNOT_SEE_MARKER} and "
    "nothing else."
)

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
    return [(name, model) for name, model in chain if _can_see(registry, name)]


def _can_see(registry: Any, name: str) -> bool:
    """Whether a provider declares vision support. Never raises."""
    try:
        provider_class = registry.get_class(name)
    except Exception:  # noqa: BLE001 — an unloadable provider simply cannot see
        return False
    return bool(getattr(provider_class, "supports_vision", False))


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
                content=_VISION_PROMPT,
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
        max_tokens=900,
        stream=True,
    )

    result = await complete_with_fallback(
        registry=registry,
        chain=chain,
        request=request,
        timeout_s=timeout_s,
        label="UltraWikiImageDescriber",
        aggregate=_aggregate,
        validate=_validate_description,
    )
    if result is None:
        return EnrichResult(reason="no provider that can read images returned a usable description")
    aggregated, provider = result
    text = _clean(str(getattr(aggregated, "text", "") or ""))[:MAX_DESCRIPTION_CHARS]
    return EnrichResult(text=text, ok=True, provider=provider)


def _aggregate(chunks: Any) -> Any:
    """Collapse a provider's stream into one object carrying ``.text``."""

    @dataclass(slots=True)
    class _Aggregated:
        text: str

    if isinstance(chunks, str):
        return _Aggregated(text=chunks)
    parts: list[str] = []
    for chunk in chunks or ():
        piece = getattr(chunk, "text", None)
        parts.append(piece if isinstance(piece, str) else str(chunk))
    return _Aggregated(text="".join(parts))


def _validate_description(aggregated: Any) -> bool:
    """Reject a non-answer so the chain moves on to the next provider.

    The ``NO_IMAGE_RECEIVED`` check is the load-bearing one: it is how a
    provider that took the call but never saw the picture is caught before its
    invented description is stored as a memory.
    """
    text = _clean(str(getattr(aggregated, "text", "") or ""))
    if not text or len(text) < 15:
        return False
    return CANNOT_SEE_MARKER not in text.upper()


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
