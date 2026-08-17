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

Everything above is about a key stored in the GEMINI slots, where the endpoint
has to be inferred. The dedicated ``vertex`` provider family is the other case:
there the user picked Vertex explicitly, so :func:`build_vertex_client` pins the
route with no probe at all and additionally serves the FULL Google Cloud path —
``[google].vertex_project``/``vertex_location`` with Application Default
Credentials instead of a key.

Which of the two a caller lands on is not a preference. Measured 2026-08-17
against a live Cloud project: Vertex accepts an API key ONLY in express mode. A
standard Cloud API key — including one created with
``--api-target=service=aiplatform.googleapis.com`` — is refused on every Vertex
surface (countTokens, generateContent, and the Live socket, which closes with
1008) with "API keys are not supported by this API. Expected OAuth2 access token
or other authentication credentials that assert a principal", while the very
same key answers 200 on AI Studio. So for an ordinary Cloud project the project
path is the only route in, and the express key is the exception.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
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


#: Region the Live socket falls back to when ``vertex_location`` is ``global``.
#: Not a preference: ``global`` opens no Live session, so SOME region has to be
#: named. us-central1 carries the widest published model set, which makes it the
#: least surprising default — and the warning above tells the user it happened.
_REALTIME_FALLBACK_LOCATION = "us-central1"


@dataclass(frozen=True, slots=True)
class VertexProject:
    """The full-Vertex (Google Cloud project) half of ``[google]``.

    ``project`` empty means express mode — the API key carries the billing
    project itself and no project/location may be sent. Anything else selects
    the enterprise path: Application Default Credentials against
    ``project``/``location``, optionally from ``service_account_path``.
    """

    project: str | None
    location: str
    service_account_path: str | None
    #: Where the DUPLEX (Live) socket is opened. Separate from ``location``
    #: because the two are genuinely different endpoints — see
    #: :meth:`realtime_location`.
    realtime_location: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.project)

    def for_realtime(self) -> VertexProject:
        """The same project, pointed at an endpoint that can serve Live.

        Measured 2026-08-17: the ``global`` endpoint serves the current Gemini
        generation for ordinary requests but opens NO Live session at all — not
        even the ``gemini-live-*`` id its own catalogue lists there; every
        attempt closes with 1008 "Publisher model not found". Regional endpoints
        do open it. So one location cannot serve both tiers, and a config with a
        single knob would force the user to choose between a current brain and a
        working voice.

        Precedence: an explicit ``[google].vertex_realtime_location`` wins; a
        regional ``vertex_location`` is used as-is; only a ``global``
        ``vertex_location`` falls back to :data:`_REALTIME_FALLBACK_LOCATION`,
        and says so in the log — a silent region change is a data-residency
        decision nobody asked for.
        """
        if self.realtime_location:
            return replace(self, location=self.realtime_location)
        if self.location.lower() != "global":
            return self
        log.warning(
            "Vertex realtime: the global endpoint serves no Live session, so "
            "the socket falls back to %s. Set [google].vertex_realtime_location "
            "to choose the region yourself — it decides where the audio is "
            "processed.",
            _REALTIME_FALLBACK_LOCATION,
        )
        return replace(self, location=_REALTIME_FALLBACK_LOCATION)


def vertex_project_settings() -> VertexProject:
    """Read the Vertex project path from ``[google]``; never raises.

    Same lazy-import + broad-fallback discipline as :func:`_configured_mode`:
    an unreadable or older config yields express mode, which is exactly the
    behaviour every install had before this path existed.
    """
    try:
        from jarvis.core.config import load_config

        google = load_config().google
        project = str(getattr(google, "vertex_project", "") or "").strip() or None
        location = str(getattr(google, "vertex_location", "") or "").strip() or "global"
        sa_path = str(getattr(google, "service_account_path", "") or "").strip() or None
        rt_location = str(getattr(google, "vertex_realtime_location", "") or "").strip() or None
    except Exception as exc:  # noqa: BLE001 — config trouble must never break clients
        log.debug(
            "Vertex project settings unreadable (%s: %s) — using express mode.",
            type(exc).__name__,
            exc,
        )
        return VertexProject(project=None, location="global", service_account_path=None)
    return VertexProject(
        project=project,
        location=location,
        service_account_path=sa_path,
        realtime_location=rt_location,
    )


