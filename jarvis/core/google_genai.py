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
* Ambiguous ``AQ....`` keys are probed ONCE per process, in two steps: a
  cheap ``models.list`` GET against AI Studio, and — only if AI Studio
  rejects the key — a free Vertex ``countTokens`` counter-probe. Accepted by
  AI Studio → AI Studio; accepted by Vertex → express key; rejected by BOTH
  → the key is simply invalid, so the historical AI Studio path surfaces
  Google's own error and nothing is cached. A confirmed verdict is cached by
  key fingerprint, so realtime session opens and per-turn calls never pay
  the probe again.
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

#: AI Studio model-list endpoint used as the first routing probe. A GET here
#: is the cheapest documented call that authenticates the key without
#: generating tokens; the key travels in the ``x-goog-api-key`` header, never
#: the URL, so it cannot leak into access logs.
_AISTUDIO_PROBE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

#: Vertex express counter-probe. An AI Studio rejection alone does NOT prove
#: the key is a Vertex express key — an invalid/expired ``AQ.`` key is
#: rejected by BOTH endpoints (measured 2026-08-12: 401
#: ACCESS_TOKEN_TYPE_UNSUPPORTED on either host). Only a successful
#: ``countTokens`` — one of exactly three methods officially in scope for
#: express mode, and free (it counts, it does not generate) — confirms the
#: Vertex route. Express uses the GLOBAL aiplatform host: no project, no
#: location, key in the same header.
_VERTEX_PROBE_URL_TMPL = (
    "https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:countTokens"
)

#: Models tried for the counter-probe, in order. The probe needs SOME model id
#: in the URL (there is no model-free express call in the documented set); a
#: 404 means "this model id is gone", not an auth verdict, so the next
#: candidate is tried. Auth semantics do not depend on which model answers
#: (never a behavioural pin, AP-21).
_VERTEX_PROBE_MODELS = ("gemini-2.5-flash", "gemini-2.5-flash-lite")

#: Minimal countTokens body — one character to count, zero generation.
_VERTEX_PROBE_BODY = {"contents": [{"role": "user", "parts": [{"text": "x"}]}]}

#: HTTP statuses that mean "this endpoint rejects this key" (as opposed to
#: "the service hiccuped"). 400 is what generativelanguage actually returns
#: for a foreign/invalid key (API_KEY_INVALID); 401 is what both hosts return
#: for a bad authorization key; 403 is kept for robustness.
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
    except Exception as exc:  # noqa: BLE001 — config trouble must never break clients
        log.debug("vertex_mode unreadable (%s: %s) — using 'auto'.", type(exc).__name__, exc)
        return "auto"
    return mode if mode in ("auto", "always", "never") else "auto"


def _probe_verdict(
    aistudio_status: int, vertex_statuses: tuple[int, ...]
) -> tuple[KeyRoute | None, str]:
    """Combine both probe answers into ``(route, detail)``.

    * AI Studio 2xx → the key IS an AI Studio key (an authorization key valid
      for both services deterministically picks AI Studio; ``vertex_mode =
      "always"`` overrides).
    * AI Studio reject + Vertex ``countTokens`` 2xx → proven express key.
    * Rejected by BOTH → the key is simply invalid; route ``None`` so the
      caller defaults to the historical AI Studio path WITHOUT caching and
      the first real call surfaces Google's own error message.
    * Anything else (5xx, 429, network) proves nothing → ``None``.
    """
    if 200 <= aistudio_status < 300:
        return "aistudio", f"AI Studio probe HTTP {aistudio_status}"
    if aistudio_status not in _AUTH_REJECT_STATUSES:
        return None, f"AI Studio probe HTTP {aistudio_status}"
    for status in vertex_statuses:
        if 200 <= status < 300:
            return "vertex", (f"AI Studio {aistudio_status}, Vertex countTokens {status}")
    return None, (
        f"rejected by both endpoints (AI Studio {aistudio_status}, "
        f"Vertex {list(vertex_statuses) or 'unprobed'}) — key likely invalid"
    )


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


def _run_probe_sync(api_key: str, transport: Any | None) -> tuple[KeyRoute | None, str]:
    """Two-step probe (sync): AI Studio GET, then the Vertex counter-probe.

    The counter-probe runs ONLY after an AI Studio auth-reject. A Vertex 404
    means "this model id is gone", not an auth verdict, so the next candidate
    model is tried; any other answer is decisive and the loop stops.
    """
    import httpx

    headers = {"x-goog-api-key": api_key}
    with httpx.Client(timeout=_PROBE_TIMEOUT_S, transport=transport) as client:
        aistudio = client.get(_AISTUDIO_PROBE_URL, params={"pageSize": 1}, headers=headers)
        if aistudio.status_code not in _AUTH_REJECT_STATUSES:
            return _probe_verdict(aistudio.status_code, ())
        statuses: list[int] = []
        for model in _VERTEX_PROBE_MODELS:
            vertex = client.post(
                _VERTEX_PROBE_URL_TMPL.format(model=model),
                json=_VERTEX_PROBE_BODY,
                headers=headers,
            )
            statuses.append(vertex.status_code)
            if vertex.status_code != 404:
                break
        return _probe_verdict(aistudio.status_code, tuple(statuses))


async def _run_probe_async(api_key: str, transport: Any | None) -> tuple[KeyRoute | None, str]:
    """Async twin of :func:`_run_probe_sync` — same two-step semantics."""
    import httpx

    headers = {"x-goog-api-key": api_key}
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S, transport=transport) as client:
        aistudio = await client.get(_AISTUDIO_PROBE_URL, params={"pageSize": 1}, headers=headers)
        if aistudio.status_code not in _AUTH_REJECT_STATUSES:
            return _probe_verdict(aistudio.status_code, ())
        statuses: list[int] = []
        for model in _VERTEX_PROBE_MODELS:
            vertex = await client.post(
                _VERTEX_PROBE_URL_TMPL.format(model=model),
                json=_VERTEX_PROBE_BODY,
                headers=headers,
            )
            statuses.append(vertex.status_code)
            if vertex.status_code != 404:
                break
        return _probe_verdict(aistudio.status_code, tuple(statuses))


def resolve_google_key_route(api_key: str, *, transport: Any | None = None) -> KeyRoute:
    """Decide the route for ``api_key`` (sync). Probes at most once per key.

    ``transport`` is a test seam: an ``httpx.MockTransport`` makes the probe
    fully offline. On network trouble — or when both endpoints reject the key
    (an invalid key, not a routing signal) — the verdict defaults to
    ``aistudio`` WITHOUT caching, so neither a flaky network nor a typo can
    pin a wrong route for the process lifetime.
    """
    fp, decided = _resolve_preamble(api_key)
    if decided is not None:
        return decided
    try:
        route, detail = _run_probe_sync(api_key, transport)
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
            "Google key %s: %s — defaulting to AI Studio without caching.",
            fp,
            detail,
        )
        return "aistudio"
    return _remember_route(fp, route, source=detail)


async def resolve_google_key_route_async(api_key: str, *, transport: Any | None = None) -> KeyRoute:
    """Async twin of :func:`resolve_google_key_route`; shares its cache."""
    fp, decided = _resolve_preamble(api_key)
    if decided is not None:
        return decided
    try:
        route, detail = await _run_probe_async(api_key, transport)
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
            "Google key %s: %s — defaulting to AI Studio without caching.",
            fp,
            detail,
        )
        return "aistudio"
    return _remember_route(fp, route, source=detail)


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
