"""Phase B5 — wiki write-wiring bootstrap.

Wires :class:`~jarvis.memory.wiki.session_rollup.SessionRollupWorker` (B7)
and :class:`~jarvis.memory.wiki.curator.WikiCurator` (B1) into the running
application's startup flow.

Entry point
-----------
Call :func:`bootstrap_wiki_integration` once from
``jarvis/ui/web/server.py`` after the event bus is ready.  The returned
:class:`WikiIntegrationHandle` must be kept alive and its
:meth:`WikiIntegrationHandle.shutdown` method awaited during teardown.

Scheduler fallback
------------------
Agent D's :class:`~jarvis.memory.wiki.scheduler.CuratorScheduler` and
:class:`~jarvis.memory.wiki.lock.VaultLock` may not yet be merged.  When
``scheduler_factory`` is ``None`` (or when ``config.fallback_to_direct_ingest``
is ``True``), the integration falls back to calling
``WikiCurator.ingest(text=…, source=…)`` directly, bypassing the lock and
cooldown logic.  A single ``INFO`` line is logged on each startup to make
this fallback visible.

Anti-patterns avoided
---------------------
- AP-3: all vault writes flow through :class:`AtomicWriter`.
- AP-4: the ``IdleEntered`` handler fires an ``asyncio.create_task`` and
  returns immediately — no blocking inside the event handler.
- AP-5: no DB mocking; real components are constructed and real paths are
  used in integration tests.
- AP-6: uses the bus provided through the bootstrap argument; no new bus.
- AP-8: failures are logged and reported, never silently swallowed.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jarvis.core.events import IdleEntered

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus
    from jarvis.core.config import VoiceBridgeConfig, WikiIntegrationConfig
    from jarvis.memory.wiki.protocols import PageRepository
    from jarvis.memory.wiki.scheduler import CuratorScheduler

log = logging.getLogger(__name__)

# Maximum seconds to wait for a running rollup task before forcefully
# cancelling it at shutdown.  Matches the AGENT-A briefing §8 constraint.
_SHUTDOWN_TIMEOUT_S = 5.0


# ---------------------------------------------------------------------------
# Running-curator registry (B5 follow-up: wiki-ingest tool)
# ---------------------------------------------------------------------------
# The ``wiki-ingest`` tool needs the same live ``WikiCurator`` instance that
# the rollup/voice-bridge paths use.  The brain factory builds tools before
# ``bootstrap_wiki_integration`` runs, so a build-time constructor injection
# is impossible.  Mirror the spawn-worker lazy-resolver pattern: stash the
# live curator in a module global once bootstrap finishes, expose a getter,
# and clear it on shutdown.
#
# Tests can override the registry by calling ``_set_running_curator`` with a
# fake before the tool's ``execute`` runs.

_running_curator: Any = None


def get_running_curator() -> Any:
    """Return the live ``WikiCurator`` instance, or ``None`` if not bootstrapped."""
    return _running_curator


def _set_running_curator(curator: Any) -> None:
    """Set or clear the module-level curator registry.  Private helper."""
    global _running_curator
    _running_curator = curator


# ---------------------------------------------------------------------------
# Handle returned to the caller
# ---------------------------------------------------------------------------


@dataclass
class WikiIntegrationHandle:
    """Returned by :func:`bootstrap_wiki_integration`.

    Keep the instance alive for the process lifetime.  Call
    :meth:`shutdown` from the application teardown hook.
    """

    _unsubscribe_idle: Callable[[], None]
    _worker_stop: Callable[[], Awaitable[None]] | None
    _task: "asyncio.Task[Any] | None" = field(default=None)
    _telemetry_task: "asyncio.Task[Any] | None" = field(default=None)
    # Spec A4: age-based journal flush loop. Same fire-and-forget
    # conventions as the hourly telemetry loop (started off the boot
    # critical path per AP-26, cancelled here on teardown).
    _journal_age_flush_task: "asyncio.Task[Any] | None" = field(default=None)
    # The VoiceFactBridge attached during bootstrap. Declared as a field (not
    # monkey-patched) so shutdown() can stop it — otherwise its TranscriptFinal
    # / ResponseGenerated subscriptions leak on every teardown.
    _voice_bridge: Any = field(default=None)
    # Wave-2: the Stage-1 candidate journal (SQLite). Closed on shutdown so
    # the connection does not leak across test bootstraps.
    _journal: Any = field(default=None)
    # Contact → person-page mirror: detach callback (notify sink + bus
    # subscription) and the boot reconciliation task.
    _contact_mirror_cleanup: Callable[[], None] | None = field(default=None)
    _contact_reconcile_task: asyncio.Task[Any] | None = field(default=None)

    async def shutdown(self) -> None:
        """Unsubscribe the ``IdleEntered`` handler and cancel pending tasks.

        Waits up to five seconds for any in-flight rollup to finish before
        cancelling.  Logs a warning when the timeout is hit.
        """
        # Unsubscribe first so no new tasks are created while we drain.
        try:
            self._unsubscribe_idle()
        except Exception:  # noqa: BLE001
            log.debug("wiki_integration: unsubscribe_idle failed; already detached")

        # Detach the contact mirror (sink + bus subscription) and stop a
        # still-running boot reconciliation.
        if self._contact_mirror_cleanup is not None:
            try:
                self._contact_mirror_cleanup()
            except Exception:  # noqa: BLE001
                log.debug(
                    "wiki_integration: contact mirror cleanup failed; continuing"
                )
            self._contact_mirror_cleanup = None
        if self._contact_reconcile_task is not None:
            self._contact_reconcile_task.cancel()
            self._contact_reconcile_task = None

        # Stop the voice-fact bridge so its bus subscriptions are released.
        if self._voice_bridge is not None:
            try:
                self._voice_bridge.stop()
            except Exception:  # noqa: BLE001
                log.debug("wiki_integration: voice_bridge.stop() failed; continuing teardown")
            self._voice_bridge = None

        # Close the Stage-1 candidate journal (after the bridge stopped, so
        # no in-flight extraction appends into a closed connection).
        if self._journal is not None:
            try:
                self._journal.close()
            except Exception:  # noqa: BLE001
                log.debug("wiki_integration: journal.close() failed; continuing teardown")
            self._journal = None

        # Stop the rollup worker (unsubscribes its own IdleEntered handler).
        if self._worker_stop is not None:
            try:
                await self._worker_stop()
            except Exception:  # noqa: BLE001
                log.debug("wiki_integration: worker.stop() failed; continuing teardown")

        # Wait for any in-flight flush to finish.
        task = self._task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=_SHUTDOWN_TIMEOUT_S)
            except (TimeoutError, asyncio.TimeoutError):
                log.warning(
                    "wiki_integration: in-flight rollup did not finish in %ss — cancelling",
                    _SHUTDOWN_TIMEOUT_S,
                )
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            except asyncio.CancelledError:
                pass
        self._task = None

        # Cancel the hourly-telemetry loop if started.
        if self._telemetry_task is not None and not self._telemetry_task.done():
            self._telemetry_task.cancel()
            try:
                await self._telemetry_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._telemetry_task = None

        # Cancel the age-based journal flush loop if started (spec A4).
        if self._journal_age_flush_task is not None and not self._journal_age_flush_task.done():
            self._journal_age_flush_task.cancel()
            try:
                await self._journal_age_flush_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._journal_age_flush_task = None

        # Clear the running-curator registry so a fresh bootstrap (e.g. in a
        # test that tears down and re-creates) does not see the stale one.
        _set_running_curator(None)

        log.info("wiki_integration: shutdown complete")


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


async def bootstrap_wiki_integration(
    *,
    bus: "EventBus",
    repo: "PageRepository",
    vault_root: Path,
    config: "WikiIntegrationConfig",
    brain_caller: Callable[[str, str], Awaitable[str]] | None = None,
    scheduler_factory: "Callable[..., CuratorScheduler] | None" = None,
    voice_bridge_config: "VoiceBridgeConfig | None" = None,
) -> WikiIntegrationHandle:
    """Wire ``SessionRollupWorker`` → (Scheduler →) ``WikiCurator`` and
    subscribe to ``IdleEntered``.

    When ``scheduler_factory`` is ``None`` or
    ``config.fallback_to_direct_ingest`` is ``True``, the integration
    falls back to calling ``WikiCurator.ingest()`` directly (bypassing the
    lock and cooldown).

    Returns a :class:`WikiIntegrationHandle` whose
    :meth:`~WikiIntegrationHandle.shutdown` must be called at app
    teardown.

    Parameters
    ----------
    bus:
        The application-wide event bus.  Must be the shared instance, not a
        new one (AP-6).
    repo:
        A :class:`~jarvis.memory.wiki.protocols.PageRepository` instance.
    vault_root:
        Absolute (or config-relative) path to the Obsidian vault root.
    config:
        :class:`~jarvis.core.config.WikiIntegrationConfig` from ``jarvis.toml``.
    brain_caller:
        Optional callable ``(system_prompt, user_text) -> str`` wired into
        the curator LLM.  When ``None``, the curator uses its own
        ``BrainProviderRegistry`` fallback.
    scheduler_factory:
        Optional factory for Agent D's ``CuratorScheduler``.  ``None``
        activates the direct-ingest fallback.
    """
    if not config.enabled:
        log.info("wiki_integration: disabled via config; skipping bootstrap")
        # Return a no-op handle so callers need no special-case branch.
        return WikiIntegrationHandle(
            _unsubscribe_idle=lambda: None,
            _worker_stop=None,
        )

    vault_path = Path(vault_root).resolve()
    log.info("wiki_integration: bootstrapping (vault=%s)", vault_path)

    # ------------------------------------------------------------------
    # Build the curator stack
    # ------------------------------------------------------------------
    root_cfg = _load_root_config()
    curator = _build_curator(
        repo=repo,
        vault_root=vault_path,
        brain_caller=brain_caller,
        root_config=root_cfg,
    )

    # Publish the live curator so the ``wiki-ingest`` tool can find it.
    # See module-level docstring on ``_running_curator``.
    _set_running_curator(curator)

    # ------------------------------------------------------------------
    # Build the scheduler whenever a factory is provided. Wave-2 needs it
    # for the JOURNAL-pressure drain regardless of the legacy direct-ingest
    # preference; ``use_scheduler`` below only decides whether the (D2-
    # retired-by-default) session re-ingest pass routes through it.
    # ------------------------------------------------------------------
    scheduler: "CuratorScheduler | None" = None
    if scheduler_factory is not None:
        try:
            scheduler = scheduler_factory(curator=curator)  # type: ignore[call-arg]
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "wiki_integration: scheduler_factory raised %s — falling back to direct ingest",
                exc,
            )
            scheduler = None

    use_scheduler = scheduler is not None and not config.fallback_to_direct_ingest
    if not use_scheduler:
        log.info(
            "wiki_integration: legacy re-ingest pass (if enabled) uses direct ingest"
        )

    # ------------------------------------------------------------------
    # Build the SessionRollupWorker (B7)
    # ------------------------------------------------------------------
    # Load the root config ONCE and hand the same snapshot to the worker
    # and to the D2 gate below — two independent load_config() calls could
    # diverge on a racy partial config write, making the gate inconsistent
    # with the worker's own wiki_write_enabled view.
    worker = _build_rollup_worker(
        repo=repo,
        vault_root=vault_path,
        bus=bus,
        root_config=root_cfg,
    )

    # D2 (2026-06): the awareness-episode -> durable session-page feed is
    # retired by default. ``wiki_write_enabled`` (SessionRollupConfig) gates
    # BOTH the worker's own page write (handled inside flush_session) AND the
    # integration's redundant re-read-and-re-ingest second curator pass below.
    # The worker is still started so its lifecycle/shutdown stays symmetric,
    # but when the feed is retired we never subscribe the re-ingest handler.
    rollup_cfg = root_cfg.memory.wiki.session_rollup
    wiki_write_enabled = bool(getattr(rollup_cfg, "wiki_write_enabled", False))

    # Wave-2 B6 (D4): make sure the living user profile page exists and
    # carries the structured sections the consolidator maintains. One-time
    # idempotent skeleton pass; failures never break boot.
    try:
        from jarvis.memory.wiki.profile import ensure_profile_skeleton

        user_slug = str(getattr(rollup_cfg, "user_entity_slug", "") or "")
        if user_slug:
            await ensure_profile_skeleton(
                vault_root=vault_path, slug=user_slug, curator=curator,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki_integration: profile skeleton pass failed: %s", exc)

    # Start the worker — this subscribes it to IdleEntered internally.
    if config.subscribe_idle:
        await worker.start()
        log.info("wiki_integration: SessionRollupWorker started")

    # ------------------------------------------------------------------
    # Subscribe our own IdleEntered handler to pass rollup output to
    # the curator (or scheduler).
    #
    # Anti-pattern note (Agent-A §8 anti-patterns):
    #   SessionRollupWorker.start() already subscribes to IdleEntered.
    #   Our handler here is a DIFFERENT handler — it receives the same
    #   event, waits for the worker's flush_session() to produce a page
    #   path, then feeds the session summary to WikiCurator.ingest().
    #   We never double-subscribe the *worker's* handler.
    # ------------------------------------------------------------------
    # Create the handle before defining the callback so the callback can
    # capture it and update the _task field.
    handle = WikiIntegrationHandle(
        _unsubscribe_idle=lambda: None,   # replaced below
        _worker_stop=worker.stop if config.subscribe_idle else None,
    )

    if not wiki_write_enabled:
        log.info(
            "wiki_integration: session-page feed retired (D2) — "
            "skipping the awareness re-ingest pass; conversation "
            "(VoiceFactBridge) remains the sole wiki feed"
        )

    if config.subscribe_idle and wiki_write_enabled:
        async def _on_idle_entered(event: IdleEntered) -> None:  # noqa: RUF029
            """Non-blocking IdleEntered handler.

            Fires an asyncio task that calls flush_session() and then
            forwards the result to the curator (or scheduler).  Returns
            immediately so the event bus dispatch loop is never blocked.
            """
            task = asyncio.create_task(
                _flush_and_ingest(
                    worker=worker,
                    curator=curator,
                    # Legacy path choice: only route through the scheduler
                    # when the operator explicitly disabled the direct-
                    # ingest fallback (pre-Wave-2 semantics preserved).
                    scheduler=scheduler if use_scheduler else None,
                    config=config,
                ),
                name="wiki-integration-flush",
            )
            handle._task = task  # noqa: SLF001
            task.add_done_callback(_log_task_result)

        bus.subscribe(IdleEntered, _on_idle_entered)

        def _unsubscribe() -> None:
            try:
                bus.unsubscribe(IdleEntered, _on_idle_entered)
            except Exception:  # noqa: BLE001
                log.debug("wiki_integration: unsubscribe failed; already detached")

        handle._unsubscribe_idle = _unsubscribe  # noqa: SLF001

    # ------------------------------------------------------------------
    # B5 follow-up (2026-05-13): VoiceFactBridge — listens for voice turns
    # where the brain replies with an acknowledgement keyword and pushes
    # the user-spoken fact straight to the curator. Without this, voice
    # turns never reach the wiki because:
    #   - voice_turns live in sessions.db, not in awareness_episodes
    #   - SessionRollupWorker reads awareness_episodes only at idle
    # The bridge closes that gap with an explicit "brain said notiert"
    # heuristic.
    # ------------------------------------------------------------------
    # Wave-2 Stage 1: candidate journal + conversation fact extractor.
    # Guarded: any failure degrades to the legacy direct-ingest bridge
    # (extractor=None) so the conversation->wiki path never goes dark.
    extractor = None
    journal = None
    try:
        extractor_cfg = root_cfg.memory.wiki.extractor
        if bool(getattr(extractor_cfg, "enabled", True)):
            from jarvis.memory.wiki.db_path import resolve_wiki_db_path
            from jarvis.memory.wiki.extractor import ConversationFactExtractor
            from jarvis.memory.wiki.journal import CandidateJournal

            db_path = resolve_wiki_db_path(
                getattr(root_cfg.memory, "data_dir", "./data")
            )
            journal = CandidateJournal(db_path)
            extractor = ConversationFactExtractor(config=root_cfg, journal=journal)
            handle._journal = journal  # noqa: SLF001
            if scheduler is not None:
                # NOTE the TOML key: the scheduler section is the TOP-LEVEL
                # [wiki_scheduler] table (JarvisConfig.wiki_scheduler), NOT
                # [memory.wiki.scheduler] — a key set there is silently
                # ignored. Moving it under WikiMemoryConfig is a tracked
                # cleanup task (config.py comment near wiki_scheduler).
                threshold = int(
                    getattr(
                        getattr(root_cfg, "wiki_scheduler", None),
                        "consolidate_after_candidates",
                        # Fallback must match SchedulerConfig's own default (3,
                        # spec A4). The old 8 here could resurrect the
                        # pre-A4 threshold if the [wiki_scheduler] section were
                        # ever absent, silently undermining ambient capture.
                        3,
                    )
                )
                extractor.attach_scheduler(scheduler, consolidate_after=threshold)
            log.info(
                "wiki_integration: Stage-1 fact extractor active "
                "(journal db=%s, journal trigger=%s)",
                db_path,
                "scheduler" if scheduler is not None else "off",
            )
        else:
            log.info(
                "wiki_integration: [memory.wiki.extractor] disabled — "
                "bridge keeps the legacy direct curator ingest"
            )
    except Exception as exc:  # noqa: BLE001
        extractor = None
        if journal is not None:
            try:
                journal.close()
            except Exception:  # noqa: BLE001
                pass
            journal = None
        log.warning(
            "wiki_integration: Stage-1 extractor unavailable (%s) — "
            "falling back to the legacy direct curator ingest", exc,
        )

    # Wave-2 Stage 2: body-aware consolidator, drained via the scheduler's
    # JOURNAL trigger; refreshes the self-documentation page after each run.
    if journal is not None and scheduler is not None:
        try:
            from jarvis.memory.wiki.consolidator import Consolidator
            from jarvis.memory.wiki.search import VaultSearch
            from jarvis.memory.wiki.self_doc import refresh_memory_page

            try:
                consolidator_search: Any = VaultSearch(vault_path)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "wiki_integration: VaultSearch unavailable for the "
                    "consolidator (%s) — slug-overlap retrieval only", exc,
                )
                consolidator_search = None

            async def _refresh_self_doc() -> None:
                await refresh_memory_page(
                    curator=curator, vault_root=vault_path, journal=journal,
                )

            consolidator = Consolidator(
                config=root_cfg,
                journal=journal,
                curator=curator,
                search=consolidator_search,
                vault_root=vault_path,
                on_run_complete=_refresh_self_doc,
            )
            scheduler.attach_consolidator(consolidator)
            log.info("wiki_integration: Stage-2 consolidator attached to scheduler")
            # C1: drain any backlog left over from the previous run so small
            # leftovers (< pressure threshold) consolidate without waiting
            # for new conversation.
            kick_journal_backlog(journal, scheduler)

            # Spec A4: below-threshold backlogs on a quiet install would
            # otherwise sit pending forever (not enough NEW conversation
            # ever arrives to cross consolidate_after_candidates). Start a
            # background age-check loop, same fire-and-forget conventions
            # as the hourly telemetry loop started below (AP-9/AP-26).
            handle._journal_age_flush_task = asyncio.create_task(  # noqa: SLF001
                _journal_age_flush_loop(journal, scheduler, root_cfg.wiki_scheduler),
                name="wiki-journal-age-flush",
            )
            log.info("wiki_integration: age-based journal flush loop started")
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "wiki_integration: Stage-2 consolidator unavailable (%s) — "
                "journal entries stay pending", exc,
            )

    # B7: make sure the self-documentation page exists from first boot.
    try:
        from jarvis.memory.wiki.self_doc import refresh_memory_page as _boot_refresh

        await _boot_refresh(curator=curator, vault_root=vault_path, journal=journal)
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki_integration: self-doc boot refresh failed: %s", exc)

    # ------------------------------------------------------------------
    # Contact → person-page mirror (deterministic, no LLM). One page per
    # saved contact under people/, archived on delete, healed at boot.
    # Spec: docs/superpowers/specs/2026-06-10-contact-wiki-mirror-design.md
    # Own AtomicWriter instance, mirroring _build_curator /
    # _build_rollup_worker (each write surface gets its own writer).
    # ------------------------------------------------------------------
    try:
        from jarvis.memory.wiki.atomic_writer import AtomicWriter as _MirrorWriter
        from jarvis.memory.wiki.contact_mirror import wire_contact_mirror

        _mirror_writer = _MirrorWriter(
            vault_root=vault_path, backup_dir=vault_path.parent / "wiki-backups"
        )
        contact_mirror, _mirror_cleanup = wire_contact_mirror(
            bus=bus, vault_root=vault_path, writer=_mirror_writer, repo=repo,
        )
        handle._contact_mirror_cleanup = _mirror_cleanup  # noqa: SLF001
        handle._contact_reconcile_task = asyncio.create_task(  # noqa: SLF001
            contact_mirror.reconcile_all(), name="contact-mirror-reconcile"
        )
        log.info("wiki_integration: contact mirror wired (people/ pages)")
    except Exception as exc:  # noqa: BLE001 — contacts absent ≠ wiki broken
        log.warning("wiki_integration: contact mirror not wired — %s", exc)

    try:
        from jarvis.memory.wiki.voice_bridge import VoiceFactBridge
        voice_bridge = VoiceFactBridge(
            bus=bus,
            curator=curator,
            config=voice_bridge_config,
            extractor=extractor,
        )
        voice_bridge.start()
        handle._voice_bridge = voice_bridge  # noqa: SLF001
        log.info(
            "wiki_integration: VoiceFactBridge attached "
            "(aggressive_mode=%s, extractor=%s)",
            getattr(voice_bridge_config, "aggressive_mode", "default(True)"),
            "stage-1" if extractor is not None else "legacy-direct",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki_integration: VoiceFactBridge failed to start: %s", exc)

    # ------------------------------------------------------------------
    # B8.7 — telemetry hourly-summary loop. Cheap, in-memory; the only
    # observable side effect is one log line per hour. Cancelled on
    # shutdown via ``handle._telemetry_task``.
    # ------------------------------------------------------------------
    try:
        from jarvis.memory.wiki.telemetry import run_hourly_summary_loop
        handle._telemetry_task = asyncio.create_task(    # noqa: SLF001
            run_hourly_summary_loop(),
            name="wiki-telemetry-hourly",
        )
        log.info("wiki_integration: hourly telemetry summary loop started")
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki_integration: telemetry summary loop failed to start: %s", exc)

    log.info("wiki_integration: bootstrap complete")
    return handle


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _flush_and_ingest(
    *,
    worker: Any,
    curator: Any,
    scheduler: "CuratorScheduler | None",
    config: "WikiIntegrationConfig",
) -> None:
    """Flush the rollup worker and forward the result to the curator.

    This runs inside an ``asyncio.create_task`` so the IdleEntered handler
    returns immediately and the event bus is never blocked (AP-4).
    """
    from jarvis.memory.wiki.session_rollup import SessionRollupResult

    log.debug("wiki_integration: flushing session rollup")
    try:
        result: SessionRollupResult = await worker.flush_session()
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki_integration: flush_session() raised %s", exc)
        return

    if result.status != "ok":
        log.info(
            "wiki_integration: rollup status=%s episode_count=%d — skipping curator",
            result.status,
            result.episode_count,
        )
        return

    if result.page_path is None:
        log.info("wiki_integration: rollup returned no page_path — skipping curator")
        return

    # Read the written page content so we can feed it to the curator.
    page_path = result.page_path
    try:
        content = await asyncio.to_thread(page_path.read_text, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "wiki_integration: could not read rolled-up page %s: %s",
            page_path,
            exc,
        )
        return

    source_label = f"session:{page_path.stem}"
    log.info(
        "wiki_integration: forwarding session page %s (%d chars) to curator",
        page_path.name,
        len(content),
    )

    if scheduler is not None:
        # Agent D's scheduler handles locking + cooldown.
        try:
            from jarvis.memory.wiki.scheduler import TriggerSource
            await scheduler.trigger(
                TriggerSource.SESSION_END,
                episode_paths=None,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("wiki_integration: scheduler.trigger() raised %s", exc)
    else:
        # Fallback: direct curator ingest.
        try:
            write_result = await curator.ingest(
                source_content=content,
                source_label=source_label,
            )
            log.info(
                "wiki_integration: curator.ingest() applied=%d skipped=%d failed=%d",
                len(write_result.applied),
                len(write_result.skipped_due_to_recent_edit),
                len(write_result.failed_validation),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("wiki_integration: curator.ingest() raised %s", exc)


def _log_task_result(task: "asyncio.Task[Any]") -> None:
    """Log any unhandled exception from the background flush task."""
    if task.cancelled():
        log.debug("wiki_integration: flush task was cancelled")
        return
    exc = task.exception()
    if exc is not None:
        log.warning("wiki_integration: flush task raised unhandled exception: %s", exc)


def _ensure_schema_present(vault_root: Path, schema_path: Path) -> None:
    """Seed ``vault_root/schema.md`` from the in-package template if absent.

    Audit-7 (CRIT-4, 2026-05-17) found this file silently missing from
    every fresh / migrated vault on the user's machine. Without it,
    :meth:`WikiCuratorLLM._load_schema` returns None and downstream code
    swallowed the absence into empty result lists, which presents to the
    user as "merk dir das" lying about persistence.

    The canonical schema lives next to the wiki package at
    ``jarvis/memory/wiki/templates/schema.md`` so it ships with the
    distribution. We copy it byte-for-byte; never overwrite an existing
    file (the user may have customised theirs). Errors are logged but
    not raised -- a degraded curator path is still strictly better than
    refusing to construct the wiki integration at all.
    """
    if schema_path.is_file():
        return
    try:
        template = Path(__file__).resolve().parent / "templates" / "schema.md"
        if not template.is_file():
            log.warning(
                "wiki.integration: schema template missing at %s -- vault "
                "%s will operate without a binding schema",
                template, vault_root,
            )
            return
        vault_root.mkdir(parents=True, exist_ok=True)
        schema_path.write_text(
            template.read_text(encoding="utf-8"), encoding="utf-8",
        )
        log.info(
            "wiki.integration: seeded schema.md from template at %s -> %s",
            template, schema_path,
        )
    except OSError as exc:
        log.warning(
            "wiki.integration: failed to seed schema.md at %s: %s",
            schema_path, exc,
        )


def _build_curator(
    *,
    repo: "PageRepository",
    vault_root: Path,
    brain_caller: Callable[[str, str], Awaitable[str]] | None,
    root_config: Any | None = None,
) -> Any:
    """Construct a :class:`~jarvis.memory.wiki.curator.WikiCurator` instance.

    All component classes are imported lazily so this module can be imported
    even when the wiki sub-package is partially built.
    """
    from jarvis.memory.wiki.atomic_writer import AtomicWriter
    from jarvis.memory.wiki.curator import WikiCurator
    from jarvis.memory.wiki.curator_llm import WikiCuratorLLM
    from jarvis.memory.wiki.db_path import resolve_wiki_db_path
    from jarvis.memory.wiki.log_writer import LogWriter
    from jarvis.memory.wiki.vault_index import VaultIndex

    if root_config is None:
        root_config = _load_root_config()

    db_path = resolve_wiki_db_path(root_config.memory.data_dir)
    backup_dir = vault_root.parent / "wiki-backups"
    writer = AtomicWriter(
        vault_root=vault_root,
        backup_dir=backup_dir,
        db_path=db_path,
    )
    log_writer = LogWriter(log_path=vault_root / "log.md")
    vault = VaultIndex(repo=repo)

    schema_path = vault_root / "schema.md"
    # CRIT-4 (2026-05-17 audit-7): missing schema.md silently broke every
    # wiki-ingest call -- WikiCuratorLLM._load_schema returned None,
    # downstream returned [] in many code paths, and "merk dir das" lied
    # to the user about persisting anything. Seed the file from the
    # canonical template if the vault is missing it; safe for fresh
    # installs and for vaults that lost the file through a manual
    # cleanup.
    _ensure_schema_present(vault_root, schema_path)
    llm = WikiCuratorLLM(
        config=root_config,
        schema_path=schema_path,
        log_path=vault_root / "log.md",
    )

    return WikiCurator(
        repo=repo,
        vault=vault,
        writer=writer,
        llm=llm,
        log_writer=log_writer,
        vault_root=vault_root,
    )


def kick_journal_backlog(journal: Any, scheduler: Any) -> None:
    """Boot-time backlog drain (Wave-2 C1).

    The journal-pressure trigger only fires once ``consolidate_after_candidates``
    facts pile up — a small leftover (1..7 candidates from the previous run)
    would otherwise sit pending until enough NEW conversation arrives. At boot
    we fire one JOURNAL trigger whenever anything is pending; the scheduler's
    cooldown + lock still gate the actual run. Fire-and-forget (AP-9) and
    fully guarded — boot never blocks or breaks on this.
    """
    try:
        if journal is None or scheduler is None:
            return
        backlog = journal.backlog_count()
        _record_backlog_health(backlog)
        if backlog <= 0:
            return
        from jarvis.memory.wiki.scheduler import fire_journal_trigger

        fire_journal_trigger(
            scheduler,
            name="wiki-journal-boot-drain",
            log_context="boot journal drain",
        )
        log.info(
            "wiki_integration: boot drain triggered for %d pending candidate(s)",
            backlog,
        )
    except RuntimeError:
        log.debug("wiki_integration: no event loop for the boot journal drain")
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki_integration: boot journal drain skipped: %s", exc)


def _should_age_flush(oldest_ms: int | None, now_ms: int, max_age_min: int) -> bool:
    """Pure decision for the age-based flush (spec A4).

    ``True`` only when the age flush is enabled (``max_age_min > 0``),
    something is pending (``oldest_ms is not None``), and the oldest pending
    candidate is at least ``max_age_min`` minutes old. Extracted from the
    loop so the age/enable/empty logic is unit-testable without a clock.
    """
    if max_age_min <= 0 or oldest_ms is None:
        return False
    age_min = (now_ms - oldest_ms) / 60_000
    return age_min >= max_age_min


async def _journal_age_flush_loop(journal: Any, scheduler: Any, sched_cfg: Any) -> None:
    """Fire a JOURNAL trigger when the oldest pending candidate exceeds the
    configured age (spec A4).

    Below-threshold backlogs on a quiet install never cross
    ``consolidate_after_candidates`` on their own — this loop is the
    backstop that still gets them written, the below-threshold counterpart
    to the extractor's count-gated trigger (both go through the shared
    :func:`~jarvis.memory.wiki.scheduler.fire_journal_trigger`). Runs off
    the voice hot path (AP-9) and was started off the boot critical path
    (AP-26); cancelled in :meth:`WikiIntegrationHandle.shutdown` exactly
    like the hourly telemetry loop. Never raises — a check failure is
    logged and the loop keeps polling.
    """
    from jarvis.memory.wiki.scheduler import fire_journal_trigger

    max_age_min = int(getattr(sched_cfg, "flush_pending_max_age_minutes", 10))
    if max_age_min <= 0:
        log.debug("wiki_integration: age-based journal flush disabled (max_age<=0)")
        return
    while True:
        await asyncio.sleep(120)
        try:
            oldest = journal.oldest_pending_ms()
            if _should_age_flush(oldest, int(time.time() * 1000), max_age_min):
                fire_journal_trigger(
                    scheduler,
                    name="wiki-journal-age-flush-trigger",
                    log_context="age-based journal flush",
                )
        except Exception:  # noqa: BLE001 — never kill the loop
            log.debug("wiki_integration: journal age flush check failed", exc_info=True)


def _record_backlog_health(count: int) -> None:
    """Record the journal backlog on the health singleton (spec A5).

    Recording must never raise into the boot/pressure-check path (AP-9).
    """
    try:
        from jarvis.memory.wiki.health import health

        health.record_backlog(count)
    except Exception:  # noqa: BLE001 — health recording must never break the pipeline
        log.debug("wiki_integration: health.record_backlog failed", exc_info=True)


def _load_root_config() -> Any:
    """Load the root ``JarvisConfig`` once (default config on failure).

    The fallback is logged at WARNING — a silently swallowed load failure
    would let the curator/rollup stack run on default provider settings
    with no diagnosis trail.
    """
    try:
        from jarvis.core.config import load_config
        return load_config()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "wiki_integration: load_config() failed (%s) — using default "
            "JarvisConfig for the rollup/curator stack",
            exc,
        )
        from jarvis.core.config import JarvisConfig
        return JarvisConfig()


def _build_rollup_worker(
    *,
    repo: "PageRepository",
    vault_root: Path,
    bus: "EventBus",
    root_config: Any | None = None,
) -> Any:
    """Construct a :class:`~jarvis.memory.wiki.session_rollup.SessionRollupWorker`.

    Opens or reuses the default recall store from the configured data
    directory. ``root_config`` lets the caller hand in an already-loaded
    snapshot (single source for the D2 gate AND the worker); when ``None``
    the config is loaded here.
    """
    from jarvis.memory.wiki.atomic_writer import AtomicWriter
    from jarvis.memory.wiki.log_writer import LogWriter
    from jarvis.memory.wiki.session_rollup import SessionRollupWorker

    if root_config is None:
        root_config = _load_root_config()
    from jarvis.memory.wiki.db_path import resolve_wiki_db_path

    db_path = resolve_wiki_db_path(root_config.memory.data_dir)
    backup_dir = vault_root.parent / "wiki-backups"
    writer = AtomicWriter(
        vault_root=vault_root,
        backup_dir=backup_dir,
        db_path=db_path,
    )
    log_writer = LogWriter(log_path=vault_root / "log.md")

    from jarvis.memory.recall import RecallStore
    recall = RecallStore(db_path)
    # Schedule an async open so the connection is available when the worker
    # first fires.  open() is idempotent — safe to call again if already open.
    try:
        loop = asyncio.get_event_loop()
        loop.call_soon(lambda: asyncio.ensure_future(_open_recall(recall)))
    except RuntimeError:
        # No running event loop at construction time (e.g. tests); the worker
        # will open the connection lazily when flush_session() is first called.
        pass

    return SessionRollupWorker(
        config=root_config,
        recall_store=recall,
        vault_root=vault_root,
        atomic_writer=writer,
        page_repo=repo,
        log_writer=log_writer,
        bus=bus,
    )


async def _open_recall(recall: Any) -> None:
    """Ensure the recall store connection is open."""
    try:
        await recall.open()
    except Exception as exc:  # noqa: BLE001
        log.warning("wiki_integration: RecallStore.open() failed: %s", exc)
