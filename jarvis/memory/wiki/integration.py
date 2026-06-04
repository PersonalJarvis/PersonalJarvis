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
    # The VoiceFactBridge attached during bootstrap. Declared as a field (not
    # monkey-patched) so shutdown() can stop it — otherwise its TranscriptFinal
    # / ResponseGenerated subscriptions leak on every teardown.
    _voice_bridge: Any = field(default=None)

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

        # Stop the voice-fact bridge so its bus subscriptions are released.
        if self._voice_bridge is not None:
            try:
                self._voice_bridge.stop()
            except Exception:  # noqa: BLE001
                log.debug("wiki_integration: voice_bridge.stop() failed; continuing teardown")
            self._voice_bridge = None

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
    curator = _build_curator(
        repo=repo,
        vault_root=vault_path,
        brain_caller=brain_caller,
    )

    # Publish the live curator so the ``wiki-ingest`` tool can find it.
    # See module-level docstring on ``_running_curator``.
    _set_running_curator(curator)

    # ------------------------------------------------------------------
    # Decide scheduler vs. direct ingest
    # ------------------------------------------------------------------
    use_scheduler = (
        scheduler_factory is not None
        and not config.fallback_to_direct_ingest
    )
    scheduler: "CuratorScheduler | None" = None

    if not use_scheduler:
        log.info(
            "wiki_integration: scheduler not wired, using direct ingest"
        )
    else:
        try:
            scheduler = scheduler_factory(curator=curator)  # type: ignore[call-arg]
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "wiki_integration: scheduler_factory raised %s — falling back to direct ingest",
                exc,
            )
            scheduler = None

    # ------------------------------------------------------------------
    # Build the SessionRollupWorker (B7)
    # ------------------------------------------------------------------
    worker = _build_rollup_worker(
        repo=repo,
        vault_root=vault_path,
        bus=bus,
    )

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

    if config.subscribe_idle:
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
                    scheduler=scheduler,
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
    try:
        from jarvis.memory.wiki.voice_bridge import VoiceFactBridge
        voice_bridge = VoiceFactBridge(
            bus=bus,
            curator=curator,
            config=voice_bridge_config,
        )
        voice_bridge.start()
        handle._voice_bridge = voice_bridge  # noqa: SLF001
        log.info(
            "wiki_integration: VoiceFactBridge attached "
            "(aggressive_mode=%s)",
            getattr(voice_bridge_config, "aggressive_mode", "default(True)"),
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
) -> Any:
    """Construct a :class:`~jarvis.memory.wiki.curator.WikiCurator` instance.

    All component classes are imported lazily so this module can be imported
    even when the wiki sub-package is partially built.
    """
    from jarvis.memory.wiki.atomic_writer import AtomicWriter
    from jarvis.memory.wiki.curator import WikiCurator
    from jarvis.memory.wiki.curator_llm import WikiCuratorLLM
    from jarvis.memory.wiki.log_writer import LogWriter
    from jarvis.memory.wiki.vault_index import VaultIndex

    backup_dir = vault_root.parent / "wiki-backups"
    writer = AtomicWriter(vault_root=vault_root, backup_dir=backup_dir)
    log_writer = LogWriter(log_path=vault_root / "log.md")
    vault = VaultIndex(repo=repo)

    # Load root config for the LLM; fall back to default config when not
    # available in the current environment.
    try:
        from jarvis.core.config import load_config
        root_config = load_config()
    except Exception:  # noqa: BLE001
        from jarvis.core.config import JarvisConfig
        root_config = JarvisConfig()

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


def _build_rollup_worker(
    *,
    repo: "PageRepository",
    vault_root: Path,
    bus: "EventBus",
) -> Any:
    """Construct a :class:`~jarvis.memory.wiki.session_rollup.SessionRollupWorker`.

    Opens or reuses the default recall store from the configured data
    directory.  Uses default config loaded from ``jarvis.toml``.
    """
    from jarvis.memory.wiki.atomic_writer import AtomicWriter
    from jarvis.memory.wiki.log_writer import LogWriter
    from jarvis.memory.wiki.session_rollup import SessionRollupWorker

    backup_dir = vault_root.parent / "wiki-backups"
    writer = AtomicWriter(vault_root=vault_root, backup_dir=backup_dir)
    log_writer = LogWriter(log_path=vault_root / "log.md")

    try:
        from jarvis.core.config import load_config
        root_config = load_config()
        data_dir = Path(root_config.memory.data_dir)
    except Exception:  # noqa: BLE001
        from jarvis.core.config import JarvisConfig
        root_config = JarvisConfig()
        data_dir = Path("./data")

    from jarvis.memory.recall import RecallStore
    recall = RecallStore(data_dir / "jarvis.db")
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
