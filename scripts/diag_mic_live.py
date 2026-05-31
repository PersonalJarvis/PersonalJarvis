"""Read 2 seconds from the current default mic and report per-100ms RMS.

Detects mute / device-mismatch by comparing live RMS against the heartbeat
RMS that the Jarvis voice pipeline sees.  No side effects.
"""
from __future__ import annotations

import math
import sys

import numpy as np
import sounddevice as sd


def rms(buf: np.ndarray) -> float:
    if buf.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(buf.astype(np.float32) ** 2)) / 32768.0)


def dbfs(value: float) -> float:
    return -120.0 if value <= 1e-7 else 20.0 * math.log10(value)


def main() -> None:
    try:
        default_in = int(sd.default.device[0])
    except (TypeError, IndexError):
        default_in = int(sd.default.device)
    dev = sd.query_devices(default_in, kind="input")
    host = sd.query_hostapis(dev["hostapi"])["name"]
    sr = 16000
    print(f"Capturing 2.0s from [{default_in}] {dev['name']} ({host}) @ {sr} Hz")
    rec = sd.rec(int(sr * 2.0), samplerate=sr, channels=1, dtype="int16", device=default_in)
    sd.wait()
    frame_ms = 100
    frame = int(sr * frame_ms / 1000)
    print("ms_offset  rms        dBFS")
    for i in range(0, rec.shape[0], frame):
        chunk = rec[i : i + frame, 0]
        r = rms(chunk)
        print(f"  {i // sr * 1000 + (i % sr) * 1000 // sr:5d}    {r:8.5f}   {dbfs(r):+6.1f}")
    overall = rms(rec[:, 0])
    print(f"OVERALL    {overall:8.5f}   {dbfs(overall):+6.1f}")
    if overall < 0.001:
        print("VERDICT: mic is effectively silent (-60 dBFS) — likely muted at OS or hardware level")
        sys.exit(1)
    elif overall < 0.01:
        print("VERDICT: very low signal (-40 dBFS) — check headset boom position / OS mic level")
    else:
        print("VERDICT: mic is producing signal")


if __name__ == "__main__":
    main()
