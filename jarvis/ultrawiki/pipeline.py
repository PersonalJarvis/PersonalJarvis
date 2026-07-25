"""UltraWiki staged pipeline worker — one loop advancing items through STATE_ORDER.

Design doc 02: ingestion is a state machine in the database, never a function
chain. Each pass claims a batch per stage, performs exactly ONE transition per
item, and commits through the store — a crash or deploy mid-run restarts to an
identical end state. Stages, in ladder order:

- ``captured -> keyword_indexed`` (instant, no model): the FTS upsert happens
  in the SAME store transaction as the state transition.
- ``keyword_indexed -> embedded`` (async, cheap): the RAW document (title +
  body, trimmed) is stored and embedded with the CONFIGURED embedding backend.
  An unconfigured or not-ready slot means the stage claims NO work — the
  backlog stays honest and keyword search keeps working (D-3: embeddings have
  no cross-family fallback).
- ``embedded -> distilled`` (async, the expensive stage): the distillation
  cache is consulted first on ``(content_hash, PROMPT_VERSION, model)``; on a
  miss the injected ``distill_fn`` runs, the SUMMARY document is stored with
  its ``distill_json``, the summary text is embedded too, and the result is
  cached so identical input is never paid for twice.

Error discipline: a per-item failure goes through ``store.mark_retry`` (60s *
4^n backoff, dead-letter after 5 attempts) and the loop NEVER dies on one item
— it logs and continues. ``asyncio.CancelledError`` is always re-raised so the
service's cancel-then-wait shutdown stays honest.

This module imports only the stdlib and the dependency-free types module at
import time (AP-26); heavier modules (embedding defaults, the distill prompt
contract) are imported lazily inside the stages.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from jarvis.ultrawiki.types import DocType, ItemState

log = logging.getLogger(__name__)

__all__ = [
    "KEYWORD_BATCH",
    "EMBED_BATCH",
    "DISTILL_BATCH",
    "IDLE_SLEEP_S",
    "BUSY_SLEEP_S",
    "PipelineWorker",
]

#: Per-pass claim sizes (design doc 02 batching).
KEYWORD_BATCH = 200
EMBED_BATCH = 32
DISTILL_BATCH = 4

#: Loop pacing: quick follow-up while there is work, gentle poll when idle.
IDLE_SLEEP_S = 2.0
BUSY_SLEEP_S = 0.1

#: The embedding ``ready()`` probe may hit the network (Ollama); cache its
#: verdict briefly so an idle loop does not hammer a dead endpoint.
_READY_PROBE_TTL_S = 30.0

#: Zero-arg factory returning the CONFIGURED embedding backend (an object
#: implementing ``jarvis.ultrawiki.types.EmbeddingBackend``) or ``None`` when
#: the slot is unconfigured — the factory owns the config decision.
EmbeddingBackendFactory = Callable[[], Any]

#: ``distill_fn(cfg, *, title, body, source_kind) -> DistillResult-like``.
DistillFn = Callable[..., Awaitable[Any]]


def _summary_text(
    question: str, summary: str, resolution: str, entities: list[str]
) -> str:
    """Compose the embed-ready text of a SUMMARY document from its fields."""
    parts: list[str] = []
    if question:
        parts.append(question)
    if summary:
        parts.append(summary)
    if resolution:
        parts.append(f"Resolution: {resolution}")
    if entities:
        parts.append("Entities: " + ", ".join(entities))
    return "\n".join(parts)


def _string_field(mapping: dict[str, Any], key: str) -> str:
    return str(mapping.get(key) or "").strip()


def _list_field(mapping: dict[str, Any], key: str) -> list[str]:
    value = mapping.get(key)
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(entry).strip() for entry in value if str(entry).strip()]
    return []


class PipelineWorker:
    """Advance items through the staged state machine, one transition each.

    ``embedding_backend_factory`` returns the configured backend or ``None``
    (unconfigured slot). ``distill_fn`` is the distillation entry point
    (production: ``jarvis.ultrawiki.distill.distill_text``); it is injected so
    tests run offline. ``now_fn`` is a test seam feeding the store's
    retry-eligibility clock; production leaves it ``None`` (real time).
    """

    def __init__(
        self,
        store: Any,
        cfg: Any,
        *,
        embedding_backend_factory: EmbeddingBackendFactory,
        distill_fn: DistillFn,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._cfg = cfg
        self._backend_factory = embedding_backend_factory
        self._distill_fn = distill_fn
        self._now_fn = now_fn
        #: Per-stage processed counters (successful transitions only).
        self.processed: dict[str, int] = {"keyword": 0, "embed": 0, "distill": 0}
        self._ready_cache: tuple[float, str, bool, str] | None = None
        self._last_slot_reason: str | None = None
        self._source_kind_cache: dict[str, str] = {}

    # -- public surface ------------------------------------------------------

    def processed_counts(self) -> dict[str, int]:
        """Copy of the per-stage processed counters."""
        return dict(self.processed)

    async def run(self, cancel_event: asyncio.Event) -> None:
        """The worker loop: run passes until *cancel_event* is set.

        A failed pass is logged and the loop continues; ``CancelledError``
        (hard task cancel) is always re-raised.
        """
        log.info("UltraWiki pipeline worker started")
        try:
            while not cancel_event.is_set():
                try:
                    attempted = await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("UltraWiki pipeline pass failed; continuing")
                    attempted = 0
                delay = BUSY_SLEEP_S if attempted else IDLE_SLEEP_S
                if cancel_event.is_set():
                    break
                try:
                    await asyncio.wait_for(cancel_event.wait(), timeout=delay)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            log.debug("UltraWiki pipeline worker cancelled")
            raise
        finally:
            log.info("UltraWiki pipeline worker stopped")

    async def run_once(self) -> int:
        """One full pass over all stages; returns the number of items worked
        on (successes AND per-item failures — pacing counts attempts)."""
        attempted = 0
        attempted += await self._keyword_pass()
        attempted += await self._embed_pass()
        attempted += await self._distill_pass()
        return attempted

    # -- shared helpers ------------------------------------------------------

    def _claim_now(self) -> datetime | None:
        return self._now_fn() if self._now_fn is not None else None

    async def _retry(self, item: dict[str, Any], stage: str, exc: BaseException) -> None:
        log.warning(
            "UltraWiki %s stage failed for item %s (%s): %s",
            stage,
            item.get("id"),
            item.get("external_id"),
            exc,
        )
        try:
            await self._store.mark_retry(
                int(item["id"]), f"{stage}: {exc}", now=self._claim_now()
            )
        except Exception:
            log.exception(
                "UltraWiki: mark_retry failed for item %s", item.get("id")
            )

    def _note_slot_gap(self, reason: str) -> None:
        """Log an honest 'stage paused' line once per reason change."""
        if reason != self._last_slot_reason:
            self._last_slot_reason = reason
            log.info("UltraWiki embed/distill stages paused: %s", reason)

    def _embedding_slot(self) -> tuple[Any | None, str, str]:
        """``(backend, model, reason)`` — backend is ``None`` when the slot is
        unconfigured, unknown, model-less, or its ``ready()`` probe fails."""
        try:
            backend = self._backend_factory()
        except Exception as exc:  # noqa: BLE001 — a broken factory pauses, never kills
            return None, "", f"embedding backend factory failed ({type(exc).__name__})"
        if backend is None:
            return None, "", (
                "no embedding backend is configured - pick one in the "
                "UltraWiki settings; keyword search keeps working"
            )
        model = str(
            getattr(getattr(self._cfg, "ultrawiki", None), "embedding_model", "") or ""
        ).strip()
        if not model:
            from jarvis.ultrawiki.embeddings import (  # noqa: PLC0415 — lazy (AP-26)
                DEFAULT_MODELS,
            )

            model = DEFAULT_MODELS.get(getattr(backend, "name", ""), "")
        if not model:
            return None, "", (
                f"embedding backend {getattr(backend, 'name', '?')!r} has no "
                "configured or default model"
            )
        ok, reason = self._backend_ready(backend)
        if not ok:
            return None, "", reason
        return backend, model, ""

    def _backend_ready(self, backend: Any) -> tuple[bool, str]:
        name = str(getattr(backend, "name", ""))
        now = time.monotonic()
        cached = self._ready_cache
        if cached is not None and cached[1] == name and now < cached[0]:
            return cached[2], cached[3]
        try:
            ok, reason = backend.ready()
        except Exception as exc:  # noqa: BLE001 — ready() must never kill the loop
            ok, reason = False, f"embedding readiness probe failed ({type(exc).__name__})"
        self._ready_cache = (now + _READY_PROBE_TTL_S, name, bool(ok), reason)
        return bool(ok), reason

    async def _source_kind(self, source_id: str) -> str:
        kind = self._source_kind_cache.get(source_id)
        if kind is not None:
            return kind
        try:
            source = await self._store.get_source(source_id)
        except Exception:  # noqa: BLE001 — a lookup hiccup degrades to 'unknown'
            source = None
        kind = str((source or {}).get("connector") or "unknown")
        self._source_kind_cache[source_id] = kind
        return kind

    @staticmethod
    def _raw_text(item: dict[str, Any]) -> str:
        title = str(item.get("title") or "")
        body = str(item.get("body_raw") or "")
        text = f"{title}\n\n{body}".strip()
        return text or str(item.get("external_id") or "")

    # -- stage passes --------------------------------------------------------

    async def _keyword_pass(self) -> int:
        """``captured -> keyword_indexed``: FTS upsert + state transition in
        one store transaction (no model, instant)."""
        items = await self._store.claim_batch(
            ItemState.KEYWORD_INDEXED, limit=KEYWORD_BATCH, now=self._claim_now()
        )
        for item in items:
            try:
                await self._store.mark_stage_done(
                    int(item["id"]),
                    ItemState.KEYWORD_INDEXED,
                    fts_title=str(item.get("title") or ""),
                    fts_body=str(item.get("body_raw") or ""),
                )
                self.processed["keyword"] += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — one poisoned item blocks nothing
                await self._retry(item, "keyword", exc)
        return len(items)

    async def _embed_pass(self) -> int:
        """``keyword_indexed -> embedded``: store the RAW document and its
        vector via the configured backend. An unusable slot claims NO work."""
        backend, model, reason = self._embedding_slot()
        if backend is None:
            self._note_slot_gap(reason)
            return 0
        self._last_slot_reason = None
        items = await self._store.claim_batch(
            ItemState.EMBEDDED, limit=EMBED_BATCH, now=self._claim_now()
        )
        if not items:
            return 0
        texts = [self._raw_text(item) for item in items]
        try:
            vectors = await backend.embed(texts, model=model)
            if len(vectors) != len(texts):
                raise RuntimeError(
                    f"backend returned {len(vectors)} vectors for {len(texts)} texts"
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — batch failure retries every member
            for item in items:
                await self._retry(item, "embed", exc)
            return len(items)
        for item, text, vector in zip(items, texts, vectors, strict=True):
            try:
                doc_id = await self._store.add_document(
                    int(item["id"]),
                    DocType.RAW,
                    text,
                    content_hash=str(item.get("content_hash") or ""),
                )
                await self._store.store_embedding(
                    doc_id, model=model, dim=len(vector), vector=vector
                )
                await self._store.mark_stage_done(int(item["id"]), ItemState.EMBEDDED)
                self.processed["embed"] += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — one poisoned item blocks nothing
                await self._retry(item, "embed", exc)
        return len(items)

    async def _distill_pass(self) -> int:
        """``embedded -> distilled``: cache-first distillation, SUMMARY
        document + summary embedding, result cached for determinism.

        The stage needs the embedding slot too (the summary is embedded), so
        an unusable slot claims no distill work either. Items can only reach
        ``embedded`` while the slot works, so this gate is rarely the limiter.
        """
        backend, model, reason = self._embedding_slot()
        if backend is None:
            self._note_slot_gap(reason)
            return 0
        items = await self._store.claim_batch(
            ItemState.DISTILLED, limit=DISTILL_BATCH, now=self._claim_now()
        )
        if not items:
            return 0
        for item in items:
            try:
                await self._distill_one(item, backend, model)
                self.processed["distill"] += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — one poisoned item blocks nothing
                await self._retry(item, "distill", exc)
        return len(items)

    def _distill_cache_model(self) -> str:
        """The ``model`` component of the distill cache key. When no explicit
        distill model/provider is configured the key-aware chain decides at
        call time, so the honest deterministic key component is ``auto``."""
        ultrawiki = getattr(self._cfg, "ultrawiki", None)
        model = str(getattr(ultrawiki, "distill_model", "") or "").strip()
        provider = str(getattr(ultrawiki, "distill_provider", "") or "").strip()
        return model or provider or "auto"

    async def _distill_one(self, item: dict[str, Any], backend: Any, model: str) -> None:
        from jarvis.ultrawiki.distill import (  # noqa: PLC0415 — lazy (AP-26)
            distill_cache_key,
        )

        title = str(item.get("title") or "")
        body = str(item.get("body_raw") or "")
        content_hash, prompt_version, cache_model = distill_cache_key(
            title=title, body=body, model=self._distill_cache_model()
        )

        fields: dict[str, Any] | None = None
        raw_json = ""
        cached = await self._store.distill_cache_get(
            content_hash, prompt_version, cache_model
        )
        if cached is not None:
            try:
                parsed = json.loads(cached)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                fields = parsed
                raw_json = cached

        fresh = fields is None
        if fields is None:
            source_kind = await self._source_kind(str(item.get("source_id") or ""))
            result = await self._distill_fn(
                self._cfg, title=title, body=body, source_kind=source_kind
            )
            fields = {
                "question": getattr(result, "question", ""),
                "summary": getattr(result, "summary", ""),
                "resolution": getattr(result, "resolution", ""),
                "entities": list(getattr(result, "entities", []) or []),
                "refs": list(getattr(result, "refs", []) or []),
            }
            raw_json = str(getattr(result, "raw_json", "") or "")
            if not raw_json:
                raw_json = json.dumps(
                    fields, ensure_ascii=False, separators=(",", ":")
                )

        text = _summary_text(
            _string_field(fields, "question"),
            _string_field(fields, "summary"),
            _string_field(fields, "resolution"),
            _list_field(fields, "entities"),
        ) or self._raw_text(item)

        doc_id = await self._store.add_document(
            int(item["id"]),
            DocType.SUMMARY,
            text,
            distill_json=raw_json,
            distill_version=prompt_version,
            content_hash=content_hash,
        )
        vectors = await backend.embed([text], model=model)
        if not vectors:
            raise RuntimeError("backend returned no vector for the summary text")
        await self._store.store_embedding(
            doc_id, model=model, dim=len(vectors[0]), vector=vectors[0]
        )
        if fresh:
            await self._store.distill_cache_put(
                content_hash, prompt_version, cache_model, raw_json
            )
        await self._store.mark_stage_done(int(item["id"]), ItemState.DISTILLED)
