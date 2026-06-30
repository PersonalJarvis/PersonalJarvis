"""faster-whisper transcription must never run concurrently on one model, and a
WEDGED model must self-heal.

Live forensic 2026-06-29 (custom wake "Hey Nico"): the wake went dead for HOURS —
every transcribe timed out at 8 s, was abandoned, retried, hung again, forever;
an app restart did not even clear it. Root cause: the wake poll loop and the VAD
"listening bubble" probe share ONE ``FasterWhisperProvider`` (``_probe_stt =
_stt`` for a custom phrase) and both call ``transcribe_pcm`` — ``model.transcribe``
runs in a worker thread, so two concurrent calls hit ctranslate2's WhisperModel
at once, which is NOT thread-safe → a permanent hang.

Two contracts pinned here:
1. A non-blocking per-model lock so two concurrent calls NEVER overlap (the
   second is skipped with ``TranscribeBusy`` instead of corrupting the model or
   piling up behind a hung call).
2. ``recover()`` drops the (possibly hung) model + its lock so the next call
   rebuilds a fresh engine — the self-heal that ends the permanent wedge.
"""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

from jarvis.plugins.stt.fwhisper import FasterWhisperProvider, TranscribeBusy

PCM_1S = b"\x00\x00" * 16_000


class _ConcurrencyProbeModel:
    """Stand-in WhisperModel that records the peak number of concurrent
    ``transcribe`` calls. ctranslate2 would corrupt/hang here; we just measure."""

    def __init__(self) -> None:
        self._active = 0
        self.max_active = 0
        self.calls = 0
        self._lock = threading.Lock()

    def transcribe(self, audio, **kwargs):  # noqa: ANN001, ANN003
        with self._lock:
            self._active += 1
            self.calls += 1
            self.max_active = max(self.max_active, self._active)
        time.sleep(0.1)  # hold long enough that an unserialized 2nd call overlaps
        with self._lock:
            self._active -= 1
        info = SimpleNamespace(language="de")
        return iter(()), info  # no segments -> empty transcript, confidence 0


async def test_concurrent_transcribe_calls_never_overlap() -> None:
    prov = FasterWhisperProvider(device="cpu", compute_type="int8")
    probe = _ConcurrencyProbeModel()
    prov._model = probe  # noqa: SLF001 — inject the fake model (skip the real load)

    # Two concurrent transcriptions on the SAME provider (the wake poll loop and
    # the VAD probe in production). The non-blocking lock means model.transcribe
    # is NEVER entered twice at once — the loser is skipped, not run concurrently.
    results = await asyncio.gather(
        prov.transcribe_pcm(PCM_1S),
        prov.transcribe_pcm(PCM_1S),
        return_exceptions=True,
    )

    assert probe.max_active == 1, (
        f"model.transcribe ran concurrently (peak {probe.max_active}) — the "
        "ctranslate2 wedge race is not prevented"
    )
    # A skipped call is a clean TranscribeBusy, never a crash.
    for r in results:
        assert (not isinstance(r, Exception)) or isinstance(r, TranscribeBusy), r


async def test_single_transcribe_still_works() -> None:
    # Regression: the guard must not break a normal single transcription.
    prov = FasterWhisperProvider(device="cpu", compute_type="int8")
    prov._model = _ConcurrencyProbeModel()  # noqa: SLF001
    t = await prov.transcribe_pcm(PCM_1S)
    assert t.text == ""  # empty segments -> empty text, no crash


async def test_busy_lock_raises_transcribe_busy() -> None:
    # A prior call already holds the inference lock (e.g. a hung transcribe).
    # The next call must skip with TranscribeBusy, NOT block/pile up behind it.
    prov = FasterWhisperProvider(device="cpu", compute_type="int8")
    prov._model = _ConcurrencyProbeModel()  # noqa: SLF001
    prov._infer_lock.acquire()  # simulate an in-flight / wedged call
    try:
        raised = False
        try:
            await prov.transcribe_pcm(PCM_1S)
        except TranscribeBusy:
            raised = True
        assert raised, "a transcribe while the lock is held must raise TranscribeBusy"
    finally:
        prov._infer_lock.release()


def test_recover_drops_model_and_swaps_in_a_fresh_lock() -> None:
    # recover() must orphan the (possibly hung) model + lock so the next call
    # rebuilds a clean engine under a lock that is NOT held by the hung thread.
    prov = FasterWhisperProvider(device="cpu", compute_type="int8")
    prov._model = object()  # pretend a wedged model is loaded  # noqa: SLF001
    old_lock = prov._infer_lock
    old_lock.acquire()  # a hung worker thread "holds" the old lock

    prov.recover()

    assert prov._model is None, "recover() must drop the model so it rebuilds"  # noqa: SLF001
    assert prov._infer_lock is not old_lock, "recover() must swap in a fresh lock"  # noqa: SLF001
    # The fresh lock is free even though the old one is still held by the wedge.
    assert prov._infer_lock.acquire(blocking=False) is True  # noqa: SLF001
    prov._infer_lock.release()  # noqa: SLF001
    old_lock.release()
