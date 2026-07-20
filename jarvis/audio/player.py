"""Cross-platform audio playback via sounddevice and PortAudio.

Consumes either ready-made PCM bytes or an `AsyncIterator[AudioChunk]`
for pseudo-streaming (sentence-by-sentence synthesis while playback runs).

Gemini 3.1 Flash TTS delivers `audio/l16` (raw linear PCM 24 kHz 16-bit mono).
No decoding needed — reinterpret directly as numpy.int16 and pass to PortAudio.
"""
from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections.abc import AsyncIterator, Callable, Sequence
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    # Type-checkers see the real module so `sd.OutputStream` annotations resolve;
    # at runtime the guarded import below binds sd (or None when absent).
    import sounddevice as sd
else:
    try:
        import sounddevice as sd
    except Exception:  # noqa: BLE001 — sounddevice/PortAudio (libportaudio2) absent (headless/slim)
        sd = None  # type: ignore[assignment]

from jarvis.audio import level_tap
from jarvis.audio.device_select import is_legacy_primary_mapper
from jarvis.audio.gain import apply_output_gain, clamp_volume
from jarvis.core.events import AudioOutFirst
from jarvis.core.protocols import AudioChunk

log = logging.getLogger("jarvis.audio.player")

# `except sd.PortAudioError` is a RUNTIME expression: when sd is None it would
# evaluate None.PortAudioError and raise AttributeError, masking the real audio
# error. Resolve the exception type once here so the except clause stays safe
# even without sounddevice (H4 review).
_PortAudioError: type[BaseException] = sd.PortAudioError if sd is not None else OSError

TTS_SAMPLE_RATE = 24_000  # Gemini 3.1 Flash TTS output rate
TTS_WRITE_BUFFER_MS = 120
_MAX_REPORTED_OUTPUT_LATENCY_S = 5.0


class _PlaybackSuperseded(RuntimeError):
    """A stopped playback attempted to acquire a new output stream."""


# Audio devices we NEVER auto-select as headset output. These are
# locale-independent hardware/interface names: monitor speakers reached over
# HDMI/DisplayPort (GPU audio chips) and digital passthrough with no guaranteed
# sink. Matched case-insensitively. The Windows MME "Sound Mapper" and
# DirectSound "Primary Sound Driver" virtual routers are NOT listed here — their
# display name is localized, so they are skipped STRUCTURALLY instead (see
# ``is_legacy_primary_mapper``), which works on every Windows UI language.
_BLOCKED_OUTPUT_SUBSTRINGS = (
    "NVIDIA High Definition",  # NVIDIA GPU audio (monitor via HDMI/DP)
    "AMD HD Audio",            # AMD GPU audio
    "SPDIF",                   # digital-out with no guaranteed sink
)

# Generic default preference order for "auto-headset" output: a curated list of
# common consumer headset product families, most specific first. It is NOT tied
# to any one machine's hardware — a user whose device is not covered names it
# via ``[audio].output_device_priority`` (consulted BEFORE this list) or pins an
# explicit ``[audio].output_device`` index, both without editing code.
#
# "PRO X" precedes "Logitech PRO X" because sounddevice frequently enumerates
# Logitech headsets under localized speaker labels that contain only "PRO X"
# and omit the vendor name, so the bare product token matches where "Logitech
# PRO X" would miss and fall back to the on-board Realtek device. The other
# bare tokens
# (Arctis, AirPods, Bose, …) exist for the same reason — many headsets list
# their model without the vendor prefix.
_HEADSET_PRIORITY = (
    "PRO X", "Logitech PRO X", "Logitech",
    "Jabra", "Sennheiser", "SteelSeries", "Arctis", "Corsair", "HyperX",
    "Razer", "Bose", "AirPods",
    "USB Audio", "Headset",
    "Realtek HD Audio", "Realtek",
)


#: Host API preference (lower number = better). User feedback 2026-04-22:
#: MME routes mono PCM to 8-channel surround partially onto silent channels
#: (Center/LFE/Rear) — the user hears nothing. WASAPI handles mono correctly
#: (duplicated onto Front-L+R). Windows MME is deprecated but often remains
#: registered as the system default.
#:
#: 2026-05-10: WDM-KS removed. PortAudio's WDM-KS backend does **not**
#: implement the **blocking stream API** ("Blocking API not supported yet"
#: / PaErrorCode -9999). Our AudioPlayer uses blocking ``stream.write()``
#: via ``sd.OutputStream`` — every open attempt crashes on WDM-KS.
#: WDM-KS now receives the default rank of 99 (effective last) and is also
#: actively filtered out in ``_resolve_output_device`` when the same physical
#: device is available on another host API.
#:
#: Cross-platform note: these keys are WINDOWS host-API names by design. On
#: macOS ("Core Audio") and Linux ("ALSA"/"JACK") nothing matches, every host
#: API gets the default rank 99, and PortAudio's enumeration order (= the OS
#: default) wins. That inert-by-data behavior is intentional — do not "fix"
#: it by adding a platform guard, and keep any future macOS/Linux preference
#: as new table entries, never as a rewrite of the ranking logic.
_HOSTAPI_PREFERENCE = {
    "Windows WASAPI": 0,     # modern, reliable mono routing, blocking OK
    "Windows DirectSound": 1,
    "MME": 2,                # deprecated, mono bug
    # "Windows WDM-KS": NOT mapped — blocking API not supported.
}

# Host APIs we NEVER choose as output host API because PortAudio's
# blocking API is not implemented on them (raises PaErrorCode -9999).
_FORBIDDEN_OUTPUT_HOSTAPIS = frozenset({"Windows WDM-KS"})


