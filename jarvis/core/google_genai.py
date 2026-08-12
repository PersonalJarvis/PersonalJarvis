"""Google GenAI client construction with AI-Studio-vs-Vertex key routing.

Google ships two API-key families for the same Gemini models:

* **Google AI Studio** keys — classic ``AIza...`` and newer ``AQ....`` —
  served by ``generativelanguage.googleapis.com``.
* **Vertex AI express mode** keys — also ``AQ....`` — served by
  ``aiplatform.googleapis.com`` and only accepted when the google-genai
  client is built with ``vertexai=True``.

The prefix alone cannot distinguish the two ``AQ.`` families, and a key sent
to the wrong endpoint fails with an auth error ("API key not valid"). This
module is the single source of truth for picking the route, shared by every
surface that builds a Gemini client (brain, tool model, realtime, STT, TTS,
dictation polish, computer use, ack brain):

* ``AIza...`` keys route straight to AI Studio — zero added latency, the
  exact behaviour every install had before this module existed.
* Ambiguous ``AQ....`` keys are probed ONCE per process: one cheap
  ``models.list`` GET against AI Studio. Accepted → AI Studio; rejected with
  an auth error → the key must be a Vertex express key. The verdict is
  cached by key fingerprint, so realtime session opens and per-turn calls
  never pay the probe again.
* ``[google].vertex_mode`` in jarvis.toml overrides the probe: ``always``
  forces Vertex for ambiguous keys, ``never`` restores the pre-Vertex
  behaviour. ``auto`` (default) probes. Read only here (AP-31).

An explicitly configured ``base_url`` (team proxy, W2) must bypass routing
entirely — the proxy speaks the AI Studio wire format — which callers get by
passing ``route="aistudio"`` to :func:`build_genai_client`.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from typing import Any, Literal

log = logging.getLogger("jarvis.google_genai")

KeyRoute = Literal["aistudio", "vertex"]

#: AI Studio model-list endpoint used as the routing probe. A GET here is the
#: cheapest documented call that authenticates the key without generating
#: tokens; the key travels in the ``x-goog-api-key`` header, never the URL,
#: so it cannot leak into access logs.
_AISTUDIO_PROBE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

#: HTTP statuses that mean "this key is not an AI Studio key" (as opposed to
#: "the service hiccuped"). 400 is what generativelanguage actually returns
#: for a foreign key (API_KEY_INVALID); 401/403 are kept for robustness.
_AUTH_REJECT_STATUSES = frozenset({400, 401, 403})

#: Probe budget. The probe runs once per process per key, off the boot
#: critical path (clients are built lazily on first use), so a short budget
#: only has to beat transient network stalls.
_PROBE_TIMEOUT_S = 5.0

_ROUTE_CACHE: dict[str, KeyRoute] = {}
_CACHE_LOCK = threading.Lock()


def _fingerprint(api_key: str) -> str:
    """Non-reversible cache/log identity for a key (first 12 hex of SHA-256)."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def classify_google_key(api_key: str) -> KeyRoute | None:
    """Classify a key by shape alone; ``None`` means "ambiguous, probe it".

    ``AIza`` is the classic AI Studio / GCP API-key prefix and is never issued
    for Vertex express, so it short-circuits with no probe. ``AQ.`` is issued
    by BOTH AI Studio and Vertex express — undecidable by shape. Anything else
    (wrong-provider paste, garbage) routes to AI Studio so it fails with the
    same honest upstream error it always did.
    """
    key = api_key.strip()
    if key.startswith("AQ."):
        return None
    return "aistudio"


def _configured_mode() -> str:
    """``[google].vertex_mode`` with a hard fallback to ``auto``.

    Lazy import + broad fallback: this module must stay importable (and
    behave sanely) even while the config system is mid-boot or the section
    is absent in an older jarvis.toml.
    """
    try:
        from jarvis.core.config import load_config

        mode = str(load_config().google.vertex_mode or "auto").strip().lower()
    except Exception:  # noqa: BLE001 — config trouble must never break clients
        return "auto"
    return mode if mode in ("auto", "always", "never") else "auto"


def _interpret_probe_status(status_code: int) -> KeyRoute | None:
    """Map the AI Studio probe's HTTP status to a route (``None`` = unknown).

    2xx proves the key is an AI Studio key. An auth-reject status proves it is
    NOT one — and the only other family sharing the ``AQ.`` shape is Vertex
    express, so that is the verdict; if the key is invalid everywhere, the
    first real call surfaces the same upstream error either way. Anything else
    (5xx, 429) proves nothing and must not be cached.
    """
    if 200 <= status_code < 300:
        return "aistudio"
    if status_code in _AUTH_REJECT_STATUSES:
        return "vertex"
    return None


def _cached_route(fp: str) -> KeyRoute | None:
    with _CACHE_LOCK:
        return _ROUTE_CACHE.get(fp)


def _remember_route(fp: str, route: KeyRoute, *, source: str) -> KeyRoute:
    with _CACHE_LOCK:
        _ROUTE_CACHE[fp] = route
    log.info("Google key %s routes via %s (%s).", fp, route, source)
    return route


