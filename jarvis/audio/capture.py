"""Cross-platform microphone capture via sounddevice and PortAudio.

Yields an `AsyncIterator[AudioChunk]` with 16 kHz mono int16 PCM —
the format Whisper expects natively. sounddevice invokes the callback in
the PortAudio thread; the bridge to asyncio runs through a queue.

Why 16 kHz? Whisper consumes 16 kHz natively, so that remains the downstream
contract. The stream requests 16 kHz first; when CoreAudio, ALSA, or a strict
Windows endpoint only accepts its 44.1/48 kHz native rate, capture opens there
and performs a stateful CPU resample before yielding each chunk.
"""
from __future__ import annotations

import asyncio
import math
import time
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from typing import TYPE_CHECKING, Any

import numpy as np
from loguru import logger as _log

if TYPE_CHECKING:
    # Type-checkers see the real module so `sd.InputStream` annotations resolve;
    # at runtime the guarded import below binds sd (or None when absent).
    import sounddevice as sd

    from jarvis.core.protocols import PermissionGate
    from jarvis.platform.permission_service import EnsureResult
else:
    try:
        import sounddevice as sd
    except Exception:  # noqa: BLE001 — sounddevice/PortAudio (libportaudio2) absent (headless/slim)
        sd = None  # type: ignore[assignment]

from jarvis.audio import topology
from jarvis.audio.device_select import is_legacy_primary_mapper
from jarvis.core.events import PERMISSION_FEATURES, PermissionNeeded
from jarvis.core.protocols import AudioChunk

SAMPLE_RATE = 16_000       # Whisper native rate
CHANNELS = 1               # Mono is sufficient for speech
# One native Silero frame. The previous 1,600-frame / 100 ms callback made
# every capture consumer wait up to 100 ms before it could see speech, silence,
# or a barge-in edge; the VAD split that block back into 512-frame pieces anyway.
# Delivering the model's native 32 ms unit removes that fixed batching delay
# without increasing inference work or changing the PCM presented downstream.
BLOCKSIZE = 512
DTYPE = "int16"

CAPTURE_BLOCK_DURATION_S = BLOCKSIZE / SAMPLE_RATE


# Reported latency of the capture stream that is currently open. Half-duplex
# echo protection needs it: a microphone frame handed over at time T was
# recorded that much EARLIER, so without it the guard releases frames that
# still carry the assistant's own voice and the model ends up answering
# itself. Kept module-level because the mic is a process-wide singleton and
# the consumers (realtime session, wake loop) never own the stream object.
_INPUT_LATENCY_CAP_S = 1.0
_last_input_latency_s = 0.0


def last_input_latency_s() -> float:
    """Capture latency of the open microphone stream, 0.0 when unknown."""
    return _last_input_latency_s


def _remember_input_latency(stream: Any) -> None:
    global _last_input_latency_s
    raw = getattr(stream, "latency", None)
    # PortAudio reports a scalar for a single stream; be liberal anyway, since
    # a wrong value here silently weakens echo protection rather than raising.
    if isinstance(raw, (tuple, list)):
        raw = raw[0] if raw else None
    try:
        latency = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):  # Unknown host latency is represented by the zero floor.
        latency = 0.0
    if not (latency > 0.0):
        latency = 0.0
    _last_input_latency_s = min(_INPUT_LATENCY_CAP_S, latency)


def capture_chunks_for_duration(seconds: float) -> int:
    """Return the default-size chunk count covering at least ``seconds``."""
    return max(1, math.ceil(max(0.0, float(seconds)) / CAPTURE_BLOCK_DURATION_S))


# Preserve the old bulk-queue duration after reducing the callback block size.
# Queue bounds are time budgets, not magic chunk counts.
DEFAULT_QUEUE_CHUNKS = capture_chunks_for_duration(2.0)


# The permission this module asks about. The service takes the plain id string
# (``PermissionId`` is a ``StrEnum``), which keeps ``jarvis.platform`` out of this
# module's import graph: a headless install must import capture without it.
_MICROPHONE = "microphone"
_GRANTED_STATES = frozenset({"granted", "not_required"})

# Exact digital silence (all-zero samples) for this long while the OS says the
# microphone is granted means macOS is feeding zeros (a denied or muted input):
# the capture reports it once and stays open (unverified on a real Mac).
_SILENCE_REPORT_AFTER_S = 5.0
_SILENCE_DETAIL = (
    "The microphone delivers only silence: access is probably denied or muted."
)
_NOT_GRANTED_MESSAGE = "Microphone access is not granted for Personal Jarvis."


class MicrophoneAccessError(PermissionError):
    """The microphone may not be opened, or was revoked while it was open.

    ``result`` is the permission layer's :class:`EnsureResult` for the refusal
    (``user_detail`` is a finished sentence for people, ``agent_detail`` the
    prohibitive one for a tool error), so a consumer can say WHY without asking
    the OS again. It is ``None`` only for an error raised by code that never
    consulted the layer.
    """

    def __init__(self, message: str = "", *, result: EnsureResult | None = None) -> None:
        super().__init__(message)
        self.result = result


def _state_value(state: Any) -> str:
    return str(getattr(state, "value", state))


def _legacy_gate_result(allowed: bool) -> EnsureResult:
    """An ``EnsureResult`` for an injected boolean ``access_gate`` (a test seam)."""
    from jarvis.platform.permission_service import (  # noqa: PLC0415 - lazy: keeps capture headless-light
        EnsureResult,
        PermissionOutcome,
        agent_detail_for,
        user_detail_for,
    )
    from jarvis.platform.permissions import PermissionId, PermissionState  # noqa: PLC0415

    if allowed:
        return EnsureResult(
            permission=PermissionId.MICROPHONE,
            outcome=PermissionOutcome.GRANTED,
            state=PermissionState.GRANTED,
            asked=False,
            outside_installed_app=False,
            agent_detail="",
            user_detail="",
        )
    return EnsureResult(
        permission=PermissionId.MICROPHONE,
        outcome=PermissionOutcome.DENIED,
        state=PermissionState.DENIED,
        asked=False,
        outside_installed_app=False,
        agent_detail=agent_detail_for(PermissionId.MICROPHONE, "denied"),
        user_detail=user_detail_for(PermissionId.MICROPHONE, "denied"),
        reason="denied",
        can_open_settings=True,
    )


# Queue depth for a REAL-TIME detection consumer (VAD endpointing, wake, barge).
# ~0.6 s: shallow enough that on a CPU which can't process every frame in real
# time the drop-OLDEST overflow policy keeps the audio near-present (so
# end-of-speech silence and the wake word are seen promptly, not seconds late),
# yet deep enough to absorb normal scheduling jitter on a machine that keeps up
# (which never fills it). Bulk recorders that must keep every frame
# (push-to-talk, dictation) use the deeper default instead. See MicrophoneCapture.
REALTIME_QUEUE_CHUNKS = capture_chunks_for_duration(0.6)

