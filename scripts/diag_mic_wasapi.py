"""Compare MME and WASAPI capture of the same mic device.

If both deliver silence -> physical mic mute.  If WASAPI works and MME does
not -> driver-path issue (then we change the resolver default).
"""
from __future__ import annotations

import math

import numpy as np
import sounddevice as sd

TARGETS = [
    (1, "MME"),
    (23, "WASAPI"),
]


def rms(buf: np.ndarray) -> float:
    if buf.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(buf.astype(np.float32) ** 2)) / 32768.0)


def dbfs(value: float) -> float:
    return -120.0 if value <= 1e-7 else 20.0 * math.log10(value)


def main() -> None:
    for idx, label in TARGETS:
        try:
            dev = sd.query_devices(idx, kind="input")
        except Exception as exc:
            print(f"[{idx}] {label}: query failed: {exc}")
            continue
        sr = 16000
        print(f"--- [{idx}] {dev['name']} via {label} @ {sr} Hz ---")
        try:
            rec = sd.rec(int(sr * 1.5), samplerate=sr, channels=1, dtype="int16", device=idx)
            sd.wait()
        except Exception as exc:
            print(f"  capture failed: {exc}\n")
            continue
        overall = rms(rec[:, 0])
        peak = float(np.max(np.abs(rec[:, 0]))) / 32768.0
        print(f"  rms={overall:.5f} ({dbfs(overall):+.1f} dBFS) | peak={peak:.5f} ({dbfs(peak):+.1f} dBFS)")
        print()


if __name__ == "__main__":
    main()
