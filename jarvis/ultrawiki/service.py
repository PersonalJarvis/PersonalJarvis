"""UltraWikiService — the one object the web layer talks to.

Lifecycle discipline (AP-26 + the WebServer.stop() teardown contract):

- The constructor is CHEAP: no I/O, no store open, no heavy import. The web
  layer constructs the service eagerly and calls :meth:`ensure_started` off
  the boot critical path.
- :meth:`ensure_started` opens the store (SQLite floor, or Postgres via the
  ``ultrawiki_db_url`` secret when ``cfg.ultrawiki.db_backend`` says so —
  falling back honestly to SQLite when the URL/driver/server is missing) and
  starts the staged pipeline as a NAMED task held on an attribute.
- :meth:`shutdown` follows the cancel-then-``wait_for(2 s)`` discipline for
  the pipeline task and every running sync job, THEN closes the store —
  never the other way around (a loop must not tick against a closed
  connection).

Consent is policy here, not in the store: :meth:`activate` registers the
default local sources with consent PENDING and pulls NOTHING;
:meth:`start_sync` refuses unless the source is explicitly approved AND
UltraWiki is enabled.

Sync jobs run as detached named tasks streaming connector items into
``store.upsert_items`` in chunks with per-chunk sync-state checkpoints; the
module-level job registry mirrors ``jarvis/harness/cu_run_registry.py``
(bounded, in-memory, JSON-safe snapshots, never breaks a job).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from jarvis.ultrawiki.progress import build_progress
from jarvis.ultrawiki.secret_scrub import scrub_item
from jarvis.ultrawiki.types import (
    ConnectorContext,
    ConsentState,
    IncrementalMode,
    PipelineCounts,
    RawItem,
)

log = logging.getLogger(__name__)

__all__ = [
    "SYNC_CHUNK_SIZE",
    "BRIDGE_ADAPTER_PENDING_NOTICE",
    "JOB_ACTIVE_STATUSES",
    "JOB_TERMINAL_STATUSES",
    "PIPELINE_STATES",
    "SYNC_PHASES",
    "SyncAlreadyRunningError",
    "SyncJob",
    "UltraWikiService",
    "active_search_service",
    "clear_jobs",
    "get_active_service",
    "set_active_service",
]

#: The honest pipeline states reported by :meth:`UltraWikiService.status`.
#: Five-layer drift discipline (AP-4): the TypeScript union in
#: ``src/lib/ultrawikiApi.ts`` and the UI labels derive from THIS list.
PIPELINE_STATES: tuple[str, ...] = (
    "waiting_for_sources",
    "idle",
    "processing",
    "paused",
)

#: The phases one sync job walks through, in order. Same five-layer drift
#: discipline as :data:`PIPELINE_STATES`: the TypeScript union in
#: ``src/lib/ultrawikiApi.ts`` and the progress labels derive from THIS list.
SYNC_PHASES: tuple[str, ...] = (
    "queued",
    "reading",
    "importing",
    "reconciling",
    "finished",
)

#: Items per ``upsert_items`` transaction / sync-state checkpoint.
SYNC_CHUNK_SIZE = 200

#: Source freshness runs off the boot path and wakes cheaply to find due work.
#: Connector capabilities own the actual per-source cadence.
FRESHNESS_STARTUP_GRACE_S = 30.0
FRESHNESS_TICK_S = 30.0
FRESHNESS_MAX_CONCURRENT_SYNCS = 2

#: What a plugin-bridge source is told when its integration has no pull
#: adapter yet: the sync really ran, it really imported nothing, and that is
#: not the user's fault or a broken credential.
BRIDGE_ADAPTER_PENDING_NOTICE = (
    "This integration is connected, but its pull adapter is not built yet - "
    "nothing was imported. The source stays approved and will import "
    "automatically once the adapter ships."
)

#: The connector id of the generic integration bridge (its "no adapter yet"
#: state is the only one that produces the notice above).
_BRIDGE_CONNECTOR_ID = "plugin-bridge"

#: Connectors that walk a folder the user chose. A zero-item run on one of
#: these is never self-explanatory: the folder can be empty, hold only file
#: types this connector does not read, or contain nothing but skipped noise -
#: three very different situations that all render as a bare "0".
_FOLDER_CONNECTOR_IDS = frozenset({"local-folder", "obsidian-vault"})


def _empty_folder_notice(source: dict[str, Any]) -> str:
    """Say which folder was read and why it produced nothing."""
    root = str((source.get("config") or {}).get("root") or "").strip()
    where = f" at {root}" if root else ""
    return (
        f"The folder{where} was read successfully, but it held no file this "
        "source can import. Text, documents and code are imported; images, "
        "video, archives and dependency folders are skipped."
    )

def _connector_identity(connector_id: str, integration_id: str) -> tuple[str, str]:
    """``(brand asset key, connector kind)`` for one registered source.

    Derived on every read instead of stored with the source: the roster is a
    product decision that must reach sources registered before it, and a brand
    frozen into the database at creation time is exactly how a card ends up
    showing a logo the app no longer ships. An unknown connector answers with
    empty strings — the UI then draws a monogram, which is the honest rendering
    of "this is not one of ours".
    """
    from jarvis.ultrawiki import connector_catalog  # noqa: PLC0415 — lazy (AP-26)

    if connector_id == _BRIDGE_CONNECTOR_ID:
        spec = connector_catalog.bridge_entry_for(integration_id)
        return (spec.brand if spec else "", "bridge")
    spec = connector_catalog.get_connector(connector_id)
    return (spec.brand, spec.kind) if spec else ("", "")


#: The default local sources :meth:`UltraWikiService.activate` registers —
#: source id == connector id for these singletons.
_DEFAULT_SOURCES: tuple[tuple[str, str], ...] = (
    ("normal-wiki", "Built-in Wiki"),
    ("jarvis-conversations", "Jarvis Conversations"),
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    return _SLUG_RE.sub("-", name.lower()).strip("-") or "area"


def _iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seconds_since(value: Any, now: datetime) -> float | None:
    """Age of an ISO timestamp, or ``None`` when absent/unusable."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        # An unusable persisted timestamp is treated as never synchronized.
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, (now - parsed.astimezone(UTC)).total_seconds())


def _is_due(value: Any, interval_s: Any, now: datetime) -> bool:
    try:
        interval = float(interval_s)
    except (TypeError, ValueError) as exc:
        log.warning("UltraWiki ignored invalid synchronization interval %r: %s", interval_s, exc)
        return False
    if interval <= 0:
        return False
    age = _seconds_since(value, now)
    return age is None or age >= interval


def _counts_dict(counts: PipelineCounts) -> dict[str, int]:
    return {**dataclasses.asdict(counts), "total": counts.total}


def _last_outcome_of(sync_state: Any) -> dict[str, Any] | None:
    """What the last FINISHED sync of a source did, or ``None``.

    ``None`` means "no sync has ever finished for this source" — deliberately
    not a zero-filled block, which would be indistinguishable from an import
    that ran and found nothing. The live job registry is in-memory and empty
    after a restart, so this persisted block is what keeps a card from falling
    back to "Approved / Never synced" for a fully imported source.
    """
    if not isinstance(sync_state, dict):
        return None
    finished_at = sync_state.get("last_outcome_at")
    if not finished_at:
        return None
    return {
        "finished_at": str(finished_at),
        "status": str(sync_state.get("last_outcome_status") or ""),
        "mode": str(sync_state.get("last_outcome_mode") or ""),
        "new": int(sync_state.get("last_new") or 0),
        "changed": int(sync_state.get("last_changed") or 0),
        "unchanged": int(sync_state.get("last_unchanged") or 0),
        "tombstoned": int(sync_state.get("last_tombstoned") or 0),
    }


# ---------------------------------------------------------------------------
# Bounded in-module sync-job registry (mirror of cu_run_registry.py)
# ---------------------------------------------------------------------------

#: Bounded history: the oldest TERMINAL jobs are evicted past this size.
_MAX_JOBS = 100

JOB_TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled"})
JOB_ACTIVE_STATUSES = frozenset({"queued", "running"})


class SyncAlreadyRunningError(RuntimeError):
    """One source already has an active sync job.

    Its own error type (not ``ValueError``) so the REST layer can answer 409
    with the ACTIVE job id instead of guessing from message text. Two
    concurrent syncs of one source would interleave their checkpoint and
    cursor writes and double every upsert.
    """

    def __init__(self, source_id: str, job_id: str) -> None:
        super().__init__(
            f"a sync is already running for source {source_id!r} "
            f"(job {job_id}) - wait for it or cancel it first"
        )
        self.source_id = source_id
        self.job_id = job_id


