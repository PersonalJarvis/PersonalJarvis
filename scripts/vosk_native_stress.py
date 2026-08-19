"""Native-concurrency stress for libvosk — the BUG-151 repro and acceptance test.

Exercises ONE shared ``vosk.Model`` from several threads the way the wake
engine does (``jarvis/plugins/wake/vosk_kws_provider.py``): short-lived
recognizers that are built, fed a little audio, finalised and dropped, next
to a long-running streaming decoder. On vosk 0.3.44 / Windows 11 (2026-08-19)
the ``churn`` mode dies with an access violation within 5-50 s; ``steady``
(long-lived recognizers decoding concurrently) and ``churn-nodecode``
(construction/destruction only) survive. Run with ``-X faulthandler`` to see
the native call each thread was in.

Modes (first argument; second = seconds, default 90):
    churn            build -> AcceptWaveform(short) -> FinalResult -> drop, x5 threads + decoder
    churn-nodecode   build -> drop only (no decode), x5 + decoder
    churn-nofinal    build -> AcceptWaveform -> drop (no finalise), x5 + decoder
    steady           five long-lived recognizers decode + FinalResult/Reset forever + decoder
    churn-serial     ``churn`` with EVERY native call behind one global lock (survives)

A fix for BUG-151 counts only when ``churn`` survives 3 x 180 s here AND
``scripts/vosk_wake_bench.py`` passes 3/3 on the weak laptop.

    python -X faulthandler scripts/vosk_native_stress.py churn 120
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import vosk  # noqa: E402

from jarvis.speech.wake_constants import resolve_vosk_model_paths  # noqa: E402

SR = 16_000
MODE = sys.argv[1] if len(sys.argv) > 1 else "churn"
DURATION_S = float(sys.argv[2]) if len(sys.argv) > 2 else 90.0

vosk.SetLogLevel(-1)
_paths = resolve_vosk_model_paths(None)
if not _paths:
    raise SystemExit("no Vosk model under data/wake_models/vosk/ — install one first")
MODEL = vosk.Model(_paths[0])
GRAMMAR = json.dumps(["hey nova", "[unk]"])
SILENCE_300MS = np.zeros(4800, dtype=np.int16).tobytes()
NOISE_3S = (np.random.default_rng(0).standard_normal(SR * 3) * 2000).astype(np.int16).tobytes()

stop = threading.Event()
counts = {"built": 0, "decoded": 0}
counts_lock = threading.Lock()
GLOBAL = threading.Lock()  # churn-serial only


def _native(fn, *args):
    """Run one native call — behind the global lock in ``churn-serial``."""
    if MODE == "churn-serial":
        with GLOBAL:
            return fn(*args)
    return fn(*args)


def _new(kind: str):
    if kind == "grammar":
        rec = _native(vosk.KaldiRecognizer, MODEL, SR, GRAMMAR)
    else:
        rec = _native(vosk.KaldiRecognizer, MODEL, SR)
    rec.SetWords(True)
    return rec


def churn(kind: str) -> None:
    while not stop.is_set():
        holder = [_new(kind)]
        if MODE != "churn-nodecode":
            _native(holder[0].AcceptWaveform, SILENCE_300MS)
            if MODE != "churn-nofinal":
                _native(holder[0].FinalResult)
        holder.clear()  # drop = vosk_recognizer_free
        with counts_lock:
            counts["built"] += 1


def steady(kind: str) -> None:
    rec = _new(kind)
    pcm = NOISE_3S[: SR * 2]
    while not stop.is_set():
        for i in range(0, len(pcm), 1024):
            if _native(rec.AcceptWaveform, pcm[i : i + 1024]):
                _native(rec.Result)
        _native(rec.FinalResult)
        rec.Reset()
        with counts_lock:
            counts["built"] += 1


def decoder() -> None:
    """The long-running streaming ear next to the churn (stage 1 in the engine)."""
    while not stop.is_set():
        rec = _new("grammar")
        for i in range(0, len(NOISE_3S), 1024):
            if _native(rec.AcceptWaveform, NOISE_3S[i : i + 1024]):
                _native(rec.Result)
            else:
                rec.PartialResult()
        _native(rec.FinalResult)
        del rec
        with counts_lock:
            counts["decoded"] += 1


def main() -> int:
    worker = steady if MODE == "steady" else churn
    threads = [threading.Thread(target=decoder, daemon=True)]
    threads += [threading.Thread(target=worker, args=("grammar",), daemon=True) for _ in range(3)]
    threads += [threading.Thread(target=worker, args=("free",), daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    t0 = time.time()
    while time.time() - t0 < DURATION_S:
        time.sleep(5)
        with counts_lock:
            print(
                f"[{MODE}] t={time.time() - t0:5.0f}s built={counts['built']} "
                f"decoded={counts['decoded']}",
                flush=True,
            )
    stop.set()
    for t in threads:
        t.join(timeout=15)
    print(f"[{MODE}] survived {DURATION_S:.0f}s: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
