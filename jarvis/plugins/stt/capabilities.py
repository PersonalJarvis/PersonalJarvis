"""Which fields ONE transcription request may carry — asked of the MODEL.

Why this is not a per-provider constant
---------------------------------------
Every hosted transcription endpoint in this repo speaks the same OpenAI-shaped
dialect, and yet they do not accept the same request. ``whisper-1`` answers
``response_format = "verbose_json"`` with per-segment timings and log
probabilities; ``gpt-4o-transcribe`` — the newer, genuinely multilingual model
this lane wants — rejects that value outright with HTTP 400 and transcribes
nothing. The same split runs through ``temperature`` and the bias ``prompt`` on
the ten-odd backends the OpenRouter gateway fronts.

So the shape of a request is a property of the MODEL, not of the vendor whose
URL it is posted to, and a plugin that hardcodes one shape can only ever be
correct for the models that existed when it was written. That is the failure
this module removes: sending a field the model does not support turns the whole
utterance into an error, and the user hears nothing back with no way to tell
that ONE optional field was the cause.

Two layers, and the second is the one that survives a model we have never seen
-----------------------------------------------------------------------------
1. **A declared default** (:func:`declared_shape`) keyed on markers in the model
   id — not on a provider name (AP-21). Whisper-family checkpoints get the full
   shape; anything else starts on the universal subset, because ``json`` is the
   one response format every transcription endpoint has ever accepted.
2. **A rejection is evidence** (:func:`shape_after_rejection`). When the service
   answers 400 and names a field, that field is dropped and the call retried —
   once per field — and the narrowed shape is remembered for the rest of the
   process (:func:`remember_shape`). A model released tomorrow therefore costs
   at most one wasted round trip on its first use instead of bricking the lane
   until somebody edits a table here.

The memory is deliberately process-local. A capability verdict written to disk
is a verdict nobody re-probes (AP-25's sticky-cache trap in miniature): a
provider having a bad minute would otherwise pin a model to a degraded shape
across every future restart with nothing in the log explaining why.

Import-clean by construction: no ``jarvis.*`` import at any level, so every STT
plugin — including the one held to a total import ban — can use it.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace
from typing import Any

log = logging.getLogger(__name__)

#: How many times ONE upload may be re-shaped before the error is surfaced.
#: There are four optional fields plus the model substitution, and a service
#: that objects to every one of them is not going to transcribe anything —
#: reporting that is more useful than a fifth attempt.
MAX_REQUEST_DOWNGRADES = 5

#: The response format every transcription endpoint accepts. Anything richer is
#: a capability, never an assumption.
UNIVERSAL_RESPONSE_FORMAT = "json"

#: The format that additionally carries per-segment timings and ``avg_logprob``
#: (which is where the confidence figure comes from).
RICH_RESPONSE_FORMAT = "verbose_json"


@dataclass(frozen=True, slots=True)
class RequestShape:
    """What one transcription request is allowed to contain.

    Every field is a permission, never an instruction: a ``True`` says the
    model would accept the field, and the caller still decides whether it has
    anything to put in it.
    """

    #: May carry a ``language`` field (a pinned recognition language).
    language: bool = True
    #: May carry a bias ``prompt`` (vocabulary priming).
    prompt: bool = True
    #: May carry a ``temperature``.
    temperature: bool = True
    #: May ask for ``verbose_json`` — segments, timings, ``avg_logprob``.
    verbose_json: bool = True

    @property
    def response_format(self) -> str:
        """The ``response_format`` value this model actually accepts."""
        return RICH_RESPONSE_FORMAT if self.verbose_json else UNIVERSAL_RESPONSE_FORMAT


#: The full shape — everything the OpenAI audio API has ever offered.
FULL_SHAPE = RequestShape()

#: The subset every transcription endpoint accepts. Used for a model whose id
#: tells us nothing, so an unknown model degrades to "works" rather than to a
#: 400 nobody can read.
UNIVERSAL_SHAPE = RequestShape(verbose_json=False)


#: Model-id markers that identify a Whisper-family checkpoint. Whisper is the
#: only transcription family that has always accepted the rich response format,
#: and its id says so on every vendor that hosts it (``whisper-1``,
#: ``whisper-large-v3``, ``openai/whisper-large-v3-turbo``, …).
_WHISPER_MARKERS: tuple[str, ...] = ("whisper",)


def declared_shape(model: str | None) -> RequestShape:
    """The shape to START a model on, before any service has objected.

    Whisper-family ids get the full shape (that is what their consumers have
    relied on for years — segment timings feed the confidence figure). Every
    other id, known or not, starts on :data:`UNIVERSAL_SHAPE`: plain ``json``
    plus the optional fields, which is what ``gpt-4o-transcribe`` and the
    gateway-hosted models accept. Being wrong in this direction costs a
    confidence number; being wrong in the other direction costs the utterance.
    """
    name = str(model or "").strip().lower()
    if not name:
        return UNIVERSAL_SHAPE
    if any(marker in name for marker in _WHISPER_MARKERS):
        return FULL_SHAPE
    return UNIVERSAL_SHAPE


#: Field name → the words a service uses when it refuses that field. Ordered:
#: the response format is checked first because a message about
#: ``verbose_json`` also contains the word ``response_format`` and nothing
#: else, while a message about ``temperature`` names only itself.
_REJECTION_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("verbose_json", ("verbose_json", "response_format", "response format")),
    ("temperature", ("temperature",)),
    ("prompt", ("prompt",)),
    ("language", ("language",)),
)


def shape_after_rejection(shape: RequestShape, message: str) -> RequestShape | None:
    """``shape`` minus the field ``message`` complained about, or ``None``.

    ``None`` means the refusal was not about an optional field — a dead key, a
    missing model, an audio format the endpoint will not take — and the caller
    must surface it instead of quietly retrying. That distinction is the whole
    safety of this mechanism: a retry ladder that treats every 400 as "drop
    something and try again" would strip a request down to nothing and report
    the last, least informative error.

    Only fields currently ENABLED can be dropped, so a repeated complaint about
    a field we already removed cannot spin the caller in a loop.
    """
    text = str(message or "").lower()
    if not text:
        return None
    for field_name, markers in _REJECTION_MARKERS:
        if not getattr(shape, field_name, False):
            continue
        if any(marker in text for marker in markers):
            return replace(shape, **{field_name: False})
    return None


# ---------------------------------------------------------------------------
# What this process has LEARNED about a model, from the services themselves
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_learned: dict[tuple[str, str], RequestShape] = {}


def _key(vendor: str, model: str | None) -> tuple[str, str]:
    return (str(vendor or "").strip().lower(), str(model or "").strip().lower())


def remember_shape(vendor: str, model: str | None, shape: RequestShape) -> None:
    """Record that ``vendor``/``model`` accepts at most ``shape``.

    Narrowing only: two concurrent calls that each learn a different rejection
    must end with BOTH fields dropped, not with whichever call finished last.
    """
    key = _key(vendor, model)
    with _lock:
        known = _learned.get(key)
        if known is None:
            _learned[key] = shape
            return
        _learned[key] = RequestShape(
            language=known.language and shape.language,
            prompt=known.prompt and shape.prompt,
            temperature=known.temperature and shape.temperature,
            verbose_json=known.verbose_json and shape.verbose_json,
        )


def resolve_shape(vendor: str, model: str | None) -> RequestShape:
    """The shape to send right now — what we learned, else what we declared."""
    with _lock:
        known = _learned.get(_key(vendor, model))
    return known if known is not None else declared_shape(model)


def reset_learned_shapes() -> None:
    """Forget every learned narrowing. Test-isolation hook."""
    with _lock:
        _learned.clear()


# ---------------------------------------------------------------------------
# The model itself was refused
# ---------------------------------------------------------------------------

#: What a service says when the MODEL — not a field — is the problem. A user
#: who pinned a model their account cannot call must still be able to dictate,
#: so the caller falls back to its own default model once and says so out loud.
#: Deliberately narrow: a phrase that also appears in an unrelated 400 would
#: hide a real configuration error behind a silent substitution.
_MODEL_REJECTION_MARKERS: tuple[str, ...] = (
    "model_not_found",
    "model not found",
    "does not exist",
    "no such model",
    "unknown model",
    "invalid model",
    "unsupported model",
    "is not a valid model",
    "not available",
)


def is_model_rejection(message: str, model: str | None = None) -> bool:
    """Whether ``message`` says the requested MODEL is unusable here.

    ``model`` is accepted so a caller can be sure the complaint is about the id
    it sent; when the id does not appear in the message the markers alone
    decide, because several services report the model error without echoing it.
    """
    text = str(message or "").lower()
    if not text:
        return False
    return any(marker in text for marker in _MODEL_REJECTION_MARKERS)


def error_text(response: Any) -> str:
    """The service's own words for a failed request, as a plain string.

    Duck-typed rather than typed against ``httpx``: this module is imported by
    the one plugin held to a total ``jarvis.*``-and-nothing-heavy contract, and
    reading two attributes needs no HTTP library. An unreadable body answers
    ``""``, which every caller treats as "no evidence, do not retry".
    """
    try:
        text = getattr(response, "text", "") or ""
    except Exception:  # noqa: BLE001 — a body we cannot read is simply no evidence
        return ""
    return str(text)[:2000]


def log_model_fallback(
    vendor: str, requested: str, fallback: str, detail: str = ""
) -> None:
    """Say out loud that a pinned model was refused and what ran instead.

    A silent substitution is the failure this repo calls AP-31 from the other
    side: the model picker would keep showing a choice that no request has used
    for weeks. One WARNING per occurrence, naming both ids and the service's
    own reason, is what makes that visible in a log somebody can read.
    """
    log.warning(
        "%s refused the configured transcription model %r (%s); this request "
        "used %r instead so dictation keeps working. Pick a model your account "
        "can call in the app's provider view to stop the substitution.",
        vendor,
        requested,
        (detail or "no detail").strip()[:200],
        fallback,
    )


__all__ = [
    "FULL_SHAPE",
    "MAX_REQUEST_DOWNGRADES",
    "RICH_RESPONSE_FORMAT",
    "UNIVERSAL_RESPONSE_FORMAT",
    "UNIVERSAL_SHAPE",
    "RequestShape",
    "declared_shape",
    "error_text",
    "is_model_rejection",
    "log_model_fallback",
    "remember_shape",
    "reset_learned_shapes",
    "resolve_shape",
    "shape_after_rejection",
]
