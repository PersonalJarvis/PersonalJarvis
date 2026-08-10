"""Local user-speech transcription for realtime transports that lack it.

Every Jarvis feature that reacts to what the USER said hangs off one event:
``input_transcript``. The live bar text, the speaking indicators, the
"auflegen" hang-up phrase, the echo guard, and — most importantly — the
delegate that reaches the wiki, the project files and every Jarvis action.

Most realtime providers transcribe user audio themselves. ChatGPT-Live does
not: its notification stream carries assistant transcripts only (verified
live — 14 assistant deltas, zero user deltas, no speech-start items), and its
client-event vocabulary has no way to ask for one. Without this bridge that
provider is deaf to Jarvis: the model answers, but Jarvis itself never learns
what was said, so none of the integrations fire.

So Jarvis transcribes the microphone audio it already owns, with the
recognizer the user already configured, and emits the same events every
other provider emits. "It is just another provider" then holds.

On such a transport this class is also the GROUNDING evidence: the adapter
authorizes a provider response only when a fresh local ``speech_started``
proves a human spoke, and otherwise rejects AND interrupts that response. So
every way this class can go quiet — a wedged engine, a mis-sampled buffer, a
mic below a hardcoded gate, a dropped control event — is indistinguishable
from "the assistant never answers again". Everything below is written for that
consequence, not merely for a missing transcript.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Any

# Leaf module, stdlib-only — importing it here drags in neither the speech
# pipeline nor any recognizer runtime. It owns the ONE silence-boilerplate
# verdict the dictation delivery gate uses as well (BUG-008 drift rule).
from jarvis.speech.wake_constants import is_silence_hallucination

log = logging.getLogger(__name__)

# Endpointing: deliberately simple and dependency-free. The far end runs its
# own turn detection for the model's benefit; this endpointer only has to
# decide when to hand one utterance to the recognizer.
#
# These are the CONFIGURED ceilings. The gate actually applied is calibrated
# down against the session's own noise floor — see ``_NoiseFloor``.
_SPEECH_RMS = 700.0
_SILENCE_RMS = 500.0
#: The legacy relationship between the two absolute gates, reused to scale the
#: silence gate whenever the speech gate is calibrated down.
_SILENCE_RATIO = _SILENCE_RMS / _SPEECH_RMS
_SPEECH_START_MS = 120
_SILENCE_END_MS = 700
_PREROLL_MS = 300
_MAX_UTTERANCE_MS = 20_000
# Minimum VOICED audio — not buffer length. The buffer also holds pre-roll and
# the trailing silence that closed the utterance, so measuring it would let a
# cough or a keyboard knock buy a recognizer call and, worse, a transcript
# that Jarvis would treat as something the user said.
_MIN_VOICED_MS = 300

# Session-relative noise-floor calibration (AP-23; the same defect and the same
# fix already shipped one layer up for the wake gates —
# ``jarvis.speech.rolling_whisper_wake.SessionNoiseFloor``). The absolute gates
# above were chosen on one machine. On a quieter input path real speech lands
# below them, so ``_begin_utterance`` never fires — and because a missing
# ``speech_started`` is missing GROUNDING, the adapter then rejects and
# interrupts every provider response: the user talks and is cut off forever.
#
#     effective = min(configured_absolute, max(K * floor, absolute_minimum))
#
# Strictly LOWER-ONLY. ``_FLOOR_INIT`` is deliberately pessimistic so a session
# STARTS at the legacy absolutes (K * init >= _SPEECH_RMS) and relaxes only once
# measured silence proves the mic is quieter — every historical measurement and
# pin therefore still holds on a normal mic.
#
# The word-agnostic discriminator (AP-27) survives BY CONSTRUCTION: silence and
# speaker echo sit AT the floor while the gate sits at ``_SPEECH_GATE_K`` x the
# floor, at ANY mic gain. Nothing here looks at what was said — only at energy.
_FLOOR_INIT = 350.0
_FLOOR_ABS_MIN = 7.0  # a muted mic's numeric noise must never define a floor
_FLOOR_ALPHA = 0.05  # EMA weight per quiet chunk
_FLOOR_QUIET_BAND = 1.5  # a chunk this close to the floor counts as quiet
# The floor must also be able to rise. A room noisier than the initial estimate
# puts every non-speech chunk ABOVE the quiet band, so a purely downward
# estimator can never learn it: the silence gate stays under the room tone and
# the utterance only ever ends at ``_MAX_UTTERANCE_MS`` — a transcript up to
# 20 s late. A sustained run of loud NON-SPEECH chunks cannot be one utterance,
# so it is the room, and the floor follows it. Capped at ``_SPEECH_RMS`` so a
# leaking speaker can never desensitize the gate past its legacy value.
_FLOOR_RISE_AFTER = 25  # consecutive above-band non-speech chunks (~0.5 s)
_FLOOR_RISE_ALPHA = 0.25
_SPEECH_GATE_K = 2.0
_SPEECH_GATE_ABS_MIN = 50.0

# The SILENCE gate is anchored to the floor FROM ABOVE, never derived from the
# speech gate from below. Deriving it downward is a trap with a sharp edge: the
# floor is by definition the level real silence sits at, so any gate placed
# BELOW the floor can never be crossed — ``_silence_ms`` never accumulates, the
# utterance never closes, and the recognizer is never called. The endpointer
# then produces one ``speech_started`` and no transcript at all, which on this
# transport reads as "the first turn worked and then it went mute forever".
#
# So: sit a clear margin ABOVE the measured floor, and clamp strictly below the
# speech gate so the hysteresis can never invert.
_SILENCE_GATE_FLOOR_K = 1.4
_SILENCE_GATE_MAX_FRACTION = 0.9

# How long after the microphone last carried real speech a server-side user
# transcript is still plausible. The far end transcribes with its own latency,
# so a genuine transcript trails the audio; a hallucinated one arrives while
# the user is silent or while the assistant itself is talking.
_SERVER_TRANSCRIPT_GRACE_MS = 2_000

# Recognizer input rate. Providers that pin one declare it; the in-tree ones all
# expect 16 kHz (the local Whisper provider REJECTS anything else outright, the
# local Nemotron decoder silently mis-reads it, and the cloud providers wrap
# whatever rate they are handed into a WAV header), so 16 kHz is the documented
# fallback and the capture rate is resampled to it rather than passed through.
_RECOGNIZER_RATE_FALLBACK = 16_000
_RECOGNIZER_RATE_ATTRS = ("native_sample_rate", "input_sample_rate", "sample_rate")

# A recognizer call is bounded, and the bound scales with the audio handed in:
# a flat ceiling either aborts a legitimate long segment or lets a wedged engine
# hold the path for as long as the longest legitimate one (AP-24).
_RECOGNITION_TIMEOUT_BASE_S = 6.0
_RECOGNITION_TIMEOUT_PER_AUDIO_S = 1.5
_RECOGNITION_TIMEOUT_MAX_S = 90.0
# A timed-out call leaves an UNCANCELLABLE worker thread inside the native
# engine, so the next call finds it occupied. Rebuilding after this many
# consecutive hard failures is the only way back (AP-24 / BUG-036); a
# slow-but-alive engine that merely reports "busy" gets a longer leash, exactly
# as the wake poll loop gives it.
_RECOVER_AFTER_FAILURES = 2
_RECOVER_AFTER_BUSY = 3

# A SET busy flag is not evidence of a wedge. Since one engine is shared
# process-wide, the ordinary meaning of "busy" is that ANOTHER session is inside
# a perfectly healthy recognition — the cache doing exactly its job. Counting
# those skips toward a rebuild made routine contention indistinguishable from a
# wedge: the second session evicted the shared engine and tore the backend down
# while the first was still inside an inference, so that utterance was lost, both
# sessions re-paid a multi-second model load, and the weights were briefly
# resident twice.
#
# What DOES prove a wedge is wall-clock. Every recognition runs under its own
# ``asyncio.wait_for`` bound and every exit path — success, timeout, error,
# cancellation — clears the flag, so a busy span that outlives the holder's own
# bound by this margin cannot still be running: the holder is gone (its loop went
# away mid-flight, or a native worker was abandoned) and the flag will never
# clear by itself. Only that span may rebuild the engine.
_BUSY_WEDGE_GRACE_S = 10.0

# Control events (``speech_started``) are grounding evidence, not telemetry: a
# dropped one makes the adapter treat the next genuine answer as a self-echo and
# interrupt it. The queue is therefore bounded by EVICTING the oldest droppable
# event, never by refusing whatever arrived last.
_EVENT_QUEUE_MAX = 256

SPEECH_STARTED = "speech_started"
TRANSCRIPT = "transcript"
TRANSCRIPT_FAILED = "transcript_failed"
SPEECH_DISCARDED = "speech_discarded"

# A discarded (too short or confidently boilerplate) utterance must close the
# ``speech_started`` edge it already emitted. The subscription adapter handles
# this kind explicitly: it preserves any provider-side caption as a fallback,
# but does not surface a transcription error when the audio was not a real turn.
_DISCARD_STREAK_WARN = 5


class RecognizerBusy(RuntimeError):
    """A recognition was skipped because this recognizer is already in use.

    Deliberately a SKIP rather than a wait (AP-24): the configured STT may own a
    native engine that a second concurrent entry wedges permanently, and queueing
    behind a call that has already hung would make every later utterance hang too.
    """


@dataclass(frozen=True, slots=True)
class InputTranscriptEvent:
    """One normalized user-speech event for the provider event stream."""

    kind: str  # see the module-level kind constants
    text: str = ""
    is_final: bool = False
    # Voiced milliseconds behind this event. Diagnosis only — no decision hangs
    # on it, but a discarded utterance is unreadable without it.
    voiced_ms: int = 0


def _rms(pcm: bytes) -> float:
    import numpy as np  # noqa: PLC0415

    if not pcm:
        return 0.0
    samples = np.frombuffer(pcm, dtype=np.int16)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))


class _NoiseFloor:
    """EMA estimate of this session's noise floor over per-chunk RMS values.

    Ported from ``jarvis.speech.rolling_whisper_wake.SessionNoiseFloor`` rather
    than imported: that module imports the faster-whisper provider at module
    level, and the realtime path must stay usable on a base install without the
    local-voice extra.

    Only chunks in the quiet band (below ``quiet_band`` x the current floor)
    update the estimate, so speech never drags the floor up; the floor may drift
    up slowly when the ROOM gets noisier (each step is bounded by the band) and
    is clamped to ``abs_min`` so a muted mic cannot zero it.
    """

    def __init__(
        self,
        initial: float = _FLOOR_INIT,
        abs_min: float = _FLOOR_ABS_MIN,
        alpha: float = _FLOOR_ALPHA,
        quiet_band: float = _FLOOR_QUIET_BAND,
    ) -> None:
        self._floor = float(initial)
        self._abs_min = float(abs_min)
        self._alpha = float(alpha)
        self._quiet_band = float(quiet_band)
        self._above_band = 0

    @property
    def floor(self) -> float:
        return self._floor

    def update(self, rms: float) -> None:
        """Fold one NON-SPEECH chunk into the estimate.

        Only the caller knows whether an utterance is open; feeding speech in
        here would let one loud sentence redefine the room.
        """
        if rms < self._floor * self._quiet_band:
            self._above_band = 0
            floor = (1.0 - self._alpha) * self._floor + self._alpha * rms
            self._floor = max(floor, self._abs_min)
            return
        # Above the band, and not speech: either the room is louder than the
        # current estimate, or this is a transient. Only a sustained run is
        # allowed to move the floor upward.
        self._above_band += 1
        if self._above_band < _FLOOR_RISE_AFTER:
            return
        self._above_band = 0
        floor = (1.0 - _FLOOR_RISE_ALPHA) * self._floor + _FLOOR_RISE_ALPHA * rms
        self._floor = min(max(floor, self._abs_min), _SPEECH_RMS)

    def gate(self, configured: float, k: float, abs_min: float) -> float:
        """Lower-only effective gate: never above ``configured``."""
        if configured <= 0.0:
            return configured
        return min(configured, max(k * self._floor, abs_min))


@dataclass(slots=True)
class _SharedRecognizer:
    """One process-wide recognizer plus the in-flight guard that follows it.

    The guard belongs to the ENGINE rather than to the transcriber holding it:
    once two sessions can share one cached backend, a per-object flag cannot see
    the other caller, and entering a native engine twice at once is what wedges
    it permanently (AP-24).

    ``busy_since`` / ``busy_bound_s`` travel with the flag for the same reason:
    the transcriber that has to decide whether a busy engine is merely serving
    someone else or is genuinely wedged is a DIFFERENT object from the one that
    took the guard, so the evidence cannot live on the holder.
    """

    backend: Any
    busy: bool = False
    #: Monotonic instant the in-flight recognition took the guard, and the
    #: timeout it runs under. Both are meaningless while ``busy`` is False.
    busy_since: float = 0.0
    busy_bound_s: float = 0.0


# Building the recognizer is the expensive half of opening a realtime call — a
# local engine costs seconds and loads weights that are identical for every
# session asking for the same configuration, so a per-transcriber engine made
# every call re-pay that inside its own handshake. Keyed by the CONSTRUCTION
# parameters (the effective STT config, session language pin included), so a
# config change or a different session language still builds a fresh engine.
# Bounded, because every retained entry can pin a model in memory.
_STT_CACHE_MAX = 4
_stt_cache: OrderedDict[str, _SharedRecognizer] = OrderedDict()
_stt_cache_lock = threading.Lock()


def _shared_recognizer(key: str, build: Any) -> _SharedRecognizer:
    """The cached recognizer for ``key``, constructed at most once per process.

    ``build`` runs UNDER the lock deliberately: a second concurrent build of the
    same configuration is precisely the duplicated model load this cache exists
    to remove, and two DIFFERENT configurations racing on the voice path is not
    a case worth trading that guarantee for.
    """
    with _stt_cache_lock:
        cached = _stt_cache.get(key)
        if cached is not None:
            _stt_cache.move_to_end(key)
            return cached
        entry = _SharedRecognizer(backend=build())
        _stt_cache[key] = entry
        while len(_stt_cache) > _STT_CACHE_MAX:
            _stt_cache.popitem(last=False)
        return entry


def _forget_shared_recognizer(entry: _SharedRecognizer) -> None:
    """Retire a wedged engine so the next session is not handed it back."""
    with _stt_cache_lock:
        for key, cached in tuple(_stt_cache.items()):
            if cached is entry:
                del _stt_cache[key]


def _reset_for_tests() -> None:
    """Drop the process-wide recognizer cache."""
    with _stt_cache_lock:
        _stt_cache.clear()


def _language_pinned_stt_config(stt_cfg: Any, language: str | None) -> Any:
    """``stt_cfg`` with the session's recognition language pinned onto it.

    The configured recognizer takes its language from the STT config, so this is
    where a session language has to land: from here the value flows through the
    per-provider gates the STT factory already owns, instead of every backend
    being probed for a keyword it may not have. A config object that cannot
    carry a language (a stand-in, an older shape) is returned untouched — the
    call keeps its configured language rather than losing its recognizer over an
    attribute.
    """
    if not language:
        return stt_cfg
    copy = getattr(stt_cfg, "model_copy", None)
    if not callable(copy) or not hasattr(stt_cfg, "language"):
        log.debug(
            "The STT configuration carries no language field, so the local "
            "input recognizer keeps its configured language instead of %r",
            language,
        )
        return stt_cfg
    return copy(update={"language": language})


def _build_default_stt(language: str | None) -> _SharedRecognizer:
    """Build (or reuse) the configured recognizer. Blocking — call in a thread."""
    from jarvis.core.config import load_config  # noqa: PLC0415
    from jarvis.plugins.stt import build_stt_from_config  # noqa: PLC0415

    stt_cfg = _language_pinned_stt_config(load_config().stt, language)
    return _shared_recognizer(repr(stt_cfg), lambda: build_stt_from_config(stt_cfg))


class LocalInputTranscriber:
    """Turn microphone PCM into ``input_transcript`` events."""

    def __init__(
        self,
        *,
        sample_rate: int = 24_000,
        stt_factory: Any = None,
        recognizer_sample_rate: int | None = None,
        language: str | None = None,
    ) -> None:
        self._sample_rate = sample_rate
        self._stt_factory = stt_factory
        # The session's recognition language. Empty means "whatever the STT
        # configuration says", which is exactly what callers got before this
        # parameter existed. Pinning it matters because an ``auto`` recognizer
        # is the configuration that answers near-silence with caption-style
        # boilerplate in the wrong language — text this transport would then
        # treat as something the user said.
        self._language = (language or "").strip() or None
        self._factory_language_logged = False
        self._recognizer_rate_override = (
            int(recognizer_sample_rate) if recognizer_sample_rate else None
        )
        self._recognizer_rate: int | None = None
        self._stt: Any = None
        self._shared: _SharedRecognizer | None = None
        # Control events must survive a stalled consumer, so this is a deque
        # evicted by policy rather than an asyncio.Queue that refuses whatever
        # arrives last (see ``_emit``). Everything here runs on one event loop,
        # so no cross-thread synchronization is required.
        self._events: deque[InputTranscriptEvent | None] = deque()
        self._event_ready = asyncio.Event()
        self._preroll: list[bytes] = []
        self._preroll_ms = 0
        self._utterance: list[bytes] = []
        self._utterance_ms = 0
        self._voiced_ms = 0
        self._speech_ms = 0
        self._silence_ms = 0
        self._in_speech = False
        self._last_speech_end = 0.0
        self._floor = _NoiseFloor()
        self._tasks: set[asyncio.Task[None]] = set()
        # A configured recognizer may wrap a native inference engine. Those
        # engines are not safe to enter concurrently (AP-24), and output
        # transcript recovery can overlap the tail of input recognition. The
        # guard is a non-blocking flag, NOT a lock: a second caller skips, so a
        # call that has hung can never make every later call hang behind it.
        # Held here only while this transcriber owns its engine alone; a cached
        # engine carries the flag itself (see ``_recognition_busy``).
        self._local_recognition_busy = False
        # The unshared mirror of the shared record's busy timestamps.
        self._local_busy_since = 0.0
        self._local_busy_bound_s = 0.0
        # Serializes only the CONSTRUCTION of the recognizer, so two utterances
        # racing at session start cannot load the model twice. Never held across
        # an inference: a model load inside the inference guard made the first
        # output-transcript recovery time out by construction.
        self._build_lock = asyncio.Lock()
        self._consecutive_failures = 0
        self._consecutive_busy = 0
        self._discarded_streak = 0
        self._closed = False
        self._unavailable_logged = False
        self._rate_mismatch_logged = False

    # -- voice activity ------------------------------------------------
    def speech_recently(self, grace_ms: int = _SERVER_TRANSCRIPT_GRACE_MS) -> bool:
        """Did the microphone actually carry speech just now?

        The answer is derived from audio ENERGY alone — never from the words
        of any transcript (AP-27). Server-side recognizers hallucinate
        caption-style text ("[exhale]", "pixelated image") on silence and on
        the echo of the assistant's own voice, and no spelling rule can
        separate those from a genuine short utterance. Their giveaway is
        physical: they arrive when nobody was making a sound.
        """
        if self._in_speech:
            return True
        if self._last_speech_end <= 0.0:
            return False
        return (time.monotonic() - self._last_speech_end) * 1000.0 <= grace_ms

    @property
    def noise_floor(self) -> float:
        """The session's measured noise floor (int16 RMS). Diagnosis only."""
        return self._floor.floor

    def _speech_gate(self) -> float:
        return self._floor.gate(_SPEECH_RMS, _SPEECH_GATE_K, _SPEECH_GATE_ABS_MIN)

    def _silence_gate(self, speech_gate: float) -> float:
        """The level below which a chunk counts as silence, for THIS session.

        Anchored above the measured noise floor rather than scaled down from
        the speech gate: real silence sits AT the floor, so a gate underneath it
        is unreachable and the utterance never ends. The scaled legacy value is
        the lower bound (so a quiet room keeps the historical behaviour) and the
        speech gate the upper bound (so the hysteresis cannot invert).
        """
        return min(
            speech_gate * _SILENCE_GATE_MAX_FRACTION,
            max(
                speech_gate * _SILENCE_RATIO,
                self._floor.floor * _SILENCE_GATE_FLOOR_K,
            ),
        )

    # -- feeding -------------------------------------------------------
    def feed(self, pcm: bytes, sample_rate: int) -> None:
        """Accept one microphone chunk; never blocks the audio path."""
        if self._closed or not pcm:
            return
        if sample_rate != self._sample_rate:
            # The pipeline is fixed at one rate per session; a mismatch means a
            # caller bug that silences this recognizer COMPLETELY — and with it
            # the grounding evidence the transport needs to answer at all. Say
            # so once, at a level that is actually visible (AP-30): a per-chunk
            # debug line is indistinguishable from working.
            if not self._rate_mismatch_logged:
                self._rate_mismatch_logged = True
                log.warning(
                    "Local input transcription is dropping every microphone "
                    "chunk: the session sends %d Hz but this recognizer was "
                    "opened for %d Hz. No user transcript can be produced "
                    "until the two rates agree.",
                    sample_rate,
                    self._sample_rate,
                )
            return
        duration_ms = int(len(pcm) / 2 / self._sample_rate * 1000)
        if duration_ms <= 0:
            return
        level = _rms(pcm)
        speech_gate = self._speech_gate()

        if not self._in_speech:
            # Only non-speech chunks may define the floor, and the quiet band
            # inside ``update`` rejects the rest.
            self._floor.update(level)
            self._preroll.append(pcm)
            self._preroll_ms += duration_ms
            while self._preroll_ms > _PREROLL_MS and len(self._preroll) > 1:
                dropped = self._preroll.pop(0)
                self._preroll_ms -= int(
                    len(dropped) / 2 / self._sample_rate * 1000
                )
            if level >= speech_gate:
                self._speech_ms += duration_ms
                if self._speech_ms >= _SPEECH_START_MS:
                    self._begin_utterance()
            else:
                self._speech_ms = 0
            return

        self._utterance.append(pcm)
        self._utterance_ms += duration_ms
        if level >= speech_gate:
            self._voiced_ms += duration_ms
        if level < self._silence_gate(speech_gate):
            self._silence_ms += duration_ms
        else:
            self._silence_ms = 0
        if (
            self._silence_ms >= _SILENCE_END_MS
            or self._utterance_ms >= _MAX_UTTERANCE_MS
        ):
            self._finish_utterance()

    def _note_discarded(self, voiced_ms: int) -> None:
        """Report an utterance that opened but was too short to be speech.

        Individually this is correct and uninteresting — a cough must not buy a
        recognizer call. A RUN of them without a single transcript in between is
        the interesting signal: it means the gate is opening on something that
        never turns into speech, which is what a mis-calibrated floor or a
        half-open microphone looks like from here. Saying only the first half
        would be a discard that reports nothing actionable (AP-30).
        """
        self._discarded_streak += 1
        log.debug(
            "Local input transcription discarded a %d ms utterance "
            "(minimum %d ms voiced, gate %.0f, floor %.0f)",
            voiced_ms,
            _MIN_VOICED_MS,
            self._speech_gate(),
            self._floor.floor,
        )
        if self._discarded_streak != _DISCARD_STREAK_WARN:
            return
        log.warning(
            "Local input transcription has discarded %d utterances in a row as "
            "too short (last %d ms, minimum %d ms voiced, gate %.0f, floor "
            "%.0f). Speech is opening the gate but never qualifying, so this "
            "call is producing no user transcripts at all.",
            self._discarded_streak,
            voiced_ms,
            _MIN_VOICED_MS,
            self._speech_gate(),
            self._floor.floor,
        )

    def _begin_utterance(self) -> None:
        self._in_speech = True
        # The frames that proved speech started are already voiced audio.
        self._voiced_ms = self._speech_ms
        self._speech_ms = 0
        self._silence_ms = 0
        self._utterance = list(self._preroll)
        self._utterance_ms = self._preroll_ms
        self._preroll = []
        self._preroll_ms = 0
        self._emit(InputTranscriptEvent(kind=SPEECH_STARTED))

    def _finish_utterance(self) -> None:
        pcm = b"".join(self._utterance)
        voiced_ms = self._voiced_ms
        self._in_speech = False
        self._utterance = []
        self._utterance_ms = 0
        self._voiced_ms = 0
        self._silence_ms = 0
        self._speech_ms = 0
        if voiced_ms < _MIN_VOICED_MS or not pcm:
            self._note_discarded(voiced_ms)
            self._emit(
                InputTranscriptEvent(kind=SPEECH_DISCARDED, voiced_ms=voiced_ms)
            )
            return
        # Only a qualifying utterance vouches for a server-side transcript; a
        # cough must not open the door that the energy gate just closed.
        self._discarded_streak = 0
        self._last_speech_end = time.monotonic()
        task = asyncio.create_task(
            self._transcribe(pcm, voiced_ms), name="realtime-local-input-transcribe"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- recognition ---------------------------------------------------
    @property
    def _recognition_busy(self) -> bool:
        """Whether the recognizer behind this transcriber is inside a call.

        Resolved through the shared cache entry whenever there is one: the
        ENGINE is what a second concurrent entry wedges, so two sessions on one
        cached backend have to read and write the same flag.
        """
        shared = self._shared
        return shared.busy if shared is not None else self._local_recognition_busy

    @_recognition_busy.setter
    def _recognition_busy(self, value: bool) -> None:
        # Without a caller-supplied bound the only honest assumption is the
        # widest one a recognition can legitimately run under.
        self._set_recognition_busy(bool(value), bound_s=_RECOGNITION_TIMEOUT_MAX_S)

    def _set_recognition_busy(self, busy: bool, *, bound_s: float) -> None:
        """Flip the in-flight guard and stamp what the holder is bounded by.

        Written onto the shared cache entry whenever there is one, because the
        reader of these fields is the OTHER transcriber on the same engine.
        """
        since = time.monotonic() if busy else 0.0
        bound = float(bound_s) if busy else 0.0
        shared = self._shared
        if shared is not None:
            shared.busy = busy
            shared.busy_since = since
            shared.busy_bound_s = bound
        else:
            self._local_recognition_busy = busy
            self._local_busy_since = since
            self._local_busy_bound_s = bound

    def _busy_wedge_state(self) -> tuple[float, bool]:
        """How long the guard has been held, and whether that proves a wedge.

        Returns ``(0.0, False)`` when nothing is in flight. A span still inside
        the holder's own bound (plus ``_BUSY_WEDGE_GRACE_S``) is ordinary
        sharing, never a reason to tear an engine down underneath its user.
        """
        shared = self._shared
        if shared is not None:
            busy = shared.busy
            since = shared.busy_since
            bound = shared.busy_bound_s
        else:
            busy = self._local_recognition_busy
            since = self._local_busy_since
            bound = self._local_busy_bound_s
        if not busy or since <= 0.0:
            return 0.0, False
        span = max(0.0, time.monotonic() - since)
        return span, span >= bound + _BUSY_WEDGE_GRACE_S

    async def warm(self) -> bool:
        """Build (and prime) the recognizer off the first utterance's path.

        A local engine costs seconds to construct and prime. Paying that inside
        the first utterance delays the first transcript past its own turn AND
        guarantees that a concurrent output-transcript recovery times out —
        which is what abandons a worker thread inside the native engine in the
        first place (AP-24). Call this once when the session opens; it is
        idempotent, never raises, and reports whether a recognizer is available.
        """
        try:
            await self._ensure_stt()
        except Exception:  # noqa: BLE001 - a deaf bar must not stop a call opening
            log.debug(
                "Local input recognizer could not be built while warming",
                exc_info=True,
            )
            self._log_unavailable()
            return False
        prime = getattr(self._stt, "warm_up", None)
        if callable(prime):
            try:
                await asyncio.to_thread(prime)
            except Exception:  # noqa: BLE001 - priming is best-effort by contract
                log.debug(
                    "Local input recognizer could not be primed; the first "
                    "utterance pays the cold cost instead",
                    exc_info=True,
                )
        return True

    def _log_unavailable(self) -> None:
        if self._unavailable_logged:
            return
        self._unavailable_logged = True
        log.warning(
            "Local input transcription is unavailable; this provider's voice "
            "still answers, but Jarvis-side features that need the user's "
            "words stay idle",
            exc_info=True,
        )

    async def _ensure_stt(self) -> Any:
        if self._stt is not None:
            return self._stt
        async with self._build_lock:
            if self._stt is not None:
                return self._stt
            factory = self._stt_factory
            if factory is not None:
                # An injected factory owns its own construction, so there is
                # nothing here that could pin a language onto it. Say so rather
                # than let a dropped language look like an applied one (AP-30).
                if self._language and not self._factory_language_logged:
                    self._factory_language_logged = True
                    log.debug(
                        "An injected recognizer factory configures itself, so "
                        "the session language %r is not applied",
                        self._language,
                    )
                shared = None
                stt = await asyncio.to_thread(factory)
            else:
                shared = await asyncio.to_thread(_build_default_stt, self._language)
                stt = shared.backend
            self._recognizer_rate = self._resolve_recognizer_rate(stt)
            self._shared = shared
            self._stt = stt
            return self._stt

    def _resolve_recognizer_rate(self, stt: Any) -> int:
        """The rate this recognizer wants its PCM in.

        Derived from the provider when it declares one, so a future 8 kHz or
        48 kHz engine is served correctly without touching this file; 16 kHz
        otherwise, which is what every in-tree provider expects.
        """
        if self._recognizer_rate_override:
            return self._recognizer_rate_override
        for attribute in _RECOGNIZER_RATE_ATTRS:
            try:
                declared = int(getattr(stt, attribute, 0) or 0)
            except (TypeError, ValueError):
                # A provider whose rate attribute is not a number tells us
                # nothing; say which one, because the fallback that follows
                # decides what sample rate the recognizer is fed.
                log.debug(
                    "Ignoring non-numeric %r on the recognizer while resolving "
                    "its native sample rate",
                    attribute,
                    exc_info=True,
                )
                continue
            if declared > 0:
                return declared
        return _RECOGNIZER_RATE_FALLBACK

    def _recognition_timeout_s(self, pcm: bytes, sample_rate: int) -> float:
        seconds = len(pcm) / 2 / max(1, sample_rate)
        return min(
            _RECOGNITION_TIMEOUT_MAX_S,
            _RECOGNITION_TIMEOUT_BASE_S + seconds * _RECOGNITION_TIMEOUT_PER_AUDIO_S,
        )

    def _note_success(self) -> None:
        self._consecutive_failures = 0
        self._consecutive_busy = 0

    async def _note_busy(self, *, in_flight: bool = False) -> None:
        """A skipped call is not proof the engine is broken — a streak is.

        ``in_flight`` marks the skip THIS transcriber's own guard produced, i.e.
        "the engine is inside a recognition right now". On a shared engine that
        is usually another session's healthy call, so such a skip only counts
        toward a rebuild once the busy span outlives the holder's own bound. A
        provider reporting its own engine busy while our guard is clear needs no
        such proof: nobody legitimate is inside it.
        """
        if in_flight:
            span, wedged = self._busy_wedge_state()
            if not wedged:
                log.debug(
                    "Local input transcription skipped a segment: the shared "
                    "recognizer has been serving another holder for %.1f s, "
                    "which is normal sharing rather than a wedged engine",
                    span,
                )
                return
        self._consecutive_busy += 1
        if self._consecutive_busy >= _RECOVER_AFTER_BUSY:
            await self._recover_recognizer(
                f"{self._consecutive_busy} consecutive skipped recognitions"
            )

    async def _note_failure(self, detail: str) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _RECOVER_AFTER_FAILURES:
            await self._recover_recognizer(
                f"{self._consecutive_failures} consecutive failures ({detail})"
            )

    async def _recover_recognizer(self, reason: str) -> None:
        """Rebuild a wedged engine instead of re-polling it forever (AP-24).

        A timed-out recognition leaves a worker thread inside the native engine
        that nothing can cancel, so the engine stays occupied and every later
        utterance fails the same way — the "works once, then deaf for the rest
        of the call" signature. Providers that own such an engine expose
        ``recover()``; the reference is dropped either way, so the next
        utterance constructs a fresh one.

        Dropping the reference is safe for everyone. ``recover()`` is not: it
        tears the model down under whoever is inside it, so it is withheld while
        a holder is still within its own bound — this transcriber cannot see the
        other session, only the guard it left behind.
        """
        stt = self._stt
        shared = self._shared
        in_flight = self._recognition_busy
        busy_span, wedged = self._busy_wedge_state()
        if shared is not None:
            # Dropping only this reference would leave the wedged engine in the
            # process-wide cache, and the next session would be handed it as if
            # it were healthy — the "deaf for the rest of the call" signature,
            # now inherited across calls.
            _forget_shared_recognizer(shared)
        self._stt = None
        self._shared = None
        self._recognizer_rate = None
        self._consecutive_failures = 0
        self._consecutive_busy = 0
        log.warning(
            "Rebuilding the local input recognizer after %s; the next "
            "utterance loads a fresh engine",
            reason,
        )
        recover = getattr(stt, "recover", None)
        if not callable(recover):
            return
        if in_flight and not wedged:
            log.warning(
                "Not tearing down the local input recognizer after %s: it is "
                "%.1f s into a recognition for another holder and still inside "
                "its own bound. The cache entry was retired, so the next "
                "utterance builds a fresh engine instead.",
                reason,
                busy_span,
            )
            return
        try:
            await asyncio.to_thread(recover)
        except Exception:  # noqa: BLE001 - the dropped reference is the real fix
            log.warning(
                "Local input recognizer recover() failed; continuing with a "
                "freshly constructed engine anyway",
                exc_info=True,
            )

    async def _transcribe(self, pcm: bytes, voiced_ms: int) -> None:
        started = time.monotonic()
        try:
            text = await self.transcribe_audio(pcm, sample_rate=self._sample_rate)
        except RecognizerBusy:
            # The recognizer is still inside an earlier call. Skipping keeps the
            # engine intact (AP-24), but the turn must still learn it got nothing.
            log.warning(
                "Local input transcription skipped a %d ms utterance: the "
                "recognizer is still busy with an earlier segment",
                voiced_ms,
            )
            self._emit(
                InputTranscriptEvent(kind=TRANSCRIPT_FAILED, voiced_ms=voiced_ms)
            )
            return
        except TimeoutError:
            log.warning(
                "Local input transcription timed out on a %d ms utterance; "
                "the recognizer is treated as wedged",
                voiced_ms,
            )
            self._emit(
                InputTranscriptEvent(kind=TRANSCRIPT_FAILED, voiced_ms=voiced_ms)
            )
            return
        except Exception:  # noqa: BLE001 - one failed utterance is not fatal
            if self._stt is None:
                self._log_unavailable()
            else:
                log.warning(
                    "Local input transcription failed for one utterance",
                    exc_info=True,
                )
            self._emit(
                InputTranscriptEvent(kind=TRANSCRIPT_FAILED, voiced_ms=voiced_ms)
            )
            return
        if not text:
            # Real speech that produced no words: the far end's own transcript
            # of the same audio is now the best thing Jarvis has.
            self._emit(
                InputTranscriptEvent(kind=TRANSCRIPT_FAILED, voiced_ms=voiced_ms)
            )
            return
        if is_silence_hallucination(text, voiced_ms / 1000.0):
            # Whisper-family boilerplate over near-silence, whole-utterance and
            # inside the short window (the shared dictation verdict — never a
            # keyword ban on real speech). Live 2026-08-06 17:39:57 the
            # recognizer delivered a subtitle outro as a genuine turn: it
            # grounded a response the user never asked for AND flipped the
            # call's output language on its way through. This closes as a
            # discard rather than a recognizer failure: the far end's caption
            # can still stand in, but a speaker leak must not raise a visible
            # "transcription unavailable" error when none arrives.
            log.info(
                "Discarding a %d ms local input transcript as silence "
                "boilerplate: %r",
                voiced_ms,
                text[:80],
            )
            self._emit(
                InputTranscriptEvent(kind=SPEECH_DISCARDED, voiced_ms=voiced_ms)
            )
            return
        log.info(
            "realtime local input transcript (%.0f ms): %r",
            (time.monotonic() - started) * 1000,
            text[:120],
        )
        self._emit(
            InputTranscriptEvent(
                kind=TRANSCRIPT, text=text, is_final=True, voiced_ms=voiced_ms
            )
        )

    async def transcribe_audio(self, pcm: bytes, *, sample_rate: int) -> str:
        """Recognize one bounded PCM segment without emitting input events.

        The Codex subscription transport uses this only when ChatGPT-Live
        supplied assistant audio but omitted its matching transcript. The
        recovered text still passes through the ordinary output scrub gate;
        this method merely supplies the missing evidence.

        Raises ``RecognizerBusy`` when the recognizer is already in use, and
        ``TimeoutError`` when a call outlives a bound scaled to the audio handed
        in. Both are deliberate: waiting behind a hung native engine is what
        turned one bad recovery into a permanently deaf call (AP-24).
        """
        if sample_rate != self._sample_rate:
            raise ValueError(
                "Local transcription received audio at an unexpected sample rate"
            )
        if not pcm:
            return ""

        # Build BEFORE taking the inference guard: a model load is not an
        # inference, and holding the guard across it blocks the input path for
        # the entire load.
        stt = await self._ensure_stt()
        target_rate = self._recognizer_rate or _RECOGNIZER_RATE_FALLBACK
        payload = self._to_recognizer_rate(pcm, target_rate)
        if not payload:
            return ""

        if self._recognition_busy:
            await self._note_busy(in_flight=True)
            raise RecognizerBusy(
                "the local recognizer is already transcribing another segment"
            )

        from jarvis.core.protocols import AudioChunk  # noqa: PLC0415

        async def chunks():  # noqa: ANN202 - one-shot adapter
            yield AudioChunk(
                pcm=payload,
                sample_rate=target_rate,
                timestamp_ns=0,
                channels=1,
            )

        # Health accounting lives HERE, at the one choke point both callers pass
        # through — the streaming utterance path and the transport's
        # output-transcript recovery. Counting it only in the caller above left
        # a wedge first hit by recovery invisible to the leash, so the self-heal
        # needed four lost utterances instead of two.
        #
        # The guard is taken WITH this call's own timeout, so a second session
        # that finds the engine busy can tell "still inside its bound" from
        # "long past every bound it had", instead of reading the flag alone.
        bound_s = self._recognition_timeout_s(payload, target_rate)
        self._set_recognition_busy(True, bound_s=bound_s)
        try:
            result = await asyncio.wait_for(stt.transcribe(chunks()), timeout=bound_s)
        except TimeoutError:
            self._recognition_busy = False
            await self._note_failure("timeout")
            raise
        except Exception as exc:  # noqa: BLE001 - classified, counted, re-raised
            self._recognition_busy = False
            # A provider reporting its OWN engine as busy (the non-blocking
            # in-flight guard the local Whisper provider raises) is the same
            # "slow but alive" signal as our own skip, not an independent
            # failure. Recognized by shape, never by importing the provider —
            # the realtime path must stay importable without the local-voice
            # extra installed.
            if type(exc).__name__ == "TranscribeBusy":
                await self._note_busy()
            else:
                await self._note_failure(type(exc).__name__)
            raise
        except BaseException:
            # Cancellation is the CALLER giving up, not the engine failing. It
            # does abandon the worker thread, but the next call's busy report is
            # what proves that — counting it here would double-count one wedge.
            self._recognition_busy = False
            raise
        self._recognition_busy = False
        self._note_success()
        return str(getattr(result, "text", "") or "").strip()

    def _to_recognizer_rate(self, pcm: bytes, target_rate: int) -> bytes:
        """Convert captured PCM to the rate the recognizer actually accepts.

        The realtime capture rate is the PROVIDER's (ChatGPT-Live runs at
        24 kHz) while recognizers pin their own: the local Whisper provider
        raises ``ValueError("Expected 16 kHz, ...")`` on anything else and the
        local Nemotron decoder mis-reads it without any error at all, so passing
        the capture rate through means every local-STT user gets zero
        transcripts on every turn. Converting here keeps that a detail of this
        bridge instead of a constraint on the transport.
        """
        if target_rate == self._sample_rate:
            return pcm
        from jarvis.realtime.audio import StreamingPcm16Resampler  # noqa: PLC0415

        # One-shot: a fresh resampler per segment, because segments are not
        # contiguous audio and carrying a tail across them would splice the end
        # of one utterance onto the start of the next.
        return StreamingPcm16Resampler(self._sample_rate, target_rate).process(pcm)

    def _emit(self, event: InputTranscriptEvent) -> None:
        if len(self._events) >= _EVENT_QUEUE_MAX:
            self._evict_one()
        self._events.append(event)
        self._event_ready.set()

    def _evict_one(self) -> None:
        """Make room without ever discarding grounding evidence.

        A dropped ``speech_started`` does not merely lose a bar update: the
        adapter counts it as the proof that a human spoke, so losing one makes
        the next genuine answer look like a self-echo and get interrupted.
        The matching ``speech_discarded`` edge is equally load-bearing: losing
        it leaves the adapter believing that utterance is still open. Ordinary
        transcript events are the droppable ones.
        """
        for index, queued in enumerate(self._events):
            if queued is not None and queued.kind not in {
                SPEECH_STARTED,
                SPEECH_DISCARDED,
            }:
                del self._events[index]
                log.warning(
                    "Local input transcript queue is full; dropped one %s "
                    "event to keep the speech boundaries intact",
                    queued.kind,
                )
                return
        dropped = self._events.popleft()
        log.warning(
            "Local input transcript queue is full of unread speech boundaries; "
            "dropping the oldest (%s). The event consumer is not draining.",
            getattr(dropped, "kind", "end-of-stream"),
        )

    # -- consuming -----------------------------------------------------
    async def next_event(self) -> InputTranscriptEvent | None:
        while not self._events:
            self._event_ready.clear()
            await self._event_ready.wait()
        event = self._events.popleft()
        if not self._events:
            self._event_ready.clear()
        return event

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in tuple(self._tasks):
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        self._events.append(None)
        self._event_ready.set()


__all__ = [
    "SPEECH_DISCARDED",
    "SPEECH_STARTED",
    "TRANSCRIPT",
    "TRANSCRIPT_FAILED",
    "InputTranscriptEvent",
    "LocalInputTranscriber",
    "RecognizerBusy",
]
