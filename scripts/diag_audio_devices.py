"""One-shot diagnostic: enumerate input/output devices + Windows mute state.

Read-only — no side effects. Used during BUG-027 triage when mic max-rms is
near zero (-77 dBFS heartbeats) despite the mic stream being open.
"""
from __future__ import annotations

import sys

try:
    import sounddevice as sd
except Exception as exc:
    print(f"sounddevice import failed: {exc}")
    sys.exit(2)


def main() -> None:
    print("=== INPUT DEVICES ===")
    default_in = sd.default.device[0] if isinstance(sd.default.device, (tuple, list)) else sd.default.device
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            host = sd.query_hostapis(dev["hostapi"])["name"]
            marker = "  <-- SYSTEM DEFAULT" if idx == default_in else ""
            print(
                f"  [{idx:2d}] {dev['name'][:55]:55s} | {host:18s} | in={dev['max_input_channels']} | "
                f"sr={int(dev['default_samplerate'])}{marker}"
            )

    print()
    print("=== OUTPUT DEVICES ===")
    default_out = sd.default.device[1] if isinstance(sd.default.device, (tuple, list)) else None
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_output_channels"] > 0:
            host = sd.query_hostapis(dev["hostapi"])["name"]
            marker = "  <-- SYSTEM DEFAULT" if idx == default_out else ""
            print(
                f"  [{idx:2d}] {dev['name'][:55]:55s} | {host:18s} | out={dev['max_output_channels']} | "
                f"sr={int(dev['default_samplerate'])}{marker}"
            )

    print()
    print(f"sd.default.device = {sd.default.device}")


if __name__ == "__main__":
    main()
