"""FileSystemProbe — watchdog-based FS watcher for active project roots.

Lifecycle:
1. ``__init__(*, bus)`` — Observer instance + internal state init.
2. ``await start()`` — starts the watchdog observer thread.
3. ``watch(repo_root)`` — adds a watch for a project root (up to MAX_ROOTS).
4. ``unwatch(repo_root)`` — removes a watch.
5. ``await stop()`` — Observer.stop() + join (timeout 1 s).

Hard Negatives §9:
- NEVER recurse over C:\\ — ALWAYS scoped to the project root
  (Watch.recursive=True is fine for a project tree, but ONLY for the
  project root, NOT for the system root)
- Bus publish from the watchdog thread MUST NOT be done directly —
  use loop.call_soon_threadsafe instead
- Probe errors MUST NOT propagate — handler swallows and logs

Debounce:
- Editors (VS Code) often emit multiple events per save (atomic save:
  temp file + rename, or multi-write). We debounce per path with a
  200 ms window: within 200 ms only the LAST event counts.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus

logger = logging.getLogger(__name__)

_DEBOUNCE_S: float = 0.2
_MAX_WATCHED_ROOTS_DEFAULT: int = 10    # Safety cap against unbounded growth
# Backward-compat alias for existing test imports
_MAX_WATCHED_ROOTS: int = _MAX_WATCHED_ROOTS_DEFAULT
_STOP_TIMEOUT_S: float = 1.0
_PATHS_BLACKLIST_PREFIXES: tuple[str, ...] = (
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "dist", "build", ".idea", ".vscode",
)
# Codex-MAJOR-4-Fix (2026-04-26): hard roots that must NEVER be watched.
# On Windows: drive roots, %WINDIR%, %PROGRAMFILES%, home folder.
# Block-pattern instead of allow-pattern because A5-Lite does not know which
# project roots the user has — the safer default is "forbid what is clearly dangerous".
_FORBIDDEN_ROOT_PATTERNS: tuple[str, ...] = (
    "c:\\windows", "c:\\program files", "c:\\program files (x86)",
    "c:\\programdata", "c:\\users\\public",
    "/", "/usr", "/etc", "/var", "/sys", "/proc",
)


def _is_forbidden_root(root: str) -> bool:
    """True if root is a forbidden system/home folder.

    Blocks: drive root (e.g. C:\\), $env:WINDIR, $env:PROGRAMFILES,
    $env:USERPROFILE directly (subfolders allowed), Linux system roots.
    """
    try:
        p = Path(root).resolve()
    except (OSError, ValueError):
        return True    # invalid root → block
    p_str = str(p).lower().rstrip("\\/")
    # Forbid drive root (e.g. "C:" or "C:\\")
    if len(p_str) <= 3 and (p_str.endswith(":") or p_str.endswith(":\\")):
        return True
    # Home folder DIRECTLY (subfolders allowed, because project roots may live there)
    try:
        home = str(Path.home().resolve()).lower().rstrip("\\/")
        if p_str == home:
            return True
    except (OSError, RuntimeError):
        pass
    # System folder patterns
    for forbidden in _FORBIDDEN_ROOT_PATTERNS:
        if p_str == forbidden.rstrip("\\/") or p_str.startswith(forbidden + "\\"):
            return True
    return False


class FileSystemProbe:
    """Watchdog-Wrapper. Probe-Methode liefert open_file_hint."""

    name: str = "filesystem"

    def __init__(
        self,
        *,
        bus: EventBus,
        max_watched_roots: int = _MAX_WATCHED_ROOTS_DEFAULT,
    ) -> None:
        self._bus = bus
        self._loop: asyncio.AbstractEventLoop | None = None
        # Lazy import so that the module remains importable without watchdog
        # (CI without watchdog is possible).
        self._observer: Any = None
        self._watches: dict[str, Any] = {}    # repo_root -> watch handle
        self._lock = threading.Lock()
        # Debounce + last-modified tracking
        self._last_emit: dict[str, float] = {}
        self._latest_in_root: dict[str, str] = {}    # repo_root -> last seen path
        self._started: bool = False
        # Codex-Claude-MAJOR-M1-Fix (2026-04-26): max-roots from config DI instead of
        # hardcoded module constant. Default stays 10, but jarvis.toml can override
        # via [awareness.probes].fs_max_watched_roots.
        self._max_watched_roots: int = max_watched_roots
        # Code-reviewer-MAJOR-M2-Fix (2026-04-26): asyncio.create_task without
        # a reference hold risks garbage collection mid-flight (silent
        # event loss). We keep a set + add_done_callback for cleanup.
        self._pending_publish_tasks: set[asyncio.Task[Any]] = set()

    # ---- Lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Starts the watchdog observer thread. Idempotent. No-op if watchdog is missing."""
        if self._started:
            return
        try:
            from watchdog.observers import Observer  # noqa: PLC0415
        except ImportError:
            logger.warning("watchdog nicht installiert — FileSystemProbe ist no-op")
            self._started = True
            return
        self._loop = asyncio.get_running_loop()
        self._observer = Observer()
        self._observer.start()
        self._started = True

    async def stop(self) -> None:
        """Observer.stop() + join. Idempotent."""
        if not self._started:
            return
        self._started = False
        observer = self._observer
        self._observer = None
        if observer is None:
            with self._lock:
                self._watches.clear()
                self._last_emit.clear()
                self._latest_in_root.clear()
            return
        try:
            observer.stop()
            await asyncio.to_thread(observer.join, _STOP_TIMEOUT_S)
        except Exception:    # noqa: BLE001
            logger.debug("FileSystemProbe stop failed", exc_info=True)
        with self._lock:
            self._watches.clear()
            self._last_emit.clear()
            self._latest_in_root.clear()

    # ---- Watch-Management --------------------------------------------------

    def watch(self, repo_root: str) -> bool:
        """Adds a watch. Returns True if newly registered.

        If the _max_watched_roots cap is reached: returns False + warning, no crash.
        On watchdog error: returns False + warning.
        Codex-MAJOR-4-Fix: blocks drive roots, $WINDIR, $PROGRAMFILES,
        and the home folder directly.
        """
        if not self._started or self._observer is None:
            return False
        repo_root = str(Path(repo_root).resolve()) if repo_root else ""
        if not repo_root:
            return False
        # Codex-MAJOR-4-Fix (2026-04-26): reject hard-blocked roots.
        # Otherwise a bug in the context resolver could recursively watch
        # C:\ or the home folder — privacy + resource risk.
        if _is_forbidden_root(repo_root):
            logger.warning(
                "FileSystemProbe.watch: forbidden root %s blocked (system/home dir)",
                repo_root,
            )
            return False
        with self._lock:
            if repo_root in self._watches:
                return False
            if len(self._watches) >= self._max_watched_roots:
                logger.warning(
                    "FileSystemProbe: cap %d reached, dropping watch %s",
                    self._max_watched_roots, repo_root,
                )
                return False
        try:
            handler = _SaveHandler(probe=self, repo_root=repo_root)
            watch = self._observer.schedule(handler, repo_root, recursive=True)
        except (OSError, FileNotFoundError) as exc:
            logger.warning("FileSystemProbe.watch(%s) failed: %s", repo_root, exc)
            return False
        with self._lock:
            self._watches[repo_root] = watch
        return True

    def unwatch(self, repo_root: str) -> None:
        repo_root = str(Path(repo_root).resolve()) if repo_root else ""
        with self._lock:
            watch = self._watches.pop(repo_root, None)
            self._last_emit = {k: v for k, v in self._last_emit.items()
                               if not k.startswith(repo_root)}
            self._latest_in_root.pop(repo_root, None)
        if watch is not None and self._observer is not None:
            try:
                self._observer.unschedule(watch)
            except (KeyError, ValueError):
                pass

    # ---- Probe-Interface (synchroner state-read) ---------------------------

    async def probe(
        self, *, cwd: str | None, process_name: str = "",
    ) -> dict[str, Any]:
        """Returns {open_file_hint: str | None}.

        ``open_file_hint`` is the most recently modified file path within the
        project root of cwd. None if no save was seen or cwd is not watched.
        """
        if cwd is None:
            return {"open_file_hint": None}
        try:
            # Path.resolve is a filesystem metadata lookup (fast, microseconds).
            # ASYNC240 disabled: probe() runs within the 200 ms budget, which is fine.
            cwd_resolved = str(Path(cwd).resolve())  # noqa: ASYNC240
        except (OSError, ValueError):
            return {"open_file_hint": None}
        with self._lock:
            # Match against any watched root that is parent of cwd
            for root in self._watches:
                if cwd_resolved == root or cwd_resolved.startswith(root + "\\") \
                        or cwd_resolved.startswith(root + "/"):
                    return {"open_file_hint": self._latest_in_root.get(root)}
        return {"open_file_hint": None}

    # ---- Internal: Event-Handler-Bridge ------------------------------------

    def _on_save_event(self, *, path: str, repo_root: str) -> None:
        """Called from the watchdog thread — bridges onto the asyncio loop.

        Debounce: 200 ms window per path. Redundant events within the window
        are dropped. If the path is inside a blacklisted subdirectory
        (.git, __pycache__), the event is skipped.
        """
        # Blacklist filter
        rel = path[len(repo_root):].lstrip("/\\") if path.startswith(repo_root) else path
        first_segment = rel.split("/")[0].split("\\")[0]
        if first_segment in _PATHS_BLACKLIST_PREFIXES:
            return

        # Debounce
        now = time.monotonic()
        with self._lock:
            last = self._last_emit.get(path, 0.0)
            if now - last < _DEBOUNCE_S:
                return
            self._last_emit[path] = now
            self._latest_in_root[repo_root] = path

        # Bus publish via loop.call_soon_threadsafe (NEVER directly in the watchdog thread)
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(
                self._publish_save_event, path, repo_root,
            )
        except RuntimeError:
            pass

    def _publish_save_event(self, path: str, repo_root: str) -> None:
        """Executed on the asyncio loop. Schedules bus.publish.

        Code-reviewer-MAJOR-M2-Fix (2026-04-26): hold the task reference in
        ``_pending_publish_tasks`` + add_done_callback for cleanup.
        Otherwise GC can collect the task mid-flight and bus.publish is
        never awaited (silent event loss).
        """
        from jarvis.core.events import FileSaved  # noqa: PLC0415
        try:
            task = asyncio.create_task(self._bus.publish(
                FileSaved(path=path, repo_root=repo_root),
            ))
            self._pending_publish_tasks.add(task)
            task.add_done_callback(self._pending_publish_tasks.discard)
        except RuntimeError:
            pass


class _SaveHandler:
    """watchdog FileSystemEventHandler-compatible (duck-typed).

    We avoid direct inheritance from FileSystemEventHandler so that
    the top-level module import of watchdog remains optional.
    """

    def __init__(self, *, probe: FileSystemProbe, repo_root: str) -> None:
        self._probe = probe
        self._repo_root = repo_root

    def dispatch(self, event: Any) -> None:
        # watchdog calls dispatch for all events
        if event.is_directory:
            return
        if event.event_type not in ("modified", "created", "moved"):
            return
        path = getattr(event, "dest_path", None) or getattr(event, "src_path", "")
        if not path:
            return
        try:
            self._probe._on_save_event(path=path, repo_root=self._repo_root)    # noqa: SLF001
        except Exception:    # noqa: BLE001
            logger.debug("FileSystemProbe save-event-handler crashed", exc_info=True)
