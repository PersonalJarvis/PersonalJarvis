"""Track outstanding registry operations and live stores before retiring pools."""

from __future__ import annotations

import logging
import threading
import time
import weakref
from collections import deque
from collections.abc import Callable
from contextlib import contextmanager
from functools import wraps
from typing import Any

from jarvis.swarm.store import SwarmAccessError

log = logging.getLogger(__name__)
_PENDING_CLEANUP = threading.BoundedSemaphore(16)


class _RetirementQueue:
    """One bounded cleanup lane whose non-daemon worker exits whenever it drains."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: deque[Callable[[], None]] = deque()
        self._thread: threading.Thread | None = None
        self._accepting = False

    def submit(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if not self._accepting:
                previous = self._thread
                if previous is not None and previous is not threading.current_thread():
                    # A draining worker has released this lock and only has to return.
                    # Join it before creating its successor; cleanup threads never overlap.
                    previous.join()
                self._queue.append(callback)
                self._accepting = True
                worker = threading.Thread(target=self._run, name="swarm-retire_0", daemon=False)
                self._thread = worker
                try:
                    worker.start()
                except RuntimeError:
                    self._accepting = False
                    self._queue.pop()
                    self._thread = previous
                    raise
            else:
                self._queue.append(callback)

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._accepting = False
                    return
                callback = self._queue.popleft()
            try:
                callback()
            except Exception:  # noqa: BLE001 - one failed finalizer cannot strand queued cleanup
                log.exception("Deferred Swarm cleanup failed")
            finally:
                # Release callback-owned references before deciding that the queue drained.
                # A resulting store finalizer can safely append another cleanup operation.
                del callback

    def wait(self, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while True:
            with self._lock:
                worker = self._thread
                if worker is None or not worker.is_alive():
                    return True
            if worker is threading.current_thread():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            worker.join(remaining)


_CLEANUP = _RetirementQueue()


def wait_for_retirement(timeout_s: float = 10.0) -> bool:
    """Join cleanup threads; future late store releases may start a short-lived worker."""
    return _CLEANUP.wait(timeout_s)


class RegistryLifetime:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._local = threading.local()
        self._stores = 0
        self._operations = 0
        self.retired = False
        self.closed = False
        self._cleanup: Any = None
        self._cleanup_scheduled = False

    def on_unused(self, callback: Any) -> None:
        self._cleanup = weakref.WeakMethod(callback)

    def _schedule_cleanup(self) -> None:
        with self._lock:
            if (
                not self.retired
                or self.closed
                or self._stores
                or self._operations
                or self._cleanup is None
                or self._cleanup_scheduled
            ):
                return
            if not _PENDING_CLEANUP.acquire(blocking=False):
                return  # The bounded service maintenance pass retries this registry.
            self._cleanup_scheduled = True

        def close() -> None:
            try:
                callback = self._cleanup()
                if callback is not None:
                    callback()
            finally:
                with self._lock:
                    self._cleanup_scheduled = False
                _PENDING_CLEANUP.release()

        try:
            _CLEANUP.submit(close)
        except RuntimeError:
            # Interpreter shutdown already owns process cleanup; never raise from a finalizer.
            with self._lock:
                self._cleanup_scheduled = False
            _PENDING_CLEANUP.release()
            log.debug("Deferred Swarm cleanup reached interpreter shutdown")

    def track(self, store: Any, *, inherited: bool = False) -> None:
        with self._lock:
            if self.closed or (
                self.retired
                and not getattr(self._local, "depth", 0)
                and not (inherited and self._stores)
            ):
                raise SwarmAccessError("Distributed registry was replaced; refresh its settings")
            self._stores += 1
        weakref.finalize(store, self._release_store)

    def _release_store(self) -> None:
        # Destructors only release a reference; network cleanup belongs to maintenance.
        with self._lock:
            self._stores -= 1
        self._schedule_cleanup()

    @contextmanager
    def operation(self):
        with self._lock:
            depth = getattr(self._local, "depth", 0)
            if self.closed or (self.retired and not depth):
                raise SwarmAccessError("Distributed registry was replaced; refresh its settings")
            self._operations += 1
            self._local.depth = depth + 1
        try:
            yield
        finally:
            with self._lock:
                self._operations -= 1
                self._local.depth -= 1
            self._schedule_cleanup()

    def retire(self) -> None:
        with self._lock:
            self.retired = True
        # The retiring owner immediately collects stores that are already idle.
        # Only a later reference/operation release needs deferred cleanup.

    def claim_close(self) -> bool:
        """Reserve cleanup once, only after all stores and operations release ownership."""
        with self._lock:
            if self.closed or not self.retired or self._stores or self._operations:
                return False
            self.closed = True
            return True


def registry_operation(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self.lifetime.operation():
            return method(self, *args, **kwargs)

    return guarded
