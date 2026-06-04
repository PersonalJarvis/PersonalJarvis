"""WikiWatcher — filesystem observer for the Obsidian vault.

Watches the wiki vault root for ``.md`` file changes in the four content
folders (``entities/``, ``concepts/``, ``projects/``, ``sessions/``) and
publishes :class:`jarvis.core.events.WikiPageChanged` events on the
provided :class:`~jarvis.core.bus.EventBus`. Events are debounced
per-path with a 500 ms window so that a curator burst that writes ten
pages in <300 ms produces ten events, not fifty.

The class is started with :meth:`WikiWatcher.start` (synchronous, captures
the running asyncio loop) and torn down with :meth:`WikiWatcher.shutdown`
(async, awaits clean observer-thread join).

Design notes
------------
- Native ``watchdog.observers.Observer`` is used; on Windows-NTFS the
  native ReadDirectoryChangesW backend produces sub-millisecond events,
  which is why the briefing forbids ``PollingObserver``.
- ``FileMovedEvent`` is expanded into two emissions (``deleted`` at the
  source path, ``created`` at the destination) so the frontend has a
  uniform event shape.
- Paths are normalised to vault-relative POSIX so the JSON payload looks
  identical regardless of operating system.
- Cross-thread publish: the observer fires from its own thread; we use
  ``asyncio.run_coroutine_threadsafe`` to schedule the publish on the
  loop we captured in :meth:`start` (never in ``__init__`` — see
  AGENT-D §6).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jarvis.core.events import WikiPageChanged

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus

# watchdog is a hard dependency for this module (already in requirements.txt),
# but we tolerate its absence at import time so the server can boot on a
# minimal install where the wiki view is disabled.
try:
    from watchdog.events import (  # type: ignore[import-not-found]
        FileSystemEventHandler,
    )
    from watchdog.observers import Observer  # type: ignore[import-not-found]
    _HAVE_WATCHDOG = True
except Exception:  # pragma: no cover - hard dep on platform
    FileSystemEventHandler = object  # type: ignore[assignment,misc]
    Observer = None  # type: ignore[assignment,misc]
    _HAVE_WATCHDOG = False


log = logging.getLogger(__name__)


# Sub-folders we forward events for. Matches the four kinds the
# PageRepository recognises. ``_archive``, ``attachments`` and
# ``99-templates`` are intentionally excluded.
WATCHED_SUBDIRS: tuple[str, ...] = ("entities", "concepts", "projects", "sessions")

# Debounce window in milliseconds. A curator ingest that writes
# 10-15 pages in <300 ms should produce one event burst (one per path),
# not ten duplicates per path.
DEFAULT_DEBOUNCE_MS = 500


class WikiWatcher:
    """Watch a vault root and publish ``WikiPageChanged`` events.

    Parameters
    ----------
    vault_root:
        Absolute path to the Obsidian vault directory. The four
        :data:`WATCHED_SUBDIRS` are expected to live under this root.
        Subdirectories that do not exist are created on :meth:`start`
        so a fresh vault still wires up cleanly.
    bus:
        The application-wide :class:`~jarvis.core.bus.EventBus`. Must be
        the shared instance (AP-6 — never construct a new one).
    debounce_ms:
        Per-path debounce window. Defaults to 500 ms which is the value
        documented in the binding briefing.
    """

    def __init__(
        self,
        vault_root: Path,
        bus: "EventBus",
        debounce_ms: int = DEFAULT_DEBOUNCE_MS,
    ) -> None:
        self.vault_root = Path(vault_root)
        self.bus = bus
        self._debounce_s = max(0.0, debounce_ms / 1000.0)

        self._observer: Any | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        # Per-path debounce: {absolute_path: (Timer, latest_kind)}.
        self._timers_lock = threading.Lock()
        self._timers: dict[Path, tuple[threading.Timer, str]] = {}
        self._shutting_down = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Start the underlying ``watchdog.Observer``.

        Returns ``True`` on success, ``False`` when watchdog is not
        available or the vault root is missing. The caller's startup
        path must wrap this in a ``try/except`` so an empty vault
        cannot block the desktop app boot.
        """
        if not _HAVE_WATCHDOG:
            log.info("wiki_watcher: watchdog not installed — live-reload disabled")
            return False
        if not self.vault_root.exists():
            log.warning(
                "wiki_watcher: vault_root does not exist: %s — live-reload disabled",
                self.vault_root,
            )
            return False

        # Capture the running event loop *here*, not in __init__. The
        # bus.publish() coroutine must be scheduled on the loop that
        # owns the bus subscribers.
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = asyncio.get_event_loop()

        # Make sure the four content folders exist so the observer
        # has something to watch. A vault that was just bootstrapped
        # may legitimately be missing them.
        for sub in WATCHED_SUBDIRS:
            (self.vault_root / sub).mkdir(parents=True, exist_ok=True)

        try:
            observer = Observer()  # type: ignore[operator]
        except Exception as exc:  # noqa: BLE001
            log.warning("wiki_watcher: could not construct Observer: %s", exc)
            return False

        handler = _WatchdogBridge(self)

        # Schedule one observer entry per sub-folder so a non-recursive
        # vault-root listing (e.g. log.md, schema.md) is excluded from
        # the stream. ``recursive=True`` is fine because the wiki sub-
        # folders themselves are flat in practice, but we keep the option
        # in case sessions/ later gets year-buckets.
        for sub in WATCHED_SUBDIRS:
            target = self.vault_root / sub
            try:
                observer.schedule(handler, str(target), recursive=True)
            except FileNotFoundError as exc:
                log.warning(
                    "wiki_watcher: could not schedule %s: %s", target, exc
                )
            except PermissionError as exc:
                log.warning(
                    "wiki_watcher: permission denied for %s: %s", target, exc
                )

        observer.daemon = True
        try:
            observer.start()
        except Exception as exc:  # noqa: BLE001
            log.warning("wiki_watcher: observer.start() failed: %s", exc)
            return False

        self._observer = observer
        log.info(
            "wiki_watcher: started — vault=%s (debounce=%dms)",
            self.vault_root,
            int(self._debounce_s * 1000),
        )
        return True

    async def shutdown(self) -> None:
        """Stop the observer thread and cancel pending debounce timers."""
        # Latch the shutdown flag first so any observer-thread event that
        # races with us drops in `_handle_event` instead of scheduling a
        # new timer after we already cleared the dict.
        self._shutting_down = True
        # Cancel any in-flight debounce timers — they would otherwise
        # try to publish on a closing loop.
        with self._timers_lock:
            timers = list(self._timers.items())
            self._timers.clear()
        for _path, (timer, _kind) in timers:
            try:
                timer.cancel()
            except Exception:  # noqa: BLE001
                pass

        observer = self._observer
        if observer is None:
            return
        try:
            observer.stop()
        except Exception as exc:  # noqa: BLE001
            log.debug("wiki_watcher: observer.stop() raised: %s", exc)
        # The observer thread is daemon, but we want a clean join so
        # background events can't leak into the next test.
        try:
            await asyncio.to_thread(observer.join, 2.0)
        except Exception as exc:  # noqa: BLE001
            log.debug("wiki_watcher: observer.join() raised: %s", exc)
        self._observer = None
        log.info("wiki_watcher: shutdown complete")

    # ------------------------------------------------------------------
    # Event ingestion (called by the watchdog bridge thread)
    # ------------------------------------------------------------------

    def _handle_event(self, raw_path: str, kind: str) -> None:
        """Dispatch a single raw watchdog event after filtering + debounce.

        ``raw_path`` is the absolute path reported by watchdog; ``kind``
        is one of ``"created" | "modified" | "deleted"``. This method is
        called from the observer thread — keep it short and never await.
        """
        if self._shutting_down:
            return
        try:
            abs_path = Path(raw_path)
        except (TypeError, ValueError):
            return

        # Filter: only .md files.
        if abs_path.suffix.lower() != ".md":
            return

        # Filter: must be under a watched sub-folder.
        if not self._is_under_watched_subdir(abs_path):
            return

        # Per-path debounce. If a timer already exists for this path,
        # cancel it; the latest event wins, with one caveat: a delete
        # after a modify should still surface as "deleted".
        with self._timers_lock:
            existing = self._timers.get(abs_path)
            if existing is not None:
                try:
                    existing[0].cancel()
                except Exception:  # noqa: BLE001
                    pass
            # "deleted" wins over "modified" if both fire in the window,
            # because reading the file would race with the deletion.
            # "created" wins over "modified" similarly (a fresh file's
            # first modify is part of its creation burst).
            prev_kind = existing[1] if existing is not None else None
            effective_kind = self._reconcile_kinds(prev_kind, kind)
            timer = threading.Timer(
                self._debounce_s,
                self._fire,
                args=(abs_path, effective_kind),
            )
            timer.daemon = True
            self._timers[abs_path] = (timer, effective_kind)
        timer.start()

    @staticmethod
    def _reconcile_kinds(prev: str | None, current: str) -> str:
        """Pick the kind to use when a path fires multiple times.

        Priority: ``deleted`` > ``created`` > ``modified``. The deletion
        win prevents the watcher emitting a stale ``modified`` event for
        a path that no longer exists by the time the debounce expires.
        ``created`` outranks ``modified`` because an editor's "save"
        often produces a ``modified`` immediately after a ``created`` in
        the same burst.
        """
        if prev is None:
            return current
        if "deleted" in (prev, current):
            return "deleted"
        if "created" in (prev, current):
            return "created"
        return current

    def _is_under_watched_subdir(self, abs_path: Path) -> bool:
        """Return True iff ``abs_path`` lives under one of the watched dirs."""
        try:
            rel = abs_path.resolve().relative_to(self.vault_root.resolve())
        except ValueError:
            return False
        parts = rel.parts
        if not parts:
            return False
        return parts[0] in WATCHED_SUBDIRS

    def _fire(self, abs_path: Path, kind: str) -> None:
        """Debounced emit — called by the threading.Timer thread."""
        # Belt-and-suspenders against the shutdown race: a Timer that
        # was started just before shutdown() can still call _fire even
        # after timer.cancel() if cancel arrived too late.
        if self._shutting_down:
            return
        # Pop our own entry so the next event for this path starts fresh.
        with self._timers_lock:
            self._timers.pop(abs_path, None)

        try:
            rel = abs_path.resolve().relative_to(self.vault_root.resolve())
        except (FileNotFoundError, ValueError):
            # For "deleted" events, abs_path.resolve() may raise on the
            # missing path. Fall back to lexical relativisation.
            try:
                rel = Path(abs_path).relative_to(self.vault_root)
            except ValueError:
                log.debug(
                    "wiki_watcher: path %s not under vault_root %s — skipping",
                    abs_path,
                    self.vault_root,
                )
                return

        path_posix = rel.as_posix()
        slug = abs_path.stem

        event = WikiPageChanged(
            slug=slug,
            path=path_posix,
            kind=kind,
        )

        loop = self._loop
        if loop is None:
            log.debug("wiki_watcher: no event loop captured — dropping event")
            return
        try:
            asyncio.run_coroutine_threadsafe(self.bus.publish(event), loop)
        except RuntimeError as exc:
            # Loop already closed (shutdown race) — log and move on.
            log.debug("wiki_watcher: publish dropped (loop closed): %s", exc)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "wiki_watcher_event_failed",
                exc_info=True,
                extra={"path": path_posix, "kind": kind, "error": str(exc)},
            )


