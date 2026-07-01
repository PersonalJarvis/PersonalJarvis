"""Microphone capture via sounddevice (WASAPI).

Yields an `AsyncIterator[AudioChunk]` with 16 kHz mono int16 PCM —
the format Whisper expects natively. sounddevice invokes the callback in
the PortAudio thread; the bridge to asyncio runs through a queue.

Why 16 kHz? Whisper resamples internally to 16 kHz — any other input rate
would be resampled first. Capturing directly at 16 kHz saves that step.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import numpy as np
from loguru import logger as _log

if TYPE_CHECKING:
    # Type-checkers see the real module so `sd.InputStream` annotations resolve;
    # at runtime the guarded import below binds sd (or None when absent).
    import sounddevice as sd
else:
    try:
        import sounddevice as sd
    except Exception:  # noqa: BLE001 — sounddevice/PortAudio (libportaudio2) absent (headless/slim)
        sd = None  # type: ignore[assignment]

from jarvis.core.protocols import AudioChunk

SAMPLE_RATE = 16_000       # Whisper native rate
CHANNELS = 1               # Mono is sufficient for speech
BLOCKSIZE = 1600           # 100 ms blocks — compromise between latency and CPU overhead
DTYPE = "int16"

_BLOCKED_INPUT_SUBSTRINGS = (
    "Stereo Mix",
    "What U Hear",
    "Loopback",
    "Monitor",
    "Output",
    "Speaker",
    "Speakers",
    "Lautsprecher",  # i18n-allow: matched against a localized (German) Windows device name
    "Headphones",
    "Kopfhoerer",  # i18n-allow: matched against a localized (German) Windows device name
    "Kopfhörer",  # i18n-allow: matched against a localized (German) Windows device name
    "HDMI",
    "Display",
    "NVIDIA High Definition",
    "AMD HD Audio",
    "Microsoft Soundmapper",
    "Primaerer Soundtreiber",  # i18n-allow: matched against a localized (German) Windows device name
    "Primärer Soundtreiber",  # i18n-allow: matched against a localized (German) Windows device name
)

_INPUT_PRIORITY = (
    "Logitech PRO X", "PRO X", "Logitech",
    "Jabra", "Sennheiser", "SteelSeries", "Corsair", "HyperX", "Razer",
    "USB Audio", "Headset", "Microphone", "Mikrofon",  # i18n-allow: matched against a localized (German) Windows device name
    "Realtek HD Audio", "Realtek",
)

# Virtual / AI microphones (NVIDIA Broadcast, voice changers, virtual cables)
# enumerate like a normal mic but only carry audio while their companion app is
# running; when that app is closed they deliver DIGITAL SILENCE (rms 0 /
# -96 dBFS), which silently kills always-on wake detection ("nothing happens",
# no error). Deprioritize them so a real hardware mic is always preferred — they
# stay a last-resort fallback (better silence-capable than no device). Forensic
# 2026-06-27: on a localized Windows both the real and the virtual mic showed up
# as "Mikrofon (PRO X)" / "Mikrofon (NVIDIA Broadcast)", matched "Mikrofon"
# equally, so the lower index (NVIDIA Broadcast) won and fed pure silence to the
# wake loop. Cross-platform: the same trap exists with VB-Audio/VoiceMeeter
# (Win), BlackHole/Loopback (macOS), and pulse/pipewire virtual sources (Linux).
_INPUT_DEPRIORITIZE = (
    "NVIDIA Broadcast", "Voice Changer", "VoiceMod", "Virtual",
    "VB-Audio", "VoiceMeeter", "CABLE Output", "Steam Streaming",
    "BlackHole", "Loopback Audio", "Monitor of",
)

# Host API preference order for 16 kHz mic capture (Whisper native).
#
# WASAPI/WDM-KS force the stream to the device's native sample rate
# (typically 48000 Hz on gaming headsets such as the Logitech PRO X). A
# sd.InputStream(samplerate=16000) then raises PaErrorCode -9997
# (Invalid sample rate). MME and DirectSound resample transparently
# to 16 kHz and are therefore the more robust choice for always-on wake.
#
# Forensics 2026-04-26: Logitech PRO X on WASAPI silently killed the wake
# loop (exception swallowed in the asyncio task), which is why
# "Hey Jarvis" had no effect. Prioritising MME fixes this.
_HOSTAPI_PREFERENCE = {
    "MME": 0,
    "Windows DirectSound": 1,
    "Windows WASAPI": 2,
    # WDM-KS deliberately missing — see _HOSTAPI_BLOCKLIST.
}

# WDM-KS rejects the blocking PortAudio API entirely on Windows 11
# (`PaErrorCode -9996 / Invalid device` on InputStream open). It is NOT
# enough to deprioritize it — when the preferred headset is offline the
# resolver still picks a WDM-KS Realtek mic and the wake loop crashes
# silently on every iteration ("Hey Jarvis" stops working). This is the
# mic-side twin of BUG-014 (TTS WDM-KS); the lesson there was
# "structural incompatibility belongs in a denylist, not a penalty".
_HOSTAPI_BLOCKLIST: frozenset[str] = frozenset({"Windows WDM-KS"})


def _fallback_input_devices(primary_idx: int) -> list[int]:
    """Return additional mic indices with the same device name but a different host API.

    If the primary index is, for example, WASAPI@48kHz and opening at 16 kHz
    fails, we look for the same physical mic name under MME or DirectSound,
    which resample transparently to 16 kHz.
    """
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception:
        return []
    if not (0 <= primary_idx < len(devices)):
        return []
    primary = devices[primary_idx]
    primary_name_root = str(primary.get("name", "")).split("(")[0].strip()
    if not primary_name_root:
        return []
    # Other indices with the same device name, sorted by host API preference.
    matches: list[tuple[int, dict]] = []
    for idx, dev in enumerate(devices):
        if idx == primary_idx:
            continue
        if dev.get("max_input_channels", 0) <= 0:
            continue
        name = str(dev.get("name", ""))
        if primary_name_root.lower() not in name.lower():
            continue
        # Same WDM-KS exclusion as _resolve_input_device — fallbacks that
        # the resolver would never have picked in the first place should
        # not appear here either.
        hostapi_idx = dev.get("hostapi", -1)
        if 0 <= hostapi_idx < len(hostapis):
            hostapi_name = hostapis[hostapi_idx].get("name", "")
            if hostapi_name in _HOSTAPI_BLOCKLIST:
                continue
        matches.append((idx, dev))

    def _hostapi_rank(entry: tuple[int, dict]) -> int:
        hostapi_idx = entry[1].get("hostapi", -1)
        if 0 <= hostapi_idx < len(hostapis):
            hostapi_name = hostapis[hostapi_idx].get("name", "")
            return _HOSTAPI_PREFERENCE.get(hostapi_name, 99)
        return 99

    matches.sort(key=_hostapi_rank)
    return [idx for idx, _ in matches]


def _resolve_input_device(device: int | str | None) -> int | str | None:
    """Resolve ``auto-headset`` to a concrete microphone device.

    Windows exposes loopback and monitor sources as input devices. If Jarvis
    opens one of those for always-on wake detection, users hear constant hiss or
    TTS echo through the capture path. Prefer named headset microphones and
    skip known playback/loopback inputs.
    """
    if device is None or isinstance(device, int):
        if device is not None:
            _log.info("Mic-Resolve: explizites Device {} verwendet.", device)
        else:
            _log.info("Mic-Resolve: system default input (device=None).")
        return device
    if not isinstance(device, str) or device != "auto-headset":
        _log.info("Mic-Resolve: named device '{}'.", device)
        return device

    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception as exc:
        _log.warning("Mic-Resolve: sd.query_devices() failed ({}). Falling back to system default.", exc)
        return None

    candidates: list[tuple[int, dict]] = []
    for idx, dev in enumerate(devices):
        if dev.get("max_input_channels", 0) <= 0:
            continue
        name = str(dev.get("name", ""))
        if any(blocked.lower() in name.lower() for blocked in _BLOCKED_INPUT_SUBSTRINGS):
            continue
        # Drop hostapis that can't serve PortAudio blocking-stream I/O —
        # WDM-KS makes InputStream.start() raise PaErrorCode -9996 even
        # when the device enumerates cleanly. Filtering here means the
        # resolver returns None (→ system default) instead of handing
        # back a broken index that the wake loop would loop-crash on.
        hostapi_idx = dev.get("hostapi", -1)
        if 0 <= hostapi_idx < len(hostapis):
            hostapi_name = hostapis[hostapi_idx].get("name", "")
            if hostapi_name in _HOSTAPI_BLOCKLIST:
                continue
        candidates.append((idx, dev))

    def _hostapi_rank(entry: tuple[int, dict]) -> int:
        hostapi_idx = entry[1].get("hostapi", -1)
        if 0 <= hostapi_idx < len(hostapis):
            hostapi_name = hostapis[hostapi_idx].get("name", "")
            return _HOSTAPI_PREFERENCE.get(hostapi_name, 99)
        return 99

    def _name_rank(entry: tuple[int, dict]) -> int:
        name = str(entry[1].get("name", ""))
        rank = len(_INPUT_PRIORITY)
        for r, sub in enumerate(_INPUT_PRIORITY):
            if sub.lower() in name.lower():
                rank = r
                break
        # Push virtual / AI mics (NVIDIA Broadcast, voice changers, virtual
        # cables) BEHIND every real hardware mic — they often deliver silence
        # when their companion app is closed (see _INPUT_DEPRIORITIZE). They are
        # not blocked, only ranked last, so they still serve as a fallback when
        # no real mic exists.
        if any(v.lower() in name.lower() for v in _INPUT_DEPRIORITIZE):
            rank += 1000
        return rank

    candidates.sort(key=lambda entry: (_name_rank(entry), _hostapi_rank(entry)))
    if candidates:
        chosen_idx, chosen_dev = candidates[0]
        chosen_hostapi_idx = chosen_dev.get("hostapi", -1)
        chosen_hostapi = (
            hostapis[chosen_hostapi_idx].get("name", "?")
            if 0 <= chosen_hostapi_idx < len(hostapis)
            else "?"
        )
        _log.info(
            "Mic-Resolve 'auto-headset': '{}' (idx={}, hostapi={}) — {} candidate(s).",
            chosen_dev.get("name", "?"),
            chosen_idx,
            chosen_hostapi,
            len(candidates),
        )
        return chosen_idx
    _log.warning(
        "Mic-Resolve 'auto-headset': no candidates found — falling back to system default."
    )
    return None


class MicrophoneCapture:
    """Async wrapper around sounddevice.InputStream.

    Usage:
        mic = MicrophoneCapture()
        async with mic:
            async for chunk in mic.stream():
                ...
    """

    # Stall watchdog: without a restart we would be blind to silent stream death.
    # On Windows this happens regularly (audio endpoint switch during TTS,
    # USB glitch without a disconnect event, power saving in the audio driver).
    # PortAudio delivers NO exception and stream.active stays True — the only
    # reliable detection is "no callback for X seconds".
    _STALL_THRESHOLD_S: float = 3.0
    _WATCHDOG_TICK_S: float = 1.0

    def __init__(
        self,
        device: int | str | None = None,
        sample_rate: int = SAMPLE_RATE,
        blocksize: int = BLOCKSIZE,
        channels: int = CHANNELS,
    ) -> None:
        self._device = _resolve_input_device(device)
        self._sample_rate = sample_rate
        self._blocksize = blocksize
        self._channels = channels
        # Queue bridges PortAudio thread → asyncio. maxsize limits back-pressure
        # to ~2 seconds of audio (20 blocks of 100 ms each) before frames are dropped.
        self._queue: asyncio.Queue[AudioChunk] = asyncio.Queue(maxsize=20)
        self._stream: sd.InputStream | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._drops = 0
        self._closed: bool = False
        self._last_chunk_monotonic: float = 0.0
        self._watchdog_task: asyncio.Task | None = None
        self._restart_count: int = 0

    def _callback(self, indata, frames, time_info, status) -> None:
        """PortAudio callback — runs in the audio thread, NOT in the asyncio loop.

        We copy the bytes (indata is a view into an internal PortAudio buffer
        that will be overwritten by the next callback) and dispatch them
        thread-safely into the asyncio queue.
        """
        if status:
            # Overflow/underflow — will be logged later via the event bus
            pass
        pcm_bytes = bytes(indata)  # copy
        chunk = AudioChunk(
            pcm=pcm_bytes,
            sample_rate=self._sample_rate,
            timestamp_ns=time.time_ns(),
            channels=self._channels,
        )
        # put_nowait is thread-safe on asyncio.Queue when wired up before the loop
        # starts — the safer alternative is call_soon_threadsafe. However,
        # call_soon_threadsafe schedules the call only at the next loop tick —
        # if the queue is full by then, an "Exception in callback" asyncio ERROR
        # is raised. We therefore wrap put_nowait in a helper that catches QueueFull.
        if self._loop and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._safe_put, chunk)
            except RuntimeError:
                self._drops += 1

    def _safe_put(self, chunk: AudioChunk) -> None:
        """Runs in the event loop — safe put with drop-on-full."""
        # Heartbeat update for the stall watchdog. Even if the queue is full,
        # the stream is considered alive — we update the timestamp before the
        # put; otherwise drops would corrupt the stall signal.
        self._last_chunk_monotonic = time.monotonic()
        try:
            self._queue.put_nowait(chunk)
        except asyncio.QueueFull:
            self._drops += 1

    async def _try_open_stream(self) -> None:
        """Open loop with host API fallback. Raises on total failure.

        Extracted from __aenter__ so the stall watchdog can reuse the same
        open logic for restarts.
        """
        attempts: list[int | str | None] = [self._device]
        try:
            if isinstance(self._device, int):
                attempts.extend(_fallback_input_devices(self._device))
        except Exception as exc:  # noqa: BLE001
            _log.debug("Mic fallback enumeration failed: {}", exc)
        last_error: Exception | None = None
        for attempt in attempts:
            try:
                stream = sd.InputStream(
                    device=attempt,
                    channels=self._channels,
                    samplerate=self._sample_rate,
                    blocksize=self._blocksize,
                    dtype=DTYPE,
                    callback=self._callback,
                )
                stream.start()
                self._stream = stream
                self._device = attempt
                _log.info(
                    "Mic opened (device={}, sr={}, blocksize={}, dtype={}).",
                    attempt,
                    self._sample_rate,
                    self._blocksize,
                    DTYPE,
                )
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                _log.warning(
                    "Mic-Open on device={} failed ({}); trying next fallback.",
                    attempt,
                    exc,
                )
        _log.error(
            "Mic-Open failed completely ({} attempt(s)) — last error: {}",
            len(attempts),
            last_error,
        )
        if last_error is not None:
            raise last_error
        raise RuntimeError("No microphone device available.")

    async def _stream_watchdog(self) -> None:
        """Detect silent stream death and restart the InputStream.

        On Windows, WASAPI/sounddevice often delivers NO exception on audio
        endpoint switches, USB glitches, or power-save resume — the stream
        remains formally active=True, but the PortAudio callback never fires
        again. Symptom: the wake detector queues stay permanently empty and
        "Hey Jarvis" is not recognised even though the pipeline and detector
        threads are alive.

        Logic: if no chunk has arrived in the callback for more than
        _STALL_THRESHOLD_S, stop+close+open the stream. Consumers of
        stream() only notice a brief audio gap.
        """
        # Initial grace pulse: wait until the first chunk has safely arrived
        # before starting the watchdog, otherwise it fires before the first frame.
        self._last_chunk_monotonic = time.monotonic()
        while not self._closed:
            await asyncio.sleep(self._WATCHDOG_TICK_S)
            if self._closed:
                return
            elapsed = time.monotonic() - self._last_chunk_monotonic
            if elapsed <= self._STALL_THRESHOLD_S:
                continue
            self._restart_count += 1
            _log.warning(
                "Mic stall detected ({:.1f}s without a frame) — restart #{} (device={}).",
                elapsed,
                self._restart_count,
                self._device,
            )
            old_stream = self._stream
            self._stream = None
            if old_stream is not None:
                try:
                    old_stream.stop()
                except Exception as exc:  # noqa: BLE001
                    _log.debug("Mic-Restart: stop() ignored ({}).", exc)
                try:
                    old_stream.close()
                except Exception as exc:  # noqa: BLE001
                    _log.debug("Mic-Restart: close() ignored ({}).", exc)
            try:
                await self._try_open_stream()
                _log.info("Mic-Restart #{} succeeded.", self._restart_count)
            except Exception as exc:  # noqa: BLE001
                _log.error(
                    "Mic-Restart #{} failed: {} — next attempt in 5s.",
                    self._restart_count,
                    exc,
                )
                # Reset the heartbeat; otherwise the watchdog would trigger again
                # on the next tick immediately — we want a 5s pause between reopens.
                self._last_chunk_monotonic = time.monotonic() + 5.0
                continue
            # Reset the heartbeat — grace window for the first frame after reopen.
            self._last_chunk_monotonic = time.monotonic()

    async def __aenter__(self) -> MicrophoneCapture:
        self._loop = asyncio.get_running_loop()
        await self._try_open_stream()
        # Start the watchdog only AFTER the first successful open; otherwise it
        # would race against a None stream during the initial open.
        self._watchdog_task = asyncio.create_task(
            self._stream_watchdog(), name="mic-stall-watchdog"
        )
        return self

    async def __aexit__(self, *exc_info) -> None:
        self._closed = True
        watchdog = self._watchdog_task
        self._watchdog_task = None
        if watchdog is not None and not watchdog.done():
            watchdog.cancel()
            try:
                await watchdog
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                _log.debug("Mic-Watchdog cleanup swallow: {}", exc)
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:  # noqa: BLE001
                _log.debug("Mic close swallow: {}", exc)
            finally:
                self._stream = None
                _log.info(
                    "Mic closed (drops={}, restarts={}).",
                    self._drops,
                    self._restart_count,
                )

    async def stream(self) -> AsyncIterator[AudioChunk]:
        """Yield audio chunks until __aexit__ is called.

        Important: the loop condition no longer depends on stream.active —
        on silent stream death that flag lies and continues to report True.
        The stall watchdog repairs the stream in the background; the consumer
        only sees a brief audio gap and continues reading.
        """
        while not self._closed:
            chunk = await self._queue.get()
            yield chunk

    @property
    def dropped_frames(self) -> int:
        """Number of frames lost due to a full queue."""
        return self._drops

    @property
    def restart_count(self) -> int:
        """Number of stream restarts triggered by the stall watchdog."""
        return self._restart_count


def pcm_bytes_to_np(pcm: bytes) -> np.ndarray:
    """Convert int16 PCM bytes to numpy float32 [-1.0, 1.0] — Whisper input format."""
    int16 = np.frombuffer(pcm, dtype=np.int16)
    return int16.astype(np.float32) / 32768.0
