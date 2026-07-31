"""Why a transcription failed, in ONE word every layer can act on.

Why this file exists
--------------------
A failed transcription used to be recorded as whatever string the provider's
HTTP client happened to raise, and that string was rendered verbatim in the
dictation history. What a user actually saw under their own words was::

    HTTPStatusError: Client error '429 Too Many Requests' for url
    'https://api.groq.com/openai/v1/audio/transcriptions' For more information
    check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429

Three things are wrong with that, and only the first one is cosmetic:

* it is a stack-trace fragment shown to a person who wanted to dictate a
  sentence — it names a Python exception class, a vendor URL and a link to an
  HTTP specification, and none of those answer "what do I do now";
* it hard-codes the identity of a provider into the user-visible surface, which
  is exactly what §3 forbids — the message must read the same for whoever's key
  is configured, and it must not leak an endpoint into a stored history file;
* it is untranslatable. The string is assembled by a third-party library, so no
  locale can ever render it, and the runtime output-language doctrine (one
  resolver, ALL user-facing output) simply cannot reach it.

So the pipeline stores a **reason code** — the same discipline
``jarvis.dictation.outcomes`` already applies to how a dictation ended — and the
UI translates it through ``dictation.failure.<reason>``. The technical detail is
not lost: it keeps going to the log, in full, where the person debugging it
actually looks.

Deliberately string-matched, not type-matched
--------------------------------------------
The STT tier has five providers behind four different HTTP clients (httpx,
aiohttp, the google-genai SDK, and a bare ``OSError`` from the local engine), so
there is no shared exception type to switch on and a status attribute exists on
some of them only. Classification therefore runs over
``"<ExceptionType>: <message>"`` — a capability question ("could another
provider survive this?"), never a provider-identity one (AP-21).

This module is the single source of that judgement:
:mod:`jarvis.speech.stt_fallback` derives its own crossable/not-crossable answer
from :func:`classify_stt_failure` rather than keeping a second marker table,
because two tables that must agree are two tables that will not.
"""

from __future__ import annotations

from typing import Final

#: Every reason a transcription can fail, as stored on
#: ``DictationCompleted.error`` and in the dictation history sidecar. Mirrored
#: in TypeScript as ``STT_FAILURE_REASONS`` and pinned by a parity test — a
#: value missing from the mirror renders as a raw identifier at the user, which
#: is the drift this repo has hit four times (AP-4 / BUG-008).
#:
#: ``rate_limited``
#:     The provider accepted the key and refused the request: too many calls in
#:     its window. Waiting fixes it; changing anything else does not.
#: ``no_credit``
#:     The account behind the key is out of credit or past its billing quota.
#: ``bad_key``
#:     The key was missing, expired or rejected. The one reason that needs the
#:     user to go and do something in the API-keys view.
#: ``unavailable``
#:     The service could not be reached — a 5xx, a timeout, a dropped
#:     connection. Nothing is wrong with the account or the audio.
#: ``rejected``
#:     The provider understood the request and refused it (a 400-class answer:
#:     unsupported format, audio too large). Another provider would refuse the
#:     same bytes, so this is the one class that must NOT be retried elsewhere.
#: ``engine_busy``
#:     The local engine was still working on the previous call. A real and
#:     recurring shape here (AP-24 / BUG-036), and worth its own sentence: it is
#:     the one failure that is nobody's fault and fixes itself.
#: ``recording_interrupted``
#:     The microphone stream ended before the user stopped dictating. Not a
#:     transcription failure at all — but it is the reason the transcript is
#:     short, and presenting a truncated result as complete is the worse lie.
#: ``no_stt``
#:     No speech-to-text provider is configured or installed.
#: ``unknown``
#:     Anything else. Kept as an explicit member rather than an empty string so
#:     "it failed and we cannot say why" stays distinguishable from "it did not
#:     fail".
STT_FAILURE_REASONS: Final[tuple[str, ...]] = (
    "rate_limited",
    "no_credit",
    "bad_key",
    "unavailable",
    "rejected",
    "engine_busy",
    "recording_interrupted",
    "no_stt",
    "unknown",
)