def _export_service_account(path: str | None) -> None:
    """Point the Cloud SDK auth chain at *path* for this process.

    Only ever ADDS the pointer, and only when the file is really there: a
    ``GOOGLE_APPLICATION_CREDENTIALS`` aimed at a missing file makes google-auth
    fail outright, which is strictly worse than letting the ambient ADC chain
    (gcloud login, workload identity, an env var the user already set) answer.
    An env var the user set themselves is left untouched.
    """
    if not path:
        return
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        log.warning(
            "Vertex service account %s does not exist — falling back to the "
            "ambient Application Default Credentials.",
            resolved,
        )
        return
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(resolved)
    log.info("Vertex AI: authenticating via the service account at %s.", resolved)


# ── Application Default Credentials, loaded ONCE per process ─────────────────
#
# On the Cloud project path every ``genai.Client`` that is handed no
# ``credentials`` resolves them itself on its first call, via
# ``google.auth.default()``. Measured 2026-08-17 on the maintainer box (Windows,
# gcloud login as ADC): that call costs 5.3-8.5 s — google-auth reads the ADC
# file, finds no project id in it, and spawns ``gcloud config config-helper``
# to ask for one (a full CLI start), regardless of the project we already pass.
# Jarvis builds a fresh client per Live session and per brain instance, so the
# price was paid on every voice handshake (5.7-12.2 s to "session ready", one
# 6 s budget timeout) and on the first turn of every brain. With ONE shared
# credentials object the very same handshake measured 1.1-1.3 s. The object
# is safe to share: it is what Google's own client libraries pass around, and
# the SDK refreshes the token on it only when expired.

#: OAuth scope Vertex AI requests are signed under (what the SDK asks for).
_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

_ADC_LOCK = threading.Lock()
_ADC_CACHE: dict[str, Any] = {}


def _adc_cache_key() -> str:
    """Which credential file google-auth would read right now.

    A service account exported by :func:`_export_service_account` after a
    gcloud login was cached must not be answered from that cache.
    """
    return os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "") or ""


def _load_application_default_credentials() -> Any:
    """The one call into google-auth (test seam). Blocking; raises on failure."""
    import google.auth  # lazy (AP-26)

    credentials, _project = google.auth.default(scopes=[_CLOUD_PLATFORM_SCOPE])
    return credentials


def cached_vertex_credentials() -> Any | None:
    """The shared credentials if a load already happened; never blocks.

    Deliberately lock-free (a dict read is atomic under the GIL): the event
    loop asks this while a warm-up thread may be holding ``_ADC_LOCK`` for the
    whole 5-8 s load, and it must get "not yet" instantly rather than wait.
    """
    return _ADC_CACHE.get(_adc_cache_key())


def vertex_credentials() -> Any | None:
    """Application Default Credentials for the Vertex project path, loaded once.

    BLOCKING on the first call (see the measurement above) — call it from a
    worker thread, never on the event loop. Later calls answer from the cache.
    ``None`` means google-auth could not resolve a credential; nothing is
    cached for that case, so a login that appears later is picked up, and the
    caller leaves the SDK's own lazy resolution in place, which surfaces
    Google's honest error on the first real call.
    """
    key = _adc_cache_key()
    cached = _ADC_CACHE.get(key)
    if cached is not None:
        return cached
    with _ADC_LOCK:
        cached = _ADC_CACHE.get(key)  # a concurrent loader may have won
        if cached is not None:
            return cached
        started = time.perf_counter()
        try:
            credentials = _load_application_default_credentials()
        except Exception as exc:  # noqa: BLE001 — auth trouble surfaces on the first call
            log.info(
                "Vertex AI: Application Default Credentials not resolvable (%s: %s) — "
                "the SDK resolves auth on the first call instead.",
                type(exc).__name__,
                exc,
            )
            return None
        if credentials is None:
            return None
        _ADC_CACHE[key] = credentials
        log.info(
            "Vertex AI: Application Default Credentials loaded once for this "
            "process in %.0f ms; every client shares them from here on.",
            (time.perf_counter() - started) * 1000.0,
        )
        return credentials


