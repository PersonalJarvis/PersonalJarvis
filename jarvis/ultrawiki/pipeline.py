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
from datetime import UTC, datetime
from typing import Any

from jarvis.ultrawiki.throughput import ThroughputTracker
from jarvis.ultrawiki.types import DocType, ItemState

log = logging.getLogger(__name__)

__all__ = [
    "KEYWORD_BATCH",
    "EMBED_BATCH",
    "DISTILL_BATCH",
    "EVENTS_BACKFILL_BATCH",
    "EVENTS_BACKFILL_CURSOR",
    "MEDIA_BATCH",
    "MAX_EMBED_CHARS",
    "MAX_MEDIA_BYTES",
    "IDLE_SLEEP_S",
    "BUSY_SLEEP_S",
    "DEFAULT_CPU_SHARE",
    "MIN_CPU_SHARE",
    "MAX_PACING_SLEEP_S",
    "PipelineWorker",
]

#: Per-pass claim sizes (design doc 02 batching).
KEYWORD_BATCH = 200
EMBED_BATCH = 32
DISTILL_BATCH = 4

#: Legacy stores may contain tombstones created before source payloads and
#: derivatives were scrubbed. Repair enough per pass to converge promptly,
#: while keeping every transaction bounded and paced with normal ingest work.
LEGACY_TOMBSTONE_REPAIR_BATCH = 5000

#: Items per pass of the deterministic event backfill. Bigger than the model
#: lanes because it costs no model call, no network and no vector: it reads a
#: distillation that is already on the row and does arithmetic on it. Bounded
#: anyway so a first boot on a large corpus stays a background job.
EVENTS_BACKFILL_BATCH = 100

#: ``uw_meta`` key holding the id the event backfill has walked up to. Carries
#: the extraction version, so bumping ``events.EVENT_VERSION`` re-opens the
#: lane over the whole corpus by itself instead of needing a migration.
EVENTS_BACKFILL_CURSOR = "events_backfill_cursor"

#: How long the embedding-dependent stages sleep after the provider answered
#: with a rate/quota rejection. A depleted quota is GLOBAL: retrying the batch
#: members individually just multiplied one 429 into 33 per pass, thousands of
#: pointless HTTP calls and write transactions per hour (observed live
#: 2026-07-26), starving the read path's event loop in the process.
EMBED_COOLDOWN_S = 600.0

#: How long the media lane rests after a pass that moved nothing.
#:
#: Its backlog query cannot be answered from an index — the pending flag lives
#: inside ``metadata_json`` and SQL narrows on it with a leading-wildcard
#: ``LIKE`` — so every attempt is a full scan of the item table, reading every
#: column of every row it walks. That is affordable once a minute and ruinous
#: ten times a second: measured at 173 ms over a 236 k-row store, which is a
#: saturated core spending its entire day re-reading a corpus to rediscover the
#: same blocked item (observed live 2026-07-27).
#:
#: The lane is explicitly allowed to achieve nothing forever, so resting it is
#: free of consequence — and a pass that DOES move something never reaches the
#: cooldown, which keeps a working backlog draining at full speed.
MEDIA_STALL_COOLDOWN_S = 300.0

#: Provider-answer shapes that mean "stop asking for a while" rather than
#: "this item is poisoned". Authentication, quota, transport and upstream
#: failures are properties of the embedding SLOT, never of the item that
#: happened to be in flight. Only content-specific 4xx responses stay on the
#: per-item retry path.
_GLOBAL_EMBED_FAILURE_MARKERS = (
    "HTTP 401",
    "HTTP 402",
    "HTTP 403",
    "HTTP 408",
    "HTTP 429",
    "HTTP 500",
    "HTTP 502",
    "HTTP 503",
    "HTTP 504",
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "connection error",
    "connection failed",
    "network error",
    "server disconnected",
    "temporarily unavailable",
    "timed out",
    "timeout",
)

#: ...and the words that mark the subset which will NOT clear by waiting. A
#: rate limit ends on its own; an exhausted quota or an unpaid bill ends only
#: when someone adds credit or points the slot at another backend. Both keep
#: retrying — a topped-up account has to resume by itself — but only this one
#: is worth putting in front of the user, because patience is not the fix.
#:
#: Matched against the provider's own failure SLUG
#: (:func:`jarvis.ultrawiki.embeddings._error_code`), never against a provider
#: name or model id (AP-21): any backend with a billing state names it with one
#: of these words, and one without simply never matches.
_QUOTA_WORDS = (
    "quota",
    "billing",
    "credit",
    "payment",
    "insufficient",
    "exhausted",
    "suspended",
)


def _global_embed_failure(exc: BaseException) -> bool:
    """True when an embedding failure belongs to the provider, not the item.

    Checked by class NAME so this module keeps its lazy-import discipline
    (AP-26); the marker strings come from the adapters' honest error texts.
    """
    if type(exc).__name__ != "EmbeddingError":
        return False
    message = str(exc)
    lowered = message.lower()
    return any(
        marker in message or marker.lower() in lowered
        for marker in _GLOBAL_EMBED_FAILURE_MARKERS
    )


def _embed_block_needs_attention(message: str) -> bool:
    """Does this rejection require a human, or will it clear by itself?

    ``HTTP 402`` (payment required) always does. ``HTTP 429`` does only when
    the provider's code slug says the problem is the account rather than the
    pace — otherwise it is an ordinary rate limit and the cooldown handles it.
    """
    lowered = message.lower()
    if any(status in lowered for status in ("http 401", "http 402", "http 403")):
        return True
    return "http 429" in lowered and any(word in lowered for word in _QUOTA_WORDS)

#: Media enrichment claims ONE item per pass, and only when every other stage
#: found nothing to do. Describing a picture costs a model call, and a photo
#: library is tens of thousands of them — so this lane is deliberately the
#: slowest thing in the system rather than something that races the import it
#: is supposed to follow.
MEDIA_BATCH = 1

