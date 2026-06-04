"""Detect OS-level mute on the default capture device using Windows Core Audio.

Read-only.  Reports mute state + volume level.  If muted, we know to unmute
in Windows Sound settings; if NOT muted but signal is silent, the hardware
boom-mic mute (Logitech PRO X flip arm) is engaged.
"""
from __future__ import annotations

import sys

try:
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume, EDataFlow
except Exception as exc:
    print(f"pycaw not available: {exc}")
    sys.exit(2)


def main() -> None:
    devices = AudioUtilities.GetAllDevices()
    print(f"Total endpoints: {len(devices)}")
    for d in devices:
        if d.state != 1:  # 1 = ACTIVE
            continue
        flow = getattr(d, "DataFlow", None)
        # Filter on capture-side endpoints
        if "Mikro" not in d.FriendlyName and "Microphone" not in d.FriendlyName and "Mic" not in d.FriendlyName:
            continue
        try:
            iface = d._dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            from ctypes import POINTER, cast
            vol = cast(iface, POINTER(IAudioEndpointVolume))
            mute = vol.GetMute()
            level_db = vol.GetMasterVolumeLevel()
            level_scalar = vol.GetMasterVolumeLevelScalar()
            print(
                f"  [{d.id[-12:]}] {d.FriendlyName[:55]:55s} | mute={bool(mute)} | "
                f"vol={level_scalar*100:5.1f}% ({level_db:+.1f} dB)"
            )
        except Exception as exc:
            print(f"  {d.FriendlyName}: probe failed: {exc}")


if __name__ == "__main__":
    main()