def _adc_for_sync_build() -> Any | None:
    """Credentials for a synchronous client build without ever blocking a loop.

    A cold cache is filled only where blocking is legal — a thread with no
    running event loop (the brain builds its client under ``to_thread``). On
    the loop the build proceeds without credentials and the SDK's threaded lazy
    load applies, exactly as before.
    """
    cached = cached_vertex_credentials()
    if cached is not None:
        return cached
    try:
        asyncio.get_running_loop()
    except RuntimeError:  # no loop in this thread — blocking here is legal
        return vertex_credentials()
    return None


def warm_vertex_credentials() -> bool:
    """Load the shared credentials AND mint the first token. Blocking.

    Meant for a boot warm-up off the critical path (a worker thread). After a
    successful warm the first Live handshake and the first brain call pay for
    neither the credential resolution nor the OAuth exchange. Returns whether
    a token is ready; a ``False`` costs nothing but the latency it was meant
    to save (the first call resolves auth itself, as it always did).
    """
    warm_shared_transport()  # the trust store is per process too — see below
    settings = _project_for("vertex")
    if settings is None or not settings.configured:
        return False
    credentials = vertex_credentials()
    if credentials is None:
        return False
    if bool(getattr(credentials, "valid", False)):
        return True
    try:
        from google.auth.transport.requests import Request  # lazy (AP-26)

        credentials.refresh(Request())
    except Exception as exc:  # noqa: BLE001 — the first call retries the exchange
        log.info(
            "Vertex AI: token pre-mint failed (%s: %s) — the first call refreshes.",
            type(exc).__name__,
            exc,
        )
        return False
    return bool(getattr(credentials, "valid", False))


def reset_vertex_credentials_cache() -> None:
    """Forget the shared credentials (tests; login changes)."""
    with _ADC_LOCK:
        _ADC_CACHE.clear()


# ── one TLS trust store per process ──────────────────────────────────────────
#
# ``genai.Client.__init__`` builds THREE ``ssl.SSLContext`` objects (httpx,
# aiohttp, websocket) unless the caller hands it one, and each parses the whole
# certifi bundle. Measured 2026-08-17 on the maintainer box: 445 ms apiece,
# 1.34 s per client — on the event loop for the Live path, on top of every
# session open and every brain instance. The SDK takes a caller-supplied
# context from ``client_args["verify"]`` / ``async_client_args["verify"|"ssl"]``
# and then skips its own. The context below is built with the SDK's exact
# parameters (``SSL_CERT_FILE``/certifi, ``SSL_CERT_DIR``), so trust behaviour
# is identical — it is simply built once and shared, which is what an
# ``SSLContext`` is for.

_TLS_LOCK = threading.Lock()
_TLS_CONTEXT: Any = None


def _shared_tls_context() -> Any:
    """The process-wide TLS context, built on first use. Raises if it cannot be.

    The fast path reads without the lock (an attribute read is atomic), so a
    caller on the event loop never waits behind a thread that is mid-build.
    """
    global _TLS_CONTEXT
    if _TLS_CONTEXT is not None:
        return _TLS_CONTEXT
    with _TLS_LOCK:
        if _TLS_CONTEXT is None:
            import ssl  # lazy (AP-26)

            import certifi  # lazy — ships with google-genai/httpx

            started = time.perf_counter()
            _TLS_CONTEXT = ssl.create_default_context(
                cafile=os.environ.get("SSL_CERT_FILE", certifi.where()),
                capath=os.environ.get("SSL_CERT_DIR"),
            )
            log.debug(
                "Google GenAI: shared TLS context built in %.0f ms.",
                (time.perf_counter() - started) * 1000.0,
            )
        return _TLS_CONTEXT


