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
  cached so identical input is never paid for twice. This stage is gated on
  BOTH slots it consumes — the embedding backend and a credential-ready chat
  provider — so an install without either pauses instead of dead-lettering
  every item (``store.requeue_failed`` recovers an already-stranded backlog).

Error discipline: a per-item failure goes through ``store.mark_retry`` (60s *
4^n backoff, dead-letter after 5 attempts) and the loop NEVER dies on one item
— it logs and continues. A failed BATCH embed call is not a per-item failure:
its members are retried individually in the same pass so one poisoned text
cannot charge an attempt to 31 healthy ones. ``asyncio.CancelledError`` is
always re-raised so the service's cancel-then-wait shutdown stays honest.

Concurrency: a sync can reset an item to ``captured`` (content changed, derived
rows purged) between a worker's claim and its commit. Every transition is
therefore a compare-and-set against the state AND content hash seen at claim
time; a lost claim writes nothing, charges no attempt, and is simply re-run on
the next pass.

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
    "MAX_EMBED_CHARS",
    "IDLE_SLEEP_S",
    "BUSY_SLEEP_S",
    "PipelineWorker",
]

#: Per-pass claim sizes (design doc 02 batching).
KEYWORD_BATCH = 200
EMBED_BATCH = 32
DISTILL_BATCH = 4

#: Character budget per embedded text, mirroring the distillation truncation
#: in ``jarvis/ultrawiki/distill.py``. An oversized document is a provider
#: 400/413 that would otherwise retry five times and dead-letter; an embedding
#: of the first 8000 characters is a far better answer than none.
MAX_EMBED_CHARS = 8000

#: Loop pacing: quick follow-up while there is work, gentle poll when idle.
IDLE_SLEEP_S = 2.0
BUSY_SLEEP_S = 0.1

#: The embedding ``ready()`` probe may hit the network (Ollama) and the distill
#: probe walks the keyring; cache both verdicts briefly so an idle loop does
#: not hammer a dead endpoint or re-walk the credential chain every 2 seconds.
_READY_PROBE_TTL_S = 30.0

#: Zero-arg factory returning the CONFIGURED embedding backend (an object
#: implementing ``jarvis.ultrawiki.types.EmbeddingBackend``) or ``None`` when
#: the slot is unconfigured — the factory owns the config decision.
EmbeddingBackendFactory = Callable[[], Any]

#: ``distill_fn(cfg, *, title, body, source_kind) -> DistillResult-like``.
DistillFn = Callable[..., Awaitable[Any]]

