"""BUG-151: libvosk native calls serialise; test doubles stay unlocked."""

from __future__ import annotations

import threading
import time

from jarvis.plugins.wake.vosk_native import (
    LockedRecognizer,
    build_recognizer,
    is_native_recognizer,
    wrap_recognizer,
)


class _Contended:
    """A stand-in whose methods overlap unless a lock serialises them."""

    def __init__(self) -> None:
        self.overlaps = 0
        self._in = 0
        self._guard = threading.Lock()

    def _enter(self) -> None:
        with self._guard:
            if self._in:
                self.overlaps += 1
            self._in += 1
        time.sleep(0.04)
        with self._guard:
            self._in -= 1

    def AcceptWaveform(self, _data: bytes) -> bool:  # noqa: N802
        self._enter()
        return False

    def FinalResult(self) -> str:  # noqa: N802
        self._enter()
        return "{}"

    def SetWords(self, _flag: bool) -> None:  # noqa: N802
        return None


def test_locked_recognizer_serializes_overlapping_native_calls() -> None:
    rec = LockedRecognizer(_Contended())
    errors: list[BaseException] = []

    def _feed() -> None:
        try:
            rec.AcceptWaveform(b"\x00\x00")
            rec.FinalResult()
        except BaseException as exc:  # noqa: BLE001 — collect for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=_feed) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2.0)
        assert not thread.is_alive()
    assert errors == []
    assert rec._rec.overlaps == 0  # noqa: SLF001 — the inner counter is the proof


def test_wrap_recognizer_leaves_test_doubles_unlocked() -> None:
    inner = _Contended()
    assert is_native_recognizer(inner) is False
    assert wrap_recognizer(inner) is inner


def test_wrap_recognizer_locks_a_vosk_named_class() -> None:
    class KaldiRecognizer(_Contended):
        pass

    KaldiRecognizer.__module__ = "vosk"
    inner = KaldiRecognizer()
    wrapped = wrap_recognizer(inner)
    assert isinstance(wrapped, LockedRecognizer)
    assert wrapped._rec is inner  # noqa: SLF001


def test_build_recognizer_uses_the_patched_vosk_factory(monkeypatch) -> None:
    built: list[object] = []

    class _FakeRec:
        def __init__(self, model, rate, grammar=None):  # noqa: ANN001
            self.model = model
            self.rate = rate
            self.grammar = grammar
            self.words = False
            built.append(self)

        def SetWords(self, flag):  # noqa: ANN001, N802
            self.words = flag

    import sys
    import types

    mod = types.ModuleType("vosk")
    mod.KaldiRecognizer = _FakeRec
    monkeypatch.setitem(sys.modules, "vosk", mod)

    rec = build_recognizer("model", 16_000, '["hey nova","[unk]"]')
    assert rec is built[0]
    assert rec.words is True
    assert rec.grammar == '["hey nova","[unk]"]'
