"""Runtime audio-device hot-swap watcher (BUG-102).

PortAudio freezes its device table at initialization, so a process that runs
for hours never sees a headset being plugged in or pulled out. Every layer
above therefore degrades at once: the persistent output stream keeps writing
to a vanished or no-longer-default device (blocking writes, seconds-long
stalls — perceived as "everything lags"), and the microphone watchdog can
only re-open devices from the stale table, so it retries a dead endpoint
forever and can never find the newly arrived one.

This module owns the ONE cross-platform cure, in three parts:

1. **Detection** — poll the out-of-process device probe
   (`jarvis.audio.devices._query_tables_fresh`), which is safe while this
   process holds live streams, and compare a name-based topology signature.
   No OS-specific listener; Windows, macOS, and Linux share the poller.
2. **Coordinated refresh** — when the topology changed: discard every
   registered capture stream and the player's output stream, re-initialize
   PortAudio under the established re-init lock (safe ONLY with no live
   streams — the BUG-058 native-fault hazard), and invalidate every resolve
   cache. The existing self-healers do the rest: the mic stall watchdog
   reopens against the now-fresh table within ~a second, and the player
   reopens lazily on the next playback.
3. **The open guard** — every native stream open holds ``stream_open_guard``
   so no stream can come to life BETWEEN terminate and initialize.

Everything fails open: a failed probe means "no judgment", a failed refresh
leaves the process exactly where it was, and a headless install without
sounddevice never gets past the first probe. Nothing here runs on the boot
critical path (AP-26) — the watcher starts after honest voice readiness.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import weakref
from collections.abc import Callable
from typing import Any

log = logging.getLogger("jarvis.audio.topology")

# Serializes native stream opens against the PortAudio re-init window. An
# RLock so a guarded open path may nest (candidate loops re-enter freely).
_OPEN_LOCK = threading.RLock()

# Live MicrophoneCapture instances that a refresh must quiesce. WeakSet: a
# capture that forgot to unregister can never be kept alive by the watcher.
# The lock covers every add/discard/snapshot — the event loop registers
# captures while the refresh worker thread iterates, and a bare WeakSet is
# not safe against that ("set changed size during iteration").
_captures_lock = threading.Lock()
_captures: weakref.WeakSet[Any] = weakref.WeakSet()

DEFAULT_POLL_S = 5.0
# One burst of CoreAudio/WASAPI events accompanies a single physical plug;
# wait this long after the first divergent signature so the refresh runs once
# against the settled topology instead of once per event.
_SETTLE_S = 1.5


def stream_open_guard() -> threading.RLock:
    """Lock every native PortAudio stream open must hold (``with ...():``)."""
    return _OPEN_LOCK


def register_capture(capture: Any) -> None:
    with _captures_lock:
        _captures.add(capture)


def unregister_capture(capture: Any) -> None:
    with _captures_lock:
        _captures.discard(capture)


def topology_signature(tables: Any) -> str | None:
    """Reduce a device-table snapshot to a comparable identity string.

    Built from device NAMES with their I/O roles plus the default pair —
    never from indices, which shuffle on every enumeration and would cause
    refresh churn without any physical change.
    """
    if not tables:
        return None
    devices, _hostapis, defaults = tables
    entries = []
    for device in devices:
        has_input = int(device.get("max_input_channels") or 0) > 0
        has_output = int(device.get("max_output_channels") or 0) > 0
        if not (has_input or has_output):
            continue
        name = " ".join(str(device.get("name", "")).split()).casefold()
        entries.append(f"{name}|{int(has_input)}{int(has_output)}")

    def _default_name(index: int | None) -> str:
        if index is None or not 0 <= index < len(devices):
            return ""
        return " ".join(str(devices[index].get("name", "")).split()).casefold()

    return (
        ";".join(sorted(entries))
        + f"##in={_default_name(defaults[0])}##out={_default_name(defaults[1])}"
    )


def _fresh_signature() -> str | None:
    try:
        import sounddevice  # noqa: F401, PLC0415 — headless short-circuit
    except Exception:  # noqa: BLE001 — no audio stack → never spawn the probe
        return None
    from jarvis.audio.devices import _query_tables_fresh

    return topology_signature(_query_tables_fresh())


def refresh_audio_backend(player: Any, output_device: Any = None) -> bool:
    """Coordinated PortAudio re-init after a topology change. Worker-thread.

    Order matters: quiesce every native stream FIRST (a terminate with live
    streams is the BUG-058 native-fault path), then re-init under the shared
    boot re-init lock, then drop every stale resolve cache. Reopening is left
    to the owners — the capture watchdog and the player's lazy open — so this
    function never creates a stream itself.
    """
    try:
        import sounddevice as sd  # noqa: PLC0415 — desktop-only optional dep
    except Exception:  # noqa: BLE001 — headless install
        return False
    try:
        from jarvis.audio.capture import _invalidate_resolve_cache
        from jarvis.audio.device_init import _REINIT_LOCK

        with _OPEN_LOCK:
            with _captures_lock:
                captures = list(_captures)
            for capture in captures:
                try:
                    capture.discard_native_stream()
                except Exception:  # noqa: BLE001 — one dead capture never blocks
                    log.debug(
                        "Capture discard during refresh failed", exc_info=True
                    )
            if player is not None:
                try:
                    player.invalidate_device_cache()
                except Exception:  # noqa: BLE001
                    log.debug(
                        "Player invalidate during refresh failed", exc_info=True
                    )
            with _REINIT_LOCK:
                with contextlib.suppress(Exception):
                    sd._terminate()
                try:
                    sd._initialize()
                except Exception as exc:  # noqa: BLE001
                    log.warning("PortAudio re-initialization failed: %s", exc)
                    return False
            _invalidate_resolve_cache()
            if player is not None:
                # Re-resolve the CONFIGURED spec against the fresh table; a
                # stale resolved index must never survive the re-init.
                with contextlib.suppress(Exception):
                    player.set_device(output_device)
    except Exception:  # noqa: BLE001 — a failed refresh must never kill the watcher
        log.warning("Audio backend refresh failed", exc_info=True)
        return False
    return True


async def watch_topology(
    player: Any,
    output_device: Any = None,
    *,
    poll_s: float = DEFAULT_POLL_S,
    probe: Callable[[], str | None] | None = None,
    refresh: Callable[[], bool] | None = None,
) -> None:
    """Poll for device hot-swap and refresh the audio backend on change.

    Runs forever; the owner cancels it on shutdown. ``probe``/``refresh`` are
    injectable for tests; the defaults use the out-of-process device probe
    and :func:`refresh_audio_backend`.
    """
    probe = probe or _fresh_signature
    refresh = refresh or (lambda: refresh_audio_backend(player, output_device))
    last: str | None = None
    while True:
        await asyncio.sleep(poll_s)
        signature = await asyncio.to_thread(probe)
        if signature is None:
            # Probe unavailable (headless, worker timeout) — no judgment.
            continue
        if last is None:
            last = signature
            continue
        if signature == last:
            continue
        await asyncio.sleep(_SETTLE_S)
        settled = await asyncio.to_thread(probe) or signature
        log.info(
            "Audio device topology changed — refreshing the audio backend "
            "(streams reopen automatically)."
        )
        ok = await asyncio.to_thread(refresh)
        log.info("Audio backend refresh %s.", "completed" if ok else "failed")
        last = settled


__all__ = [
    "DEFAULT_POLL_S",
    "refresh_audio_backend",
    "register_capture",
    "stream_open_guard",
    "topology_signature",
    "unregister_capture",
    "watch_topology",
]
