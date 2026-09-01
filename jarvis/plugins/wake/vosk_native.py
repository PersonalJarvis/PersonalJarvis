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

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

_LOCK = threading.Lock()

# One worker that does nothing but let recognizers die. ``vosk_recognizer_free``
# (KaldiRecognizer.__del__) is a native call measured at 15 s on a cold, paging
# box (2026-08-30 "Event loop STALLED" stack) — and it runs on WHICHEVER thread
# drops the last reference, which was the asyncio loop during wake teardown.
_RELEASE_POOL: ThreadPoolExecutor | None = None
_RELEASE_POOL_LOCK = threading.Lock()


def _release_pool() -> ThreadPoolExecutor:
    global _RELEASE_POOL
    with _RELEASE_POOL_LOCK:
        if _RELEASE_POOL is None:
            _RELEASE_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vosk-release")
        return _RELEASE_POOL


def release_recognizer(rec: Any) -> None:
    """Let *rec*'s native free run on the release worker, never on the caller.

    The last reference is moved into a box and cleared on the worker under the
    same process-wide lock every other native call holds (BUG-151 — the free is
    a native call too). Never raises: during interpreter shutdown the executor
    refuses new work, and the box is then cleared inline — exactly the pre-fix
    behaviour, which is acceptable on the one path where no loop is left to
    stall.
    """
    if rec is None:
        return
    box = [rec]
    del rec

    def _drop() -> None:
        with _LOCK:
            box.clear()

    try:
        _release_pool().submit(_drop)
    except Exception:  # noqa: BLE001 — shutdown fallback, see docstring
        _drop()


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
        try:
            rec = self.__dict__.pop("_rec", None)
            if rec is not None and is_native_recognizer(rec):
                release_recognizer(rec)
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