# Input NAMES we never open as a microphone: playback/loopback/monitor sources
# (opening one feeds constant hiss or TTS echo into the wake path) and GPU-HDMI
# audio. Matched case-insensitively. A few translated playback labels (the
# localized speaker/headphone words listed below) are additive coverage for a
# localized Windows where a loopback enumerates under its translated name — data,
# prose. The MME "Sound Mapper" / DirectSound "Primary Sound Driver" virtual
# routers are NOT listed here (their name is localized); they are skipped
# STRUCTURALLY via ``is_legacy_primary_mapper``, which also correctly catches
# the DirectSound *recording* mapper that no fixed substring covered.
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
)

# Generic default preference order for "auto-headset" microphone selection, most
# specific first. Not tied to any one machine's hardware — a user whose mic is
# not covered names it via ``[audio].input_device_priority`` (consulted BEFORE
# this list) or pins an explicit ``[audio].input_device`` index, without editing
# code. Bare product tokens (PRO X, Arctis, …) exist because sounddevice often
# enumerates a headset mic without the vendor prefix. "Microphone" / "Mikrofon"
# are the generic last-resort real-mic labels across common Windows UI locales.
_INPUT_PRIORITY = (
    "Logitech PRO X", "PRO X", "Logitech",
    "Jabra", "Sennheiser", "SteelSeries", "Arctis", "Corsair", "HyperX",
    "Razer", "Bose", "AirPods",
    "USB Audio", "Headset", "Microphone",
    "Mikrofon",  # i18n-allow: localized German mic-label matching data
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
# Cross-platform note: WINDOWS host-API names by design — on macOS ("Core
# Audio") and Linux ("ALSA"/"JACK") nothing matches, so ranking falls through
# to the OS default enumeration order. Intentionally inert-by-data; add
# macOS/Linux preferences as new entries if ever needed, don't platform-gate.
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
    except Exception:  # Device enumeration is an optional compatibility probe.
        return []
    if not (0 <= primary_idx < len(devices)):
        return []
    primary = devices[primary_idx]
    primary_name = " ".join(str(primary.get("name", "")).casefold().split())
    if not primary_name:
        return []
    # Other indices with the same device name, sorted by host API preference.
    matches: list[tuple[int, dict]] = []
    for idx, dev in enumerate(devices):
        if idx == primary_idx:
            continue
        if dev.get("max_input_channels", 0) <= 0:
            continue
        # Host-API twins carry the same PortAudio device name. Comparing the
        # complete normalized name matters: common labels such as
        # ``Microphone (External)`` and ``Microphone (Built-in)`` are different
        # physical inputs and must not be cached as interchangeable twins.
        name = " ".join(str(dev.get("name", "")).casefold().split())
        if name != primary_name:
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


def _os_default_input_name(
    devices: Sequence[dict], hostapis: Sequence[dict]
) -> str | None:
    """Name of the user's OS-selected default INPUT (microphone) device, when it
    is a real, usable mic — else None.

    The "your device first" contract for capture: ``auto-headset`` prefers the
    user's system default microphone, EXCEPT when that default is a device the
    resolver exists to avoid — a loopback/monitor source, the localized virtual
    mapper, or a virtual/AI mic (NVIDIA Broadcast, VB-Audio, …) that goes silent
    when its companion app is closed. In those cases this returns None so the
    resolver falls back to the generic heuristic and picks a real hardware mic
    instead of feeding digital silence to the wake loop. The NAME is returned so
    the candidate sort still picks the mic's best host-API twin (MME/DirectSound
    resample to 16 kHz) and skips WDM-KS. A missing default / absent sounddevice
    yields None.
    """
    try:
        default_in = sd.default.device[0]
    except Exception:  # noqa: BLE001 — no default / no sounddevice -> no preference
        return None
    if not isinstance(default_in, int) or not (0 <= default_in < len(devices)):
        return None
    dev = devices[default_in]
    name = str(dev.get("name", ""))
    if not name or dev.get("max_input_channels", 0) <= 0:
        return None
    low = name.lower()
    if any(b.lower() in low for b in _BLOCKED_INPUT_SUBSTRINGS):
        return None
    if is_legacy_primary_mapper(default_in, hostapis, devices, output=False):
        return None
    if any(v.lower() in low for v in _INPUT_DEPRIORITIZE):
        return None  # virtual/AI mic as OS default -> fall back to a real mic
    return name


def _rank_input_device_candidates(
    devices: Sequence[dict],
    hostapis: Sequence[dict],
    priority: Sequence[str] | None = None,
) -> list[tuple[int, dict]]:
    """Return every usable input device in automatic-selection order.

    Keeping the complete ranking separate from ``_resolve_input_device`` lets
    stream-open recovery move to another physical microphone without inventing
    a second, subtly different set of platform filters. The first entry remains
    the normal resolver choice; later entries are recovery candidates only.
    """
    user_priority = tuple(p for p in (priority or ()) if p)
    os_default_name = _os_default_input_name(devices, hostapis)
    effective_priority = (
        (*user_priority, os_default_name) if os_default_name else user_priority
    )

    candidates: list[tuple[int, dict]] = []
    for idx, dev in enumerate(devices):
        if dev.get("max_input_channels", 0) <= 0:
            continue
        name = str(dev.get("name", ""))
        if any(blocked.lower() in name.lower() for blocked in _BLOCKED_INPUT_SUBSTRINGS):
            continue
        if is_legacy_primary_mapper(idx, hostapis, devices, output=False):
            continue
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
        low = str(entry[1].get("name", "")).lower()
        for rank, substring in enumerate(effective_priority):
            if substring.lower() in low:
                return rank
        rank = len(effective_priority) + len(_INPUT_PRIORITY)
        for generic_rank, substring in enumerate(_INPUT_PRIORITY):
            if substring.lower() in low:
                rank = len(effective_priority) + generic_rank
                break
        if any(virtual.lower() in low for virtual in _INPUT_DEPRIORITIZE):
            rank += 1000
        return rank

    candidates.sort(key=lambda entry: (_name_rank(entry), _hostapi_rank(entry)))
    return candidates


def _ranked_input_device_indices(
    priority: Sequence[str] | None = None,
) -> list[int]:
    """Enumerate usable microphones in resolver order for open recovery."""
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception as exc:  # noqa: BLE001 - recovery remains best-effort
        _log.debug("Mic recovery enumeration failed: {}", exc)
        return []
    return [
        idx
        for idx, _device in _rank_input_device_candidates(
            devices, hostapis, priority
        )
    ]


# Resolve-result cache: (device_spec, priority) -> (resolved, monotonic_ts).
# WHY: resolving "auto-headset" enumerates every audio device (~0.4s on
# Windows) and used to run on EVERY MicrophoneCapture construction — the
# dominant share of the wake→session mic handover gap, during which the
# user's first words after "Hey Jarvis" were simply not captured (live
# forensic 2026-07-11: bar visible instantly, speech only heard ~0.5-0.7s
# later). A capture whose stream is delivering frames TOUCHES its cache
# entry every watchdog tick, so the session mic that opens <1s after the
# wake mic closed reuses the proven device instantly. Freshness window is
# deliberately short (a few seconds past the last live frame) and any
# open FAILURE invalidates, so hot-plug/device-switch behaviour falls back
# to a full fresh resolve exactly as before.
_RESOLVE_CACHE: dict[tuple[Any, tuple[str, ...]], tuple[Any, float]] = {}
_RESOLVE_CACHE_FRESH_S = 5.0


def _antialias_taps(from_rate: int, to_rate: int, num_taps: int = 33) -> np.ndarray:
    """Hamming-windowed sinc low-pass just below the TARGET Nyquist.

    Cutoff sits at 0.45 x the target rate (7.2 kHz for a 16 kHz target), which
    keeps the whole speech band and the wake models' feature range while
    removing what would otherwise fold into it.
    """
    cutoff_hz = 0.45 * to_rate
    fc = cutoff_hz / from_rate  # cycles per SOURCE sample
    n = np.arange(num_taps, dtype=np.float64) - (num_taps - 1) / 2.0
    taps = 2.0 * fc * np.sinc(2.0 * fc * n) * np.hamming(num_taps)
    total = taps.sum()
    return taps / total if total else taps


class _StreamingPcm16Resampler:
    """Stateful CPU-only resampler for interleaved PCM16 microphone frames.

    PortAudio callbacks split a continuous signal into independent buffers. A
    stateless per-buffer conversion would introduce a discontinuity at every
    boundary, so the final source frame and fractional position are retained
    for the next callback. NumPy is part of the universal base installation;
    no GPU or optional native inference dependency is involved.

    Downsampling additionally low-pass filters FIRST. Without that, interpolating
    48 kHz straight down to 16 kHz folds every component above 8 kHz back into
    the speech band at full amplitude — sibilance, keyboard clatter, fan and
    coil whine, switching-supply noise. This path is reached only where the
    native-rate fallback engages (CoreAudio, ALSA/PipeWire), i.e. macOS and
    Linux; Windows resamples host-side in MME and never gets here. So the entire
    wake stack — the openWakeWord melspec, the Vosk MFCCs, the Whisper log-mel,
    and the AP-27 energy constants — was calibrated on clean Windows captures
    and then deployed on aliased audio. Filtering strictly removes noise that
    was never in any model's training data, so it cannot trade one corner of the
    latency/recall/precision triangle for another; it only puts these two OSes
    on the signal quality Windows already has.
    """

    def __init__(self, from_rate: int, to_rate: int, channels: int) -> None:
        if from_rate <= 0 or to_rate <= 0:
            raise ValueError("PCM sample rates must be positive")
        if channels <= 0:
            raise ValueError("PCM channel count must be positive")
        self.from_rate = int(from_rate)
        self.to_rate = int(to_rate)
        self.channels = int(channels)
        self._step = self.from_rate / self.to_rate
        self._tail: np.ndarray | None = None
        self._position = 0.0
        # Only DOWNsampling can alias; upsampling needs no pre-filter.
        self._taps: np.ndarray | None = (
            _antialias_taps(self.from_rate, self.to_rate)
            if self.from_rate > self.to_rate
            else None
        )
        self._filter_tail: np.ndarray | None = None

    def _antialias(self, frames: np.ndarray) -> np.ndarray:
        """Apply the low-pass, carrying ``taps-1`` frames across callbacks."""
        taps = self._taps
        assert taps is not None
        history = taps.size - 1
        if self._filter_tail is None:
            # Prime with the first frame repeated instead of zeros: a
            # zero-primed filter fades the first ~1 ms of the stream in, which
            # would be an audible click and a dip in the very first wake window.
            self._filter_tail = np.repeat(frames[:1], history, axis=0)
        padded = np.concatenate((self._filter_tail, frames), axis=0)
        self._filter_tail = padded[-history:].copy() if history else None
        out = np.empty_like(frames)
        for channel in range(self.channels):
            out[:, channel] = np.convolve(padded[:, channel], taps, mode="valid")
        return out

    def process(self, pcm16: bytes) -> bytes:
        if not pcm16:
            return b""
        frame_width = 2 * self.channels
        if len(pcm16) % frame_width:
            raise ValueError("PCM16 input must contain complete audio frames")
        if self.from_rate == self.to_rate:
            return bytes(pcm16)

        samples = np.frombuffer(pcm16, dtype="<i2").reshape(-1, self.channels)
        frames = samples.astype(np.float64)
        if self._taps is not None:
            frames = self._antialias(frames)
        if self._tail is not None:
            frames = np.concatenate((self._tail, frames), axis=0)
        if frames.shape[0] < 2:
            self._tail = frames[-1:].copy()
            return b""

        limit = float(frames.shape[0] - 1)
        positions = np.arange(self._position, limit, self._step, dtype=np.float64)
        if positions.size:
            left = np.floor(positions).astype(np.int64)
            fraction = (positions - left)[:, np.newaxis]
            values = frames[left] + (frames[left + 1] - frames[left]) * fraction
            output = (
                np.clip(np.rint(values), -32768, 32767)
                .astype("<i2")
                .reshape(-1)
                .tobytes()
            )
            self._position = float(positions[-1] + self._step - limit)
        else:
            output = b""
            self._position -= limit

        self._tail = frames[-1:].copy()
        return output


def _default_input_sample_rate(device: int | str | None) -> int:
    """Return PortAudio's default input rate for ``device``, or zero.

    Passing ``kind='input'`` is important for ``device=None``: sounddevice then
    returns the default input device record rather than the complete device
    list. The one-argument fallback keeps compatibility with simple test
    doubles and older sounddevice-compatible implementations.
    """
    try:
        try:
            info = sd.query_devices(device, "input")
        except TypeError:  # Older sounddevice releases lack the kind argument.
            info = sd.query_devices(device)
        return int(info.get("default_samplerate", 0) or 0)
    except Exception:  # noqa: BLE001 - device query is advisory
        return 0


def _native_input_rates(
    device: int | str | None, target_rate: int
) -> tuple[int, ...]:
    """Candidate hardware rates after every device rejected ``target_rate``."""
    rates: list[int] = []
    for rate in (_default_input_sample_rate(device), 48_000, 44_100):
        if rate > 0 and rate != target_rate and rate not in rates:
            rates.append(rate)
    return tuple(rates)


def _resolve_cache_key(
    device: int | str | None, priority: tuple[str, ...]
) -> tuple[Any, tuple[str, ...]]:
    return (device, priority)


def _touch_resolve_cache(
    device_spec: int | str | None, priority: tuple[str, ...], resolved: Any
) -> None:
    """Mark ``resolved`` as live-and-working for ``device_spec`` right now."""
    _RESOLVE_CACHE[_resolve_cache_key(device_spec, priority)] = (
        resolved,
        time.monotonic(),
    )


def _invalidate_resolve_cache() -> None:
    """Drop every cached resolve — called on open failures/stream stalls."""
    _RESOLVE_CACHE.clear()


def _request_topology_probe() -> None:
    """Nudge the topology watcher to re-probe now (best-effort, never raises).

    Imported lazily: ``topology`` imports this module, so a top-level import
    would be circular.
    """
    try:
        from jarvis.audio.topology import request_topology_probe

        request_topology_probe()
    except Exception:  # noqa: BLE001 — a diagnostics nudge must never propagate
        _log.debug("topology probe request skipped", exc_info=True)


def _cached_resolve(
    device_spec: int | str | None, priority: tuple[str, ...]
) -> Any | None:
    entry = _RESOLVE_CACHE.get(_resolve_cache_key(device_spec, priority))
    if entry is None:
        return None
    resolved, ts = entry
    if time.monotonic() - ts > _RESOLVE_CACHE_FRESH_S:
        return None
    return resolved


def _resolve_input_device(
    device: int | str | None,
    priority: Sequence[str] | None = None,
) -> int | str | None:
    """Resolve ``auto-headset`` to a concrete microphone device.

    Windows exposes loopback and monitor sources as input devices. If Jarvis
    opens one of those for always-on wake detection, users hear constant hiss or
    TTS echo through the capture path. Prefer named headset microphones and
    skip known playback/loopback inputs and the localized MME/DirectSound
    virtual mapper.

    ``priority`` is the user's own mic-name preference
    (``[audio].input_device_priority``). When non-empty, a device whose name
    contains a user entry outranks EVERY generic ``_INPUT_PRIORITY`` match, so a
    user with an uncommon microphone wins by naming it — no code edit. Empty
    ``priority`` reproduces the generic-only behavior exactly.
    """
    if device is None or isinstance(device, int):
        if device is not None:
            _log.info("Mic-Resolve: explicit device {} used.", device)
        else:
            _log.info("Mic-Resolve: system default input (device=None).")
        return device
    if not isinstance(device, str):
        return device
    if device != "auto-headset":
        # A concrete NAME (the Settings device picker persists names — the
        # only identifier stable across reboots/hot-plugs): resolve to an
        # index via the shared lookup (best host-API twin — MME first for
        # 16 kHz capture — WDM-KS/mapper excluded). An unplugged/unknown name
        # falls through to the auto-headset heuristic so the wake loop never
        # bricks on a missing device.
        from jarvis.audio.devices import resolve_device_by_name

        named_idx = resolve_device_by_name(device, output=False)
        if named_idx is not None:
            _log.info("Mic-Resolve: named device '{}' -> index {}.", device, named_idx)
            return named_idx
        _log.warning(
            "Mic-Resolve: configured input device '{}' not found — falling "
            "back to auto-headset selection.",
            device,
        )
        # Fall through to the auto-headset heuristic below.

    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception as exc:
        _log.warning(
            "Mic-Resolve: sd.query_devices() failed ({}). Falling back to "
            "system default.",
            exc,
        )
        return None

    # The complete ranking is shared with open-time recovery. Its first entry
    # preserves the resolver contract: explicit user priority, then the usable
    # OS default, then generic real-microphone heuristics and host-API ranking.
    candidates = _rank_input_device_candidates(devices, hostapis, priority)
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


class _ResizableQueue(asyncio.Queue[AudioChunk]):
    """The capture bridge queue, whose bound may change while it is in use.

    One microphone stream serves consumers with opposite needs. A wake detector
    wants a SHALLOW bound so a CPU that falls behind sees near-present audio; a
    dictation wants a DEEP one, because every frame is a word the user said and
    the drop-oldest policy deletes speech rather than staleness. The stream is
    handed from the first to the second mid-flight (the wake handoff), so the
    bound has to move with it.

    ``asyncio.Queue`` exposes ``maxsize`` read-only and offers no resize. The
    obvious alternative — build a new queue and copy the items over — silently
    strands any consumer already parked in ``get()`` on the old object, which on
    this path is the drain that owns the recording. So the bound is moved in
    place instead: ``_maxsize`` is read afresh by ``full()`` on every put, so a
    raise takes effect on the very next frame and nobody is left waiting.
    """

    #: Declared for the type checker: the base class keeps it as a plain
    #: attribute behind the read-only ``maxsize`` property.
    _maxsize: int

    def set_maxsize(self, value: int) -> None:
        """Change the bound. Never below one — a zero bound means UNbounded."""
        self._maxsize = max(1, int(value))


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
        max_queue_chunks: int = DEFAULT_QUEUE_CHUNKS,
        device_priority: Sequence[str] | None = None,
        access_gate: Callable[[], bool] | None = None,
        permission_feature: str = "voice",
        interactive: bool = False,
        permission_wait_s: float = 0.0,
        permission_gate: PermissionGate | None = None,
    ) -> None:
        if permission_feature not in PERMISSION_FEATURES:
            raise ValueError(f"Unknown permission feature: {permission_feature!r}")
        # Permission contract (design-v2 3.5): ONE service ``ensure`` when the
        # stream opens, then one cheap ``check`` per watchdog tick. ``interactive``
        # says whether a user gesture started this capture (it may make macOS ask);
        # the default is the safe one, a background start that never asks.
        # ``permission_wait_s`` > 0 waits for the OS dialog and is for a caller
        # that is not an edge gesture (a held key passes 0).
        self._permission_feature = permission_feature
        self._interactive = bool(interactive)
        self._permission_wait_s = max(0.0, float(permission_wait_s))
        # ``access_gate`` is the legacy boolean test seam: when given it REPLACES
        # the service (no ask, no episode). ``permission_gate`` injects a
        # ``PermissionGate`` (the real service by default, resolved per call).
        self._access_gate = access_gate
        self._permission_gate = permission_gate
        # User-configured mic-name priority ([audio].input_device_priority),
        # consulted BEFORE the generic _INPUT_PRIORITY default when resolving
        # "auto-headset". Empty = today's generic behavior.
        self._device_priority: tuple[str, ...] = tuple(device_priority or ())
        # Original spec (e.g. "auto-headset") kept for the resolve cache; the
        # cache only serves STRING specs — ints/None resolve instantly anyway.
        self._device_spec: int | str | None = device
        cached = (
            _cached_resolve(device, self._device_priority)
            if isinstance(device, str)
            else None
        )
        if cached is not None:
            _log.info(
                "Mic-Resolve: cache hit for '{}' -> {} (device live moments ago).",
                device,
                cached,
            )
            self._device = cached
        else:
            self._device = _resolve_input_device(device, self._device_priority)
        # Keep the originally resolved microphone separate from the currently
        # open one. If recovery temporarily moves to another physical input,
        # the preferred device is tried again on the next capture/restart.
        self._preferred_device: int | str | None = self._device
        self._using_physical_fallback = False
        self._sample_rate = sample_rate
        self._blocksize = blocksize
        self._channels = channels
        # ``_sample_rate`` is the stable downstream contract (normally 16 kHz).
        # Some CoreAudio/ALSA devices only open at 44.1/48 kHz; in that case the
        # hardware rate changes while the callback resamples back to the public
        # contract before constructing an AudioChunk.
        self._capture_sample_rate = sample_rate
        self._capture_resampler: _StreamingPcm16Resampler | None = None
        # Queue bridges PortAudio thread → asyncio. maxsize bounds how STALE the
        # audio a consumer sees may get: with the drop-OLDEST policy in
        # ``_safe_put`` a full queue always holds the most-recent
        # ``max_queue_chunks`` blocks, so worst-case staleness is the queue
        # depth times the configured block duration. The default stays at ~2 s
        # after the low-latency 32 ms block change and is generous back-
        # pressure for a bulk consumer (push-to-talk, which records every frame).
        # A REAL-TIME detection consumer (VAD endpointing, wake) passes a SHALLOW
        # depth (~0.6 s) so that on a CPU that can't keep up the end-of-speech
        # silence and the wake word are seen near-present, not 2 s late — the
        # "stuck listening / missed wake on a weaker laptop" bug. A machine that
        # keeps up never fills the queue, so the depth is invisible there.
        #
        # The depth is not frozen at construction: a stream opened for wake is
        # handed to a dictation without being reopened, and the two want
        # opposite bounds (see ``set_queue_depth``).
        self._queue: _ResizableQueue = _ResizableQueue(
            maxsize=max(1, int(max_queue_chunks))
        )
        self._stream: sd.InputStream | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._drops = 0
        self._overflows = 0
        self._closed: bool = False
        self._last_chunk_monotonic: float = 0.0
        self._watchdog_task: asyncio.Task | None = None
        self._restart_count: int = 0
        # Permission bookkeeping. ``_watch_access``: the watchdog re-checks the
        # grant each tick (False off macOS, where the service answers
        # NOT_REQUIRED and the capture never touches it again). ``_revoked`` /
        # ``_access_error``: the grant was lost while open; ``stream()`` raises.
        self._watch_access = False
        self._unreadable_ticks = 0
        self._revoked = False
        self._access_error: MicrophoneAccessError | None = None
        # Digital-silence guard (macOS only, armed when the OS said GRANTED).
        self._silence_guard_armed = False
        self._zero_since: float | None = None
        self._silence_reported = False

    def _service_gate(self) -> PermissionGate:
        """The injected gate, else the process service (resolved per call)."""
        gate = self._permission_gate
        if gate is not None:
            return gate
        from jarvis.platform.permission_service import (  # noqa: PLC0415 - lazy: keeps capture headless-light
            get_permission_service,
        )

        return get_permission_service()

    def _legacy_gate_allows(self) -> bool:
        gate = self._access_gate
        if gate is None:
            return True
        try:
            return bool(gate())
        except Exception as exc:  # noqa: BLE001 - a gate that raises is not a grant
            _log.debug("Mic access gate raised ({}); treated as not granted.", exc)
            return False

    @staticmethod
    def _access_error_for(result: EnsureResult) -> MicrophoneAccessError:
        return MicrophoneAccessError(result.user_detail or _NOT_GRANTED_MESSAGE, result=result)

    async def _ensure_access(self, *, interactive: bool) -> None:
        """The ONE permission ensure of an open; raises unless the mic is granted.

        Runs before CoreAudio is touched, so a refused open never creates a
        device. The service makes the native request only for an interactive
        caller, publishes the episode itself and never raises.
        """
        if self._access_gate is not None:
            result = _legacy_gate_result(self._legacy_gate_allows())
        else:
            result = await self._service_gate().ensure_async(
                _MICROPHONE,
                feature=self._permission_feature,
                interactive=interactive,
                wait_s=self._permission_wait_s if interactive else 0.0,
            )
        if not result.granted:
            raise self._access_error_for(result)
        # Off macOS the answer is NOT_REQUIRED: nothing to watch, no guard. The
        # guard is for a real service grant only (an injected gate is a test seam).
        os_granted = _state_value(result.outcome) == "granted"
        self._watch_access = self._access_gate is not None or os_granted
        self._silence_guard_armed = self._access_gate is None and os_granted
        self._unreadable_ticks = 0

    def _callback(self, indata, frames, time_info, status) -> None:
        """PortAudio callback — runs in the audio thread, NOT in the asyncio loop.

        We copy the bytes (indata is a view into an internal PortAudio buffer
        that will be overwritten by the next callback) and dispatch them
        thread-safely into the asyncio queue.
        """
        if status and getattr(status, "input_overflow", False):
            # PortAudio's own word that its input buffer overran: audio was
            # lost BEFORE it reached this callback. It is the one hardware-level
            # loss signal there is, and it used to be discarded right here — so
            # a dictation with a hole in it was reported whole, and the only
            # loss anyone could see was a timestamp gap that cannot tell a real
            # hole from callback jitter. A plain increment on the audio thread
            # (atomic under the GIL); readers take the delta over their own
            # lifetime through ``overflow_count``.
            self._overflows += 1
        pcm_bytes = bytes(indata)  # copy
        resampler = self._capture_resampler
        if resampler is not None:
            try:
                pcm_bytes = resampler.process(pcm_bytes)
            except ValueError as exc:
                # A malformed callback buffer must not escape into PortAudio's
                # real-time thread and terminate capture. Count it as a dropped
                # frame; the next complete callback can continue normally.
                self._drops += 1
                _log.warning("Mic callback dropped malformed PCM: {}", exc)
                return
            if not pcm_bytes:
                return
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
            except RuntimeError:  # The event loop closed between the guard and handoff.
                self._drops += 1

    def _wake_parked_reader(self) -> None:
        """Wake a consumer blocked in ``stream()`` after the capture closed.

        ``__aexit__`` stops the native stream, so no further frame will ever
        arrive — and a consumer parked on ``queue.get()`` would wait for one
        forever (BUG-185: the session's abandoned microphone pump). An empty
        marker frame wakes it; ``stream()`` sees ``_closed`` and returns. A
        full queue needs no marker: the reader wakes on the next queued frame.
        """
        try:
            self._queue.put_nowait(
                AudioChunk(pcm=b"", sample_rate=self._sample_rate, timestamp_ns=0)
            )
        except asyncio.QueueFull:
            return

    def _track_silence(self, pcm: bytes, now: float) -> None:
        """Remember since when the stream has carried only exact zeros.

        Runs per frame on the event loop, so it must stay cheap: ``frombuffer``
        is a view of the bytes already in hand (no copy) and ``any()`` stops at
        the first non-zero sample. The watchdog tick reads the verdict.
        """
        if len(pcm) < 2 or len(pcm) % 2:
            return
        if np.frombuffer(pcm, dtype=np.int16).any():
            self._zero_since = None
            self._silence_reported = False
        elif self._zero_since is None:
            self._zero_since = now

    def _safe_put(self, chunk: AudioChunk) -> None:
        """Runs in the event loop — safe put with drop-OLDEST on full."""
        # Heartbeat update for the stall watchdog. Even if the queue is full,
        # the stream is considered alive — we update the timestamp before the
        # put; otherwise drops would corrupt the stall signal.
        self._last_chunk_monotonic = time.monotonic()
        if self._silence_guard_armed:
            self._track_silence(chunk.pcm, self._last_chunk_monotonic)
        try:
            self._queue.put_nowait(chunk)
        except asyncio.QueueFull:
            # A consumer that cannot keep up in real time (a weaker CPU running
            # the per-frame VAD / wake inference) backs the queue up. Drop the
            # OLDEST chunk and enqueue the newest so the consumer always processes
            # near-PRESENT audio (staleness bounded to the queue depth) instead of
            # a growing stale backlog — the wake detector then scores fresh frames
            # and the VAD sees the current end-of-speech silence promptly, not a
            # 2 s-old snapshot. Mirrors the wake fanout's existing drop-oldest
            # policy (``_run_parallel_wake``). Still counted as a drop; the VAD's
            # timestamp gap-credit accounts for the dropped time so end-of-speech
            # stays anchored to real wall-clock, not delivered-frame count.
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:  # A concurrent consumer already freed the slot.
                pass
            try:
                self._queue.put_nowait(chunk)
            except asyncio.QueueFull:  # A concurrent producer won the replacement slot.
                pass
            self._drops += 1

    async def _try_open_stream(self, *, interactive: bool | None = None) -> None:
        """Open the preferred mic, then recover through safe alternatives.

        Extracted from ``__aenter__`` so the stall watchdog can reuse the same
        logic. Automatic/name-based selection may fail over to another physical
        input; an explicit numeric device remains pinned.

        The permission is ensured ONCE here, before any device is opened
        (``MicrophoneAccessError`` otherwise). ``interactive=None`` means the
        constructor's choice; the watchdog restarts pass ``False`` because a
        restart is never a user gesture.
        """
        await self._ensure_access(
            interactive=self._interactive if interactive is None else interactive
        )
        preferred_attempts: list[int | str | None] = [self._preferred_device]
        try:
            if isinstance(self._preferred_device, int):
                for fallback in _fallback_input_devices(self._preferred_device):
                    if fallback not in preferred_attempts:
                        preferred_attempts.append(fallback)
        except Exception as exc:  # noqa: BLE001
            _log.debug("Mic host-API fallback enumeration failed: {}", exc)

        # A numeric device is an explicit pin. ``None`` and string specs are
        # automatic/name-based choices, so they may move to another real input
        # when CoreAudio, ALSA, or a Windows endpoint refuses to reopen after a
        # completed voice turn.
        # Preserve the Windows recovery contract within each physical group:
        # try 16 kHz on the selected endpoint and each same-device host-API twin
        # before changing the hardware rate. Only after preferred-device target
        # and native rates fail do we move to another physical microphone.
        def _open_candidates() -> Iterator[tuple[int | str | None, int, bool]]:
            for attempt in preferred_attempts:
                yield attempt, self._sample_rate, False
            # Lazy: native-rate queries and cross-device enumeration stay off
            # the normal 16 kHz fast path and therefore do not add latency to a
            # healthy wake→session microphone handover.
            for attempt in preferred_attempts:
                for rate in _native_input_rates(attempt, self._sample_rate):
                    yield attempt, rate, False
            if isinstance(self._device_spec, int):
                return
            alternate_attempts = [
                candidate
                for candidate in _ranked_input_device_indices(self._device_priority)
                if candidate not in preferred_attempts
            ]
            for attempt in alternate_attempts:
                yield attempt, self._sample_rate, True
            for attempt in alternate_attempts:
                for rate in _native_input_rates(attempt, self._sample_rate):
                    yield attempt, rate, True

        last_error: Exception | None = None
        attempt_count = 0
        for attempt, capture_rate, physical_fallback in _open_candidates():
            attempt_count += 1
            stream = None
            try:
                capture_blocksize = (
                    0
                    if self._blocksize == 0
                    else max(
                        1,
                        round(
                            self._blocksize
                            * capture_rate
                            / max(1, self._sample_rate)
                        ),
                    )
                )
                self._capture_sample_rate = capture_rate
                self._capture_resampler = (
                    None
                    if capture_rate == self._sample_rate
                    else _StreamingPcm16Resampler(
                        capture_rate, self._sample_rate, self._channels
                    )
                )
                # The guard keeps this open out of the PortAudio re-init
                # window of the hot-swap watcher (BUG-102): a stream born
                # between _terminate and _initialize is a native fault. The
                # open runs OFF the event loop so a refresh holding the
                # guard stalls only this open, never the whole loop.
                def _guarded_open(
                    device: int | str | None, rate: int, blocksize: int
                ) -> Any:
                    with topology.stream_open_guard():
                        opened = sd.InputStream(
                            device=device,
                            channels=self._channels,
                            samplerate=rate,
                            blocksize=blocksize,
                            dtype=DTYPE,
                            callback=self._callback,
                        )
                        try:
                            opened.start()
                        except BaseException:
                            # The failed native stream must not leak — the
                            # outer candidate loop only sees the exception.
                            try:
                                opened.close()
                            except Exception:  # noqa: BLE001, S110 - preserve the original open failure
                                pass
                            raise
                        return opened

                stream = await asyncio.to_thread(
                    _guarded_open, attempt, capture_rate, capture_blocksize
                )
                self._stream = stream
                _remember_input_latency(stream)
                self._device = attempt
                self._using_physical_fallback = physical_fallback
                if not physical_fallback:
                    self._preferred_device = attempt
                if isinstance(self._device_spec, str) and not physical_fallback:
                    _touch_resolve_cache(
                        self._device_spec, self._device_priority, attempt
                    )
                if physical_fallback:
                    _log.warning(
                        "Preferred microphone device(s) {} could not be opened; "
                        "using alternative input device {} for this capture.",
                        preferred_attempts,
                        attempt,
                    )
                _log.info(
                    "Mic opened (device={}, capture_sr={}, output_sr={}, "
                    "blocksize={}, dtype={}).",
                    attempt,
                    capture_rate,
                    self._sample_rate,
                    capture_blocksize,
                    DTYPE,
                )
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                # ``InputStream`` can be constructed before ``start`` fails.
                # Close that partial stream so PortAudio/CoreAudio does not keep
                # a poisoned handle across every subsequent wake-loop retry.
                if stream is not None:
                    # Off-loop: a half-opened stream on a device that just
                    # disappeared closes as slowly as any other (see
                    # _discard_stream_off_loop), and this runs on the loop that
                    # serves the desktop window.
                    await self._discard_stream_off_loop(stream)
                # A failed open means the cached/resolved device may be gone —
                # force the next construction through a fresh full resolve, and
                # ask the topology watcher to look NOW rather than at its next
                # poll (that is why the poll interval can stay cheap).
                _invalidate_resolve_cache()
                _request_topology_probe()
                _log.warning(
                    "Mic-Open on device={} at {}Hz failed ({}); trying next "
                    "fallback.",
                    attempt,
                    capture_rate,
                    exc,
                )
        self._using_physical_fallback = False
        _log.error(
            "Mic-Open failed completely ({} attempt(s)) — last error: {}",
            attempt_count,
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

        The same tick is where the microphone grant is watched: one cheap
        ``check`` per tick (never per frame), and a revoke ends the capture
        with ``MicrophoneAccessError``.
        """
        # Initial grace pulse: wait until the first chunk has safely arrived
        # before starting the watchdog, otherwise it fires before the first frame.
        self._last_chunk_monotonic = time.monotonic()
        while not self._closed:
            await asyncio.sleep(self._WATCHDOG_TICK_S)
            if self._closed:
                return
            if await self._access_revoked():
                return
            elapsed = time.monotonic() - self._last_chunk_monotonic
            if elapsed <= self._STALL_THRESHOLD_S:
                if (
                    self._silence_guard_armed
                    and not self._silence_reported
                    and self._zero_since is not None
                    and time.monotonic() - self._zero_since >= _SILENCE_REPORT_AFTER_S
                ):
                    await self._report_digital_silence()
                # The stream is delivering — keep the resolve cache fresh so a
                # capture constructed moments after this one closes (the
                # wake→session handover) skips the ~0.4s device enumeration.
                if (
                    isinstance(self._device_spec, str)
                    and not self._using_physical_fallback
                ):
                    _touch_resolve_cache(
                        self._device_spec, self._device_priority, self._device
                    )
                continue
            # Stalled: whatever we knew about the device landscape is suspect.
            _invalidate_resolve_cache()
            _request_topology_probe()
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
                # A stalled callback stream cannot drain gracefully — see
                # ``_discard_stream`` (abort-first, CoreAudio ``stop()`` can
                # block for minutes after an endpoint transition). Off-loop,
                # because this watchdog runs on the loop that also serves the
                # desktop window.
                await self._discard_stream_off_loop(old_stream)
            self._zero_since = None
            try:
                await self._try_open_stream(interactive=False)
                _log.info("Mic-Restart #{} succeeded.", self._restart_count)
            except MicrophoneAccessError as exc:
                # The grant is gone: no retry loop, the capture ends like a revoke.
                self._end_revoked(exc)
                return
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

    def _end_revoked(self, error: MicrophoneAccessError) -> None:
        """Mark the capture revoked and wake a reader parked in ``stream()``."""
        self._revoked = True
        self._access_error = error
        self._wake_parked_reader()

    async def _access_revoked(self) -> bool:
        """One watchdog-tick permission check. ``True``: the capture was ended.

        ``check`` is silent and cheap. A grant that is no longer there closes
        the native stream, publishes the permission episode through the service
        (``PermissionNeeded`` with the real reason, an episode for the card when
        a user started this capture) and makes ``stream()`` raise
        ``MicrophoneAccessError`` carrying the ``EnsureResult``.
        """
        if not self._watch_access:
            return False
        if self._access_gate is not None:
            if self._legacy_gate_allows():
                return False
            result = _legacy_gate_result(False)
        else:
            try:
                result = await self._read_revoke_result()
            except Exception:  # noqa: BLE001 - an injected gate that raises must not end the watchdog
                # The real service never raises, but a custom PermissionGate may.
                # Treat the tick as "not revoked" so stall recovery and the next
                # tick's check keep working; the revoke is caught on a later tick.
                _log.debug("Mic permission watch tick failed; capture kept.", exc_info=True)
                return False
            if result is None:
                return False
        _log.warning("Mic access is no longer granted ({}); ending the capture.", result.reason)
        # Release the device first: when a consumer learns of the revoke, the
        # native stream (and the OS microphone indicator) is already gone.
        stream, self._stream = self._stream, None
        if stream is not None:
            await self._discard_stream_off_loop(stream)
        self._end_revoked(self._access_error_for(result))
        return True

    async def _read_revoke_result(self) -> Any | None:
        """The service half of one watchdog tick: ``None`` while the grant stands.

        ``check`` first (silent, cheap); only a lost grant goes on to ``ensure``,
        whose ``EnsureResult`` describes the revoke. May raise when a custom gate
        does: the caller contains that.
        """
        gate = self._service_gate()
        state = _state_value(gate.check(_MICROPHONE))
        if state in _GRANTED_STATES:
            self._unreadable_ticks = 0
            return None
        if state == "unavailable":
            # A failed native read is not a revoke: one such tick is forgiven
            # so a transient error cannot kill a working dictation.
            self._unreadable_ticks += 1
            if self._unreadable_ticks < 2:
                return None
        # ensure() publishes the episode and re-reads the state. A denial
        # makes no native request, so an interactive capture may use it to
        # upgrade the episode to a user-origin card; every other state is
        # published as background (a lost stream is never a reason to ask).
        result = await gate.ensure_async(
            _MICROPHONE,
            feature=self._permission_feature,
            interactive=self._interactive and state == "denied",
            wait_s=0.0,
        )
        if result.granted:
            self._unreadable_ticks = 0
            return None
        return result

    async def _report_digital_silence(self) -> None:
        """5 s of exact zeros while the OS says granted: re-check once, report once.

        macOS feeds zeros to a denied or muted input instead of raising an error
        (unverified on a real Mac), so a silent capture would otherwise be
        recorded as if it were speech. The stream stays open: this is a notice,
        not a verdict. A re-check that finds the grant gone is left to the next
        tick's revoke path.
        """
        self._silence_reported = True
        try:
            gate = self._service_gate()
            invalidate = getattr(gate, "invalidate", None)
            if callable(invalidate):
                invalidate(_MICROPHONE)  # drop the cached GRANTED so the re-check is live
            if _state_value(gate.check(_MICROPHONE)) not in _GRANTED_STATES:
                return
        except Exception:  # noqa: BLE001 - a notice must never end the watchdog
            _log.debug("Mic silence re-check failed; no notice published.", exc_info=True)
            return
        _log.warning(
            "Mic delivered only digital silence for {:.0f}s while access reads as "
            "granted (denied or muted input).",
            _SILENCE_REPORT_AFTER_S,
        )
        try:
            from jarvis.core.bus import get_default_bus  # noqa: PLC0415 - only on this rare path

            await get_default_bus().publish(
                PermissionNeeded(
                    source_layer="audio.capture",
                    permissions=(_MICROPHONE,),
                    feature=self._permission_feature,
                    reason="denied",
                    phase="blocked",
                    # Background on purpose: the OS says granted, so this is more
                    # likely a muted or noise-gated input than a denial, and a
                    # floating "Open Settings" card would be wrong. The notice has
                    # no service episode (so no close on the first audible frame):
                    # a closable "denied or muted" episode is a service API request.
                    origin="background",
                    can_open_settings=True,
                    detail=_SILENCE_DETAIL,
                )
            )
        except Exception:  # noqa: BLE001 - a notice must never break the capture
            _log.debug("Mic silence notice could not be published.", exc_info=True)

    def discard_native_stream(self) -> None:
        """Drop the native stream so the stall watchdog reopens it (BUG-102).

        Called by the topology watcher (worker thread) right before a
        PortAudio re-init: the old stream belongs to the dying PortAudio
        instance and must not survive it. Backdating the heartbeat makes the
        watchdog fire on its next one-second tick instead of waiting out the
        full stall threshold, so the audible mic gap stays minimal.
        """
        stream, self._stream = self._stream, None
        if stream is not None:
            self._discard_stream(stream)
        if not isinstance(self._device_spec, int):
            # Indices renumber across a PortAudio re-init; retrying the stale
            # resolved index first could silently open a DIFFERENT physical
            # device. Re-enter name/auto resolution against the fresh table.
            self._preferred_device = self._device_spec
        self._last_chunk_monotonic = min(
            self._last_chunk_monotonic,
            time.monotonic() - self._STALL_THRESHOLD_S - 1.0,
        )

    #: How long a coroutine waits for a native stream to shut down before it
    #: leaves the rest to a worker thread. Long enough that the ordinary close
    #: (milliseconds) still finishes in order; short enough that a wedged one
    #: cannot be mistaken for normal work.
    _DISCARD_GRACE_S = 2.0

    @classmethod
    async def _discard_stream_off_loop(cls, stream: Any) -> None:
        """Discard *stream* without ever freezing the event loop.

        ``Pa_CloseStream`` is a synchronous native call, and after an endpoint
        transition it can sit there for minutes — measured on Windows/WASAPI on
        2026-08-20: 61.7 s and 188.3 s, both from this watchdog. Every one of
        those seconds froze the SINGLE asyncio loop this process has, which is
        also the loop serving the desktop window's HTTP: the socket kept
        accepting, nothing was answered, and a window opening in that gap stayed
        blank with no error (see jarvis/ui/window_watchdog.py).

        The dead stream's shutdown is not worth that. It runs in a worker
        thread; the coroutine waits a short grace so the normal case keeps its
        ordering, then moves on and lets the thread finish alone.
        """
        task = asyncio.create_task(asyncio.to_thread(cls._discard_stream, stream))
        done, _pending = await asyncio.wait({task}, timeout=cls._DISCARD_GRACE_S)
        if done:
            return
        # Left running on purpose. Consume the eventual result so a failure in
        # a stream we already gave up on cannot surface as a stray "task
        # exception was never retrieved".
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        _log.warning(
            "Mic discard is still blocked after {:.0f}s (native close wedged) — "
            "continuing without it; the event loop stays responsive.",
            cls._DISCARD_GRACE_S,
        )

    @staticmethod
    def _discard_stream(stream: Any) -> None:
        """Abort-then-close a (possibly wedged) native stream, never raising.

        Abort discards the dead native stream immediately; CoreAudio can
        leave ``stop()`` blocked for minutes after an endpoint transition.
        The ``stop`` fallback keeps lightweight test doubles compatible.

        Blocking by nature — callers ON the event loop must go through
        :meth:`_discard_stream_off_loop`.
        """
        discard_method = "abort"
        try:
            abort = getattr(stream, "abort", None)
            if callable(abort):
                abort()
            else:
                discard_method = "stop"
                stream.stop()
        except Exception as exc:  # noqa: BLE001
            _log.debug("Mic discard: {}() ignored ({}).", discard_method, exc)
        try:
            stream.close()
        except Exception as exc:  # noqa: BLE001
            _log.debug("Mic discard: close() ignored ({}).", exc)

    async def __aenter__(self) -> MicrophoneCapture:
        self._loop = asyncio.get_running_loop()
        await self._try_open_stream()
        # Start the watchdog only AFTER the first successful open; otherwise it
        # would race against a None stream during the initial open.
        self._watchdog_task = asyncio.create_task(
            self._stream_watchdog(), name="mic-stall-watchdog"
        )
        # Visible to the hot-swap watcher so a PortAudio re-init can quiesce
        # this stream first (BUG-102).
        topology.register_capture(self)
        return self

    async def __aexit__(self, *exc_info) -> None:
        topology.unregister_capture(self)
        self._closed = True
        self._wake_parked_reader()
        watchdog = self._watchdog_task
        self._watchdog_task = None
        if watchdog is not None and not watchdog.done():
            watchdog.cancel()
            try:
                await watchdog
            except asyncio.CancelledError:  # Cancellation is the successful watchdog teardown.
                pass
            except Exception as exc:  # noqa: BLE001
                _log.debug("Mic-Watchdog cleanup swallow: {}", exc)
        if self._stream is not None:
            stream, self._stream = self._stream, None
            # Same reason as the watchdog path: closing a microphone is a
            # native call that can wedge, and every capture teardown runs on
            # the loop that serves the window. The handover happens between
            # wake and session on every single turn, so a freeze here is felt
            # more often than anywhere else.
            await self._discard_stream_off_loop(stream)
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
        only sees a brief audio gap and continues reading. When the watchdog
        finds the microphone grant gone, the next read raises
        ``MicrophoneAccessError`` (no per-frame permission probe happens here).
        """
        while not self._closed:
            self._raise_if_revoked()
            chunk = await self._queue.get()
            if self._closed:
                # Woken by ``__aexit__``: the device is gone and so is the
                # reason for this consumer to exist. A reader parked here on an
                # empty queue used to sit forever after close (BUG-185).
                return
            # Woken by the watchdog's revoke marker (or a frame that raced it).
            self._raise_if_revoked()
            yield chunk

    def _raise_if_revoked(self) -> None:
        error = self._access_error
        if error is not None:
            raise MicrophoneAccessError(str(error), result=error.result)

    @property
    def revoked(self) -> bool:
        """``True`` once the microphone grant was lost while the capture was open."""
        return self._revoked

    @property
    def dropped_frames(self) -> int:
        """Number of frames lost due to a full queue."""
        return self._drops

    @property
    def overflow_count(self) -> int:
        """How often PortAudio reported an input overflow on this stream.

        Cumulative for the stream's lifetime — a consumer that wants "during
        my turn" takes the difference between two readings.
        """
        return self._overflows

    @property
    def queue_depth(self) -> int:
        """Current bound of the bridge queue, in chunks."""
        return self._queue.maxsize

    def set_queue_depth(self, max_queue_chunks: int) -> int:
        """Re-bound the bridge queue for a new kind of consumer. Returns the old bound.

        The one lever that decides whether a slow moment costs WORDS. When the
        event loop stalls longer than the queue holds, ``_safe_put`` starts
        dropping the oldest frame per arrival and that speech is gone for good —
        no retry reaches it, because it never became a chunk anyone read. A
        bulk consumer therefore raises the bound for as long as it owns the
        stream and puts it back afterwards, so the wake detector does not
        inherit a depth that would let it score seconds-stale audio.

        Takes effect immediately, including for a consumer already waiting in
        ``stream()`` — the queue object itself never changes identity.
        """
        previous = self._queue.maxsize
        self._queue.set_maxsize(max_queue_chunks)
        return previous

    @property
    def restart_count(self) -> int:
        """Number of stream restarts triggered by the stall watchdog."""
        return self._restart_count


def pcm_bytes_to_np(pcm: bytes) -> np.ndarray:
    """Convert int16 PCM bytes to numpy float32 [-1.0, 1.0] — Whisper input format."""
    int16 = np.frombuffer(pcm, dtype=np.int16)
    return int16.astype(np.float32) / 32768.0