def _apply_edge_fades(pcm: bytes, sample_rate: int, fade_ms: int = 5) -> bytes:
    """Apply a linear fade-in + fade-out (each ``fade_ms``) to int16 mono PCM.

    Rationale: ``sd.play(blocking=True)`` tears down the WASAPI OutputStream
    completely on every call and reopens it for the next batch. Without fades,
    batches end/start at arbitrary sample values — this produces audible
    clicks/pops on stream reopen. A 5 ms fade is inaudible for speech audio but
    reliably eliminates discontinuities. It also dampens resampler edge ringing
    (scipy.signal.resample_poly) at batch boundaries — the "robotic squeak"
    between sentences.
    """
    if not pcm:
        return pcm
    arr = np.frombuffer(pcm, dtype=np.int16).copy()
    fade_samples = max(1, int(sample_rate * fade_ms / 1000))
    if arr.size < 2 * fade_samples:
        return pcm  # too short for clean fading
    ramp_in = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
    arr[:fade_samples] = (arr[:fade_samples].astype(np.float32) * ramp_in).astype(np.int16)
    ramp_out = np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
    arr[-fade_samples:] = (arr[-fade_samples:].astype(np.float32) * ramp_out).astype(np.int16)
    return arr.tobytes()


def _resample_int16(arr: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Rational resampling of int16 PCM (mono or stereo, N×C array).

    Uses ``scipy.signal.resample_poly`` when available — the highest-quality
    option (polyphase FIR, no aliasing). Falls back to linear interpolation
    if scipy is absent (test environments).
    """
    if src_rate == dst_rate:
        return arr
    from math import gcd
    g = gcd(src_rate, dst_rate)
    up = dst_rate // g
    down = src_rate // g
    try:
        from scipy.signal import resample_poly
        # Float32 for resample_poly, then back to int16
        arr_f = arr.astype(np.float32)
        out_f = resample_poly(arr_f, up, down, axis=0)
        return np.clip(out_f, -32768, 32767).astype(np.int16)
    except Exception:  # noqa: BLE001
        # scipy-free fallback: coarse linear interpolation
        n_out = int(arr.shape[0] * dst_rate / src_rate)
        idx = np.linspace(0, arr.shape[0] - 1, n_out)
        idx_int = idx.astype(np.int64)
        if arr.ndim == 1:
            return arr[idx_int]
        return arr[idx_int, :]


def _candidate_output_rates(source_rate: int, device_default: int = 0) -> list[int]:
    """Prioritised output sample rates for TTS playback.

    For speech TTS, an integer multiple of the source rate is noticeably
    cleaner than 24 kHz → 44.1 kHz. Many Windows headsets support 48 kHz
    even when the default is set to 44.1 kHz.
    """
    candidates: list[int] = []
    for rate in (
        source_rate,
        source_rate * 2 if source_rate in (22_050, 24_000) else 0,
        48_000,
        device_default,
        44_100,
    ):
        if rate and rate not in candidates:
            candidates.append(rate)
    return candidates


def _os_default_output_name(
    devices: Sequence[Any], hostapis: Sequence[Any]
) -> str | None:
    """Name of the user's OS-selected default OUTPUT device, when it is a real,
    usable sink — else None.

    The "your device first" contract: ``auto-headset`` prefers whatever the user
    picked as their system default speaker, EXCEPT when that default is a device
    auto-headset exists to avoid — a monitor/HDMI or SPDIF output, or the
    localized MME/DirectSound virtual mapper. In those cases this returns None so
    the resolver falls back to the generic headset heuristic instead of routing
    to a wrong/dead sink. The NAME (not the index) is returned so the existing
    candidate sort still picks the device's best host-API twin (WASAPI over MME)
    and skips WDM-KS. A missing default / absent sounddevice yields None.
    """
    try:
        default_out = sd.default.device[1]
    except Exception:  # noqa: BLE001 — no default / no sounddevice -> no preference
        return None
    if not isinstance(default_out, int) or not (0 <= default_out < len(devices)):
        return None
    dev = devices[default_out]
    name = str(dev.get("name", ""))
    if not name or dev.get("max_output_channels", 0) <= 0:
        return None
    low = name.lower()
    if any(b.lower() in low for b in _BLOCKED_OUTPUT_SUBSTRINGS):
        return None
    if is_legacy_primary_mapper(default_out, hostapis, devices, output=True):
        return None
    return name


def _resolve_output_device(
    device: int | str | None,
    priority: Sequence[str] | None = None,
) -> int | str | None:
    """Resolve "auto-headset" or None to a concrete device index.

    - int: returned as-is (user specified an explicit index)
    - None: system default
    - a concrete NAME (the Settings device picker persists names — the only
      identifier stable across reboots/hot-plugs): resolved to an index via
      :func:`jarvis.audio.devices.resolve_device_by_name` (best host-API twin,
      WDM-KS/mapper excluded). An unplugged/unknown name falls through to the
      auto-headset heuristic below — playback never bricks on a missing device.
    - "auto-headset": searches output devices for headset patterns, skips
      GPU-HDMI outputs (monitor speakers) and the localized MME/DirectSound
      virtual mapper, prefers WASAPI over MME (mono routing bug on 8-channel
      surround)

    ``priority`` is the user's own device-name preference
    (``[audio].output_device_priority``). When non-empty, a device whose name
    contains a user entry outranks EVERY generic ``_HEADSET_PRIORITY`` match, so
    a user with an uncommon headset (Focusrite, an audio interface, a specific
    dongle) wins by naming it — no code edit. Empty ``priority`` reproduces the
    generic-only behavior exactly.
    """
    if device is None or isinstance(device, int):
        return device
    if not isinstance(device, str):
        return device
    if device != "auto-headset":
        from jarvis.audio.devices import resolve_device_by_name

        named_idx = resolve_device_by_name(device, output=True)
        if named_idx is not None:
            log.info("Output device %r resolved to index %d.", device, named_idx)
            return named_idx
        log.warning(
            "Configured output device %r not found — falling back to "
            "auto-headset selection.",
            device,
        )
        # Fall through to the auto-headset heuristic below.

    user_priority = tuple(p for p in (priority or ()) if p)

    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception as exc:  # noqa: BLE001
        log.warning("Device query failed, using system default: %s", exc)
        return None

    # "Your device first": prefer the user's OS-selected default OUTPUT device
    # over the generic headset guesses, UNLESS it is a device auto-headset exists
    # to bypass (monitor/HDMI, SPDIF, the localized virtual mapper) — then fall
    # back to the heuristic below so playback never lands on a wrong/dead sink.
    # Injected as a NAME so the candidate sort still picks its best host-API twin
    # (WASAPI) and skips WDM-KS. Ranks BELOW an explicit output_device_priority.
    os_default_name = _os_default_output_name(devices, hostapis)
    effective_priority = (
        (*user_priority, os_default_name) if os_default_name else user_priority
    )

    # Candidates: real output devices that are not on the blocklist.
    # WDM-KS is filtered when the same device (same name) is available on
    # another host API — otherwise, for example, the only Realtek Speakers
    # device lands on WDM-KS and crashes later at OutputStream open with
    # -9999 'Blocking API not supported yet'.
    raw_candidates: list[tuple[int, dict, str]] = []  # (idx, dev, hostapi_name)
    for idx, d in enumerate(devices):
        if d.get("max_output_channels", 0) <= 0:
            continue
        name = d.get("name", "")
        low = name.lower()
        if any(blocked.lower() in low for blocked in _BLOCKED_OUTPUT_SUBSTRINGS):
            continue
        # Locale-independent skip of the MME "Sound Mapper" / DirectSound
        # "Primary Sound Driver" virtual router (translated display name).
        if is_legacy_primary_mapper(idx, hostapis, devices, output=True):
            continue
        hostapi_idx = d.get("hostapi", -1)
        hostapi_name = (
            hostapis[hostapi_idx].get("name", "")
            if 0 <= hostapi_idx < len(hostapis) else ""
        )
        raw_candidates.append((idx, d, hostapi_name))

    # WDM-KS crashes on OutputStream open (-9999 'Blocking API not supported
    # yet'). As long as ANY safe output device exists, we NEVER choose a
    # WDM-KS device. Important — and the bugfix versus the old same-name
    # logic: not even when a device name exists ONLY on WDM-KS (e.g.
    # "Speakers (Realtek HD Audio output)" has no MME/WASAPI twin).
    # The previously used same-name filter let exactly such WDM-KS-only
    # devices through; they won on name rank and crashed on playback
    # (BUG-014 recurrence 2026-05-24: Brain+TTS ok, user hears nothing).
    # Only when ALL candidates are WDM-KS do we take one as a last resort.
    safe_exists = any(
        ha not in _FORBIDDEN_OUTPUT_HOSTAPIS for _, _, ha in raw_candidates
    )
    candidates: list[tuple[int, dict]] = []
    for idx, d, ha in raw_candidates:
        if ha in _FORBIDDEN_OUTPUT_HOSTAPIS and safe_exists:
            continue
        candidates.append((idx, d))

    def _hostapi_rank(entry: tuple[int, dict]) -> int:
        hostapi_idx = entry[1].get("hostapi", -1)
        if 0 <= hostapi_idx < len(hostapis):
            hostapi_name = hostapis[hostapi_idx].get("name", "")
            return _HOSTAPI_PREFERENCE.get(hostapi_name, 99)
        return 99

    def _name_rank(entry: tuple[int, dict]) -> int:
        low = entry[1].get("name", "").lower()
        # Precedence: explicit user priority, then the OS-selected default device
        # (both carried in ``effective_priority``), then the generic headset list.
        # Each block keeps "earlier entry = stronger"; the generic block is offset
        # by len(effective_priority) so a generic hit can never tie or beat a
        # user / OS-default hit.
        for rank, sub in enumerate(effective_priority):
            if sub.lower() in low:
                return rank
        for rank, sub in enumerate(_HEADSET_PRIORITY):
            if sub.lower() in low:
                return len(effective_priority) + rank
        return len(effective_priority) + len(_HEADSET_PRIORITY)

    # Primary sort key: name match; secondary: host API preference. Without a
    # name match the original order (system default) is preserved.
    candidates.sort(key=lambda e: (_name_rank(e), _hostapi_rank(e)))
    if candidates:
        idx, d = candidates[0]
        hostapi_idx = d.get("hostapi", -1)
        hostapi_name = (
            hostapis[hostapi_idx].get("name", "?")
            if 0 <= hostapi_idx < len(hostapis) else "?"
        )
        log.info(
            "auto-headset → %s (idx=%d, ch=%s, hostapi=%s)",
            d.get("name"), idx, d.get("max_output_channels"), hostapi_name,
        )
        return idx

    log.warning("auto-headset found no matching device — using system default.")
    return None


class AudioPlayer:
    """Thread-safe async player for int16 PCM audio."""

    def __init__(
        self,
        device: int | str | None = None,
        sample_rate: int = TTS_SAMPLE_RATE,
        channels: int = 1,
        bus: Any = None,
        volume: float = 1.0,
        device_priority: Sequence[str] | None = None,
    ) -> None:
        # User-configured device-name priority ([audio].output_device_priority).
        # Consulted BEFORE the generic _HEADSET_PRIORITY default when resolving
        # "auto-headset", so an uncommon headset wins by name without a code
        # edit. Empty tuple = today's generic behavior. Kept so set_device can
        # re-resolve with the same preference after a hot-swap.
        self._device_priority: tuple[str, ...] = tuple(device_priority or ())
        # Resolve "auto-headset" / similar strings to the actual device index.
        # Integer values are not resolved — the user specifies those explicitly.
        self._device = _resolve_output_device(device, self._device_priority)
        self._sample_rate = sample_rate
        self._channels = channels
        # Master output volume knob in [0.0, 1.0]. Applied in _write_samples via
        # the shared gain helper (jarvis.audio.gain), which scales it to a makeup
        # boost + soft limiter so 100% is a real loudness lift, not just unity.
        # Clamped so a stray config/runtime value can never invert or over-range.
        self._volume = clamp_volume(volume)
        self._device_logged = False  # logged once on the first play call
        # Optional bus reference. When set, play_chunks() publishes
        # AudioOutFirst on the first audible sample so UI subscribers
        # (orb mouth animation, SPEAKING bubble) sync to actual audio start.
        self._bus = bus
        # Serialise concurrent playback. Two producers (Pre-Thinking Flash-
        # Brain announcement vs. streaming-brain answer) used to race to
        # play_chunks/play_pcm on the same device, opening separate
        # sd.OutputStream instances that WASAPI mixed on the speaker. See
        # docs/diagnostics/voice-overlap-2026-05-14.md. The lock is lazy
        # because the player is instantiated in sync code but acquired
        # from a running event loop. stop() deliberately stays outside the
        # lock so barge-in can preempt a held playback.
        self._play_lock: asyncio.Lock | None = None
        # Persistent OutputStream across play_chunks() calls. The streaming-
        # TTS pipeline calls play_chunks() once per sentence; without a
        # persistent stream every sentence boundary triggered a fresh
        # sd.OutputStream open/close cycle, which (a) re-paid WASAPI's
        # ~200 ms latency='high' prebuffer, (b) re-ran scipy.signal.
        # resample_poly polyphase FIR with edge ringing at sentence
        # boundaries — together producing the "haaaaa lalala oooo"
        # phoneme-stretch reported by the user on 2026-05-16. The stream
        # now stays open until stop() aborts it or the source rate changes.
        self._active_stream: sd.OutputStream | None = None
        self._active_source_rate: int | None = None
        self._active_device_rate: int | None = None
        # ``OutputStream`` setup runs in a worker thread while stop() runs on
        # the voice event loop. A stop can therefore land after setup began but
        # before the worker publishes the new stream. The generation + lock
        # make that late stream stale instead of letting it resurrect playback.
        self._playback_generation = 0
        self._stream_state_lock = threading.Lock()
        # Most devices expose at least two output channels, but small USB DACs,
        # accessibility devices, and virtual sinks may be genuinely mono. The
        # value is refreshed whenever a stream opens; stereo remains the safe
        # fallback when PortAudio cannot report device capabilities.
        self._stream_channels: int = 2
        # Cache of ((device_id, source_rate) -> working device_rate) per
        # AudioPlayer instance. Without this cache, every stop()+next-turn
        # restart pays the full samplerate-cascade cost — on the AB13X USB
        # headset that is one `OutputStream @ 24000Hz failed -9997` warning
        # followed by a 48000Hz open. The cache lets the second turn skip
        # the failure attempt entirely. See the 2026-05-16 Wave 2 diagnosis.
        #
        # 2026-05-18 H3 fix: keying by ``(device, source_rate)`` makes the
        # cache hot-swap-safe. Previously the key was only ``source_rate``,
        # so when the user yanked the USB headset and a different device
        # was resolved by the next ``_resolve_output_device`` call (or by
        # ``invalidate_device_cache()`` flipping ``self._device``), the
        # stale 48000 entry from the old device could be returned for a
        # device that doesn't support 48000 → silent TTS or PortAudio
        # ``-9997 Invalid sample rate``. Including the device in the key
        # makes the lookup miss for the new device and walk the cascade
        # fresh.
        self._device_rate_cache: dict[tuple[Any, int], int] = {}
        # Playback-progress telemetry for the pipeline stall watchdog (Wave-1
        # latency fix). ``last_write_ns`` is bumped after every successful
        # ``stream.write`` sub-block; the watchdog reads it to tell a healthy
        # back-pressure wait apart from a wedged device. See ``abort_active``.
        self._init_progress()

    def _init_progress(self) -> None:
        """(Re)set playback-progress counters.

        Separate from ``__init__`` so ``__new__``-built instances (test
        fixtures, hot-reload) can arm the counters without the full ctor.
        """
        self.frames_written: int = 0
        self.last_write_ns: int = 0
        # Owner of the current playback-progress counter. ``SpeechPipeline``
        # creates one ``play_chunks`` task for the main turn while background
        # announcements may also call ``play_chunks`` on the same shared player.
        # Without an owner stamp, an announcement's audio frame can look like
        # progress for the still-silent main turn and trip the mid-playback
        # stall watchdog.
        self.last_write_owner_task_id: int | None = None

    def _get_play_lock(self) -> asyncio.Lock:
        if self._play_lock is None:
            self._play_lock = asyncio.Lock()
        return self._play_lock

    def _get_stream_state_lock(self) -> threading.Lock:
        lock = getattr(self, "_stream_state_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._stream_state_lock = lock
        return lock

    @property
    def output_latency_s(self) -> float:
        """Return the active device's bounded PortAudio output latency.

        Blocking ``OutputStream.write()`` returns after PortAudio accepts a
        buffer, not necessarily after the device has made every accepted frame
        audible. Realtime half-duplex uses this value to keep microphone audio
        local until that hardware buffer has drained. Missing, malformed, or
        implausible backend values fail safely to zero so headless and test
        players retain the conservative fixed tail guard.
        """

        try:
            with self._get_stream_state_lock():
                stream = getattr(self, "_active_stream", None)
                if stream is None:
                    return 0.0
                raw_latency = getattr(stream, "latency", 0.0)
            if isinstance(raw_latency, (tuple, list)):
                raw_latency = raw_latency[-1] if raw_latency else 0.0
            latency = float(raw_latency)
        except (AttributeError, TypeError, ValueError, _PortAudioError):
            return 0.0
        if not math.isfinite(latency) or latency <= 0.0:
            return 0.0
        return min(latency, _MAX_REPORTED_OUTPUT_LATENCY_S)

    def invalidate_device_cache(self) -> None:
        """Forget every cached device_rate and tear down the active stream.

        Call this on USB hot-swap / device-disconnect / device-reset events
        (BUG-H3, 2026-05-18). Any subsequent ``play_pcm`` / ``play_chunks``
        will re-walk the samplerate cascade against the now-current device.

        The reason we cannot rely on PortAudio errors to detect a swap is
        that ``sd.OutputStream`` happily reports a write of zero frames as
        success when the underlying device has vanished — the audio just
        evaporates silently. Forcing the cascade on swap is the safe path.
        """
        with self._get_stream_state_lock():
            self._playback_generation = (
                getattr(self, "_playback_generation", 0) + 1
            )
            stream = self._active_stream
            self._active_stream = None
            self._active_source_rate = None
            self._active_device_rate = None
        if stream is not None:
            self._close_output_stream(stream)
        self._device_rate_cache.clear()

    def set_device(self, device: int | str | None) -> None:
        """Re-resolve the output device and drop any cached state tied to
        the old device.

        Use this when the user (or auto-detection) decides to switch
        headsets at runtime. Equivalent to invalidate_device_cache() plus
        ``self._device = _resolve_output_device(device)`` plus relogging
        the new device on the next play.
        """
        # ``getattr`` default keeps ``__new__``-built instances (test fixtures /
        # hot-reload that skip ``__init__``) working — same pattern as the
        # ``_volume`` / progress-counter reads elsewhere in this class.
        new_device = _resolve_output_device(
            device, getattr(self, "_device_priority", ())
        )
        if new_device == self._device:
            return  # no-op; avoid needless cache flush
        self._device = new_device
        self._device_logged = False  # re-log the new device on next play
        self.invalidate_device_cache()

    def set_volume(self, volume: float) -> None:
        """Live-apply a new master output volume (0.0–1.0) — no stream restart.

        The value is read per sub-block in ``_write_samples``, so a change made
        mid-utterance takes effect on the next ~60 ms block. Clamped to [0, 1];
        the open PortAudio stream is untouched (gain rides on top of it,
        orthogonal to device/rate/lifecycle state).
        """
        self._volume = clamp_volume(volume)

    def _log_device_once(self) -> None:
        """Log the active output device once per AudioPlayer instance.

        Critical for bug diagnosis: when TTS is running but the user hears
        nothing, playback may be going to the wrong device (HDMI monitor
        instead of the headset).
        """
        if self._device_logged:
            return
        self._device_logged = True
        try:
            if self._device is None:
                default_out = sd.default.device[1]
                dev_info = sd.query_devices(default_out)
                log.info(
                    "AudioPlayer using system default output: %s "
                    "(idx=%s, ch=%s, rate=%s)",
                    dev_info.get("name"), default_out,
                    dev_info.get("max_output_channels"),
                    int(dev_info.get("default_samplerate", 0)),
                )
            else:
                dev_info = sd.query_devices(self._device)
                log.info(
                    "AudioPlayer using device: %s (idx=%s)",
                    dev_info.get("name"), self._device,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("AudioPlayer device query failed: %s", exc)

    async def play_pcm(self, pcm: bytes, sample_rate: int | None = None) -> None:
        """Play a complete PCM byte blob — single-shot (ACK chimes, alerts).

        Opens an OutputStream briefly, writes the blob, then closes it.
        Edge fades (5 ms) smooth the open/close transition so the short-lived
        stream produces no clicks. For streaming TTS playback see
        ``play_chunks``, which keeps a persistent stream open.
        """
        self._log_device_once()
        rate = sample_rate or self._sample_rate
        pcm = _apply_edge_fades(pcm, rate)
        async with self._get_play_lock():
            await asyncio.to_thread(self._play_blob, pcm, rate)

    def _play_blob(self, pcm: bytes, source_rate: int) -> None:
        """Sync: open stream, write blob, close stream."""
        arr = np.frombuffer(pcm, dtype=np.int16)
        stream, device_rate = self._open_output_stream(source_rate)
        try:
            self._write_samples(stream, arr, source_rate, device_rate)
        finally:
            self._close_output_stream(stream)

    def _open_output_stream(self, source_rate: int) -> tuple[sd.OutputStream, int]:
        """Open a persistent ``sd.OutputStream`` (float32 stereo or mono).

        Tries candidate rates (device default → 48 kHz → 44.1 kHz → source)
        until PortAudio stops failing with -9997 "Invalid sample rate". The
        working rate is returned as ``device_rate`` — the caller must resample
        int16 PCM from ``source_rate`` to ``device_rate`` before ``stream.write()``
        if the two differ.

        Community best practice (sounddevice docs ``play_long_file.py``,
        RealtimeTTS, Pipecat, LiveKit-Agents):

        - ``dtype='float32'`` — int16/int32 crackles on some devices
          (spatialaudio/python-sounddevice#347).
        - ``latency='high'`` — stable on WASAPI shared mode without underruns;
          PortAudio#303 documents "bursts + pauses" with buffers that are too small.
        - ``blocksize=0`` — PortAudio chooses the optimal block size; fixed
          values below ~480 frames force underruns.
        - ``channels=2`` + mono duplication in the caller — 7.1 surround headsets
          (e.g. Logitech PRO X) otherwise route mono to silent Center/LFE/Rear.
          A device reporting only one output channel opens as mono instead.
        """
        try:
            try:
                # With device=None, ``kind='output'`` requests the default
                # output record instead of the complete PortAudio device list.
                dev_info = sd.query_devices(self._device, "output")
            except TypeError:
                # Compatibility with simple test doubles and older
                # sounddevice-compatible implementations.
                dev_info = sd.query_devices(self._device)
            dev_default = int(dev_info.get("default_samplerate", 0))
            max_output_channels = int(dev_info.get("max_output_channels", 0) or 0)
        except Exception:  # noqa: BLE001
            dev_default = 0
            max_output_channels = 0

        # Preserve stereo routing for every device that supports it (important
        # for surround headsets), while allowing true mono-only endpoints to
        # open instead of failing with "Invalid number of channels".
        stream_channels = 1 if max_output_channels == 1 else 2

        # Skip the cascade if we already learned which rate works for this
        # (device, source_rate) pair. Cuts log-noise and open-latency on
        # every turn after the first (Wave 2 fix, 2026-05-16).
        #
        # Device-keyed (H3 fix, 2026-05-18): on USB hot-swap the resolved
        # device index changes, so the lookup misses and we walk the
        # cascade for the new device — no stale 48000 hit on a Realtek
        # board that only does 44.1k.
        cache_key = (self._device, source_rate)
        cached_rate = self._device_rate_cache.get(cache_key)
        if cached_rate is not None:
            candidates = [cached_rate]
        else:
            candidates = _candidate_output_rates(source_rate, dev_default)

        last_exc: BaseException | None = None
        for target_rate in candidates:
            try:
                # latency=0.2 reserves a real 200 ms PortAudio buffer.
                # The string keyword "high" is a DEVICE HINT — on USB
                # headsets like the AB13X it resolves to ~10 ms, far too
                # small to absorb inter-sentence pipeline gaps. The Wave 2
                # diagnosis on 2026-05-16 attributed audible "crackling +
                # slowdown" to this 10 ms drain. 0.2 s matches the buffer
                # depth used by LiveKit-Agents / Pipecat / RealtimeTTS for
                # TTS-streaming on WASAPI shared mode.
                stream = sd.OutputStream(
                    samplerate=target_rate,
                    device=self._device,
                    channels=stream_channels,
                    dtype="float32",
                    blocksize=0,
                    latency=0.2,
                )
                stream.start()
                self._stream_channels = stream_channels
                self._device_rate_cache[cache_key] = target_rate
                log.info(
                    "OutputStream opened @ %d Hz (source=%d Hz, device=%s, "
                    "channels=%d, actual_latency=%.3fs)",
                    target_rate,
                    source_rate,
                    self._device,
                    stream_channels,
                    stream.latency,
                )
                return stream, target_rate
            except _PortAudioError as exc:
                last_exc = exc
                if "-9997" not in str(exc) and "Invalid sample rate" not in str(exc):
                    raise
                log.warning(
                    "OutputStream @ %dHz failed (%s) — trying next rate …",
                    target_rate, exc,
                )

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("No supported sample rate found")

    def _write_samples(
        self,
        stream: sd.OutputStream,
        arr: np.ndarray,
        source_rate: int,
        device_rate: int,
        *,
        playback_generation: int | None = None,
    ) -> None:
        """Int16 mono → float32 device channels + resample + ``stream.write()``.

        Blocks until the internal PortAudio buffer has room — this is the
        natural back-pressure mechanism (no user-side batching needed).

        Captures the ``underflowed`` flag from ``stream.write()``. ``True``
        means WASAPI had to fill with silence between this write and the
        previous one — i.e. the PortAudio buffer ran empty mid-stream. On
        the user side this sounds like crackling or brief dropouts. We log
        every occurrence instead of discarding the flag as before
        (Wave-2 diagnosis 2026-05-16).

        Phantom-Underflow note (2026-05-18 audit-4 MED): PortAudio may set
        ``underflowed`` on the very first write of a freshly-opened stream
        even when the pipeline is healthy, because the 200 ms latency
        buffer hasn't yet filled. The warning is intentionally NOT
        suppressed for first-writes — silencing it would mask legitimate
        mid-stream underruns. If log spam becomes a problem, the right
        place to gate is at log-handler level (rate-limit per (device,
        rate) tuple), not here.
        """
        if arr.size == 0:
            return
        if arr.ndim == 1 and source_rate != device_rate:
            arr = _resample_int16(arr, source_rate, device_rate)
        # int16 [-32768, 32767] → float32 [-1.0, 1.0)
        arr_f = arr.astype(np.float32) * (1.0 / 32768.0)
        # Mono → the device's actual stream width. Stereo duplication keeps the
        # established Front-L/Front-R routing on surround headsets; a mono-only
        # endpoint receives an explicit N×1 array accepted by sounddevice.
        if arr_f.ndim == 1:
            if getattr(self, "_stream_channels", 2) == 1:
                arr_f = arr_f.reshape(-1, 1)
            else:
                arr_f = np.column_stack((arr_f, arr_f))
        # column_stack returns an array whose strides may not match what
        # PortAudio expects — copy ensures C-contiguous layout. Cheap
        # (the buffer is at most ~120 ms of stereo float32).
        if not arr_f.flags["C_CONTIGUOUS"]:
            arr_f = np.ascontiguousarray(arr_f)
        # Write in ~60 ms sub-blocks, feeding the LIVE output RMS per block, so
        # the jarvis-bar equalizer reacts to Jarvis's voice exactly like it
        # reacts to your mic — moving with the actual loudness instead of one
        # coarse level per sentence that left the bars frozen and blocky. It is
        # the SAME continuous PortAudio stream (no clicks), and the blocking
        # write still provides back-pressure. ~60 ms blocks are large enough to
        # never starve the buffer.
        feed_level = level_tap.has_subscribers()
        # Master output volume: scale the whole buffer once via the shared gain
        # helper (makeup boost + soft limiter above unity, plain attenuation
        # below), then write it in sub-blocks. ``arr_out is arr_f`` when the knob
        # sits exactly at unity, so full playback stays byte-identical. The
        # visualizer is fed the PRE-gain RMS (arr_f), so the orb/equalizer keeps
        # tracking the speech itself — full bars even when the volume is low, and
        # not artificially pumped when it is boosted. ``getattr`` default keeps
        # ``__new__``-built test/hot-reload instances (which skip ``__init__``)
        # at unity instead of crashing.
        arr_out = apply_output_gain(arr_f, getattr(self, "_volume", 1.0))
        block = max(1, int(device_rate * 0.06))
        for start in range(0, arr_out.shape[0], block):
            out = arr_out[start:start + block]
            try:
                underflowed = stream.write(out)
            except _PortAudioError:
                # ``stop()`` deliberately aborts the native stream while this
                # blocking write may still be running in a worker thread. Core
                # Audio and several Windows backends report that expected abort
                # as a PortAudio error. Suppress it only when the generation or
                # stream owner proves cancellation won; a live-device failure
                # still propagates to the caller.
                if playback_generation is None:
                    raise
                with self._get_stream_state_lock():
                    cancelled = (
                        getattr(self, "_playback_generation", 0)
                        != playback_generation
                        or getattr(self, "_active_stream", None) is not stream
                    )
                if not cancelled:
                    raise
                log.debug("PortAudio write ended after playback cancellation")
                return
            # Playback progress for the pipeline stall watchdog: a healthy
            # ~60 ms sub-block returns well inside the watchdog's stall window;
            # only a wedged device leaves ``last_write_ns`` frozen. ``getattr``
            # defaults keep this resilient for ``__new__``-built instances (test
            # fixtures / hot-reload) that skipped ``_init_progress`` — progress
            # telemetry must never be the thing that crashes playback.
            self.frames_written = getattr(self, "frames_written", 0) + int(out.shape[0])
            self.last_write_ns = time.monotonic_ns()
            if underflowed:
                log.warning(
                    "PortAudio underflow during write (frames=%d, source=%dHz, "
                    "device=%dHz) — buffer drained mid-stream, audible click/crackle",
                    out.shape[0], source_rate, device_rate,
                )
            if feed_level:
                pre = arr_f[start:start + block]  # PRE-gain RMS for the visualizer
                if pre.size:
                    level_tap.feed(float(np.sqrt(np.mean(np.square(pre)))))

    def _close_output_stream(self, stream: sd.OutputStream) -> None:
        """Flush and stop: ``stream.stop()`` blocks until the buffer is empty."""
        try:
            stream.stop()
        except Exception as exc:  # noqa: BLE001
            log.warning("stream.stop() failed: %s", exc)
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            log.debug("Output stream close failed", exc_info=True)

    async def play_chunks(
        self,
        chunks: AsyncIterator[AudioChunk],
        *,
        should_play: Callable[[], bool] | None = None,
    ) -> bool:
        """Stream TTS chunks into a persistent output stream.

        Return ``True`` only when at least one PCM frame was accepted by the
        device stream. A producer that yields no audio, or a stale playback
        rejected by ``should_play``, returns ``False``. Callers use this receipt
        to keep the audible transcript free of text that never reached output.

        ``should_play`` is an optional staleness predicate evaluated after the
        play lock is acquired and before each buffered write. When it returns
        False the playback is dropped silently — the ack preamble uses this so
        it is never voiced once the main answer has started speaking (2026-06-20
        'preamble after the answer' defense-in-depth).

        History — what went wrong before:
            * First version: ``sd.play(blocking=True)`` **per chunk** — each
              20 ms fragment tore down and rebuilt the WASAPI stream completely.
              On 7.1 headsets, short fragments landed on silent channels or
              were lost in the reopen overhead.
            * Second version: accumulate chunks into 1 s / 500 ms / 200 ms
              batches and call ``sd.play(blocking=True)`` per batch. Same
              problem, just less frequent — plus resampler edge ringing at
              every batch boundary, which manifested as a "robotic squeak".

        2026-04-24 — industry standard (Pipecat, LiveKit-Agents, RealtimeTTS,
        sounddevice docs ``play_long_file.py``, Cartesia SDK): **a single,
        permanently open** ``sd.OutputStream`` per response, fed via
        ``stream.write()``. PortAudio blocks internally when its buffer is
        full — no user-space batching needed. Stream reopen only on a sample
        rate change (Gemini 24 kHz → SAPI5 fallback 22 kHz) — an edge case.

        No edge fades on chunks: the stream stays open and chunks are
        appended seamlessly → no discontinuity, no clicks.
        """
        self._log_device_once()
        stream_state_lock = self._get_stream_state_lock()
        with stream_state_lock:
            playback_generation = getattr(self, "_playback_generation", 0)
        owner_task = asyncio.current_task()
        owner_task_id = id(owner_task) if owner_task is not None else None
        # Reset the playback-progress watchdog signal at the START of every
        # playback — BEFORE awaiting the play lock, so the invariant
        # "last_write_ns == 0 until this playback's first frame" holds from the
        # task's very first event-loop tick, not just from lock acquisition.
        # ``last_write_ns`` is otherwise only zeroed once in ``_init_progress``
        # and then carries the PREVIOUS turn's timestamp across turns. The
        # pipeline stall watchdog reads it, so after a >stall-window thinking gap
        # it saw a stale-but-non-zero value and aborted the fresh, still-
        # synthesizing answer before its first frame (a false "device-wedge") —
        # the "Jarvis listens forever / never speaks" root cause. Resetting here
        # restores the watchdog's <=0 "no first frame yet" guard for every turn,
        # not just the first, and closes the lock-wait window (a lock held by a
        # non-writing op such as a slow stream-open must not leave a stale value
        # visible to the watchdog).
        self.last_write_owner_task_id = owner_task_id
        self.last_write_ns = 0
        self.frames_written = 0
        # Lazy lock acquisition (2026-06-20 'preamble spoken AFTER the answer'):
        # pull the first NON-EMPTY chunk BEFORE taking the play lock. The
        # streaming answer's play task is created at turn-start and, on a long
        # tool turn, blocks for seconds waiting for its first audio chunk while
        # the brain runs the tool. Grabbing the lock up front held the device
        # idle for that whole gap, so a concurrently-published ack preamble
        # blocked behind the still-silent answer and was voiced AFTER it. Pulling
        # the first chunk first means the lock is held ONLY for real playback —
        # the preamble can play during the gap. ``last_write_ns`` stays 0 through
        # this pre-lock wait, preserving the BUG-032 "no first frame yet"
        # watchdog invariant.
        chunk_aiter = chunks.__aiter__()
        first_chunk: AudioChunk | None = None
        while True:
            try:
                candidate = await chunk_aiter.__anext__()
            except StopAsyncIteration:
                return False  # producer yielded nothing
            if candidate.pcm:
                first_chunk = candidate
                break

        async def _from_first() -> AsyncIterator[AudioChunk]:
            if first_chunk is not None:
                yield first_chunk
            async for _c in chunk_aiter:
                yield _c

        async with self._get_play_lock():
            # Staleness gate: re-check at the last moment before any audio is
            # written. A preamble whose synthesis / lock-wait was overtaken by
            # the main answer is dropped here rather than queued behind it.
            if should_play is not None and not should_play():
                return False
            def _ensure_stream(needed_rate: int) -> tuple[sd.OutputStream, int]:
                # Reuse the persistent OutputStream across sentence-by-sentence
                # play_chunks() calls — closing+reopening per sentence is what
                # caused the "haaaaa lalala oooo" stretch (see __init__ comment).
                with stream_state_lock:
                    if (
                        getattr(self, "_playback_generation", 0)
                        != playback_generation
                    ):
                        raise _PlaybackSuperseded
                    if (
                        self._active_stream is not None
                        and self._active_source_rate == needed_rate
                    ):
                        assert self._active_device_rate is not None
                        return self._active_stream, self._active_device_rate
                    old_stream = self._active_stream
                    self._active_stream = None
                    self._active_source_rate = None
                    self._active_device_rate = None
                # Stream open is intentionally outside the state lock: stop()
                # must remain immediate even when PortAudio setup is slow.
                if old_stream is not None:
                    self._close_output_stream(old_stream)
                new_stream, device_rate = self._open_output_stream(needed_rate)
                with stream_state_lock:
                    if (
                        getattr(self, "_playback_generation", 0)
                        == playback_generation
                    ):
                        self._active_stream = new_stream
                        self._active_source_rate = needed_rate
                        self._active_device_rate = device_rate
                        return new_stream, device_rate
                # stop() won while the worker opened PortAudio. Close the late
                # handle locally; never publish it as the active stream.
                self._close_output_stream(new_stream)
                raise _PlaybackSuperseded

            pending = bytearray()
            pending_rate: int | None = None
            first_audio_published = False

            async def _flush_pending(*, final: bool = False) -> bool:
                nonlocal pending, pending_rate, first_audio_published
                if not pending or pending_rate is None:
                    return True
                min_bytes = int(pending_rate * TTS_WRITE_BUFFER_MS / 1000) * 2
                if not final and len(pending) < min_bytes:
                    return True
                if should_play is not None and not should_play():
                    pending.clear()
                    return False
                pcm = bytes(pending)
                pending.clear()
                try:
                    stm, dev_rate = await asyncio.to_thread(
                        _ensure_stream, pending_rate
                    )
                except _PlaybackSuperseded:
                    pending.clear()
                    return False
                arr = np.frombuffer(pcm, dtype=np.int16)
                # Tell the UI how long this block will be audible BEFORE the
                # blocking write below. _write_samples blocks for the whole
                # playback with no further level, so the level tap alone makes
                # the jarvis bar fall back to its "thinking" wave mid-sentence.
                # note_playing marks the playback window so the bar shows the
                # speaking equalizer for the entire block (mono samples / rate).
                if arr.size:
                    level_tap.note_playing(arr.size / pending_rate)
                # The live TTS output amplitude is now fed PER ~60 ms sub-block
                # from inside _write_samples (so the jarvis-bar equalizer moves
                # with Jarvis's voice across the whole sentence, not one coarse
                # level per flush). Nothing to feed here.
                self.last_write_owner_task_id = owner_task_id
                await asyncio.to_thread(
                    self._write_samples,
                    stm,
                    arr,
                    pending_rate,
                    dev_rate,
                    playback_generation=playback_generation,
                )
                with stream_state_lock:
                    playback_superseded = (
                        getattr(self, "_playback_generation", 0)
                        != playback_generation
                        or getattr(self, "_active_stream", None) is not stm
                    )
                if playback_superseded:
                    return False
                # First audible sample reached PortAudio — tell the bus so the
                # mascot mouth + SPEAKING bubble sync to actual audio start
                # instead of the speculative SPEAKING state-transition.
                # MUST be awaited: EventBus.publish is an async coroutine.
                if not first_audio_published and self._bus is not None:
                    first_audio_published = True
                    try:
                        await self._bus.publish(AudioOutFirst())
                        log.info("AudioOutFirst published")
                    except Exception as exc:  # noqa: BLE001
                        log.debug("AudioOutFirst publish failed: %s", exc)
                return True

            # NOTE: no finally-close — the stream stays open across play_chunks
            # calls and is only torn down by stop() (barge-in) or by the next
            # _ensure_stream call that observes a sample-rate mismatch.
            async for chunk in _from_first():
                if not chunk.pcm:
                    continue
                if pending_rate is not None and chunk.sample_rate != pending_rate:
                    if not await _flush_pending(final=True):
                        return False
                pending_rate = chunk.sample_rate
                pending.extend(chunk.pcm)
                if not await _flush_pending():
                    return False
            if not await _flush_pending(final=True):
                return False
            return self.frames_written > 0

    def abort_active(self) -> None:
        """Force-abort the live OutputStream to unblock a wedged ``stream.write``.

        PortAudio's blocking write runs in a worker thread that Python cannot
        cancel; ``Pa_AbortStream`` (``stream.abort()``) is the only way to make
        the blocked write return so the ``asyncio.to_thread`` unwinds. Called by
        the pipeline playback watchdog on a device stall (no audio frames for
        the stall window) so the voice turn unwinds and the session re-arms,
        instead of hanging until the 120 s ceiling. Idempotent; cross-platform
        (PortAudio abort behaves identically on Windows/macOS/Linux).
        """
        level_tap.reset_playing()
        with self._get_stream_state_lock():
            self._playback_generation = (
                getattr(self, "_playback_generation", 0) + 1
            )
            stream = self._active_stream
            self._active_stream = None
            self._active_source_rate = None
            self._active_device_rate = None
        if stream is not None:
            try:
                stream.abort()
            except Exception as exc:  # noqa: BLE001
                log.debug("abort_active: stream.abort() failed: %s", exc)
            try:
                stream.close()
            except Exception:  # noqa: BLE001, S110
                pass

    def stop(self) -> None:
        """Abort ongoing playback (e.g. for barge-in).

        Important: ``sd.stop()`` only affects streams started via ``sd.play()``
        — the persistent ``sd.OutputStream`` from ``play_chunks`` is invisible
        to ``sd.stop()``. We therefore also call ``stream.abort()``
        (Pa_AbortStream: discards buffered audio immediately, unlike
        ``stream.stop()`` which waits for the drain — for barge-in we want
        the fast discard).
        """
        # Barge-in discards the buffered tail, so the UI must stop showing the
        # speaking equalizer for audio that will never play.
        level_tap.reset_playing()
        with self._get_stream_state_lock():
            self._playback_generation = (
                getattr(self, "_playback_generation", 0) + 1
            )
            stream = self._active_stream
            self._active_stream = None
            self._active_source_rate = None
            self._active_device_rate = None
        if stream is not None:
            try:
                stream.abort()
            except Exception as exc:  # noqa: BLE001
                log.debug("OutputStream.abort() failed: %s", exc)
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                log.debug("Output stream close during shutdown failed", exc_info=True)
        # sounddevice is None on hosts without PortAudio (headless server) —
        # stop() must stay callable there instead of raising AttributeError.
        if sd is not None:
            sd.stop()
