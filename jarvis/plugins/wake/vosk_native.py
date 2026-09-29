"""Serialize every libvosk native call (BUG-151).

libvosk 0.3.44 is safe for many long-lived recognizers decoding at once on
one ``Model``. It is not safe under concurrent short-lived
``KaldiRecognizer`` construction + ``AcceptWaveform`` + ``FinalResult`` —
that pattern access-violates the process (Windows, 5-50 s in
``scripts/vosk_native_stress.py churn``). Partial locks (finals only,
finals exclusive vs decodes, finals + constructions exclusive) were not
reliably enough. One process-wide lock around every native call survived.

Fakes in unit tests are not native and must stay unlocked: the concurrent
grammar/free barrier test deadlocks if a lock serialises two
``FinalResult`` waits. Only real ``vosk.KaldiRecognizer`` instances are
wrapped.
"""

from __future__ import annotations

import queue
import threading
from typing import Any

_LOCK = threading.Lock()

# One worker that does nothing but let recognizers die. ``vosk_recognizer_free``
# (KaldiRecognizer.__del__) is a native call measured at 15 s on a cold, paging
# box (2026-08-30 "Event loop STALLED" stack) — and it runs on WHICHEVER thread
# drops the last reference, which was the asyncio loop during wake teardown.
#
# The hand-off is a ``SimpleQueue`` fed by a plain daemon thread, never a
# ``ThreadPoolExecutor``: the proxy's ``__del__`` runs wherever the garbage
# collector fires, including INSIDE another ``ThreadPoolExecutor.submit`` that
# already holds ``concurrent.futures``' process-wide, non-reentrant
# ``_global_shutdown_lock`` (it allocates a Thread, which can trigger a GC).
# A second ``submit`` from that ``__del__`` wanted the same lock on the same
# thread and froze the asyncio loop forever (BUG-221: wake word, API and every
# window dead, 0 % CPU). ``SimpleQueue.put`` is documented reentrant and safe in
# destructors; nothing on the ``__del__`` path may take any other lock.
_RELEASE_QUEUE: queue.SimpleQueue[list[Any]] = queue.SimpleQueue()
_RELEASE_WORKER: threading.Thread | None = None
_RELEASE_WORKER_LOCK = threading.Lock()


def _release_worker_loop() -> None:
    while True:
        box = _RELEASE_QUEUE.get()
        with _LOCK:
            box.clear()
        del box


def _ensure_release_worker() -> None:
    """Start the release worker. Never call this from ``__del__`` (it locks)."""
    global _RELEASE_WORKER
    if _RELEASE_WORKER is not None:
        return
    with _RELEASE_WORKER_LOCK:
        if _RELEASE_WORKER is None:
            worker = threading.Thread(target=_release_worker_loop, name="vosk-release", daemon=True)
            worker.start()
            _RELEASE_WORKER = worker


def _enqueue_release(box: list[Any]) -> None:
    """Lock-free hand-off of a boxed last reference — safe inside ``__del__``."""
    _RELEASE_QUEUE.put(box)


def release_recognizer(rec: Any) -> None:
    """Let *rec*'s native free run on the release worker, never on the caller.

    The last reference is moved into a box and cleared on the worker under the
    same process-wide lock every other native call holds (BUG-151 — the free is
    a native call too). A daemon worker means a recognizer still queued at
    interpreter exit is freed by the interpreter's own teardown, the one path
    where no loop is left to stall.
    """
    if rec is None:
        return
    _ensure_release_worker()
    box = [rec]
    del rec
    _enqueue_release(box)