# Ordered most-specific first: a 429 body frequently also says "quota", and a
# credential error frequently also says "invalid request", so the first match
# wins and the order below IS the precedence.

#: Deliberately narrow. A bare ``"busy"`` would also match a microphone the OS
#: refused, which is a different problem with a different answer.
_ENGINE_BUSY_MARKERS: Final[tuple[str, ...]] = (
    "transcribebusy",
    "already in flight",
    "resource busy",
    "device busy",
)
_RATE_LIMITED_MARKERS: Final[tuple[str, ...]] = (
    "429",
    "too many requests",
    "rate limit",
    "rate_limit",
    "ratelimit",
)

_NO_CREDIT_MARKERS: Final[tuple[str, ...]] = (
    "402",
    "payment required",
    "insufficient_quota",
    "insufficient quota",
    "quota",
    "billing",
    "credit balance",
    "out of credit",
)

_BAD_KEY_MARKERS: Final[tuple[str, ...]] = (
    "401",
    "403",
    "unauthorized",
    "invalid api key",
    "invalid_api_key",
    "api_key missing",
    "api key missing",
    "authentication",
    "permission denied",
)

_UNAVAILABLE_MARKERS: Final[tuple[str, ...]] = (
    "500",
    "502",
    "503",
    "504",
    "internal server error",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "overloaded",
    "temporarily",
    "timeout",
    "timed out",
    "connection",
    "connecterror",
    "readerror",
    "remoteprotocolerror",
)

_REJECTED_MARKERS: Final[tuple[str, ...]] = (
    "400",
    "413",
    "415",
    "422",
    "bad request",
    "invalid request",
    "unsupported",
    "too large",
)

#: reason -> the markers that identify it, in precedence order.
_MARKERS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("engine_busy", _ENGINE_BUSY_MARKERS),
    ("rate_limited", _RATE_LIMITED_MARKERS),
    ("no_credit", _NO_CREDIT_MARKERS),
    ("bad_key", _BAD_KEY_MARKERS),
    ("unavailable", _UNAVAILABLE_MARKERS),
    ("rejected", _REJECTED_MARKERS),
)

#: The reasons another provider deserves a shot at the SAME audio. ``rejected``
#: is excluded on purpose: the provider understood the request and refused it,
#: so re-sending those bytes elsewhere burns a second quota for the same answer.
#: ``unknown`` is excluded because an unclassified failure is, by definition,
#: not known to be survivable. ``engine_busy`` is excluded to keep the chain
#: behaving exactly as it did before this vocabulary existed: a busy local
#: engine is the normal "try again in a moment" of AP-24's non-blocking lock,
#: and turning every one of those into a cloud call would spend a request to
#: avoid a wait of milliseconds.
CROSSABLE_REASONS: Final[frozenset[str]] = frozenset(
    {"rate_limited", "no_credit", "bad_key", "unavailable"}
)

#: English sentences for the surfaces that have no locale of their own — the
#: log, the CLI, ``DictationCompleted.detail``. The UI does NOT read these; it
#: translates the reason code through ``dictation.failure.<reason>`` so the
#: message follows the user's language like every other user-facing string.
#: Deliberately provider-neutral: the sentence must read the same whichever key
#: the downloader configured (§3).
_MESSAGES: Final[dict[str, str]] = {
    "rate_limited": "Speech recognition hit the provider's rate limit.",
    "no_credit": "The speech recognition account is out of credit.",
    "bad_key": "The speech recognition key was rejected.",
    "unavailable": "The speech recognition service could not be reached.",
    "rejected": "The speech recognition service could not process this audio.",
    "engine_busy": "The speech recognition engine was still busy.",
    "recording_interrupted": "The recording ended before you stopped it.",
    "no_stt": "No speech-to-text provider is available.",
    "unknown": "Speech recognition failed.",
}


