"""Wait for the audio device enumeration to settle before opening streams.

Permanent fix for the post-reboot audio-device-index drift (BUG-014 class,
2026-05-25 episode). Jarvis autostarts as a tray app the moment the user logs
in — frequently *before* Windows has finished enumerating audio endpoints
(USB headset, monitor audio over DisplayPort, etc.). PortAudio caches the
device table at its first initialization, so a too-early start freezes a
*partial* table for the whole process lifetime:

    Mic-Resolve 'auto-headset' ... — 4 Kandidat(en)      # should be 7
    AudioPlayer nutzt Device: ... (idx=14)               # idx 14 = monitor now
    OutputStream @ 24000Hz failed (Invalid sample rate -9997)

The wake word still fires, but the resolved speaker index points at a
stale/silent endpoint, so the chime and every TTS answer evaporate — the user
hears nothing and concludes "Hey Jarvis doesn't trigger".

``wait_for_stable_audio_devices`` polls the device count, forcing PortAudio to
re-scan on every poll (``_terminate`` + ``_initialize`` — a plain
``query_devices`` would just return the cached partial table), and returns
once the count has been stable for ``stable_window_s`` or ``max_wait_s`` is
hit. Call it once at warm-up, before any stream opens, then re-resolve the
output device. It is a graceful no-op on a headless VPS (sounddevice not
installed) and never raises — audio robustness must never break boot.
"""
from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from typing import Any

__all__ = ["wait_for_stable_audio_devices"]


def _get_sd() -> Any | None:
    """Lazy-import sounddevice. Returns ``None`` when it is not installed
    (headless VPS / base install without the ``[desktop]`` extra)."""
    try:
        import sounddevice as sd  # noqa: PLC0415 — desktop-only optional dep
    except Exception:  # noqa: BLE001 — ImportError or PortAudio load failure
        return None
    return sd


def _refresh_device_count(sd: Any) -> int:
    """Force PortAudio to re-enumerate and return the count of real I/O
    devices. Re-init is the only way to escape a frozen partial table inside
    a single process; a bare ``query_devices`` returns the cached list."""
    # _terminate before the first _initialize is harmless; both can fail
    # benignly on odd PortAudio states — re-enumeration is best-effort.
    with contextlib.suppress(Exception):
        sd._terminate()
    with contextlib.suppress(Exception):
        sd._initialize()
    try:
        devices = sd.query_devices()
    except Exception:  # noqa: BLE001 — PortAudio can throw mid-scan
        return 0
    return sum(
        1
        for d in devices
        if (d.get("max_input_channels", 0) or 0) > 0
        or (d.get("max_output_channels", 0) or 0) > 0
    )


def wait_for_stable_audio_devices(
    *,
    max_wait_s: float = 8.0,
    stable_window_s: float = 1.5,
    poll_interval_s: float = 0.5,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Block until the audio device table settles, then leave PortAudio with a
    freshly re-enumerated table.

    Args:
        max_wait_s: Hard upper bound — never block boot longer than this.
        stable_window_s: The count must stay unchanged for this long to count
            as settled.
        poll_interval_s: Delay between re-scans.
        monotonic / sleep: Injectable clock for deterministic tests.

    Returns:
        Diagnostics dict: ``available`` (sounddevice present), ``device_count``,
        ``stable`` (settled vs. timed out), ``polls``, ``reinits``, ``waited_s``.
    """
    sd = _get_sd()
    if sd is None:
        return {
            "available": False,
            "device_count": 0,
            "stable": False,
            "polls": 0,
            "reinits": 0,
            "waited_s": 0.0,
        }

    start = monotonic()
    deadline = start + max_wait_s
    last_count: int | None = None
    stable_since: float | None = None
    polls = 0

    while True:
        count = _refresh_device_count(sd)
        polls += 1
        now = monotonic()
        if count != last_count:
            last_count = count
            stable_since = now
        stable = stable_since is not None and (now - stable_since) >= stable_window_s
        if stable or now >= deadline:
            return {
                "available": True,
                "device_count": last_count or 0,
                "stable": bool(stable),
                "polls": polls,
                "reinits": polls,
                "waited_s": round(now - start, 3),
            }
        sleep(poll_interval_s)