def _resolve_preamble(api_key: str) -> tuple[str, KeyRoute | None]:
    """Shared sync/async front half: cache, config override, shape.

    Returns ``(fingerprint, route)`` where a non-``None`` route is final and
    the probe is skipped.
    """
    fp = _fingerprint(api_key)
    cached = _cached_route(fp)
    if cached is not None:
        return fp, cached
    shape = classify_google_key(api_key)
    if shape is not None:
        # Unambiguous shape — cache without logging chatter: this is the
        # unchanged historical path taken by every AIza key on every boot.
        with _CACHE_LOCK:
            _ROUTE_CACHE[fp] = shape
        return fp, shape
    mode = _configured_mode()
    if mode == "always":
        return fp, _remember_route(fp, "vertex", source="vertex_mode=always")
    if mode == "never":
        return fp, _remember_route(fp, "aistudio", source="vertex_mode=never")
    return fp, None


def resolve_google_key_route(api_key: str, *, transport: Any | None = None) -> KeyRoute:
    """Decide the route for ``api_key`` (sync). Probes at most once per key.

    ``transport`` is a test seam: an ``httpx.MockTransport`` makes the probe
    fully offline. On network trouble the verdict defaults to ``aistudio``
    WITHOUT caching, so a flaky network cannot pin a wrong route for the
    process lifetime.
    """
    fp, decided = _resolve_preamble(api_key)
    if decided is not None:
        return decided
    try:
        import httpx

        with httpx.Client(timeout=_PROBE_TIMEOUT_S, transport=transport) as client:
            response = client.get(
                _AISTUDIO_PROBE_URL,
                params={"pageSize": 1},
                headers={"x-goog-api-key": api_key},
            )
        route = _interpret_probe_status(response.status_code)
    except Exception as exc:  # noqa: BLE001 — probe failure must not break calls
        log.warning(
            "Google key %s: routing probe failed (%s: %s) — defaulting to "
            "AI Studio without caching.",
            fp,
            type(exc).__name__,
            exc,
        )
        return "aistudio"
    if route is None:
        log.warning(
            "Google key %s: routing probe returned HTTP %s — defaulting to "
            "AI Studio without caching.",
            fp,
            response.status_code,
        )
        return "aistudio"
    return _remember_route(fp, route, source=f"probe HTTP {response.status_code}")


async def resolve_google_key_route_async(api_key: str, *, transport: Any | None = None) -> KeyRoute:
    """Async twin of :func:`resolve_google_key_route`; shares its cache."""
    fp, decided = _resolve_preamble(api_key)
    if decided is not None:
        return decided
    try:
        import httpx

        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S, transport=transport) as client:
            response = await client.get(
                _AISTUDIO_PROBE_URL,
                params={"pageSize": 1},
                headers={"x-goog-api-key": api_key},
            )
        route = _interpret_probe_status(response.status_code)
    except Exception as exc:  # noqa: BLE001 — probe failure must not break calls
        log.warning(
            "Google key %s: routing probe failed (%s: %s) — defaulting to "
            "AI Studio without caching.",
            fp,
            type(exc).__name__,
            exc,
        )
        return "aistudio"
    if route is None:
        log.warning(
            "Google key %s: routing probe returned HTTP %s — defaulting to "
            "AI Studio without caching.",
            fp,
            response.status_code,
        )
        return "aistudio"
    return _remember_route(fp, route, source=f"probe HTTP {response.status_code}")


def _client_kwargs(api_key: str, route: KeyRoute, http_options: Any | None) -> dict[str, Any]:
    """Pure kwargs assembly for ``genai.Client`` — unit-testable sans SDK.

    Express mode is exactly ``vertexai=True`` plus the key; no project or
    location — the express endpoint infers the trial/billing project from the
    key itself. The explicit ``api_key`` argument outranks any ambient
    ``GOOGLE_API_KEY``/``GEMINI_API_KEY`` env, so no env-stripping is needed
    here (unlike the TTS service-account path, which authenticates via env).
    """
    kwargs: dict[str, Any] = {"api_key": api_key}
    if route == "vertex":
        kwargs["vertexai"] = True
    if http_options is not None:
        kwargs["http_options"] = http_options
    return kwargs


def build_genai_client(
    api_key: str,
    *,
    http_options: Any | None = None,
    route: KeyRoute | None = None,
) -> Any:
    """Build a ``google.genai.Client`` on the right endpoint for this key.

    Drop-in replacement for the bare ``genai.Client(api_key=...)`` calls that
    predate Vertex support. ``route`` pins the endpoint when the caller
    already knows it (team proxy → ``"aistudio"``); ``None`` resolves it via
    :func:`resolve_google_key_route`. The SDK import stays inside the
    function so headless installs without google-genai import this module
    cleanly (AP-26).
    """
    from google import genai

    resolved = route or resolve_google_key_route(api_key)
    return genai.Client(**_client_kwargs(api_key, resolved, http_options))


async def build_genai_client_async(
    api_key: str,
    *,
    http_options: Any | None = None,
    route: KeyRoute | None = None,
) -> Any:
    """Async twin of :func:`build_genai_client` for event-loop callers.

    The realtime surface opens sessions inside the loop; resolving the route
    with the async probe keeps a first-ever ``AQ.`` key from blocking the
    loop for the probe round-trip.
    """
    from google import genai

    resolved = route or await resolve_google_key_route_async(api_key)
    return genai.Client(**_client_kwargs(api_key, resolved, http_options))


def reset_route_cache() -> None:
    """Forget every probed verdict (tests; key rotation edge cases)."""
    with _CACHE_LOCK:
        _ROUTE_CACHE.clear()
