"""UltraWiki rerank backends — the LLM chain plus Voyage and Cohere adapters.

Rerank is an OPTIONAL retrieval stage (design doc 03): a small scoring model
re-scores the fused top-N candidates against the actual question. When no
rerank-capable provider is configured or the configured one is not ready, the
caller skips the stage honestly and the fusion order stands — never a brick.

**Scores are absolute, on a 0-10 scale, for EVERY backend.** That is the whole
point of the stage (Cerebras: "it gives each document a score from zero to ten,
and we keep the top ten"): RRF fusion is ordinal and can never say "nothing
here is relevant", while a 0-10 grade can. Every unsolicited surface gates on
that number (``[ultrawiki].rerank_min_score``), so the scale must not depend on
which backend answered — the vendor adapters normalize their native 0-1
relevance by x10 (AP-21: gate on the capability, never on the provider name).

Three backends:

- ``llm`` — the universal one. Grades through the key-aware, cross-family wiki
  provider chain (identical to :mod:`jarvis.ultrawiki.distill`), so it runs on
  whatever the install actually holds: a cloud key, a subscription CLI, or a
  purely local Ollama with no network at all. This is what keeps the stage
  alive for an arbitrary downloader (§3, AP-22).
- ``voyage`` / ``cohere`` — dedicated cross-encoder APIs for installs that hold
  one of those keys; lower latency, but a paid single-vendor path.

Vendor adapters are plain HTTP (no SDKs); credentials resolve through
:func:`jarvis.core.config.get_secret`. ``ready()`` is a credential-presence
probe only (AP-21): never raises, never performs a paid call. A failing rerank
call raises :class:`RerankError`; the retrieval path catches it and falls back
to fusion order.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx

from jarvis.core.config import get_secret

log = logging.getLogger(__name__)

__all__ = [
    "RerankError",
    "LLMReranker",
    "VoyageReranker",
    "CohereReranker",
    "RERANK_BACKENDS",
    "DEFAULT_RERANK_MODELS",
    "MAX_SCORE",
    "build_rerank_prompt",
    "parse_rerank_scores",
    "available_rerankers",
    "resolve_reranker",
]


class RerankError(RuntimeError):
    """A rerank call failed (HTTP, transport, or response parse). The caller
    skips the stage and keeps the fusion order."""


#: The one relevance scale every backend reports on (Cerebras: zero to ten).
MAX_SCORE = 10.0

_RERANK_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)

#: Sensible current default model per backend. The LLM backend has no fixed
#: default — the provider chain picks each family's cheap router-tier model.
DEFAULT_RERANK_MODELS: dict[str, str] = {
    "llm": "",
    "voyage": "rerank-2.5",
    "cohere": "rerank-v3.5",
}


def _to_scale(value: Any) -> float:
    """Vendor 0-1 relevance -> the shared 0-10 grade, clamped."""
    return round(min(max(float(value) * MAX_SCORE, 0.0), MAX_SCORE), 4)


def _clamp_score(value: Any) -> float:
    """An already-0-10 grade, clamped (a model may answer 11 or -1)."""
    return round(min(max(float(value), 0.0), MAX_SCORE), 4)


class _HttpReranker:
    """Shared httpx plumbing; ``transport`` is injectable for offline tests."""

    name = "base"
    _URL = ""
    _SECRET_SLOT = ""
    _KEY_LABEL = ""

    def __init__(
        self,
        *,
        model: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._model = model or DEFAULT_RERANK_MODELS.get(self.name, "")
        self._transport = transport

    def _key(self) -> str | None:
        return get_secret(self._SECRET_SLOT)

    def ready(self) -> tuple[bool, str]:
        if self._key():
            return True, ""
        # Short and place-neutral: the key field sits right below this line on
        # the provider's own card, so naming the raw snake_case slot is noise.
        # The env var stays -- it is the headless recovery path.
        return False, (
            f"No {self._KEY_LABEL} API key yet - add one below, "
            f"or set {self._SECRET_SLOT.upper()}."
        )

    def _payload(self, query: str, documents: list[str], top_k: int) -> dict[str, Any]:
        raise NotImplementedError

    def _parse(self, data: Any) -> list[tuple[int, float]]:
        raise NotImplementedError

    async def rerank(
        self, query: str, documents: list[str], top_k: int
    ) -> list[tuple[int, float]]:
        """Score ``documents`` against ``query``; return ``(index, score)``
        pairs, best first, at most ``top_k`` entries."""
        if not documents:
            return []
        key = self._key()
        if not key:
            raise RerankError(f"{self.name}: no API key configured")
        try:
            async with httpx.AsyncClient(
                timeout=_RERANK_TIMEOUT, transport=self._transport
            ) as client:
                response = await client.post(
                    self._URL,
                    headers={"Authorization": f"Bearer {key}"},
                    json=self._payload(query, documents, top_k),
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            raise RerankError(
                f"{self.name}: rerank request failed with "
                f"HTTP {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RerankError(
                f"{self.name}: rerank request failed ({type(exc).__name__})"
            ) from exc
        except ValueError as exc:
            raise RerankError(f"{self.name}: rerank response is not valid JSON") from exc
        try:
            return self._parse(data)
        except (KeyError, TypeError, ValueError) as exc:
            raise RerankError(f"{self.name}: unexpected rerank response shape") from exc


class VoyageReranker(_HttpReranker):
    """Voyage AI ``POST /v1/rerank``."""

    name = "voyage"
    _URL = "https://api.voyageai.com/v1/rerank"
    _SECRET_SLOT = "voyage_api_key"  # noqa: S105 — secret slot NAME, not a value
    _KEY_LABEL = "Voyage AI"

    def _payload(self, query: str, documents: list[str], top_k: int) -> dict[str, Any]:
        return {
            "model": self._model,
            "query": query,
            "documents": documents,
            "top_k": top_k,
        }

    def _parse(self, data: Any) -> list[tuple[int, float]]:
        return [
            (int(row["index"]), _to_scale(row["relevance_score"]))
            for row in data["data"]
        ]


class CohereReranker(_HttpReranker):
    """Cohere ``POST /v2/rerank``."""

    name = "cohere"
    _URL = "https://api.cohere.com/v2/rerank"
    _SECRET_SLOT = "cohere_api_key"  # noqa: S105 — secret slot NAME, not a value
    _KEY_LABEL = "Cohere"

    def _payload(self, query: str, documents: list[str], top_k: int) -> dict[str, Any]:
        return {
            "model": self._model,
            "query": query,
            "documents": documents,
            "top_n": top_k,
        }

    def _parse(self, data: Any) -> list[tuple[int, float]]:
        return [
            (int(row["index"]), _to_scale(row["relevance_score"]))
            for row in data["results"]
        ]


# ---------------------------------------------------------------------------
# LLM reranker — the universal backend
# ---------------------------------------------------------------------------

#: Candidate text is truncated before grading: a relevance judgement needs the
#: opening of a document, not its full body, and 20 untruncated candidates
#: would dominate a cheap model's context window.
_DOC_TRUNCATE_CHARS = 700
_DOC_TRUNCATION_MARKER = " […]"

_LLM_TIMEOUT_S = 45.0

_LLM_SYSTEM_PROMPT = (
    "You are a precise relevance grader inside a personal memory search "
    "engine. You grade how well each candidate document answers ONE question. "
    "You answer with STRICT minified JSON only - no prose, no markdown fences, "
    "no keys beyond the requested ones. You grade what is written, you never "
    "invent content a candidate does not contain."
)


def _truncate_document(text: str) -> str:
    flat = " ".join(str(text or "").split())
    if len(flat) <= _DOC_TRUNCATE_CHARS:
        return flat
    return flat[:_DOC_TRUNCATE_CHARS] + _DOC_TRUNCATION_MARKER


def build_rerank_prompt(query: str, documents: list[str]) -> str:
    """The grading prompt: XML-tagged sections, strict-JSON contract.

    Candidates are numbered so the model answers with indices instead of
    echoing text back — cheaper, and it cannot rename a document.
    """
    candidates = "\n".join(
        f"[{index}] {_truncate_document(text)}"
        for index, text in enumerate(documents)
    )
    return (
        "<task>\n"
        "Grade how well each candidate answers the question below.\n"
        "</task>\n"
        f"<question>{' '.join(str(query).split())}</question>\n"
        f"<candidates>\n{candidates}\n</candidates>\n"
        "<scale>\n"
        "10 = directly and completely answers the question\n"
        "7-9 = answers it, or contains the decisive fact\n"
        "4-6 = related and useful background, but does not answer it\n"
        "1-3 = same topic area only, or a near-duplicate wording that "
        "answers a DIFFERENT question\n"
        "0 = unrelated, or pure filler with no information\n"
        "</scale>\n"
        "<output_format>\n"
        "Return STRICT minified JSON on a single line, one entry per "
        "candidate, in any order:\n"
        '{"scores":[{"i":0,"score":7},{"i":1,"score":0}]}\n'
        "Rules: no markdown fences; no additional keys; no commentary; \"i\" is\n"
        "the candidate number shown in brackets; \"score\" is an integer 0-10.\n"
        "Grade every candidate, including the ones you score 0.\n"
        "</output_format>"
    )


def parse_rerank_scores(text: str, *, count: int) -> list[tuple[int, float]]:
    """Salvage ``(index, score)`` pairs out of a model's grading answer.

    Out-of-range indices, duplicates, and unparseable entries are dropped;
    candidates the model never mentioned simply do not appear (the caller
    appends them below the graded ones, keeping fusion order among them).
    Raises :class:`RerankError` when nothing usable could be salvaged, which
    lets the provider chain try the next family.
    """
    from jarvis.ultrawiki.distill import extract_json_object  # noqa: PLC0415 — lazy (AP-26)

    extracted = extract_json_object(text)
    if extracted is None:
        raise RerankError("llm: grading output holds no parseable JSON object")
    _, parsed = extracted
    rows = parsed.get("scores")
    if not isinstance(rows, (list, tuple)):
        raise RerankError("llm: grading JSON has no 'scores' array")
    pairs: list[tuple[int, float]] = []
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row["i"])
            score = _clamp_score(row["score"])
        except (KeyError, TypeError, ValueError):
            continue
        if index < 0 or index >= count or index in seen:
            continue
        seen.add(index)
        pairs.append((index, score))
    if not pairs:
        raise RerankError("llm: grading output scored no candidate")
    pairs.sort(key=lambda pair: (-pair[1], pair[0]))
    return pairs


class LLMReranker:
    """Grades candidates through the key-aware, cross-family provider chain.

    The universal backend: it needs no rerank-specific vendor account, only a
    chat provider the install already has — including a purely local Ollama on
    a headless box. Model resolution mirrors :mod:`jarvis.ultrawiki.distill`:
    an explicit ``[ultrawiki].rerank_model`` applies to the configured leading
    provider, every other credential-ready family follows with its own cheap
    router-tier model.
    """

    name = "llm"

    def __init__(
        self,
        cfg: Any = None,
        *,
        registry: Any = None,
        timeout_s: float = _LLM_TIMEOUT_S,
    ) -> None:
        self._cfg = cfg
        self._registry = registry
        self._timeout_s = timeout_s

    # -- chain ---------------------------------------------------------------

    def _build_registry(self) -> Any:
        if self._registry is None:
            from jarvis.brain.provider_registry import (  # noqa: PLC0415 — lazy (AP-26)
                BrainProviderRegistry,
            )

            self._registry = BrainProviderRegistry()
        return self._registry

    def _chain(self) -> list[tuple[str, str | None]]:
        """Ordered ``(provider, model)`` attempts; empty when nothing is ready."""
        from jarvis.memory.wiki.provider_chain import (  # noqa: PLC0415 — lazy (AP-26)
            build_wiki_provider_chain,
            credential_ready_wiki_providers,
        )

        cfg = self._cfg
        ultrawiki = getattr(cfg, "ultrawiki", None)
        configured = str(getattr(ultrawiki, "rerank_model", "") or "").strip()
        # An explicit rerank PROVIDER cannot be named here: "llm" IS the slot
        # value. The chain therefore leads with the distill provider when set
        # (same tier of work, same cheap models), else the brain primary.
        lead = str(getattr(ultrawiki, "distill_provider", "") or "").strip()
        if not lead:
            brain = getattr(cfg, "brain", None)
            lead = str(getattr(brain, "primary", "") or "")
        available = set(self._build_registry().available())
        credential_ready = credential_ready_wiki_providers(
            available=available, config=cfg
        )
        return build_wiki_provider_chain(
            primary=lead,
            model_override=configured,
            available=available,
            credential_ready=credential_ready,
        )

    def credential_source(self) -> str:
        """Key-free provenance: which family would actually grade (the HTTP
        adapters' twin, so the settings card can say where this runs)."""
        try:
            chain = self._chain()
        except Exception:  # noqa: BLE001 — provenance is decoration, never a failure
            return ""
        if not chain:
            return ""
        provider, model = chain[0]
        suffix = f" ({model})" if model else ""
        return f"your configured {provider} chat provider{suffix}"

    def ready(self) -> tuple[bool, str]:
        """Credential-presence probe only (AP-21) — never a paid call."""
        try:
            chain = self._chain()
        except Exception as exc:  # noqa: BLE001 — a broken probe reports, never raises
            log.debug("llm rerank chain probe failed", exc_info=True)
            return False, f"provider chain probe failed ({type(exc).__name__})"
        if chain:
            return True, ""
        return False, (
            "No credential-ready chat provider is available to grade results - "
            "save any provider key in the API-Keys view, connect a "
            "subscription CLI, or run a local Ollama model."
        )

    # -- grading -------------------------------------------------------------

    async def rerank(
        self, query: str, documents: list[str], top_k: int
    ) -> list[tuple[int, float]]:
        """Grade ``documents`` 0-10 against ``query``; best first, at most
        ``top_k`` entries."""
        if not documents:
            return []
        from jarvis.brain.streaming import aggregate  # noqa: PLC0415 — lazy (AP-26)
        from jarvis.core.protocols import (  # noqa: PLC0415 — lazy (AP-26)
            BrainMessage,
            BrainRequest,
        )
        from jarvis.memory.wiki.provider_chain import (  # noqa: PLC0415 — lazy (AP-26)
            complete_with_fallback,
        )

        chain = self._chain()
        if not chain:
            raise RerankError("llm: no credential-ready provider to grade with")

        count = len(documents)

        def _validate(agg: Any) -> str | None:
            """A transport success whose text holds no usable grades is
            unusable output — the chain then tries the next family."""
            try:
                parse_rerank_scores(getattr(agg, "text", "") or "", count=count)
            except RerankError as exc:
                return str(exc)
            return None

        request = BrainRequest(
            messages=(
                BrainMessage(
                    role="user", content=build_rerank_prompt(query, documents)
                ),
            ),
            system=_LLM_SYSTEM_PROMPT,
            temperature=0.0,  # grading, not creativity
            max_tokens=1200,
            stream=True,
        )
        result = await complete_with_fallback(
            registry=self._build_registry(),
            chain=chain,
            request=request,
            timeout_s=self._timeout_s,
            label="UltraWikiReranker",
            aggregate=aggregate,
            validate=_validate,
        )
        if result is None:
            raise RerankError(
                "llm: no configured provider returned usable relevance grades"
            )
        agg, provider = result
        pairs = parse_rerank_scores(getattr(agg, "text", "") or "", count=count)
        log.debug("reranked %d candidates via provider %s", count, provider)
        return pairs[: max(0, int(top_k))]


#: name -> factory(cfg). ``llm`` leads: it is the backend that works with
#: whatever credential the install actually holds (§3, AP-22), while the vendor
#: adapters need one specific paid account. cfg is unused by the HTTP adapters
#: (their keys ride the secret chain) but keeps one registry shape.
RERANK_BACKENDS: dict[str, Callable[[Any], Any]] = {
    "llm": lambda cfg: LLMReranker(cfg),
    "voyage": lambda cfg: VoyageReranker(),
    "cohere": lambda cfg: CohereReranker(),
}


#: One-line English explanation per backend for the settings surface.
RERANK_DETAILS: dict[str, str] = {
    "llm": (
        "Grades results with the chat providers you already have — cloud key, "
        "subscription CLI, or a local Ollama model offline. No extra account."
    ),
    "voyage": "Dedicated Voyage AI reranking model — needs a Voyage API key.",
    "cohere": "Dedicated Cohere reranking model — needs a Cohere API key.",
}


def available_rerankers(cfg: Any) -> list[dict[str, Any]]:
    """Readiness report over every rerank backend, via ``ready()`` only."""
    rows: list[dict[str, Any]] = []
    for name, factory in RERANK_BACKENDS.items():
        try:
            usable, reason = factory(cfg).ready()
        except Exception as exc:  # noqa: BLE001 — a broken probe reports, never raises
            usable, reason = False, f"readiness probe failed ({type(exc).__name__})"
            log.debug("rerank backend %s readiness probe failed", name, exc_info=True)
        rows.append(
            {
                "name": name,
                "ready": usable,
                "reason": reason,
                "default_model": DEFAULT_RERANK_MODELS.get(name, ""),
                "detail": RERANK_DETAILS.get(name, ""),
            }
        )
    return rows


def resolve_reranker(cfg: Any) -> Any | None:
    """The configured, ready reranker — or ``None``, telling the caller to
    skip the stage honestly (unconfigured, unknown, or not ready)."""
    ultrawiki = getattr(cfg, "ultrawiki", None)
    name = str(getattr(ultrawiki, "rerank_provider", "") or "").strip()
    if not name:
        return None
    factory = RERANK_BACKENDS.get(name)
    if factory is None:
        log.warning("unknown rerank provider %r - skipping the rerank stage", name)
        return None
    backend = factory(cfg)
    usable, reason = backend.ready()
    if not usable:
        log.info("rerank provider %s not ready (%s) - skipping the stage", name, reason)
        return None
    return backend