def classify_stt_failure(exc: BaseException | str | None) -> str:
    """One member of :data:`STT_FAILURE_REASONS` for ``exc``.

    Accepts an exception or an already-formatted string, because the failure
    crosses a couple of layers before it is stored and re-classifying a string
    must give the same answer as classifying the exception it came from.

    ``None`` and an empty string classify as ``"unknown"`` rather than raising:
    this runs on the path that is ALREADY handling a failure, and a classifier
    that can itself fail would turn a recoverable dictation into a crash.
    """
    if exc is None:
        return "unknown"
    if isinstance(exc, str):
        blob = exc.lower()
    else:
        blob = f"{type(exc).__name__}: {exc}".lower()
    if not blob.strip():
        return "unknown"
    # An explicit status beats text matching when the provider gave us one: the
    # message body of a 503 can easily contain the word "quota". Duck-typed
    # across the three shapes the STT tier raises (our own typed error, the
    # google-genai SDK's ``.code``, the httpx ``.response.status_code``) so this
    # module needs no HTTP client and no provider import.
    status = _status_of(exc)
    if status is not None:
        by_status = _reason_for_status(status)
        if by_status is not None:
            return by_status
    for reason, markers in _MARKERS:
        if any(marker in blob for marker in markers):
            return reason
    return "unknown"


def normalize_stt_failure(value: BaseException | str | None) -> str | None:
    """A reason code for ``value``, passing an already-valid code through.

    The backstop that belongs at the STORE, not at each caller.
    :func:`classify_stt_failure` is deliberately not idempotent — ``"bad_key"``
    contains none of the markers that identify a bad key, so classifying an
    already-classified value would answer ``"unknown"`` and quietly destroy the
    reason. Every path that persists or publishes a failure goes through here
    instead, so a caller that hands over a raw provider string cannot put one
    back in front of the user, and one that hands over a code keeps it.

    ``None`` (and a blank string) stays ``None``: "no failure" must remain
    distinguishable from "failed for an unknown reason".
    """
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            return None
        if value in STT_FAILURE_REASONS:
            return value
    return classify_stt_failure(value)


def stt_failure_message(reason: str | None) -> str:
    """The English sentence for ``reason``, for logs / CLI / API ``detail``.

    An unrecognised reason degrades to the ``unknown`` sentence rather than
    echoing the raw value, so a newer backend talking to an older consumer still
    produces a sentence instead of an identifier.
    """
    return _MESSAGES.get(str(reason or ""), _MESSAGES["unknown"])


def is_crossable_failure(reason: str | None) -> bool:
    """``True`` when another provider deserves a shot at the same audio."""
    return str(reason or "") in CROSSABLE_REASONS


def _status_of(exc: BaseException | str | None) -> int | None:
    """The HTTP status behind ``exc``, or ``None`` when there is not one."""
    if exc is None or isinstance(exc, str):
        return None
    for candidate in (
        getattr(exc, "status", None),
        getattr(exc, "code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        # ``True`` is an int in Python; a boolean flag is never a status.
        if isinstance(candidate, bool):
            continue
        if isinstance(candidate, int) and 100 <= candidate <= 599:
            return candidate
    return None


def _reason_for_status(status: int) -> str | None:
    """The reason a bare HTTP status implies, or ``None`` when it implies none."""
    if status == 429:
        return "rate_limited"
    if status == 402:
        return "no_credit"
    if status in (401, 403):
        return "bad_key"
    if status >= 500:
        return "unavailable"
    if 400 <= status < 500:
        return "rejected"
    return None


__all__ = [
    "CROSSABLE_REASONS",
    "STT_FAILURE_REASONS",
    "classify_stt_failure",
    "is_crossable_failure",
    "normalize_stt_failure",
    "stt_failure_message",
]