def _with_shared_tls(http_options: Any) -> Any:
    """Hand the client the shared TLS context unless the caller set its own.

    Accepts every shape ``genai.Client`` accepts — ``None``, a plain dict, or a
    typed ``HttpOptions`` — and returns the same shape with the transport args
    filled in. Anything the caller already put there wins. If the context
    cannot be built the options pass through untouched and the SDK builds its
    own, exactly as before.
    """
    try:
        ctx = _shared_tls_context()
    except Exception as exc:  # noqa: BLE001 — the SDK's own default remains
        log.debug("Shared TLS context unavailable (%s: %s).", type(exc).__name__, exc)
        return http_options

    def _filled(client_args: Any, async_args: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        sync_filled = dict(client_args or {})
        async_filled = dict(async_args or {})
        sync_filled.setdefault("verify", ctx)
        async_filled.setdefault("verify", ctx)
        async_filled.setdefault("ssl", ctx)
        return sync_filled, async_filled

    if http_options is None:
        sync_filled, async_filled = _filled(None, None)
        return {"client_args": sync_filled, "async_client_args": async_filled}
    if isinstance(http_options, dict):
        merged = dict(http_options)
        merged["client_args"], merged["async_client_args"] = _filled(
            merged.get("client_args"), merged.get("async_client_args")
        )
        return merged
    # A typed HttpOptions (pydantic model): a copy with the args filled in.
    try:
        sync_filled, async_filled = _filled(
            getattr(http_options, "client_args", None),
            getattr(http_options, "async_client_args", None),
        )
        return http_options.model_copy(
            update={"client_args": sync_filled, "async_client_args": async_filled}
        )
    except Exception as exc:  # noqa: BLE001 — an unknown shape passes through
        log.debug("http_options of type %s left untouched: %s", type(http_options).__name__, exc)
        return http_options


def reset_shared_tls_context() -> None:
    """Forget the shared TLS context (tests)."""
    global _TLS_CONTEXT
    with _TLS_LOCK:
        _TLS_CONTEXT = None


def warm_shared_transport() -> bool:
    """Build the shared TLS context ahead of the first client. Blocking.

    Provider-agnostic: the AI Studio route pays the same three-context build
    per client as Vertex does, so a boot warm-up on either route calls this
    from a worker thread. Returns whether the context is ready.
    """
    try:
        _shared_tls_context()
    except Exception as exc:  # noqa: BLE001 — the SDK's own default remains
        log.debug("Shared TLS context warm failed (%s: %s).", type(exc).__name__, exc)
        return False
    return True


async def _ensure_shared_tls_off_loop() -> None:
    """Fill a cold TLS context in a worker thread (async builders only).

    The context build parses the whole certifi bundle (~0.4 s measured); the
    async twins run on the event loop, so a cold first build must not happen
    inline. Failures are left to :func:`_with_shared_tls`, which passes the
    caller's options through untouched.
    """
    if _TLS_CONTEXT is None:
        await asyncio.to_thread(warm_shared_transport)


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


def _client_kwargs(
    api_key: str,
    route: KeyRoute,
    http_options: Any | None,
    project: VertexProject | None = None,
) -> dict[str, Any]:
    """Pure kwargs assembly for ``genai.Client`` — unit-testable sans SDK.

    Two shapes, never mixed. Express mode is exactly ``vertexai=True`` plus the
    key; no project or location — the express endpoint infers the trial/billing
    project from the key itself. The Google Cloud project path is
    ``vertexai=True`` plus ``project``/``location`` and NO key at all: the SDK
    enforces that mutual exclusion, and sending both is a hard construction
    error rather than a preference.

    The explicit ``api_key`` argument outranks any ambient
    ``GOOGLE_API_KEY``/``GEMINI_API_KEY`` env, so no env-stripping is needed on
    the key paths. The project path authenticates through Application Default
    Credentials instead, so it drops the key argument entirely — an ambient
    ``GOOGLE_API_KEY`` next to it makes the SDK warn and pick one.
    """
    if route == "vertex" and project is not None and project.configured:
        kwargs: dict[str, Any] = {
            "vertexai": True,
            "project": project.project,
            "location": project.location,
        }
        if http_options is not None:
            kwargs["http_options"] = http_options
        return kwargs
    kwargs = {"api_key": api_key}
    if route == "vertex":
        kwargs["vertexai"] = True
    if http_options is not None:
        kwargs["http_options"] = http_options
    return kwargs


def _project_for(route: KeyRoute) -> VertexProject | None:
    """Project settings for a resolved route, plus the ADC side effect.

    ``None`` for AI Studio: that path has no notion of a Cloud project, and
    returning anything else would put a config read on every client build for
    the endpoint the vast majority of installs use. On the Vertex route the
    settings are read AND the service account is exported here, because the
    Cloud SDK reads that env at client construction — after this returns is
    already too late.
    """
    if route != "vertex":
        return None
    settings = vertex_project_settings()
    if settings.configured:
        _export_service_account(settings.service_account_path)
    return settings


def build_vertex_client(
    api_key: str = "", *, http_options: Any | None = None, realtime: bool = False
) -> Any:
    """Build a client PINNED to Vertex AI — no probe, no AI Studio fallback.

    The dedicated ``vertex`` provider family calls this instead of
    :func:`build_genai_client` because its endpoint is a user decision, not a
    guess: an ``AIza``-shaped key would be classified as AI Studio and sent to
    the wrong host. Pinning also skips the probe round-trip entirely.

    ``api_key`` may be empty on the Cloud project path, where Application
    Default Credentials do the authenticating.

    ``realtime=True`` resolves the DUPLEX endpoint instead of the ordinary one
    (:meth:`VertexProject.for_realtime`). The two differ: ``global`` serves the
    current Gemini generation for normal requests and no Live session at all, so
    a single location cannot drive both tiers.

    On the project path the client is handed the process-wide Application
    Default Credentials (:func:`vertex_credentials`) whenever they are loaded,
    so it never resolves — and never re-pays for — auth on its own. A cold cache
    is filled here only off the event loop; on the loop the SDK's own threaded
    lazy load stays in charge (see :func:`_adc_for_sync_build`).
    """
    from google import genai

    settings = _project_for("vertex")
    if not api_key and not (settings and settings.configured):
        raise RuntimeError(
            "Vertex AI is not configured: store a Vertex AI API key (or set "
            "[google].vertex_project for the Google Cloud project path)."
        )
    if realtime and settings is not None and settings.configured:
        settings = settings.for_realtime()
    kwargs = _client_kwargs(api_key, "vertex", _with_shared_tls(http_options), settings)
    if "project" in kwargs:
        credentials = _adc_for_sync_build()
        if credentials is not None:
            kwargs["credentials"] = credentials
    return genai.Client(**kwargs)


async def build_vertex_client_async(
    api_key: str = "", *, http_options: Any | None = None, realtime: bool = False
) -> Any:
    """Async twin of :func:`build_vertex_client`.

    Pinning means there is no probe to keep off the event loop. What this twin
    does keep off it is the ONE-TIME credential resolution on the project path:
    a cold cache is filled in a worker thread first, so the client built below
    already carries the shared credentials and the handshake that follows pays
    for the socket alone.
    """
    await _ensure_shared_tls_off_loop()
    if not api_key and cached_vertex_credentials() is None:
        # _project_for, not vertex_project_settings: it exports a configured
        # service account FIRST, so the load below reads that file and the
        # cache key the sync build looks up afterwards is the same one.
        settings = _project_for("vertex")
        if settings is not None and settings.configured:
            await asyncio.to_thread(vertex_credentials)
    return build_vertex_client(api_key, http_options=http_options, realtime=realtime)


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

    The client is handed the shared TLS context (see :func:`_with_shared_tls`)
    but NOT the shared Application Default Credentials: a key stored in a
    Gemini slot always authenticates by that key, even on the express route.
    Only the dedicated Vertex family (:func:`build_vertex_client`) walks the
    Cloud project path where ADC signs — the speed-up for that resolution
    lives there on purpose.
    """
    from google import genai

    resolved = route or resolve_google_key_route(api_key)
    return genai.Client(
        **_client_kwargs(api_key, resolved, _with_shared_tls(http_options), _project_for(resolved))
    )


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
    await _ensure_shared_tls_off_loop()
    return genai.Client(
        **_client_kwargs(api_key, resolved, _with_shared_tls(http_options), _project_for(resolved))
    )


def reset_route_cache() -> None:
    """Forget every probed verdict (tests; key rotation edge cases)."""
    with _CACHE_LOCK:
        _ROUTE_CACHE.clear()