#: Bytes of one media file read for enrichment. Above this the providers would
#: refuse the upload anyway, and reading it would only cost memory.
MAX_MEDIA_BYTES = 24 * 1024 * 1024

#: Character budget per embedded TEXT — now per passage, not per item.
#:
#: It used to be a content cap: an item was cut here and everything past it
#: never reached the vector space, so a 200 KB file was searchable by its
#: opening paragraph alone. Items are split into passages first
#: (``jarvis.ultrawiki.chunking``), and this only guards the provider limit
#: for a single pathological passage. Raising it does not make more content
#: searchable; the chunker already does that.
MAX_EMBED_CHARS = 8000

#: Loop pacing: quick follow-up while there is work, gentle poll when idle.
#: ``BUSY_SLEEP_S`` is now the FLOOR of the proportional pause, not the pause
#: itself — see :meth:`PipelineWorker.run`.
IDLE_SLEEP_S = 2.0
BUSY_SLEEP_S = 0.1

#: Share of ONE core the ingest lane may occupy, when the config names none.
#:
#: Five percent, because this lane competes with the thing the user is actually
#: doing. Ingestion is background work by definition: nobody is waiting for the
#: 200 000th document to become searchable this second, while everybody notices
#: a machine that has gone sluggish. At this setting a corpus takes longer to
#: come online and the app stays responsive throughout, which is the right way
#: round — and the knob exists for anyone who would rather trade it back.
DEFAULT_CPU_SHARE = 0.05

#: Floor for the same knob. Zero would be indistinguishable from "UltraWiki is
#: broken", and that is a support question, not a setting.
MIN_CPU_SHARE = 0.01

#: Longest proportional pause, so one pathological pass (a huge media file, a
#: provider timing out inside the window) cannot park the lane for an hour.
MAX_PACING_SLEEP_S = 30.0

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


