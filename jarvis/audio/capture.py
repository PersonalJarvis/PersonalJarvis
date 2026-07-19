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
import sys
import time
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from typing import TYPE_CHECKING, Any

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

from jarvis.audio.device_select import is_legacy_primary_mapper
from jarvis.core.protocols import AudioChunk

SAMPLE_RATE = 16_000       # Whisper native rate
CHANNELS = 1               # Mono is sufficient for speech
BLOCKSIZE = 1600           # 100 ms blocks — compromise between latency and CPU overhead
DTYPE = "int16"


class MicrophoneAccessError(PermissionError):
    """The current process is not allowed to open the local microphone."""


def _macos_microphone_access_gate() -> Callable[[], bool] | None:
    """Build an uncached, non-prompting TCC gate for a macOS capture.

    Importing the native permission port is deliberately deferred so the base
    audio module remains portable on Windows, Linux, and headless installs.
    The returned port probes both the grant and app-bundle identity on every
    call; it never invokes an Apple request API.
    """
    if sys.platform != "darwin":
        return None
    try:
        from jarvis.platform.permissions import (  # noqa: PLC0415
            PermissionId,
            get_system_permission_port,
        )

        port = get_system_permission_port()
    except Exception:  # noqa: BLE001 - protected capture must fail closed
        return lambda: False

    def _allowed() -> bool:
        try:
            return port.runtime_access_granted(PermissionId.MICROPHONE)
        except Exception:  # noqa: BLE001 - a probe failure is not permission
            return False

    return _allowed

# Queue depth for a REAL-TIME detection consumer (VAD endpointing, wake, barge).
# ~0.6 s: shallow enough that on a CPU which can't process every frame in real
# time the drop-OLDEST overflow policy keeps the audio near-present (so
# end-of-speech silence and the wake word are seen promptly, not seconds late),
# yet deep enough to absorb normal scheduling jitter on a machine that keeps up
# (which never fills it). Bulk recorders that must keep every frame
# (push-to-talk, dictation) use the deeper default instead. See MicrophoneCapture.
REALTIME_QUEUE_CHUNKS = 6

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
    except Exception:
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


class _StreamingPcm16Resampler:
    """Stateful CPU-only resampler for interleaved PCM16 microphone frames.

    PortAudio callbacks split a continuous signal into independent buffers. A
    stateless per-buffer conversion would introduce a discontinuity at every
    boundary, so the final source frame and fractional position are retained
    for the next callback. NumPy is part of the universal base installation;
    no GPU or optional native inference dependency is involved.
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
        except TypeError:
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
    _ACCESS_RECHECK_S: float = 0.25

    def __init__(
        self,
        device: int | str | None = None,
        sample_rate: int = SAMPLE_RATE,
        blocksize: int = BLOCKSIZE,
        channels: int = CHANNELS,
        max_queue_chunks: int = 20,
        device_priority: Sequence[str] | None = None,
        access_gate: Callable[[], bool] | None = None,
    ) -> None:
        self._access_gate = (
            access_gate if access_gate is not None else _macos_microphone_access_gate()
        )
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
        # ``max_queue_chunks`` blocks, so worst-case staleness ==
        # max_queue_chunks x 100 ms. The default 20 (~2 s) is generous back-
        # pressure for a bulk consumer (push-to-talk, which records every frame).
        # A REAL-TIME detection consumer (VAD endpointing, wake) passes a SHALLOW
        # depth (~0.6 s) so that on a CPU that can't keep up the end-of-speech
        # silence and the wake word are seen near-present, not 2 s late — the
        # "stuck listening / missed wake on a weaker laptop" bug. A machine that
        # keeps up never fills the queue, so the depth is invisible there.
        self._queue: asyncio.Queue[AudioChunk] = asyncio.Queue(
            maxsize=max(1, int(max_queue_chunks))
        )
        self._stream: sd.InputStream | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._drops = 0
        self._closed: bool = False
        self._last_chunk_monotonic: float = 0.0
        self._watchdog_task: asyncio.Task | None = None
        self._restart_count: int = 0

    def _require_microphone_access(self) -> None:
        """Fail before a native open and whenever a live grant is revoked."""
        gate = self._access_gate
        if gate is None:
            return
        try:
            allowed = bool(gate())
        except Exception:  # noqa: BLE001 - protected capture must fail closed
            allowed = False
        if not allowed:
            raise MicrophoneAccessError(
                "Microphone capture requires a granted macOS permission under "
                "the installed Personal Jarvis app identity."
            )

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
            except RuntimeError:
                self._drops += 1

    def _safe_put(self, chunk: AudioChunk) -> None:
        """Runs in the event loop — safe put with drop-OLDEST on full."""
        # Heartbeat update for the stall watchdog. Even if the queue is full,
        # the stream is considered alive — we update the timestamp before the
        # put; otherwise drops would corrupt the stall signal.
        self._last_chunk_monotonic = time.monotonic()
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
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(chunk)
            except asyncio.QueueFull:
                pass
            self._drops += 1

    async def _try_open_stream(self) -> None:
        """Open the preferred mic, then recover through safe alternatives.

        Extracted from ``__aenter__`` so the stall watchdog can reuse the same
        logic. Automatic/name-based selection may fail over to another physical
        input; an explicit numeric device remains pinned.
        """
        self._require_microphone_access()
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
            self._require_microphone_access()
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
                stream = sd.InputStream(
                    device=attempt,
                    channels=self._channels,
                    samplerate=capture_rate,
                    blocksize=capture_blocksize,
                    dtype=DTYPE,
                    callback=self._callback,
                )
                stream.start()
                self._stream = stream
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
                    try:
                        stream.close()
                    except Exception as close_exc:  # noqa: BLE001
                        _log.debug(
                            "Mic-Open cleanup for device={} ignored ({}).",
                            attempt,
                            close_exc,
                        )
                # A failed open means the cached/resolved device may be gone —
                # force the next construction through a fresh full resolve.
                _invalidate_resolve_cache()
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
            self._require_microphone_access()
            if self._access_gate is None:
                chunk = await self._queue.get()
            else:
                try:
                    chunk = await asyncio.wait_for(
                        self._queue.get(), timeout=self._ACCESS_RECHECK_S
                    )
                except TimeoutError:
                    # No audio frame is still a live interval: recheck TCC so a
                    # revoke closes a stalled/silent stream without waiting for
                    # PortAudio to produce another callback.
                    continue
            self._require_microphone_access()
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