class _WatchdogBridge(FileSystemEventHandler):  # type: ignore[misc]
    """Adapter from watchdog's per-event callbacks to ``WikiWatcher``."""

    def __init__(self, watcher: WikiWatcher) -> None:
        super().__init__()
        self._watcher = watcher

    def on_created(self, event: Any) -> None:  # noqa: D401
        if getattr(event, "is_directory", False):
            return
        src = getattr(event, "src_path", None)
        if src:
            self._watcher._handle_event(str(src), "created")  # noqa: SLF001

    def on_modified(self, event: Any) -> None:  # noqa: D401
        if getattr(event, "is_directory", False):
            return
        src = getattr(event, "src_path", None)
        if src:
            self._watcher._handle_event(str(src), "modified")  # noqa: SLF001

    def on_deleted(self, event: Any) -> None:  # noqa: D401
        if getattr(event, "is_directory", False):
            return
        src = getattr(event, "src_path", None)
        if src:
            self._watcher._handle_event(str(src), "deleted")  # noqa: SLF001

    def on_moved(self, event: Any) -> None:  # noqa: D401
        if getattr(event, "is_directory", False):
            return
        src = getattr(event, "src_path", None)
        dst = getattr(event, "dest_path", None)
        if src:
            self._watcher._handle_event(str(src), "deleted")  # noqa: SLF001
        if dst:
            self._watcher._handle_event(str(dst), "created")  # noqa: SLF001


__all__ = ["WikiWatcher", "WATCHED_SUBDIRS", "DEFAULT_DEBOUNCE_MS"]