def native_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run one libvosk constructor or method behind the process-wide lock."""
    with _LOCK:
        return fn(*args, **kwargs)


def is_native_recognizer(rec: Any) -> bool:
    """True for a real ``vosk.KaldiRecognizer``, false for test doubles."""
    cls = type(rec)
    return cls.__name__ == "KaldiRecognizer" and cls.__module__ == "vosk"


class LockedRecognizer:
    """Proxy that holds the process lock for every method on a recognizer."""

    def __init__(self, rec: Any) -> None:
        self._rec = rec
        if is_native_recognizer(rec):
            # Start the worker HERE, in ordinary code: ``__del__`` below may only
            # enqueue, because it can run inside a lock its own thread holds.
            _ensure_release_worker()

    def AcceptWaveform(self, data: Any) -> Any:  # noqa: N802 — vosk API
        with _LOCK:
            return self._rec.AcceptWaveform(data)

    def PartialResult(self) -> Any:  # noqa: N802 — vosk API
        with _LOCK:
            return self._rec.PartialResult()

    def Result(self) -> Any:  # noqa: N802 — vosk API
        with _LOCK:
            return self._rec.Result()

    def FinalResult(self) -> Any:  # noqa: N802 — vosk API
        with _LOCK:
            return self._rec.FinalResult()

    def Reset(self) -> Any:  # noqa: N802 — vosk API
        with _LOCK:
            return self._rec.Reset()

    def SetWords(self, flag: Any) -> Any:  # noqa: N802 — vosk API
        with _LOCK:
            return self._rec.SetWords(flag)

    def SetGrammar(self, grammar: Any) -> Any:  # noqa: N802 — vosk API
        # BUG-163: the first version of this proxy listed only the methods the
        # provider calls UNCONDITIONALLY. ``SetGrammar`` is probed with
        # ``getattr(rec, "SetGrammar", None)`` (older vosk builds lack it), so
        # leaving it off this class silently downgraded the acoustic
        # competition to its static grammar on every build — the free ear's
        # own hypothesis never joined the alternatives again, and bare words
        # ('power', 'pedro', 'hey nova') won the wake. The proxy must expose
        # every method the wrapped recognizer has, hence ``__getattr__`` below
        # as the backstop; this explicit method exists so the one the
        # precision rests on can never fall through to it by accident.
        with _LOCK:
            return self._rec.SetGrammar(grammar)

    def __del__(self) -> None:
        # The proxy is the ONLY holder of the native recognizer (wrap_recognizer
        # hands out the proxy, never the inner object), so this is the one
        # chokepoint where every recognizer death passes — a one-shot verify
        # discarded mid-loop, stage-1 recognizers replaced by _fresh_recs, a
        # cancelled wake task's closure. Hand the corpse to the release worker
        # so the 15 s native free never runs on the thread that dropped it.
        # BUG-221: enqueue only — no executor, no lock (see _RELEASE_QUEUE). The
        # reference goes straight into the box so no local on this frame can
        # outlive the worker's clear and free the recognizer here after all.
        try:
            box = [self.__dict__.pop("_rec", None)]
            if box[0] is not None and is_native_recognizer(box[0]):
                _enqueue_release(box)
        except Exception:  # noqa: BLE001, S110 — __del__ must never raise; the
            # worst case is the pre-fix behaviour (free on this thread).
            pass

    def __getattr__(self, name: str) -> Any:
        """Every OTHER native method stays reachable — and locked.

        ``getattr(rec, "SomeMethod", None)`` on the proxy must answer exactly
        as it would on the wrapped recognizer: present on builds that have it,
        absent otherwise. A callable is returned behind the same process-wide
        lock every listed method holds; a plain attribute passes through.
        (``__getattr__`` only runs for names this class does not define, so
        the explicit methods above are unaffected and ``_rec`` itself never
        recurses.)
        """
        if name == "_rec":  # guard against half-initialised proxies
            raise AttributeError(name)
        target = getattr(self._rec, name)
        if not callable(target):
            return target

        def _locked(*args: Any, **kwargs: Any) -> Any:
            with _LOCK:
                return target(*args, **kwargs)

        return _locked


def wrap_recognizer(rec: Any) -> Any:
    """Lock a native recognizer; leave test doubles untouched."""
    if rec is None or not is_native_recognizer(rec):
        return rec
    return LockedRecognizer(rec)


def build_recognizer(model: Any, sample_rate: int, grammar: str | None = None) -> Any:
    """Construct a ``KaldiRecognizer``, ``SetWords(True)``, wrap if native."""
    from vosk import KaldiRecognizer

    with _LOCK:
        rec = (
            KaldiRecognizer(model, sample_rate, grammar)
            if grammar is not None
            else KaldiRecognizer(model, sample_rate)
        )
        rec.SetWords(True)
    return wrap_recognizer(rec)
