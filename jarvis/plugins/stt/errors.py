"""One classifiable HTTP error shape for every cloud STT plugin.

WHY THIS EXISTS. The speech pipeline decides whether a failed transcription is
worth another attempt from the HTTP status behind it — 429 and 5xx are a blip
worth retrying, 401 (dead key) and 400 (bad audio) are not — and it decides how
long to wait from the server's ``Retry-After``. It could only ever read those
facts off an ``httpx.HTTPStatusError``, which exactly ONE of the four cloud STT
plugins raised. The other three flattened every status into a bare
``RuntimeError``, so the whole retry ladder was dead code for them: whoever's
key was not a Groq key lost the entire turn to the first rate limit, while
those plugins' own docstrings promised they "degrade honestly (AP-22)". That is
AP-23 in miniature — the maintainer's provider was the only one that worked.

``STTHTTPError`` carries the two machine-readable facts a caller needs (the
``status`` and the requested ``retry_after`` in seconds) and stays a
``RuntimeError``, so every existing ``except RuntimeError`` / "degrade to the
key-free local floor" path behaves exactly as it does today; only code that
WANTS the status has to know this type exists.

It also keeps the legacy ``exc.response.status_code`` / ``exc.response.headers``
shape alive (see :class:`_ErrorResponseView`). That is deliberate, not
nostalgia: a typed ``.status`` that only a future consumer reads would have left
the retry ladder just as dead as it is now, whereas mirroring the shape the
current consumer already duck-types makes all four providers classifiable the
moment they raise this.

Deliberately dependency-free — no ``httpx``, no ``jarvis.*``. The plugin modules
stay import-clean (the entry-point contract), response objects are duck-typed
rather than imported, and a caller holding nothing but a status code can still
raise a useful error.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

__all__ = [
    "STTHTTPError",
    "http_error_from_response",
    "parse_retry_after",
    "status_from_exception",
]


def parse_retry_after(value: Any, *, now: float | None = None) -> float | None:
    """Seconds to wait, read from a ``Retry-After`` header value.

    RFC 9110 allows TWO forms and real providers send both: a delta in seconds
    (``"30"``) and an HTTP-date (``"Wed, 21 Oct 2015 07:28:00 GMT"``). The
    OpenAI-compatible audio APIs send the delta form; a gateway or CDN sitting
    in front of one of them sends the date form. Understanding only one of them
    means silently falling back to blind exponential backoff for half the
    install base — the kind of "works for me" gap AP-23 is about — so both are
    handled here, once, instead of in four plugins.

    ``now`` (epoch seconds) exists so the date branch is testable without
    waiting for a clock. Returns ``None`` for an absent or unparseable value and
    NEVER raises: a malformed header must degrade to the caller's own backoff,
    not kill the turn. A date already in the past (or a skewed clock) clamps to
    ``0.0`` — "retry now", never a negative sleep.
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:  # pragma: no cover — older stdlib returned None instead
        return None
    if when.tzinfo is None:
        # An HTTP-date with no zone is UTC by definition (RFC 9110 §5.6.7).
        when = when.replace(tzinfo=UTC)
    reference = datetime.now(UTC) if now is None else datetime.fromtimestamp(now, tz=UTC)
    return max(0.0, (when - reference).total_seconds())


