"""Emit a fresh-process PortAudio device snapshot for Settings.

PortAudio freezes its device table when it initializes.  A long-running
desktop process therefore cannot discover a headset connected later by simply
calling ``query_devices()`` again.  This tiny worker starts a separate Python
process, whose new PortAudio instance sees the current operating-system device
set, and returns only the fields used by :mod:`jarvis.audio.devices`.

The worker is intentionally independent from the live audio process.  It never
terminates or reinitializes that process's PortAudio instance, so an open wake
microphone or output stream cannot be invalidated during a Settings rescan.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SNAPSHOT_MARKER = "JARVIS_AUDIO_DEVICE_SNAPSHOT="


def _device_index(value: object) -> int:
    """Normalize a sounddevice default index to an integer sentinel."""
    try:
        index = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return -1
    return index if index >= 0 else -1


def collect_snapshot() -> dict[str, Any]:
    """Return the current PortAudio tables, or an honest empty headless set."""
    try:
        import sounddevice as sd  # noqa: PLC0415 - optional desktop dependency
    except Exception:  # noqa: BLE001 - no PortAudio is normal on headless hosts
        return {"ok": True, "devices": [], "hostapis": [], "default": [-1, -1]}

    try:
        devices = [
            {
                "name": str(device.get("name", "")),
                "hostapi": _device_index(device.get("hostapi", -1)),
                "max_input_channels": int(device.get("max_input_channels", 0) or 0),
                "max_output_channels": int(device.get("max_output_channels", 0) or 0),
            }
            for device in sd.query_devices()
        ]
        hostapis = [
            {
                "name": str(hostapi.get("name", "")),
                "devices": [_device_index(index) for index in hostapi.get("devices", ())],
            }
            for hostapi in sd.query_hostapis()
        ]
        default_pair = sd.default.device
        default = [_device_index(default_pair[0]), _device_index(default_pair[1])]
    except Exception:  # noqa: BLE001 - the parent falls back to its cached table
        return {"ok": False, "devices": [], "hostapis": [], "default": [-1, -1]}

    return {
        "ok": True,
        "devices": devices,
        "hostapis": hostapis,
        "default": default,
    }


def main(output_path: str | None = None) -> int:
    """Write one marker-delimited JSON record for the parent process.

    Frozen GUI builds have no usable stdout, so their internal probe mode
    supplies a private temporary output path.  The plain module entry point
    keeps stdout support for diagnostics and development.
    """
    payload = collect_snapshot()
    record = SNAPSHOT_MARKER + json.dumps(
        payload, ensure_ascii=True, separators=(",", ":")
    )
    if output_path is not None:
        try:
            Path(output_path).write_text(record + "\n", encoding="utf-8")
        except OSError:
            return 1
    else:
        print(record, flush=True)
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1] if len(sys.argv) == 2 else None))