def _utc_now_iso() -> str:
    """Wall-clock UTC stamp for the embed block. Deliberately NOT the
    monotonic clock the cooldown uses: this one is shown to a person, and
    "since 2026-07-26T18:14Z" is what makes a standstill legible."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


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
        #: Sliding-window rate meters over those counters, one per SLOW lane.
        #: Keyword indexing needs no model and never paces anything, so it gets
        #: no meter — the two stages that call out to a model are the two that
        #: decide how long a corpus takes.
        self._rate: dict[str, ThroughputTracker] = {
            "embed": ThroughputTracker(),
            "distill": ThroughputTracker(),
        }
        self._ready_cache: tuple[float, str, bool, str] | None = None
        self._distill_ready_cache: tuple[float, bool, str] | None = None
        #: stage -> the pause reason last logged, so a persistent gap logs once.
        self._pause_reasons: dict[str, str] = {}
        self._source_kind_cache: dict[str, str] = {}
        #: monotonic deadline until which the embedding-dependent stages rest
        #: after a rate/quota rejection (0.0 = no cooldown).
        self._embed_cooldown_until = 0.0
        #: monotonic deadline until which the media lane rests after a pass
        #: that changed nothing, and the id it last handed to enrichment.
        #: Together they are how a stalled lane stops rescanning the corpus —
        #: see :meth:`_media_pass`.
        self._media_cooldown_until = 0.0
        self._media_last_id: int | None = None
        #: ``name:model`` of the embedding slot the stages last resolved. The
        #: identity of a vector space, and therefore of whatever is refusing
        #: to fill it.
        self._embed_slot_key = ""
        #: ``False`` once the deterministic event backfill has drained the
        #: corpus, so the finished lane costs one ``uw_meta`` read per pass.
        self._events_backfill_open = True
        #: The live provider-level embedding block, or ``None`` while vectors
        #: are coming back. Survives the individual cooldown naps: what matters
        #: to a reader is "this has been refused since yesterday evening", not
        #: "the current ten-minute rest has four minutes left".
        self._embed_block: dict[str, Any] | None = None

    # -- public surface ------------------------------------------------------

    def processed_counts(self) -> dict[str, int]:
        """Copy of the per-stage processed counters."""
        return dict(self.processed)

    def throughput(self) -> dict[str, dict[str, Any]]:
        """Measured rate of the two model-bound lanes (see
        :mod:`jarvis.ultrawiki.throughput`).

        The service turns these into an ETA against the live backlog. Reported
        rather than computed here because the backlog is the store's business,
        not the worker's.
        """
        return {name: meter.snapshot() for name, meter in self._rate.items()}

    def stage_pause_reasons(self) -> dict[str, str]:
        """Why each paused stage is paused, ``{}`` when everything runs.

        These sentences already existed — they were logged once per change and
        never left the log file. A summarising lane that has been deliberately
        parked for the duration of an index rebuild is indistinguishable, from
        the outside, from one that is broken: both show ``distill: 0`` forever.
        The difference is this string, so it has to reach the screen.
        """
        return dict(self._pause_reasons)

    def embed_block(self) -> dict[str, Any] | None:
        """The live provider-level embedding block, or ``None``.

        ``reason`` (the provider's honest answer), ``needs_attention`` (waiting
        will not fix it), ``since`` (UTC ISO of the FIRST rejection in this
        streak) and ``rejections`` (how many since).

        The service reads this so ``/api/ultrawiki/status`` can report what the
        worker already knows. Without it the two disagreed in the worst
        possible direction: the embedding SLOT probe is a credential check by
        contract (AP-21), so a key that exists but is out of quota probes
        ``ready`` — and the overview kept saying "still filling up, you do not
        have to wait" over a queue that had not advanced past keyword indexing
        in fifteen hours, with "Nothing needs your attention" underneath
        (observed live 2026-07-27). One concept, known in one layer, never
        carried to the layer that speaks: the drift class of BUG-008 again.

        A copy, so a status reader cannot mutate the worker's state.
        """
        return dict(self._embed_block) if self._embed_block is not None else None

    def _duty_cycle(self) -> float:
        """Share of one core the ingest lane may take, from the config.

        Clamped to a sane band rather than trusted: 0 would stop ingestion
        entirely (a config typo must not silently disable the product), and
        above 1.0 means nothing — the loop is single-threaded either way.
        """
        ultrawiki = getattr(self._cfg, "ultrawiki", None)
        try:
            raw = float(getattr(ultrawiki, "cpu_share", DEFAULT_CPU_SHARE))
        except (TypeError, ValueError):
            return DEFAULT_CPU_SHARE
        return min(1.0, max(MIN_CPU_SHARE, raw))

    async def run(self, cancel_event: asyncio.Event) -> None:
        """The worker loop: run passes until *cancel_event* is set.

        A failed pass is logged and the loop continues; ``CancelledError``
        (hard task cancel) is always re-raised.

        **Pacing is proportional, not fixed.** Indexing a corpus is real work
        — tokenizing 236 k documents into FTS5 and embedding them is not a bug
        to be optimized away — but it is work nobody is waiting for, and a
        fixed 100 ms pause between passes does not bound it: a pass that takes
        900 ms still gets 90 % of a core, which is exactly what it took
        (observed live 2026-07-27, with the whole machine slowed down behind
        it). Sleeping in PROPORTION to how long the pass ran caps the lane at a
        known share of one core no matter how big the corpus, how slow the
        disk, or how fast the CPU — the same guarantee on a Raspberry Pi and on
        an M4, with no per-machine tuning and no platform-specific API.
        """
        log.info(
            "UltraWiki pipeline worker started (cpu_share=%.0f%%)",
            self._duty_cycle() * 100.0,
        )
        try:
            while not cancel_event.is_set():
                started = time.monotonic()
                try:
                    attempted = await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("UltraWiki pipeline pass failed; continuing")
                    attempted = 0
                if attempted:
                    # Worked for `elapsed` -> rest until that is only `duty` of
                    # the window. Read per pass, so changing the knob in the
                    # settings takes effect without a restart.
                    elapsed = max(0.0, time.monotonic() - started)
                    duty = self._duty_cycle()
                    delay = max(BUSY_SLEEP_S, elapsed * (1.0 / duty - 1.0))
                    delay = min(delay, MAX_PACING_SLEEP_S)
                else:
                    delay = IDLE_SLEEP_S
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
        # The event backfill runs only in the gaps too, and unlike the lanes
        # above it terminates: once it has walked the corpus it costs one meta
        # read per pass. Counted as attempted work so pacing sees it — it is
        # cheap, not free, and a large corpus should not spin through it at
        # full speed on a laptop.
        if not attempted:
            attempted += await self._events_backfill_pass()
        # Media enrichment runs only in the gaps. It is the one stage that can
        # be skipped forever without anything breaking, so by default it never
        # takes a turn away from a stage that cannot. "eager" opts out of that
        # deference; it does not raise the batch size.
        if not attempted or self._media_mode() == "eager":
            attempted += await self._media_pass()
        # The word lexicon runs LAST, in whatever gap is left. Like the event
        # backfill it TERMINATES — once the vocabulary is harvested and
        # embedded it costs one cursor read per pass — but building it over a
        # large corpus takes many passes, and placing it above the media lane
        # would starve that lane for the whole build. Word search degrades
        # gracefully while it fills; a photo nobody described stays invisible.
        if not attempted:
            attempted += await self._lexicon_pass()
        await self._promote_pass()
        attempted += await self._legacy_tombstone_repair_pass()
        # Sample AFTER every pass, including passes that achieved nothing: a
        # lane that is refused or paused has to keep feeding its meter, or a
        # standstill would simply stop updating and read as the last healthy
        # rate forever.
        now = time.monotonic()
        for name, meter in self._rate.items():
            meter.sample(now, self.processed.get(name, 0))
        return attempted

    async def _legacy_tombstone_repair_pass(self) -> int:
        """Drain payloads/derivatives left by pre-invariant tombstones."""
        repair = getattr(self._store, "repair_legacy_tombstones", None)
        if repair is None:
            return 0
        try:
            repaired = int(
                await repair(limit=LEGACY_TOMBSTONE_REPAIR_BATCH) or 0
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — maintenance never kills ingestion
            log.warning(
                "UltraWiki legacy tombstone repair failed; retrying next pass",
                exc_info=True,
            )
            return 0
        if repaired:
            log.info(
                "UltraWiki scrubbed %d legacy tombstone payload(s)", repaired
            )
        return repaired

    async def _reembed_is_running(self) -> bool:
        """Is a model switch rebuilding the vector space? Never raises.

        A store without the capability (an older embedded one, a test fake)
        answers ``False``, so every stage keeps its normal behaviour there.
        """
        probe = getattr(self._store, "reembed_is_running", None)
        if probe is None:
            return False
        try:
            return bool(await probe())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — an unreadable pin never pauses a stage
            log.debug("UltraWiki: rebuild probe failed", exc_info=True)
            return False

    async def _reconcile_embedding_space(self, model: str) -> None:
        """Keep the store's vector space in step with the configured model.

        The switch is registered by whoever changes the setting — but only ONE
        of the paths that can change it ever did (the settings route). The
        activation route behind the Normal/Ultra switch, a voice-driven config
        change, a hand-edited ``jarvis.toml`` and a config restored from
        another machine all wrote the new model and left the store pinned to
        the old one. The result was silent and total: every vector rejected,
        every item charged a retry, the whole corpus on its way to the
        dead-letter state, and a surface that still said "still filling up".

        Asking the store once per pass — with the model this pass will really
        use, after the slot resolved it — makes the guarantee independent of
        which door the user walked through. Never raises: an unreconcilable
        store still gets its honest per-item failure below.
        """
        reconcile = getattr(self._store, "reconcile_space", None)
        if reconcile is None:  # an older embedded store, or a test fake
            return
        try:
            verdict = str(await reconcile(model) or "")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — reconciliation never kills the lane
            log.debug("UltraWiki: embedding-space reconciliation failed", exc_info=True)
            return
        if verdict == "started":
            log.warning(
                "UltraWiki: the configured embedding model %r did not match the "
                "stored vector space — a background rebuild was registered. "
                "Semantic search keeps answering from the current vectors until "
                "it completes.",
                model,
            )

    async def _promote_pass(self) -> None:
        """Swap in a finished embedding rebuild (see
        ``UltraStore.promote_pending_space``).

        A model switch builds the new vector space alongside the live one, so
        SOMETHING has to notice when the shadow is complete. This runs after
        every pass and costs one meta read when no rebuild is going on. A store
        without the capability (an older embedded one) simply never promotes.
        """
        promote = getattr(self._store, "promote_pending_space", None)
        if promote is None:
            return
        try:
            await promote()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a failed swap retries next pass
            log.warning(
                "UltraWiki embedding-space promotion failed; the current "
                "vectors keep serving search",
                exc_info=True,
            )

    # -- shared helpers ------------------------------------------------------

    def _claim_now(self) -> datetime | None:
        return self._now_fn() if self._now_fn is not None else None

    async def _retry(self, item: dict[str, Any], stage: str, exc: BaseException) -> None:
        from jarvis.ultrawiki.store import (  # noqa: PLC0415 — lazy (AP-26)
            EmbeddingSpaceMismatch,
        )

        if isinstance(exc, EmbeddingSpaceMismatch):
            # A configuration fault, not a poisoned item. Charging it here cost
            # the item an attempt, and five attempts dead-letter it: a single
            # mis-registered model switch was quietly converting the entire
            # corpus into `failed` at 32 items a pass, one innocent item at a
            # time, while the surface reported a healthy backlog. The lane
            # pauses instead and the backlog waits, intact, for the space to
            # agree again (`_reconcile_embedding_space`).
            self._note_stage_pause(stage, str(exc), key="embed-space-mismatch")
            return
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

    def _note_stage_pause(self, stage: str, reason: str, *, key: str = "") -> None:
        """Log an honest 'stage paused' line once per reason change.

        *key* is what "the same reason" means for deduplication, defaulting to
        the sentence itself. The cooldown reason carries a live countdown, so
        deduplicating on the text logged the identical standstill every time
        the remaining minute ticked over — 2 614 lines of it in one session's
        log, which is precisely how a real, permanent block hides in plain
        sight.
        """
        marker = key or reason
        if self._pause_reasons.get(stage) != marker:
            self._pause_reasons[stage] = marker
            log.info("UltraWiki %s stage paused: %s", stage, reason)

    def _clear_stage_pause(self, stage: str) -> None:
        self._pause_reasons.pop(stage, None)

    def _begin_embed_cooldown(self, exc: BaseException) -> None:
        """One provider-wide failure rests BOTH embedding-dependent stages.

        No attempt is charged and no retry is written: the failure is the
        provider's global state, not any item's fault — the claims were
        read-only, so every item simply becomes claimable again when the
        cooldown ends.
        """
        self._embed_cooldown_until = time.monotonic() + EMBED_COOLDOWN_S
        message = str(exc)
        previous = self._embed_block or {}
        first = bool(not previous)
        self._embed_block = {
            "reason": message,
            "needs_attention": _embed_block_needs_attention(message),
            # The streak's START, never the current nap's — a reader needs to
            # know this has been refused since yesterday, not that the latest
            # ten-minute rest began a moment ago.
            "since": str(previous.get("since") or _utc_now_iso()),
            "rejections": int(previous.get("rejections") or 0) + 1,
            # WHOSE refusal this is. A block belongs to the slot that produced
            # it, and answering "switch your embedding backend" while still
            # reporting the old backend's complaint is worse than saying
            # nothing (see _forget_block_from_another_slot).
            "slot": self._embed_slot_key,
        }
        # Only the FIRST rejection of a streak warns. A permanent block would
        # otherwise write a fresh warning every ten minutes forever, and a log
        # that repeats itself is a log nobody reads.
        if first:
            log.warning(
                "UltraWiki embedding provider failed globally (%s) — resting "
                "the embed and distill stages for %.0f minutes "
                "instead of hammering it per item%s",
                exc,
                EMBED_COOLDOWN_S / 60.0,
                (
                    ". This will not clear by waiting: check that provider's "
                    "credentials or billing, or choose another embedding backend"
                    if self._embed_block["needs_attention"]
                    else ""
                ),
            )

    def _forget_block_from_another_slot(self, slot_key: str) -> None:
        """Drop a block (and its cooldown) once the slot itself has changed.

        The advice a blocked stage gives is "add credit, or switch the
        embedding backend". Taking that advice used to change nothing for up
        to ten minutes: the cooldown was a global nap, and the block was
        remembered without recording WHOSE it was — so a freshly chosen,
        perfectly healthy backend sat out the dead one's rest while the UI
        went on quoting the dead one's complaint (observed live 2026-07-27,
        switching from a depleted key to a local backend).

        A block belongs to the slot that earned it. A different slot has
        earned nothing, and starts clean.
        """
        block = self._embed_block
        if block is None or block.get("slot") == slot_key:
            return
        log.info(
            "UltraWiki embedding slot changed to %s — dropping the standstill "
            "recorded for %s and resuming immediately",
            slot_key or "(none)",
            block.get("slot") or "(unknown)",
        )
        self._embed_block = None
        self._embed_cooldown_until = 0.0
        self._clear_stage_pause("embed")
        self._clear_stage_pause("distill")

    def _note_embed_success(self) -> None:
        """A vector came back — whatever the provider was refusing is over.

        Called on every successful embed call rather than only after a block,
        so a topped-up account or a passing rate limit clears the standstill
        report by itself. Nothing about recovery may require a restart.
        """
        block = self._embed_block
        if block is not None:
            log.info(
                "UltraWiki embedding provider recovered after %d rejection(s) "
                "since %s — the embed and distill stages resume",
                int(block.get("rejections") or 0),
                block.get("since"),
            )
            self._embed_block = None
        self._embed_cooldown_until = 0.0

    def _embed_cooldown_reason(self) -> str:
        """Honest pause reason while the cooldown runs, else ``""``."""
        remaining = self._embed_cooldown_until - time.monotonic()
        if remaining <= 0:
            return ""
        return (
            "embedding provider is temporarily unavailable — retrying "
            f"in {max(1, int(remaining // 60))} minute(s)"
        )

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
        # The identity of the vector space this pass will write into. Recorded
        # before any call, so a rejection can be attributed to the slot that
        # earned it rather than to whatever is configured when it is read.
        self._embed_slot_key = f"{getattr(backend, 'name', '?')}:{model}"
        self._forget_block_from_another_slot(self._embed_slot_key)
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
        """The item's FULL text. No cap — the chunker bounds the passages."""
        title = str(item.get("title") or "")
        body = str(item.get("body_raw") or "")
        text = f"{title}\n\n{body}".strip()
        return text or str(item.get("external_id") or "")

    @classmethod
    def _item_chunks(cls, item: dict[str, Any]) -> list[Any]:
        """The passages of one item, each carrying the item's title.

        Every passage repeats the title: a vector for "line 4 200 of a source
        file" is unretrievable without knowing which file it is from, and the
        title is the cheapest possible carrier of that.
        """
        from jarvis.ultrawiki.chunking import Chunk, chunk_text  # noqa: PLC0415

        title = str(item.get("title") or "").strip()
        body = str(item.get("body_raw") or "")
        pieces = chunk_text(body)
        if not pieces:
            text = cls._cap_embed_input(cls._raw_text(item))
            return [Chunk(index=0, text=text, char_start=0, char_end=len(text))]
        if not title:
            return [
                Chunk(
                    index=c.index,
                    text=cls._cap_embed_input(c.text),
                    char_start=c.char_start,
                    char_end=c.char_end,
                )
                for c in pieces
            ]
        return [
            Chunk(
                index=c.index,
                text=cls._cap_embed_input(f"{title}\n\n{c.text}"),
                char_start=c.char_start,
                char_end=c.char_end,
            )
            for c in pieces
        ]

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
        cooldown = self._embed_cooldown_reason()
        if cooldown:
            # A stable dedup key, because the sentence carries a countdown.
            self._note_stage_pause("embed", cooldown, key="embed-cooldown")
            return 0
        await self._reconcile_embedding_space(model)
        self._clear_stage_pause("embed")
        items = await self._store.claim_batch(
            ItemState.EMBEDDED, limit=EMBED_BATCH, now=self._claim_now()
        )
        if not items:
            return 0
        # One embed input PER PASSAGE, flattened across the claimed items so
        # the batch call stays a batch call. The offsets map each block of
        # vectors back to the item it belongs to.
        per_item = [self._item_chunks(item) for item in items]
        texts = [chunk.text for chunks in per_item for chunk in chunks]
        try:
            vectors = await backend.embed(texts, model=model)
            if len(vectors) != len(texts):
                raise RuntimeError(
                    f"backend returned {len(vectors)} vectors for {len(texts)} texts"
                )
            self._note_embed_success()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — fall back to per-item embedding
            # A rate/quota rejection is GLOBAL, not a poison item: retrying
            # the members individually would multiply one 429 into 33 calls
            # per pass. Rest instead; the read-only claims release themselves.
            if _global_embed_failure(exc):
                self._begin_embed_cooldown(exc)
                return 0
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
            return await self._embed_individually(items, per_item, backend, model)
        offset = 0
        for item, chunks in zip(items, per_item, strict=True):
            block = vectors[offset : offset + len(chunks)]
            offset += len(chunks)
            try:
                await self._store_embedded(item, chunks, block, model)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — one poisoned item blocks nothing
                await self._retry(item, "embed", exc)
        return len(items)

    async def _embed_individually(
        self,
        items: list[dict[str, Any]],
        per_item: list[list[Any]],
        backend: Any,
        model: str,
    ) -> int:
        """Per-item fallback after a failed batch call (see ``_embed_pass``).

        Each item is re-embedded with ITS OWN passages only, so a single
        unembeddable one accrues the attempt while the rest of the batch
        still lands.
        """
        for item, chunks in zip(items, per_item, strict=True):
            try:
                vectors = await backend.embed(
                    [chunk.text for chunk in chunks], model=model
                )
                if len(vectors) != len(chunks):
                    raise RuntimeError(
                        f"backend returned {len(vectors)} vectors for "
                        f"{len(chunks)} passages"
                    )
                self._note_embed_success()
                await self._store_embedded(item, chunks, vectors, model)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — only this item is charged
                if _global_embed_failure(exc):
                    # Mid-loop quota exhaustion: stop charging anyone and
                    # rest — the remaining claims are read-only anyway.
                    self._begin_embed_cooldown(exc)
                    break
                await self._retry(item, "embed", exc)
        return len(items)

    async def _store_embedded(
        self, item: dict[str, Any], chunks: list[Any], vectors: Any, model: str
    ) -> None:
        """RAW passages + one vector each + the guarded ``embedded`` transition.

        Every passage of the item becomes its own document and its own vector,
        which is what makes text beyond the opening paragraph retrievable at
        all. The whole set is swapped atomically, so a reader never sees an
        item holding half its passages.

        When the compare-and-set below finds the item already reset by a
        concurrent content change, the documents just written are stale —
        harmless, because ``replace_documents`` swaps the whole set again when
        the next pass re-embeds the new content.
        """
        doc_ids = await self._store.replace_documents(
            int(item["id"]),
            DocType.RAW,
            chunks,
            content_hash=str(item.get("content_hash") or ""),
        )
        for doc_id, vector in zip(doc_ids, vectors, strict=True):
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
        cooldown = self._embed_cooldown_reason()
        if cooldown:
            # The distilled summary must be embedded too, so this stage rests
            # through the same provider cooldown as the embed pass.
            self._note_stage_pause("distill", cooldown, key="embed-cooldown")
            return 0
        distill_ok, distill_reason = await self._distill_ready()
        if not distill_ok:
            self._note_stage_pause("distill", distill_reason)
            return 0
        if await self._reembed_is_running():
            # A model switch has taken semantic search down until the new space
            # is complete, and distillation cannot shorten that: only the embed
            # stage releases an item from the rebuild. Worse, `begin_reembed`
            # demoted these items, so every summary produced now is produced
            # again after the swap. Measured on a live store, the LLM round
            # trips of this stage were ~90 % of a pass — the rebuild was
            # waiting hours on work that would be thrown away.
            self._note_stage_pause(
                "distill",
                "summaries are paused while the search index is rebuilt on the "
                "new embedding model - they resume by themselves once it is "
                "complete, and nothing is lost meanwhile",
                key="reembed-priority",
            )
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
                if _global_embed_failure(exc):
                    self._begin_embed_cooldown(exc)
                    break
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
                # Prompt version 2. Cached alongside everything else, so a
                # re-run never pays for the events either.
                "events": list(getattr(result, "events", []) or []),
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
        self._note_embed_success()
        await self._store.store_embedding(
            doc_id, model=model, dim=len(vectors[0]), vector=vectors[0]
        )
        if fresh:
            await self._store.distill_cache_put(
                content_hash, prompt_version, cache_model, raw_json
            )
        await self._derive_events(item, fields)
        return bool(
            await self._store.mark_stage_done(
                int(item["id"]), ItemState.DISTILLED, **self._claim_guard(item)
            )
        )

    async def _derive_events(
        self, item: dict[str, Any], fields: dict[str, Any]
    ) -> None:
        """Turn this item's distillation into episodic events (design doc 01).

        Rides the distillation that just ran: **no model call, no network, no
        extra pass**. Purely deterministic work over data already in hand, so
        it costs a few hundred microseconds on the slowest stage of the write
        path and nothing at all on the read path.

        Never fatal. A store without the event tables (a third-party backend,
        a test fake), a derivation that raises, a database that refuses the
        write — all of them leave the item distilled and searchable. Events
        are an accelerator for episodic questions, not a precondition for
        having a memory at all.
        """
        ultrawiki = getattr(self._cfg, "ultrawiki", None)
        if not bool(getattr(ultrawiki, "events_enabled", True)):
            return
        replace = getattr(self._store, "replace_events", None)
        if not callable(replace):
            return
        try:
            from jarvis.ultrawiki.events import (  # noqa: PLC0415 — lazy (AP-26)
                derive_events,
            )

            events = derive_events(
                distill=fields,
                title=str(item.get("title") or ""),
                recorded_at=str(item.get("timestamp_utc") or ""),
            )
            if not events:
                # Still a replace: an item that USED to yield events and no
                # longer does must lose them, or a corrected source leaves the
                # old answer standing. The store answers the common case (an
                # item that never had any) with one indexed lookup and no write
                # transaction, so the empty majority costs almost nothing.
                await replace(int(item["id"]), [])
                return
            await replace(int(item["id"]), events)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — events never block a distillation
            log.warning(
                "event derivation failed for item %s — the item stays "
                "distilled and searchable without episodic rows",
                item.get("id"),
                exc_info=True,
            )

    # -- event backfill over an already-distilled corpus ----------------------

    async def _events_backfill_pass(self) -> int:
        """Derive events for items distilled BEFORE this feature existed.

        The distillation stage only ever claims items that are not yet
        distilled, so a corpus imported earlier would never reach
        :meth:`_derive_events` — its owner would be told the knowledge base
        answers episodic questions while it silently answered none of them.

        This lane closes that gap for exactly ZERO model calls: it re-reads the
        distillation JSON already stored on each item and runs the same pure
        derivation. It walks the corpus once, by id, remembering how far it got
        in ``uw_meta``, and then costs one meta read per pass forever. Every
        capability it needs is probed, so a third-party store or a test fake
        simply has no backfill (and no error).
        """
        if not self._events_backfill_open:
            return 0
        ultrawiki = getattr(self._cfg, "ultrawiki", None)
        if not bool(getattr(ultrawiki, "events_enabled", True)):
            return 0
        reader = getattr(self._store, "items_with_distillation", None)
        get_meta = getattr(self._store, "get_meta", None)
        set_meta = getattr(self._store, "set_meta", None)
        if not callable(reader) or not callable(get_meta) or not callable(set_meta):
            self._events_backfill_open = False
            return 0
        from jarvis.ultrawiki.events import EVENT_VERSION  # noqa: PLC0415 — lazy (AP-26)

        key = f"{EVENTS_BACKFILL_CURSOR}_v{EVENT_VERSION}"
        try:
            raw = await get_meta(key)
            cursor = int(str(raw or "0"))
        except (TypeError, ValueError):
            cursor = 0
        except Exception:  # noqa: BLE001 — a store without the table has no lane
            log.debug("event backfill: cursor unreadable", exc_info=True)
            self._events_backfill_open = False
            return 0
        if cursor < 0:  # the sentinel this lane writes when it is finished
            self._events_backfill_open = False
            return 0
        try:
            items = await reader(after_id=cursor, limit=EVENTS_BACKFILL_BATCH)
        except Exception:  # noqa: BLE001 — never fails a pass
            log.debug("event backfill: read failed", exc_info=True)
            self._events_backfill_open = False
            return 0
        if not items:
            await self._store.set_meta(key, "-1")
            self._events_backfill_open = False
            log.info("UltraWiki: episodic event backfill complete")
            return 0
        for item in items:
            try:
                fields = json.loads(str(item.get("distill_json") or "{}"))
            except ValueError:
                fields = {}
            if isinstance(fields, dict):
                await self._derive_events(item, fields)
            cursor = max(cursor, int(item["id"]))
        await self._store.set_meta(key, str(cursor))
        return len(items)

    # -- word lexicon --------------------------------------------------------

    async def _lexicon_pass(self) -> int:
        """Harvest vocabulary, then embed the terms word search compares against.

        Two bounded steps per pass, harvest first: embedding a term the corpus
        has not been walked for yet would be paying for a word nobody can
        search. Both are resumable — the harvest by a cursor, the embedding by
        "which terms have no vector in this space" — so a restart continues
        instead of starting over, and a finished lexicon costs one cursor read
        per pass.

        Every capability is probed, so a third-party store or a test fake
        simply has no lexicon lane (and no error). An embedding slot that is
        unusable pauses only THIS lane: word search still answers through the
        provider-free co-occurrence path.
        """
        ultrawiki = getattr(self._cfg, "ultrawiki", None)
        if not bool(getattr(ultrawiki, "lexicon_enabled", True)):
            return 0
        if not callable(getattr(self._store, "lexicon_scan_batch", None)):
            return 0
        from jarvis.ultrawiki import lexicon as lexicon_mod  # noqa: PLC0415 — lazy (AP-26)

        try:
            harvested = await lexicon_mod.harvest_pass(
                self._store, limit=lexicon_mod.HARVEST_BATCH
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — an optional lane never kills ingestion
            log.warning(
                "UltraWiki lexicon harvest failed; retrying next pass", exc_info=True
            )
            return 0
        if harvested:
            return harvested

        # Vocabulary is up to date — spend the pass on term vectors instead.
        # A rebuild in flight is left alone: its shadow space is incomplete, so
        # embedding terms into it would pin the lexicon to a geometry no search
        # answers from yet (D-3).
        if await self._reembed_is_running():
            return 0
        try:
            model, dim = await self._store.embedding_space()
        except Exception:  # noqa: BLE001 — an unreadable pin pauses the lane only
            log.debug("UltraWiki lexicon: embedding space unreadable", exc_info=True)
            return 0
        if not model or int(dim) <= 0:
            return 0  # nothing embedded yet; the document lanes go first
        backend, _configured_model, reason = await self._embedding_slot()
        if backend is None:
            self._note_stage_pause("lexicon", reason)
            return 0
        if self._embed_cooldown_reason():
            return 0  # the provider asked everyone to wait; this lane can
        self._clear_stage_pause("lexicon")
        try:
            embedded = await lexicon_mod.embed_terms_pass(
                self._store,
                backend,
                # The store's ACTIVE pin, never the configured model: term
                # vectors have to live in the same space as the passages they
                # will be compared against.
                model=model,
                dim=int(dim),
                limit=lexicon_mod.EMBED_BATCH,
                max_terms=int(getattr(ultrawiki, "lexicon_max_terms", 20000) or 20000),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a provider outage pauses, never kills
            if _global_embed_failure(exc):
                self._begin_embed_cooldown(exc)
            else:
                log.info(
                    "UltraWiki lexicon: term embedding failed (%s); "
                    "retrying next pass",
                    exc,
                )
            return 0
        if embedded:
            log.debug("UltraWiki lexicon: embedded %d term(s)", embedded)
        return embedded

    # -- media enrichment ----------------------------------------------------

    async def _media_pass(self) -> int:
        """Describe one picture, or transcribe one recording. Frugal by design.

        This lane is the only one that may achieve nothing forever: an install
        with no vision-capable provider keeps its photos findable by filename,
        folder and capture date, and drains the backlog the day one appears.
        So every failure here is recorded on the item and the loop moves on —
        nothing retries in a tight circle, and nothing blocks.
        """
        mode = self._media_mode()
        if mode == "off":
            return 0
        # Before the scan, never after: the scan IS the cost here (see
        # MEDIA_STALL_COOLDOWN_S), so a lane that is resting must not pay for
        # the question it has already been answered.
        if time.monotonic() < self._media_cooldown_until:
            return 0
        pending = await self._pending_media(MEDIA_BATCH)
        if not pending:
            self._clear_stage_pause("media")
            # An empty backlog is a stable answer — nothing but an import can
            # change it, and that takes longer than the nap.
            self._media_cooldown_until = time.monotonic() + MEDIA_STALL_COOLDOWN_S
            return 0
        worked = 0
        for item in pending:
            try:
                await self._enrich_one(item)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — one file never stops the lane
                log.warning(
                    "UltraWiki media enrichment failed for item %s: %s",
                    item.get("id"),
                    exc,
                )
                await self._record_media_outcome(
                    item, text="", reason=f"enrichment failed ({type(exc).__name__})"
                )
            worked += 1
        # Did the lane actually MOVE? The backlog is ordered by id, so the same
        # head twice running means enrichment neither described that item nor
        # marked it undescribable — the tight circle this lane's docstring
        # promises never to spin in, and which it nonetheless spun in for a
        # whole day because every pass reported the item as "worked" and asked
        # again 100 ms later. Resting on a repeat is what makes that promise
        # true; a lane that is draining sees a new head every pass and is never
        # slowed down here.
        try:
            head: int | None = int(pending[0]["id"])
        except (KeyError, TypeError, ValueError):
            head = None
        if head is not None and head == self._media_last_id:
            self._media_cooldown_until = time.monotonic() + MEDIA_STALL_COOLDOWN_S
            self._note_stage_pause(
                "media",
                f"item {head} did not move; resting the lane for "
                f"{MEDIA_STALL_COOLDOWN_S / 60.0:.0f} min",
                key="media-stall",
            )
        self._media_last_id = head
        return worked

    def _media_mode(self) -> str:
        """``frugal`` (default), ``eager`` or ``off``, from the config."""
        ultrawiki = getattr(self._cfg, "ultrawiki", None)
        raw = str(getattr(ultrawiki, "media_enrich", "") or "").strip().lower()
        return raw if raw in ("off", "frugal", "eager") else "frugal"

    async def _pending_media(self, limit: int) -> list[dict[str, Any]]:
        """Media items awaiting enrichment; empty when the store predates it."""
        fetch = getattr(self._store, "pending_media_items", None)
        if not callable(fetch):
            return []
        try:
            return await fetch(limit=limit)
        except Exception:  # noqa: BLE001 — a query failure pauses the lane, nothing else
            log.debug("UltraWiki: media backlog query failed", exc_info=True)
            return []

    async def _enrich_one(self, item: dict[str, Any]) -> None:
        from jarvis.ultrawiki import media as media_mod  # noqa: PLC0415 — lazy (AP-26)
        from jarvis.ultrawiki import media_enrich  # noqa: PLC0415 — lazy (AP-26)

        metadata = dict(item.get("metadata") or {})
        kind = str(metadata.get("media_kind") or "")
        reference = media_mod.ref_from_metadata(metadata)
        if reference is None:
            await self._record_media_outcome(
                item,
                text="",
                reason="this item carries no reference back to the original file",
                retryable=False,
            )
            return

        data = await asyncio.to_thread(_read_media_bytes, media_mod, reference)
        if data is None:
            await self._record_media_outcome(
                item,
                text="",
                reason="the original file is no longer where it was imported from",
                retryable=False,
            )
            return

        filename = reference.display_name
        if kind == "image":
            # The cheap gate BEFORE the expensive call: most picture files on a
            # real machine are icons, sprites and cache thumbnails, and each one
            # would still cost a full model call. Permanent, because a 200-byte
            # icon never becomes worth reading.
            skip = media_enrich.skip_reason_for_image(
                self._media_path_for(item, filename), size_bytes=len(data)
            )
            if skip:
                await self._record_media_outcome(
                    item, text="", reason=skip, retryable=False
                )
                return
            result = await media_enrich.describe_image(
                data, filename=filename, cfg=self._cfg
            )
        elif kind in ("audio", "video"):
            # The same gate, which this lane went without. Pictures were
            # filtered by provenance from the day the rule was written; audio
            # was not, and the files the rule was written FROM are recordings:
            # 218 419 wake-word debug clips under data/wake_debug on one live
            # machine, 220 520 items queued for a transcription each
            # (2026-07-27). Whichever lane is missing the gate is the lane that
            # spends the corpus.
            skip = media_enrich.skip_reason_for_recording(
                self._media_path_for(item, filename), size_bytes=len(data)
            )
            if skip:
                await self._record_media_outcome(
                    item, text="", reason=skip, retryable=False
                )
                return
            result = await media_enrich.transcribe_recording(
                data, filename=filename, cfg=self._cfg
            )
        else:
            await self._record_media_outcome(
                item,
                text="",
                reason=f"nothing here knows how to read a {kind or 'file'} of this kind",
                retryable=False,
            )
            return

        if not result.ok:
            self._note_stage_pause("media", result.reason)
            await self._record_media_outcome(
                item, text="", reason=result.reason, retryable=result.retryable
            )
            return
        self._clear_stage_pause("media")
        await self._record_media_outcome(
            item, text=result.text, reason="", provider=result.provider, kind=kind
        )

    @staticmethod
    def _media_path_for(item: dict[str, Any], filename: str) -> str:
        """The path the skip rules judge: the one INSIDE the chosen source.

        Deliberately the ``external_id`` and never the absolute path. An
        absolute path is mostly the machine's own structure - on Windows every
        temp file sits under ``AppData`` - so judging it would skip files the
        user deliberately pointed at. The external id is what the user's own
        folder looks like, which is what "this belongs to a program" must be
        read from.
        """
        return str(item.get("external_id") or "") or filename

    async def _record_media_outcome(
        self,
        item: dict[str, Any],
        *,
        text: str,
        reason: str,
        retryable: bool = True,
        provider: str = "",
        kind: str = "",
    ) -> None:
        """Write the outcome back, through the ordinary upsert path.

        Success APPENDS to the body rather than replacing it: the file facts
        underneath (name, folder, capture date, camera, place) are what makes
        the item findable by time and place, and a description does not
        supersede them. The changed body changes the content hash, which is
        what puts the item back at ``captured`` so the normal ladder embeds
        and distils the new text — no separate re-indexing path to maintain.

        A failure leaves the body untouched and only records why. ``retryable``
        false clears the pending flag so a permanently unreadable file stops
        being picked up; true leaves it set, and the next capable provider
        drains it.
        """
        from jarvis.ultrawiki.types import RawItem  # noqa: PLC0415 — lazy

        metadata = dict(item.get("metadata") or {})
        body = str(item.get("body_raw") or "")
        if not text:
            # A failure changes nothing about the content, and unchanged
            # content makes ``upsert_items`` leave the row completely untouched
            # (its zero-new-work guarantee) — so the note has to be written
            # through the narrow metadata path or it would vanish, and the same
            # file would be retried forever with nothing to show for it.
            metadata["enrich_error"] = reason
            if not retryable:
                metadata["enrich_pending"] = False
            await self._store.set_item_metadata(int(item["id"]), metadata)
            return

        metadata["enrich_pending"] = False
        metadata["enriched_by"] = provider
        metadata.pop("enrich_error", None)
        label = "Transcript" if kind in ("audio", "video") else "Description"
        body = f"{body}\n\n{label}: {text}".strip()

        await self._store.upsert_items(
            str(item.get("source_id") or ""),
            [
                RawItem(
                    external_id=str(item.get("external_id") or ""),
                    body=body,
                    permalink=str(item.get("permalink") or ""),
                    timestamp_utc=str(item.get("timestamp_utc") or ""),
                    title=str(item.get("title") or ""),
                    thread_key=str(item.get("thread_key") or ""),
                    author_raw=str(item.get("author_raw") or ""),
                    metadata=metadata,
                )
            ],
        )


def _read_media_bytes(media_mod: Any, reference: Any) -> bytes | None:
    """Read one media file's bytes in a worker thread. Blocking; never raises."""
    stream = media_mod.open_media(reference)
    if stream is None:
        return None
    try:
        with stream:
            return stream.read(MAX_MEDIA_BYTES)
    except (OSError, RuntimeError, ValueError):
        log.debug("UltraWiki: media file could not be read", exc_info=True)
        return None