def status_from_exception(exc: BaseException | None) -> int | None:
    """The HTTP status behind ``exc``, or ``None`` when it is not an HTTP error.

    Duck-typed across the three shapes the STT tier actually sees, in order of
    precision: our own :class:`STTHTTPError` (``.status``), the google-genai
    SDK's ``APIError`` (``.code`` — an int status, and the SDK is NOT importable
    on a base install, so it can only ever be duck-typed), and the
    ``httpx.HTTPStatusError`` family (``.response.status_code``). Anything else
    — a transport error, a timeout, a bug — is not an HTTP status and returns
    ``None`` so the caller treats it as such rather than inventing one.
    """
    if exc is None:
        return None
    for candidate in (
        getattr(exc, "status", None),
        getattr(exc, "code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(candidate, bool):
            # ``True`` is an int in Python; a boolean flag is never a status.
            continue
        if isinstance(candidate, int) and 100 <= candidate <= 599:
            return candidate
    return None


class _ErrorHeaders(Mapping):
    """Case-insensitive, read-only COPY of a failed response's headers.

    A copy, not the live response: an exception can outlive the call that
    produced it (it is stored, re-raised, logged), and pinning the provider's
    response object inside it would keep its buffers — and on some clients its
    connection — alive far past the request. Case-insensitivity is not a nicety
    either: HTTP header names are case-insensitive and providers genuinely vary
    between ``Retry-After`` and ``retry-after``, so a case-sensitive lookup
    silently loses the delay for half of them.
    """

    __slots__ = ("_items",)

    def __init__(self, source: Any = None) -> None:
        items: dict[str, str] = {}
        try:
            pairs = source.items() if hasattr(source, "items") else (source or ())
            for key, value in pairs:
                items[str(key).lower()] = str(value)
        except Exception:  # noqa: BLE001 — unreadable headers must not mask the error
            items = {}
        self._items = items

    def __getitem__(self, key: str) -> str:
        return self._items[str(key).lower()]

    def __iter__(self):  # noqa: ANN204 — Mapping protocol
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:  # pragma: no cover — logging nicety
        return f"_ErrorHeaders({self._items!r})"


class _ErrorResponseView:
    """The two response facts a caller needs, without the response object.

    Exposed on the exception as ``.response`` ON PURPOSE. The pipeline's current
    classifier reads ``exc.response.status_code`` and its backoff reads
    ``exc.response.headers.get("retry-after")`` — the ``httpx.HTTPStatusError``
    shape Groq alone used to raise. Mirroring exactly those two attributes is
    what turns the other three plugins from unclassifiable into classifiable
    without a coordinated change on the consumer side, and it keeps Groq's
    behaviour bit-for-bit identical when it starts raising this type instead.
    """

    __slots__ = ("headers", "status_code")

    def __init__(self, status_code: int, headers: Any = None) -> None:
        self.status_code = status_code
        self.headers = _ErrorHeaders(headers)


class STTHTTPError(RuntimeError):
    """A cloud STT provider answered with an HTTP error status.

    ``status`` is that status. ``retry_after`` is the delay the server asked
    for, in SECONDS, or ``None`` when it asked for none (or asked
    unintelligibly) — the caller then uses its own backoff. When it is not
    passed explicitly it is read from the response's ``Retry-After`` header, so
    a plugin only has to hand over the headers it already has.

    The message stays the plugin's own English sentence: this type ADDS
    machine-readable facts, it never replaces the human-readable reason a user
    or a log reader sees. Subclassing ``RuntimeError`` keeps every existing
    handler (the STT factory's local-floor fallback, the pipeline's honest
    apology) working unchanged.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int,
        retry_after: float | None = None,
        headers: Any = None,
    ) -> None:
        super().__init__(message)
        self.status = int(status)
        self.response = _ErrorResponseView(self.status, headers)
        if retry_after is None:
            retry_after = parse_retry_after(self.response.headers.get("retry-after"))
        self.retry_after = None if retry_after is None else max(0.0, float(retry_after))


def _error_detail(response: Any) -> str:
    """The provider's own explanation for a failure, best-effort.

    These APIs answer with ``{"error": {"message": ...}}`` (or occasionally a
    plain string), but an overloaded gateway answers with HTML — so a JSON parse
    failure falls back to a short slice of the raw body instead of dropping the
    only clue the user gets. Never raises.
    """
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 — body may not be JSON at all
        try:
            return (getattr(response, "text", "") or "").strip()[:200]
        except Exception:  # noqa: BLE001 — an unreadable body is simply no detail
            return ""
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            return str(err.get("message", "")).strip()
        if isinstance(err, str):
            return err.strip()
    return ""


def http_error_from_response(response: Any, *, vendor: str) -> STTHTTPError:
    """Build the typed error for an HTTP error ``response`` from ``vendor``.

    One helper for every OpenAI-shaped STT plugin, so four plugins cannot drift
    into four different sentences for the same 429 — and so a fifth plugin gets
    the classification for free instead of re-deriving it. The wording is
    exactly what those plugins already shipped (``"<vendor> STT failed:
    <reason> (<detail>)"``); only the TYPE changed.

    ``response`` is duck-typed (``status_code`` / ``headers`` / ``json()`` /
    ``text``), so this module needs no HTTP client of its own and a test can
    hand it a plain stub.
    """
    status = int(getattr(response, "status_code", 0) or 0)
    reason = {
        401: f"invalid or missing {vendor} API key",
        402: f"{vendor} account out of credit",
        429: f"{vendor} rate limit / quota exceeded",
    }.get(status, f"{vendor} STT HTTP {status}")
    message = f"{vendor} STT failed: {reason}"
    detail = _error_detail(response)
    if detail:
        message = f"{message} ({detail})"
    return STTHTTPError(
        message, status=status, headers=getattr(response, "headers", None)
    )
