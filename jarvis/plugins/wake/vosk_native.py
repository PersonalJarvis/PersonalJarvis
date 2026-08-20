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
from typing import Any

_LOCK = threading.Lock()


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