#: ``() -> (usable, honest_reason_if_not)`` for the distillation slot. Injected
#: whenever ``distill_fn`` is NOT the production chain (an injected distiller
#: brings its own provider, so probing the credential chain would be wrong —
#: and would make the result depend on whatever keys the host happens to have).
DistillReadyFn = Callable[[], tuple[bool, str]]


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
        distill_ready_fn: DistillReadyFn | None = None,
    ) -> None:
        self._store = store
        self._cfg = cfg
        self._backend_factory = embedding_backend_factory
        self._distill_fn = distill_fn
        self._now_fn = now_fn
        self._distill_ready_fn = distill_ready_fn
        #: Per-stage processed counters (successful transitions only).
        self.processed: dict[str, int] = {"keyword": 0, "embed": 0, "distill": 0}
        self._ready_cache: tuple[float, str, bool, str] | None = None
        self._distill_ready_cache: tuple[float, bool, str] | None = None
        #: stage -> the pause reason last logged, so a persistent gap logs once.
        self._pause_reasons: dict[str, str] = {}
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

    def _note_stage_pause(self, stage: str, reason: str) -> None:
        """Log an honest 'stage paused' line once per reason change."""
        if self._pause_reasons.get(stage) != reason:
            self._pause_reasons[stage] = reason
            log.info("UltraWiki %s stage paused: %s", stage, reason)

    def _clear_stage_pause(self, stage: str) -> None:
        self._pause_reasons.pop(stage, None)

    def _note_lost_claim(self, item: dict[str, Any], stage: str) -> None:
        """A concurrent content change invalidated this claim — not an error.

        The item is back at ``captured`` with its derived rows purged, so the
        next pass re-runs the whole ladder against the NEW content. Nothing is
        retried and no attempt is charged.
        """
        log.debug(
            "UltraWiki %s stage: claim on item %s lost to a concurrent content "
            "change; the next pass re-runs it",
            stage,
            item.get("id"),
        )

    async def _embedding_slot(self) -> tuple[Any | None, str, str]:
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
        ok, reason = await self._backend_ready(backend)
        if not ok:
            return None, "", reason
        return backend, model, ""

    async def _backend_ready(self, backend: Any) -> tuple[bool, str]:
        name = str(getattr(backend, "name", ""))
        now = time.monotonic()
        cached = self._ready_cache
        if cached is not None and cached[1] == name and now < cached[0]:
            return cached[2], cached[3]

        def _probe() -> tuple[bool, str]:
            try:
                ok, reason = backend.ready()
            except Exception as exc:  # noqa: BLE001 — ready() must never kill the loop
                return False, (
                    f"embedding readiness probe failed ({type(exc).__name__})"
                )
            return bool(ok), str(reason or "")

        # ready() is SYNCHRONOUS and may block on a socket (Ollama) or the OS
        # keyring — never on the event loop, which also serves voice and chat.
        ok, reason = await asyncio.to_thread(_probe)
        self._ready_cache = (now + _READY_PROBE_TTL_S, name, ok, reason)
        return ok, reason

    async def _distill_ready(self) -> tuple[bool, str]:
        """Is there a credential-ready chat provider for the distill stage?

        Mirrors ``UltraWikiService._distill_slot_status``. Without this gate the
        stage claimed work on an install with no chat credential at all, failed
        every item five times, and dead-lettered the entire corpus into
        ``failed`` — from which nothing ever returned. Now the stage simply
        pauses, honestly and once, and the backlog waits at ``embedded``.
        """
        now = time.monotonic()
        cached = self._distill_ready_cache
        if cached is not None and now < cached[0]:
            return cached[1], cached[2]
        if self._distill_ready_fn is not None:
            try:
                ok, reason = self._distill_ready_fn()
            except Exception as exc:  # noqa: BLE001 — a broken probe pauses, never kills
                ok, reason = False, (
                    f"distill readiness probe failed ({type(exc).__name__})"
                )
            ok, reason = bool(ok), str(reason or "")
        else:
            # Credential probes walk keyring / env / .env — off the loop.
            ok, reason = await asyncio.to_thread(self._probe_distill_chain)
        self._distill_ready_cache = (now + _READY_PROBE_TTL_S, ok, reason)
        return ok, reason

    def _probe_distill_chain(self) -> tuple[bool, str]:
        """Is any chat provider credential-ready? BLOCKING (keyring walk)."""
        try:
            from jarvis.brain.provider_registry import (  # noqa: PLC0415 — lazy
                BrainProviderRegistry,
            )
            from jarvis.memory.wiki.provider_chain import (  # noqa: PLC0415 — lazy
                credential_ready_wiki_providers,
            )

            chain = credential_ready_wiki_providers(
                available=set(BrainProviderRegistry().available()), config=self._cfg
            )
        except Exception as exc:  # noqa: BLE001 — a broken probe pauses, never kills
            return False, f"distill chain probe failed ({type(exc).__name__})"
        if not chain:
            return False, (
                "no credential-ready chat provider is available for "
                "distillation - add any chat provider key (or start a local "
                "one) and the backlog resumes; keyword and semantic search "
                "keep working"
            )
        return True, ""

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
    def _cap_embed_input(text: str) -> str:
        """Trim an embed input to :data:`MAX_EMBED_CHARS` (provider limits)."""
        return text if len(text) <= MAX_EMBED_CHARS else text[:MAX_EMBED_CHARS]

    @classmethod
    def _raw_text(cls, item: dict[str, Any]) -> str:
        title = str(item.get("title") or "")
        body = str(item.get("body_raw") or "")
        text = f"{title}\n\n{body}".strip()
        return cls._cap_embed_input(text or str(item.get("external_id") or ""))

    @staticmethod
    def _claim_guard(item: dict[str, Any]) -> dict[str, Any]:
        """The compare-and-set keys captured at claim time (see
        ``UltraStore.mark_stage_done``)."""
        return {
            "expected_state": item.get("state") or None,
            "expected_content_hash": item.get("content_hash") or None,
        }

    # -- stage passes --------------------------------------------------------

    async def _keyword_pass(self) -> int:
        """``captured -> keyword_indexed``: FTS upsert + state transition in
        one store transaction (no model, instant)."""
        items = await self._store.claim_batch(
            ItemState.KEYWORD_INDEXED, limit=KEYWORD_BATCH, now=self._claim_now()
        )
        for item in items:
            try:
                committed = await self._store.mark_stage_done(
                    int(item["id"]),
                    ItemState.KEYWORD_INDEXED,
                    fts_title=str(item.get("title") or ""),
                    fts_body=str(item.get("body_raw") or ""),
                    **self._claim_guard(item),
                )
                if committed:
                    self.processed["keyword"] += 1
                else:
                    self._note_lost_claim(item, "keyword")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — one poisoned item blocks nothing
                await self._retry(item, "keyword", exc)
        return len(items)

    async def _embed_pass(self) -> int:
        """``keyword_indexed -> embedded``: store the RAW document and its
        vector via the configured backend. An unusable slot claims NO work."""
        backend, model, reason = await self._embedding_slot()
        if backend is None:
            self._note_stage_pause("embed", reason)
            return 0
        self._clear_stage_pause("embed")
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
        except Exception as exc:  # noqa: BLE001 — fall back to per-item embedding
            # ONE unembeddable text (provider 400 on some content) used to
            # charge an attempt to all 32 batch members, so a single poison
            # item dead-lettered a whole healthy batch every five passes.
            # Re-embed each member ALONE in this SAME pass instead: only the
            # genuinely failing item accrues an attempt.
            log.info(
                "UltraWiki embed batch of %d failed (%s) — retrying its members "
                "individually in this pass",
                len(items),
                exc,
            )
            return await self._embed_individually(items, texts, backend, model)
        for item, text, vector in zip(items, texts, vectors, strict=True):
            try:
                await self._store_embedded(item, text, vector, model)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — one poisoned item blocks nothing
                await self._retry(item, "embed", exc)
        return len(items)

    async def _embed_individually(
        self,
        items: list[dict[str, Any]],
        texts: list[str],
        backend: Any,
        model: str,
    ) -> int:
        """Per-item fallback after a failed batch call (see ``_embed_pass``)."""
        for item, text in zip(items, texts, strict=True):
            try:
                vectors = await backend.embed([text], model=model)
                if not vectors:
                    raise RuntimeError("backend returned no vector for the text")
                await self._store_embedded(item, text, vectors[0], model)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — only this item is charged
                await self._retry(item, "embed", exc)
        return len(items)

    async def _store_embedded(
        self, item: dict[str, Any], text: str, vector: Any, model: str
    ) -> None:
        """RAW document + vector + the guarded ``embedded`` transition.

        When the compare-and-set finds the item already reset by a concurrent
        content change, the document just written is stale — harmless, because
        ``add_document`` REPLACES the ``(item_id, doc_type)`` row when the next
        pass re-embeds the new content.
        """
        doc_id = await self._store.add_document(
            int(item["id"]),
            DocType.RAW,
            text,
            content_hash=str(item.get("content_hash") or ""),
        )
        await self._store.store_embedding(
            doc_id, model=model, dim=len(vector), vector=vector
        )
        committed = await self._store.mark_stage_done(
            int(item["id"]), ItemState.EMBEDDED, **self._claim_guard(item)
        )
        if committed:
            self.processed["embed"] += 1
        else:
            self._note_lost_claim(item, "embed")

    async def _distill_pass(self) -> int:
        """``embedded -> distilled``: cache-first distillation, SUMMARY
        document + summary embedding, result cached for determinism.

        TWO gates, because the stage needs TWO slots: the embedding slot (the
        summary is embedded too) AND a credential-ready chat provider for the
        distillation call itself. Either one unusable means the stage claims NO
        work — the backlog waits honestly instead of burning five attempts per
        item and dead-lettering the corpus (a keyless install used to lose
        everything that way).
        """
        backend, model, reason = await self._embedding_slot()
        if backend is None:
            self._note_stage_pause("distill", reason)
            return 0
        distill_ok, distill_reason = await self._distill_ready()
        if not distill_ok:
            self._note_stage_pause("distill", distill_reason)
            return 0
        self._clear_stage_pause("distill")
        items = await self._store.claim_batch(
            ItemState.DISTILLED, limit=DISTILL_BATCH, now=self._claim_now()
        )
        if not items:
            return 0
        for item in items:
            try:
                if await self._distill_one(item, backend, model):
                    self.processed["distill"] += 1
                else:
                    self._note_lost_claim(item, "distill")
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

    async def _distill_one(
        self, item: dict[str, Any], backend: Any, model: str
    ) -> bool:
        """One distillation; ``False`` means the claim was lost (see
        ``_note_lost_claim``)."""
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

        text = self._cap_embed_input(
            _summary_text(
                _string_field(fields, "question"),
                _string_field(fields, "summary"),
                _string_field(fields, "resolution"),
                _list_field(fields, "entities"),
            )
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
        return bool(
            await self._store.mark_stage_done(
                int(item["id"]), ItemState.DISTILLED, **self._claim_guard(item)
            )
        )
