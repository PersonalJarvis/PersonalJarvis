"""UltraWiki embedding backends — six thin httpx adapters, one frozen protocol.

Every backend implements the frozen :class:`jarvis.ultrawiki.types.EmbeddingBackend`
protocol: ``name``, ``ready() -> (bool, reason)``, ``async embed(texts, *, model)``.
All adapters are plain HTTP (httpx) — no provider SDKs — and resolve credentials
through :func:`jarvis.core.config.get_secret` / ``get_secret_any`` (OS keyring →
ENV → ``.env`` → local-file fallback), so they work identically on Windows,
macOS, and a headless Linux server.

Design rule D-3 (BINDING for this slot): **embeddings have NO cross-family
fallback.** The embedding pair ``(backend, model)`` pins the vector space of the
whole corpus; silently crossing to a different provider mid-ingest would mix
incompatible vector spaces and corrupt semantic search. A dead or keyless
backend therefore pauses the embed stage honestly (``ready()`` reports why,
``embed()`` raises :class:`EmbeddingError` and the pipeline retries later) while
keyword search keeps working. This is deliberately different from the chat
tiers' AP-22 cross-family chains.

Probes are capability/credential checks only (AP-21): ``ready()`` never raises,
never performs a paid call, and never special-cases provider names beyond each
backend's own adapter.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from jarvis.core.config import get_secret, get_secret_any

log = logging.getLogger(__name__)

__all__ = [
    "EmbeddingError",
    "OllamaEmbedding",
    "GeminiEmbedding",
    "OpenAIEmbedding",
    "VoyageEmbedding",
    "MistralEmbedding",
    "CohereEmbedding",
    "EMBEDDING_BACKENDS",
    "DEFAULT_MODELS",
    "available_backends",
    "credential_source",
]


class EmbeddingError(RuntimeError):
    """A single embed() call failed (HTTP, transport, or response parse).

    The staged pipeline owns retries: this error keeps the item's last good
    state and schedules ``next_retry_at`` — it is never swallowed silently.
    """


#: Maximum texts per single HTTP request; larger inputs are chunked.
#: 96 is the smallest hard batch ceiling across the supported providers
#: (Cohere v2 caps at 96; Gemini batchEmbedContents at 100).
_CHUNK_SIZE = 96

#: Embedding calls move real payloads; a local Ollama on CPU can be slow.
_EMBED_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0)

#: ready() must answer fast — an offline endpoint may not stall anything.
_PROBE_TIMEOUT = httpx.Timeout(connect=2.0, read=2.0, write=2.0, pool=2.0)

_DEFAULT_OLLAMA_ENDPOINT = "http://localhost:11434"

#: Any-of credential candidates for Gemini embeddings, mirroring the core
#: ``PROVIDER_SECRET_CANDIDATES["gemini"]`` generic slots.
_GEMINI_KEY_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("gemini_api_key", "GEMINI_API_KEY"),
    ("google_aistudio_api_key", "GOOGLE_AIStudio_API_KEY"),
    ("google_api_key", "GOOGLE_API_KEY"),
)


#: Body keys that may carry a provider's machine-readable failure code. Every
#: supported backend answers an error as a JSON object with a short slug under
#: one of these, usually nested in an ``error`` object. Nothing here names a
#: provider or a model (AP-21) — it is the shape of an error body, not a brand.
_ERROR_CODE_KEYS: tuple[str, ...] = ("code", "type", "status", "reason")

#: A failure code is a SLUG, not prose. Anything longer or wordier is a human
#: message, and a message can quote the submitted text straight back — which
#: must never reach a log line or the ``last_error`` column of an item row.
_CODE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")


def _error_code(response: httpx.Response) -> str:
    """The provider's short failure code, or ``""`` when it did not send one.

    Why this exists: an embeddings endpoint answers ``HTTP 429`` both for "you
    are sending too fast, wait a moment" and for "this account is out of
    credit, nothing changes until someone acts". For a backlog those are
    opposite situations — the first clears itself, the second never does — and
    the status code alone cannot tell them apart. The body's code slug can
    (``rate_limit_exceeded`` vs ``insufficient_quota``), so the pipeline is
    given the chance to see it and stop presenting a dead queue as a busy one.

    Only a slug is ever returned, never the human-readable message beside it:
    this string is logged and stored on the item, and a provider is free to
    echo the submitted text back inside a message.
    """
    try:
        body = response.json()
    except (ValueError, TypeError):
        return ""
    scopes = [body] if isinstance(body, dict) else []
    if scopes and isinstance(body.get("error"), dict):
        scopes.insert(0, body["error"])
    for scope in scopes:
        for key in _ERROR_CODE_KEYS:
            value = scope.get(key)
            if isinstance(value, str) and _CODE_PATTERN.match(value.strip()):
                return value.strip().lower()
    return ""


def _chunks(texts: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(texts), size):
        yield texts[start : start + size]


class _HttpEmbedding:
    """Shared httpx plumbing for every backend.

    ``transport`` is injectable so tests run fully offline through
    ``httpx.MockTransport``; production callers leave it ``None``.
    """

    name = "base"

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def credential_source(self) -> str:
        """Honest, KEY-FREE provenance of what satisfies this backend.

        The settings and Ask surfaces show users which credential or endpoint
        is actually carrying semantic search ("via your saved Gemini API key"),
        so a downloader can tell WHY a slot is live. It names the secret slot or
        endpoint — never the value, and never a truncated value.
        """
        return ""

    def _async_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=_EMBED_TIMEOUT, transport=self._transport)

    async def _post_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None,
        payload: dict[str, Any],
    ) -> Any:
        """POST and return the parsed JSON body, mapping every failure to
        :class:`EmbeddingError` with an honest, credential-free message."""
        try:
            async with self._async_client() as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            # The code slug rides along so the pipeline can tell a wait-and-
            # retry rejection from one that needs the user (see _error_code).
            code = _error_code(exc.response)
            raise EmbeddingError(
                f"{self.name}: embedding request failed with "
                f"HTTP {exc.response.status_code}" + (f" ({code})" if code else "")
            ) from exc
        except httpx.HTTPError as exc:
            raise EmbeddingError(
                f"{self.name}: embedding request failed ({type(exc).__name__})"
            ) from exc
        except ValueError as exc:  # response body is not JSON
            raise EmbeddingError(f"{self.name}: embedding response is not valid JSON") from exc

    def _check_count(self, vectors: list[list[float]], expected: int) -> list[list[float]]:
        if len(vectors) != expected:
            raise EmbeddingError(f"{self.name}: expected {expected} vectors, got {len(vectors)}")
        return vectors


class OllamaEmbedding(_HttpEmbedding):
    """Local Ollama — the keyless, offline-friendly option.

    ``ready()`` is a fast ``GET {endpoint}/api/version`` with a 2 s connect
    timeout, mirroring the honest-timeout style of the ack flash-brain Ollama
    adapter: an offline daemon answers "not ready" quickly, never stalls.
    """

    name = "ollama"

    def __init__(
        self,
        endpoint: str = _DEFAULT_OLLAMA_ENDPOINT,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(transport=transport)
        self._endpoint = (endpoint or _DEFAULT_OLLAMA_ENDPOINT).rstrip("/")

    def credential_source(self) -> str:
        return f"local Ollama endpoint {self._endpoint} (no key, nothing leaves this machine)"

    def ready(self) -> tuple[bool, str]:
        url = f"{self._endpoint}/api/version"
        try:
            with httpx.Client(timeout=_PROBE_TIMEOUT, transport=self._transport) as client:
                client.get(url).raise_for_status()
            return True, ""
        except Exception as exc:  # noqa: BLE001 — ready() never raises
            return False, (
                f"Ollama endpoint {self._endpoint} is unreachable "
                f"({type(exc).__name__}); is 'ollama serve' running?"
            )

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        vectors: list[list[float]] = []
        for chunk in _chunks(texts, _CHUNK_SIZE):
            data = await self._post_json(
                f"{self._endpoint}/api/embed",
                headers=None,
                payload={"model": model, "input": chunk},
            )
            try:
                got = [list(map(float, vec)) for vec in data["embeddings"]]
            except (KeyError, TypeError, ValueError) as exc:
                raise EmbeddingError(f"{self.name}: unexpected embedding response shape") from exc
            vectors.extend(self._check_count(got, len(chunk)))
        return vectors


class GeminiEmbedding(_HttpEmbedding):
    """Google Gemini embeddings — AI Studio REST, Vertex express when routed.

    AI Studio keys use ``batchEmbedContents`` (unchanged historical path).
    A key that :mod:`jarvis.core.google_genai` resolves to Vertex express
    mode goes through ``:predict`` on the global aiplatform host instead —
    the ``:embedContent`` route does not exist there (measured 2026-08-12).
    Vertex serves gemini-embedding-001 one instance per request, so the
    express path sends texts individually; embedding runs are background
    jobs, and a correct slow lane beats a fast 400.
    """

    name = "gemini"

    _BASE = "https://generativelanguage.googleapis.com/v1beta"
    _VERTEX_URL_TMPL = (
        "https://aiplatform.googleapis.com/v1beta1/publishers/google/models/{model}:predict"
    )
    _KEY_LABEL = "Gemini"

    def _key(self) -> str | None:
        return get_secret_any(_GEMINI_KEY_CANDIDATES)

    def credential_source(self) -> str:
        # Report WHICH of the accepted slots answered — a user with only
        # GOOGLE_API_KEY set should see that, not a generic "Gemini key".
        for slot, env in _GEMINI_KEY_CANDIDATES:
            if get_secret(slot, env_fallback=env):
                return f"your saved {self._KEY_LABEL} API key ({slot})"
        return ""

    def ready(self) -> tuple[bool, str]:
        if self._key():
            return True, ""
        return False, (
            "No Gemini API key is configured - save gemini_api_key in the "
            "API-Keys view or set the GEMINI_API_KEY environment variable."
        )

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        key = self._key()
        if not key:
            raise EmbeddingError(f"{self.name}: no API key configured")
        # Route check is lazy and cached process-wide; AIza keys never probe.
        from jarvis.core.google_genai import resolve_google_key_route_async

        if await resolve_google_key_route_async(key) == "vertex":
            return await self._embed_vertex(key, texts, model=model)
        url = f"{self._BASE}/models/{model}:batchEmbedContents"
        vectors: list[list[float]] = []
        for chunk in _chunks(texts, _CHUNK_SIZE):
            data = await self._post_json(
                url,
                headers={"x-goog-api-key": key},
                payload={
                    "requests": [
                        {
                            "model": f"models/{model}",
                            "content": {"parts": [{"text": text}]},
                        }
                        for text in chunk
                    ]
                },
            )
            try:
                got = [list(map(float, entry["values"])) for entry in data["embeddings"]]
            except (KeyError, TypeError, ValueError) as exc:
                raise EmbeddingError(f"{self.name}: unexpected embedding response shape") from exc
            vectors.extend(self._check_count(got, len(chunk)))
        return vectors

    async def _embed_vertex(self, key: str, texts: list[str], *, model: str) -> list[list[float]]:
        """Vertex express ``:predict`` lane — one instance per request.

        Embeddings are officially undocumented for express mode (only
        countTokens/generateContent/streamGenerateContent are in scope), so a
        rejection here is a real possibility — the wrapped error says what to
        do about it instead of leaving a bare HTTP code.
        """
        url = self._VERTEX_URL_TMPL.format(model=model)
        vectors: list[list[float]] = []
        for text in texts:
            try:
                data = await self._post_json(
                    url,
                    headers={"x-goog-api-key": key},
                    payload={"instances": [{"content": text}]},
                )
            except EmbeddingError as exc:
                raise EmbeddingError(
                    f"{exc} — this Google key routes through Vertex AI "
                    "express mode; if embeddings stay unavailable there, use "
                    "a Google AI Studio key for UltraWiki embeddings."
                ) from exc
            try:
                values = data["predictions"][0]["embeddings"]["values"]
                vectors.append([float(v) for v in values])
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise EmbeddingError(
                    f"{self.name}: unexpected Vertex embedding response shape"
                ) from exc
        return self._check_count(vectors, len(texts))


class _OpenAIShapedEmbedding(_HttpEmbedding):
    """Shared adapter for the OpenAI-compatible ``/v1/embeddings`` shape
    (OpenAI, Voyage, Mistral): Bearer auth, ``{"model", "input": [...]}``,
    response ``{"data": [{"index", "embedding"}]}``."""

    _URL = ""
    _SECRET_SLOT = ""
    _KEY_LABEL = ""

    def _key(self) -> str | None:
        return get_secret(self._SECRET_SLOT)

    def credential_source(self) -> str:
        if self._key():
            return f"your saved {self._KEY_LABEL} API key ({self._SECRET_SLOT})"
        return ""

    def ready(self) -> tuple[bool, str]:
        if self._key():
            return True, ""
        # Short and place-neutral: the line is read on the settings card (where
        # the key field now sits, right below it), in CLI output and in logs.
        # It used to send the user to the API-Keys view, which has no field for
        # this slot -- a dead end dressed up as an instruction -- and to spell
        # out the raw snake_case slot name, which is noise next to the input
        # box it names. The env var stays: that is the headless recovery path.
        return False, (
            f"No {self._KEY_LABEL} API key yet - add one below, or set {self._SECRET_SLOT.upper()}."
        )

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        key = self._key()
        if not key:
            raise EmbeddingError(f"{self.name}: no API key configured")
        vectors: list[list[float]] = []
        for chunk in _chunks(texts, _CHUNK_SIZE):
            data = await self._post_json(
                self._URL,
                headers={"Authorization": f"Bearer {key}"},
                payload={"model": model, "input": chunk},
            )
            try:
                rows = list(data["data"])
                rows.sort(key=lambda row: int(row.get("index", 0)))
                got = [list(map(float, row["embedding"])) for row in rows]
            except (KeyError, TypeError, ValueError) as exc:
                raise EmbeddingError(f"{self.name}: unexpected embedding response shape") from exc
            vectors.extend(self._check_count(got, len(chunk)))
        return vectors


class OpenAIEmbedding(_OpenAIShapedEmbedding):
    name = "openai"
    _URL = "https://api.openai.com/v1/embeddings"
    _SECRET_SLOT = "openai_api_key"  # noqa: S105 — secret slot NAME, not a value
    _KEY_LABEL = "OpenAI"


class VoyageEmbedding(_OpenAIShapedEmbedding):
    name = "voyage"
    _URL = "https://api.voyageai.com/v1/embeddings"
    _SECRET_SLOT = "voyage_api_key"  # noqa: S105 — secret slot NAME, not a value
    _KEY_LABEL = "Voyage AI"


class MistralEmbedding(_OpenAIShapedEmbedding):
    name = "mistral"
    _URL = "https://api.mistral.ai/v1/embeddings"
    _SECRET_SLOT = "mistral_api_key"  # noqa: S105 — secret slot NAME, not a value
    _KEY_LABEL = "Mistral"


class CohereEmbedding(_HttpEmbedding):
    """Cohere v2 embed. ``input_type`` distinguishes stored documents from
    query-time embeddings; the ingest pipeline keeps the default."""

    name = "cohere"

    _URL = "https://api.cohere.com/v2/embed"

    def __init__(
        self,
        *,
        input_type: str = "search_document",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(transport=transport)
        self._input_type = input_type

    def _key(self) -> str | None:
        return get_secret("cohere_api_key")

    def credential_source(self) -> str:
        return "your saved Cohere API key (cohere_api_key)" if self._key() else ""

    def ready(self) -> tuple[bool, str]:
        if self._key():
            return True, ""
        return False, (
            "No Cohere API key is configured - save cohere_api_key in the "
            "API-Keys view or set the COHERE_API_KEY environment variable."
        )

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        key = self._key()
        if not key:
            raise EmbeddingError(f"{self.name}: no API key configured")
        vectors: list[list[float]] = []
        for chunk in _chunks(texts, _CHUNK_SIZE):
            data = await self._post_json(
                self._URL,
                headers={"Authorization": f"Bearer {key}"},
                payload={
                    "model": model,
                    "texts": chunk,
                    "input_type": self._input_type,
                    "embedding_types": ["float"],
                },
            )
            try:
                got = [list(map(float, vec)) for vec in data["embeddings"]["float"]]
            except (KeyError, TypeError, ValueError) as exc:
                raise EmbeddingError(f"{self.name}: unexpected embedding response shape") from exc
            vectors.extend(self._check_count(got, len(chunk)))
        return vectors


def _make_ollama(cfg: Any) -> OllamaEmbedding:
    endpoint = _DEFAULT_OLLAMA_ENDPOINT
    ultrawiki = getattr(cfg, "ultrawiki", None)
    if ultrawiki is not None:
        endpoint = getattr(ultrawiki, "ollama_endpoint", endpoint) or endpoint
    return OllamaEmbedding(endpoint=endpoint)


#: name -> factory(cfg). The cfg argument feeds backend-local settings only
#: (today: the Ollama endpoint); credentials always ride the secret chain.
EMBEDDING_BACKENDS: dict[str, Callable[[Any], Any]] = {
    "ollama": _make_ollama,
    "gemini": lambda cfg: GeminiEmbedding(),
    "openai": lambda cfg: OpenAIEmbedding(),
    "voyage": lambda cfg: VoyageEmbedding(),
    "mistral": lambda cfg: MistralEmbedding(),
    "cohere": lambda cfg: CohereEmbedding(),
}

#: Sensible current default model per backend, surfaced by the settings UI.
DEFAULT_MODELS: dict[str, str] = {
    "ollama": "qwen3-embedding:4b",
    "gemini": "gemini-embedding-001",
    "openai": "text-embedding-3-small",
    "voyage": "voyage-3.5",
    "mistral": "mistral-embed",
    "cohere": "embed-v4.0",
}


def credential_source(name: str, cfg: Any) -> str:
    """Key-free provenance of one backend's credential/endpoint, or ``""``.

    Asks the ADAPTER (AP-21: no provider-name special-casing out here), so a
    new backend supplies its own honest phrase and this function never grows a
    branch. Never raises and never returns any part of a secret value.
    """
    factory = EMBEDDING_BACKENDS.get(name)
    if factory is None:
        return ""
    try:
        return str(factory(cfg).credential_source() or "")
    except Exception:  # noqa: BLE001 — provenance is decoration, never a failure
        log.debug("embedding backend %s provenance probe failed", name, exc_info=True)
        return ""


def available_backends(cfg: Any) -> list[dict[str, Any]]:
    """Readiness report over every registered backend, via ``ready()`` only.

    Purely capability/credential probes (AP-21): no paid calls, no provider
    special-casing beyond each backend's own adapter, never raises.
    """
    rows: list[dict[str, Any]] = []
    for name, factory in EMBEDDING_BACKENDS.items():
        try:
            usable, reason = factory(cfg).ready()
        except Exception as exc:  # noqa: BLE001 — a broken probe reports, never raises
            usable, reason = False, f"readiness probe failed ({type(exc).__name__})"
            log.debug("embedding backend %s readiness probe failed", name, exc_info=True)
        rows.append(
            {
                "name": name,
                "ready": usable,
                "reason": reason,
                "default_model": DEFAULT_MODELS.get(name, ""),
            }
        )
    return rows
