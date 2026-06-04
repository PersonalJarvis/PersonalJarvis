"""Audio playback via sounddevice (WASAPI).

Consumes either ready-made PCM bytes or an `AsyncIterator[AudioChunk]`
for pseudo-streaming (sentence-by-sentence synthesis while playback runs).

Gemini 3.1 Flash TTS delivers `audio/l16` (raw linear PCM 24 kHz 16-bit mono).
No decoding needed — reinterpret directly as numpy.int16 and pass to PortAudio.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
import sounddevice as sd

from jarvis.audio import level_tap
from jarvis.core.events import AudioOutFirst
from jarvis.core.protocols import AudioChunk

log = logging.getLogger("jarvis.audio.player")

TTS_SAMPLE_RATE = 24_000  # Gemini 3.1 Flash TTS output rate
TTS_WRITE_BUFFER_MS = 120

# Audio devices we NEVER select as auto-headset output — monitor speakers
# via HDMI/DisplayPort. Typical GPU audio chip names.
_BLOCKED_OUTPUT_SUBSTRINGS = (
    "NVIDIA High Definition",  # NVIDIA GPU audio (monitor via HDMI/DP)
    "AMD HD Audio",            # AMD GPU audio
    "Primärer Soundtreiber",  # Windows primary hook, ambiguous
    "Microsoft Soundmapper",
    "SPDIF",                   # digital-out with no target on unknown setups
)

# Preference order for "auto-headset": the first match is used.
# 2026-05-10: "PRO X" is listed first because sounddevice often lists Logitech
# headsets as "Lautsprecher (PRO X)" (without the manufacturer name) — "Logitech
# PRO X" misses the user's headset and falls back to the Realtek on-board device.
_HEADSET_PRIORITY = (
    "PRO X", "Logitech PRO X", "Logitech",
    "Jabra", "Sennheiser", "SteelSeries", "Corsair", "HyperX", "Razer",
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


def _resolve_output_device(device: int | str | None) -> int | str | None:
    """Resolve "auto-headset" or None to a concrete device index.

    - int: returned as-is (user specified an explicit index)
    - None: system default
    - "auto-headset": searches output devices for headset patterns, skips
      GPU-HDMI outputs (monitor speakers), prefers WASAPI over MME
      (mono routing bug on 8-channel surround)
    """
    if device is None or isinstance(device, int):
        return device
    norm = device.strip().lower() if isinstance(device, str) else ""
    # "auto" / "" → OS default output (the speaker/headphones the user selected
    # in Windows). A literal "auto" used to reach sounddevice as a device NAME →
    # "No output device matching 'auto'" → TTS playback failed and Jarvis stayed
    # silent. "auto-headset" keeps the smart headset / WDM-KS-avoiding picker.
    if norm in ("auto", ""):
        return None
    if norm != "auto-headset":
        return device

    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception as exc:  # noqa: BLE001
        log.warning("Device-Query fehlgeschlagen, nutze System-Default: %s", exc)
        return None

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
        if any(blocked in name for blocked in _BLOCKED_OUTPUT_SUBSTRINGS):
            continue
        hostapi_idx = d.get("hostapi", -1)
        hostapi_name = (
            hostapis[hostapi_idx].get("name", "")
            if 0 <= hostapi_idx < len(hostapis) else ""
        )
        raw_candidates.append((idx, d, hostapi_name))

    # WDM-KS crasht beim OutputStream-Open (-9999 'Blocking API not supported
    # yet'). Solange IRGENDEIN sicheres Output-Device existiert, waehlen wir NIE
    # ein WDM-KS-Device. Wichtig — und der Bugfix gegenueber der alten Same-Name-
    # Logik: auch dann nicht, wenn ein Geraetename NUR auf WDM-KS existiert
    # (z.B. "Speakers (Realtek HD Audio output)" hat keinen MME/WASAPI-Zwilling).
    # Der frueher genutzte Same-Name-Filter liess genau solche WDM-KS-only-
    # Devices durch; sie gewannen per Name-Rang und crashten beim Playback
    # (BUG-014 Wiederholung 2026-05-24: Brain+TTS ok, User hoert nichts).
    # Nur wenn ALLE Kandidaten WDM-KS sind, nehmen wir als letztes Mittel eins.
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
        name = entry[1].get("name", "")
        for rank, sub in enumerate(_HEADSET_PRIORITY):
            if sub.lower() in name.lower():
                return rank
        return len(_HEADSET_PRIORITY)

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

    log.warning("auto-headset fand kein passendes Device — System-Default.")
    return None


class AudioPlayer:
    """Thread-safe async player for int16 PCM audio."""

    def __init__(
        self,
        device: int | str | None = None,
        sample_rate: int = TTS_SAMPLE_RATE,
        channels: int = 1,
        bus: Any = None,
    ) -> None:
        # Resolve "auto-headset" / similar strings to the actual device index.
        # Integer values are not resolved — the user specifies those explicitly.
        self._device = _resolve_output_device(device)
        self._sample_rate = sample_rate
        self._channels = channels
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
        # Cache of ((device_id, source_rate) -> working device_rate) per
        # AudioPlayer instance. Without this cache, every stop()+next-turn
        # restart pays the full samplerate-cascade cost — on the AB13X USB
        # headset that is one `OutputStream @ 24000Hz failed -9997` warning
        # followed by a 48000Hz open. The cache lets the second turn skip
        # the failure attempt entirely. See 2026-05-16 Welle-2 diagnosis.
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

    def _get_play_lock(self) -> asyncio.Lock:
        if self._play_lock is None:
            self._play_lock = asyncio.Lock()
        return self._play_lock

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
        if self._active_stream is not None:
            self._close_output_stream(self._active_stream)
            self._active_stream = None
            self._active_source_rate = None
            self._active_device_rate = None
        self._device_rate_cache.clear()

    def set_device(self, device: int | str | None) -> None:
        """Re-resolve the output device and drop any cached state tied to
        the old device.

        Use this when the user (or auto-detection) decides to switch
        headsets at runtime. Equivalent to invalidate_device_cache() plus
        ``self._device = _resolve_output_device(device)`` plus relogging
        the new device on the next play.
        """
        new_device = _resolve_output_device(device)
        if new_device == self._device:
            return  # no-op; avoid needless cache flush
        self._device = new_device
        self._device_logged = False  # re-log the new device on next play
        self.invalidate_device_cache()

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
                    "AudioPlayer nutzt System-Default-Output: %s (idx=%s, ch=%s, rate=%s)",
                    dev_info.get("name"), default_out,
                    dev_info.get("max_output_channels"),
                    int(dev_info.get("default_samplerate", 0)),
                )
            else:
                dev_info = sd.query_devices(self._device)
                log.info(
                    "AudioPlayer nutzt Device: %s (idx=%s)",
                    dev_info.get("name"), self._device,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("AudioPlayer Device-Abfrage fehlgeschlagen: %s", exc)

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
        """Open a persistent ``sd.OutputStream`` (float32 stereo).

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
        """
        try:
            dev_info = sd.query_devices(self._device)
            dev_default = int(dev_info.get("default_samplerate", 0))
        except Exception:  # noqa: BLE001
            dev_default = 0

        # Skip the cascade if we already learned which rate works for this
        # (device, source_rate) pair. Cuts log-noise and open-latency on
        # every turn after the first (Welle-2 fix, 2026-05-16).
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
                # small to absorb inter-sentence pipeline gaps. The Welle-2
                # diagnosis on 2026-05-16 attributed audible "crackling +
                # slowdown" to this 10 ms drain. 0.2 s matches the buffer
                # depth used by LiveKit-Agents / Pipecat / RealtimeTTS for
                # TTS-streaming on WASAPI shared mode.
                stream = sd.OutputStream(
                    samplerate=target_rate,
                    device=self._device,
                    channels=2,
                    dtype="float32",
                    blocksize=0,
                    latency=0.2,
                )
                stream.start()
                self._device_rate_cache[cache_key] = target_rate
                log.info(
                    "OutputStream opened @ %d Hz (source=%d Hz, device=%s, "
                    "actual_latency=%.3fs)",
                    target_rate, source_rate, self._device, stream.latency,
                )
                return stream, target_rate
            except sd.PortAudioError as exc:
                last_exc = exc
                if "-9997" not in str(exc) and "Invalid sample rate" not in str(exc):
                    raise
                log.warning(
                    "OutputStream @ %dHz failed (%s) — naechste Rate …",
                    target_rate, exc,
                )

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Keine unterstuetzte Samplerate gefunden")

    def _write_samples(
        self,
        stream: sd.OutputStream,
        arr: np.ndarray,
        source_rate: int,
        device_rate: int,
    ) -> None:
        """Int16 mono → float32 stereo + resample + ``stream.write()``.

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
        # Mono → stereo (Front-L, Front-R duplicated)
        if arr_f.ndim == 1:
            arr_f = np.column_stack((arr_f, arr_f))
        # column_stack returns an array whose strides may not match what
        # PortAudio expects — copy ensures C-contiguous layout. Cheap
        # (the buffer is at most ~120 ms of stereo float32).
        if not arr_f.flags["C_CONTIGUOUS"]:
            arr_f = np.ascontiguousarray(arr_f)
        underflowed = stream.write(arr_f)
        if underflowed:
            log.warning(
                "PortAudio underflow during write (frames=%d, source=%dHz, "
                "device=%dHz) — buffer drained mid-stream, audible click/crackle",
                arr_f.shape[0], source_rate, device_rate,
            )

    def _close_output_stream(self, stream: sd.OutputStream) -> None:
        """Flush and stop: ``stream.stop()`` blocks until the buffer is empty."""
        try:
            stream.stop()
        except Exception as exc:  # noqa: BLE001
            log.warning("stream.stop() failed: %s", exc)
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            pass

    async def play_chunks(self, chunks: AsyncIterator[AudioChunk]) -> None:
        """Stream TTS chunks into a persistent WASAPI OutputStream.

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
        async with self._get_play_lock():
            def _ensure_stream(needed_rate: int) -> tuple[sd.OutputStream, int]:
                # Reuse the persistent OutputStream across sentence-by-sentence
                # play_chunks() calls — closing+reopening per sentence is what
                # caused the "haaaaa lalala oooo" stretch (see __init__ comment).
                if (
                    self._active_stream is not None
                    and self._active_source_rate == needed_rate
                ):
                    assert self._active_device_rate is not None
                    return self._active_stream, self._active_device_rate
                # Rate change or initial open: close the old stream, open a new one
                if self._active_stream is not None:
                    self._close_output_stream(self._active_stream)
                    self._active_stream = None
                new_stream, device_rate = self._open_output_stream(needed_rate)
                self._active_stream = new_stream
                self._active_source_rate = needed_rate
                self._active_device_rate = device_rate
                return new_stream, device_rate

            pending = bytearray()
            pending_rate: int | None = None
            first_audio_published = False

            async def _flush_pending(*, final: bool = False) -> None:
                nonlocal pending, pending_rate, first_audio_published
                if not pending or pending_rate is None:
                    return
                min_bytes = int(pending_rate * TTS_WRITE_BUFFER_MS / 1000) * 2
                if not final and len(pending) < min_bytes:
                    return
                pcm = bytes(pending)
                pending.clear()
                stm, dev_rate = await asyncio.to_thread(_ensure_stream, pending_rate)
                arr = np.frombuffer(pcm, dtype=np.int16)
                await asyncio.to_thread(
                    self._write_samples, stm, arr, pending_rate, dev_rate
                )
                # Out-of-band TTS output amplitude for the whisper-bar speaking
                # equalizer. Deliberately NOT the EventBus (~8 Hz would spam the
                # flight-recorder wildcard subscriber); zero-cost when no sink is
                # registered (mascot/none style → has_subscribers() is False).
                if level_tap.has_subscribers() and arr.size:
                    rms = float(
                        np.sqrt(np.mean(np.square(arr.astype(np.float32) * (1.0 / 32768.0))))
                    )
                    # feed() normalizes (adaptive gain) so Jarvis's voice drives
                    # the bars to full range — raw RMS (~0.1) barely moved them.
                    level_tap.feed(rms)
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

            # NOTE: no finally-close — the stream stays open across play_chunks
            # calls and is only torn down by stop() (barge-in) or by the next
            # _ensure_stream call that observes a sample-rate mismatch.
            async for chunk in chunks:
                if not chunk.pcm:
                    continue
                if pending_rate is not None and chunk.sample_rate != pending_rate:
                    await _flush_pending(final=True)
                pending_rate = chunk.sample_rate
                pending.extend(chunk.pcm)
                await _flush_pending()
            await _flush_pending(final=True)

    def stop(self) -> None:
        """Abort ongoing playback (e.g. for barge-in).

        Important: ``sd.stop()`` only affects streams started via ``sd.play()``
        — the persistent ``sd.OutputStream`` from ``play_chunks`` is invisible
        to ``sd.stop()``. We therefore also call ``stream.abort()``
        (Pa_AbortStream: discards buffered audio immediately, unlike
        ``stream.stop()`` which waits for the drain — for barge-in we want
        the fast discard).
        """
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
                pass
        sd.stop()