@dataclass
class SyncJob:
    """One sync run's control-plane view (in-memory, not history)."""

    job_id: str
    source_id: str
    mode: str  # "backfill" | "incremental"
    status: str = "queued"
    #: One of :data:`SYNC_PHASES` — WHAT the job is doing, where ``status``
    #: says whether it is doing anything at all.
    phase: str = "queued"
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    chunks: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    tombstoned: int = 0
    #: Credential files stored WITHOUT their content, and individual secrets
    #: replaced inside otherwise ordinary items. Reported so the protection is
    #: visible: a filter nobody can see is a filter nobody trusts.
    secrets_withheld: int = 0
    secrets_redacted: int = 0
    error: str = ""
    #: The asyncio.Task while the job is active; cleared when it ends.
    task: Any = None

    @property
    def items(self) -> int:
        """Items pulled from the source so far.

        Tombstones are excluded: nothing was PULLED for a row that only
        disappeared at the origin, so counting them would inflate the live
        "imported N so far" number the card shows.
        """
        return self.new + self.changed + self.unchanged

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe public view (never exposes the task)."""
        return {
            "job_id": self.job_id,
            "source_id": self.source_id,
            "mode": self.mode,
            "status": self.status,
            "phase": self.phase,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "chunks": self.chunks,
            "items": self.items,
            "new": self.new,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "tombstoned": self.tombstoned,
            "secrets_withheld": self.secrets_withheld,
            "secrets_redacted": self.secrets_redacted,
            "error": self.error,
        }


_JOBS: OrderedDict[str, SyncJob] = OrderedDict()


def _evict_jobs_if_needed() -> None:
    if len(_JOBS) <= _MAX_JOBS:
        return
    for job_id, job in list(_JOBS.items()):
        if len(_JOBS) <= _MAX_JOBS:
            break
        if job.status in JOB_TERMINAL_STATUSES:
            _JOBS.pop(job_id, None)


def _register_job(job: SyncJob) -> None:
    try:
        _JOBS[job.job_id] = job
        _evict_jobs_if_needed()
    except Exception:  # noqa: BLE001 — the registry must never break a sync
        log.debug("ultrawiki job register failed", exc_info=True)


def _get_job(job_id: str) -> SyncJob | None:
    return _JOBS.get(job_id)


def _active_job_for(source_id: str) -> SyncJob | None:
    """The active (queued/running) job of *source_id*, if any."""
    for job in _JOBS.values():
        if job.source_id == source_id and job.status in JOB_ACTIVE_STATUSES:
            return job
    return None


def _list_job_snapshots(limit: int = 20) -> list[dict[str, Any]]:
    jobs = list(_JOBS.values())
    jobs.sort(key=lambda j: j.started_at, reverse=True)
    return [j.snapshot() for j in jobs[: max(1, int(limit))]]


def _cancel_job(job_id: str) -> bool:
    """Cancel ONE active job. False when unknown or already terminal."""
    job = _JOBS.get(job_id)
    if job is None or job.status in JOB_TERMINAL_STATUSES or job.task is None:
        return False
    try:
        job.task.cancel()
    except Exception:  # noqa: BLE001 — a broken task must not 500 the API
        log.debug("ultrawiki job cancel failed", exc_info=True)
        return False
    return True


def clear_jobs() -> None:
    """Test/teardown helper — wipes the job registry."""
    _JOBS.clear()


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


class UltraWikiService:
    """Facade over store + pipeline + connectors for the REST/CLI surface.

    ``embedding_backend_factory`` and ``distill_fn`` are injectable test
    seams; production leaves them ``None`` (the configured backend registry
    and ``distill_text`` are resolved lazily).
    """

    def __init__(
        self,
        cfg: Any,
        bus: Any = None,
        *,
        embedding_backend_factory: Callable[[], Any] | None = None,
        distill_fn: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._cfg = cfg
        self._bus = bus
        self._store: Any = None
        self._backend_in_use = ""
        self._pipeline: Any = None
        self._pipeline_task: asyncio.Task[None] | None = None
        self._freshness_task: asyncio.Task[None] | None = None
        self._cancel_event: asyncio.Event | None = None
        self._start_lock = asyncio.Lock()
        self._sync_tasks: dict[str, asyncio.Task[None]] = {}
        self._degradations: list[str] = []
        self._embedding_backend_factory = (
            embedding_backend_factory or self._default_backend_factory
        )
        self._injected_distill_fn = distill_fn

    # -- config helpers ------------------------------------------------------

    def _uw_cfg(self) -> Any:
        return getattr(self._cfg, "ultrawiki", None)

    def _uw_enabled(self) -> bool:
        return bool(getattr(self._uw_cfg(), "enabled", False))

    def _note_degradation(self, reason: str) -> None:
        if reason not in self._degradations:
            self._degradations.append(reason)
            log.warning("UltraWiki degraded: %s", reason)

    # -- default provider seams ---------------------------------------------

    def _default_backend_factory(self) -> Any:
        """The configured embedding backend, or ``None`` (unconfigured slot)."""
        provider = str(getattr(self._uw_cfg(), "embedding_provider", "") or "").strip()
        if not provider:
            return None
        from jarvis.ultrawiki.embeddings import (  # noqa: PLC0415 — lazy (AP-26)
            EMBEDDING_BACKENDS,
        )

        factory = EMBEDDING_BACKENDS.get(provider)
        if factory is None:
            log.warning(
                "UltraWiki: unknown embedding provider %r - the embed stage "
                "stays paused",
                provider,
            )
            return None
        return factory(self._cfg)

    async def _call_distill(
        self, cfg: Any, *, title: str, body: str, source_kind: str
    ) -> Any:
        if self._injected_distill_fn is not None:
            return await self._injected_distill_fn(
                cfg, title=title, body=body, source_kind=source_kind
            )
        from jarvis.ultrawiki.distill import distill_text  # noqa: PLC0415 — lazy

        return await distill_text(cfg, title=title, body=body, source_kind=source_kind)

    # -- lifecycle -----------------------------------------------------------

    @staticmethod
    def _register_pull_adapters() -> None:
        """Wire the built-in integration readers into the plugin bridge."""
        try:
            from jarvis.ultrawiki.adapters import (  # noqa: PLC0415 — lazy (AP-26)
                register_builtin_adapters,
            )

            register_builtin_adapters()
        except Exception as exc:  # noqa: BLE001 — no reader is not a boot failure
            log.warning("UltraWiki: pull adapters could not register: %s", exc)

    async def ensure_started(self, *, pipeline_grace_s: float = 0.0) -> None:
        """Open the store and start the pipeline (idempotent, off boot path).

        The pipeline only starts while ``cfg.ultrawiki.enabled`` is true; a
        later enable is picked up by the next ``ensure_started`` call.

        ``pipeline_grace_s`` holds the WORKER back for that many seconds while
        the store still opens immediately. The two have opposite cost profiles:
        opening the store is cheap and is what every read, search and recall
        needs at once, whereas the worker chews through the whole backlog and
        was measured holding ~1.3 CPU cores indefinitely. Starting both at boot
        made the backlog compete with the app's own startup for the machine.
        Callers on the boot path pass a grace period; an in-app enable passes
        none, because there the user is waiting for the work to begin.
        """
        async with self._start_lock:
            if self._store is None:
                self._store = await self._open_store()
                await self._heal_bridge_labels(self._store)
                # The readers behind the plugin bridge. Registered HERE rather
                # than at import time: an adapter reaches for credentials and
                # HTTP clients, and the bridge must stay importable on a
                # machine that has neither (AP-26). Idempotent and never
                # raises — a broken adapter shortens the list, nothing more.
                self._register_pull_adapters()
            if self._uw_enabled() and self._pipeline_task is None:
                from jarvis.ultrawiki.pipeline import (  # noqa: PLC0415 — lazy
                    PipelineWorker,
                )

                self._cancel_event = asyncio.Event()
                self._pipeline = PipelineWorker(
                    self._store,
                    self._cfg,
                    embedding_backend_factory=self._embedding_backend_factory,
                    distill_fn=self._call_distill,
                    # An injected distiller brings its own provider, so the
                    # credential-chain gate would be both wrong and dependent
                    # on whatever keys the host happens to hold.
                    distill_ready_fn=(
                        (lambda: (True, ""))
                        if self._injected_distill_fn is not None
                        else None
                    ),
                )
                self._pipeline_task = asyncio.create_task(
                    self._run_pipeline_after_grace(
                        self._pipeline, self._cancel_event, pipeline_grace_s
                    ),
                    name="ultrawiki-pipeline",
                )
            if (
                self._uw_enabled()
                and self._cancel_event is not None
                and self._freshness_task is None
            ):
                freshness_grace = max(
                    FRESHNESS_STARTUP_GRACE_S, float(pipeline_grace_s or 0.0)
                )
                self._freshness_task = asyncio.create_task(
                    self._run_freshness_after_grace(
                        self._cancel_event, freshness_grace
                    ),
                    name="ultrawiki-source-freshness",
                )

    @staticmethod
    async def _run_pipeline_after_grace(
        pipeline: Any, cancel_event: asyncio.Event, grace_s: float
    ) -> None:
        """Wait out *grace_s*, then run the worker until cancelled.

        The wait is a cancellable wait ON the shutdown event, never a blind
        sleep: a plain sleep would keep ``stop()`` blocked for the rest of the
        grace period, turning a startup optimisation into a shutdown stall.
        A shutdown during the grace period therefore skips the run entirely.
        """
        if grace_s > 0:
            try:
                await asyncio.wait_for(cancel_event.wait(), timeout=grace_s)
            except TimeoutError:
                pass  # grace elapsed, nothing asked us to stop -> start working
            if cancel_event.is_set():
                return
        await pipeline.run(cancel_event)

    async def _run_freshness_after_grace(
        self, cancel_event: asyncio.Event, grace_s: float
    ) -> None:
        """Schedule due source reads without entering the boot critical path."""
        try:
            await asyncio.wait_for(cancel_event.wait(), timeout=max(0.0, grace_s))
        except TimeoutError:
            # The timeout is the expected signal that the startup grace elapsed.
            pass
        if cancel_event.is_set():
            return
        while not cancel_event.is_set():
            try:
                await self._sync_due_sources()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one scheduler tick cannot kill freshness
                log.warning("UltraWiki source freshness tick failed", exc_info=True)
            try:
                await asyncio.wait_for(
                    cancel_event.wait(), timeout=FRESHNESS_TICK_S
                )
            except TimeoutError:
                # The timeout is the expected signal for the next scheduler tick.
                pass

    async def _sync_due_sources(
        self, *, now: datetime | None = None
    ) -> list[str]:
        """Start due connector jobs and return their ids.

        The helper is intentionally a single scheduler for every connector.
        Capability declarations choose cadence and mode; connector code never
        owns timers. Recent failed attempts count as attempts too, preventing a
        broken remote source from being hammered every scheduler tick.
        """
        if not self._uw_enabled() or self._store is None:
            return []
        instant = (now or datetime.now(UTC)).astimezone(UTC)
        store = self._require_store()
        sources = await store.list_sources()
        active_count = sum(
            1 for job in _JOBS.values() if job.status in JOB_ACTIVE_STATUSES
        )
        started: list[str] = []
        for source in sources:
            if active_count >= FRESHNESS_MAX_CONCURRENT_SYNCS:
                break
            source_id = str(source.get("id") or "")
            if (
                not source_id
                or source.get("consent") != ConsentState.APPROVED.value
                or not source.get("enabled", False)
                or _active_job_for(source_id) is not None
            ):
                continue
            try:
                connector = self._build_connector(
                    str(source.get("connector") or "")
                )
            except ValueError as exc:
                log.warning(
                    "UltraWiki freshness skipped source %s: %s", source_id, exc
                )
                continue
            capabilities = getattr(connector, "capabilities", None)
            refresh_s = getattr(capabilities, "refresh_interval_s", None)
            reconcile_s = getattr(capabilities, "reconcile_interval_s", None)
            if refresh_s is None and reconcile_s is None:
                continue
            sync_state = await store.get_sync_state(source_id) or {}
            last_success = sync_state.get("last_success_at") or source.get(
                "last_sync_at"
            )
            refresh_due = _is_due(last_success, refresh_s, instant)
            reconcile_due = _is_due(
                sync_state.get("backfill_complete_at"), reconcile_s, instant
            )
            if not refresh_due and not reconcile_due:
                continue
            intervals = [
                float(value)
                for value in (refresh_s, reconcile_s)
                if value is not None and float(value) > 0
            ]
            retry_interval = min(intervals) if intervals else FRESHNESS_TICK_S
            if not _is_due(
                sync_state.get("last_outcome_at"), retry_interval, instant
            ):
                continue
            mode = getattr(
                capabilities, "incremental", IncrementalMode.NONE
            )
            full = reconcile_due or mode == IncrementalMode.NONE
            try:
                job_id = await self.start_sync(source_id, full=full)
            except (SyncAlreadyRunningError, ValueError) as exc:
                log.info(
                    "UltraWiki freshness could not start source %s: %s",
                    source_id,
                    exc,
                )
                continue
            started.append(job_id)
            active_count += 1
        return started

    async def _open_store(self) -> Any:
        from jarvis.ultrawiki import store as store_mod  # noqa: PLC0415 — lazy

        backend = str(getattr(self._uw_cfg(), "db_backend", "sqlite") or "sqlite")
        if backend.strip().lower() == "postgres":
            from jarvis.core.config import get_secret  # noqa: PLC0415 — lazy

            # The secret chain walks the OS keyring / .env — never on the loop.
            conn_str = await asyncio.to_thread(get_secret, "ultrawiki_db_url")
            if not conn_str:
                self._note_degradation(
                    "the Postgres backend is configured but no "
                    "'ultrawiki_db_url' connection string is saved - falling "
                    "back to the local SQLite store"
                )
            else:
                pg_store = store_mod.PostgresStore(conn_str)
                try:
                    await pg_store.open()
                except Exception as exc:  # noqa: BLE001 — degrade, never brick
                    # psycopg echoes the DSN (password included) in several
                    # failure modes and this line is shown in /status.
                    self._note_degradation(
                        "the Postgres store is unavailable "
                        f"({store_mod.sanitize_conn_error(exc, conn_str)}) - "
                        "falling back to the local SQLite store"
                    )
                else:
                    self._backend_in_use = "postgres"
                    return pg_store
        data_dir = getattr(getattr(self._cfg, "memory", None), "data_dir", None)
        sqlite_store = store_mod.UltraStore(
            store_mod.resolve_ultrawiki_db_path(data_dir)
        )
        await sqlite_store.open()
        self._backend_in_use = "sqlite"
        return sqlite_store

    async def shutdown(self) -> None:
        """Cancel pipeline + sync tasks (cancel, then ``wait_for(2 s)``),
        THEN close the store."""
        async with self._start_lock:
            if self._cancel_event is not None:
                self._cancel_event.set()
            freshness_task = self._freshness_task
            if freshness_task is not None:
                freshness_task.cancel()
                try:
                    await asyncio.wait_for(freshness_task, timeout=2.0)
                except asyncio.CancelledError:
                    # Cancellation is the expected completion path during shutdown.
                    pass
                except TimeoutError:
                    log.warning("UltraWiki freshness task did not stop within 2 seconds")
                except Exception as exc:  # noqa: BLE001 — shutdown best-effort
                    log.warning("UltraWiki freshness shutdown error: %s", exc)
                self._freshness_task = None
            task = self._pipeline_task
            if task is not None:
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=2.0)
                except (TimeoutError, asyncio.CancelledError):
                    pass
                except Exception as exc:  # noqa: BLE001 — shutdown best-effort
                    log.warning("UltraWiki pipeline shutdown error: %s", exc)
                self._pipeline_task = None
            for job_id, sync_task in list(self._sync_tasks.items()):
                sync_task.cancel()
                try:
                    await asyncio.wait_for(sync_task, timeout=2.0)
                except (TimeoutError, asyncio.CancelledError):
                    pass
                except Exception as exc:  # noqa: BLE001 — shutdown best-effort
                    log.warning("UltraWiki sync %s shutdown error: %s", job_id, exc)
            self._sync_tasks.clear()
            if self._store is not None:
                try:
                    await self._store.close()
                except Exception as exc:  # noqa: BLE001 — shutdown best-effort
                    log.warning("UltraWiki store close failed: %s", exc)
                self._store = None
            self._pipeline = None
            self._cancel_event = None
            self._backend_in_use = ""

    def _require_store(self) -> Any:
        if self._store is None:
            raise RuntimeError(
                "UltraWiki is not started - call ensure_started() first"
            )
        return self._store

    # -- status --------------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        """Honest capability + backlog report for the settings surface."""
        started = self._store is not None
        counts = PipelineCounts()
        sources: list[dict[str, Any]] = []
        vector: dict[str, Any] = {"ready": False, "reason": "store not started"}
        reembed: dict[str, Any] = {}
        if started:
            try:
                counts = await self._store.counts()
                sources = [
                    self._source_summary(row)
                    for row in await self._store.list_sources()
                ]
            except Exception as exc:  # noqa: BLE001 — status never raises
                self._note_degradation(f"store status query failed ({exc})")
            try:
                vec_ok, vec_reason = await self._store.vector_status()
                vector = {"ready": bool(vec_ok), "reason": vec_reason}
            except Exception as exc:  # noqa: BLE001 — status never raises
                vector = {"ready": False, "reason": f"vector probe failed ({exc})"}
            # A model switch rebuilds the vector space in the background while
            # search keeps answering from the old one. Without this the UI
            # would show a fully-green knowledge base and no hint that half the
            # corpus is still being re-embedded.
            status_fn = getattr(self._store, "reembed_status", None)
            if status_fn is not None:
                try:
                    reembed = dict(await status_fn())
                except Exception as exc:  # noqa: BLE001 — status never raises
                    self._note_degradation(f"re-embed status query failed ({exc})")
        pipeline_running = (
            self._pipeline_task is not None and not self._pipeline_task.done()
        )
        processed = (
            self._pipeline.processed_counts() if self._pipeline is not None else {}
        )
        # Every slot probe walks credentials (keyring / .env) or an endpoint —
        # all three go to a worker thread together, never on the event loop.
        slots = await asyncio.to_thread(self._slot_statuses)
        # What the WORKER has actually been told by the provider. The slot
        # probe above is a credential check by contract (AP-21), so it cannot
        # see a key that exists and is refused; only the worker can, and
        # without carrying it here the status route reported a busy pipeline
        # over a lane that had been dead for fifteen hours.
        embed_block = (
            self._pipeline.embed_block() if self._pipeline is not None else None
        )
        if embed_block:
            embedding_slot = dict(slots.get("embedding") or {})
            slots["embedding"] = {
                **embedding_slot,
                "ready": False,
                "reason": str(embed_block.get("reason") or "")
                or str(embedding_slot.get("reason") or ""),
                "blocked_since": embed_block.get("since"),
                "needs_attention": bool(embed_block.get("needs_attention")),
            }
        # ONE derivation of "how far along" for every surface that asks (the
        # strip, the checklist, the overview). See jarvis/ultrawiki/progress.py
        # for the screenshot that made a second derivation unacceptable.
        counts_payload = _counts_dict(counts)
        progress = build_progress(counts_payload)
        state, reason = self._pipeline_state(
            progress, sources, slots, running=pipeline_running, embed_block=embed_block
        )
        # How long the backlog on screen actually takes, measured from the
        # worker's own completed transitions. See jarvis/ultrawiki/throughput.py
        # for the five-hour session that made an un-timed backlog unacceptable.
        pause_reasons = (
            self._pipeline.stage_pause_reasons() if self._pipeline is not None else {}
        )
        throughput = self._throughput(progress, pause_reasons)
        return {
            "enabled": self._uw_enabled(),
            "started": started,
            "backend": {
                "configured": str(
                    getattr(self._uw_cfg(), "db_backend", "sqlite") or "sqlite"
                ),
                "in_use": self._backend_in_use,
            },
            "slots": slots,
            "vector": vector,
            # Empty unless an embedding-model switch is rebuilding right now:
            # {model, active_model, done, total} over DOCUMENTS.
            "reembed": reembed,
            "counts": counts_payload,
            "progress": progress,
            "pipeline": {
                "running": pipeline_running,
                "state": state,
                "reason": reason,
                "processed": processed,
                # Shipped separately from the sentence so a surface can style a
                # standstill that needs the user differently from one that
                # clears itself, without parsing prose.
                "blocked": bool(embed_block),
                "blocked_needs_attention": bool(
                    (embed_block or {}).get("needs_attention")
                ),
                "blocked_since": (embed_block or {}).get("since"),
                # Why a stage is standing still, when it is. A deliberately
                # parked lane and a broken one both read as "0 processed"
                # without this.
                "paused": pause_reasons,
            },
            # Measured rate + resulting ETA per lane; see _throughput.
            "throughput": throughput,
            "sources": sources,
            "jobs": _list_job_snapshots(10),
            "degradations": list(self._degradations),
        }

    def _slot_statuses(self) -> dict[str, Any]:
        """The three capability-slot reports. BLOCKING (credential probes) —
        only ever called through ``asyncio.to_thread``."""
        return {
            "embedding": self._embedding_slot_status(),
            "distill": self._distill_slot_status(),
            "rerank": self._rerank_slot_status(),
        }

    def _throughput(
        self, progress: dict[str, Any], pause_reasons: dict[str, str]
    ) -> dict[str, Any]:
        """Measured rate + ETA for the two model-bound lanes. Never raises.

        The backlogs are derived from the ONE progress model rather than
        recounted here (progress.py exists because a second derivation always
        drifts):

        * **embedding** — everything not yet past the embed stage, i.e. the
          ``captured`` and ``keyword_indexed`` buckets. A rebuild's demoted
          items are already sitting in ``keyword_indexed``, so they are counted
          once, here, and not added again.
        * **summarising** — everything that has not reached ``distilled``,
          minus the dead-lettered items, because those will not arrive there.

        A worker that has not started yet reports empty snapshots, which
        :func:`~jarvis.ultrawiki.throughput.build_throughput` renders as "not
        measured yet" rather than as zero.
        """
        try:
            buckets = dict(progress.get("buckets") or {})
            captured = int(buckets.get("captured") or 0)
            keyword_indexed = int(buckets.get("keyword_indexed") or 0)
            embedded = int(buckets.get("embedded") or 0)
            meters = (
                self._pipeline.throughput() if self._pipeline is not None else {}
            )
            from jarvis.ultrawiki.throughput import (  # noqa: PLC0415 — lazy (AP-26)
                build_throughput,
            )

            return build_throughput(
                embed=dict(meters.get("embed") or {}),
                distill=dict(meters.get("distill") or {}),
                embed_backlog=captured + keyword_indexed,
                distill_backlog=captured + keyword_indexed + embedded,
                distill_paused_reason=str(pause_reasons.get("distill") or ""),
            )
        except Exception as exc:  # noqa: BLE001 — status never raises
            log.debug("UltraWiki throughput assembly failed: %s", exc, exc_info=True)
            return {}

    def _pipeline_state(
        self,
        progress: dict[str, Any],
        sources: list[dict[str, Any]],
        slots: dict[str, Any],
        *,
        running: bool,
        embed_block: dict[str, Any] | None = None,
    ) -> tuple[str, str]:
        """``(state, honest English reason)`` for the import-progress strip.

        "Pipeline running" on a fresh activation with zero approved sources
        read as "something is already pulling my data" — the exact opposite of
        the consent contract. A live worker LOOP is not the same thing as work
        being done, so the strip states which of the five situations holds:
        nothing approved yet, nothing left to do, actually processing, blocked
        on a slot, or — *embed_block* — refused by a provider that answers but
        will not serve. ``running`` is still reported separately.

        The last one has to be passed in because it cannot be derived here: a
        slot probe is a credential check by contract (AP-21) and reports a
        depleted key as ``ready``. Only the worker has been told otherwise.

        Takes the shared progress model rather than raw counts: the backlog
        this function reports and the backlog the checklist reports must be
        the same number, and the only way to guarantee that is to have one
        function produce it (:mod:`jarvis.ultrawiki.progress`).
        """
        approved = [
            source
            for source in sources
            if str(source.get("consent") or "") == ConsentState.APPROVED.value
            and bool(source.get("enabled"))
        ]
        waiting = dict(progress.get("waiting_by_bucket") or {})
        pending_keyword = int(waiting.get("captured") or 0)
        pending_embed = int(waiting.get("keyword_indexed") or 0)
        pending_distill = int(waiting.get("embedded") or 0)
        backlog = int(progress.get("waiting") or 0)
        failed = int(progress.get("failed") or 0)
        failed_note = (
            f" {failed} item(s) gave up after repeated errors — use "
            "'retry failed items' once the cause is fixed."
            if failed
            else ""
        )

        if not approved:
            return "waiting_for_sources", (
                "No source is approved yet, so nothing is being read. Approve a "
                "source under Sources and start a sync — until then this store "
                "stays empty by design." + failed_note
            )
        if backlog == 0:
            return "idle", (
                "Everything ingested so far is fully processed; the pipeline is "
                "waiting for new or changed items." + failed_note
            )

        embed_ready = bool((slots.get("embedding") or {}).get("ready"))
        embed_reason = str((slots.get("embedding") or {}).get("reason") or "")
        distill_ready = bool((slots.get("distill") or {}).get("ready"))
        distill_reason = str((slots.get("distill") or {}).get("reason") or "")

        # A provider that is REFUSING outranks every other reading of the
        # queue, including a keyword lane that is still moving. The lane that
        # is standing still is the one carrying summaries and semantic search,
        # and reporting "processing" because the free stage still ticks is how
        # a fifteen-hour standstill kept rendering as a healthy import with
        # "Nothing needs your attention" underneath it.
        if embed_block and (pending_embed or pending_distill):
            still_running = (
                f" Keyword indexing is unaffected and still running "
                f"({pending_keyword} item(s) to go), so new items keep "
                "becoming findable by exact words."
                if pending_keyword
                else ""
            )
            advice = (
                " Waiting will not clear this: add credit to that provider, or "
                "switch the embedding backend in the UltraWiki settings."
                if embed_block.get("needs_attention")
                else " The pipeline retries on its own."
            )
            return "paused", (
                f"{pending_embed + pending_distill} item(s) are waiting for the "
                "embedding stage, which the provider is refusing: "
                f"{embed_block.get('reason') or embed_reason}."
                + advice
                + still_running
                + failed_note
            )

        if pending_keyword > 0:
            blocked = ""
            if pending_embed and not embed_ready:
                blocked = f" The embedding stage is paused: {embed_reason}"
            elif pending_distill and not distill_ready:
                blocked = f" The distillation stage is paused: {distill_reason}"
            state, reason = "processing", (
                f"{backlog} item(s) are queued for processing." + blocked + failed_note
            )
        elif pending_embed > 0 and not embed_ready:
            state, reason = "paused", (
                f"{pending_embed} item(s) are keyword-searchable and waiting for "
                f"the embedding stage: {embed_reason}" + failed_note
            )
        elif pending_distill > 0 and pending_embed == 0 and not distill_ready:
            state, reason = "paused", (
                f"{pending_distill} item(s) are embedded and waiting for the "
                f"distillation stage: {distill_reason}" + failed_note
            )
        else:
            state, reason = "processing", (
                f"{backlog} item(s) are queued for processing." + failed_note
            )

        if state == "processing" and not running:
            return "paused", (
                f"{backlog} item(s) are queued, but the ingest pipeline is not "
                "running — UltraWiki is off or still starting." + failed_note
            )
        return state, reason

    @staticmethod
    def _source_summary(row: dict[str, Any]) -> dict[str, Any]:
        """One source's card payload — including whether anything is happening.

        The three fields that make the difference between "Approved / Never
        synced" and a card a user can read: ``active_job`` (a run in flight,
        with its phase and the items pulled so far), ``last_outcome`` (what the
        last finished run did, restart-proof), and ``last_notice`` (it ran, it
        worked, it had nothing to import — distinct from ``last_error``).
        """
        counts = row.get("counts")
        source_id = str(row.get("id") or "")
        active = _active_job_for(source_id)
        config = row.get("config") or {}
        connector_id = str(row.get("connector") or "")
        integration_id = (
            str(config.get("integration_id") or "") if isinstance(config, dict) else ""
        )
        brand, connector_kind = _connector_identity(connector_id, integration_id)
        editable_config: dict[str, Any] = {}
        if connector_id in _FOLDER_CONNECTOR_IDS and isinstance(config, dict):
            editable_config = {
                "root": str(config.get("root") or ""),
                "exclude": [str(value) for value in config.get("exclude") or []],
            }
        return {
            "id": row.get("id"),
            "connector": connector_id,
            "label": row.get("label"),
            # The integration behind a plugin-bridge source, so the card can
            # name it instead of showing an opaque generated source id.
            "integration_id": integration_id,
            # Brand identity of an EXISTING source, so its card carries the
            # same logo the picker offered it under. Derived, never stored: a
            # roster change must reach sources registered before it.
            "brand": brand,
            "connector_kind": connector_kind,
            "consent": row.get("consent"),
            "enabled": row.get("enabled"),
            "areas": row.get("areas", []),
            # Only folder settings are returned: they contain no credential,
            # and the Sources view needs them to repair a path or exclusion.
            "config": editable_config,
            "counts": _counts_dict(counts)
            if isinstance(counts, PipelineCounts)
            else counts,
            "sync_state": row.get("sync_state"),
            "active_job": active.snapshot() if active is not None else None,
            "last_outcome": _last_outcome_of(row.get("sync_state")),
            "last_sync_at": row.get("last_sync_at"),
            "last_error": row.get("last_error"),
            "last_notice": row.get("last_notice"),
        }

    def _embedding_slot_status(self) -> dict[str, Any]:
        provider = str(getattr(self._uw_cfg(), "embedding_provider", "") or "").strip()
        model = str(getattr(self._uw_cfg(), "embedding_model", "") or "").strip()
        try:
            from jarvis.ultrawiki import embeddings as embeddings_mod  # noqa: PLC0415

            available = embeddings_mod.available_backends(self._cfg)
        except Exception as exc:  # noqa: BLE001 — status never raises
            return {
                "provider": provider,
                "model": model,
                "ready": False,
                "reason": f"embedding backend probe failed ({type(exc).__name__})",
                "available": [],
            }
        row = next((r for r in available if r.get("name") == provider), None)
        if not provider:
            ready, reason = False, "no embedding provider is configured"
        elif row is None:
            ready, reason = False, f"unknown embedding provider {provider!r}"
        else:
            ready, reason = bool(row.get("ready")), str(row.get("reason") or "")
        via = ""
        if ready:
            try:
                via = embeddings_mod.credential_source(provider, self._cfg)
            except Exception:  # noqa: BLE001 — provenance is decoration
                via = ""
        return {
            "provider": provider,
            "model": model or (str(row.get("default_model") or "") if row else ""),
            "ready": ready,
            "reason": reason,
            "via": via,
            "available": available,
        }

    def _distill_slot_status(self) -> dict[str, Any]:
        provider = str(getattr(self._uw_cfg(), "distill_provider", "") or "").strip()
        model = str(getattr(self._uw_cfg(), "distill_model", "") or "").strip()
        try:
            from jarvis.brain.provider_registry import (  # noqa: PLC0415 — lazy
                BrainProviderRegistry,
            )
            from jarvis.memory.wiki.provider_chain import (  # noqa: PLC0415 — lazy
                credential_ready_wiki_providers,
            )

            available = set(BrainProviderRegistry().available())
            chain = sorted(
                credential_ready_wiki_providers(available=available, config=self._cfg)
            )
        except Exception as exc:  # noqa: BLE001 — status never raises
            return {
                "provider": provider,
                "model": model,
                "ready": False,
                "reason": f"distill chain probe failed ({type(exc).__name__})",
                "via": "",
                "chain": [],
            }
        ready = bool(chain)
        reason = (
            ""
            if ready
            else "no credential-ready chat provider is available for distillation"
        )
        # Provenance names the credential path, never a key: whatever chat
        # provider(s) the install actually holds is what carries this stage.
        if not ready:
            via = ""
        elif provider and provider in chain:
            via = f"your pinned chat provider {provider}"
        elif provider:
            via = (
                f"your pinned chat provider {provider} (not in the "
                f"credential-ready chain: {', '.join(chain)})"
            )
        else:
            via = f"chat-provider chain ({', '.join(chain)})"
        return {
            "provider": provider,
            "model": model,
            "ready": ready,
            "reason": reason,
            "via": via,
            "chain": chain,
        }

    def _rerank_slot_status(self) -> dict[str, Any]:
        provider = str(getattr(self._uw_cfg(), "rerank_provider", "") or "").strip()
        model = str(getattr(self._uw_cfg(), "rerank_model", "") or "").strip()
        try:
            from jarvis.ultrawiki import rerank as rerank_mod  # noqa: PLC0415 — lazy

            available = rerank_mod.available_rerankers(self._cfg)
        except Exception as exc:  # noqa: BLE001 — status never raises
            return {
                "provider": provider,
                "model": model,
                "ready": False,
                "reason": f"rerank backend probe failed ({type(exc).__name__})",
                "via": "",
                "available": [],
                "ranking": self._ranking_knobs(),
            }
        row = next((r for r in available if r.get("name") == provider), None)
        if not provider:
            ready = False
            reason = "rerank is not configured - the fusion order stands (optional stage)"
        elif row is None:
            ready, reason = False, f"unknown rerank provider {provider!r}"
        else:
            ready, reason = bool(row.get("ready")), str(row.get("reason") or "")
        return {
            "provider": provider,
            "model": model,
            "ready": ready,
            "reason": reason,
            "via": self._rerank_via(provider, row) if ready else "",
            "available": available,
            # The knobs ride along with the slot they govern, so the settings
            # card can show what the ranking actually does today.
            "ranking": self._ranking_knobs(),
        }

    def _ranking_knobs(self) -> dict[str, float]:
        """Live fusion weights / age decay / relevance floor; ``{}`` on error."""
        try:
            from jarvis.ultrawiki.search import ranking_settings  # noqa: PLC0415 — lazy

            return ranking_settings(self._cfg)
        except Exception:  # noqa: BLE001 — status never raises
            return {}

    def _rerank_via(self, provider: str, row: dict[str, Any] | None) -> str:
        """Key-free provenance of the rerank slot, or ``""`` when unknown.

        Asks the readiness row first and the backend adapter second (by
        capability, never by provider name), and stays EMPTY rather than
        inventing a credential path the backend never used.
        """
        if row is not None:
            declared = str(row.get("via") or "").strip()
            if declared:
                return declared
        try:
            from jarvis.ultrawiki import rerank as rerank_mod  # noqa: PLC0415 — lazy

            factory = rerank_mod.RERANK_BACKENDS.get(provider)
            if factory is None:
                return ""
            describe = getattr(factory(self._cfg), "credential_source", None)
            return str(describe() or "") if callable(describe) else ""
        except Exception:  # noqa: BLE001 — provenance is decoration, never a failure
            return ""

    # -- activation & sources ------------------------------------------------

    async def activate(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Pure-domain activation: seed areas + register the default local
        sources with consent PENDING. Writes NO config and pulls NOTHING —
        approval (and any pull) is a separate, explicit user step."""
        await self.ensure_started()
        store = self._require_store()
        payload = payload or {}
        default_area = await store.ensure_default_area()
        created_areas: list[str] = []
        for name in payload.get("areas", []) or []:
            label = str(name).strip()
            if not label:
                continue
            slug = _slugify(label)
            await store.upsert_area(slug, label)
            created_areas.append(slug)
        sources_created: list[str] = []
        sources_existing: list[str] = []
        for source_id, label in _DEFAULT_SOURCES:
            if await store.get_source(source_id) is not None:
                sources_existing.append(source_id)
                continue
            await store.upsert_source(
                source_id,
                connector=source_id,
                label=label,
                config={},
                areas=[default_area],
            )
            sources_created.append(source_id)
        return {
            "default_area": default_area,
            "areas_created": created_areas,
            "sources_created": sources_created,
            "sources_existing": sources_existing,
        }

    async def add_source(
        self,
        connector_id: str,
        label: str,
        config: dict[str, Any] | None = None,
        area_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Register a new source (consent PENDING — nothing is pulled)."""
        await self.ensure_started()
        store = self._require_store()
        from jarvis.ultrawiki import connectors as connectors_mod  # noqa: PLC0415

        registry = connectors_mod.discover_connectors()
        if connector_id not in registry:
            raise ValueError(
                f"unknown connector {connector_id!r} "
                f"(available: {', '.join(sorted(registry))})"
            )
        resolved_config = await asyncio.to_thread(
            self._checked_folder_config, registry[connector_id](), dict(config or {})
        )
        source_id = f"{connector_id}-{uuid.uuid4().hex[:8]}"
        await store.upsert_source(
            source_id,
            connector=connector_id,
            label=self._resolve_label(connector_id, label, resolved_config),
            config=resolved_config,
            areas=list(area_ids or []),
        )
        source = await store.get_source(source_id)
        assert source is not None
        return source

    async def update_source(
        self,
        source_id: str,
        *,
        label: str | None = None,
        config: dict[str, Any] | None = None,
        area_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Update one source without resetting consent or stored content."""
        await self.ensure_started()
        store = self._require_store()
        source = await store.get_source(source_id)
        if source is None:
            raise ValueError(f"unknown source {source_id!r}")
        connector_id = str(source.get("connector") or "")
        connector = self._build_connector(connector_id)
        resolved_config: dict[str, Any] | None = None
        if config is not None:
            resolved_config = dict(source.get("config") or {})
            resolved_config.update(dict(config))
            resolved_config = await asyncio.to_thread(
                self._checked_folder_config, connector, resolved_config
            )
        next_label = str(label).strip() if label is not None else str(source["label"])
        if not next_label:
            raise ValueError("source label must not be blank")
        await store.upsert_source(
            source_id,
            connector=connector_id,
            label=next_label,
            config=resolved_config,
            areas=list(area_ids) if area_ids is not None else None,
        )
        updated = await store.get_source(source_id)
        assert updated is not None
        return updated

    @staticmethod
    def _checked_folder_config(
        connector: Any, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Clean and CHECK a folder path while the user is still watching.

        A folder source is the one connector whose credential is a path the
        user typed, so it is the one that can be wrong in a way no later stage
        can recover from. Validating at registration turns "approved, synced,
        zero items, no reason" into a refusal the user can act on immediately;
        the cleaned value is what gets stored, so a path pasted with a shell
        prompt attached works on the first try instead of never.

        Blocking (it stats the path) — call it through ``asyncio.to_thread``.
        """
        from jarvis.ultrawiki.connectors.local_folder import (  # noqa: PLC0415
            LocalFolderRootError,
            normalize_root_path,
        )
        from jarvis.ultrawiki.types import AuthKind  # noqa: PLC0415

        raw = config.get("root")
        if getattr(connector, "auth", None) is not AuthKind.LOCAL_PATH or not raw:
            return config
        checked = dict(config)
        checked["root"] = normalize_root_path(str(raw))
        ctx = ConnectorContext(source_id="(new source)", config=checked)
        try:
            connector._resolve_root(ctx)  # noqa: SLF001 — the connector's own rule
        except LocalFolderRootError as exc:
            raise ValueError(str(exc)) from exc
        return checked

    async def _heal_bridge_labels(self, store: Any) -> None:
        """One-time self-heal: give pre-existing bridge sources their real name.

        ``_resolve_label`` only runs at creation, so sources registered before
        it existed keep the generic "Connected integration" label forever —
        the maintainer connected GitHub and could not tell which card it was.
        Runs once per store open (not per status poll: the candidate lookup
        probes live registries); the healed label is PERSISTED, so a source is
        only ever probed while its label is still generic. Best-effort by
        design — naming is decoration and must never block startup.
        """
        try:
            rows = await store.list_sources()
        except Exception:  # noqa: BLE001 — decoration, never a startup failure
            log.debug("bridge label heal skipped: source listing failed", exc_info=True)
            return
        for row in rows:
            connector_id = str(row.get("connector") or "")
            if connector_id != _BRIDGE_CONNECTOR_ID:
                continue
            stored = str(row.get("label") or "")
            config = row.get("config") or {}
            resolved = self._resolve_label(connector_id, stored, config)
            if resolved == stored:
                continue
            try:
                await store.upsert_source(
                    str(row.get("id")), connector=connector_id, label=resolved
                )
                log.info(
                    "bridge source %s relabeled %r -> %r",
                    row.get("id"),
                    stored,
                    resolved,
                )
            except Exception:  # noqa: BLE001
                log.debug("bridge label heal failed for %s", row.get("id"), exc_info=True)

    @staticmethod
    def _resolve_label(
        connector_id: str, label: str, config: dict[str, Any] | None
    ) -> str:
        """The name the card carries — the INTEGRATION's, for bridge sources.

        A plugin-bridge source registered under the bridge's own generic name
        produced a list of identical "Connected Integrations" cards nobody
        could tell apart. When the caller supplied no real name, the product's
        own name ("GitHub", "Notion", ...) is used instead.

        The CATALOG answers first and the live registry only second. The
        registry's display string is whatever a vendor or a hand-written
        ``mcp.json`` put there and drifts into unreadable shapes; the roster
        holds the real product name and, unlike the registry, still answers
        when the integration has been disconnected in the meantime. Both
        lookups are best-effort: neither may fail a source registration over
        what is ultimately decoration.
        """
        chosen = str(label or "").strip()
        if connector_id != _BRIDGE_CONNECTOR_ID:
            return chosen
        integration_id = str((config or {}).get("integration_id") or "").strip()
        if not integration_id:
            return chosen
        generic = {
            "",
            _BRIDGE_CONNECTOR_ID,
            integration_id.lower(),
            "connected integration",
            "connected integrations",
            # The exact label the pre-fix UI stored for every bridge source.
            "connected integration (plugins / mcp)",
            "connected integration (plugins/mcp)",
        }
        if chosen.lower() not in generic:
            return chosen
        from jarvis.ultrawiki import connector_catalog  # noqa: PLC0415 — lazy (AP-26)

        spec = connector_catalog.bridge_entry_for(integration_id)
        if spec is not None:
            return spec.label
        try:
            from jarvis.ultrawiki.connectors import (  # noqa: PLC0415 — lazy (AP-26)
                plugin_bridge,
            )

            candidate = next(
                (
                    row
                    for row in plugin_bridge.list_candidates()
                    if row.get("id") == integration_id
                ),
                None,
            )
        except Exception:  # noqa: BLE001 — naming is decoration, never a failure
            log.debug("bridge label lookup failed", exc_info=True)
            return chosen
        if candidate is None:
            return chosen
        return str(candidate.get("label") or "").strip() or chosen

    async def approve_source(
        self, source_id: str, *, auto_sync: bool = True
    ) -> dict[str, Any]:
        """Grant consent and, by default, import everything the source holds.

        Approving used to only flip a flag: the card said "Approved", stayed on
        "Never synced", and the user had no way to tell that a second, separate
        click was still owed. Consent is the gate, so the moment it is granted
        the natural next step is the FULL import — started here as a normal
        sync job the user can watch and cancel. ``auto_sync=False`` keeps the
        old consent-only behaviour for callers that drive their own sync.

        Returns ``{"source", "job_id", "auto_sync", "detail"}``; ``job_id`` is
        ``None`` whenever no import could be started, and ``detail`` says why
        in plain English. A refused import NEVER fails the approval — consent
        was granted and must stay granted.
        """
        source = await self._set_consent(source_id, ConsentState.APPROVED)
        job_id: str | None = None
        detail = ""
        if auto_sync:
            job_id, detail = await self._start_import_after_approval(source)
        return {
            "source": source,
            "job_id": job_id,
            "auto_sync": bool(auto_sync),
            "detail": detail,
        }

    async def _start_import_after_approval(
        self, source: dict[str, Any]
    ) -> tuple[str | None, str]:
        """``(job_id, honest English detail)`` — never raises."""
        source_id = str(source.get("id") or "")
        active = _active_job_for(source_id)
        if active is not None:
            return active.job_id, (
                "An import for this source is already running - it keeps going."
            )
        try:
            connector = self._build_connector(str(source.get("connector") or ""))
        except ValueError as exc:
            return None, f"Consent was granted, but no import could start: {exc}"
        if not bool(
            getattr(getattr(connector, "capabilities", None), "backfill", False)
        ):
            return None, (
                "Consent was granted. This source type cannot read its whole "
                "history, so nothing was imported automatically."
            )
        try:
            job_id = await self.start_sync(source_id, full=True)
        except SyncAlreadyRunningError as exc:
            return exc.job_id, (
                "An import for this source is already running - it keeps going."
            )
        except ValueError as exc:
            # Mode off, source disabled, connector missing — all honest
            # refusals the user can fix; the consent itself stands.
            return None, f"Consent was granted, but no import could start: {exc}"
        return job_id, (
            "Importing everything this source holds into your private "
            "knowledge store. Everything is COPIED - nothing is changed or "
            "removed at the source."
        )

    async def revoke_source(self, source_id: str) -> dict[str, Any]:
        return await self._set_consent(source_id, ConsentState.REVOKED)

    async def _set_consent(
        self, source_id: str, consent: ConsentState
    ) -> dict[str, Any]:
        await self.ensure_started()
        store = self._require_store()
        if await store.get_source(source_id) is None:
            raise ValueError(f"unknown source {source_id!r}")
        await store.set_consent(source_id, consent)
        source = await store.get_source(source_id)
        assert source is not None
        return source

    # -- sync jobs -----------------------------------------------------------

    async def start_sync(self, source_id: str, *, full: bool = False) -> str:
        """Start a backfill/incremental sync as a detached named task.

        Refuses (``ValueError``) unless UltraWiki is enabled AND the source
        holds explicit approved consent — no connector pulls a single byte
        before then. A source that already has an active job raises
        :class:`SyncAlreadyRunningError` (the REST layer answers 409).

        ``full=True`` is the FULL REFRESH: it clears the resume checkpoint AND
        the incremental cursor, so the connector re-yields everything from the
        beginning. That is the only shape in which deletions can be detected —
        tombstoning needs the COMPLETE set of ids that still exist, which only
        a from-scratch backfill provides. Without it a source that has finished
        its first backfill only ever runs incrementally, and a file deleted
        afterwards stays in the store forever.
        """
        if not self._uw_enabled():
            raise ValueError(
                "UltraWiki is disabled - enable it in the Wiki settings "
                "before syncing sources"
            )
        await self.ensure_started()
        store = self._require_store()
        source = await store.get_source(source_id)
        if source is None:
            raise ValueError(f"unknown source {source_id!r}")
        if source.get("consent") != ConsentState.APPROVED.value:
            raise ValueError(
                f"source {source_id!r} is not approved (consent is "
                f"{source.get('consent')!r}) - nothing is pulled before the "
                "source is explicitly approved"
            )
        if not source.get("enabled", False):
            raise ValueError(f"source {source_id!r} is disabled")
        active = _active_job_for(source_id)
        if active is not None:
            raise SyncAlreadyRunningError(source_id, active.job_id)
        connector = self._build_connector(str(source.get("connector") or ""))
        sync_state = await store.get_sync_state(source_id) or {}
        if full:
            # Persist the reset before the job starts, so a crash mid-run
            # still resumes as a full backfill rather than a partial one.
            await store.set_sync_state(
                source_id, backfill_checkpoint=None, cursor=None
            )
            sync_state = {
                **sync_state,
                "backfill_checkpoint": None,
                "cursor": None,
            }
        capabilities = getattr(connector, "capabilities", None)
        incremental_mode = getattr(
            capabilities, "incremental", IncrementalMode.NONE
        )
        use_incremental = (
            not full
            and bool(sync_state.get("backfill_complete_at"))
            and incremental_mode != IncrementalMode.NONE
        )
        mode = "incremental" if use_incremental else "backfill"
        job = SyncJob(
            job_id=uuid.uuid4().hex[:12], source_id=source_id, mode=mode
        )
        _register_job(job)
        task = asyncio.create_task(
            self._run_sync(job, source, connector, sync_state),
            name=f"ultrawiki-sync-{source_id}-{job.job_id}",
        )
        job.task = task
        self._sync_tasks[job.job_id] = task
        task.add_done_callback(
            lambda _t, jid=job.job_id: self._sync_tasks.pop(jid, None)
        )
        return job.job_id

    def _build_connector(self, connector_id: str) -> Any:
        from jarvis.ultrawiki import connectors as connectors_mod  # noqa: PLC0415

        factory = connectors_mod.discover_connectors().get(connector_id)
        if factory is None:
            raise ValueError(
                f"no connector {connector_id!r} is installed for this source"
            )
        return factory()

    def _connector_context(self, source: dict[str, Any]) -> ConnectorContext:
        def _secret_get(name: str) -> str | None:
            from jarvis.core.config import get_secret  # noqa: PLC0415 — lazy

            return get_secret(name)

        return ConnectorContext(
            source_id=str(source.get("id") or ""),
            config=dict(source.get("config") or {}),
            secret_get=_secret_get,
        )

    async def _run_sync(
        self,
        job: SyncJob,
        source: dict[str, Any],
        connector: Any,
        sync_state: dict[str, Any],
    ) -> None:
        store = self._require_store()
        source_id = job.source_id
        ctx = self._connector_context(source)
        job.status = "running"
        job.phase = "reading"
        checkpoint = sync_state.get("backfill_checkpoint")
        # Delete-reconcile needs the COMPLETE yielded-id set, which only a
        # backfill running from the very beginning can provide.
        full_backfill = job.mode == "backfill" and not checkpoint
        max_cursor = self._parse_cursor(sync_state.get("cursor"))
        yielded_ids: set[str] = set()
        buffer: list[RawItem] = []
        try:
            if job.mode == "incremental":
                stream: AsyncIterator[RawItem] = connector.incremental(
                    ctx, sync_state.get("cursor")
                )
            else:
                stream = connector.backfill(ctx, checkpoint)
            async for item in stream:
                buffer.append(item)
                if job.mode == "backfill" and not item.deleted:
                    yielded_ids.add(item.external_id)
                if len(buffer) >= SYNC_CHUNK_SIZE:
                    max_cursor = await self._flush_chunk(
                        job, source_id, buffer, max_cursor
                    )
                    buffer = []
            if buffer:
                max_cursor = await self._flush_chunk(
                    job, source_id, buffer, max_cursor
                )
            if job.mode == "backfill":
                deletes_capable = bool(
                    getattr(getattr(connector, "capabilities", None), "deletes", False)
                )
                if full_backfill and deletes_capable:
                    job.phase = "reconciling"
                    job.tombstoned += await store.reconcile_deletes(
                        source_id, yielded_ids
                    )
            # Stamp completion only after delete reconciliation finishes.  A
            # large vector-backed cleanup can take minutes; using a timestamp
            # captured before it made the source immediately look stale again
            # and let the freshness scheduler overwrite the useful full-run
            # outcome with a no-op incremental run.
            now = _iso_now()
            if job.mode == "backfill":
                # Clearing the checkpoint is what makes the NEXT backfill a full
                # one. Left behind, it points at the LAST item of a completed
                # walk, so a connector that cannot run incrementally
                # (IncrementalMode.NONE) resumed at the very end and yielded
                # nothing, forever — its source looked permanently frozen.
                await store.set_sync_state(
                    source_id,
                    backfill_checkpoint=None,
                    backfill_complete_at=now,
                    last_success_at=now,
                )
            else:
                await store.set_sync_state(source_id, last_success_at=now)
            await store.set_source_status(
                source_id,
                last_sync_at=now,
                last_error=None,
                last_notice=self._import_notice_for(source, job),
            )
            await self._record_outcome(store, job, "done", now)
            job.status = "done"
        except asyncio.CancelledError:
            # No outcome is recorded here: a cancelled run has no outcome to
            # report, and shutdown cancels every job right before the store
            # closes — writing on this path would race that close.
            job.status = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 — the job records, never crashes callers
            log.exception(
                "UltraWiki sync %s for source %s failed", job.job_id, source_id
            )
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            try:
                await store.set_source_status(source_id, last_error=job.error)
                await self._record_outcome(store, job, "failed", _iso_now())
            except Exception:  # noqa: BLE001 — best-effort bookkeeping
                log.debug("sync error bookkeeping failed", exc_info=True)
        finally:
            job.ended_at = time.time()
            job.phase = "finished"
            job.task = None

    @staticmethod
    async def _record_outcome(
        store: Any, job: SyncJob, status: str, finished_at: str
    ) -> None:
        """Persist the finished job's totals; a failure here never fails the job."""
        try:
            await store.record_sync_outcome(
                job.source_id,
                status=status,
                mode=job.mode,
                finished_at=finished_at,
                new=job.new,
                changed=job.changed,
                unchanged=job.unchanged,
                tombstoned=job.tombstoned,
            )
        except Exception:  # noqa: BLE001 — bookkeeping, never the work itself
            log.debug("sync outcome bookkeeping failed", exc_info=True)

    @staticmethod
    def _import_notice_for(source: dict[str, Any], job: SyncJob) -> str | None:
        """The amber "it worked, there was nothing to take" line, or ``None``.

        A plugin-bridge source whose integration has no pull adapter yet syncs
        successfully and imports zero items. Until now that left only a log
        line, so the card showed a green "Approved" source that never grew and
        gave the user nothing to act on. ``None`` CLEARS any previous notice —
        the moment an adapter ships and items arrive, the line disappears.
        """
        if job.items > 0:
            return None
        connector_id = str(source.get("connector") or "")
        if connector_id in _FOLDER_CONNECTOR_IDS:
            return _empty_folder_notice(source)
        if connector_id != _BRIDGE_CONNECTOR_ID:
            return None
        integration_id = str((source.get("config") or {}).get("integration_id") or "")
        if not integration_id:
            return None
        try:
            from jarvis.ultrawiki.connectors import (  # noqa: PLC0415 — lazy (AP-26)
                plugin_bridge,
            )

            if plugin_bridge.has_pull_adapter(integration_id):
                return None
        except Exception:  # noqa: BLE001 — an unreadable registry states nothing
            log.debug("pull-adapter probe failed", exc_info=True)
            return None
        return BRIDGE_ADAPTER_PENDING_NOTICE

    async def _flush_chunk(
        self,
        job: SyncJob,
        source_id: str,
        items: list[RawItem],
        max_cursor: int | None,
    ) -> int | None:
        store = self._require_store()
        # The LAST gate before storage, and deliberately the only one: every
        # connector reaches the store through here, so a reader added later is
        # covered without having to remember. Order matters — items are scrubbed
        # BEFORE the write, never cleaned up afterwards, because a secret that
        # was written once has already been indexed and embedded.
        items = self._scrub(job, items)
        counts = await store.upsert_items(source_id, items)
        job.phase = "importing"
        job.chunks += 1
        job.new += counts.new
        job.changed += counts.changed
        job.unchanged += counts.unchanged
        job.tombstoned += counts.tombstoned
        # Cursor advance from item metadata (connector convention: file
        # walkers carry 'mtime_ns', jarvis-conversations carries 'max_rowid').
        for item in items:
            for key in ("mtime_ns", "max_rowid"):
                value = item.metadata.get(key)
                if value is None:
                    continue
                try:
                    numeric = int(value)
                except (TypeError, ValueError):
                    continue
                if max_cursor is None or numeric > max_cursor:
                    max_cursor = numeric
        updates: dict[str, Any] = {}
        if job.mode == "backfill":
            updates["backfill_checkpoint"] = items[-1].external_id
        if max_cursor is not None:
            updates["cursor"] = str(max_cursor)
        if updates:
            await store.set_sync_state(source_id, **updates)
        return max_cursor

    @staticmethod
    def _scrub(job: SyncJob, items: list[RawItem]) -> list[RawItem]:
        """Credentials out, everything else through — and counted for the user.

        Items are never DROPPED here. A withheld file keeps its name, date and
        link, which is both honest ("there is a .env in this project" is true
        and useful) and load-bearing: the backfill checkpoint is the last
        item's id, so removing one would move the resume point.
        """
        scrubbed: list[RawItem] = []
        withheld = 0
        redactions = 0
        for item in items:
            result = scrub_item(item)
            scrubbed.append(result.item)
            withheld += 1 if result.withheld else 0
            redactions += result.redactions
        if withheld or redactions:
            job.secrets_withheld += withheld
            job.secrets_redacted += redactions
            log.info(
                "UltraWiki sync %s: withheld %d credential file(s) and redacted "
                "%d secret(s) before storing",
                job.source_id,
                withheld,
                redactions,
            )
        return scrubbed

    @staticmethod
    def _parse_cursor(cursor: Any) -> int | None:
        if cursor is None or cursor == "":
            return None
        try:
            return int(cursor)
        except (TypeError, ValueError):
            return None

    # -- job registry surface ------------------------------------------------

    def job_snapshot(self, job_id: str) -> dict[str, Any] | None:
        job = _get_job(job_id)
        return job.snapshot() if job is not None else None

    def list_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        return _list_job_snapshots(limit)

    def cancel_job(self, job_id: str) -> bool:
        return _cancel_job(job_id)

    # -- recovery ------------------------------------------------------------

    async def requeue_failed(self, source_id: str | None = None) -> int:
        """Return dead-lettered items to the pipeline; count moved.

        The counterpart to the stage gates: whatever already gave up while a
        slot was dead (no chat credential, unreachable embedding endpoint) is
        stranded in ``failed`` for good, because retries are exhausted. This is
        how a user recovers that backlog after fixing the cause — no
        re-import, no data loss, no hand-edited database.
        """
        await self.ensure_started()
        store = self._require_store()
        if source_id is not None and await store.get_source(source_id) is None:
            raise ValueError(f"unknown source {source_id!r}")
        return int(await store.requeue_failed(source_id))

    # -- identity (design doc 05 · D-10) -------------------------------------

    async def seed_identities(self, *, contact_store: Any = None) -> dict[str, Any]:
        """Seed entities from the user's address book; idempotent and re-runnable.

        Safe to call on every activation: a second pass links instead of
        duplicating. Runs off the boot path like every other service action.
        ``contact_store`` is a test seam; production resolves the real one.
        """
        await self.ensure_started()
        from jarvis.ultrawiki.identity_store import (  # noqa: PLC0415 — lazy (AP-26)
            seed_from_contacts,
        )

        report = await seed_from_contacts(
            self._require_store(), contact_store=contact_store
        )
        return report.to_dict()

    async def list_people(
        self, *, query: str = "", limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """The People view's list: live person entities, name-ordered."""
        await self.ensure_started()
        return await self._require_store().list_people(
            query=query, limit=limit, offset=offset
        )

    async def person_profile(self, entity_id: int) -> dict[str, Any] | None:
        """One person's identifiers, merge history and open proposals."""
        await self.ensure_started()
        return await self._require_store().get_person(int(entity_id))

    async def identity_queue(
        self, *, status: str | None = "pending", limit: int = 100
    ) -> list[dict[str, Any]]:
        """Merge proposals awaiting a decision (``status=None`` for all)."""
        await self.ensure_started()
        return await self._require_store().list_confirm_queue(
            status=status, limit=limit
        )

    async def confirm_identity_merge(
        self, queue_id: int, *, decided_by: str = "user"
    ) -> dict[str, Any]:
        """Apply one proposal. Returns the audit id that reverses it."""
        await self.ensure_started()
        merge_id = await self._require_store().confirm_merge(
            int(queue_id), decided_by=decided_by
        )
        return {"queue_id": int(queue_id), "merge_id": int(merge_id)}

    async def reject_identity_merge(
        self, queue_id: int, *, decided_by: str = "user"
    ) -> dict[str, Any]:
        """Decline one proposal permanently (never asked about again)."""
        await self.ensure_started()
        await self._require_store().reject_merge(int(queue_id), decided_by=decided_by)
        return {"queue_id": int(queue_id), "status": "rejected"}

    async def unmerge_identity(self, merge_id: int) -> dict[str, Any]:
        """Undo one merge, restoring both identities exactly as they were."""
        await self.ensure_started()
        await self._require_store().unmerge(int(merge_id))
        return {"merge_id": int(merge_id), "status": "undone"}

    async def identity_merge_log(
        self, *, entity_id: int | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """The audit trail: every merge, its evidence, and whether it stands."""
        await self.ensure_started()
        return await self._require_store().list_merge_log(
            entity_id=entity_id, limit=limit
        )

    # -- episodic events (design doc 01 · uw_events) -------------------------

    async def list_events(
        self,
        *,
        since: str | None = None,
        until: str | None = None,
        kind: str | None = None,
        entity_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Episodic events in a time window, newest first.

        ``since``/``until`` bound the VALID time (when it happened), not the
        recorded time, and match by overlap — an event that spans a month is
        returned for a query about one day inside it.
        """
        await self.ensure_started()
        return await self._require_store().list_events(
            since=since,
            until=until,
            kind=kind,
            entity_id=entity_id,
            limit=limit,
            offset=offset,
        )

    async def events_between(
        self,
        start: str,
        end: str,
        *,
        kind: str | None = None,
        entity_id: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """The design's ``events_between`` primitive (doc 03) — cheap, LLM-free."""
        await self.ensure_started()
        return await self._require_store().events_between(
            start, end, kind=kind, entity_id=entity_id, limit=limit
        )

    async def event_detail(self, event_id: int) -> dict[str, Any] | None:
        """One event with its participants, place and evidence."""
        await self.ensure_started()
        return await self._require_store().get_event(int(event_id))

    async def event_counts(self) -> dict[str, int]:
        """How many events of each kind the store holds."""
        await self.ensure_started()
        return await self._require_store().event_counts()

    # -- search --------------------------------------------------------------

    async def search(self, query: str, **kwargs: Any) -> Any:
        """Delegate to the retrieval module (built separately); lazy import
        keeps this module cheap and decoupled from the read path."""
        await self.ensure_started()
        try:
            from jarvis.ultrawiki import search as search_mod  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "UltraWiki search is not available in this build"
            ) from exc
        return await search_mod.search(
            store=self._require_store(), cfg=self._cfg, query=query, **kwargs
        )

    async def word_search(self, word: str, **kwargs: Any) -> Any:
        """Expand one word into its meaning-neighbourhood and retrieve passages.

        Same seam as :meth:`search`: the retrieval module owns the logic and is
        imported lazily (AP-26). Returns a
        :class:`~jarvis.ultrawiki.word_search.WordSearchOutcome`, which carries
        its own honest status instead of raising on a thin index.
        """
        await self.ensure_started()
        from jarvis.ultrawiki import word_search as word_search_mod  # noqa: PLC0415 — lazy (AP-26)

        return await word_search_mod.word_search(
            self._require_store(), self._cfg, word, **kwargs
        )

    async def rebuild_lexicon(self) -> dict[str, Any]:
        """Throw the word vocabulary away so the background pass recounts it.

        The one repair for a ``doc_freq`` that has drifted after many deletions
        and the way a user asks for a fresh vocabulary after changing the
        embedding model. Deliberately manual: it costs the embedding calls of
        rebuilding the whole term index.
        """
        await self.ensure_started()
        store = self._require_store()
        reset = getattr(store, "reset_lexicon", None)
        if not callable(reset):
            return {
                "ok": False,
                "reason": "this knowledge store has no word lexicon",
                "lexicon": {},
            }
        await reset()
        model, dim = await store.embedding_space()
        return {
            "ok": True,
            "reason": "",
            "lexicon": dict(await store.lexicon_counts(model=model, dim=dim)),
        }


# ---------------------------------------------------------------------------
# Active-service seam
# ---------------------------------------------------------------------------
#
# The brain's memory surfaces — the ``wiki-recall`` tool and the system-prompt
# context injector — sit BELOW the web layer and cannot reach ``app.state``.
# This is the same write-once-at-bootstrap handle registry
# ``jarvis/core/runtime_refs.py`` keeps for the BrainManager and the MCP
# registry: the WebServer registers the instance it constructed, and the lower
# layers ask for it instead of importing upward.
#
# A one-element list lets the setter rebind without ``global``. Assigning a
# single object reference is atomic in CPython and this is written once at
# bootstrap and read many times afterwards, so no lock is involved.

_ACTIVE_SERVICE: list[Any] = []


def set_active_service(service: UltraWikiService | None) -> None:
    """Register the live service, or clear the registration with ``None``."""
    if _ACTIVE_SERVICE:
        _ACTIVE_SERVICE[0] = service
    else:
        _ACTIVE_SERVICE.append(service)


def get_active_service() -> UltraWikiService | None:
    """The live service, or ``None`` when this runtime has none.

    Falls back to the handle the WebServer parks on ``app.state``, so a
    process that built a service without passing through
    :func:`set_active_service` still answers. ``None`` therefore means "there
    is genuinely no service here" (headless brain, tests, boot still in
    flight), never "the registration was missed".
    """
    service = _ACTIVE_SERVICE[0] if _ACTIVE_SERVICE else None
    if service is not None:
        return service
    try:
        from jarvis.core import runtime_refs  # noqa: PLC0415 — lazy (AP-26)

        app = runtime_refs.get_web_app()
    except Exception:  # noqa: BLE001 — a missing runtime is a None, never a raise
        return None
    return getattr(getattr(app, "state", None), "ultrawiki", None)


def active_search_service() -> UltraWikiService | None:
    """The live service ONLY while it can actually answer a search.

    Three conditions, every one of them IO-free because this is consulted per
    brain turn on the voice path (AP-9): a service exists, UltraWiki mode is
    ON in the config object that service holds (the same object the routes
    mutate on an in-app toggle, so a mode switch needs no restart), and its
    store is open. Anything else answers ``None`` and the caller keeps its
    pre-UltraWiki behaviour unchanged.
    """
    service = get_active_service()
    if service is None:
        return None
    try:
        if not service._uw_enabled():
            return None
        if service._store is None:
            return None
    except Exception:  # noqa: BLE001 — an unreadable handle is simply not ready
        return None
    return service
