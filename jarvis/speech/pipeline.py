"""Speech-Pipeline mit Call/Hangup-State-Machine + Parallel-Wake-Detection.

Wake-Detection läuft im IDLE-State über ZWEI parallele Pfade auf demselben
Mic-Stream (Fanout):
  1. openWakeWord — schnell (15-30 ms Latenz), fragil bei deutscher Aussprache
  2. Whisper-Wake — robust (800-1200 ms Latenz), versteht Deutsch nativ

Acknowledgment-Feedback:
  - Sofort beim Wake/Call: kurzer Chime (180 ms, in-memory generiert)
  - Dann pre-renderter "Ja?"-Ton (beim Startup einmal via Gemini-TTS)
  - Dann ACTIVE-State

Hotkeys (call / hangup) sind parallel zum Wake IMMER aktiv — global-hotkeys
registriert Windows-weite Low-Level-Hooks.
"""
from __future__ import annotations

import asyncio
import enum
import hashlib
import logging
import os
import random
import re
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import numpy as np

from jarvis.audio import mic_level
from jarvis.audio.capture import (
    REALTIME_QUEUE_CHUNKS,
    MicrophoneCapture,
    pcm_bytes_to_np,
)
from jarvis.audio.chime import CHIME_PCM, CHIME_SAMPLE_RATE, DISCONNECT_PCM, READY_PCM
from jarvis.audio.device_init import wait_for_stable_audio_devices
from jarvis.audio.player import AudioPlayer
from jarvis.audio.vad import VAD_FRAME_SAMPLES, SileroEndpointer
from jarvis.audio.vad_reasons import FORCED_CUT_REASONS
from jarvis.brain.output_filter import scrub_for_voice
from jarvis.core.events import (
    CU_PROGRESS_EVENTS,
    ActionPlanned,
    AnnouncementRequested,
    AudioOutFirst,
    BrainTTFT,
    DictationTranscript,
    LatencyTurnComplete,
    ListeningStarted,
    ObservationCaptured,
    JarvisAgentAnnouncement,
    JarvisAgentBackgroundCompleted,
    SpeechSpoken,
    TranscriptFinal,
    TranscriptionUpdate,
    UtteranceCaptured,
    VoiceBootStatus,
    VoiceMuteChanged,
    VoiceMuteToggleRequested,
    VoiceSessionEnded,
    VoiceSessionStarted,
    WakeCandidateDetected,
    WakeWordDetected,
)
from jarvis.core.protocols import AudioChunk, Transcript
from jarvis.core.turn_language import (
    DEFAULT_LOCALE,
    normalize_language_tag,
    resolve_output_language,
)
from jarvis.plugins.stt.fwhisper import FasterWhisperProvider
from jarvis.plugins.tts.gemini_flash_tts import GEMINI_TTS_SAMPLE_RATE, GeminiFlashTTS
from jarvis.plugins.wake.openwakeword_provider import (
    PRODUCTION_WAKE_THRESHOLD,
    OpenWakeWordProvider,
)
from jarvis.sessions.constants import (
    HANGUP_ERROR,
    HANGUP_HOTKEY,
    HANGUP_IDLE_TIMEOUT,
    HANGUP_SHUTDOWN,
    HANGUP_TURN_COMPLETE,
    HANGUP_VOICE_PATTERN,
    SPOKEN_KIND_ACTION_DONE,
    SPOKEN_KIND_ANNOUNCEMENT,
    SPOKEN_KIND_BACKCHANNEL,
    SPOKEN_KIND_CLARIFY,
    SPOKEN_KIND_COMPLETION,
    SPOKEN_KIND_PREAMBLE,
    SPOKEN_KIND_PRIVACY,
    SPOKEN_KIND_PROGRESS,
    SPOKEN_KIND_STT_UNAVAILABLE,
    SPOKEN_KIND_SUBAGENT,
    SPOKEN_KIND_TIMEOUT,
    SPOKEN_KIND_UNAVAILABLE,
)
from jarvis.skills.schema import SkillDirectTriggered
from jarvis.skills.skill_context import try_get_skill_context
from jarvis.skills.trigger_matcher import TriggerMatcher
from jarvis.speech.completeness import (
    Completeness,
    classify_completeness,
)
from jarvis.speech.completion import (
    REASON_TRAILING_ELLIPSIS,
    is_cancel,
    is_incomplete,
)
from jarvis.speech.continuation_buffer import ContinuationBuffer
from jarvis.speech.continuation_window import ContinuationWindow
from jarvis.speech.hangup import (
    HANGUP_RE,
    contains_end_signal,
    is_legacy_farewell,
)
from jarvis.speech.pending_buffer import PendingPromptBuffer
from jarvis.speech.persona import PhrasePicker, iter_all_start_ack
from jarvis.speech.rolling_whisper_wake import RollingWhisperWake
from jarvis.speech.wake_verifier import (
    CUSTOM_WAKE_MIN_RMS,
    pcm_tail_rms,
    verify_wake_with_stt,
)
from jarvis.telemetry.latency import LatencyPhase, LatencyTracker
from jarvis.trigger.hotkey import HotkeyTrigger

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus
    from jarvis.state.supervisor import Supervisor


log = logging.getLogger("jarvis.speech.pipeline")


async def _gather_timed(
    named_thunks: list[tuple[str, Callable[[], Awaitable[Any]]]],
) -> tuple[dict[str, float], list[Any]]:
    """Run named async thunks concurrently and time each one individually.

    Returns ``(timings_ms, results)`` where ``timings_ms[name]`` is the
    per-thunk wall-clock in milliseconds (recorded even if the thunk raises) and
    ``results`` mirrors ``asyncio.gather(..., return_exceptions=True)`` order:
    each entry is the thunk's return value or its captured exception. Used to
    expose which Phase-A loader dominates warm-up (the gather otherwise hides
    per-loader cost behind its slowest member).
    """
    timings: dict[str, float] = {}

    async def _run(name: str, thunk: Callable[[], Awaitable[Any]]) -> Any:
        t0 = time.monotonic()
        try:
            return await thunk()
        finally:
            timings[name] = (time.monotonic() - t0) * 1000.0

    results = await asyncio.gather(
        *(_run(name, thunk) for name, thunk in named_thunks),
        return_exceptions=True,
    )
    return timings, list(results)


# Long-dictation accumulation guardrails. When the VAD force-cuts a long
# continuous utterance (reason in FORCED_CUT_REASONS), the pipeline buffers
# the PCM fragments and only finalizes at a natural endpoint. These caps stop
# a stuck mic / endless speaker-bleed from accumulating forever.
_MAX_CARRY_SECONDS = 60.0
_MAX_CARRY_PCM_BYTES = 16_000 * 2 * 60  # 16 kHz * int16 * 60 s ≈ 1.9 MB

# Grace before the thinking-phase continuation-interrupt monitor may fire. Much
# shorter than the playback barge grace (1.5 s) because during pure thinking
# there is no TTS playing, so speaker->mic echo is not a concern.
_CONTINUATION_THINKING_GRACE_S: float = 0.3

# How long to wait for a cancelled brain turn to unwind before ABANDONING it.
# A brain stream blocked on an inline action that ignores asyncio cancellation
# (a long ``computer_use`` step stops only via its own ``cancel_active_cu``
# token) would otherwise never finish, and an unbounded ``await task`` would
# freeze the whole voice session (live bug 2026-06-19). After this grace the
# task is left to unwind on its own so control always returns to the loop.
_BRAIN_CANCEL_GRACE_S: float = 2.0

# Delegation-composition patience (live 2026-06-16). Forensic: "Could you please
# start a sub-agent mission which gives me a complete, complete, complete" was
# submitted on a mid-composition thinking pause — the turn ended at the normal
# 1.5 s silence window (reason=silence, silence_ms=1472), NOT on a probe
# force-cut. The word "sub-agent" is not a trigger; composing a delegation simply
# involves longer pauses than a short command. When the live partial transcript
# shows a delegation being composed, the STT probe extends THIS utterance's
# silence window (``SileroEndpointer.extend_silence_window``) so the pause to
# formulate the task is not mistaken for "done". The marker set mirrors the
# brain's explicit force-spawn triggers; "mission" alone is excluded as too
# broad. High precision: a short command never matches → snappy default kept.
_DELEGATION_SILENCE_MS = 3000
_DELEGATION_COMPOSITION_RE = re.compile(
    r"\b(?:"
    r"sub[\s-]?agent(?:en|s)?(?:[\s-]?mission)?"
    r"|spawn\w*|delegate|delegier\w*|openclaw"
    r")\b",
    re.IGNORECASE,
)


def _looks_like_delegation_composition(partial: str | None) -> bool:
    """True if the live partial transcript shows a delegation being composed."""
    return bool(partial) and _DELEGATION_COMPOSITION_RE.search(partial) is not None


def _should_hold_complete_delegation_for_grace(text: str | None) -> bool:
    """True for complete-looking delegation text that may still receive a follow-up."""
    return _looks_like_delegation_composition(text)


# Minimum word count of the live partial past which we treat the utterance as a
# long dictation (not a short command) and grant it the wider silence window. A
# short command (e.g. "open Chrome", "hang up") never reaches it → stays snappy.
# Lowered 12 → 7 (live bug 2026-06-18, session b34a4bba): the 10-word question
# "Hey Jarvis, was geht ab? Kannst du mir bitte mal …" fell just under the old
# 12-word threshold, got only the base 1.5 s window, and was cut mid-sentence
# when the user paused to think after "mal". 7 still keeps every ordinary 2–6
# word command snappy (no extension) while giving mid-length, still-forming
# questions room to pause. See tests/unit/speech/test_long_composition_patience.py.
_LONG_COMPOSITION_MIN_WORDS = 7


def _looks_like_long_composition(partial: str | None) -> bool:
    """True when the live partial shows an ongoing LONG dictation that deserves a
    wider silence window — vocabulary-independent, so ANY long prompt (not only
    delegations) gets room to pause and think mid-composition. A short command
    (e.g. "open Chrome", "hang up") stays well under the threshold → stays snappy.

    Deliberately a word-count signal only: ``completion.is_incomplete`` is too
    conservative on live partials (it flags a trailing comma/ellipsis but not a
    bare open preposition like "nach"), and a short open-ended tail is already
    re-attached downstream by the continuation-recombine path. Deep dive
    2026-06-16: a long "Agents" / "Agent Team" prompt was chopped at every 1.5 s
    pause because the old trigger matched only delegation keywords.
    """
    if not partial:
        return False
    return len(partial.split()) >= _LONG_COMPOSITION_MIN_WORDS


def _should_extend_silence_for_composition(partial: str | None) -> bool:
    """Single entry point for the adaptive-patience decision: widen the silence
    window when the user is composing a delegation OR any long / open-ended
    utterance, so the system lets a long dictation finish instead of cutting at
    every thinking pause."""
    return _looks_like_delegation_composition(partial) or _looks_like_long_composition(
        partial
    )


BrainCallback = Callable[[str], Awaitable[str]]


# AnnouncementRequested.kind values that deliver an answer the user is owed — a
# finished background mission / sub-agent / worker / OpenClaw result. These
# punch through the hangup gate (AD-OE5/OE6 zero-silent-drop) and cancel any
# pending "still on it" heartbeat. ``subagent`` is the attributed sibling of
# ``completion``: same delivery semantics, but rendered as its own transcript
# track ("Jarvis Sub-Agent / Output").
_READBACK_KINDS: frozenset[str] = frozenset(
    {SPOKEN_KIND_COMPLETION, SPOKEN_KIND_SUBAGENT}
)


def _announcement_spoken_kind(kind: str | None) -> str:
    """Map an ``AnnouncementRequested.kind`` to a ``SpeechSpoken.spoken_kind``.

    AnnouncementRequested carries {``preamble``, ``completion``, ``subagent``,
    ``info``, ``progress``, ``None``}. The first four map 1:1 onto the
    spoken-track vocabulary; ``info`` and the legacy ``None`` default (skill-
    output callers) fall back to the generic ``announcement`` tag.
    """
    if kind in (
        SPOKEN_KIND_PREAMBLE,
        SPOKEN_KIND_COMPLETION,
        SPOKEN_KIND_SUBAGENT,
        SPOKEN_KIND_PROGRESS,
    ):
        return kind
    return SPOKEN_KIND_ANNOUNCEMENT


async def _echo_brain(text: str) -> str:
    return text


# AD-OE6 zero-silent-drop fallback. Spoken (never displayed) when the whole
# brain provider chain is exhausted — the only honest thing to say when there
# is no model left to think with. Kept short, bilingual and TTS-clean: the raw
# provider-chain diagnostic from BrainManager carries URLs and setup jargon and
# is UI-only, so it must not be read aloud. ``_speak`` does not scrub, so these
# phrases reach TTS verbatim. (Runtime TTS strings stay bilingual per the
# voice-output policy; only artifacts must be English.)
_BRAIN_UNAVAILABLE_PHRASE: dict[str, str] = {
    "de": (
        "Entschuldige, Ruben — ich erreiche gerade keines meiner Sprachmodelle. "
        "Bitte prüf kurz, ob bei den Anbietern noch Guthaben ist."
    ),
    "en": (
        "Sorry, Ruben — I can't reach any of my language models right now. "
        "Please check whether your providers still have credit."
    ),
    "es": (
        "Lo siento, Ruben — ahora mismo no puedo acceder a ninguno de mis "
        "modelos de lenguaje. Comprueba si tus proveedores aún tienen crédito."
    ),
}

# AD-OE6 zero-silent-drop fallback for the *final* utterance STT. A cloud STT
# (Groq/OpenAI/Deepgram) can transiently 429 when the in-utterance stability
# probe and this final call briefly exceed the provider's rate window. After
# ``_transcribe_final`` exhausts its retries we say this instead of dropping the
# user into silence (the "Jarvis listens forever, never answers" bug,
# 2026-05-25). Short, bilingual, TTS-clean (``_speak`` does not scrub).
_STT_UNAVAILABLE_PHRASE: dict[str, str] = {
    "de": "Entschuldige, ich habe dich akustisch gerade nicht verstanden. Sag es bitte noch einmal.",
    "en": "Sorry, I didn't catch that just now. Could you say it again?",
    "es": "Perdona, no te he entendido bien ahora mismo. ¿Puedes repetirlo, por favor?",
}

# Honest cross-family fallback when a requested duplex provider cannot open a
# session. The classic pipeline remains available for this voice call so a
# missing key, exhausted balance, unsupported model, or network outage never
# turns the Realtime switch into a silent dead end (AP-22/AP-23).
_REALTIME_UNAVAILABLE_PHRASE: dict[str, str] = {
    "de": (
        "Die Realtime-Verbindung ist gerade nicht verfügbar. "
        "Ich wechsle für diese Sitzung zur klassischen Sprachverarbeitung."
    ),
    "en": (
        "The realtime connection is unavailable right now. "
        "I am switching this session to the classic voice pipeline."
    ),
    "es": (
        "La conexión en tiempo real no está disponible ahora mismo. "
        "Cambiaré esta sesión al sistema de voz clásico."
    ),
}

# AD-OE6 zero-silent-drop fallback for a brain TURN that times out. Live bug
# 2026-05-29: "kannst du Claude Code öffnen" stalled the Gemini stream; the
# brain-timeout path returned to LISTENING in SILENCE (and idle_timeout
# pre-empted brain_timeout, so the turn just hung up with no feedback).
#
# Honest, cause-aware messaging (live complaint 2026-06-30). The old single
# phrase ("Sorry, I couldn't finish the answer in time.") explained NOTHING:
# a slow MCP/plugin tool hung ~35 s, the turn timed out, and Jarvis apologised
# for "taking too long" with no reason. Honesty over guessing (CLAUDE.md §1.4):
#   • TOOL-STALL — the turn was beheaded mid-tool-loop (no first audio frame,
#     i.e. the assistant was blocked waiting on a tool/stage that never
#     returned) OR a desktop (computer_use) tool was demonstrably active when
#     the stall fired: name that honest cause. The concrete tool NAME lives in
#     the brain's tool-use loop (jarvis/brain/manager.py), not reachable from
#     here without coupling, so we name the generic-but-true cause.
#   • NO-ANSWER — a bare provider stall / total cap with no tool evidence:
#     honestly admit we could not find it out, never the vague "took too long".
# Both are short, TTS-clean (``_speak`` does not scrub — no em-dash, two short
# sentences), and carry all supported locales (de/en/es). String-only: NO LLM
# call in this timeout/scrub path (AP-11). Resolved through the ONE output-
# language decision via ``_resolve_timeout_phrase`` below (CLAUDE.md §1 — no
# per-layer language re-derivation).
_TIMEOUT_TOOL_STALL_PHRASE: dict[str, str] = {
    "de": "Ich habe rechtzeitig keine Antwort bekommen. Ein Tool, auf das ich gewartet habe, hat nicht reagiert.",
    "en": "I couldn't get an answer in time. A tool I was waiting on didn't respond.",
    "es": "No pude obtener una respuesta a tiempo. Una herramienta que esperaba no respondió.",
}

_TIMEOUT_NO_ANSWER_PHRASE: dict[str, str] = {
    "de": "Das konnte ich gerade nicht herausfinden.",
    "en": "I couldn't find that out just now.",
    "es": "No pude averiguar eso ahora mismo.",
}

# AD-OE6 zero-silent-drop fallback for an ABANDONED incomplete utterance. When
# the user trails off on a dangling fragment ("…erinnere mich daran, dass" +
# silence) and never continues, the ContinuationBuffer would hold it silently
# forever (its timeout is lazy — it only drops on the *next* utterance). Instead
# of leaving the user in silence ("Jarvis hört für immer zu", 2026-06-08) we ask
# a short clarifying question. Fires only AFTER the grace window expires with no
# continuation, so a real thinking-pause-then-continue is never interrupted.
# Short, bilingual, TTS-clean (``_speak`` does not scrub).
_CLARIFY_QUESTION_PHRASE: dict[str, str] = {
    "de": "Wie meinst du das genau?",
    "en": "What do you mean exactly?",
    "es": "¿Qué quieres decir exactamente?",
}

# AD-OE6 confirmation for a SUCCESSFUL wordless desktop-action turn. When the
# router brain runs a desktop-action tool (computer_use / open_app / click / …)
# and the CU loop does the work but the brain emits no narration text, the turn
# is NOT empty/confused — the action LANDED. Live bug 2026-06-09
# (data/jarvis_desktop.log 16:27): computer_use opened Chrome, then the silent-
# turn handler spoke the clarifying question "Wie meinst du das genau?", so a
# success looked like incomprehension ("er checkt das nicht"). We instead speak
# a short confirmation. Substantive (not a forbidden filler — "Erledigt." is the
# canonical butler confirmation, see output_filter), bilingual, TTS-clean
# (``_speak`` does not scrub).
_ACTION_DONE_PHRASE: dict[str, str] = {
    "de": "Erledigt.",
    "en": "Done.",
    "es": "Listo.",
}


_PHRASE_LANGS: frozenset[str] = frozenset({"de", "en", "es"})


def _phrase_lang(lang: str | None) -> str:
    """Normalize a detected-language tag to a canned-phrase key ("de"/"en"/"es").

    The utterance language reaches the phrase pickers in two shapes: full
    language NAMES from the STT transcript (``(transcript.language or
    "en").lower()`` → ``"german"``/``"spanish"`` for Groq Whisper) and
    BCP-47-ish CODES ("de", "de-DE", "es-ES") from config pins / announcements.
    Both collapse through the canonical ``normalize_language_tag`` so every
    supported language (de/en/es) selects its own phrase set; anything
    unrecognised falls back to ``DEFAULT_LOCALE``. The pickers used to test
    ``lang.startswith("de")`` only — ``"german"`` does not start with "de", so
    every canned AD-OE6 fallback (clarify question, action-done ack,
    brain-timeout, brain/STT-unavailable, smalltalk fallback) was spoken in
    ENGLISH to a German speaker, and the German variants were dead code (live
    bug 2026-06-09: "antwortet fast immer mit einer englischen
    Standardphrase"); a Spanish speaker hit the same trap until this normalizer
    learned ``es`` (Runtime Output Language doctrine). The canned tables now
    carry all three languages.
    """
    code = normalize_language_tag(lang)
    return code if code in _PHRASE_LANGS else DEFAULT_LOCALE


# Timeout sites that PROVE the assistant was blocked waiting on a downstream
# tool/stage when the turn timed out: a no-first-frame beheading means the brain
# produced no first audio frame because it was still inside a tool call that
# never returned. Such a site always speaks the honest TOOL-STALL phrase. The
# stream-stall / total-cap sites only do so when a desktop tool was demonstrably
# active (``_long_tool_last_activity``); otherwise they carry no tool evidence
# and admit the honest NO-ANSWER outcome instead.
_TIMEOUT_TOOL_STALL_SITES: frozenset[str] = frozenset({"empty_after_no_first_frame"})


def _resolve_timeout_phrase(site: str, lang: str, *, tool_active: bool) -> str:
    """Pick the honest, cause-aware timeout phrase for ``site``.

    Resolves language through the ONE shared decision (``_phrase_lang``) — never
    a per-layer re-derivation (CLAUDE.md §1). String-only, no LLM call (AP-11).
    Names a tool cause when the turn was beheaded mid-tool-loop (no first frame)
    or a desktop tool was active; otherwise honestly admits no answer was found.
    """
    key = _phrase_lang(lang)
    if site in _TIMEOUT_TOOL_STALL_SITES or tool_active:
        return _TIMEOUT_TOOL_STALL_PHRASE[key]
    return _TIMEOUT_NO_ANSWER_PHRASE[key]


# Transient STT failures worth a retry: cloud rate-limit (429) and transient
# gateway/server errors (5xx). Anything else (401 bad key, 400 bad audio) is a
# hard error and must NOT be retried — retrying only hammers the provider.
_STT_TRANSIENT_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})
# Total final-transcription attempts = 1 + _STT_FINAL_RETRIES. The in-utterance
# probe stops the instant the VAD endpoint fires, so the shared rate window
# frees within ~1 s — two retries with capped backoff almost always recover.
_STT_FINAL_RETRIES: int = 2
_STT_RETRY_BASE_S: float = 0.4
_STT_RETRY_CAP_S: float = 2.0

# Hard ceiling on a single TTS playback. PortAudio's blocking ``stream.write``
# waits for output-buffer room, and a flaky output device can make it (or a
# stalled TTS chunk generator) block forever — observed live as a 10 s
# OutputStream stall plus ``Invalid sample rate -9997`` retries. Without a
# bound, ``_speak`` never returns, which wedges ``_handle_utterance`` ->
# ``_active_session`` so the ``_state_loop`` finally that resets
# ``self._state`` to IDLE never runs and the wake loop stops re-arming
# ("Hey Jarvis" goes permanently deaf until restart). AD-OE6 — recover, never
# silently hang.
#
# 2026-06-08 (Wave-1 latency fix): the old ceiling was 120 s — the live root
# cause of the 60-156 s voice-hangs on "open app" turns (a wedged output device
# left ``stream.write`` blocked and this ceiling was the ONLY escape).
#
# 2026-06-08 (watchdog redesign): the ceiling now bounds ONLY the no-first-frame
# window — a TTS provider that never yields any audio. It no longer caps total
# playback, so a legitimately long spoken answer is never truncated mid-speech;
# an ACTIVE playback is governed solely by the mid-playback no-progress stall
# below. This pairs with ``AudioPlayer.play_chunks`` resetting ``last_write_ns``
# per playback, so the watchdog's "no first frame yet" (<=0) guard works on
# EVERY turn — not just the first — instead of reading a stale cross-turn
# timestamp and falsely aborting a fresh, still-synthesizing answer.
_TTS_PLAYBACK_CEILING_S: float = 20.0
# Mid-playback no-audio-frame gap that means the output device is wedged (a
# healthy ~60 ms sub-block write returns far inside this). Trips the watchdog →
# ``player.abort_active()`` → turn unwinds → session re-arms.
_TTS_PLAYBACK_STALL_S: float = 5.0
# The no-first-frame ceiling beheads an empty turn at _TTS_PLAYBACK_CEILING_S
# (20 s) — NOT at the brain stall window (30 s). So the floor below which that
# path's spoken "took too long" notice is suppressed (a stale-state guard) must
# be derived from THAT ceiling, never the brain stall window: a real abort fires
# at ~ceiling (clears the floor), a spurious sub-second stale fire is far below
# this fraction (stays suppressed). Live bug 2026-06-14 16:17 — a 30 s floor
# swallowed a real 20.83 s abort, so every research turn the deep brain couldn't
# start answering within 20 s ended in guaranteed silence.
_NO_FIRST_FRAME_FLOOR_FRACTION: float = 0.5
# Async timeout callbacks can arrive a few milliseconds shy of the configured
# wall-clock floor, especially in accelerated unit tests. Treat near-floor
# elapsed times as legitimate timeouts, not stale state.
_TIMEOUT_FLOOR_EPSILON_S: float = 0.05


def _playback_progress_stalled(last_write_ns: int, stall_s: float) -> bool:
    """True when audio frames stopped reaching PortAudio for ``stall_s``.

    ``last_write_ns == 0`` (no frame produced yet) is deliberately NOT a stall:
    the first-token / producer window is owned by the brain stall guard, so a
    slow first token must not be misread here as a device wedge. Only a
    *mid-playback* gap trips this. Cross-platform — pure monotonic-clock math.
    """
    if last_write_ns <= 0:
        return False
    return (time.monotonic_ns() - last_write_ns) >= int(stall_s * 1e9)


def _stt_error_status(exc: BaseException) -> int | None:
    """HTTP status of an STT error, duck-typed so the plugin stays un-imported.

    ``httpx.HTTPStatusError`` (raised by the cloud STT plugins) carries the
    response on ``.response.status_code``; any provider that mirrors that shape
    is understood. Returns ``None`` for non-HTTP errors.
    """
    return getattr(getattr(exc, "response", None), "status_code", None)


def _is_transient_stt_error(exc: BaseException) -> bool:
    """True when an STT error is a recoverable rate-limit / gateway blip."""
    return _stt_error_status(exc) in _STT_TRANSIENT_STATUS


def _stt_retry_delay(exc: BaseException | None, attempt: int) -> float:
    """Backoff before the next final-STT attempt.

    Honours a server ``Retry-After`` header when present (seconds form),
    otherwise capped exponential backoff. Always within ``[0, cap]``.
    """
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if headers is not None and hasattr(headers, "get"):
        raw = headers.get("retry-after")
        if raw:
            try:
                return min(_STT_RETRY_CAP_S, max(0.0, float(raw)))
            except (TypeError, ValueError):
                pass
    return min(_STT_RETRY_CAP_S, _STT_RETRY_BASE_S * (2 ** attempt))


class PipelineState(enum.Enum):
    IDLE = "idle"
    ACTIVE = "active"


class TurnTakingState(enum.Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    USER_SPEAKING = "USER_SPEAKING"
    WAITING_FOR_FINAL_TRANSCRIPT = "WAITING_FOR_FINAL_TRANSCRIPT"
    PROCESSING = "PROCESSING"
    # Final transcript classified as syntactically open-ended (incomplete-prompt
    # completion buffer pending). Pipeline stays silent, mic open, until a
    # continuation arrives or the per-gap timeout flushes the fragment to the
    # brain (AD-OE6 — zero silent drops). See
    # docs/superpowers/specs/2026-05-25-incomplete-prompt-completion-design.md.
    WAITING_FOR_COMPLETION = "WAITING_FOR_COMPLETION"
    JARVIS_SPEAKING = "JARVIS_SPEAKING"


# Turn-states in which the user holds the floor: the mic is open and they are
# speaking or their words are still being finalised. While in any of these, an
# asynchronous non-interrupt announcement (ack preamble, mission/background
# readback, workflow completion) must NOT barge — AD-OE5 "speak ONLY at the next
# turn-boundary". A preamble is then dropped (ephemeral); a completion is
# deferred and flushed when the floor clears (AD-OE6 zero-silent-drop).
_USER_HOLDS_FLOOR_STATES: frozenset[TurnTakingState] = frozenset({
    TurnTakingState.USER_SPEAKING,
    TurnTakingState.WAITING_FOR_FINAL_TRANSCRIPT,
    TurnTakingState.WAITING_FOR_COMPLETION,
})

# Floor states for the continuation DRAIN (a strict subset of the announcement
# floor set above). The drain must defer ONLY while the user is ACTIVELY
# speaking the continuation (USER_SPEAKING) or it is still being transcribed
# (WAITING_FOR_FINAL_TRANSCRIPT). It must NOT defer on WAITING_FOR_COMPLETION:
# that is precisely the "a fragment is held and no continuation has arrived"
# state the drain exists to resolve — deferring on it would let the held
# fragment rot until the idle-timeout (the very "Jarvis hört für immer zu" wedge
# this fix closes). Unlike the clarify question (which speaks TTS and must never
# talk over a half-finalised turn), the drain only dispatches silently to the
# brain, so acting in WAITING_FOR_COMPLETION is correct.
_DRAIN_HOLDS_FLOOR: frozenset[TurnTakingState] = frozenset({
    TurnTakingState.USER_SPEAKING,
    TurnTakingState.WAITING_FOR_FINAL_TRANSCRIPT,
})


# Hang-up patterns + the END_CALL sentinel live in jarvis/speech/hangup.py
# (shared, stdlib-only, also imported by jarvis/telephony/session.py). HANGUP_RE,
# contains_end_signal and is_legacy_farewell are imported at the top of this module.

# Latenz-Sprint-1: Satzgrenzen-Splitter fuer den Streaming-TTS-Pfad.
# Matched whitespace/newline DIREKT NACH einem Satzendezeichen — also den
# Uebergang von Satz n nach Satz n+1. Final-Flush am Stream-Ende uebernimmt
# das letzte Fragment ohne folgendes Whitespace.
_STREAM_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|(?<=[.!?…])\n+")

# Wake-Only-Filter: reine Wake-Word-Utterances ohne Follow-Up werden NICHT
# ans Brain geschickt. Sonst halluziniert das LLM ein "Ja?" / "Sir?" /
# "Hallo" — doppelt zu dem bereits abgespielten ACK und nervig.
# Matched: "Jarvis", "Jarvis.", "Hey Jarvis!", "Ok Jarvis", "Hi Jarvis",
# auch Whisper-Verhoerer wie "Jervis", "Jarvi", "Yarvis".
WAKE_ONLY_RE = re.compile(
    r"^\s*("
    r"(hey|ok|okay|hi|hallo|ey|ja|yo)\s+"
    r")?"
    r"j[aeä]rv[iy]s?"
    r"[.!?,\s]*$",
    re.IGNORECASE,
)

# STT hallucination markers (YouTube end cards, ad outros, copyright strings
# Whisper emits on an empty mic / speaker leak). Blocked before the brain
# call. Single definition lives in wake_constants (the rolling wake's
# bias-echo confirm consumes the same list — BUG-008 drift rule).
from jarvis.speech.wake_constants import (  # noqa: E402
    STT_HALLUCINATION_RE as _STT_HALLUCINATION_RE,
)

# Paraphrase-Prefixes die Gemini/Claude bei Unsicherheit voranstellen.
# Werden als Post-Processing vor dem TTS abgeschnitten.
_PARAPHRASE_PREFIXES: tuple[str, ...] = (
    "ich verstehe, du moechtest", "ich verstehe du moechtest",
    "ich verstehe, du möchtest", "ich verstehe du möchtest",
    "ich verstehe, dass du", "ich verstehe dass du",
    "ich verstehe, du willst", "ich verstehe du willst",
    "du willst also", "du moechtest also", "du möchtest also",
    "wenn ich dich richtig verstehe",
    "okay, ich habe verstanden", "okay ich habe verstanden",
    "alles klar, du", "alles klar du",
    "verstanden. ich werde", "verstanden, ich werde",
    "verstanden — du moechtest", "verstanden — du möchtest",
    "i understand you want", "you want me to",
    "if i understand correctly", "got it, you want",
    "ja, ich verstehe", "ja ich verstehe",
)

_NON_SUBSTANTIVE_RESPONSE_RE = re.compile(
    r"^\s*("
    r"ja,?\s+ich\s+verstehe\.?|"
    r"ich\s+verstehe\.?|"
    r"verstanden\.?|"
    r"ich\s+bin\s+einsatzbereit\.?|"
    r"okay\.?|"
    r"alles\s+klar\.?|"
    r"kuemmere\s+mich\s+drum,?\s+sir\.?|"
    r"kümmer(?:e)?\s+mich\s+drum,?\s+sir\.?|"
    r"erledigt,?\s+sir\.?(\s+fertig\.?\s*\d+\s+von\s+\d+\s+schritten\s+erfolgreich\.?)?|"
    r"fertig\.?\s*(\d+\s+von\s+\d+\s+schritten\s+erfolgreich\.?)?"
    r")\s*$",
    re.IGNORECASE,
)

# Kurzes ACK das beim Wake gesprochen wird. Leer = nur der Chime spielt,
# keine gesprochene Phrase — User-Praeferenz 2026-04-24: die JARVIS-
# Persona-Phrasen ("Sir?", "Sofort.", "Mach ich.") klingen peinlich, raus.
ACK_PHRASE = ""


def _is_wake_only(text: str) -> bool:
    """True wenn die Utterance nur aus Wake-Word besteht (kein Command).

    Zweite Bedingung: weniger als 3 "meaningful chars" (alles ausser
    Whitespace/Punctuation). Verhindert dass auch sehr kurze Noise-
    Transkripte wie ".", "uh", "mhm" einen Brain-Call ausloesen.
    """
    if WAKE_ONLY_RE.match(text):
        return True
    meaningful = re.sub(r"[^\wäöüÄÖÜß]+", "", text)
    return len(meaningful) < 3


def _strip_paraphrase_prefix(response: str) -> str:
    """Schneidet Paraphrase-Prefixes ab falls das Modell welche produziert.

    Verbessertes Butler-Feeling: statt "Ich verstehe, du moechtest X. Hier
    ist Y." hoert der User nur "Hier ist Y." — falls nach Prefix-Cut nichts
    Sinnvolles uebrig ist, wird der Original-Response zurueckgegeben.
    """
    low = response.strip().lower()
    for prefix in _PARAPHRASE_PREFIXES:
        if low.startswith(prefix):
            # Nach dem ersten Satz-Ende abschneiden; der Rest ist i.d.R.
            # die eigentliche Antwort.
            candidates = [
                response.find(sep, len(prefix))
                for sep in (". ", "! ", "? ")
            ]
            candidates = [c for c in candidates if c > 0]
            if candidates:
                cut = min(candidates)
                stripped = response[cut + 2:].strip()
                if stripped:
                    log.info("🧹 Paraphrase-Prefix entfernt: %r → ...",
                             response[:cut + 1])
                    return stripped
            break
    return response


def _is_non_substantive_response(response: str) -> bool:
    """True fuer reine ACK-/Butler-Filler, die nicht gesprochen werden sollen."""
    return bool(_NON_SUBSTANTIVE_RESPONSE_RE.match(response.strip()))


def _smalltalk_fallback_for_non_substantive(prompt: str, lang: str) -> str | None:
    """Return a short answer when a smalltalk prompt produced only filler."""
    low = prompt.strip().lower()
    wellbeing_markers = (
        "wie geht",
        "how are you",
        "how's it going",
    )
    if not any(marker in low for marker in wellbeing_markers):
        return None
    if _phrase_lang(lang) == "de":
        return "Mir geht's gut, Ruben. Was machen wir als Naechstes?"
    return "I'm good, Ruben. What's next?"


_INCOMPLETE_TAIL_RE = re.compile(
    r"\b("
    r"wenn|falls|ob|weil|dass|damit|bevor|nachdem|obwohl|während|waehrend|"
    r"und|oder|aber|sondern|mit|ohne|für|fuer|von|zu|zur|zum|auf|in|im|am|an|"
    r"der|die|das|den|dem|des|ein|eine|einen|einem|einer|"
    r"if|whether|because|that|so|before|after|although|while|and|or|but|with|"
    r"without|for|from|to|into|on|in|at|the|a|an"
    r")\s*$",
    re.IGNORECASE,
)


def _looks_context_incomplete(text: str) -> bool:
    """Heuristic guard for voice turns that clearly need another fragment.

    STT usually removes punctuation, so this intentionally only catches
    obvious dangling constructs. Anything that looks like a complete command or
    question is allowed through to the brain immediately.
    """
    stripped = text.strip()
    if not stripped:
        return True
    if stripped.endswith((".", "!", "?", ":", ";")):
        return stripped.endswith(":")
    words = re.findall(r"[\wäöüÄÖÜß']+", stripped, flags=re.UNICODE)
    if len(words) < 2:
        return True
    if _looks_like_complete_smalltalk(stripped):
        return False
    if _INCOMPLETE_TAIL_RE.search(stripped):
        return True
    low = stripped.lower()
    # Only conjunctions / relative-particle starters count as "clearly
    # dangling" — `kannst du` / `can you` were removed because they
    # produced false positives on complete questions like "Kannst du das
    # fixen", trapping the pipeline in silent LISTENING. The remaining
    # markers are constructions that genuinely cannot stand alone.
    incomplete_starters = (
        "jarvis wenn ",
        "wenn ",
        "falls ",
        "if ",
        "when ",
        "ob du ",
    )
    return any(low == marker.strip() or low.endswith(marker) for marker in incomplete_starters)


def _merge_partial_transcript(current: str, incoming: str) -> str:
    """Merge overlapping STT probe tails into a readable live transcript."""
    current = current.strip()
    incoming = incoming.strip()
    if not current:
        return incoming
    if not incoming:
        return current

    current_words = current.split()
    incoming_words = incoming.split()
    current_norm = _normalized_partial_words(current)
    incoming_norm = _normalized_partial_words(incoming)

    if _is_likely_partial_correction(current_norm, incoming_norm):
        return incoming
    if _is_likely_repeated_tail(current_norm, incoming_norm):
        return current

    max_overlap = min(len(current_words), len(incoming_words))

    for overlap in range(max_overlap, 0, -1):
        if current_norm[-overlap:] == incoming_norm[:overlap]:
            return " ".join([*current_words, *incoming_words[overlap:]])
    if incoming.lower() in current.lower():
        return current
    if current.lower() in incoming.lower():
        return incoming
    return f"{current} {incoming}"


def _normalized_partial_words(text: str) -> list[str]:
    words = re.findall(r"[\w']+", text.lower(), flags=re.UNICODE)
    normalized: list[str] = []
    for word in words:
        word = (
            word.replace("ä", "ae")
            .replace("ö", "oe")
            .replace("ü", "ue")
            .replace("ß", "ss")
        )
        word = word.replace("fuer", "fur")
        if word in {"einen", "einem", "einer"}:
            word = "ein"
        elif word.endswith("s") and len(word) > 5:
            word = word[:-1]
        normalized.append(word)
    return normalized


def _is_likely_partial_correction(
    current_words: list[str],
    incoming_words: list[str],
) -> bool:
    if not current_words or not incoming_words:
        return False
    if incoming_words[: len(current_words)] == current_words:
        return True
    shared_prefix = 0
    for current_word, incoming_word in zip(current_words, incoming_words):
        if current_word != incoming_word:
            break
        shared_prefix += 1
    return shared_prefix >= 2 and len(incoming_words) >= len(current_words)


def _is_likely_repeated_tail(
    current_words: list[str],
    incoming_words: list[str],
) -> bool:
    if len(incoming_words) < 2 or len(incoming_words) > len(current_words):
        return False
    suffix = current_words[-len(incoming_words):]
    matches = sum(
        1 for current_word, incoming_word in zip(suffix, incoming_words)
        if current_word == incoming_word
    )
    return matches >= max(2, len(incoming_words) - 1)


def _looks_like_complete_smalltalk(text: str) -> bool:
    low = text.lower()
    return any(
        marker in low
        for marker in (
            "wie geht",
            "how are you",
            "how's it going",
            "hallo jarvis",
            "hi jarvis",
            "danke jarvis",
            "thank you jarvis",
        )
    )


async def _queue_iter(q: asyncio.Queue) -> AsyncIterator[AudioChunk]:
    """Hilfs-Adapter: Queue → AsyncIterator (für die Wake-Detektoren)."""
    while True:
        chunk = await q.get()
        if chunk is None:  # Poison-Pill
            return
        yield chunk


def _default_tts_for_pipeline(config: Any) -> Any:
    """The default TTS when the caller supplies none — key-aware (AP-22).

    Mirrors the STT default a few lines into ``__init__``: build through the same
    key-aware ``build_tts_from_config`` the real construction paths use, so a
    single-key user's spoken output — INCLUDING the deterministic "couldn't
    understand you" readback — crosses to whatever TTS family the user actually
    has a key for instead of being hard-pinned to a keyless Gemini default that
    goes silently mute (AP-22/AP-6). Degrades to a bare ``GeminiFlashTTS`` only
    when there is no config or the factory itself fails, so voice boot is never
    broken. (In practice every real caller passes a TTS built this way already;
    this closes the latent ``tts=None`` fallback that ignored the user's key.)
    """
    if config is not None and getattr(config, "tts", None) is not None:
        try:
            from jarvis.plugins.tts import build_tts_from_config

            return build_tts_from_config(config.tts)
        except Exception as exc:  # noqa: BLE001 — a TTS build must never break voice boot
            log.warning("TTS factory failed (%s); using the default Gemini voice.", exc)
    return GeminiFlashTTS()


class SpeechPipeline:
    """End-to-End Pipeline mit Call/Hangup-Lifecycle + Parallel-Wake."""

    def __init__(
        self,
        call_hotkeys: tuple[str, ...] = ("ctrl+right_alt+j", "f3+f4"),
        # Push-to-talk hotkeys (held = record, release = submit). Distinct from
        # ``call_hotkeys`` (which are wake-style toggles). When non-empty, these
        # combos fire on BOTH key edges; holding records raw audio and releasing
        # submits it as one prompt (one-shot). Empty (default) = no PTT, every
        # call hotkey stays a toggle. Production wiring fills this from
        # ``cfg.trigger.hotkey`` when ``cfg.trigger.push_to_talk`` is True.
        ptt_hotkeys: tuple[str, ...] = (),
        hangup_hotkeys: tuple[str, ...] = ("f1+f2",),
        # Legacy keyword seed for the no-plan provider. Empty: no wake model
        # ships (design 2026-07-07); only a wake plan arms a real detector.
        wake_keywords: tuple[str, ...] = (),
        wake_threshold: float = 0.10,
        stt: FasterWhisperProvider | None = None,
        tts: GeminiFlashTTS | None = None,
        wake: OpenWakeWordProvider | None = None,
        brain_callback: BrainCallback | None = None,
        vad_silence_ms: int = 1500,   # User-Feedback 2026-04-22 (2): 350ms schnitt bei Denkpausen ab. 2026-06-08: 1200→1500ms ("1,5-Sekunden-Pause-Regel") — gibt mehr Luft für Denkpausen und reduziert das Zerstückeln langer Anweisungen in Fragmente. Kurze Commands wie 'auflegen' bleiben schnell, weil HANGUP_RE vor dem Brain-Call greift.
        stt_final_timeout_s: float = 8.0,
        # No-PROGRESS (stall) window for a streaming brain turn — NOT a total
        # wall-clock cap. The deadline resets every time the turn makes progress
        # (a streamed text chunk OR a tool-use-loop boundary, see
        # ``_run_brain_with_stall_guard`` + ``_mark_brain_progress``). It fires
        # the spoken fallback only when the provider is genuinely STALLED — no
        # progress at all for this long — which is the original liveness guard
        # ("Jarvis stopped thinking and never replied"). Live bug 2026-06-01:
        # this used to be a TOTAL cap (25 s), so a vision question that ran a
        # Gemini tool-use loop (image upload + context cache + function_call +
        # tool execution) legitimately exceeded it and was guillotined mid-work —
        # Jarvis looked lazy while it was still working. Idle-hangup is no longer
        # a coupling concern (the old "MUST be < idle_timeout" rule): the brain
        # turn is awaited INLINE in ``_active_session`` (pipeline.py ~2748), so
        # the idle timer never ticks during PROCESSING — which frees us to widen
        # this from 25 s to 30 s (2x the observed worst-case no-progress gap of
        # ~15 s, still below the provider's own ~40 s stream timeout so the
        # provider's error path wins on a true hang). KNOWN LIMITATION: the
        # window also covers a single model round's *pre-first-output* think time
        # (image processing before the first delta). If that ever exceeds this
        # value the fallback still fires — there is no progress signal during the
        # in-flight HTTP request. Raise this (not the ceiling) for a
        # consistently-slow vision profile.
        brain_timeout_s: float = 30.0,
        # Absolute ceiling backstop for a brain turn that keeps drip-feeding
        # progress forever (pathological). Bounds the worst case so the session
        # can never wedge in PROCESSING. Generous: real vision+tool turns finish
        # well under it; only a misbehaving provider ever reaches it.
        brain_hard_timeout_s: float = 90.0,
        # Poll cadence for the stall guard. Small + cheap (only runs during an
        # in-flight brain turn). Sub-second so the spoken fallback is timely.
        brain_stall_poll_s: float = 0.5,
        # Auto-flush pending fragments collected by `_complete_or_buffer_context`
        # if no follow-up arrives within this window. Prevents the silent
        # listening-trap where STT delivered "Jarvis wenn ..." once and then
        # the user never completed the sentence — without this timer the
        # pipeline would only break out via the 30 s idle hangup.
        pending_context_flush_s: float = 4.0,
        input_device: int | str | None = None,
        output_device: int | str | None = None,
        idle_timeout_s: float = 30.0,
        post_tts_listen_suppression_s: float = 0.8,
        # User-Mandat 2026-05-18: Jarvis darf NUR nach explizitem "Hey Jarvis"
        # einen Turn starten. Wenn False (Default), endet die Session direkt
        # nach der TTS-Antwort mit hangup_reason=turn_complete und der Wake-
        # Listener ist wieder die einzige Eintrittstuer — kein Open-Mic-
        # Folgeturn, der auf Hintergrundgespraeche / TV / Mit-Bewohner
        # triggert. Wenn True, bleibt die Session nach der Antwort offen und
        # weitere Turns laufen ohne neues Wake bis HANGUP_RE / idle_timeout_s
        # / Hotkey die Session beendet (Legacy-Konversationsmodus 2026-05-05
        # bis 2026-05-18). Production-Wiring liest cfg.trigger.single_turn_mode
        # und uebersetzt es in ``not single_turn_mode`` an dieser Stelle.
        continue_listening_after_response: bool = False,
        enable_openwakeword: bool = True,
        enable_whisper_wake: bool = True,
        # When True (default), an OpenWakeWord hit is a *candidate* only: the
        # wake loop transcribes the few seconds before the hit with the
        # configured utterance STT (e.g. Groq) and requires a strict
        # "hey/hi/hallo + jarv" pattern before activating. Eliminates the
        # bare-"Jarvis" false fires from the neural model without pendulumming
        # the OWW threshold (BUG-009 floor stays intact). Production wiring
        # reads ``cfg.trigger.require_hey_prefix``.
        require_hey_prefix: bool = True,
        # When False, NO local FasterWhisperProvider is built (cloud-first
        # lightweight wake: openWakeWord only, no GPU, no ~1 GB model). The
        # RollingWhisperWake backstop and the faster-whisper VAD probe are
        # then disabled. An explicitly passed ``stt`` always wins regardless
        # of this flag. Default True preserves the legacy heavy-path behaviour.
        enable_local_whisper: bool = True,
        ack_phrase: str = ACK_PHRASE,
        bus: EventBus | None = None,
        supervisor: Supervisor | None = None,
        config: Any = None,
        vision_provider: Any = None,
        activation_gate: Callable[[], bool] | None = None,
        # Pre-Thinking-Ack Flash-Brain (spec: 2026-05-11-pre-thinking-ack-
        # flash-brain-design.md). When provided, every user utterance kicks
        # off a parallel acknowledgment LLM call BEFORE the main brain
        # starts thinking. Output is published as
        # AnnouncementRequested(kind="preamble") so the existing
        # _on_announcement handler runs it through TTS. None disables the
        # feature without changing any code paths.
        ack_brain: Any = None,
        # Resolved custom-wake-word plan (jarvis.speech.wake_phrase.WakeWordPlan).
        # When None (every legacy call site + all existing tests) the wake path
        # is byte-identical to the historical "Hey Jarvis" behaviour. When set,
        # it overrides the OWW model + threshold, the prefix-verifier matcher,
        # and the rolling-whisper pattern from the plan.
        wake_plan: Any = None,
    ) -> None:
        self._call_hotkeys = call_hotkeys
        self._ptt_hotkeys = ptt_hotkeys
        self._hangup_hotkeys = hangup_hotkeys
        # Push-to-talk runtime state. ``_ptt_mode`` arms the raw-recording path
        # in ``_active_session``; ``_ptt_release_event`` is the up-edge signal
        # that ends the recording. A held key never auto-ends via the VAD — the
        # release (or the safety cap) is the only natural endpoint. The cap
        # guards against a stuck-key / lost-release-edge wedging the mic open.
        self._ptt_mode = False
        self._ptt_release_event = asyncio.Event()
        self._ptt_max_hold_s = 60.0
        # While the key is held, re-transcribe the growing buffer every N
        # seconds and publish it as a non-final TranscriptionUpdate so the orb
        # bubble shows the live transcript (parity with the wake-word path,
        # which gets live partials from the VAD stability probe). PTT bypasses
        # the VAD, so it has no probe — this is its own lightweight live feed.
        # One cloud-STT call per interval while holding; 0 disables the feed.
        self._ptt_partial_interval_s = 1.2
        # Chat mic-dictation: transcribe-only into the chat input box, never to
        # the brain. A SEPARATE lane from the voice path — its own stop event +
        # task so it can never touch ``_handle_utterance`` / the wake loop.
        self._dictation_stop_event = asyncio.Event()
        self._dictation_task: asyncio.Task[None] | None = None
        self._dictation_max_s = 300.0
        # ``self._stt`` is the LOCAL FasterWhisperProvider used by the wake
        # backstop + VAD endpoint-probe (many calls/sec, a cloud round-trip
        # would be too slow). In the cloud-first lightweight path it is None:
        # openWakeWord alone handles wake and no local Whisper is loaded.
        # An explicitly passed ``stt`` always wins; otherwise build one only
        # when the heavy local path is enabled.
        if stt is not None:
            self._stt = stt
        elif enable_local_whisper:
            self._stt = FasterWhisperProvider()
        else:
            self._stt = None
        # ``self._utterance_stt`` is the post-wake final transcription. It
        # honours ``cfg.stt.provider`` and may resolve to a cloud STT (Groq,
        # OpenAI, Deepgram). Defaults to the local instance if no config is
        # passed in (may be None in the lightweight path).
        self._utterance_stt: Any = self._stt
        if config is not None and getattr(config, "stt", None) is not None:
            try:
                from jarvis.plugins.stt import build_stt_from_config

                resolved = build_stt_from_config(config.stt)
                # Swap in the resolved provider when it differs from the local
                # instance — or whenever there is no local instance at all.
                if resolved is not self._stt and (
                    self._stt is None or type(resolved) is not type(self._stt)
                ):
                    self._utterance_stt = resolved
                    log.info(
                        "Utterance-STT provider resolved: %s (wake stays local)",
                        type(resolved).__name__,
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "Utterance-STT factory failed (%s); reusing local Whisper for utterances.",
                    exc,
                )
        # Live transcript preview uses the cheap local probe when available.
        # In lightweight mode there is no local Whisper, but the post-wake
        # utterance STT may still exist (cloud provider). Keep that path alive
        # so the Listening bubble does not stay stuck on "...".
        self._probe_stt: Any = self._stt or self._utterance_stt
        # User STT dictionary (Wispr-Flow-style custom vocabulary): wrap the
        # utterance + preview handles so EVERY provider's transcript gets the
        # user's corrections — brain turns, chat dictation, and the live
        # preview alike (pure string ops, hot-path safe). The wake path
        # (``self._stt``, wake whisper, echo confirm) stays UNWRAPPED: wake
        # matching must never see rewritten transcripts (AP-27 territory).
        try:
            from jarvis.speech.stt_dictionary import wrap_stt_with_dictionary

            self._utterance_stt = wrap_stt_with_dictionary(self._utterance_stt)
            self._probe_stt = wrap_stt_with_dictionary(self._probe_stt)
        except Exception as exc:  # noqa: BLE001 — corrections must never break voice boot
            log.warning("STT dictionary wrapper unavailable: %s", exc)
        self._tts = tts or _default_tts_for_pipeline(config)
        self._openwakeword_enabled = enable_openwakeword
        # Custom-wake-word plan (jarvis.speech.wake_phrase.WakeWordPlan) or None.
        # When None, the wake path is byte-identical to the legacy "Hey Jarvis"
        # behaviour (every existing test + call site). When set, the plan drives
        # the OWW model + threshold, the prefix-verifier matcher, and the
        # rolling-whisper pattern.
        self._wake_plan = wake_plan
        self._wake_matcher = getattr(wake_plan, "matcher", None)
        # Human label for the wake-listener log line so debugging reflects the
        # actually-configured phrase ("Computer") instead of a hardcoded
        # "Hey Jarvis" when a custom wake word is in use.
        self._wake_phrase_label = getattr(wake_plan, "phrase", None) or "the wake word"
        # Live-apply signal: set_wake_plan() flips this so a running
        # _run_parallel_wake aborts early and _wake_loop re-arms with the new
        # detector/model/matcher — the wake word changes WITHOUT an app restart.
        self._wake_reload_event = asyncio.Event()
        # Live-apply signal: set_keybinds() flips this so the running hotkey
        # trigger re-arms with the new Call/Hangup/Talk combos — a keybind change
        # takes effect WITHOUT an app restart (mirrors the wake live-reload).
        self._hotkey_reload_event = asyncio.Event()
        if wake is not None:
            self._wake = wake
        elif wake_plan is not None and getattr(wake_plan, "engine", "") == "vosk_kws":
            # Any-word Vosk grammar KWS (design spec 2026-07-05): identical
            # CPU-only detector on every OS, phrase is pure configuration.
            from jarvis.plugins.wake.vosk_kws_provider import VoskKwsProvider

            self._wake = VoskKwsProvider(
                phrase=wake_plan.phrase,
                model_path=wake_plan.vosk_model_path or "",
                keyword=wake_plan.oww_keyword,
                on_candidate=self._on_vosk_wake_candidate,
            )
        elif wake_plan is not None:
            self._wake = OpenWakeWordProvider(
                keywords=(wake_plan.oww_keyword,),
                activation_threshold=wake_plan.threshold,
                model_path=wake_plan.oww_model_path,
                # A user-trained custom_onnx model fires on normal-volume speech
                # (~0.9) and must NOT be fed the amplify-only AGC: it lifts quiet
                # BREATH to full scale, which the model then fires on at ~1.0 (live
                # 2026-07-01 "triggers on breathing"). Raw audio scores breath ~0.
                # The pretrained OWW models DO need the AGC (they fire at 0.15-0.23).
                gain_normalization=getattr(wake_plan, "engine", "") != "custom_onnx",
            )
        else:
            self._wake = OpenWakeWordProvider(
                keywords=wake_keywords, activation_threshold=wake_threshold
            )
        # Rolling-window Whisper: transcribes the last ~2 s of audio on a poll
        # cadence and matches the wake phrase. No VAD-endpoint dependency, so
        # it stays robust on a quiet mic. It needs BOTH a local Whisper engine
        # AND a phrase matcher: with no wake plan there is no phrase to listen
        # for (no shipped default pattern — design 2026-07-07), so no rolling
        # detector is armed; a later set_wake_plan() arms one live. The
        # enabled flag stays in lockstep so the heartbeat reports whisper=off
        # instead of a phantom on.
        if (
            enable_whisper_wake
            and self._stt is not None
            and self._wake_matcher is not None
        ):
            self._whisper_wake = RollingWhisperWake(
                self._stt,
                pattern=self._wake_matcher,
                poll_interval_s=self._wake_poll_interval(),
            )
            self._whisper_wake_enabled = True
        else:
            self._whisper_wake = None
            self._whisper_wake_enabled = False
        # require_hey_prefix may arrive either as an explicit kwarg or from
        # cfg.trigger.require_hey_prefix. The kwarg wins so tests can override.
        cfg_require = True
        if config is not None and getattr(config, "trigger", None) is not None:
            cfg_require = bool(
                getattr(config.trigger, "require_hey_prefix", True)
            )
        self._require_hey_prefix = bool(require_hey_prefix) and cfg_require
        self._brain: BrainCallback = brain_callback or _echo_brain
        # Flash-Brain reference (None when feature disabled).
        self._ack_brain: Any = ack_brain
        self._turn_state = TurnTakingState.IDLE
        # Incomplete-prompt completion buffer + its per-gap timeout task.
        # See docs/superpowers/specs/2026-05-25-incomplete-prompt-completion-design.md.
        self._completion_buffer = PendingPromptBuffer()
        self._completion_timeout_task: asyncio.Task[None] | None = None
        # True when the currently buffered fragment is COMPLETE-classified
        # (waiting on the short conversational grace window before dispatch);
        # False when it is INCOMPLETE-classified (long wait, silent discard
        # on timeout). Drives the branch in _completion_timeout_fire.
        self._buffer_is_complete: bool = False
        self._stt_final_timeout_s = stt_final_timeout_s
        self._brain_timeout_s = max(1.0, float(brain_timeout_s))
        # Stall guard (see _run_brain_with_stall_guard). Ceiling is clamped to be
        # >= the stall window so the two never invert.
        self._brain_hard_timeout_s = max(
            self._brain_timeout_s, float(brain_hard_timeout_s)
        )
        self._brain_stall_poll_s = max(0.05, float(brain_stall_poll_s))
        # Floor below which the canned "took too long" phrase is suppressed as a
        # stale-state guard (see VoiceConfig.min_timeout_phrase_s + the floor
        # guard in _speak_brain_timeout). Read from config when present, then
        # CLAMPED to <= the stall window: a real timeout only fires *after* that
        # window, so its elapsed is always >= the floor — the clamp makes
        # "suppress a legitimate timeout" structurally impossible.
        _min_phrase = getattr(
            getattr(config, "voice", None),
            "min_timeout_phrase_s",
            self._brain_timeout_s,
        )
        self._min_timeout_phrase_s = min(
            self._brain_timeout_s, max(0.0, float(_min_phrase))
        )
        self._brain_last_progress = time.monotonic()
        # Monotonic stamp of the current brain-bound turn's start (set in
        # _handle_utterance_turn next to the per-turn flag reset). 0.0 = no turn
        # in flight; the floor guard refuses to suppress on the sentinel so a
        # turn it cannot prove was fast still speaks (AD-OE6 zero-silent-drop).
        self._turn_start_monotonic: float = 0.0
        # Pre-first-token "still-thinking" heartbeat (WS2, live bug 2026-06-14):
        # a dedicated monotonic stamp the no-first-frame ceiling re-arm reads, so
        # a deep brain that thinks for tens of seconds before its first token (no
        # on_progress, no tool round) is not beheaded. Pinged by
        # _run_brain_with_stall_guard only while pre-first-token. Kept SEPARATE
        # from _brain_last_progress so the 30 s brain no-progress stall guard
        # stays intact. 0.0 = no turn in flight / not thinking.
        self._brain_thinking_heartbeat: float = 0.0
        # Monotonic stamp of the last *long-running desktop tool* heartbeat
        # (computer_use loop step → ObservationCaptured/ActionPlanned on the bus
        # → _on_agent_progress). While these keep arriving the absolute ceiling
        # is suspended in _run_brain_with_stall_guard, so a legitimately long
        # multi-step desktop automation is never guillotined mid-work (live bug
        # 2026-06-07: a 10-step OBS automation was cut off at 30 s). 0.0 = never
        # seen, so the ceiling applies normally to ordinary chat/vision turns.
        self._long_tool_last_activity: float = 0.0
        # True once the streaming turn has handed a real sentence to TTS. Read
        # by the stall-fallback guard so a canned timeout phrase is never
        # stacked on top of an answer the user is already hearing (live bug
        # 2026-06-02). Reset at the start of every _brain_streaming turn.
        self._spoke_this_turn = False
        # Hard ceiling on a single TTS playback (see _TTS_PLAYBACK_CEILING_S).
        # Guards against a stalled output device / TTS stream wedging _speak —
        # which would freeze the voice session and stop the wake loop re-arming.
        self._speak_playback_ceiling_s = _TTS_PLAYBACK_CEILING_S
        # Mid-playback device-wedge detector (Wave-1 latency fix). Polls the
        # player's write-progress and aborts a stalled device in ~5 s instead of
        # waiting out the ceiling — the core fix for the 60-156 s "open app"
        # voice-hangs.
        self._speak_playback_stall_s = _TTS_PLAYBACK_STALL_S
        # Floor below which the NO-FIRST-FRAME timeout notice is suppressed as a
        # stale-state guard. Unlike _min_timeout_phrase_s (sized to the 30 s
        # brain stall window for the stall/total-cap sites), this path is
        # beheaded at the shorter _speak_playback_ceiling_s, so its floor is
        # derived from THAT ceiling and clamped <= it: a real ~20 s abort always
        # clears the floor, a spurious sub-second fire never does (live bug
        # 2026-06-14 — a 30 s floor swallowed a real 20.83 s abort → silence).
        _nff_cfg = getattr(
            getattr(config, "voice", None), "no_first_frame_phrase_floor_s", None
        )
        self._no_first_frame_floor_s = min(
            self._speak_playback_ceiling_s,
            max(0.0, float(_nff_cfg))
            if _nff_cfg is not None
            else _NO_FIRST_FRAME_FLOOR_FRACTION * self._speak_playback_ceiling_s,
        )
        # Per-turn mark: the no-first-frame ceiling beheaded this turn's
        # playback. Read by _handle_silent_brain_turn so a beheaded-and-empty
        # turn ends with an audible timeout notice instead of silent LISTENING
        # (AD-OE6; live bug 2026-06-10 14:34). Reset at every turn finalize.
        self._playback_aborted_no_first_frame = False
        # Per-turn terminal mark: a timeout / "couldn't finish" notice already
        # spoke for this utterance, so the outcome is closed. The double-answer
        # guard in ``_speak`` reads it to suppress a late/abandoned brain ANSWER
        # (kind="reply") that would otherwise speak a SECOND time for the same
        # content (live complaint 2026-06-30: a stalled tool timed out, then the
        # turn re-answered). Re-armed at every utterance finalize.
        self._brain_timeout_spoken_this_turn = False
        self._pending_context_flush_s = max(0.5, float(pending_context_flush_s))
        self._pending_flush_task: asyncio.Task[None] | None = None
        self._vad = SileroEndpointer(
            silence_ms=vad_silence_ms,
            # Hard-cap of one captured chunk. The old 2026-05-09 value of 8 s
            # assumed a cap-hit DISPATCHED (so it "felt too long"), but a
            # ``max_utterance`` cut now carry-merges (``FORCED_CUT_REASONS`` →
            # keep listening, no dispatch — see _handle_utterance_turn), so a
            # higher cap costs no extra wait: it only reduces how often a long
            # continuous dictation is sliced into carry chunks (which degraded
            # transcription — deep dive 2026-06-16: an 8 s slice came back as
            # just "und mehr."). 15 s keeps slicing rare while the carry-runaway
            # guard (60 s / ~1.9 MB) stays the real backstop.
            max_utterance_s=15,
            on_speech_start=self._on_vad_speech_start,
            on_silence_start=self._on_vad_silence_start,
            on_silence_cancel=self._on_vad_silence_cancel,
            on_endpoint=self._on_vad_endpoint,
            probe_callback=self._on_vad_probe,
            probe_interval_ms=650,
            probe_min_active_ms=650,
            probe_tail_ms=1800,
        )
        # STT stability probe — guards against speaker bleed (music,
        # podcast) where Silero keeps reporting "speech" but Whisper
        # transcribes only the user. The probe transcribes only the tail
        # of the active buffer (last 2 s). Two signals end the turn:
        #   1. tail comes back empty / very short and low-confidence
        #      → user hasn't said anything new for a while, end now.
        #   2. tail transcript is identical to the previous tail
        #      transcript → nothing new arrived, end now.
        # The tail-only approach is critical because transcribing a
        # growing buffer would feed more and more music into Whisper
        # with each probe, producing fresh hallucinated lyrics every
        # call and never stabilising.
        self._probe_last_text: str = ""
        self._probe_live_text: str = ""
        self._probe_stable_count: int = 0
        # A loud stable tail must PERSIST across probes before forcing — the same
        # 2-probe persistence the empty/boilerplate tail already requires
        # (2026-06-14). A single stable reading is not proof the user stopped:
        # Whisper hands back the same clipped partial across a brief mid-sentence
        # pause, and the old one-shot force cut the user off at silence_ms=0
        # (live 2026-06-15: 'i would like you to...' force-cut on a single probe).
        # No probe-force may rest on one reading any more.
        self._probe_required_stable: int = 2
        # Consecutive *loud empty* tails seen this turn. The empty-tail signal
        # forces only after it PERSISTS for ``_probe_required_empty`` probes —
        # a single empty reading mid-speech is a transient Whisper miss on a
        # quiet/half-formed syllable (the "och ha..." → 'um' cut at silence_ms=0,
        # 2026-06-14), not proof the user stopped. Mirrors the stable-tail
        # persistence so a still-speaking user is never cut on one bad probe;
        # sustained emptiness (real speaker bleed) still forces.
        self._probe_empty_count: int = 0
        self._probe_required_empty: int = 2
        # True once the user has produced a genuine (non-empty, non-boilerplate)
        # tail this turn. Monotonic within a turn; reset at the boundary. While
        # False the turn is "pure bleed so far" and a known-hallucination tail
        # forces immediately (the original speaker-bleed cure). Once True, a
        # boilerplate tail is almost always Whisper mis-decoding the user's
        # ongoing speech (live 2026-06-15: 'thank you for your help.' conf 0.43
        # mid-sentence) — it must no longer short-circuit the silence patience
        # and instead defers like a loud empty tail.
        self._probe_seen_real_speech: bool = False
        self._probe_in_flight: bool = False
        # Monotonic turn-scope token. Captured when a probe is spawned and
        # re-checked when it completes: a probe whose generation no longer
        # matches belongs to an already-ended turn and must not touch turn
        # state. Bumped by ``_reset_probe_state`` at every turn boundary.
        # This is the fix for the cross-turn probe leak (2026-05-25): a cloud
        # utterance-STT probe can return one or more turns late and otherwise
        # forces a stale endpoint onto the next utterance (discarded as a
        # false_start → silently dropped turn). See
        # tests/unit/speech/test_probe_cross_turn_leak.py.
        self._probe_generation: int = 0
        # Threshold below which a tail transcript counts as "empty".
        # Whisper usually emits hallucinated single words like "thank
        # you." or "danke." on near-silence — we don't want those to
        # count as "the user said something new".
        self._probe_min_text_len: int = 4
        self._probe_min_confidence: float = 0.55
        # Bus injected so AudioPlayer publishes AudioOutFirst on the first
        # audible sample — UI subscribers (orb mouth animation + SPEAKING
        # bubble) sync to actual audio start, not the early SPEAKING state.
        # Master output volume (0.0–1.0) from [tts].volume. Defensive getattr
        # chain: ``config`` may be None (test fixtures) and older TOMLs predate
        # the field — both fall back to full volume.
        _tts_volume = getattr(getattr(config, "tts", None), "volume", 1.0)
        # Optional user device-name priority ([audio].*_device_priority) fed into
        # the "auto-headset" resolver so an uncommon headset/mic wins by name
        # without a code edit. Defensive getattr: ``config`` may be None (test
        # fixtures) and older TOMLs predate the fields — both mean "no override".
        _audio_cfg = getattr(config, "audio", None)
        self._output_priority: tuple[str, ...] = tuple(
            getattr(_audio_cfg, "output_device_priority", None) or ()
        )
        self._input_priority: tuple[str, ...] = tuple(
            getattr(_audio_cfg, "input_device_priority", None) or ()
        )
        self._player = AudioPlayer(
            device=output_device,
            bus=bus,
            volume=_tts_volume,
            device_priority=self._output_priority,
        )
        # Kept so warm-up can re-resolve the output device against a freshly
        # re-enumerated PortAudio table (post-reboot idx-drift cure, BUG-014).
        self._output_device = output_device
        self._input_device = input_device
        # Idle/silence auto-hangup. A value <= 0 DISABLES it: the session then
        # waits forever for the next utterance or a manual hangup (hotkey /
        # "auflegen"), exactly the "stay active until I hang up" mandate
        # (2026-06-30). ``_idle_timeout_s`` is kept at a sane POSITIVE value even
        # when disabled because the re-arm grace fields below are derived from it
        # and the grace math must never see a zero/negative window; those graces
        # are only consulted on the idle-expiry branch, which is unreachable once
        # the hangup is disabled (the loop passes ``timeout=None`` to asyncio.wait).
        self._idle_hangup_enabled = idle_timeout_s > 0
        self._idle_timeout_s = idle_timeout_s if idle_timeout_s > 0 else 30.0
        # Monotonic timestamp of the last out-of-band announcement Jarvis
        # actually SPOKE (mission/background readback, preamble) plus the grace
        # window it grants. An async readback is delivered via ``_on_announcement``
        # — OFF the ``_active_session`` idle loop — so unlike a normal inline
        # answer it does not naturally reset the idle window. Without this the
        # idle window that was armed mid-mission expires seconds after the
        # readback and hangs up on a user who never asked to (live bug 2026-06-18
        # 08:52: a Computer-Use failure readback at :02 was followed by an
        # idle_timeout hangup at :18). The idle-expiry branch re-arms a fresh
        # window while within this grace. Bounded — one full window's worth.
        self._last_announcement_spoken_monotonic: float | None = None
        self._post_readback_grace_s: float = self._idle_timeout_s
        # Monotonic timestamp of the moment Jarvis last STOPPED speaking an
        # inline answer (SPEAKING -> LISTENING). A long turn dispatched OFF the
        # ``_active_session`` loop — the delegation grace / completion timer buffers
        # a complete-looking command and answers it ~24 s later — leaves the idle
        # window that was armed at the user's utterance still ticking through the
        # whole turn, so it expires seconds after the answer lands and hangs up on
        # a user who never asked to (forensic 2026-06-27 08:49: a "switch the
        # worker" command answered after ~24 s; the 30 s window armed at the
        # utterance expired 6 s later). The idle-expiry branch grants ONE fresh
        # window while within this grace (== one idle window).
        self._last_answer_floor_monotonic: float | None = None
        self._post_tts_listen_suppression_s = post_tts_listen_suppression_s
        self._input_suppressed_until_ns: int = 0
        self._continue_listening_after_response = continue_listening_after_response
        self._session_end_reason: str | None = None
        self._ack_phrase = ack_phrase

        self._state = PipelineState.IDLE
        self._call_event = asyncio.Event()
        self._hangup_event = asyncio.Event()
        self._current_voice_session_id: str | None = None
        # Optionale Event-Bus-Integration — wenn None, sind alle
        # _transition/_emit-Calls no-ops (Rueckwaertskompatibilitaet)
        self._bus = bus
        self._supervisor = supervisor
        # Permanent-Vision (Wave-2 B7): optional injected. Bei None sind alle
        # Vision-Hooks no-ops — Pipeline laeuft unveraendert wie vorher.
        self._config = config
        self._vision_provider = vision_provider
        self._activation_gate = activation_gate or (lambda: True)
        # Wave 0 (omni-latency): per-turn hot-path latency tracker. Anchored at
        # utterance finalize in ``_handle_utterance``; ``None`` until a turn runs.
        self._latency_tracker: LatencyTracker | None = None
        self._latency_first_audio_marked = False
        # Wake-Cooldown nach Hangup: verhindert dass TTS-Ausgabe den Mic
        # selbst wieder als "Hey Jarvis" triggert (Speaker→Mic-Feedback-Loop).
        self._wake_lock_until: float = 0.0
        self._post_hangup_lock_s: float = 3.0
        # A user-initiated HARD hangup (JarvisBar close, hotkey, "auflegen")
        # stops the player, so there is NO TTS tail to echo — the long
        # post-hangup lock then only DEAFENS the wake to the user's very next
        # "Hey <wake>" ("say it twice", live log 2026-07-02 18:40/18:46). Such a
        # hangup uses this SHORT lock instead: just past the disconnect earcon,
        # not the 3 s speaker-tail guard. Set by ``_trigger_voice_hangup`` when
        # it stops the player; reset per session so a no-op hangup while idle
        # cannot shorten a later natural end's lock.
        self._explicit_hangup_lock_s: float = 0.4
        self._explicit_hard_hangup: bool = False
        self._last_wake_keyword: str = ""
        # 2026-05-26: timestamp of the last priority="interrupt"
        # announcement, used by ``_on_announcement`` to gate preamble-class
        # announcements that would otherwise produce cross-surface voice
        # incoherence.  See diagnosis in
        # docs/plans/voice-phrase-mismatch-2026-05-26/README.md and the
        # ``suppress_preamble_after_interrupt_ms`` knob on AckBrainConfig.
        self._last_interrupt_announcement_ts: float | None = None
        # 2026-07-06 interim-ack redesign: (text, monotonic) of the last SPOKEN
        # preamble/progress announcement. ``_on_announcement`` drops a new
        # preamble/progress line with identical wording inside the
        # ``preamble_dedup_window_s`` window — no emitter may repeat itself
        # verbatim in quick succession (forensic 2026-07-05: the identical
        # grounded ack spoke three times in one session).
        self._last_preamble_spoken: tuple[str, float] | None = None
        # v2 anti-loop backstop: monotonic timestamps of SPOKEN preamble/
        # progress lines. ``_on_announcement`` drops anything beyond
        # ``preamble_rate_limit_per_min`` in a rolling 60 s window so the
        # historical "kept repeating forever" bug class dies at this shared
        # chokepoint no matter which emitter misbehaves.
        self._preamble_spoken_times: deque[float] = deque(maxlen=32)
        # AD-OE5/OE6: completion-class announcements that arrive while the user
        # holds the floor are parked here and flushed at the next turn-boundary
        # (when the turn-state returns to LISTENING/IDLE) instead of barging
        # mid-utterance. Preambles are dropped, not parked — they are stale by
        # the time the user finishes. See ``_on_announcement`` + ``_set_turn_state``.
        self._deferred_announcements: list[AnnouncementRequested] = []
        # Pre-rendered ACK ("Ja?") als PCM-Bytes — beim Warm-up gefüllt
        self._ack_pcm: bytes = b""
        # Pre-rendered Task-Ack-Phrasen ("Sofort.", "Right away." …) als PCM-Cache.
        # Key: (lang, phrase_text) → PCM-Bytes. Abspielrate siehe GeminiFlashTTS (24 kHz).
        self._task_ack_pcm: dict[tuple[str, str], bytes] = {}
        self._phrase_picker = PhrasePicker()
        # Multi-fragment turn buffer. VAD/STT can split natural speech at
        # pauses; we hold only clearly incomplete fragments here.
        self._pending_user_context: list[str] = []
        # VAD endpoint reason from the most recent _on_vad_endpoint call.
        # Dual-purpose: (a) Long-dictation accumulation — when the VAD
        # force-cuts a still-ongoing utterance (reason="max_utterance"),
        # `_handle_utterance` reads this and carries the partial PCM in
        # `_carry_pcm` to merge with the next segment so a >cap dictation
        # becomes ONE turn instead of N truncated ones. (b) Optional C-signal
        # for a future completeness classifier — same field, same reason
        # values. Reset to None at the start of every _handle_utterance turn
        # so stale reasons never bleed through.
        self._last_endpoint_reason: str | None = None
        self._carry_pcm: bytearray = bytearray()
        self._carry_started_monotonic: float | None = None
        # Tracks whether the assistant has spoken (TTS) at least once in the
        # current session. Reserved for the completeness-signal selection
        # (earcon vs spoken cue); harmless if unused.
        self._session_has_assistant_spoken: bool = False
        # Race-Delay: erst wenn Brain länger als diese Schwelle denkt, spielen wir
        # einen Task-Ack ab. Kürzere Brain-Calls bleiben komplett still → keine
        # Redundanz zwischen "Sofort." und direkt folgender Antwort.
        # 2026-04-24: von 1.5 auf 0.8 s gesenkt — Haiku antwortet typisch in
        # 600-900 ms; der Ack soll nur bei echten Wartezeiten feuern, nicht
        # bei normalen Turns.
        self._task_ack_delay_s: float = 0.8

        # Global voice mute — toggled via mascot doubleClick (and any
        # future trigger surface that publishes VoiceMuteToggleRequested).
        # While True, ``_activation_allowed`` returns False so the wake
        # path never fires, and every TTS exit short-circuits. The wake-
        # loop itself keeps running; unmuting is instantaneous.
        self._muted: bool = False

        # CRIT-5 watchdog (user decision 2026-05-17): when the user fires a
        # force-spawn-worker, the worker subprocess runs silently for the
        # duration of the mission. Per the 2026-05-12 calibration, the
        # Spawn-ACK is intentionally suppressed -- but Audit-1 found the
        # resulting 40-90 s silence leaves the user unable to tell whether
        # Jarvis is working or stuck. Compromise: at 90 s we emit a single
        # discrete "Bin noch dran." via AnnouncementRequested. Cancel
        # the watchdog on OpenClawBackgroundCompleted so successful
        # short missions stay silent. FIFO list, one entry per pending
        # spawn -- matches the sequential-dispatch model of the voice
        # pipeline. The 90 s threshold is well past the typical short
        # mission (8-30 s) and avoids spamming the user every spawn.
        self._spawn_watchdog_tasks: list[asyncio.Task[None]] = []
        # First "still on it" heartbeat fires after this delay; 90 s of pure
        # silence read as a crash (2026-06-19), so the first reassurance comes
        # sooner. Then up to ``_heartbeat_max_count`` total, ``_heartbeat_interval_s``
        # apart, while the mission is in flight — hard-bounded so it can never run
        # forever (the in-flight hold equals the watchdog lifetime, see
        # _live_spawn_watchdogs). Tests override these.
        self._spawn_watchdog_delay_s: float = 30.0
        self._heartbeat_interval_s: float = 60.0
        self._heartbeat_max_count: int = 3
        self._heartbeat_recent: deque[str] = deque(maxlen=2)

        # TTS-Announcement-Bridge (Phase 5 CL-13): Router/Tools emittieren
        # `AnnouncementRequested` wenn sie dem User eine Zwischenansage geben
        # wollen (z.B. "Starte einen Sub-Agenten, einen Moment."), ohne den
        # Brain-Pfad zu durchlaufen. Handler spricht direkt via TTS.
        if self._bus is not None:
            self._bus.subscribe(AnnouncementRequested, self._on_announcement)
            # Fire-and-Forget OpenClaw: wenn ein Background-Run fertig wird,
            # proaktive Voice-Ansage ("Sir, fertig. <summary>") — so weiss der
            # User auch dann Bescheid, wenn er zwischenzeitlich etwas anderes
            # gemacht hat.
            self._bus.subscribe(
                JarvisAgentBackgroundCompleted, self._on_background_completed
            )
            # Spawn-Ansage: dynamisch aus action/target geformt.
            self._bus.subscribe(JarvisAgentAnnouncement, self._on_spawn_announcement)
            # Mute toggle from any trigger surface (mascot doubleClick,
            # future hotkey/REST). The handler flips ``self._muted`` and
            # republishes the authoritative state on the bus.
            self._bus.subscribe(
                VoiceMuteToggleRequested, self._on_mute_toggle_requested
            )
            # Wave 0 (omni-latency): perceived time-to-first-audio (ack OR
            # brain, whichever speaks first) feeds the per-turn latency tracker.
            self._bus.subscribe(AudioOutFirst, self._on_audio_out_first)
            # Computer-use liveness: a desktop-automation loop (computer_use)
            # runs as ONE opaque tool call that streams NO text, so the brain
            # stall guard cannot tell "stepping through a 20-action plan" from
            # "provider wedged" by watching text chunks. The loop emits
            # liveness events per step phase (observe / act / per-phase
            # CUStepProfiled — the latter covers long THINK phases that emit
            # neither of the former); treat each as brain progress so a
            # working desktop task is never cut off mid-work (live bugs
            # 2026-06-07 OBS killed at 30 s; 2026-06-09 CapCut beheaded by the
            # 20 s TTS ceiling). The subscription iterates the
            # CU_PROGRESS_EVENTS contract tuple — a new loop event type is
            # added THERE, never here (contract test in
            # tests/unit/harness/test_cu_wave0.py).
            for _ev_type in CU_PROGRESS_EVENTS:
                self._bus.subscribe(_ev_type, self._on_agent_progress)

        # Skills-Brain-Integration: Direct-Trigger + Cron. Ohne gesetzten
        # SkillContext bleiben beide Pfade no-op.
        self._trigger_matcher: TriggerMatcher | None = None
        self._cron_task: asyncio.Task | None = None
        self._cron_stop: asyncio.Event = asyncio.Event()
        # Phase-B warm-up (confirmation-audio pre-render) runs off the critical
        # path so voice can declare ready early. Kept on the instance so tests
        # (and a graceful shutdown) can await it.
        self._warmup_background_task: asyncio.Task | None = None
        # Audible boot-ready cue. It must never sit between ready=True and the
        # wake loop; slow output devices can block playback for seconds.
        self._warmup_ready_cue_task: asyncio.Task | None = None
        # Deferred wake-non-critical loaders (VAD/STT/TTS) run off the
        # wake-ready path so "Hey Jarvis" responds without waiting out the
        # 7-24 s starved warm-up (see ``_warmup_phase_a``). Kept on the instance
        # so a graceful shutdown can cancel + await it.
        self._deferred_warmup_task: asyncio.Task | None = None

        # ContinuationBuffer (Spec docs/superpowers/specs/
        # 2026-05-25-incomplete-prompt-completion-design.md): coalesces a
        # syntactically open-ended utterance (trailing comma / conjunction /
        # determiner / preposition) with the next utterance into ONE brain
        # turn. Prevents the live regression 2026-05-26 12:13 where ONE user
        # task ("Subagent spawnen, …baut, in der …beschrieben wird,") was VAD-
        # cut at the comma and the continuation triggered a SEPARATE
        # spawn_worker — producing multiple sub-agent missions for one task.
        self._continuation_buffer: ContinuationBuffer = ContinuationBuffer()
        # Autonomous drain timer for a silently-held continuation fragment. The
        # ContinuationBuffer has no timer of its own (it only drops a stale
        # fragment lazily on the next process() call); when a held fragment gets
        # neither a continuation nor a clarifying question, this timer dispatches
        # it to the brain after the grace window so it is never silently dropped
        # at the session idle-timeout (AD-OE6; "Jarvis hört für immer zu" wedge
        # 2026-06-19, session da25113a). See _arm_continuation_drain.
        self._continuation_drain_task: asyncio.Task[None] | None = None
        # Continuation recombine (2026-06-16): re-attach a fast-follow utterance
        # to the in-flight turn. See ContinuationWindow + _maybe_recombine_continuation.
        _voice_cfg = getattr(self._config, "voice", None)
        self._continuation_interrupt_enabled = bool(
            getattr(_voice_cfg, "continuation_interrupt_enabled", True)
        )
        self._continuation_window = ContinuationWindow(
            grace_ms=int(getattr(_voice_cfg, "continuation_grace_ms", 2500)),
            max_chain=int(getattr(_voice_cfg, "continuation_max_chain", 3)),
        )
        self._continuation_dispatched_this_turn = False
        # Prior text to drop from history when a recombined turn actually
        # dispatches (deferred so an early-returning guard never mutates history).
        self._continuation_pending_drop: str | None = None
        # Active timeout the ContinuationBuffer lacks: when a held incomplete
        # fragment is never continued, this fires a clarifying question instead
        # of leaving the user in silence (AD-OE6; "hört für immer zu" fix).
        self._clarify_timer_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Live-Provider-Switch (Voice ohne Pipeline-Restart)
    # ------------------------------------------------------------------

    def set_tts(self, new_tts: Any) -> None:
        """Tauscht den TTS-Provider live aus. Kein Pipeline-Restart noetig.

        Der naechste ``_speak()``-Aufruf nutzt automatisch die neue Instanz —
        ein bereits laufender ``synthesize()``-AsyncGen wird nicht
        unterbrochen, weil er an die alte Instanz gebunden ist (sauberer
        Cut-over).

        **Cache-Invalidierung:** Pre-renderte ACK-/Task-Ack-PCM stammen aus
        dem alten Provider und wuerden sonst beim naechsten Wake/Brain-Delay
        in der alten Stimme abgespielt — das war der "warum hoere ich noch
        die alte Stimme nach dem Switch"-Bug. Wir leeren die Caches, der
        naechste Wake rendert sie auto neu (oder skippt bei leerem
        ``ACK_PHRASE``).
        """
        old = type(self._tts).__name__
        new = type(new_tts).__name__
        log.info("TTS-Live-Switch: %s -> %s (Caches invalidiert)", old, new)
        self._tts = new_tts
        self._ack_pcm = b""
        self._task_ack_pcm.clear()

    def set_silence_window_ms(self, ms: int) -> None:
        """Live-apply a new voice silence window to the running VAD.

        Delegates to ``SileroEndpointer.set_silence_window_ms`` so a Settings
        change takes effect immediately (no restart). No-op-safe when the VAD is
        absent (headless / not yet started) — the value still persisted and
        applies on the next start.
        """
        vad = getattr(self, "_vad", None)
        setter = getattr(vad, "set_silence_window_ms", None)
        if callable(setter):
            setter(int(ms))

    def set_tts_volume(self, volume: float) -> None:
        """Live-apply a new master TTS output volume (0.0–1.0) — no restart.

        Delegates to ``AudioPlayer.set_volume`` so a Settings change is audible
        on the next spoken sub-block. No-op-safe when the player is absent
        (headless / not yet started) — the value still persisted and applies on
        the next start.
        """
        player = getattr(self, "_player", None)
        setter = getattr(player, "set_volume", None)
        if callable(setter):
            setter(float(volume))

    def set_audio_devices(
        self,
        *,
        input_device: str | None = None,
        output_device: str | None = None,
    ) -> None:
        """Live-apply a Settings device pick — no app/pipeline restart.

        ``None`` leaves a side unchanged; a device NAME pins it and the
        ``"auto-headset"`` sentinel restores automatic selection (resolution
        happens at stream-open time in the player/capture resolvers).

        - Output: ``AudioPlayer.set_device`` re-resolves and tears down the
          persistent stream, so the next utterance plays on the new device.
        - Input: every mic open reads ``self._input_device`` (per-turn opens
          pick it up naturally); the long-lived wake session is re-armed via
          ``_wake_reload_event`` — the same live-reload contract as
          ``set_wake_plan`` — so the always-on mic reopens on the new device
          within a moment.
        """
        if output_device is not None:
            self._output_device = output_device or None
            player = getattr(self, "_player", None)
            setter = getattr(player, "set_device", None)
            if callable(setter):
                setter(self._output_device)
        if input_device is not None:
            self._input_device = input_device or None
            reload_event = getattr(self, "_wake_reload_event", None)
            if reload_event is not None:
                reload_event.set()

    def _wake_poll_interval(self) -> float:
        """The stt_match wake poll interval — always the fastest calibrated
        value (the user-facing Sensitivity slider was removed 2026-07-10;
        "always spawn at maximum speed on every OS" is now unconditional, not
        a slider-derived choice)."""
        from jarvis.speech.wake_phrase import WAKE_POLL_INTERVAL_S

        return WAKE_POLL_INTERVAL_S

    def set_wake_plan(self, plan: Any) -> None:
        """Live-apply a resolved WakeWordPlan — no app/pipeline restart.

        Root cause of "only Hey Jarvis works": the wake model + matcher are
        wired ONCE at construction, so a UI/toml change never reached the
        running detector. This rebuilds the wake detection in place:

        - openwakeword / custom_onnx -> swap in a new OpenWakeWordProvider for
          the plan's model; the neural model is reloaded lazily on the next
          wake-loop entry.
        - stt_match (an arbitrary custom phrase) -> build a local Whisper engine
          if absent, enable the RollingWhisperWake transcript matcher, and turn
          OpenWakeWord off (the neural model cannot detect an arbitrary phrase).

        After updating the references it flips ``_wake_reload_event`` so the
        running ``_run_parallel_wake`` aborts and ``_wake_loop`` re-arms with the
        new detectors (mic is reopened cleanly). Mirrors the ``set_tts``
        live-switch contract. Safe to call from the FastAPI handler thread — it
        shares the pipeline's event loop.
        """
        prev = getattr(self._wake_plan, "oww_keyword", None)
        self._wake_plan = plan
        self._wake_matcher = getattr(plan, "matcher", None)
        self._wake_phrase_label = getattr(plan, "phrase", None) or "the wake word"
        engine = getattr(plan, "engine", "openwakeword")

        # Build a local Whisper engine on demand for the stt_match path. The
        # provider __init__ is light (the model loads lazily on first
        # transcription), so this does not block the caller.
        if getattr(plan, "needs_local_whisper", False) and self._stt is None:
            try:
                from jarvis.plugins.stt import build_wake_whisper

                stt_cfg = getattr(self._config, "stt", None)
                lang = getattr(stt_cfg, "language", None)
                lang = None if lang in ("", "auto", None) else lang
                # Small CPU wake model (cfg.stt.wake_*), not the heavy utterance
                # model — keeps a live wake-word switch fast (Blackwell CUDA load
                # is ~71 s; base/cpu ~0.45 s, measured). Seed the prompt with the
                # custom phrase so the small model transcribes the (proper-noun)
                # wake name instead of a common word — forensic 2026-06-22.
                # fast_first: this runs on a live settings switch (often from
                # the FastAPI handler), so it must stay non-blocking. The
                # non-fast build now runs the one-time GPU inference probe
                # (blocking up to minutes on a cache miss) — that belongs to
                # the boot hot-swap only. Trade-off: after a LIVE wake-word
                # switch the stt_match wake runs on base/cpu until the next
                # app start, whose background hot-swap restores turbo/cuda.
                self._stt = build_wake_whisper(
                    stt_cfg,
                    language=lang,
                    wake_phrase=getattr(plan, "phrase", None),
                    fast_first=True,
                )
                if self._probe_stt is None:
                    try:
                        from jarvis.speech.stt_dictionary import (
                            wrap_stt_with_dictionary,
                        )

                        self._probe_stt = wrap_stt_with_dictionary(self._stt)
                    except Exception:  # noqa: BLE001 — preview must survive without it
                        self._probe_stt = self._stt
                log.info("Wake-Live-Switch: built local Whisper for custom phrase.")
            except Exception as exc:  # noqa: BLE001 — degrade, never crash the switch
                log.warning("Wake-Live-Switch: local Whisper build failed: %s", exc)

        if not getattr(plan, "wake_available", True):
            # No local model for the user's OWN word — arm NO detector. This is
            # the explicit, honest "wake off, use the hotkey" mode (product rule
            # 2026-07-04), NOT a dead listener: the user activates via hotkey /
            # push-to-talk. Do NOT fall back to the bundled branded 'Hey Rhasspy'
            # model — listening for a word the user never says is the bug we are
            # removing. Installing the local speech pack (any word) or a custom
            # .onnx re-arms the wake via a later set_wake_plan.
            self._openwakeword_enabled = False
            self._whisper_wake_enabled = False
            if self._whisper_wake is not None and self._wake_matcher is not None:
                self._whisper_wake._pattern = self._wake_matcher  # noqa: SLF001
            log.info(
                "Wake-Live-Switch: no local model for %r — wake word OFF; "
                "hotkey / push-to-talk is the activation. Install the local "
                "speech pack (works for any word) or supply a custom .onnx to "
                "enable the wake word.",
                self._wake_phrase_label,
            )
        elif engine == "vosk_kws":
            # Any-word Vosk grammar KWS (design spec 2026-07-05) — same
            # detector on every OS; the phrase is pure configuration, so a
            # live wake-word change is just a new provider instance.
            from jarvis.plugins.wake.vosk_kws_provider import VoskKwsProvider

            self._wake = VoskKwsProvider(
                phrase=plan.phrase,
                model_path=getattr(plan, "vosk_model_path", None) or "",
                keyword=plan.oww_keyword,
                on_candidate=self._on_vosk_wake_candidate,
            )
            self._openwakeword_enabled = True
            if self._whisper_wake is not None and self._wake_matcher is not None:
                self._whisper_wake._pattern = self._wake_matcher  # noqa: SLF001
            self._whisper_wake_enabled = False
        elif engine in ("openwakeword", "custom_onnx"):
            self._wake = OpenWakeWordProvider(
                keywords=(plan.oww_keyword,),
                activation_threshold=plan.threshold,
                model_path=plan.oww_model_path,
                # No amplify-only AGC for a user-trained custom model — it lifts
                # quiet breath to full scale and false-fires (see the ctor site).
                gain_normalization=engine != "custom_onnx",
            )
            self._openwakeword_enabled = True
            # OWW stands alone for a live switch (lightweight default). The heavy
            # RollingWhisperWake backstop is a boot-time opt-in (heavy_local_whisper),
            # not part of a live wake-word change — turn it off here so switching
            # back to "Hey Jarvis" does not leave a stale custom-phrase matcher
            # running. Keep the pattern in sync in case it is re-enabled.
            if self._whisper_wake is not None and self._wake_matcher is not None:
                self._whisper_wake._pattern = self._wake_matcher  # noqa: SLF001
            self._whisper_wake_enabled = False
        else:  # stt_match — arbitrary phrase via local-Whisper transcript match
            if self._stt is not None:
                self._openwakeword_enabled = False
                self._whisper_wake = RollingWhisperWake(
                    self._stt,
                    pattern=self._wake_matcher,
                    poll_interval_s=self._wake_poll_interval(),
                )
                self._whisper_wake_enabled = True
            else:
                # stt_match was requested but the local Whisper engine could not
                # be built. Product rule (2026-07-04): do NOT fall back to a
                # branded 'Hey Rhasspy' model (listening for a word the user never
                # says). Arm NO detector — the wake word is OFF and the honest
                # activation is the hotkey / push-to-talk. This is an explicit,
                # user-visible mode, not a silent dead listener; installing or
                # repairing the local speech pack re-arms the custom phrase via a
                # later set_wake_plan.
                self._openwakeword_enabled = False
                self._whisper_wake_enabled = False
                log.warning(
                    "Wake-Live-Switch: stt_match requested but no local Whisper "
                    "could be built for %r — wake word OFF; use the hotkey / "
                    "push-to-talk. Install or repair the local speech pack (works "
                    "for any word) or supply a custom .onnx to enable it.",
                    self._wake_phrase_label,
                )

        log.info(
            "Wake-Live-Switch: %s -> engine=%s keyword=%s (oww=%s whisper=%s)",
            prev,
            engine,
            getattr(plan, "oww_keyword", "?"),
            self._openwakeword_enabled,
            self._whisper_wake_enabled,
        )
        # Re-arm the running wake loop with the new detectors.
        self._wake_reload_event.set()

    def set_keybinds(
        self,
        *,
        call: list[str] | None = None,
        hangup: list[str] | None = None,
        ptt: list[str] | None = None,
    ) -> None:
        """Live-apply changed voice keybinds — no app/pipeline restart.

        Root cause of "I set a key and pressing it does nothing": the Call /
        Hangup / Talk combos are armed once at pipeline start (the
        ``async with HotkeyTrigger`` block), so a UI/toml save only reached the
        OS on the next boot. This updates the stored combos and flips
        ``_hotkey_reload_event`` so the running hotkey trigger re-arms in place.

        Mirrors the ``set_wake_plan`` live-apply contract: safe to call from the
        FastAPI handler thread — it shares the pipeline's event loop. Only the
        actions passed are changed; ``None`` leaves that action untouched.
        """
        if call is not None:
            self._call_hotkeys = list(call)
        if hangup is not None:
            self._hangup_hotkeys = list(hangup)
        if ptt is not None:
            self._ptt_hotkeys = list(ptt)
        log.info(
            "Keybind-Live-Switch: CALL=[%s] PTT=[%s] HANGUP=[%s]",
            ", ".join(self._call_hotkeys),
            ", ".join(self._ptt_hotkeys) or "off",
            ", ".join(self._hangup_hotkeys),
        )
        self._hotkey_reload_event.set()

    async def _hotkey_reload_loop(self, trigger: HotkeyTrigger) -> None:
        """Re-arm the live hotkey trigger whenever set_keybinds flips the event.

        Keeps the outer ``async with HotkeyTrigger`` (and the whole voice
        session) intact — only the OS registrations are swapped — so a keybind
        change applies without an app restart. A failed re-arm is contained
        inside ``HotkeyTrigger.rearm`` (degrade, never raise).
        """
        while True:
            await self._hotkey_reload_event.wait()
            self._hotkey_reload_event.clear()
            bindings: dict[str, list[str]] = {
                "call": list(self._call_hotkeys),
                "hangup": list(self._hangup_hotkeys),
            }
            ptt_events: set[str] = set()
            if self._ptt_hotkeys:
                bindings["ptt"] = list(self._ptt_hotkeys)
                ptt_events.add("ptt")
            await trigger.rearm(bindings, push_to_talk=ptt_events)

    # ------------------------------------------------------------------
    # Bus / Supervisor Helper — no-op wenn nicht konfiguriert
    # ------------------------------------------------------------------

    async def _transition(self, new_state: str) -> None:
        if self._supervisor is not None:
            try:
                await self._supervisor.set_state(new_state)
            except Exception as exc:  # noqa: BLE001
                log.warning("Supervisor-Transition zu %s fehlgeschlagen: %s", new_state, exc)
        # Permanent-Vision Privacy-Hook (Wave-2 B7): bei IDLE pausieren,
        # bei ACTIVE-Familie (LISTENING/THINKING/SPEAKING) resumen.
        self._maybe_toggle_vision_on_state(new_state)

    async def _set_turn_state(
        self,
        new_state: TurnTakingState,
        *,
        only_from: TurnTakingState | None = None,
    ) -> None:
        previous = getattr(self, "_turn_state", TurnTakingState.IDLE)
        # Conditional transition (``only_from``): apply ONLY when the state is
        # still the expected origin. Used by callbacks that may race a newer
        # state (a late VAD false-start endpoint must never yank the machine
        # out of JARVIS_SPEAKING).
        if only_from is not None and previous != only_from:
            return
        if previous != new_state:
            log.info("turn-state: %s -> %s", previous.value, new_state.value)
        # Jarvis just STOPPED speaking → the floor goes back to the user. Stamp it
        # so the idle loop can grant a fresh listening window even when the turn
        # ran off the main loop (delegation grace / completion timer) and left the
        # original idle window ticking (forensic 2026-06-27 08:49).
        if (
            previous == TurnTakingState.JARVIS_SPEAKING
            and new_state == TurnTakingState.LISTENING
        ):
            self._last_answer_floor_monotonic = time.monotonic()
        self._turn_state = new_state
        await self._transition(self._supervisor_state_for_turn(new_state))
        # Turn-boundary: the floor has cleared → flush any announcements that
        # were deferred while the user was speaking (AD-OE6 zero-silent-drop).
        # Replayed through ``_on_announcement`` so they re-run every guard
        # (hangup, mute, the now-passing floor check). Scheduled, not awaited,
        # so a deferred readback's playback never blocks the state machine.
        if (
            new_state in (TurnTakingState.LISTENING, TurnTakingState.IDLE)
            and getattr(self, "_deferred_announcements", None)
        ):
            pending = self._deferred_announcements
            self._deferred_announcements = []
            for event in pending:
                asyncio.create_task(
                    self._on_announcement(event), name="deferred-announcement"
                )

    def _within_post_answer_grace(self) -> bool:
        """True if Jarvis stopped speaking within the last idle window. The idle
        loop grants ONE fresh listening window so a slow answer dispatched off
        this loop is not hung up on seconds after it lands (forensic 2026-06-27).
        Bounded: after the re-armed window elapses the stamp is older than one
        idle window and normal idle-timeout resumes."""
        last = self._last_answer_floor_monotonic
        return last is not None and (time.monotonic() - last) < self._idle_timeout_s

    @staticmethod
    def _supervisor_state_for_turn(state: TurnTakingState) -> str:
        if state == TurnTakingState.IDLE:
            return "IDLE"
        if state in (
            TurnTakingState.LISTENING,
            TurnTakingState.USER_SPEAKING,
            TurnTakingState.WAITING_FOR_FINAL_TRANSCRIPT,
        ):
            return "LISTENING"
        if state == TurnTakingState.PROCESSING:
            return "THINKING"
        if state == TurnTakingState.JARVIS_SPEAKING:
            return "SPEAKING"
        return "IDLE"

    def _schedule_turn_state(
        self,
        state: TurnTakingState,
        *,
        only_from: TurnTakingState | None = None,
    ) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(
            self._set_turn_state(state, only_from=only_from),
            name=f"turn-state-{state.value}",
        )

    def _on_vad_speech_start(self) -> None:
        log.info("voice activity start")
        self._schedule_turn_state(TurnTakingState.USER_SPEAKING)
        # A resumed utterance freezes the continuation grace so a slow follow-up
        # still recombines with the just-finished turn (session 71f2d2de). The
        # SAME freeze must reach the pre-dispatch ContinuationBuffer: a fragment
        # held there ("Kannst du bitte...") whose continuation begins inside the
        # window but finalizes just past the 8 s deadline would otherwise be
        # dropped and split the turn (session 241a1984, 2026-06-18). Fail-open:
        # continuation hygiene must never crash the turn.
        try:
            win = getattr(self, "_continuation_window", None)
            if win is not None:
                win.note_speech_resumed()
            buf = getattr(self, "_continuation_buffer", None)
            if buf is not None:
                buf.note_speech_resumed()
        except Exception:  # noqa: BLE001
            log.debug("continuation note_speech_resumed failed (non-fatal)", exc_info=True)

    def _on_vad_silence_start(self) -> None:
        log.info("silence timer start")

    def _on_vad_silence_cancel(self) -> None:
        log.info("silence timer cancel")
        self._schedule_turn_state(TurnTakingState.USER_SPEAKING)

    def _on_vad_endpoint(self, reason: str) -> None:
        log.info("voice activity stop: reason=%s", reason)
        # Carry the endpoint reason to the turn handler. The PCM blob is
        # consumed on a separate channel (vad_iter.__anext__ in
        # _active_session) that only sees bytes; this synchronous callback
        # fires just before the blob is yielded, so _handle_utterance can read
        # the reason to decide accumulate (forced cut) vs. finalize. The
        # same field is also exposed as a C-signal to a future completeness
        # classifier ("max_utterance" = hard-chopped utterance).
        self._last_endpoint_reason = reason
        self._reset_probe_state()
        if reason != "false_start":
            self._schedule_turn_state(TurnTakingState.WAITING_FOR_FINAL_TRANSCRIPT)
        else:
            # A discarded false start must RELEASE the floor: the VAD start
            # set USER_SPEAKING, no transcript will ever follow, and without
            # this transition every completion announcement is deferred
            # "user holds the floor" until some unrelated event (live
            # 2026-07-02 19:06: the mission readback sat 31 s behind a 96 ms
            # VAD blip). Guarded so a racing newer state is never regressed.
            self._schedule_turn_state(
                TurnTakingState.LISTENING,
                only_from=TurnTakingState.USER_SPEAKING,
            )

    def _reset_probe_state(self) -> None:
        self._probe_last_text = ""
        self._probe_live_text = ""
        self._probe_stable_count = 0
        self._probe_empty_count = 0
        # Per-turn discriminator: a fresh turn has not seen real speech yet, so
        # boilerplate is treated as pure bleed (immediate force) until the user
        # actually says something. Cleared here at every turn boundary so it can
        # never leak into the next turn.
        self._probe_seen_real_speech = False
        # Turn boundary: advance the generation so any probe still in flight
        # from the just-ended turn is dropped on completion, and release the
        # in-flight latch so the next turn can probe immediately (a stuck
        # latch from a slow cloud probe would otherwise disable the next
        # turn's probes entirely — the second face of the cross-turn leak).
        self._probe_generation = getattr(self, "_probe_generation", 0) + 1
        self._probe_in_flight = False

    def _on_vad_probe(self, pcm: bytes, tail_loud: bool = True) -> None:
        """Sync callback from SileroEndpointer; spawns the async STT probe task.

        The probe runs Whisper on the *tail* (last ~2 s) of the active
        utterance buffer. While the user is speaking and music plays from
        the speakers, Silero keeps streaming "speech" forever — but
        Whisper only transcribes the close user voice. Two end signals:
        an empty / low-confidence tail (no new user speech in the last
        2 s) or a tail transcript identical to the previous one
        (nothing new added).

        ``tail_loud`` (from the VAD) gates both signals: only a *loud* empty /
        stable tail is speaker bleed and forces the endpoint. A *quiet* tail is
        a genuine thinking pause — the probe defers to the natural ``silence_ms``
        endpoint so the user is not cut off mid-thought. Defaults to ``True`` so
        legacy/direct callers keep the original force-on-empty behaviour.
        """
        # Lightweight mode can still use the utterance STT provider for live
        # transcript preview; only skip probing when no probe STT exists.
        probe_stt = getattr(self, "_probe_stt", None)
        if probe_stt is None:
            return
        if self._probe_in_flight:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._probe_in_flight = True
        generation = getattr(self, "_probe_generation", 0)
        loop.create_task(
            self._stt_probe_async(pcm, generation, tail_loud),
            name="stt-stability-probe",
        )

    async def _stt_probe_async(
        self, pcm: bytes, generation: int | None = None, tail_loud: bool = True
    ) -> None:
        try:
            probe_stt = getattr(self, "_probe_stt", None) or self._stt
            transcript = await probe_stt.transcribe_pcm(pcm)
            # Stale-turn guard: a probe captured ``generation`` when it was
            # spawned. If the turn has since ended (``_reset_probe_state``
            # bumped the generation), this result belongs to a dead turn —
            # drop it before it can force an endpoint or publish a partial
            # into the current turn. ``generation is None`` means the caller
            # did not turn-tag the probe (direct/legacy call) → always honour.
            if generation is not None and generation != getattr(
                self, "_probe_generation", generation
            ):
                return
            raw_text = (getattr(transcript, "text", "") or "").strip()
            text = raw_text.lower()
            confidence = float(getattr(transcript, "confidence", 0.0) or 0.0)

            # Signal 1: empty / hallucination-level tail. The user hasn't
            # said anything in the last `probe_tail_ms`. Force endpoint
            # immediately — this is the dominant case when only music is
            # left in the tail.
            #
            # Three ways "tail is empty" can be true:
            #   (a) Whisper returned no text at all.
            #   (b) The text is shorter than `_probe_min_text_len` — too
            #       little to be a real utterance.
            #   (c) The text matches `_STT_HALLUCINATION_RE` — a known
            #       Whisper-on-silence phrase ("Vielen Dank.", "thanks
            #       for watching", "Untertitel im Auftrag …" …).
            #
            # Confidence alone is NOT a valid empty-tail signal. Whisper's
            # avg log-prob is naturally low on 2-second tails that end on
            # a grammatically dangling word (relative pronouns like
            # "...welcher", subordinating conjunctions, prepositions),
            # because the language model has no follow-up context to anchor
            # the score. Using confidence < threshold as a standalone
            # endpoint-trigger cuts users off mid-sentence — the exact
            # symptom of BUG-018 (2026-05-11): the probe forced endpoint
            # at silence_ms=160 because the real-speech tail "spawnen
            # welcher" scored confidence=0.45 < 0.55.
            #
            # Confidence is kept around for telemetry / future use but no
            # longer steers the endpoint by itself. Signal 2 (stable tail
            # repetition) still catches the residual case where Whisper
            # latches onto a stable background phrase that escapes the
            # hallucination regex.
            # A KNOWN Whisper-on-silence/music boilerplate phrase (subtitle /
            # broadcast credits / "Vielen Dank.") is a deterministic artifact,
            # not the user — high-confidence bleed. A merely empty / too-short
            # tail is ambiguous: bleed OR a quiet half-formed syllable the user
            # is still producing. Keep them separate (they get different
            # patience below).
            tail_is_hallucination = _STT_HALLUCINATION_RE.search(text) is not None
            tail_is_empty = not text or len(text) < self._probe_min_text_len
            if tail_is_empty or tail_is_hallucination:
                if not tail_loud:
                    # Quiet tail = the user paused to think, not speaker bleed.
                    # Do NOT bypass silence_ms via request_endpoint(); defer to
                    # the natural silence endpoint so the user keeps the floor
                    # (the "no time to think" bug, 2026-05-25). The relative-
                    # silence calibration guarantees the silence timer is already
                    # accumulating, so the turn will still end.
                    self._probe_empty_count = 0
                    log.info(
                        "STT probe: quiet empty tail (text=%r) → defer to silence",
                        text[:40],
                    )
                    return
                # Loud empty / too-short tail, OR a (possibly known-boilerplate)
                # tail — whether or not the user has produced clean speech yet
                # this turn. ALL of these are ambiguous on a single reading:
                # speaker bleed OR a brief mumble/hesitation OR the user's live
                # speech that Whisper mis-decoded (e.g. "och ha..." → 'um' at
                # silence_ms=0, 2026-06-14; 'thank you for your help.' conf 0.43
                # mid-sentence, 2026-06-15; and the opening words 'I would like
                # you to' mis-decoded as 'i would like to thank you for your
                # time.' on the FIRST probe, 2026-06-15 19:07 — which the old
                # pre-speech one-shot force beheaded). There is NO reliable way to
                # tell pure pre-speech bleed from hallucinated live speech on a
                # single probe, so we no longer special-case it: every loud
                # empty/boilerplate tail must PERSIST across probes before forcing
                # (mirrors the stable-tail signal). A transient miss defers and
                # keeps the floor; sustained emptiness/boilerplate (real bleed,
                # where the silence endpoint can never fire) still forces — just
                # one probe later. DO NOT re-add a one-shot pre-speech force here:
                # it cannot distinguish a hallucinated real-speech opener from
                # bleed and so cuts the user off mid-sentence (recurred 4×).
                self._probe_empty_count += 1
                if self._probe_empty_count < self._probe_required_empty:
                    log.info(
                        "STT probe: loud empty/boilerplate tail (text=%r, %d/%d) → defer",
                        text[:40],
                        self._probe_empty_count,
                        self._probe_required_empty,
                    )
                    return
                log.info(
                    "STT probe: empty/boilerplate tail sustained (text=%r conf=%.2f, %dx) → force",
                    text[:40],
                    confidence,
                    self._probe_empty_count,
                )
                self._vad.request_endpoint()
                self._reset_probe_state()
                return

            # Tail is non-empty: the empty-tail run is broken, so reset its
            # counter (only *consecutive* empty tails accumulate toward a force).
            self._probe_empty_count = 0
            # The user has produced genuine (clean, non-boilerplate) speech this
            # turn. Kept as a per-turn telemetry/lifecycle marker (monotonic
            # within the turn; cleared at the boundary by ``_reset_probe_state``,
            # guarded against cross-turn leak in test_probe_cross_turn_leak.py).
            # It no longer GATES any endpoint: every empty/boilerplate tail now
            # defers via the same 2-probe persistence regardless of this flag, so
            # a hallucinated real-speech opener is never force-cut. Signal 2 (loud
            # stable tail) still forces on its own persistence path below.
            self._probe_seen_real_speech = True

            # Signal 2: identical to last tail → nothing new arrived.
            self._probe_live_text = _merge_partial_transcript(
                getattr(self, "_probe_live_text", ""),
                raw_text,
            )

            # Adaptive patience: the live partial shows the user composing a
            # delegation OR any long / open-ended dictation → grant this utterance
            # a wider silence window so a pause to formulate the task is not cut
            # off (deep dive 2026-06-16: a long "Agents"/"Agent Team" prompt was
            # chopped at every 1.5 s pause because the trigger matched only
            # delegation keywords). Re-asserted on every non-empty probe so it
            # survives a max_utterance carry; the VAD resets it to the snappy
            # default at the next speech start. Guarded so a VAD double without
            # the method (tests) is a harmless no-op and never aborts the force
            # logic below.
            if _should_extend_silence_for_composition(self._probe_live_text):
                _extend = getattr(self._vad, "extend_silence_window", None)
                if callable(_extend):
                    _extend(_DELEGATION_SILENCE_MS)

            publish_event = getattr(self, "_publish_event", None)
            if callable(publish_event):
                try:
                    await publish_event(
                        TranscriptionUpdate(
                            source_layer="speech.stt.partial",
                            text=self._probe_live_text,
                            is_final=False,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    log.debug("Partial transcription publish failed: %s", exc)

            if text == self._probe_last_text:
                self._probe_stable_count += 1
                if self._probe_stable_count >= self._probe_required_stable:
                    if not tail_loud:
                        # Stable but quiet: the user said a short fragment and
                        # paused. Defer to silence_ms instead of cutting in —
                        # they may still be mid-thought.
                        log.info(
                            "STT probe: stable but quiet tail → defer to silence: %r",
                            text[:80],
                        )
                        return
                    if is_incomplete(
                        raw_text, language=getattr(transcript, "language", "") or ""
                    ):
                        # The tail is syntactically OPEN-ENDED — it ends in a
                        # trailed-off marker ('...'), an open conjunction
                        # ('... and'), a noun-requiring determiner, or a trailing
                        # comma. Whisper appends '...' exactly when the speaker
                        # audibly broke off mid-utterance, so a "stable" reading
                        # of such a tail is the user PAUSING mid-thought, not
                        # finishing. Force-cutting it beheads the turn at
                        # silence_ms≈0 (live 2026-06-15: 'i would like you to...'
                        # → "What do you mean exactly?"). Defer to the natural
                        # silence endpoint instead — the same trailed-off signal
                        # the ContinuationBuffer trusts downstream
                        # (completion.is_incomplete, single source of truth).
                        log.info(
                            "STT probe: stable but incomplete tail (open-ended) → "
                            "defer to silence: %r",
                            raw_text[:80],
                        )
                        return
                    log.info(
                        "STT probe: tail stable (%dx) → force endpoint: %r",
                        self._probe_stable_count,
                        text[:80],
                    )
                    self._vad.request_endpoint()
                    self._reset_probe_state()
            else:
                self._probe_last_text = text
                self._probe_stable_count = 0
        except Exception as exc:  # noqa: BLE001
            log.debug("STT probe failed: %s", exc)
        finally:
            # Only release the latch if it still belongs to this turn. A
            # stale probe (turn already ended) must not clear the latch the
            # next turn may already have re-acquired — ``_reset_probe_state``
            # already cleared it at the boundary.
            if generation is None or generation == getattr(
                self, "_probe_generation", generation
            ):
                self._probe_in_flight = False

    def _vision_cfg(self) -> Any:
        """Liefert RouterVisionConfig oder None (tolerant zu fehlender Config)."""
        cfg = self._config
        if cfg is None:
            return None
        return getattr(getattr(getattr(cfg, "brain", None), "router", None), "vision", None)

    def _maybe_toggle_vision_on_state(self, new_state: str) -> None:
        """Pausiert/resumed den VisionContextProvider anhand Pipeline-State.

        No-op wenn kein Provider injected oder pause_on_idle=False.
        """
        if self._vision_provider is None:
            return
        vcfg = self._vision_cfg()
        pause_on_idle = getattr(vcfg, "pause_on_idle", True) if vcfg is not None else True
        if not pause_on_idle:
            return
        if new_state == "IDLE":
            try:
                self._vision_provider.pause()
            except Exception as exc:  # noqa: BLE001
                log.warning("Vision-pause() bei IDLE fehlgeschlagen: %s", exc)
        elif new_state in ("LISTENING", "THINKING", "SPEAKING"):
            try:
                self._vision_provider.resume()
            except Exception as exc:  # noqa: BLE001
                log.warning("Vision-resume() bei %s fehlgeschlagen: %s", new_state, exc)

    def _match_privacy_phrase(self, text: str) -> str | None:
        """Matcht Privacy-Voice-Phrasen aus Config. Gibt 'pause'/'resume'/None."""
        vcfg = self._vision_cfg()
        if vcfg is None:
            return None
        text_low = text.lower()
        pause_phrases = (
            (getattr(vcfg, "voice_pause_phrase_de", "") or "").lower(),
            (getattr(vcfg, "voice_pause_phrase_en", "") or "").lower(),
        )
        resume_phrases = (
            (getattr(vcfg, "voice_resume_phrase_de", "") or "").lower(),
            (getattr(vcfg, "voice_resume_phrase_en", "") or "").lower(),
        )
        # Resume zuerst — "vision back on" ist spezifischer als "privacy".
        if any(p and p in text_low for p in resume_phrases):
            return "resume"
        if any(p and p in text_low for p in pause_phrases):
            return "pause"
        return None

    def _activation_allowed(self) -> bool:
        """True when external UI/lifecycle state permits voice activation.

        While muted (mascot doubleClick → ``_muted=True``) we always
        return False so the wake-loop ignores every detection. The loop
        keeps spinning; unmuting is one bool flip away.

        ``getattr`` defaults to False for pipelines constructed via
        ``__new__`` (used by privacy/vision unit tests that bypass
        ``__init__``) — those instances are never muted by definition.
        """
        if getattr(self, "_muted", False):
            return False
        try:
            return bool(self._activation_gate())
        except Exception as exc:  # noqa: BLE001
            log.warning("Voice activation gate failed closed: %s", exc)
            return False

    @property
    def is_muted(self) -> bool:
        """Snapshot of the global voice mute flag.

        Public so tests and the REST surface can read the live value
        without going through the bus.
        """
        return self._muted

    async def _on_mute_toggle_requested(
        self, event: VoiceMuteToggleRequested
    ) -> None:
        """Flip the mute flag and broadcast the authoritative state.

        Idempotent toggle: callers do not have to know the current state.
        We log the change at INFO so the live log carries an audit trail.
        """
        new_value = not self._muted
        self._muted = new_value
        log.info(
            "🔇 Voice mute %s (source=%s)",
            "ENABLED" if new_value else "disabled",
            event.source or "unknown",
        )
        # Mute is INPUT-ONLY (maintainer intent 2026-06-29: "mute my mic FOR
        # Jarvis, do NOT mute Jarvis — other things keep working"). We deliberately
        # do NOT call self._player.stop() here: that ran Pa_AbortStream
        # (stream.abort) mid-TTS-write, which (a) contradicted the input-only
        # intent and (b) wedged the shared WASAPI device — the next wake-mic then
        # opened "successfully" but delivered only dead/silent frames (no
        # rolling-whisper, no 3 s Mic-Stall fire), so "Hey <wake>" silently
        # stopped working after a mute-during-speech + hangup. Jarvis finishes the
        # current sentence; new _speak() calls are still suppressed while muted,
        # and the user's input frames are dropped at our boundary — the OS mic is
        # untouched. Forensic: data/jarvis_desktop.log 2026-06-29 14:09–14:12.
        if self._bus is None:
            return
        try:
            await self._bus.publish(
                VoiceMuteChanged(muted=new_value, source=event.source)
            )
        except Exception:  # noqa: BLE001
            log.exception("VoiceMuteChanged publish failed")

    async def _emit_wake(self, keyword: str, confidence: float = 0.0) -> None:
        self._last_wake_keyword = keyword
        if self._bus is not None:
            try:
                await self._bus.publish(
                    WakeWordDetected(
                        source_layer="speech",
                        keyword=keyword,
                        confidence=confidence,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("WakeWordDetected-Publish fehlgeschlagen: %s", exc)

    async def _publish_event(self, event: Any) -> None:
        if self._bus is None:
            return
        try:
            await self._bus.publish(event)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s publish failed: %s", type(event).__name__, exc)

    async def _publish_utterance_captured(self, pcm: bytes) -> None:
        duration_ms = int((len(pcm) / 2) / 16_000 * 1000)
        audio_ref = hashlib.sha256(pcm).hexdigest()[:16]
        await self._publish_event(
            UtteranceCaptured(
                source_layer="speech.vad",
                audio_ref=audio_ref,
                duration_ms=duration_ms,
            )
        )

    # ------------------------------------------------------------------
    # Skills-Brain-Integration: Phase Skills-1
    # ------------------------------------------------------------------

    async def _try_skill_direct_trigger(self, text: str, lang: str) -> bool:
        """Pre-brain hook: voice-pattern match against installed skills.

        Instruction-skill model (2026-06-09 rebuild, AD-S4): a trigger match
        no longer macro-runs the skill and reads raw Markdown aloud. It notes
        the match on the BrainManager (``note_skill_trigger``) and returns
        ``False`` so the normal brain turn proceeds — the manager injects the
        rendered skill instructions into that turn (guaranteed invocation,
        uniform voice output through scrub_for_voice). Always returns
        ``False``; the brain path is never bypassed anymore.
        """
        skill_ctx = try_get_skill_context()
        if skill_ctx is None:
            return False
        if self._trigger_matcher is None:
            self._trigger_matcher = TriggerMatcher(skill_ctx.registry)
        match_result = self._trigger_matcher.match_voice_with_match(text, lang=lang)
        if match_result is None:
            return False
        matched, regex_match = match_result

        # The last non-empty capture group is the "content" (e.g. the tail
        # after the trigger phrase: "merk dir: <content>"). Skills reference
        # it as {{ content }} in their Jinja render context.
        content = ""
        groups = regex_match.groups()
        for grp in reversed(groups):
            if grp and grp.strip():
                content = grp.strip()
                break

        log.info("Skill trigger matched: '%s' for '%s'", matched.name, text)
        await self._emit_skill_direct(matched.name, "voice_direct")
        note = getattr(self._brain, "note_skill_trigger", None)
        if callable(note):
            note(matched.name, content=content, source="trigger")
        else:
            log.warning(
                "brain has no note_skill_trigger — skill %s rides on the "
                "routing-guard probe only", matched.name,
            )
        return False

    async def _emit_skill_direct(self, skill_name: str, trigger_type: str) -> None:
        """Bus-Event SkillDirectTriggered — no-op wenn kein bus konfiguriert."""
        if self._bus is None:
            return
        try:
            await self._bus.publish(SkillDirectTriggered(
                source_layer="speech.pipeline",
                skill_name=skill_name,
                trigger_type=trigger_type,
            ))
        except Exception as exc:  # noqa: BLE001
            log.warning("SkillDirectTriggered-Publish fehlgeschlagen: %s", exc)

    async def _skill_cron_loop(self, stop_event: asyncio.Event) -> None:
        """Cron scheduler for skills.

        Runs as a parallel asyncio task; yields a skill when its cron trigger
        fires and hands it to ``_handle_cron_skill`` (instruction-skill model,
        AD-S4: the brain executes the skill; the spoken result goes out as an
        announcement through the normal scrubbed announcement path).
        """
        ctx = try_get_skill_context()
        if ctx is None:
            return
        if self._trigger_matcher is None:
            self._trigger_matcher = TriggerMatcher(ctx.registry)
        matcher = self._trigger_matcher
        try:
            async for skill in matcher.run_cron_scheduler(stop_event):
                try:
                    await self._handle_cron_skill(skill)
                except Exception as exc:  # noqa: BLE001
                    log.exception("Cron skill '%s' failed: %s", skill.name, exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("Skill-Cron-Loop crashed: %s", exc)

    async def _handle_cron_skill(self, skill: Any) -> None:
        """Run one scheduled skill fire through the brain (AD-S4 extension).

        The brain receives a synthetic scheduled-run turn with the skill
        noted (``note_skill_trigger`` → instruction injection + SkillInvoked
        source="cron"); the reply is announced via ``AnnouncementRequested``
        (scrubbed TTS path). Falls back to the legacy macro runner when the
        wired brain cannot take the handoff (echo/mock brains).
        """
        from jarvis.skills.schema import SkillLifecycleState

        state = getattr(skill, "state", None)
        if state not in (
            SkillLifecycleState.ACTIVE, SkillLifecycleState.VALIDATED,
        ):
            log.debug("cron fire for %s skipped (state=%s)", skill.name, state)
            return
        fm = getattr(skill, "frontmatter", None)
        if fm is not None and fm.risk_policy.default_tier == "block":
            log.info("cron fire for %s skipped (block tier)", skill.name)
            return

        await self._emit_skill_direct(skill.name, "cron")
        note = getattr(self._brain, "note_skill_trigger", None)
        if not callable(note):
            # Legacy fallback: no brain handoff available (echo/mock brain).
            ctx = try_get_skill_context()
            if ctx is not None:
                result = await ctx.runner.run(skill, args={"_trigger": "cron"})
                log.info(
                    "Cron skill '%s' (legacy runner): success=%s",
                    skill.name, result.success,
                )
            return

        note(skill.name, source="cron")
        # Compose the scheduled-run instruction in the conversation language so
        # the brain answers in that language (it derives the reply language from
        # the prompt text) — a German chat must not receive an English briefing
        # (forensic 2026-06-23: the screenshot's English "Good morning, Chef…"
        # announcement). ``lang`` also tags the announcement so the resolver
        # speaks it in the same language. de strings are functional brain
        # prompts, not user-facing artifacts (i18n-allow).
        lang = self._output_language(None, "")
        _prompts = {
            "de": (
                "[Geplanter Lauf] Es ist Zeit für den Skill '{name}'. Führe "  # i18n-allow
                "jetzt seine Anweisungen aus und berichte das Ergebnis kurz."  # i18n-allow
            ),
            "es": (
                "[ejecución programada] Es hora del skill '{name}'. Ejecuta sus "
                "instrucciones ahora e informa brevemente el resultado."
            ),
            "en": (
                "[scheduled run] It is time for the '{name}' skill. Execute its "
                "instructions now and report the result briefly."
            ),
        }
        reply = await self._brain(
            _prompts.get(lang, _prompts["en"]).format(name=skill.name)
        )
        text = (reply or "").strip()
        if text and self._bus is not None:
            try:
                await self._bus.publish(
                    AnnouncementRequested(
                        source_layer="speech.pipeline",
                        text=text,
                        language=lang,
                        priority="normal",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("cron skill announcement failed: %s", exc)
        log.info("Cron skill '%s' executed via brain turn", skill.name)

    async def _on_announcement(self, event: AnnouncementRequested) -> None:
        """TTS-Bypass-Handler (CL-13): spricht sofort, ohne Brain-Pfad.

        Bei ``priority="interrupt"`` wird laufendes Audio-Playback via
        ``AudioPlayer.stop()`` abgebrochen (Barge-in-aequivalent).

        Nutzt ``synthesize()`` + ``player.play_chunks()`` — denselben Pfad wie
        die normale Antwort-Ausgabe. Frueher stand hier ``self._tts.speak(...)``;
        das war ein Phantom-Call, da ``GeminiFlashTTS`` nur ``synthesize()``
        exponiert. Der stumme ``AttributeError`` machte Sub-Agent-Announcements
        unhoerbar (Silent-Failure, entdeckt 2026-04-23).
        """
        if getattr(self, "_muted", False):
            log.debug("Announcement suppressed — voice muted: %r", event.text)
            return
        # Hangup-Gate: once "auflegen" fired, queued/late announcements
        # (Flash-Brain preamble, spawn-watchdog, late background readback)
        # must not punch through. The gate clears at the start of the next
        # session in `_state_loop` (line ~1726).
        hangup = getattr(self, "_hangup_event", None)
        # A readback (kind in _READBACK_KINDS — "completion" or "subagent") is a
        # FRESH turn delivering the answer the user asked for — an offloaded
        # background mission/sub-agent that finished after "auflegen" — so it must
        # punch through the hangup gate (AD-OE5/OE6 zero-silent-drop). A stale
        # preamble / untagged late announcement stays dropped. Live bug
        # 2026-06-14: a heavy research mission's result was silently dropped
        # because the user hung up 13 s after the optimistic ACK.
        is_readback = getattr(event, "kind", None) in _READBACK_KINDS
        if hangup is not None and hangup.is_set() and not is_readback:
            log.info(
                "Announcement nach Hangup unterdrückt: %r", event.text[:80]
            )
            return
        # A completion / sub-agent readback IS the mission's answer — cancel any
        # pending "still on it" heartbeats so a reassurance never lands AFTER the
        # result (the success path does not publish OpenClawBackgroundCompleted,
        # so the heartbeat is not otherwise drained on completion). 2026-06-19.
        if is_readback:
            self._cancel_spawn_heartbeats()
        log.info(
            "📢 Announcement: %r (prio=%s lang=%s)",
            event.text, event.priority, event.language,
        )
        # When the Pre-Thinking-Ack Flash-Brain is wired in, the legacy
        # per-tool template emitter on `brain.router.ack` would double-speak:
        # the Flash-Brain already published its preamble on
        # `brain.ack_brain`, and the router's `generate_ack(tool_name, ...)`
        # callback would fire afterwards on the same utterance. Silently
        # drop the legacy source while the Flash-Brain is active so the user
        # only hears one ack per turn.
        if (
            getattr(self, "_ack_brain", None) is not None
            and getattr(event, "source_layer", None) == "brain.router.ack"
        ):
            log.debug(
                "Skipping legacy router-ack announcement %r — Flash-Brain active.",
                event.text,
            )
            return
        event_kind = getattr(event, "kind", None)
        is_preamble = event_kind == "preamble"
        is_progress = event_kind == "progress"
        # 2026-05-26 cross-surface voice incoherence guard. After an
        # interrupt-priority announcement (typically a MissionFailed
        # readback) the user has just heard a terminal statement; a
        # follow-up "preamble" from any subscriber (Flash-Brain or
        # skill announcement) lands as an incoherent
        # second sentence — see diagnosis README. Suppress preambles
        # inside the configured quiet window.
        if is_preamble and self._last_interrupt_announcement_ts is not None:
            ack_cfg = (
                getattr(self._config, "ack_brain", None)
                if self._config is not None
                else None
            )
            quiet_ms = getattr(
                ack_cfg, "suppress_preamble_after_interrupt_ms", 5000
            )
            if quiet_ms > 0:
                elapsed = time.monotonic() - self._last_interrupt_announcement_ts
                if elapsed * 1000.0 < quiet_ms:
                    log.info(
                        "Preamble announcement suppressed — within %d ms "
                        "post-interrupt quiet window (elapsed=%.0f ms): %r",
                        quiet_ms, elapsed * 1000.0, event.text[:80],
                    )
                    return
        # Symmetric turn-boundary guard (AD-OE5) + idle guard (live bug 2026-07-01).
        # A background "still on it" heartbeat (kind="progress") is only meaningful
        # while the user is ACTIVELY in a session waiting for the mission — the mic
        # is open and Jarvis is LISTENING. It is dropped in every other state:
        # during a foreground turn (THINKING/JARVIS_SPEAKING/…) it would talk over
        # that turn, and while IDLE there is NO active session at all, so speaking
        # it is Jarvis "talking out of nowhere" into a machine the user walked away
        # from (live bug 2026-07-01: a force-spawned mission's three bounded beats
        # spoke into fresh, empty wake sessions after the user hung up). An
        # interrupt is a deliberate barge and still punches through.
        current_turn_state = getattr(self, "_turn_state", TurnTakingState.IDLE)
        if (
            event.priority != "interrupt"
            and is_progress
            and current_turn_state is not TurnTakingState.LISTENING
        ):
            log.info(
                "Progress announcement dropped — no active listening session (%s): %r",
                getattr(current_turn_state, "value", current_turn_state),
                event.text[:80],
            )
            return
        if event.priority != "interrupt" and (
            current_turn_state in _USER_HOLDS_FLOOR_STATES
        ):
            # A preamble or a "still on it" heartbeat (kind="progress") is only
            # meaningful in the moment — once the user holds the floor it is
            # stale, so DROP it (never defer/replay it after the user finishes or
            # after the mission answer; events.py: progress = "droppable when
            # stale"). Completion/readback below owes the user information and is
            # deferred instead.
            if is_preamble or is_progress:
                log.info(
                    "Announcement dropped — user holds the floor (%s): %r",
                    event_kind or "normal",
                    event.text[:80],
                )
                return
            # Completion/readback owes the user information → park it and flush
            # at the next turn-boundary (AD-OE6 zero-silent-drop).
            self._deferred_announcements.append(event)
            log.info(
                "Announcement deferred — user holds the floor: %r",
                event.text[:80],
            )
            return
        # A preamble ("I'm about to think about this") is only coherent BEFORE
        # the answer is voiced. If the turn is already JARVIS_SPEAKING by the
        # time it reaches the handler, the answer (or another readback) is being
        # spoken right now, so the preamble is stale → drop it instead of
        # queueing it behind the answer on the shared player (live bug
        # 2026-06-20: the ack "Ich schaue mir jetzt …" played AFTER the tool
        # result). The floor guard above only covers the USER holding the floor;
        # this covers Jarvis already speaking. The remaining race — the answer
        # starting DURING the preamble's synthesis / play-lock wait — is caught
        # by the should_play predicate handed to play_chunks below.
        if is_preamble and (
            getattr(self, "_turn_state", TurnTakingState.IDLE)
            is TurnTakingState.JARVIS_SPEAKING
        ):
            log.info(
                "Preamble dropped — Jarvis already speaking the answer: %r",
                event.text[:80],
            )
            return
        # Usefulness gate (2026-07-06 interim-ack redesign): the grounded
        # router ack publishes the instant a tool is SELECTED — too early to
        # know whether the bridge is even needed. When the voice turn is
        # currently PROCESSING, hold the ack for the commit grace and only
        # speak it if the brain is STILL busy afterwards (same AD-OE5 helper
        # the Flash-Brain streaming path uses); a turn that answers within
        # the grace stays ack-free. Announcements arriving with no voice turn
        # in flight (chat path) keep legacy behavior.
        if (
            is_preamble
            and getattr(event, "source_layer", None) == "brain.router.ack"
            and getattr(self, "_turn_state", TurnTakingState.IDLE)
            is TurnTakingState.PROCESSING
        ):
            ack_cfg = getattr(getattr(self, "_config", None), "ack_brain", None)
            commit_grace_ms = int(
                getattr(ack_cfg, "grounded_ack_commit_grace_ms", 900) or 0
            )
            if commit_grace_ms > 0 and not await self._await_ack_turn_commit(
                commit_grace_ms
            ):
                log.info(
                    "Grounded ack dropped — turn left PROCESSING during the "
                    "%d ms commit grace (state=%s): %r",
                    commit_grace_ms,
                    getattr(self._turn_state, "name", self._turn_state),
                    event.text[:80],
                )
                return
        if event.priority == "interrupt":
            # Arm the quiet window BEFORE TTS so a synchronous publish of a
            # preamble immediately afterwards sees the up-to-date timestamp.
            self._last_interrupt_announcement_ts = time.monotonic()
            try:
                self._player.stop()
            except Exception as exc:  # noqa: BLE001
                log.warning("Player-Stop vor Announcement fehlgeschlagen: %s", exc)
        # Phase-1-Output-Filter auch fuer Bus-Announcements (Skill-Output,
        # OpenClaw-Announce, Vision-Privacy-Hinweise). Mandat-Pfad #2.
        # Pre-Thinking-Ack Flash-Brain (kind="preamble"): the AckGenerator
        # already ran scrub_for_voice with ack_mode=True. We still re-scrub
        # here as a safety net, but pass ack_mode=is_preamble so legitimate
        # filler-opener phrases ("Lass mich kurz nachschauen.") are
        # preserved on the second pass too.
        # Resolve the announcement's spoken language through the ONE
        # authoritative resolver — the live brain.reply_language pin and the
        # sticky conversation_language win over whatever an emitter stamped on
        # the event, and an undetectable/None tag falls back to the resolved
        # turn language, never a hardcoded German default (forensic 2026-06-23:
        # a German voice chat spoke an English "ANNOUNCEMENT" because
        # event.language was trusted verbatim). The event tag is only a hint,
        # passed where the STT tag normally goes.
        ann_lang = self._output_language(event.language, event.text or "")
        scrubbed = scrub_for_voice(
            event.text, language=ann_lang, ack_mode=is_preamble
        )
        if scrubbed.actions:
            log.info(
                "🧹 Announcement-Filter [%s]: %s (fallback=%s)",
                ann_lang, scrubbed.actions, scrubbed.fallback_used,
            )
        if not scrubbed.cleaned.strip():
            log.info("Announcement nach Filter leer — schweige.")
            return
        # Duplicate-wording safety net (2026-07-06 interim-ack redesign): no
        # emitter may speak the SAME preamble/progress line twice in quick
        # succession, regardless of source (grounded ack, Flash-Brain, skill) —
        # forensic 2026-07-05: one session spoke the identical grounded ack
        # three times. Only the ephemeral kinds are deduped;
        # completion/interrupt readbacks deliver owed answers and may repeat.
        if is_preamble or is_progress:
            dedup_cfg = getattr(getattr(self, "_config", None), "ack_brain", None)
            dedup_window_s = float(
                getattr(dedup_cfg, "preamble_dedup_window_s", 180) or 0
            )
            spoken_text = scrubbed.cleaned.strip()
            last_spoken = getattr(self, "_last_preamble_spoken", None)
            if (
                dedup_window_s > 0
                and last_spoken is not None
                and last_spoken[0] == spoken_text
                and (time.monotonic() - last_spoken[1]) < dedup_window_s
            ):
                log.info(
                    "Preamble dropped — identical wording spoken %.0fs ago: %r",
                    time.monotonic() - last_spoken[1],
                    event.text[:80],
                )
                return
            # v2 anti-loop backstop: hard cap on spoken preamble/progress
            # lines per rolling 60 s window, ANY source. Kills the historical
            # "kept saying it forever" bug class at the last shared
            # chokepoint. Completion/interrupt readbacks never enter this
            # branch (owed answers are exempt).
            rate_limit = int(
                getattr(dedup_cfg, "preamble_rate_limit_per_min", 3) or 0
            )
            spoken_times = getattr(self, "_preamble_spoken_times", None)
            if spoken_times is None:
                spoken_times = deque(maxlen=32)
                self._preamble_spoken_times = spoken_times
            now_monotonic = time.monotonic()
            if rate_limit > 0:
                recent_count = sum(
                    1 for t in spoken_times if now_monotonic - t < 60.0
                )
                if recent_count >= rate_limit:
                    log.warning(
                        "Preamble dropped — rate-limit backstop (%d spoken in "
                        "the last 60s, cap %d): %r",
                        recent_count, rate_limit, event.text[:80],
                    )
                    return
            self._last_preamble_spoken = (spoken_text, now_monotonic)
            spoken_times.append(now_monotonic)
        # We are now committed to actually speaking this announcement (past every
        # suppression / defer / empty guard). Record it as voice activity so the
        # idle-timeout branch in ``_active_session`` re-arms a fresh window: an
        # out-of-band readback (mission completion/failure) hands the floor back
        # to the user just like an inline answer, and must not be followed by an
        # idle hangup seconds later (live bug 2026-06-18 08:52). Set BEFORE the
        # TTS playback so the grace also covers the readback's own play time.
        self._last_announcement_spoken_monotonic = time.monotonic()
        # Document the announcement in the session log — it is voiced through
        # this bypass path, not _speak, so it would otherwise be invisible.
        # ``detail`` carries an optional technical diagnostic (e.g. a failed
        # Computer-Use exit code + harness reason) that is NOT spoken but is
        # surfaced in the transcript for debugging.
        self._emit_spoken(
            scrubbed.cleaned,
            ann_lang,
            _announcement_spoken_kind(getattr(event, "kind", None)),
            getattr(event, "detail", None),
        )
        # A finished sub-agent / mission readback (completion / subagent) hands
        # the floor BACK to the user — drive the UI/orb into SPEAKING for its
        # duration so the mascot animates. The out-of-band announcement path
        # bypasses the turn-state machine, which is why a readback used to play
        # with no visual "Jarvis is talking" signal (2026-06-19). Only readbacks
        # animate (a preamble / progress nudge keeps its prior visual), and the
        # restore is DETERMINISTIC — IDLE once the user has hung up, else
        # LISTENING (the mic is still open). Not capturing a prior state keeps
        # this race-free against a concurrently-flushed deferred announcement.
        animate = is_readback and self._supervisor is not None
        if animate:
            await self._transition("SPEAKING")
        try:
            # Drive the TTS pin from the SAME resolved language as the scrub,
            # not from event.language again — a None/auto tag here used to send
            # language_code=None, which lets the multilingual TTS (Cartesia)
            # fall back to its English voice on German text (the British-accent
            # symptom; forensic 2026-06-23).
            lang_code = self._bcp47(ann_lang)
            try:
                chunks = self._tts.synthesize(scrubbed.cleaned, language_code=lang_code)
            except TypeError:
                chunks = self._tts.synthesize(scrubbed.cleaned)
            if is_preamble:
                # Staleness gate evaluated by the player right before it writes
                # audio: if the answer has started speaking by the time the
                # preamble's synthesis + play-lock wait completes, drop it so it
                # is never voiced after the answer (2026-06-20 misorder fix).
                # TypeError fallback mirrors the synthesize() compat shim above:
                # an older player / test fake without the should_play kwarg still
                # plays (the synchronous JARVIS_SPEAKING guard already covers the
                # already-speaking case; only the in-flight race is then uncovered).
                try:
                    await self._player.play_chunks(
                        chunks,
                        should_play=lambda: (
                            getattr(self, "_turn_state", TurnTakingState.IDLE)
                            is not TurnTakingState.JARVIS_SPEAKING
                        ),
                    )
                except TypeError:
                    await self._player.play_chunks(chunks)
            else:
                await self._player.play_chunks(chunks)
        except Exception as exc:  # noqa: BLE001
            log.warning("Announcement-Speak fehlgeschlagen: %s", exc)
        finally:
            if animate:
                hungup = hangup is not None and hangup.is_set()
                await self._transition("IDLE" if hungup else "LISTENING")

    async def _await_ack_turn_commit(self, grace_ms: int) -> bool:
        """Poll the turn-state for up to ``grace_ms``; True only if it stays
        PROCESSING throughout — the continuation grace from AD-OE5.

        The Pre-Thinking-Ack is ready a few hundred ms after the VAD endpoint,
        often before the VAD has registered that the user merely paused and kept
        talking. Returning False the instant the turn leaves PROCESSING (the
        continuation interrupt flips it to LISTENING/USER_SPEAKING, or the brain
        answered → JARVIS_SPEAKING) lets the caller drop the ack before it can
        speak over the user.
        """
        step_s = 0.05
        steps = max(1, int(grace_ms / 1000.0 / step_s))
        for _ in range(steps):
            if self._turn_state is not TurnTakingState.PROCESSING:
                return False
            await asyncio.sleep(step_s)
        return self._turn_state is TurnTakingState.PROCESSING

    async def _spawn_flash_brain_ack(self, utterance: str, language: str) -> None:
        """Run the Pre-Thinking-Ack Flash-Brain and publish its output —
        but only when the main brain is still thinking by then.

        User-feedback 2026-05-13: a Flash-Brain ack that lands while the
        main answer is already arriving feels redundant and chatty. The
        ack should ONLY surface when the brain is actually slow. After
        the ack is generated, this task polls ``self._turn_state``
        every 100 ms for up to ``suppress_if_brain_faster_than_ms``; if
        the state has already moved to ``JARVIS_SPEAKING`` or
        ``LISTENING`` (i.e. the brain already started or finished
        speaking), the ack is dropped silently. Only if the brain is
        still in ``PROCESSING`` when the timer expires does the ack get
        published.

        Fire-and-forget — any failure swallows so a Flash-Brain stall
        never blocks the main response path.
        """
        if self._ack_brain is None:
            return

        # Wave 3 (omni-latency): streaming ack path. Speak the first ack
        # sentence the moment it is ready, but ONLY if the main brain has not
        # already started speaking. No post-buffer poll — the ack exists to
        # bridge the wait, so it must not add its own delay. Falls back to the
        # legacy run()+poll path when streaming is disabled or unavailable.
        ack_cfg = getattr(self._config, "ack_brain", None) if self._config else None
        run_stream = getattr(self._ack_brain, "run_stream", None)
        if getattr(ack_cfg, "streaming", False) and run_stream is not None:
            spoke = False
            grace_ms = int(getattr(ack_cfg, "ack_continuation_grace_ms", 1200))
            try:
                async for sentence in run_stream(utterance, language=language):
                    if not sentence:
                        continue
                    # Continuation grace (AD-OE5). The streaming ack is ready
                    # ~700 ms after the VAD endpoint — often BEFORE the VAD has
                    # registered that the user merely paused and is still
                    # talking (live incident 2026-06-17 12:42: the ack spoke
                    # ~795 ms before the continuation was detected). Before the
                    # FIRST audible sentence, poll until the turn leaves
                    # PROCESSING (user resumed → continuation interrupt, or the
                    # brain already answered) — drop the ack then — or the grace
                    # elapses with the turn still committed.
                    if not spoke and grace_ms > 0:
                        if not await self._await_ack_turn_commit(grace_ms):
                            log.info(
                                "Flash-Brain ack suppressed — turn left PROCESSING "
                                "during continuation grace (state=%s)",
                                self._turn_state.name,
                            )
                            return
                    # Gate: the ack ("I'm about to think about this") is only
                    # valid while the turn is STILL thinking about the committed
                    # utterance. Any other state — brain already speaking/done,
                    # or the user (re)speaking — drops it.
                    if self._turn_state is not TurnTakingState.PROCESSING:
                        log.info(
                            "Flash-Brain ack suppressed — turn no longer "
                            "PROCESSING (state=%s)",
                            self._turn_state.name,
                        )
                        return
                    tracker = getattr(self, "_latency_tracker", None)
                    if tracker is not None and not spoke:
                        tracker.mark(LatencyPhase.ACK_FIRST_TOKEN)
                    try:
                        await self._publish_event(
                            AnnouncementRequested(
                                source_layer="brain.ack_brain",
                                text=sentence,
                                priority="normal",
                                language=language,
                                kind="preamble",
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Flash-Brain ack publish failed: %s", exc)
                    spoke = True
            except Exception as exc:  # noqa: BLE001
                log.warning("Flash-Brain ack stream raised: %s", exc)
            return

        try:
            ack = await self._ack_brain.run(utterance, language=language)
        except Exception as exc:  # noqa: BLE001
            log.warning("Flash-Brain ack task raised: %s", exc)
            return
        if not ack:
            return  # silent — generator decided to suppress (any of F1-F10)

        # Suppress-if-fast gate. Read threshold from config (or
        # fallback to 2000 ms if the runtime is wired without one).
        suppress_ms = 2000
        try:
            cfg_ack = getattr(self._config, "ack_brain", None) if self._config else None
            if cfg_ack is not None:
                suppress_ms = int(
                    getattr(cfg_ack, "suppress_if_brain_faster_than_ms", suppress_ms)
                )
        except Exception:  # noqa: BLE001
            pass

        if suppress_ms > 0:
            # Poll the turn-state until either the brain has moved on
            # (drop the ack) or the threshold has elapsed (publish).
            poll_step_s = 0.1
            poll_steps = max(1, int(suppress_ms / 1000.0 / poll_step_s))
            for _ in range(poll_steps):
                await asyncio.sleep(poll_step_s)
                # Drop the ack the instant the turn leaves PROCESSING — the
                # brain answered (JARVIS_SPEAKING) OR the user resumed
                # (USER_SPEAKING / LISTENING). The latter is the AD-OE5
                # continuation guard the streaming path enforces via the grace.
                if self._turn_state is not TurnTakingState.PROCESSING:
                    log.info(
                        "Flash-Brain ack suppressed — turn no longer PROCESSING "
                        "within %d ms (state=%s)",
                        suppress_ms,
                        self._turn_state.name,
                    )
                    return

        try:
            await self._publish_event(
                AnnouncementRequested(
                    source_layer="brain.ack_brain",
                    text=ack,
                    priority="normal",
                    language=language,
                    kind="preamble",
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Flash-Brain ack publish failed: %s", exc)

    async def _on_background_completed(
        self, event: JarvisAgentBackgroundCompleted
    ) -> None:
        """Proaktive Voice-Ansage wenn ein Background-OpenClaw-Task fertig wird.

        User-Wunsch 2026-05-11 (Bug-Report Voice-Spawn-Latenz): Completion-
        Voice-Meldung soll hoerbar sein, damit der User auch dann Bescheid
        weiss, wenn er zwischenzeitlich anderes gemacht hat. Phrasen sind
        fix + ohne "Sir" + ohne Engineering-Jargon, damit der Output-Filter
        (``scrub_for_voice``) nicht den Spruch komplett wegwirft.

        Vorher (2026-04-25 .. 2026-05-10) war dieser Pfad mit einem fruehen
        ``return`` suppress't — Wunsch damals war "keine standardisierten
        Bestaetigungs-Phrasen". 2026-05-11 widerrufen.

        CRIT-5 (2026-05-17): cancel the oldest pending spawn-watchdog --
        FIFO matches the sequential dispatch model. If the watchdog has
        already fired and emitted "Bin noch dran.", the cancel is a
        cheap no-op.
        """
        if self._spawn_watchdog_tasks:
            task = self._spawn_watchdog_tasks.pop(0)
            if not task.done():
                task.cancel()
        if getattr(self, "_muted", False):
            log.debug("Background-completed announcement suppressed — voice muted")
            return
        # WS3b (live bug 2026-06-14): an OpenClaw mission that completes AFTER
        # the user hung up still owes them its result. This readback is a FRESH
        # turn (the answer they asked for), not a stale leftover from the aborted
        # turn, so it must NOT be dropped by the hangup gate (AD-OE6
        # zero-silent-drop). The mission ran in its own subprocess + Job Object;
        # hangup never killed it. The mute guard above still silences a
        # deliberately-muted session, and the phrases below stay priority
        # "normal" → queued behind any current speech, never barging
        # mid-utterance (AD-OE5).
        # Resolve the readback language from the original request utterance,
        # falling back to the worker summary text (pin > conversation-stickiness
        # > detected utterance/summary > default) so an English/Spanish user
        # never hears "Fertig." in German, and the "Done./Fertig." prefix never
        # mismatches the summary language (forensic 2026-06-23: announcement
        # emitters bypassed the resolver).
        lang = self._output_language(
            None,
            getattr(event, "utterance", "") or getattr(event, "summary", "") or "",
        )
        ph = self._BG_READBACK_PHRASES.get(lang, self._BG_READBACK_PHRASES["en"])
        if event.success and event.summary:
            summ = event.summary.strip()
            if len(summ) > 200:
                summ = summ[:200].rsplit(" ", 1)[0] + "…"
            text = ph["done_summ"].format(s=summ)
        elif event.success:
            text = ph["done"]
        else:
            err_short = (event.error or ph["unknown_err"])[:80]
            text = ph["fail"].format(e=err_short)
        # Defense-in-Depth: Summary/Error kann aus dem OpenClaw-Pfad kommen
        # und Engineering-Tokens (Sub-Agent, Subprocess, MCP) enthalten.
        # scrub_for_voice filtert die raus, sonst leakt Worker-Mechanik
        # in den Voice-Kanal (vgl. Mandat-Pfad #2 Output-Filter).
        scrubbed = scrub_for_voice(text, language=lang)
        if scrubbed.actions:
            log.info(
                "🧹 Background-Filter: %s (fallback=%s)",
                scrubbed.actions, scrubbed.fallback_used,
            )
        cleaned = scrubbed.cleaned.strip()
        if not cleaned:
            log.info(
                "OpenClaw background fertig — Ansage nach Filter leer, schweige."
            )
            return
        log.info(
            "OpenClaw background fertig (success=%s, dauer=%.1fs) — Ansage: %r",
            event.success, event.duration_s, cleaned,
        )
        # AD-OE5: this path plays straight to the player, bypassing
        # ``_on_announcement``. If the user holds the floor, park the readback
        # as a completion announcement and let the turn-boundary flush replay it
        # through the choke point (which then emits it to the session log + plays
        # it). Returning here means it is neither logged-as-spoken nor played
        # until the floor clears — no barge, no double-emit.
        if getattr(self, "_turn_state", TurnTakingState.IDLE) in _USER_HOLDS_FLOOR_STATES:
            self._deferred_announcements.append(
                AnnouncementRequested(
                    source_layer="harness.openclaw.background",
                    text=cleaned,
                    language=lang,
                    priority="normal",
                    kind="subagent",
                )
            )
            log.info(
                "Background completion deferred — user holds the floor: %r",
                cleaned[:80],
            )
            return
        # Document the sub-agent readback in the session log — it is voiced
        # through this background path, not _speak, so it would otherwise be
        # invisible in the Transcription view. Tagged ``subagent`` so it renders
        # on the attributed "Jarvis Sub-Agent / Output" track.
        self._emit_spoken(cleaned, lang, SPOKEN_KIND_SUBAGENT)
        # Re-arm the readback grace exactly like ``_on_announcement`` (:2386): a
        # background result delivered through THIS direct path also hands the
        # floor back to the user, so ``_active_session`` must keep the mic open
        # afterward instead of idle-hanging-up seconds later (2026-06-19).
        self._last_announcement_spoken_monotonic = time.monotonic()
        # Laufendes Playback stoppen damit die Ansage prompt durchkommt.
        try:
            self._player.stop()
        except Exception as exc:  # noqa: BLE001
            log.warning("Player-Stop vor Background-Ansage fehlgeschlagen: %s", exc)
        # Animate the mascot/orb for this readback too (same as _on_announcement);
        # restore DETERMINISTICALLY afterward — IDLE if the user hung up, else
        # LISTENING. No prior-state capture, so this never races a concurrently
        # flushed deferred announcement.
        animate = self._supervisor is not None
        hangup = getattr(self, "_hangup_event", None)
        if animate:
            await self._transition("SPEAKING")
        try:
            try:
                chunks = self._tts.synthesize(cleaned, language_code=self._bcp47(lang))
            except TypeError:
                chunks = self._tts.synthesize(cleaned)
            await self._player.play_chunks(chunks)
        except Exception as exc:  # noqa: BLE001
            log.warning("Background-completed Voice-Ansage failed: %s", exc)
        finally:
            if animate:
                hungup = hangup is not None and hangup.is_set()
                await self._transition("IDLE" if hungup else "LISTENING")

    async def _on_spawn_announcement(self, event: JarvisAgentAnnouncement) -> None:
        """Spawn-ACK ist auf User-Wunsch (2026-05-12) deaktiviert.

        History:
            2026-04-25 .. 2026-05-10 — Pfad war stumm (User-Wunsch).
            2026-05-11 — kurz reaktiviert mit fixer Phrase "Okay, mache ich."
                         weil der User ein Voice-Feedback wollte um stille
                         Timeouts vom erfolgreichen Spawn unterscheiden zu
                         koennen.
            2026-05-12 — User widerruft. Jede Spawn-ACK-Phrase nervt; der
                         User unterscheidet Spawn vs. Timeout jetzt visuell
                         (Sub-Agents-Board) und ueber den Background-
                         Completed-Voice-Readback am Ende der Mission.

        Der Bus-Event selbst (``OpenClawAnnouncement``) wird in
        ``spawn_worker.py`` weiterhin publisht und vom UI gelesen — wir
        unterdruecken hier nur den Voice-Pfad. Cleanup-Logging behalten wir
        einmalig pro Event, damit man im Log noch sehen kann dass der ACK
        absichtlich ueber-sprungen wurde (debug-friendly bei spaeteren
        Voice-Bug-Reports).
        """
        log.info(
            "Spawn-ACK suppress't (User-Wunsch 2026-05-12) — action=%r target=%r",
            event.action, event.target,
        )
        # CRIT-5 (2026-05-17) + heartbeat rework (2026-06-19): schedule the
        # background-mission heartbeat so the user hears a varied, language-
        # resolved "still on it" reassurance while a long mission runs (first
        # beat well before the old 90 s, then bounded). _on_background_completed
        # cancels it on the crash path; a completion readback cancels it via
        # _cancel_spawn_heartbeats in the happy path.
        self._schedule_spawn_watchdog()
        return

    def _schedule_spawn_watchdog(self) -> None:
        """Start the background-mission heartbeat: a bounded series of varied,
        language-resolved "still on it" reassurances while the mission has not
        completed (see ``_spawn_watchdog_body``). FIFO-cancelled by
        ``_on_background_completed``; also cancelled on a completion readback via
        ``_cancel_spawn_heartbeats``."""
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            self._spawn_watchdog_body(),
            name=f"spawn-watchdog-{len(self._spawn_watchdog_tasks)}",
        )
        self._spawn_watchdog_tasks.append(task)

    def _live_spawn_watchdogs(self) -> list[asyncio.Task[None]]:
        """Drop finished spawn-watchdog tasks; return the still-live ones.

        A watchdog counts as a "background mission in flight" only while it is
        still running its bounded heartbeat sequence. Once the sequence is
        exhausted (or it is cancelled) it is ``done()`` and must no longer hold the voice
        session open — otherwise the idle-timeout override in ``_active_session``
        and the keep-listening branch in ``_finish_after_response`` would keep
        the session in LISTENING *forever* after a force-spawn. In production the
        success path never publishes ``OpenClawBackgroundCompleted`` (the
        readback travels the MissionAnnouncer → ``AnnouncementRequested`` path,
        and ``_on_background_completed`` — the only code that pops the list —
        fires solely on the crash path), so the list is otherwise never drained.
        Pruning bounds the in-flight extension to the watchdog lifetime.
        """
        self._spawn_watchdog_tasks[:] = [
            t for t in self._spawn_watchdog_tasks if not t.done()
        ]
        return self._spawn_watchdog_tasks

    def _background_mission_in_flight(self) -> bool:
        """True while anything is still working for the user in the background:
        an OpenClaw spawn watchdog counting down OR a live Computer-Use
        mission.

        Consumed by the idle-timeout branch in ``_active_session`` and the
        single-turn hangup decision in ``_finish_after_response`` so the voice
        session does not hang up mid-mission (live bug 2026-06-10: the idle
        timeout fired 40 s into a running CU mission; the mission kept
        clicking invisibly for two more minutes and spoke its failure
        announcement into a dead session). Bounded on both legs: watchdogs
        self-remove after their bounded heartbeat sequence, and the CU token is
        cleared in the harness ``finally`` with a hard mission deadline."""
        if self._live_spawn_watchdogs():
            return True
        try:
            from jarvis.harness.computer_use_context import (  # noqa: PLC0415
                cu_mission_active,
            )

            return cu_mission_active()
        except Exception:  # noqa: BLE001 — probe must never break the session
            return False

    def _pick_heartbeat_phrase(self) -> tuple[str, str]:
        """Pick one varied "still on it" heartbeat phrase + its language.

        Language flows through the single output-language resolver
        (``_output_language``: ``brain.reply_language`` pin > sticky
        conversation language > ``DEFAULT_LOCALE``) — never hard-coded "de", so
        a German / English / Spanish conversation hears the heartbeat in its own
        language. The phrase comes from the varied ``STILL_RUNNING_PHRASES`` pool
        with a small no-repeat guard so consecutive beats differ. The pool lives
        in the allowlisted spawn-announcement module (keeps the German/Spanish
        runtime strings out of this file); a lazy import + neutral fallback keeps
        the spoken path crash-proof.
        """
        lang = _phrase_lang(self._output_language(None, ""))
        try:
            from jarvis.brain.ack_brain.spawn_announcement import (  # noqa: PLC0415
                STILL_RUNNING_PHRASES,
            )

            pool = STILL_RUNNING_PHRASES.get(lang) or STILL_RUNNING_PHRASES["en"]
        except Exception:  # noqa: BLE001 — the heartbeat must never crash the loop
            return ("Still working on it.", lang)
        recent = getattr(self, "_heartbeat_recent", None)
        choices = [p for p in pool if recent is None or p not in recent] or list(pool)
        choice = random.choice(choices)  # noqa: S311 — phrase variety, not crypto
        if recent is not None:
            recent.append(choice)
        return (choice, lang)

    async def _spawn_watchdog_body(self) -> None:
        """Speak a bounded, varied, language-resolved "still on it" heartbeat
        while a background mission runs.

        Replaces the old one-shot, German-only "Bin noch dran." (2026-06-19):
        the first beat fires after ``_spawn_watchdog_delay_s`` (90 s of silence
        read as a crash), then up to ``_heartbeat_max_count`` total,
        ``_heartbeat_interval_s`` apart, so the wait feels alive instead of dead.
        Each beat is picked fresh (``_pick_heartbeat_phrase``) and emitted as a
        ``priority="normal"`` ``AnnouncementRequested`` — which the AD-OE5 floor
        guard in ``_on_announcement`` drops if the user holds the floor (never
        speaks over the user). A muted session stays silent. ``CancelledError``
        (mission finished / completion readback) exits quietly.

        On EVERY terminal path the task removes itself from
        ``_spawn_watchdog_tasks``. That list is the "background mission in flight"
        signal read by ``_active_session``'s idle-timeout override and by
        ``_finish_after_response``; a done-but-still-listed task would hold the
        voice session open forever, because the success path never publishes the
        ``OpenClawBackgroundCompleted`` event that would otherwise drain it. The
        hard cap bounds the in-flight hold to the heartbeat lifetime.
        """
        try:
            max_count = max(1, getattr(self, "_heartbeat_max_count", 3))
            interval = getattr(self, "_heartbeat_interval_s", 60.0)
            delay = self._spawn_watchdog_delay_s
            for beat in range(1, max_count + 1):
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    return
                delay = interval
                if getattr(self, "_muted", False):
                    log.debug("Spawn-heartbeat: muted, skipping beat %d", beat)
                    continue
                if self._bus is None:
                    return
                phrase, lang = self._pick_heartbeat_phrase()
                log.info(
                    "Spawn-heartbeat #%d (mission still running) — %r (%s)",
                    beat, phrase, lang,
                )
                try:
                    await self._bus.publish(
                        AnnouncementRequested(
                            text=phrase,
                            language=lang,
                            priority="normal",
                            # "progress" = droppable when stale: if the user
                            # holds the floor when this lands, _on_announcement
                            # DROPS it (never defers/replays a stale "still on
                            # it" after the user finishes or after the answer).
                            kind="progress",
                        )
                    )
                except Exception:  # noqa: BLE001
                    log.warning(
                        "Spawn-heartbeat: AnnouncementRequested publish failed",
                        exc_info=True,
                    )
        finally:
            # Self-remove on every exit. _on_background_completed may have
            # already popped this task (FIFO cancel) — remove() is then a
            # harmless no-op (ValueError swallowed).
            me = asyncio.current_task()
            try:
                self._spawn_watchdog_tasks.remove(me)
            except ValueError:
                pass

    def _cancel_spawn_heartbeats(self) -> None:
        """Cancel every pending spawn heartbeat.

        Called when a mission delivers its actual answer (a readback —
        ``kind="completion"`` or ``kind="subagent"``) so Jarvis never says "still
        on it" right AFTER the result. The
        success path does not publish ``OpenClawBackgroundCompleted``, so the
        heartbeat is otherwise only drained by its own cap; this is the precise
        hook that silences it the moment the answer lands. Each cancelled task
        still self-removes from ``_spawn_watchdog_tasks`` in its ``finally``.
        """
        tasks = getattr(self, "_spawn_watchdog_tasks", None)
        if not tasks:
            return
        for task in list(tasks):
            if not task.done():
                task.cancel()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        await self._warmup()
        # Permanent-Vision: Background-Refresh-Loop hier im Pipeline-Event-Loop
        # starten. Ohne das kriegt `VisionContextProvider.current()` nie einen
        # gecachten Frame und der Router-Brain sieht den Screen nicht. Fehler
        # beim Start duerfen die Voice-Session nicht toeten — Text-Only-
        # Fallback greift weiter.
        if self._vision_provider is not None:
            try:
                await self._vision_provider.start()
                log.info("VisionContextProvider Background-Loop gestartet.")
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "VisionContextProvider.start() fehlgeschlagen — "
                    "Router laeuft ohne Screen-Kontext: %s",
                    exc,
                    exc_info=True,
                )
        hotkey_bindings = {
            "call": list(self._call_hotkeys),
            "hangup": list(self._hangup_hotkeys),
        }
        # Push-to-talk binding (both key edges) — only when configured. Kept as
        # its own event so the configured wake hotkey can be true PTT while
        # F3+F4 stays a quick toggle (a two-F-key chord is awkward to hold).
        ptt_events: set[str] = set()
        if self._ptt_hotkeys:
            hotkey_bindings["ptt"] = list(self._ptt_hotkeys)
            ptt_events.add("ptt")
        log.info(
            "Pipeline bereit. CALL=[%s] PTT=[%s] HANGUP=[%s] OWW=%s WAKE=%s (threshold=%.2f) "
            "WHISPER-WAKE=%s TURN-MODE=%s",
            ", ".join(self._call_hotkeys),
            ", ".join(self._ptt_hotkeys) or "off",
            ", ".join(self._hangup_hotkeys),
            "on" if self._openwakeword_enabled else "off",
            list(self._wake._keywords),
            self._wake._threshold,
            "on" if self._whisper_wake_enabled else "off",
            # Observability for the single-turn vs conversation pendulum: the
            # mode was previously invisible in the log, which made it impossible
            # to tell from telemetry whether a `turn_complete` hangup was the
            # configured single-turn behavior or a regression. See
            # feedback_voice_session_mode + BUG-009-style env-propagation traps.
            "conversation (until 'auflegen'/idle/hotkey)"
            if self._continue_listening_after_response
            else "single-turn (fresh wake per turn)",
        )
        def _log_task_exit(task: asyncio.Task) -> None:
            # Asyncio verschluckt Task-Exceptions sonst silent — wir hatten
            # genau diesen Bug 2026-04-26 (Mic-Open mit ungueltiger Sample-Rate
            # killte den Wake-Task ohne ein einziges Log).
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                log.error(
                    "Pipeline-Task '%s' beendet mit Exception: %s",
                    task.get_name(),
                    exc,
                    exc_info=exc,
                )

        async with HotkeyTrigger(hotkey_bindings, push_to_talk=ptt_events) as trigger:
            hotkey_task = asyncio.create_task(self._hotkey_loop(trigger), name="hotkey")
            hotkey_task.add_done_callback(_log_task_exit)
            # Live keybind re-arm: set_keybinds() flips _hotkey_reload_event and
            # this task re-registers the new combos in place (no app restart).
            hotkey_reload_task = asyncio.create_task(
                self._hotkey_reload_loop(trigger), name="hotkey-reload"
            )
            hotkey_reload_task.add_done_callback(_log_task_exit)
            wake_task = (
                asyncio.create_task(self._wake_loop(), name="wake")
                if self._wake_listening_enabled()
                else None
            )
            if wake_task is not None:
                wake_task.add_done_callback(_log_task_exit)
            else:
                log.info("Wake-Listener deaktiviert; Mikrofon bleibt bis zum Hotkey-Call geschlossen.")
            main_task = asyncio.create_task(self._state_loop(), name="state")
            main_task.add_done_callback(_log_task_exit)

            # Skills-Brain-Integration: Cron-Scheduler-Task starten wenn Skills bereit.
            # Wenn kein SkillContext gesetzt ist, ueberspringen wir den Cron-Pfad
            # ohne Fehler (Headless-Mode, Tests).
            self._cron_stop.clear()
            cron_ctx = try_get_skill_context()
            if cron_ctx is not None:
                self._cron_task = asyncio.create_task(
                    self._skill_cron_loop(self._cron_stop), name="skill-cron"
                )
                log.info("Skill-Cron-Scheduler aktiv.")

            try:
                await main_task
            finally:
                self._cron_stop.set()
                tasks_to_cancel: list[asyncio.Task] = [hotkey_task, hotkey_reload_task]
                if wake_task is not None:
                    tasks_to_cancel.append(wake_task)
                if self._cron_task is not None:
                    tasks_to_cancel.append(self._cron_task)
                for t in tasks_to_cancel:
                    t.cancel()
                for t in tasks_to_cancel:
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
                if self._vision_provider is not None:
                    try:
                        await self._vision_provider.stop()
                    except Exception as exc:  # noqa: BLE001
                        log.debug("VisionContextProvider.stop() swallow: %s", exc)
                self._cron_task = None
                # Phase-B confirmation-audio render is fire-and-forget; cancel it
                # here so it is never orphaned past pipeline shutdown.
                await self._cancel_warmup_background()

    async def _emit_boot_status(self, *, ready: bool, detail: str = "") -> None:
        """Publish a VoiceBootStatus on the bus (guarded — never breaks boot).

        ``ready=False`` is emitted at the very start of warm-up; ``ready=True``
        only once ALL deferred loaders (wake model + VAD + TTS client) have
        completed, from ``_warmup_deferred_loaders`` — the first honest moment
        the user can both be heard AND get a spoken reply. (It runs after Phase A
        has returned, so the wake loop is already listening by then.)
        """
        if self._bus is None:
            return
        try:
            await self._bus.publish(VoiceBootStatus(ready=ready, detail=detail))
        except Exception as exc:  # noqa: BLE001 — status signal never breaks boot
            log.warning("VoiceBootStatus(ready=%s) publish failed: %s", ready, exc)

    async def _cancel_warmup_background(self) -> None:
        """Cancel + await the fire-and-forget warm-up tasks on shutdown.

        Three background tasks run off the wake-critical path: Phase B (the ACK +
        task-ack confirmation-audio pre-render), the deferred wake/VAD/TTS
        loaders (``_warmup_deferred_loaders``), and the boot-ready audio cue
        (``_warmup_ready_cue_task``, created at the end of the deferred loaders).
        When ``run()`` is cancelled (the normal desktop-shutdown path) all must
        be cancelled and awaited, otherwise they are orphaned: under
        ``pythonw.exe`` there is no stderr to surface the "Task exception was
        never retrieved" warning, and a late TTS render could write stale PCM
        into ``_ack_pcm`` / ``_task_ack_pcm`` after a live TTS-provider switch
        already cleared them. Guarded so shutdown never raises.
        """
        for attr in (
            "_warmup_background_task",
            "_warmup_ready_cue_task",
            "_deferred_warmup_task",
        ):
            task = getattr(self, attr, None)
            if task is None:
                continue
            if not task.done():
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 — shutdown swallow
                pass
            setattr(self, attr, None)

    async def _warmup(self) -> None:
        log.info("Warm-up: Whisper / Silero / Wake-Word / TTS …")
        await self._emit_boot_status(ready=False, detail="warmup_start")

        # --- Phase A: start the wake LOOP, nothing else -------------------
        # Phase A blocks on the absolute minimum so the wake loop is listening
        # fast: the audio-device table settling (BUG-014 guard) and the wake
        # start. The heavy VAD / STT / TTS loads are deliberately NOT here — they
        # move to the background ``_warmup_deferred_loaders`` so they never gate
        # wake-loop start. NOTE: a started wake loop is NOT the same as "ready to
        # converse"; honest readiness (incl. TTS) is signalled later, from the
        # deferred loaders (see below).
        phase_a_start = time.monotonic()
        await self._warmup_phase_a()
        phase_a_ms = (time.monotonic() - phase_a_start) * 1000.0
        log.info("Warm-up Phase A (critical listening path) done in %.0f ms.", phase_a_ms)
        # Honest readiness (2026-06-29): Phase A only starts the wake LOOP — it
        # does NOT mean the user can hold a conversation yet (VAD + TTS, and the
        # custom-wake Whisper model, are still loading in
        # _warmup_deferred_loaders below). Readiness is therefore NOT signalled
        # here. BOTH the VoiceBootStatus(ready=True) and the audible "you can
        # speak" cue now fire at the END of _warmup_deferred_loaders — the first
        # moment wake (model) + VAD + TTS are ALL genuinely up. Previously the
        # openWakeWord path flipped ready in Phase A and the custom-phrase path
        # flipped it right after the wake model alone — either way the UI said
        # "ready" while TTS was still loading, so the user spoke and nothing came
        # back ("it says ready but I can't talk"). ready=False was already
        # emitted at warmup_start above, so the UI stays in its honest
        # "starting up / preparing to listen" state until the deferred loaders
        # complete.

        # --- Phase B: confirmation audio, off the critical path -----------
        # Pre-rendering the ACK + ~20 task-ack phrases used to dominate warm-up
        # (~20 sequential TTS round-trips). Fire it as a background task so it
        # never delays the ready signal; if the wake word fires before the ACK
        # is cached, the chime above plays instead.
        self._warmup_background_task = asyncio.create_task(
            self._warmup_phase_b(), name="warmup-confirmation-audio"
        )
        log.info("Warm-up Phase A complete — confirmation audio rendering in background.")

    def _log_warmup_ready_cue_done(self, task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            log.warning("Warm-up ready cue failed: %s", exc)

    async def _warmup_phase_a(self) -> None:
        """Bring up ONLY the wake-critical listening path, then return.

        The wake loop is started the moment this returns, so it must block on
        the absolute minimum: the audio-device table settling (BUG-014 guard)
        and the OpenWakeWord model starting. That is everything the wake path
        needs to hear "Hey Jarvis" and spawn the orb.

        The heavy VAD / STT / TTS-client loads are deliberately NOT here. Each
        of them lazy-imports a large C-extension (onnxruntime for the wake model
        and Silero-VAD, ctranslate2 for local Whisper); run concurrently inside
        the boot storm they serialize on the Python import lock and starve to
        7-24 s (measured: ``wake-start=14187, vad-load=12672``). Because the wake
        loop used to start only after the WHOLE warm-up finished, gating it on
        those loads left the wake word dead for that entire window — the
        "~10 s delay / nothing spawns" regression. They are only needed AFTER a
        wake and are lazy-safe (each object re-ensures its model on first use),
        so they move to a background deferred task (``_warmup_deferred_loaders``)
        that never gates wake readiness.
        """
        async def _start_wake() -> None:
            if self._openwakeword_enabled:
                await self._wake.start()

        # return_exceptions=True (inside _gather_timed): a slow/failing loader
        # must not abort the other. The per-loader timing exposes which one
        # dominates the wake-critical warm-up (boot-perf forensic).
        timings, results = await _gather_timed(
            [
                ("audio-stabilize", self._stabilize_audio_devices),
                ("wake-start", _start_wake),
            ]
        )
        self._warmup_phase_a_timings = timings
        log.info(
            "Warm-up Phase A (wake-critical) per-loader (ms): %s",
            ", ".join(
                f"{name}={timings[name]:.0f}"
                for name in sorted(timings, key=lambda n: -timings[n])
            ),
        )
        for name, res in zip(("audio-stabilize", "wake-start"), results, strict=True):
            if isinstance(res, Exception):
                log.warning("Warm-up Phase A task '%s' failed: %s", name, res)

        # Heavy model loads move off the wake-ready path — fire-and-forget so a
        # wake can fire (and the orb spawn) while they are still loading. A wake
        # that arrives first simply triggers a one-off lazy load on the VAD/STT
        # object instead of waiting out the whole warm-up. Cancelled on shutdown
        # via ``_cancel_warmup_background``.
        self._deferred_warmup_task = asyncio.create_task(
            self._warmup_deferred_loaders(), name="warmup-deferred-loaders"
        )

    async def _warmup_deferred_loaders(self) -> None:
        """Background pre-load of the FULL conversational stack (wake model +
        VAD + TTS), then signal HONEST readiness.

        Runs OFF the wake-critical path (see ``_warmup_phase_a``): the wake loop
        is already listening by the time this starts, so a wake mid-load just
        triggers a one-off lazy load on the VAD/STT/TTS object. What this owns is
        the *honest* readiness signal — ``VoiceBootStatus(ready=True)`` and the
        audible "you can speak" cue fire ONLY at the very end, once wake (model),
        VAD endpointing AND the TTS reply path are all initialized. Flipping
        ready before TTS was up was the "it says ready but I can't talk" bug
        (2026-06-29).

        Ordering / GIL: the wake-model load is the one heavy CUDA/GIL step, kept
        FIRST and ALONE (racing it against the VAD load serialized both on the
        import + CUDA-init lock to ~11.8 s — forensic 2026-06-22). The TTS client
        init is network-bound (releases the GIL on I/O), so it is kicked off
        CONCURRENTLY with the wake warm-up to overlap its latency instead of
        paying it sequentially afterwards. VAD loads after the wake model. Every
        load is guarded so one slow/failing load never strands readiness — it
        will lazy-load / fall back on first use.
        """
        deferred_t0 = time.monotonic()

        # TTS client init is network-bound → overlap it with the (CUDA-heavy)
        # wake warm-up rather than adding it on afterwards. Idempotent: a later
        # lazy ``_ensure_client`` on first synth is a harmless no-op.
        async def _init_tts() -> None:
            await asyncio.to_thread(self._tts._ensure_client)

        tts_task = asyncio.create_task(_init_tts(), name="warmup-tts-init")
        try:
            # PRIORITY: pre-warm the WAKE model FIRST and ALONE. For a custom wake
            # phrase (stt_match / rolling-whisper) ``self._stt`` IS the wake model,
            # and until it is in memory + warmed the wake loop cannot transcribe.
            # Prime with one REAL inference (not just the model load): the first
            # transcribe is cold (CUDA kernel JIT / cuDNN algo search), and that
            # cost used to land on the user's first "Hey Jarvis" (swallowed wake,
            # forensic 2026-06-28). ``warm_up`` pays it here; falls back to
            # ``_ensure_model``.
            if self._stt is not None:
                _wake_t0 = time.monotonic()
                try:
                    _prime = getattr(self._stt, "warm_up", None)
                    await asyncio.to_thread(
                        _prime if callable(_prime) else self._stt._ensure_model
                    )
                    log.info(
                        "Wake-model pre-warm done in %.0f ms (priority, no GIL race).",
                        (time.monotonic() - _wake_t0) * 1000.0,
                    )
                except Exception as exc:  # noqa: BLE001 — lazy load on first use still works
                    log.warning("Wake-model pre-warm failed (will lazy-load): %s", exc)

            # VAD after the wake model (both touch heavy C-extensions; serialize
            # to dodge the import/CUDA-init lock contention measured above).
            _vad_t0 = time.monotonic()
            try:
                await asyncio.to_thread(self._vad._ensure_model)
                log.info("VAD load done in %.0f ms.", (time.monotonic() - _vad_t0) * 1000.0)
            except Exception as exc:  # noqa: BLE001 — lazy load on first use still works
                log.warning("Warm-up deferred loader 'vad-load' failed: %s", exc)

            # Join the concurrently-running TTS init (started before the wake warm).
            tts_err: Exception | None = None
            try:
                await tts_task
            except Exception as exc:  # noqa: BLE001 — lazy load on first synth still works
                tts_err = exc
                log.warning("Warm-up deferred loader 'tts-init' failed: %s", exc)

            log.info(
                "Warm-up deferred loaders done in %.0f ms (tts-init=%s).",
                (time.monotonic() - deferred_t0) * 1000.0,
                "ok" if tts_err is None else "failed",
            )

            # --- HONEST READINESS: the full stack is up (or attempted) ------
            # Wake model warmed + VAD loaded + TTS client init done → the user
            # can now actually be heard AND get a spoken reply. Signal ready and
            # play the audible cue here, exactly once, at the first truthful
            # moment. Even on a failed VAD/TTS load we still flip ready (each
            # lazy-loads / falls back on first use) rather than stranding the UI
            # in "starting up" forever. The cue is fire-and-forget so a
            # slow/wedged output device never sits between ready=True and a
            # responsive wake loop.
            await self._emit_boot_status(ready=True, detail="listening")
            self._warmup_ready_cue_task = asyncio.create_task(
                self._play_ready_cue(), name="warmup-ready-cue"
            )
            self._warmup_ready_cue_task.add_done_callback(self._log_warmup_ready_cue_done)
        finally:
            # Never orphan the concurrently-started TTS init: if this deferred
            # task is cancelled (desktop shutdown) before the ``await tts_task``
            # join above is reached, CancelledError propagates straight here. Cancel
            # + await it so a late ``_ensure_client`` failure can't surface as an
            # unretrieved-exception warning under pythonw.exe. No-op on the normal
            # path (tts_task already awaited → done). Swallowed; re-raises the
            # original CancelledError after cleanup.
            if not tts_task.done():
                tts_task.cancel()
                try:
                    await tts_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001 — shutdown swallow
                    pass

    async def _warmup_phase_b(self) -> None:
        """Background pre-render of confirmation audio (never blocks ready).

        Order matters: the wake ACK "Ja?" is rendered first (highest priority —
        it is the immediate wake feedback), then the task-ack phrases are
        rendered concurrently. Fully guarded; any failure degrades to the chime.
        """
        bg_start = time.monotonic()
        await self._prerender_ack_phrase()
        await self._prerender_task_acks()
        bg_ms = (time.monotonic() - bg_start) * 1000.0
        log.info("Warm-up Phase B (confirmation audio) done in %.0f ms.", bg_ms)

    async def _prerender_ack_phrase(self) -> None:
        """Cache the wake-ACK phrase ("Ja?") PCM, or fall back to the chime."""
        # Skip the ACK pre-render when the phrase is empty (user preference: no
        # spoken wake reaction, chime only). Gemini-TTS would raise an API error
        # on "" anyway.
        if not self._ack_phrase:
            log.info("ACK phrase disabled — chime only on wake.")
            self._ack_pcm = b""
            return
        try:
            log.info("Pre-rendering ACK phrase '%s' …", self._ack_phrase)
            chunks: list[AudioChunk] = []
            async for c in self._tts.synthesize(self._ack_phrase):
                chunks.append(c)
            self._ack_pcm = b"".join(c.pcm for c in chunks)
            log.info("ACK phrase cached (%d KB).", len(self._ack_pcm) // 1024)
        except Exception as exc:  # noqa: BLE001
            log.warning("ACK pre-render failed (%s) — chime only as feedback.", exc)
            self._ack_pcm = b""

    async def _stabilize_audio_devices(self) -> None:
        """Wait for the audio device enumeration to settle, then re-resolve the
        output device against the now-fresh PortAudio table.

        Permanent cure for the post-reboot device-index drift (BUG-014 class):
        Jarvis can autostart before Windows finishes enumerating audio
        endpoints, freezing a partial table that points the speaker index at a
        stale/silent device. Fully guarded — must never block or break boot.
        """
        try:
            # If the launcher prefetched the (blocking ~1.5 s) device settle in a
            # daemon thread at boot, reuse its already-settled result instead of
            # re-paying the poll wait on the wake-critical path. Falls back to a
            # fresh settle when no prefetch ran / it is still polling — identical
            # to today's behavior, so this can only help, never slow boot.
            from jarvis.audio.device_init import get_prefetched_audio_result

            info = get_prefetched_audio_result()
            if info is None:
                info = await asyncio.to_thread(wait_for_stable_audio_devices)
            log.info(
                "Audio-Geräte stabilisiert: %d Geräte (stable=%s, %.1fs, %d reinit).",
                info.get("device_count", 0),
                info.get("stable"),
                info.get("waited_s", 0.0),
                info.get("reinits", 0),
            )
            self._player.set_device(self._output_device)
        except Exception as exc:  # noqa: BLE001 — audio robustness never breaks boot
            log.warning("Audio-Geräte-Stabilisierung übersprungen (%s).", exc)

    async def _play_ready_cue(self) -> None:
        """Play the ascending boot-ready tone once. Silent no-op on a headless
        VPS / when no output device exists, or when the global "Sound effects"
        switch is off — never raises."""
        await self._play_earcon(READY_PCM)

    async def _prerender_task_acks(self) -> None:
        phrases = iter_all_start_ack()
        log.info("Pre-rendere %d Task-Ack-Phrasen …", len(phrases))

        async def _render_one(lang: str, phrase: str) -> bool:
            """Render + cache one phrase. Returns True on a non-empty cache.

            Each call writes a distinct ``(lang, phrase)`` key into the shared
            dict; the write sits between two awaits, so the cooperative
            event-loop scheduler never preempts it (these are asyncio tasks on
            one loop, not OS threads) — concurrent writes are safe without a
            lock. A single failure degrades to no cache entry → chime fallback,
            never raises.
            """
            try:
                chunks: list[AudioChunk] = []
                try:
                    it = self._tts.synthesize(phrase, language_code=self._bcp47(lang))
                except TypeError:
                    it = self._tts.synthesize(phrase)
                async for c in it:
                    chunks.append(c)
                pcm = b"".join(c.pcm for c in chunks)
                if pcm:
                    self._task_ack_pcm[(lang, phrase)] = pcm
                    return True
            except Exception as exc:  # noqa: BLE001
                log.warning("Task-Ack pre-render '%s' (%s) fehlgeschlagen: %s", phrase, lang, exc)
            return False

        # Render all phrases concurrently — the dominant warm-up cost was these
        # ~20 sequential TTS round-trips. _render_one never raises, so a plain
        # gather is enough (no return_exceptions needed).
        results = await asyncio.gather(*(_render_one(lang, phrase) for lang, phrase in phrases))
        ok = sum(1 for r in results if r)
        log.info("Task-Ack-Cache: %d/%d Phrasen bereit.", ok, len(phrases))

    # ------------------------------------------------------------------
    # Hotkey-Loop
    # ------------------------------------------------------------------

    async def _hotkey_loop(self, trigger: HotkeyTrigger) -> None:
        async for event_name in trigger.events():
            if event_name == "call":
                log.info("📞 CALL via Hotkey")
                self._call_event.set()
            elif event_name == "ptt_press":
                self._on_ptt_press()
            elif event_name == "ptt_release":
                self._on_ptt_release()
            elif event_name == "hangup":
                log.info("📵 HANGUP via Hotkey")
                self._trigger_voice_hangup()

    def _on_ptt_press(self) -> None:
        """Push-to-talk DOWN edge — arm raw recording and open the session.

        Idempotent: ``global_hotkeys`` re-fires on_press on every key-repeat
        poll while the chord is held, so a second press during an active
        session (or while already armed) is a no-op. Only a fresh press from
        IDLE starts a recording, which keeps PTT from racing a running
        wake-word session.
        """
        if self._ptt_mode or self._state != PipelineState.IDLE:
            return
        if not self._activation_allowed():
            log.info("PTT press ignored: Desktop-App not visible.")
            return
        # NB: the post-hangup wake-lock is deliberately NOT consulted here. That
        # lock exists to stop Jarvis' own TTS tail from re-triggering the *wake
        # word* (audio echo). PTT is an explicit key press — no echo path — so
        # gating it would only block intentional rapid re-presses for 3 s.
        log.info("🎙 PTT DOWN — recording (hold to talk, release to send)")
        self._ptt_mode = True
        self._ptt_release_event.clear()
        self._call_event.set()

    def _on_ptt_release(self) -> None:
        """Push-to-talk UP edge — stop recording and submit what was held.

        A no-op when no PTT recording is armed (e.g. the press was ignored
        because a session was already running). Safe to call spuriously.
        """
        if not self._ptt_mode:
            return
        log.info("🎙 PTT UP — submit")
        self._ptt_release_event.set()

    def request_voice_session(
        self, *, seed_messages: list[tuple[str, str]] | None = None
    ) -> bool:
        """Arm a wake-style voice session from outside the audio path.

        The "Speak in this conversation" button (``POST /api/chats/{kind}/
        {cid}/speak``) calls this to start a session that already remembers a
        past conversation — functionally "Hey Jarvis", but with seeded context.

        Same-loop, in-process: uvicorn and the pipeline share the orchestrator
        event loop (see ``server.py`` / ``desktop_app.py``), so this runs on
        the pipeline's own loop and may set ``_call_event`` directly — exactly
        as the wake/PTT arming paths do.

        Returns ``False`` (no-op) when a session is already active or arming,
        or when the desktop app is not visible (``_activation_allowed``). The
        brain is seeded ONLY when we actually arm, so a rejected request never
        pollutes an unrelated in-flight session's history. Like PTT (an
        explicit user action with no audio-echo path), the post-hangup
        wake-lock is intentionally not consulted here.
        """
        if self._ptt_mode or self._state != PipelineState.IDLE:
            log.info("request_voice_session ignored: pipeline not idle.")
            return False
        if not self._activation_allowed():
            log.info("request_voice_session ignored: activation not allowed.")
            return False
        if seed_messages:
            brain = getattr(self, "_brain", None)
            seed = getattr(brain, "seed_history", None)
            if callable(seed):
                try:
                    seed(seed_messages)
                except Exception:  # noqa: BLE001 — seeding must never block arming
                    log.warning(
                        "request_voice_session: brain seed failed", exc_info=True
                    )
        self._ptt_mode = False  # wake-style, not raw PTT recording
        self._last_wake_keyword = "chat_resume"
        log.info(
            "📞 request_voice_session — arming wake-style session (seeded=%d turns)",
            len(seed_messages or []),
        )
        self._call_event.set()
        return True

    def _post_hangup_lock_seconds(self) -> float:
        """Wake-lock duration for the session that just ended (one-shot).

        SHORT (`_explicit_hangup_lock_s`) after a user HARD hangup — the
        JarvisBar close / hotkey / "auflegen" stopped the player, so there is no
        TTS tail to echo and the long lock would only swallow the user's very
        next "Hey <wake>". Otherwise the FULL `_post_hangup_lock_s` guards the
        speaker tail of a natural end / farewell. Consumes the hard-hangup flag
        so it applies to exactly one session end.
        """
        hard = self._explicit_hard_hangup
        self._explicit_hard_hangup = False
        return self._explicit_hangup_lock_s if hard else self._post_hangup_lock_s

    def request_hangup(self) -> None:
        """End the live voice session from outside the audio path.

        The jarvis-bar's hover-to-close cross calls this (and any future UI
        close affordance). Thread-safe like ``request_voice_session``: it routes
        through the single hangup chokepoint, whose primitives (``Event.set``,
        player stop, CU-cancel) are safe to invoke from the Tk thread. A no-op
        in practice when no session is active — the never-consumed event is
        cleared at the next session start.
        """
        log.info("📵 request_hangup — closing the voice session")
        self._trigger_voice_hangup()

    def request_ptt_toggle(self) -> None:
        """Toggle endpoint-free dictation — the jarvis-bar's square button.

        First call opens a mic with NO silence-endpoint (speak as long as you
        want, pauses included); the second call submits. Thread-safe like the
        other request_* entries: it just drives the existing PTT press/release
        edges, whose primitives (``Event.set``, bool flags) are safe from the
        Tk thread. ``_on_ptt_press`` is a no-op unless idle; ``_on_ptt_release``
        is a no-op unless a PTT recording is armed — so a stray toggle never
        misbehaves.
        """
        if self._ptt_mode:
            self._on_ptt_release()  # submit what was dictated
        else:
            self._on_ptt_press()    # open an endpoint-free mic

    def is_session_active(self) -> bool:
        """Ground truth for "a live voice session is in progress right now".

        The jarvis-bar uses this to decide whether a close-X click is a real
        hang-up or a useless no-op. The bar's visual mode can get stuck in the
        active "listen" look when a wake popped it but the post-hangup wake-lock
        cooldown rejected the session — no ``VoiceSessionStarted`` is published
        and no ``IDLE`` state follows, so nothing resets the bar (freeze
        forensic 2026-06-28). The turn-state is the authoritative signal: it is
        ``IDLE`` iff no ``_active_session`` loop is running.
        """
        return (
            getattr(self, "_turn_state", TurnTakingState.IDLE)
            is not TurnTakingState.IDLE
        )

    def _trigger_voice_hangup(self, *, stop_player: bool = True) -> None:
        """Hard-stop the voice channel — the single hangup chokepoint.

        User intent (2026-05-20): "auflegen" is an absolute kill switch.
        No matter what Jarvis is currently saying, announcing, or queueing,
        a hangup must silence the voice channel immediately. Background
        OpenClaw missions keep running (they live in their own subprocess
        + Job Object); only their *voice readback* is suppressed via the
        ``_hangup_event`` gate on the bus-driven announcement handlers.

        ``stop_player=False`` is used when the brain itself emitted the
        farewell ("Goodbye, Ruben.") — we let that final utterance play.
        """
        if stop_player:
            try:
                self._player.stop()
            except Exception as exc:  # noqa: BLE001
                log.warning("Player-Stop bei Hangup fehlgeschlagen: %s", exc)
            # Player stopped => no TTS tail => the next session end uses the
            # SHORT wake-lock so the user can re-wake immediately. A farewell
            # hangup (stop_player=False) keeps the full speaker-tail guard.
            self._explicit_hard_hangup = True
        self._session_end_reason = HANGUP_VOICE_PATTERN
        self._hangup_event.set()
        # BUG-CU-HANGUP (2026-05-28): "auflegen" must also STOP a running
        # Computer-Use mission immediately — otherwise Jarvis keeps clicking
        # the screen in the background after the user told it to stop. This is
        # CU-scoped (cancels only the active CU token), so OpenClaw
        # background missions are unaffected (their voice readback is muted via
        # the _hangup_event gate, matching the documented hangup contract).
        try:
            from jarvis.harness.computer_use_context import cancel_active_cu
            if cancel_active_cu("voice_hangup"):
                log.info("Voice-Hangup: active Computer-Use mission cancelled.")
        except Exception:  # noqa: BLE001 — hangup must never crash
            log.debug("CU cancel-on-hangup failed (non-fatal)", exc_info=True)
        # The X must also stop a CHAT turn, not only a voice turn. A chat turn
        # runs on a separate dispatcher (``desktop_app._on_user_message``) that
        # never observes ``_hangup_event`` — so before this it kept thinking
        # through every X press (live bug 2026-06-19: ~27 ignored presses). The
        # cancel is edge-triggered and loop-safe (this may run on the Tk thread).
        try:
            from jarvis.core.runtime_refs import cancel_active_chat_turn
            if cancel_active_chat_turn():
                log.info("Voice-Hangup: active chat turn cancelled.")
        except Exception:  # noqa: BLE001 — hangup must never crash
            log.debug("chat-turn cancel-on-hangup failed (non-fatal)", exc_info=True)
        # Discard any pending continuation fragment so it can't leak into the
        # next voice session (the user has explicitly ended this one), and
        # cancel its clarifying-question timer so no question fires after hangup.
        try:
            self._cancel_clarify_question()
            self._cancel_continuation_drain()
            self._continuation_buffer.discard()
            win = getattr(self, "_continuation_window", None)
            if win is not None:
                win.clear()
        except Exception:  # noqa: BLE001 — hangup must never crash
            log.debug("ContinuationBuffer.discard() failed (non-fatal)", exc_info=True)

    # ------------------------------------------------------------------
    # Wake-Loop mit Parallel-Detection
    # ------------------------------------------------------------------

    def _wake_listening_enabled(self) -> bool:
        return self._openwakeword_enabled or self._whisper_wake_enabled

    async def _wake_loop(self) -> None:
        """Lauscht im IDLE-State auf Wake-Word über ZWEI parallele Pfade.

        Ein Mic-Stream wird per Fanout in zwei Queues gesplittet:
        (1) openWakeWord-Queue   → schneller Primary-Detector
        (2) Whisper-Wake-Queue   → robuster Fallback

        Wer zuerst triggert, feuert `call_event` und beide Tasks werden gecancelt.
        """
        log.info(
            "Wake-Loop gestartet (oww=%s, whisper=%s, gate=%s).",
            "on" if self._openwakeword_enabled else "off",
            "on" if self._whisper_wake_enabled else "off",
            "open" if self._activation_allowed() else "closed",
        )
        # Both detectors off: PARK, do not die. The old code did
        # ``await asyncio.Event().wait()`` on a FRESH event nobody ever sets —
        # a permanent sleep that not even a later live wake-word change could
        # re-arm (only an app restart). Wait on ``_wake_reload_event`` instead so
        # a ``set_wake_plan`` that re-enables a detector wakes the loop back up,
        # in-app. The 30 s timeout re-logs the parked state so a genuinely dead
        # listener stays visible without busy-spinning. (Mission: "no dead state
        # blocks waking"; AP-22: recovery must be reachable in-app, not a restart.)
        while not self._wake_listening_enabled():
            log.warning(
                "Both wake detectors are disabled — wake is PARKED until a "
                "wake-word change re-enables one (voice still works via hotkey). "
                "Waiting on a live wake-plan reload, not sleeping forever."
            )
            try:
                await asyncio.wait_for(self._wake_reload_event.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                pass
            finally:
                self._wake_reload_event.clear()
        gate_blocked_logged_at = 0.0
        while True:
            if not self._activation_allowed():
                now = time.time()
                if now - gate_blocked_logged_at > 30.0:
                    # Name the REAL reason: mute is checked first in
                    # _activation_allowed, so a muted user must not be told the
                    # window is hidden (that misled a live freeze diagnosis).
                    reason = (
                        "voice is muted"
                        if getattr(self, "_muted", False)
                        else "desktop window not visible?"
                    )
                    log.info(
                        "Wake-Loop wartet — Activation-Gate geschlossen (%s).",
                        reason,
                    )
                    gate_blocked_logged_at = now
                await asyncio.sleep(0.25)
                continue
            if self._state != PipelineState.IDLE:
                await asyncio.sleep(0.1)
                continue
            try:
                log.info(
                    "🎧 Wake-Listener aktiv — sag '%s' …",
                    getattr(self, "_wake_phrase_label", "the wake word"),
                )
                await self._run_parallel_wake()
            except Exception as exc:  # noqa: BLE001
                log.exception("Wake-Loop Fehler: %s", exc)
                await asyncio.sleep(0.5)

    async def _on_vosk_wake_candidate(self, active: bool) -> None:
        """Optimistic, visual-only bar reveal for the vosk_kws engine
        (spawn-latency mission 2026-07-10).

        Unlike OpenWakeWord, ``VoskKwsProvider.detect()`` does not yield
        anything until AFTER its own internal confirm (grammar re-score +
        energy gate + free-decode sound confirm) already completed — so the
        OWW-result-timed optimistic publish further down in
        ``_run_parallel_wake`` runs too late to help this engine (it would
        fire right before the authoritative ``WakeWordDetected`` anyway).
        The provider instead calls back HERE the moment a grammar candidate
        clears its own cheap RMS pre-gate — well before its confirm_tail_s
        wait and verify pass — so the visual reveal lands in roughly one
        poll cadence. ``active=False`` retracts if that candidate is later
        rejected. Visual only: never opens a session, mirrors
        ``_on_wake_candidate`` on the orb-bridge side (ui/orb/bus_bridge.py).

        The ``active=True`` publish is gated on the SAME
        ``_should_show_optimistic_candidate()`` check the OWW branch uses —
        chiefly its ``PipelineState.IDLE`` guard, since ``detect()`` can in
        principle still be mid-candidate when a hotkey/PTT session starts the
        state elsewhere. ``active=False`` is published unconditionally: the
        bridge's retract handler is idempotent/no-op-safe against a retract
        with no matching prior show (``ui/orb/bus_bridge.py::_on_wake_candidate``
        — a real active session is left alone, an idle bar is simply
        re-asserted idle), so it does not need the same gate.
        """
        if active and not self._should_show_optimistic_candidate():
            return
        try:
            await self._publish_event(
                WakeCandidateDetected(source_layer="speech", active=active)
            )
        except Exception as exc:  # noqa: BLE001 — a broken publish must not kill wake
            log.warning("vosk wake-candidate publish failed: %s", exc)

    def _should_show_optimistic_candidate(self) -> bool:
        """Whether an unverified OWW hit may pop the overlay bar immediately.

        The optimistic reveal exists so a genuine "Hey Jarvis" feels instant on
        the precise pretrained model, where false candidates are rare and a
        reject costs one brief bar flash. A user-trained custom_onnx model
        fires on breath/ambient speech many times a minute (user GIF
        2026-07-02: the bar popped open/closed on auto-repeat), so for that
        engine the bar appears only AFTER the STT verify confirms the wake —
        a ~1 s later reveal instead of a constant flicker.
        """
        if self._state != PipelineState.IDLE:
            return False
        plan = getattr(self, "_wake_plan", None)
        return not (plan is not None and getattr(plan, "engine", "") == "custom_onnx")

    async def _verify_oww_hit(self, pcm_snapshot: bytes) -> bool:
        """Second-stage gate: ask the utterance STT whether the few seconds
        leading up to an OpenWakeWord hit actually contained the configured
        wake phrase. Returns True if the phrase's matcher confirms it.

        STT-outage failure modes degrade OPEN (return True with a log line) so
        a misconfigured STT, a network blip, or a rate-limit cannot brick the
        wake — we'd rather accept the occasional false positive than have the
        user shout into a dead listener. A clear, non-matching transcript
        (genuine other speech) suppresses, preserving the bare-"Jarvis"
        BUG-009 guard. How a WORKING STT that heard no speech (empty
        transcript / silence-hallucination boilerplate) is treated depends on
        the engine: a user-trained custom_onnx hit SUPPRESSES (live forensic
        2026-07-01 — such models false-fire on breath/noise, so "no speech
        heard" is evidence of a false fire, not an STT problem), while any
        other OWW hit keeps the historical degrade-open behaviour (forensic
        2026-06-28 — short real wakes often mis-transcribe to nothing).
        """
        if not self._require_hey_prefix:
            return True
        # The STT re-verification runs for user-trained custom_onnx models
        # (live forensic 2026-07-01: a few-shot model scored breath/ambient/
        # other speech up to 1.000 — a false-positive storm; the transcript
        # matched against the phrase's own sound-folded fuzzy matcher is the
        # real discriminator). Since the product ships no pretrained model
        # (design 2026-07-07), custom_onnx plans are the only source of OWW
        # hits; vosk_kws is exempt via verify_prefix=False (its own confirm).
        plan = getattr(self, "_wake_plan", None)
        if plan is not None and not getattr(plan, "verify_prefix", True):
            return True
        # A user-trained custom_onnx model is a WEAK discriminator (live
        # forensic 2026-07-01/02: scores up to 1.000 on breath/ambient/other
        # speech, 15 fires in 25 s at the worst). Its hits get the strict
        # treatment throughout this gate.
        custom_model_hit = (
            plan is not None and getattr(plan, "engine", "") == "custom_onnx"
        )
        # Silence gate (custom only, BEFORE any STT round-trip): a hit whose
        # trailing audio is essentially silent is a breath/noise false fire by
        # definition — there is no speech an STT could confirm. Suppressing it
        # here (a) kills the "fires out of nowhere" storm at zero cost and
        # (b) stops the fire flood from hammering the verify STT into
        # 429/timeouts (live 2026-07-02: that flood-induced outage is what
        # opened the degrade-open hole and produced ghost activations).
        if custom_model_hit and pcm_snapshot:
            tail_rms = pcm_tail_rms(pcm_snapshot)
            if tail_rms < CUSTOM_WAKE_MIN_RMS:
                log.info(
                    "wake-verify: suppressed — near-silent audio "
                    "(rms %.4f < %.3f) on a custom-model hit; skipping STT",
                    tail_rms,
                    CUSTOM_WAKE_MIN_RMS,
                )
                return False
        if self._utterance_stt is None:
            log.warning(
                "require_hey_prefix=True but no utterance STT — accepting OWW hit"
            )
            return True
        # Nothing captured yet (empty ring buffer) — there is genuinely no audio
        # to confirm the wake, so reject without an STT round-trip. (Whether a
        # NON-empty buffer with no usable transcript suppresses or degrades
        # open is engine-dependent — see below.)
        if not pcm_snapshot:
            return False
        matched, text = await verify_wake_with_stt(
            self._utterance_stt,
            pcm_snapshot,
            matcher=getattr(self, "_wake_matcher", None),
        )
        if matched:
            return True
        # For a custom-model hit, a WORKING STT that heard no wake phrase —
        # an empty transcript or a known silence-hallucination boilerplate —
        # is evidence of a false fire and must SUPPRESS. Any other OWW hit
        # keeps the documented degrade-open behaviour (historical forensic
        # 2026-06-28: false fires happened on real speech, so an empty
        # transcript there really does mean the STT failed).
        #
        # Persistent verify-STT failure (Groq 429/503/timeout after retries):
        # - non-custom hit: degrade OPEN. Candidates are rare, so an
        #   unreachable STT is a provider problem, not evidence about what the
        #   user said; accepting keeps the wake alive through an outage (AP-22).
        # - custom_onnx: FAIL CLOSED (live 2026-07-02, 3 ghost activations
        #   overnight). The fire flood of a weak model eventually hits an STT
        #   timeout, and degrade-open then activates Jarvis although nobody
        #   spoke. The session a wake opens needs that same STT to hear
        #   anything (it would be a deaf session), and hotkey/orb-click remain
        #   as in-app activation paths — honest degradation, not a bricked
        #   wake.
        if text is None:
            if custom_model_hit:
                log.info(
                    "wake-verify: suppressed — verify STT unreachable on a "
                    "custom-model hit (fail-closed: the session would be deaf "
                    "without STT; hotkey/orb-click still activate)"
                )
                return False
            log.info(
                "wake-verify: verify STT unreachable on a strong OWW hit — "
                "accepting (degrade-open; an STT outage must not brick the "
                "wake, AP-22)"
            )
            return True
        text = text.strip()
        # KNOWN STT hallucination boilerplate ("Untertitelung des ZDF, 2020",
        # "Vielen Dank."). Whisper emits these on silence/noise buffers.
        # - custom_onnx: the buffer held silence/noise → the model false-fired
        #   on breath/ambient → suppress (fail-closed).
        # - non-custom hit: forensic 2026-06-28 showed short REAL wake
        #   buffers hallucinate these for ~half of all valid wakes → accept
        #   (degrade-open), else the wake "stops working" intermittently.
        # An arbitrary non-matching transcript (genuine OTHER speech) still
        # suppresses for both, so the bare-"Jarvis" guard (BUG-009) stays.
        if text and _STT_HALLUCINATION_RE.search(text) is not None:
            if custom_model_hit:
                log.info(
                    "wake-verify: suppressed — STT hallucination %r on a "
                    "custom-model hit (silence/noise false fire, fail-closed)",
                    text[:80],
                )
                return False
            log.info(
                "wake-verify: STT hallucination %r on a strong OWW hit — "
                "accepting (degrade-open, not a real rejection)",
                text[:80],
            )
            return True
        # The verify STT WORKED and produced an empty transcript.
        # - custom_onnx: no speech in the buffer → breath/noise false fire →
        #   suppress. This is the "fires out of nowhere" half of the
        #   2026-07-01 storm.
        # - non-custom hit: an empty transcription on a strong hit is far
        #   more likely a silence-mis-transcription of a short real wake than
        #   a spontaneous model fire → accept (degrade-open, forensic
        #   2026-06-28 "the wake sometimes stops working entirely").
        if not text:
            if custom_model_hit:
                log.info(
                    "wake-verify: suppressed — empty transcript on a "
                    "custom-model hit (no speech captured, fail-closed)"
                )
                return False
            log.info(
                "wake-verify: verify STT returned no transcript on a strong OWW "
                "hit — accepting (degrade-open)"
            )
            return True
        log.info(
            "wake-verify: suppressed — transcript %r has no wake prefix", text[:80]
        )
        return False

    async def _run_parallel_wake(self) -> None:
        """Öffnet ein Mic, fannt zu 2 Detector-Queues, wartet auf ersten Hit."""
        oww_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        whisper_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        detector_queues = [oww_queue] if self._openwakeword_enabled else []
        if self._whisper_wake_enabled and self._whisper_wake is not None:
            detector_queues.append(whisper_queue)
        if not detector_queues:
            await asyncio.sleep(1.0)
            return

        # Rolling PCM ring buffer for post-OWW prefix verification (see
        # ``_verify_oww_hit``). 2.5 s at 16 kHz mono int16 ≈ 80 kB, well above
        # the longest natural "Hey Jarvis" plus a little headroom. We keep the
        # buffer regardless of whether prefix verification is enabled so the
        # cost is identical between modes and a runtime config flip never
        # needs to re-arm fanout.
        ring_bytes = bytearray()
        RING_MAX = 16_000 * 2 * 3  # ~3 s

        async def _fanout(mic: MicrophoneCapture) -> None:
            async for chunk in mic.stream():
                # Input mute (Jarvis-scoped): if the user mutes while a wake
                # session is already mid-wait, stop feeding the detectors so
                # Jarvis goes deaf immediately — without touching the OS mic.
                # (A fresh mute while IDLE is already handled by _wake_loop's
                # _activation_allowed() gate, which never opens the mic.)
                if getattr(self, "_muted", False):
                    continue
                ring_bytes.extend(chunk.pcm)
                if len(ring_bytes) > RING_MAX:
                    del ring_bytes[: len(ring_bytes) - RING_MAX]
                for q in detector_queues:
                    try:
                        q.put_nowait(chunk)
                    except asyncio.QueueFull:
                        # Detector ist hinten dran — ältesten Chunk droppen
                        try:
                            q.get_nowait()
                            q.put_nowait(chunk)
                        except asyncio.QueueEmpty:
                            pass

        async def _run_oww() -> str:
            async for kw in self._wake.detect(_queue_iter(oww_queue)):
                return f"oww:{kw}"
            return ""

        async def _run_whisper() -> str:
            if self._whisper_wake is None:
                await asyncio.Event().wait()  # nie
                return ""
            async for kw in self._whisper_wake.detect(_queue_iter(whisper_queue)):
                return f"whisper:{kw}"
            return ""

        # NOTE: the wake mic keeps the DEEP default queue on purpose. Its
        # detectors offload inference (openWakeWord ``to_thread``; whisper-wake
        # ``await transcribe_pcm``), so the event loop stays free and the cheap
        # ``_fanout`` drains this queue near-instantly — it never fills, so a
        # shallow depth would be inert here anyway. The wake path's real staleness
        # lever is the per-detector queues below (``oww_queue`` / ``whisper_queue``),
        # which belong to the wake layer; this change deliberately does not touch
        # them. The drop-OLDEST overflow policy (capture ``_safe_put``) still
        # applies and is safe here.
        async with MicrophoneCapture(
            device=self._input_device, device_priority=self._input_priority
        ) as mic:
            fanout_task = asyncio.create_task(_fanout(mic), name="fanout")
            oww_task = (
                asyncio.create_task(_run_oww(), name="oww-wake")
                if self._openwakeword_enabled
                else None
            )
            tasks = [fanout_task]
            if oww_task is not None:
                tasks.append(oww_task)
            if self._whisper_wake_enabled:
                whisper_task = asyncio.create_task(_run_whisper(), name="whisper-wake")
                tasks.append(whisper_task)
            else:
                whisper_task = None  # type: ignore[assignment]

            async def _detector_heartbeat() -> None:
                """Alle 10s: loggen dass die Detector-Tasks noch leben.

                Wenn hier nichts mehr kommt obwohl der Watchdog läuft, ist
                ein Task silent gestorben (Queue-Deadlock, Exception-Swallow).
                """
                while True:
                    await asyncio.sleep(10.0)
                    oww_alive = oww_task is not None and not oww_task.done()
                    whisper_alive = whisper_task is not None and not whisper_task.done()
                    fanout_alive = not fanout_task.done()
                    log.info(
                        "wake-detectors-heartbeat: fanout=%s oww=%s whisper=%s oww_q=%d wsp_q=%d",
                        "alive" if fanout_alive else "DEAD",
                        "alive" if oww_alive else "DEAD" if oww_task else "off",
                        "alive" if whisper_alive else "DEAD" if whisper_task else "off",
                        oww_queue.qsize(),
                        whisper_queue.qsize(),
                    )
                    # Wenn ein Detector-Task mit Exception gestorben ist, sichtbar machen
                    for t in (oww_task, whisper_task):
                        if t is None or not t.done():
                            continue
                        try:
                            t.result()
                        except asyncio.CancelledError:
                            pass
                        except Exception as exc:  # noqa: BLE001
                            log.error("Detector-Task %s gestorben: %s", t.get_name(), exc)

            heartbeat_task = asyncio.create_task(_detector_heartbeat(), name="wake-heartbeat")

            # Live-apply: a set_wake_plan() flips _wake_reload_event so we abort
            # this mic/detector session and let _wake_loop re-arm with the new
            # model/matcher — the wake word changes without an app restart.
            reload_task = asyncio.create_task(
                self._wake_reload_event.wait(), name="wake-reload"
            )
            tasks.append(reload_task)

            try:
                # Warten bis EIN Detector etwas yielded — oder ein Live-Reload.
                detector_tasks = [t for t in tasks if t is not fanout_task]
                done, _pending = await asyncio.wait(
                    detector_tasks, return_when=asyncio.FIRST_COMPLETED
                )
                if reload_task in done:
                    self._wake_reload_event.clear()
                    log.info(
                        "🔁 Wake-Live-Reload — re-arming with the new wake word."
                    )
                    return  # finally cancels detectors + closes mic; loop re-enters
                for t in done:
                    if t is reload_task:
                        continue
                    result = t.result()
                    if result:
                        log.info("🎙 WAKE-Kandidat über %s", result)
                        # Prefix-Verifier: an OWW hit is only a *candidate*
                        # until the cloud STT confirms "hey/hi/hallo + jarv"
                        # in the few seconds before the trigger. Whisper-wake
                        # already enforces the same pattern, so its hits skip
                        # the second stage. See ``_verify_oww_hit``.
                        if result.startswith("oww:"):
                            # Optimistic VISUAL reveal: pop the overlay bar NOW,
                            # before the slow STT prefix-verify below, so the bar
                            # feels instant on "Hey Jarvis". This is visual-only
                            # (WakeCandidateDetected never opens a session turn),
                            # so a rejected candidate costs a brief bar flash, not
                            # a phantom session — retracted just below on reject.
                            # Custom-model candidates never reveal optimistically
                            # (constant flicker, see the helper's docstring).
                            show_candidate = self._should_show_optimistic_candidate()
                            # vosk_kws already published its OWN, earlier
                            # candidate signal directly from the provider
                            # (see _on_vosk_wake_candidate) — timed BEFORE its
                            # internal confirm_tail_s wait, unlike this
                            # OWW-result-timed publish which only runs AFTER
                            # vosk's full internal verify already completed.
                            # Publishing True again here would be a redundant,
                            # mistimed duplicate landing almost simultaneously
                            # with WakeWordDetected. `show_candidate` itself
                            # stays UNCHANGED so the lock/not-activatable
                            # retract path further below still correctly
                            # retracts the EARLIER provider-side show if this
                            # wake gets discarded.
                            engine = getattr(
                                getattr(self, "_wake_plan", None), "engine", ""
                            )
                            if show_candidate and engine != "vosk_kws":
                                await self._publish_event(
                                    WakeCandidateDetected(
                                        source_layer="speech", active=True
                                    )
                                )
                            verified = await self._verify_oww_hit(bytes(ring_bytes))
                            if not verified:
                                if show_candidate:
                                    await self._publish_event(
                                        WakeCandidateDetected(
                                            source_layer="speech", active=False
                                        )
                                    )
                                log.info(
                                    "🚫 WAKE verworfen — kein 'Hey'-Prefix im "
                                    "Transkript der letzten ~3 s"
                                )
                                # Clear the detector's debounce cooldown: this
                                # candidate was a false positive, so a genuine
                                # "Hey Jarvis" spoken right after must NOT be
                                # swallowed for the full cooldown window.
                                note_rejected = getattr(
                                    self._wake, "note_rejected_candidate", None
                                )
                                if callable(note_rejected):
                                    note_rejected()
                                break
                        log.info("🎙 WAKE bestätigt über %s", result)
                        if self._state == PipelineState.IDLE:
                            # The state-loop SILENTLY drops a wake that arrives
                            # inside the post-hangup echo lock (or when the app
                            # is not activatable) — it just ``continue``s. But
                            # _emit_wake below publishes WakeWordDetected, which
                            # flips the overlay bar to its "listen" look. Emitting
                            # for a wake the loop will then drop leaves the bar
                            # stuck "listening" with no session behind it, and the
                            # user is ignored ("wake triggers, nothing happens").
                            # Mirror the loop's gate HERE, before _emit_wake, and
                            # retract the optimistic candidate instead of lying.
                            now = time.time()
                            locked = now < self._wake_lock_until
                            if locked or not self._activation_allowed():
                                if locals().get("show_candidate"):
                                    await self._publish_event(
                                        WakeCandidateDetected(
                                            source_layer="speech", active=False
                                        )
                                    )
                                if locked:
                                    log.info(
                                        "🔒 Wake-Lock aktiv — Emit verworfen "
                                        "(noch %.1fs)",
                                        max(0.0, self._wake_lock_until - now),
                                    )
                                else:
                                    log.info(
                                        "Wake-Emit verworfen — App nicht "
                                        "aktivierbar."
                                    )
                                break
                            # Event-Bus: WakeWordDetected + Supervisor-State
                            # werden fuer UI-Feedback gebraucht (Orb einblenden).
                            keyword = result.split(":", 1)[-1] if ":" in result else result
                            await self._emit_wake(keyword)
                            self._call_event.set()
                        break
            finally:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except (asyncio.CancelledError, Exception):
                    pass
                for t in tasks:
                    if not t.done():
                        t.cancel()
                for t in tasks:
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass

    # ------------------------------------------------------------------
    # State-Loop
    # ------------------------------------------------------------------

    async def _state_loop(self) -> None:
        while True:
            await self._call_event.wait()
            self._call_event.clear()
            # Wake-Cooldown-Check: ignoriere Wake-Events die während
            # der Lock-Zeit kommen (Speaker-Echo nach Hangup).
            now = time.time()
            if not self._activation_allowed():
                log.info("Voice-Call ignoriert: Desktop-App ist nicht sichtbar.")
                # A discarded call consumes any PTT arming with it — otherwise a
                # stale ``_ptt_mode`` would reroute the NEXT (wake-word) call
                # into the raw-recording path that has no key to release.
                self._ptt_mode = False
                continue
            if now < self._wake_lock_until:
                remaining = self._wake_lock_until - now
                log.info("🔒 Wake-Lock aktiv — ignoriere (noch %.1fs)", remaining)
                self._ptt_mode = False
                continue
            self._hangup_event.clear()
            # Fresh session: forget any hard-hangup flag left by a no-op hangup
            # while idle, so ONLY this session's own ending decides its lock.
            self._explicit_hard_hangup = False
            self._state = PipelineState.ACTIVE
            # Reset per-session completeness signal state: a new session
            # starts "fresh" so the first INCOMPLETE gets an earcon, not a
            # spoken cue. (The spoken-cue path is for mid-conversation use.)
            self._session_has_assistant_spoken = False
            session_id = str(uuid4())
            self._current_voice_session_id = session_id
            session_started_at = time.time()
            wake_keyword = (
                "push_to_talk"
                if self._ptt_mode
                else (self._last_wake_keyword or "hotkey")
            )
            self._last_wake_keyword = ""
            await self._publish_event(
                VoiceSessionStarted(
                    source_layer="speech.pipeline",
                    session_id=session_id,
                    wake_keyword=wake_keyword,
                    language="de",
                )
            )
            hangup_reason = HANGUP_ERROR
            log.info("📞 ANRUF angenommen")
            # Orb einblenden & Mic-Pulsieren aktivieren
            await self._set_turn_state(TurnTakingState.LISTENING)
            try:
                await self._play_ack(ptt=self._ptt_mode)
                hangup_reason = await self._active_session()
            except Exception as exc:  # noqa: BLE001
                log.exception("Session-Fehler: %s", exc)
            finally:
                self._current_voice_session_id = None
                # PTT is one-shot per hold — disarm before the next session so a
                # stale flag can never reroute a later wake-word session into
                # the raw-recording path.
                self._ptt_mode = False
                # Tear down any pending incomplete-utterance hold so a clarify
                # question can never fire into a dead session (e.g. the turn
                # ended via idle-timeout, not the hangup handler) and a held
                # fragment can never leak into the next session.
                try:
                    self._cancel_clarify_question()
                    self._cancel_continuation_drain()
                    self._continuation_buffer.discard()
                    win = getattr(self, "_continuation_window", None)
                    if win is not None:
                        win.clear()
                except Exception:  # noqa: BLE001 — teardown must never crash
                    log.debug("Clarify/continuation teardown failed", exc_info=True)
                await self._publish_event(
                    VoiceSessionEnded(
                        source_layer="speech.pipeline",
                        session_id=session_id,
                        hangup_reason=hangup_reason,
                        duration_s=max(0.0, time.time() - session_started_at),
                    )
                )
                self._state = PipelineState.IDLE
                # Supervisor zurueck auf IDLE — Orb verschwindet
                await self._set_turn_state(TurnTakingState.IDLE)
                # Disconnect-Sound als hörbares Hangup-Signal (earcon — gated
                # by the global "Sound effects" switch).
                await self._play_earcon(DISCONNECT_PCM)
                # Cooldown setzen damit Speaker-Echo nicht sofort re-triggert.
                lock_s = self._post_hangup_lock_seconds()
                self._wake_lock_until = time.time() + lock_s
                log.info("📵 AUFGELEGT — zurück zu IDLE (Wake-Lock %.1fs).", lock_s)

    def _earcons_enabled(self) -> bool:
        """Whether synthesized UI earcons may play.

        Read fresh from the shared config object so the Settings → Behavior
        "Sound effects" master switch applies live (no restart). Defensive
        default ``True`` — a missing field must never silence tones.
        """
        ui = getattr(getattr(self, "_config", None), "ui", None)
        return bool(getattr(ui, "sound_effects", True))

    async def _play_earcon(
        self, pcm: bytes, *, sample_rate: int = CHIME_SAMPLE_RATE
    ) -> None:
        """Play a synthesized earcon unless the global "Sound effects" switch
        is off. Never raises — an earcon failure must not crash a turn (AD-OE6).
        Does NOT gate the spoken TTS voice, which is not an earcon.
        """
        if not self._earcons_enabled():
            return
        try:
            await self._player.play_pcm(pcm, sample_rate=sample_rate)
        except Exception as exc:  # noqa: BLE001
            log.debug("Earcon playback skipped (%s).", exc)

    async def _play_ack(self, *, ptt: bool = False) -> None:
        """Chime + pre-rendertes 'Ja?' — Gesamtdauer ~400-600 ms.

        Push-to-talk plays ONLY the chime: the user is already holding the key
        and talking, so a spoken "Ja?" would talk over their opening words. The
        chime is immediate feedback that recording is live; speech is not.
        """
        try:
            await self._play_earcon(CHIME_PCM)
            if ptt:
                # Chime only, and NO dead-zone: the mic opens the instant this
                # returns and the user is already holding the key + talking. The
                # 400ms sleep below exists to keep the spoken "Ja?" from leaking
                # into the mic — PTT has no spoken ACK, so running it would just
                # swallow the opening words of every capture (and turn a short
                # hold into a silent no-op, since the mic is not open yet when
                # the key is released).
                return
            if self._ack_pcm:
                await self._player.play_pcm(self._ack_pcm, sample_rate=24_000)
            # Kurze Echo-Suppression: bei Open-Back-Kopfhoerern leakt der
            # pre-renderte ACK ins Mic und triggert VAD auf dem TTS-Echo.
            # 400ms Dead-Zone nach ACK verhindert Self-Retrigger-Loops.
            await asyncio.sleep(0.4)
        except Exception as exc:  # noqa: BLE001
            log.warning("ACK-Playback fehlgeschlagen: %s", exc)

    async def _active_session(self) -> str:
        # Fresh session → never inherit a half-accumulated dictation from a
        # previous session that ended mid-carry (hangup / idle-timeout).
        self._carry_pcm = bytearray()
        self._carry_started_monotonic = None
        self._last_endpoint_reason = None
        # A fresh session never inherits a previous session's readback grace.
        self._last_announcement_spoken_monotonic = None
        self._last_answer_floor_monotonic = None
        if self._ptt_mode:
            # Push-to-talk is deliberately a discrete classic turn: the key-up
            # edge is its endpoint and the duplex protocols do not expose one
            # provider-neutral commit primitive. Normal wake/hotkey sessions
            # use Realtime when selected.
            return await self._ptt_session()
        requested_mode = str(
            getattr(
                getattr(getattr(self, "_config", None), "voice", None),
                "mode",
                "pipeline",
            )
            or "pipeline"
        ).strip().lower()
        if requested_mode == "realtime":
            realtime_reason = await self._active_realtime_session()
            if realtime_reason is not None:
                return realtime_reason
            await self._speak_realtime_unavailable()
        async with MicrophoneCapture(
            device=self._input_device,
            max_queue_chunks=REALTIME_QUEUE_CHUNKS,
            device_priority=self._input_priority,
        ) as mic:
            vad_iter = self._vad.utterances(
                self._session_input_stream(mic.stream())
            ).__aiter__()
            # The VAD ``__anext__`` task PERSISTS across idle windows. Cancelling
            # it kills the underlying async generator (the next ``__anext__``
            # raises ``StopAsyncIteration``), so when a background mission keeps
            # the session open past the idle timeout we must re-await the SAME
            # pending task — never recreate it on the dead generator (that was
            # the spawn-in-flight override no-op: it ``continue``d, the loop top
            # recreated ``__anext__`` on a cancelled generator, and the session
            # hung up with HANGUP_SHUTDOWN anyway). The task is recreated only
            # after it has yielded an utterance.
            next_task: asyncio.Task[bytes] | None = None
            try:
                while not self._hangup_event.is_set():
                    await self._set_turn_state(TurnTakingState.LISTENING)
                    await self._publish_event(ListeningStarted(source_layer="speech"))
                    if next_task is None:
                        next_task = asyncio.create_task(vad_iter.__anext__())
                    hangup_task = asyncio.create_task(self._hangup_event.wait())
                    try:
                        done, _pending = await asyncio.wait(
                            {next_task, hangup_task},
                            # Idle auto-hangup disabled (``session_idle_timeout_s``
                            # <= 0) → wait indefinitely for an utterance or a
                            # manual hangup; the idle-expiry branch below is then
                            # never reached, so the session stays active until the
                            # user hangs up. Otherwise bound the LISTENING window
                            # so a silent session hangs up after the timeout.
                            timeout=(
                                self._idle_timeout_s
                                if getattr(self, "_idle_hangup_enabled", True)
                                else None
                            ),
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    except asyncio.CancelledError:
                        next_task.cancel()
                        hangup_task.cancel()
                        raise
                    # The hangup waiter is recreated each iteration — always drop
                    # it. The VAD task is preserved unless it actually completed.
                    if hangup_task not in done:
                        hangup_task.cancel()
                    if hangup_task in done:
                        next_task.cancel()
                        return HANGUP_HOTKEY
                    if next_task not in done:
                        # Idle timeout — the VAD is still waiting for speech.
                        # ``_live_spawn_watchdogs`` prunes fired/cancelled
                        # watchdogs (the watchdog self-removes after its single
                        # progress phrase), so this extension is bounded to the
                        # watchdog lifetime and can never wedge the session open
                        # forever. While a mission is genuinely in flight, keep
                        # ``next_task`` pending so the generator survives the
                        # next idle window and the readback lands in a live
                        # session.
                        if self._background_mission_in_flight():
                            log.info(
                                "Idle-Timeout reached but a background mission is "
                                "in flight - keeping the voice session open."
                            )
                            continue
                        # A background mission JUST spoke its readback out-of-band
                        # (Computer-Use / OpenClaw completion or failure). That
                        # readback handed the floor back to the user exactly like a
                        # normal inline answer — but, delivered via ``_on_announcement``
                        # OFF this loop, it did NOT reset the idle window, which may
                        # have been armed mid-mission. Re-arm a fresh window so the
                        # user can react instead of being hung up on seconds after
                        # the result (live bug 2026-06-18 08:52: a CU failure
                        # readback at :02 was followed by an idle_timeout hangup at
                        # :18, ~10 s after the user heard the failure — no hangup
                        # command was ever given). Bounded by ``_post_readback_grace_s``
                        # (one full idle window's worth); then idle resumes normally.
                        last_spoken = self._last_announcement_spoken_monotonic
                        if (
                            last_spoken is not None
                            and (time.monotonic() - last_spoken)
                            < self._post_readback_grace_s
                        ):
                            log.info(
                                "Idle-Timeout reached shortly after a spoken "
                                "readback - keeping the voice session open so the "
                                "user can respond."
                            )
                            continue
                        # A normal/inline answer — or one dispatched OFF this loop
                        # via the delegation grace / completion timer — just handed
                        # the floor back to the user. The idle window armed at the
                        # user's utterance ticked down DURING the (slow) turn, so
                        # re-arm one fresh window instead of hanging up seconds after
                        # the answer lands (forensic 2026-06-27 08:49). Bounded: the
                        # stamp is cleared, so the next idle window hangs up normally.
                        if self._within_post_answer_grace():
                            log.info(
                                "Idle-Timeout reached right after Jarvis answered - "
                                "re-arming a fresh window so the user can respond."
                            )
                            self._last_answer_floor_monotonic = None
                            continue
                        log.info("⏲ Idle-Timeout — lege auf.")
                        next_task.cancel()
                        return HANGUP_IDLE_TIMEOUT
                    # The VAD yielded (or raised) — consume it, then recreate the
                    # task on the next loop iteration.
                    try:
                        utterance_pcm: bytes = next_task.result()
                    except StopAsyncIteration:
                        next_task = None
                        return HANGUP_SHUTDOWN
                    except Exception as exc:  # noqa: BLE001
                        log.exception("VAD-Fehler: %s", exc)
                        next_task = None
                        continue
                    next_task = None
                    await self._set_turn_state(TurnTakingState.WAITING_FOR_FINAL_TRANSCRIPT)
                    await self._publish_utterance_captured(utterance_pcm)
                    if not await self._handle_utterance(utterance_pcm):
                        return self._session_end_reason or HANGUP_VOICE_PATTERN
            finally:
                # A still-pending VAD task (idle hangup, exception, app cancel)
                # must be cancelled so the generator + mic stream unwind cleanly.
                if next_task is not None and not next_task.done():
                    next_task.cancel()
        return HANGUP_HOTKEY

    async def _active_realtime_session(self) -> str | None:
        """Run one desktop duplex session, or request classic fallback.

        Imports stay lazy so the optional realtime stack never enters the boot
        critical path. The session owns provider-family fallback and resampling;
        this adapter owns only the local microphone, speaker, and lifecycle.
        Desktop starts half-duplex because PortAudio has no portable acoustic
        echo cancellation. The browser surface provides full duplex with Web
        Audio echo cancellation.
        """
        try:
            from jarvis.realtime.desktop import DesktopRealtimePlayback
            from jarvis.realtime.factory import build_realtime_session
        except ImportError as exc:
            log.warning("Realtime desktop stack is unavailable: %s", exc)
            return None

        playback = DesktopRealtimePlayback(self._player)
        turn_complete = asyncio.Event()
        speaking = False

        async def _send_binary(pcm: bytes) -> None:
            nonlocal speaking
            if not speaking:
                speaking = True
                await self._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
            await playback.send_binary(pcm)

        async def _send_json(message: dict[str, Any]) -> None:
            nonlocal speaking
            kind = str(message.get("type", ""))
            if kind == "audio_ready":
                playback.set_sample_rate(
                    int(message.get("output_sample_rate", 24_000) or 24_000)
                )
                log.info(
                    "Realtime desktop session ready: provider=%s input=%sHz output=%sHz",
                    message.get("provider", "unknown"),
                    message.get("input_sample_rate", "unknown"),
                    message.get("output_sample_rate", "unknown"),
                )
            elif kind == "transcript":
                role = str(message.get("role", ""))
                if role == "user" and bool(message.get("is_final", False)):
                    await self._set_turn_state(TurnTakingState.PROCESSING)
            elif kind == "tts_cancel":
                speaking = False
                await playback.cancel()
                await self._set_turn_state(TurnTakingState.LISTENING)
            elif kind == "turn_complete":
                await playback.finish_turn()
                speaking = False
                await self._set_turn_state(TurnTakingState.LISTENING)
                if not self._continue_listening_after_response:
                    turn_complete.set()
            elif kind in {"provider_error", "error_spoken"}:
                log.warning("Realtime desktop status: %s", message)

        session_id = getattr(self, "_current_voice_session_id", None) or str(uuid4())
        session = build_realtime_session(
            cfg=self._config,
            bus=self._bus,
            session_id=session_id,
            send_binary=_send_binary,
            send_json=_send_json,
            half_duplex=True,
            surface="desktop",
            brain=getattr(self, "_brain", None),
        )
        if session is None:
            return None

        wait_tasks: set[asyncio.Task[Any]] = set()
        reason = "desktop_fallback"
        try:
            await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
            await self._set_turn_state(TurnTakingState.LISTENING)
            async with MicrophoneCapture(
                device=self._input_device,
                max_queue_chunks=REALTIME_QUEUE_CHUNKS,
                device_priority=self._input_priority,
            ) as mic:
                async def _send_microphone() -> None:
                    async for chunk in self._session_input_stream(mic.stream()):
                        await session.handle_audio_frame(chunk.pcm)

                microphone_task = asyncio.create_task(
                    _send_microphone(), name=f"rt-mic-{session_id}"
                )
                provider_task = asyncio.create_task(
                    session.wait_finished(), name=f"rt-provider-{session_id}"
                )
                hangup_task = asyncio.create_task(
                    self._hangup_event.wait(), name=f"rt-hangup-{session_id}"
                )
                wait_tasks.update({microphone_task, provider_task, hangup_task})
                if not self._continue_listening_after_response:
                    wait_tasks.add(
                        asyncio.create_task(
                            turn_complete.wait(), name=f"rt-turn-{session_id}"
                        )
                    )
                done, _pending = await asyncio.wait(
                    wait_tasks, return_when=asyncio.FIRST_COMPLETED
                )
                if hangup_task in done and self._hangup_event.is_set():
                    reason = HANGUP_HOTKEY
                    return HANGUP_HOTKEY
                if turn_complete.is_set():
                    reason = HANGUP_TURN_COMPLETE
                    return HANGUP_TURN_COMPLETE
                for task in done:
                    if task.cancelled():
                        continue
                    exc = task.exception()
                    if exc is not None:
                        log.warning(
                            "Realtime desktop task ended with %s: %s",
                            type(exc).__name__,
                            exc,
                        )
                # A dead provider stream or microphone unwinds into the classic
                # pipeline inside the same call, with an honest spoken notice.
                return None
        except asyncio.CancelledError:
            reason = "shutdown"
            raise
        except Exception as exc:  # noqa: BLE001 — classic fallback is load-bearing
            log.warning("Realtime desktop session failed; using pipeline: %s", exc)
            return None
        finally:
            for task in wait_tasks:
                if not task.done():
                    task.cancel()
            for task in wait_tasks:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            await session.end(reason=reason)
            await playback.close()

    async def _ptt_session(self) -> str:
        """Push-to-talk turn: record raw mic audio until the key is released,
        then submit the whole capture as ONE prompt (one-shot).

        Unlike :meth:`_active_session`, the VAD is bypassed entirely: the key
        defines the endpoint, not silence detection. A continuous drain task
        copies every mic chunk into ``buffer`` with no gaps; the main wait races
        the release edge, a hangup, and a max-hold safety cap. On release the
        buffer is transcribed + answered exactly like a wake-word utterance,
        then the session ends (the next prompt needs another hold).
        """
        buffer = bytearray()
        hung_up = False
        async with MicrophoneCapture(
            device=self._input_device, device_priority=self._input_priority
        ) as mic:
            mic_open_at = time.monotonic()
            await self._set_turn_state(TurnTakingState.LISTENING)
            await self._publish_event(ListeningStarted(source_layer="speech"))

            async def _drain() -> None:
                # Raw capture — no _session_input_stream TTS-echo filter: PTT
                # plays only a short chime (no spoken ACK), and the user is
                # holding the key with deliberate intent to record now.
                async for chunk in mic.stream():
                    buffer.extend(chunk.pcm)
                    # PTT bypasses the VAD, where mic_level.feed normally lives,
                    # so feed the live loudness here too — otherwise the overlay
                    # dictation bars stay flat and you cannot tell it is hearing
                    # you. Same normalized RMS as the VAD; zero-cost when no
                    # overlay is subscribed.
                    if mic_level.has_subscribers():
                        samples = pcm_bytes_to_np(chunk.pcm)
                        if samples.size:
                            mic_level.feed(
                                float(np.sqrt(np.mean(np.square(samples))))
                            )

            drain_task = asyncio.create_task(_drain(), name="ptt-drain")
            release_task = asyncio.create_task(self._ptt_release_event.wait())
            hangup_task = asyncio.create_task(self._hangup_event.wait())
            # Background live-transcript feed (cosmetic — the bubble). NOT part
            # of the wait-set: it must never end the session, only mirror what
            # is being held into the orb bubble. Cancelled + awaited in cleanup.
            live_task = (
                asyncio.create_task(
                    self._ptt_live_transcribe(lambda: bytes(buffer)),
                    name="ptt-live-transcript",
                )
                if self._ptt_partial_interval_s > 0
                else None
            )
            wait_set = {drain_task, release_task, hangup_task}
            all_tasks = list(wait_set) + ([live_task] if live_task else [])
            try:
                done, _pending = await asyncio.wait(
                    wait_set,
                    timeout=self._ptt_max_hold_s,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                for t in all_tasks:
                    t.cancel()
                raise
            if not done:
                log.warning(
                    "PTT max-hold %.0fs reached — submitting what was held.",
                    self._ptt_max_hold_s,
                )
            hung_up = hangup_task in done or self._hangup_event.is_set()
            # Stop draining + the live feed and let every task settle. ALL must
            # be awaited after cancel — leaving any pending throws "Task was
            # destroyed but it is pending" on the GC pass after the mic closes.
            for t in all_tasks:
                t.cancel()
            for t in all_tasks:
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

        if hung_up:
            return HANGUP_HOTKEY

        # A too-short hold (accidental tap / instant release) carries no real
        # speech — submitting empty audio would just transcribe to nothing or a
        # Whisper hallucination. 300 ms @ 16 kHz int16 = 9600 bytes.
        min_bytes = int(0.3 * 16_000 * 2)
        if len(buffer) < min_bytes:
            log.info(
                "PTT: hold too short (%.0f ms recorded, %.0f ms mic-open) — nothing to submit.",
                (len(buffer) / 2) / 16_000 * 1000,
                (time.monotonic() - mic_open_at) * 1000,
            )
            return HANGUP_TURN_COMPLETE

        pcm = bytes(buffer)
        # The key — not the VAD — is the endpoint, so there is never a forced-cut
        # carry to merge. Clear it defensively before the single turn.
        self._carry_pcm = bytearray()
        self._carry_started_monotonic = None
        self._last_endpoint_reason = None
        await self._set_turn_state(TurnTakingState.WAITING_FOR_FINAL_TRANSCRIPT)
        await self._publish_utterance_captured(pcm)
        # One-shot: run exactly one turn, then end the session regardless of the
        # _handle_utterance return value (it may request continuation, which PTT
        # does not honour). A hangup phrase inside the turn still wins.
        # skip_completion: the key release is the endpoint — submit straight to
        # the brain, never park the turn in the incomplete-sentence buffer.
        await self._handle_utterance(pcm, skip_completion=True)
        if self._hangup_event.is_set():
            return self._session_end_reason or HANGUP_VOICE_PATTERN
        return HANGUP_TURN_COMPLETE

    async def _ptt_live_transcribe(self, snapshot: Callable[[], bytes]) -> None:
        """Background live-transcript feed for the held push-to-talk audio.

        Every ``_ptt_partial_interval_s`` it transcribes the held-so-far buffer
        and publishes it as a non-final ``TranscriptionUpdate`` so the orb
        bubble shows the live transcript while the key is down — parity with the
        wake-word path's VAD stability probe (PTT bypasses the VAD, so it needs
        its own feed). Purely cosmetic and best-effort: every error is swallowed
        and the loop continues. It NEVER drives the turn — the authoritative
        transcription is the final one in ``_handle_utterance``. ``_ptt_session``
        cancels it on release / hangup / max-hold.
        """
        interval = self._ptt_partial_interval_s
        stt = getattr(self, "_utterance_stt", None)
        if stt is None or interval <= 0:
            return
        # A sub-~0.4s snapshot is almost always a Whisper hallucination on
        # near-silence; wait until enough audio has accumulated before probing.
        min_bytes = int(0.4 * 16_000 * 2)
        try:
            while True:
                await asyncio.sleep(interval)
                pcm = snapshot()
                if len(pcm) < min_bytes:
                    continue
                try:
                    transcript = await stt.transcribe_pcm(pcm)
                except Exception as exc:  # noqa: BLE001 — cosmetic, keep going
                    log.debug("PTT live-transcript probe failed: %s", exc)
                    continue
                text = (getattr(transcript, "text", "") or "").strip() if transcript else ""
                if not text:
                    continue
                try:
                    await self._publish_event(
                        TranscriptionUpdate(
                            source_layer="speech.stt",
                            text=text,
                            is_final=False,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    log.debug("PTT live-transcript publish failed: %s", exc)
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Chat mic-dictation (transcribe-only → chat input, never the brain)
    # ------------------------------------------------------------------

    def dictation_available(self) -> bool:
        """True if the server can run mic-dictation (a mic + an STT exist).

        Lets the UI hide the mic button on a headless host with no capture
        device (cloud-first: a missing capability is a clean no-op, AD-OE6).
        """
        return self._utterance_stt is not None and self._input_device != "none"

    def start_dictation(self) -> bool:
        """Begin a transcribe-only dictation session (idempotent-safe).

        Returns ``False`` when it cannot start — a voice/PTT session is active,
        the pipeline is busy, dictation is already running, or no STT is wired.
        The caller (WS handler) turns ``False`` into an honest UI message rather
        than silently doing nothing.

        This NEVER routes to the brain: it spawns ``_dictation_session`` which
        only publishes ``DictationTranscript`` events.
        """
        if self._utterance_stt is None:
            log.info("start_dictation ignored: no STT provider.")
            return False
        if self._dictation_task is not None and not self._dictation_task.done():
            log.info("start_dictation ignored: dictation already running.")
            return False
        if self._ptt_mode or self._state != PipelineState.IDLE:
            log.info("start_dictation ignored: pipeline not idle.")
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.warning("start_dictation: no running loop.")
            return False
        # A fresh dictation session must NOT inherit a stale hangup. ``_hangup_event``
        # is set by every "auflegen" and is otherwise only cleared when the next
        # VOICE session is accepted (``_run_session``). The dictation lane shares
        # that event in its ``asyncio.wait`` gate, so a leftover hangup from an
        # earlier voice call would finalize this session on its first tick — the
        # mic appears to "stop the instant you click it". Clear it here, mirroring
        # the voice-path ``self._hangup_event.clear()`` at session accept.
        hangup = getattr(self, "_hangup_event", None)
        if hangup is not None:
            hangup.clear()
        self._dictation_stop_event = asyncio.Event()
        self._dictation_task = loop.create_task(
            self._dictation_session(), name="chat-dictation"
        )
        log.info("🎙️ dictation started (transcribe-only → chat input).")
        return True

    def stop_dictation(self) -> bool:
        """Signal the active dictation session to finish (best-effort)."""
        if self._dictation_task is None or self._dictation_task.done():
            return False
        self._dictation_stop_event.set()
        return True

    async def _dictation_session(self) -> None:
        """Capture mic audio and stream live transcripts to the chat input.

        A stripped-down ``_ptt_session``: open the mic, drain into a buffer,
        transcribe the held-so-far buffer every ``_ptt_partial_interval_s`` and
        publish a non-final ``DictationTranscript``; on the stop event (or the
        max-duration cap) transcribe once more and publish the final one. It
        deliberately does NOT use the VAD, the brain, TTS, or the turn-state
        machine — the whole thing is wrapped fail-open so a dictation error can
        never break a later real voice turn (BUG-020 discipline).
        """
        stt = self._utterance_stt
        if stt is None:
            return
        buffer = bytearray()
        interval = self._ptt_partial_interval_s if self._ptt_partial_interval_s > 0 else 1.2
        # Sub-0.4s of audio is almost always a near-silence Whisper
        # hallucination; wait until enough has accumulated before transcribing.
        min_bytes = int(0.4 * 16_000 * 2)
        last_published = ""

        async def _probe() -> None:
            """Periodic non-final transcript of the held-so-far buffer."""
            nonlocal last_published
            try:
                while True:
                    await asyncio.sleep(interval)
                    pcm = bytes(buffer)
                    if len(pcm) < min_bytes:
                        continue
                    try:
                        transcript = await stt.transcribe_pcm(pcm)
                    except Exception as exc:  # noqa: BLE001 — cosmetic, keep going
                        log.debug("dictation probe failed: %s", exc)
                        continue
                    text = (getattr(transcript, "text", "") or "").strip()
                    if not text or text == last_published:
                        continue
                    last_published = text
                    try:
                        await self._publish_event(
                            DictationTranscript(
                                source_layer="speech.dictation",
                                text=text,
                                is_final=False,
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.debug("dictation partial publish failed: %s", exc)
            except asyncio.CancelledError:
                pass

        try:
            async with MicrophoneCapture(
                device=self._input_device, device_priority=self._input_priority
            ) as mic:

                async def _drain() -> None:
                    async for chunk in mic.stream():
                        buffer.extend(chunk.pcm)

                drain_task = asyncio.create_task(_drain(), name="dictation-drain")
                probe_task = asyncio.create_task(_probe(), name="dictation-probe")
                stop_task = asyncio.create_task(self._dictation_stop_event.wait())
                hangup_task = asyncio.create_task(self._hangup_event.wait())
                wait_set = {stop_task, hangup_task, drain_task}
                all_tasks = [drain_task, probe_task, stop_task, hangup_task]
                try:
                    await asyncio.wait(
                        wait_set,
                        timeout=self._dictation_max_s,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    for t in all_tasks:
                        t.cancel()
                    for t in all_tasks:
                        try:
                            await t
                        except (asyncio.CancelledError, Exception):  # noqa: BLE001
                            pass

            # One final transcription of the whole capture.
            pcm = bytes(buffer)
            final_text = ""
            if len(pcm) >= min_bytes:
                try:
                    transcript = await stt.transcribe_pcm(pcm)
                    final_text = (getattr(transcript, "text", "") or "").strip()
                except Exception as exc:  # noqa: BLE001
                    log.debug("dictation final transcribe failed: %s", exc)
            try:
                await self._publish_event(
                    DictationTranscript(
                        source_layer="speech.dictation",
                        text=final_text,
                        is_final=True,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                log.debug("dictation final publish failed: %s", exc)
            log.info("🎙️ dictation ended (%d chars).", len(final_text))
        except Exception:  # noqa: BLE001 — dictation must never break voice
            log.warning("dictation session crashed (non-fatal)", exc_info=True)
            try:
                await self._publish_event(
                    DictationTranscript(
                        source_layer="speech.dictation", text="", is_final=True
                    )
                )
            except Exception:  # noqa: BLE001
                pass

    async def _session_input_stream(
        self, chunks: AsyncIterator[AudioChunk]
    ) -> AsyncIterator[AudioChunk]:
        """Filtert Mic-Frames, die waehrend/nach Jarvis-TTS aufgenommen wurden."""
        dropped = 0
        async for chunk in chunks:
            # Input mute (Jarvis-scoped): while muted, drop the user's audio at
            # OUR input boundary so Jarvis stops hearing them mid-session —
            # without touching the OS microphone, so every other app keeps the
            # mic. The mute flag already gated wake activation + TTS output; this
            # closes the active-session input gap ("ich rede, aber er hoert mich
            # trotzdem", 2026-06-28).  # i18n-allow
            if getattr(self, "_muted", False):
                continue
            if self._should_drop_session_input(chunk):
                dropped += 1
                continue
            if dropped:
                log.info("TTS-Echo-Sperre: %d Mic-Chunk(s) verworfen.", dropped)
                dropped = 0
            yield chunk

    def _should_drop_session_input(self, chunk: AudioChunk) -> bool:
        until_ns = getattr(self, "_input_suppressed_until_ns", 0)
        if until_ns <= 0:
            return False
        chunk_ts = getattr(chunk, "timestamp_ns", 0) or time.time_ns()
        if chunk_ts < until_ns:
            return True
        self._input_suppressed_until_ns = 0
        return False

    def _suppress_session_input_after_tts(self, reason: str) -> None:
        seconds = max(0.0, float(getattr(self, "_post_tts_listen_suppression_s", 0.0)))
        if seconds <= 0.0:
            return
        until_ns = time.time_ns() + int(seconds * 1_000_000_000)
        previous = getattr(self, "_input_suppressed_until_ns", 0)
        self._input_suppressed_until_ns = max(previous, until_ns)
        log.info("TTS-Echo-Sperre aktiv: %.1fs (%s).", seconds, reason)

    def _voice_confirm_pending(self) -> bool:
        """True while the brain is awaiting a spoken yes/no for a deferred
        ``ask``-tier tool — keep the session open so the answer is not cut off
        (analogous to ``_background_mission_in_flight``). Forensic 2026-06-26: an
        ask-tier tool asked "really do that?" and the session then ended before
        the user could answer. Defensive: a brain callback without the probe (the
        echo fake, an older build) reports no pending confirm and never crashes
        the hangup decision."""
        brain = getattr(self, "_brain", None)
        probe = getattr(brain, "has_pending_voice_confirm", None)
        if not callable(probe):
            return False
        try:
            return bool(probe())
        except Exception:  # noqa: BLE001 — the hangup decision must never crash
            return False

    async def _finish_after_response(self, *, barged: bool = False) -> bool:
        """Schliesst normale Voice-Turns; Barge-in darf weiterlaufen.

        Spawn-in-flight override: a force-spawn-worker ACK ("Mach ich, ich
        lasse dafuer einen OpenClaw-Subagent ...") is a promise, not the
        answer -- the actual answer arrives 30-90 s later as the mission
        readback via ``OpenClawBackgroundCompleted``. Hanging up after the
        ACK closes the mic context (see ``_active_session``'s
        ``MicrophoneCapture`` block), the readback plays into a dead
        session, and the user has to re-wake to continue. While at least
        one entry sits in ``_spawn_watchdog_tasks`` we therefore keep the
        turn open. Single-turn-mode is re-asserted naturally on the next
        ``_finish_after_response`` call once the readback has been
        delivered and the watchdog has been popped.
        """
        if (
            barged
            or self._continue_listening_after_response
            or self._background_mission_in_flight()
            or self._voice_confirm_pending()
        ):
            await self._set_turn_state(TurnTakingState.LISTENING)
            return True
        self._session_end_reason = HANGUP_TURN_COMPLETE
        await self._set_turn_state(TurnTakingState.IDLE)
        return False

    async def _on_audio_out_first(self, event: AudioOutFirst) -> None:
        """Mark perceived time-to-first-audio once per turn (ack or brain)."""
        tracker = self._latency_tracker
        if tracker is not None and not self._latency_first_audio_marked:
            self._latency_first_audio_marked = True
            tracker.mark(LatencyPhase.TURN_TO_FIRST_AUDIO)

    async def _transcribe_final(self, pcm: bytes) -> Transcript | None:
        """Final utterance transcription with transient-error retry (AD-OE6).

        The in-utterance stability probe fires a cloud-STT call every ~650 ms
        and shares one rate budget with this final call, so under speech the
        provider can return ``429 Too Many Requests`` — and the final call used
        to inherit it and silently drop the turn ("Jarvis listens forever, never
        answers", 2026-05-25). The probe stops the instant the VAD endpoint
        fires, so the rate window frees within ~1 s: we retry *transient*
        failures (429 / 5xx / timeout) with capped backoff. A *non-transient*
        error (401 bad key, 400 bad audio) fails fast. Returns ``None`` only
        when every attempt failed — the caller then speaks an apology instead of
        going mute.
        """
        last_exc: BaseException | None = None
        for attempt in range(_STT_FINAL_RETRIES + 1):
            stt_task = asyncio.create_task(
                self._utterance_stt.transcribe_pcm(pcm), name="stt-final"
            )
            try:
                return await asyncio.wait_for(
                    stt_task, timeout=self._stt_final_timeout_s
                )
            except TimeoutError as exc:
                stt_task.cancel()
                last_exc = exc
                log.warning(
                    "STT final timeout after %.1fs (attempt %d/%d)",
                    self._stt_final_timeout_s,
                    attempt + 1,
                    _STT_FINAL_RETRIES + 1,
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not _is_transient_stt_error(exc):
                    log.exception("STT finalization failed (non-retryable): %s", exc)
                    return None
                log.warning(
                    "STT transient error %s (attempt %d/%d)",
                    _stt_error_status(exc),
                    attempt + 1,
                    _STT_FINAL_RETRIES + 1,
                )
            if attempt < _STT_FINAL_RETRIES:
                await asyncio.sleep(_stt_retry_delay(last_exc, attempt))
        log.error(
            "STT final exhausted %d attempts (last error: %s)",
            _STT_FINAL_RETRIES + 1,
            last_exc,
        )
        return None

    async def _handle_utterance(self, pcm: bytes, *, skip_completion: bool = False) -> bool:
        """Run one utterance turn, then flush its latency row (Wave 0).

        The ``LatencyTurnComplete`` flush lives HERE (not inside the turn
        body) because the body has a dozen return paths — a ``finally`` is
        the only way "one finalized turn = exactly one flush" holds for all
        of them. The tracker is cleared up-front so a fresh turn can never
        inherit (and flush) the previous turn's marks; the body re-creates
        it once the utterance is actually finalized, so carry fragments and
        empty tail flushes never produce a row.
        """
        self._latency_tracker = None
        try:
            self._continuation_dispatched_this_turn = False
            return await self._handle_utterance_turn(
                pcm, skip_completion=skip_completion
            )
        finally:
            if getattr(self, "_continuation_dispatched_this_turn", False):
                win = getattr(self, "_continuation_window", None)
                if win is not None:
                    win.mark_idle()
            # Drop a parked recombine that never reached dispatch (a guard
            # returned early) so it cannot leak into the next turn.
            self._continuation_pending_drop = None
            self._emit_latency_turn_complete()

    def _emit_latency_turn_complete(self) -> None:
        """Fire-and-forget flush of this turn's stage snapshot.

        AP-9/AP-18 discipline: telemetry never blocks and never breaks the
        hot path — emission is a created task, every error is swallowed.
        """
        tracker = getattr(self, "_latency_tracker", None)
        bus = getattr(self, "_bus", None)
        if tracker is None or bus is None or not tracker.enabled:
            return
        stages = tracker.stages_snapshot()
        if not stages:
            return
        try:
            event = LatencyTurnComplete(
                trace_id=tracker.trace_id,
                source_layer="speech.pipeline",
                anchor_ns=tracker.anchor_ns,
                stages_ms=stages,
                errors=tracker.errors_snapshot(),
            )
            asyncio.create_task(bus.publish(event))  # noqa: RUF006 — fire-and-forget
        except Exception:  # noqa: BLE001 — telemetry must never break the turn
            log.debug("LatencyTurnComplete emit failed", exc_info=True)

    def _maybe_recombine_continuation(self, text: str) -> tuple[str, bool]:
        """Unit C: if the user kept talking while the brain was thinking/speaking
        (or within the short grace afterwards), return the COMBINED text plus a
        ``continued=True`` flag for the subsequent ``_arm_continuation`` call.

        A cancel phrase ("vergiss das") clears the window and never merges.
        Fail-open: any error returns ``(text, False)`` — the user is never
        swallowed (AD-OE6). No-op when the feature is disabled or unarmed.
        """
        if not getattr(self, "_continuation_interrupt_enabled", False):
            return text, False
        window = getattr(self, "_continuation_window", None)
        if window is None:
            return text, False
        if is_cancel(text):
            window.clear()
            return text, False
        try:
            combined = window.try_recombine(text)
        except Exception:  # noqa: BLE001 — fail-open by contract
            log.warning("ContinuationWindow.try_recombine raised; failing open", exc_info=True)
            return text, False
        if not combined or combined == text:
            return text, False
        log.info("↪ Continuation recombine → %r", combined[:120])
        # Consume the window NOW so a later guard (ContinuationBuffer hold,
        # privacy/skill early-return) that prevents dispatch cannot leave the old
        # prior armed to re-merge on the next utterance (double-coalescing). DEFER
        # the history drop to _arm_continuation so it is applied only when this
        # turn truly dispatches — an early return must not mutate history for a
        # turn the brain never sees.
        self._continuation_pending_drop = window.text
        window.clear()
        return combined, True

    def _arm_continuation(self, text: str, *, continued: bool) -> None:
        """Unit A: record the text we are about to dispatch so the NEXT
        utterance can re-attach to it. Flags that this turn dispatched, so the
        turn-end hook starts the grace countdown only for armed turns. No-op
        when disabled."""
        if not getattr(self, "_continuation_interrupt_enabled", False):
            return
        window = getattr(self, "_continuation_window", None)
        if window is None:
            return
        try:
            # Deferred history drop: a recombine earlier this turn parked the
            # prior text; apply it ONLY now that the turn actually dispatches, so
            # an early-returning guard never mutated history.
            prior = getattr(self, "_continuation_pending_drop", None)
            if prior:
                self._continuation_pending_drop = None
                brain = getattr(self, "_brain", None)
                if brain is not None and hasattr(brain, "drop_last_turn"):
                    try:
                        brain.drop_last_turn(prior)
                    except Exception:  # noqa: BLE001 — history hygiene never crashes the turn
                        log.debug("drop_last_turn failed (non-fatal)", exc_info=True)
            window.note_dispatch(text, continued=continued)
            self._continuation_dispatched_this_turn = True
        except Exception:  # noqa: BLE001
            log.debug("continuation note_dispatch failed (non-fatal)", exc_info=True)

    async def _handle_utterance_turn(
        self, pcm: bytes, *, skip_completion: bool = False
    ) -> bool:
        # ``skip_completion`` bypasses the incomplete-sentence buffer: the
        # caller guarantees this utterance is a COMPLETE turn. Push-to-talk
        # sets it because the key release is the explicit endpoint — there is
        # no "user paused mid-sentence" ambiguity to wait out, so buffering
        # would only add a spurious flush-timer delay before the brain runs.
        # --- Long-dictation accumulation (forced-cut merge) ----------------
        # The VAD force-cuts a continuous utterance at its max-length cap and
        # yields a fragment with reason "max_utterance" (recorded on
        # self._last_endpoint_reason by _on_vad_endpoint just before the
        # fragment was yielded). Such a cut means the user is STILL talking:
        # buffer the fragment and keep listening instead of running an
        # independent, truncated brain turn. Only a natural endpoint
        # (silence / stt_stable) finalizes the merged audio. Guardrails cap
        # the carry so a stuck mic cannot accumulate forever.
        #
        # Defensive getattr: test fixtures construct the pipeline via
        # ``SpeechPipeline.__new__`` (see the _ack_brain note below) and do
        # not always set these fields; a missing field means "no accumulation
        # in progress", which preserves the legacy single-turn behaviour.
        reason = getattr(self, "_last_endpoint_reason", None)
        self._last_endpoint_reason = None
        carry = getattr(self, "_carry_pcm", None)
        if carry:
            pcm = bytes(carry) + pcm
        if reason in FORCED_CUT_REASONS:
            now = time.monotonic()
            started = getattr(self, "_carry_started_monotonic", None)
            if started is None:
                started = now
                self._carry_started_monotonic = now
            self._carry_pcm = bytearray(pcm)
            runaway = (
                len(self._carry_pcm) > _MAX_CARRY_PCM_BYTES
                or (now - self._carry_started_monotonic) > _MAX_CARRY_SECONDS
            )
            if not runaway:
                log.info(
                    "↪ Forced-cut (reason=%s): carry %.1f KB, keep listening.",
                    reason,
                    len(self._carry_pcm) / 1024,
                )
                await self._set_turn_state(TurnTakingState.LISTENING)
                return True
            log.warning(
                "Forced-cut carry runaway (%.1f KB / %.1fs) — finalizing turn.",
                len(self._carry_pcm) / 1024,
                now - (self._carry_started_monotonic or now),
            )
        # Natural end (or runaway): finalize the merged audio as one turn.
        self._carry_pcm = bytearray()
        self._carry_started_monotonic = None
        if not pcm:
            # Empty VAD tail flush with nothing buffered (e.g. the runaway
            # guard already finalized the carry): zero bytes of audio — skip
            # the STT round-trip and keep listening.
            await self._set_turn_state(TurnTakingState.LISTENING)
            return True
        # -------------------------------------------------------------------
        # Wave 0 (omni-latency): anchor a fresh per-turn latency tracker at
        # utterance finalize. perf_counter marks are free; emission is
        # fire-and-forget so the hot path never blocks on telemetry.
        lat_cfg = getattr(self._config, "latency", None)
        self._latency_first_audio_marked = False
        # Per-turn re-arm of the beheaded-playback mark (BUG-032 lesson: never
        # let a previous turn's abort leak into this turn's empty-turn handling).
        self._playback_aborted_no_first_frame = False
        # Per-turn re-arm of the timeout-terminal mark: a fresh utterance must
        # be free to speak its answer even if the PREVIOUS turn ended in a
        # timeout notice (the double-answer guard is scoped to ONE utterance).
        self._brain_timeout_spoken_this_turn = False
        # Anchor this brain-bound turn's wall-clock here — the single point past
        # all early returns (forced-cut carry, empty PCM, wake-only) where the
        # turn commits to the brain. The floor guard in _speak_brain_timeout uses
        # it to refuse a "took too long" phrase on a turn that genuinely ran
        # under the floor (the sub-second spurious-apology bug, 2026-06-14).
        self._turn_start_monotonic = time.monotonic()
        self._latency_tracker = LatencyTracker(
            self._bus,
            uuid4(),
            enabled=getattr(lat_cfg, "enabled", True),
        )
        utt_stt_name = getattr(
            self._utterance_stt, "provider_label", type(self._utterance_stt).__name__
        )
        log.info("→ Transcribing (%.1f KB) via %s …", len(pcm) / 1024, utt_stt_name)
        await self._set_turn_state(TurnTakingState.WAITING_FOR_FINAL_TRANSCRIPT)
        transcript = await self._transcribe_final(pcm)
        if transcript is None:
            # AD-OE6 zero-silent-drop: every retry of the final transcription
            # failed (e.g. a sustained Groq 429 rate-limit). Do NOT slip back to
            # LISTENING in silence — the user would keep talking into a void
            # ("Jarvis listens forever, never answers"). Say we missed it, then
            # resume so they can simply repeat.
            await self._speak_stt_unavailable()
            await self._set_turn_state(TurnTakingState.LISTENING)
            return True
        log.info(
            "transcript final: text=%r language=%s confidence=%.3f",
            transcript.text,
            transcript.language,
            getattr(transcript, "confidence", 0.0) or 0.0,
        )
        # Tag whether this finalized utterance will be re-attached to the still-
        # open turn by the continuation-recombine path (brain mid-thinking, window
        # live). NON-mutating mirror of _maybe_recombine_continuation's gate
        # (enabled + armed/live + not a cancel phrase) — so the SessionRecorder
        # records the coalesced fragments as ONE turn instead of splitting them.
        _cont_win = getattr(self, "_continuation_window", None)
        continues_previous = bool(
            getattr(self, "_continuation_interrupt_enabled", False)
            and _cont_win is not None
            and _cont_win.is_live()
            and not is_cancel(transcript.text)
        )
        await self._publish_event(
            TranscriptFinal(
                source_layer="speech.stt",
                transcript=transcript,
                continues_previous=continues_previous,
            )
        )
        if self._latency_tracker is not None:
            self._latency_tracker.mark(LatencyPhase.STT_FINALIZE)
        text = transcript.text.strip()
        if not text:
            await self._set_turn_state(TurnTakingState.LISTENING)
            return True

        # Empty-Turn-Guard: reine Wake-Words ohne Command gehen nicht ans
        # Brain. Sonst halluziniert das LLM ein zweites "Ja?" / "Sir?" ueber
        # den bereits abgespielten ACK. Rueckkehr zu LISTENING, User kann den
        # eigentlichen Command nachreichen.
        if _is_wake_only(text):
            log.info("🤫 Wake-only-Turn (%r) — skip Brain, weiter zuhören.", text)
            await self._set_turn_state(TurnTakingState.LISTENING)
            return True

        # Hangup muss vor dem STT-Halluzinationsfilter laufen: kurze
        # "Auflegen"-Turns werden von Whisper gelegentlich als "Vielen Dank"
        # transkribiert, was sonst als Halluzination verworfen wuerde.
        if HANGUP_RE.search(text):
            log.info("Voice-Hangup via Regex (%r) - lege auf.", text)
            self._trigger_voice_hangup()
            return False

        # STT-Halluzinations-Guard: Whisper transkribiert bei Speaker-Leak /
        # leisem Mic manchmal Werbe-Outros, Copyright-Strings, YouTube-
        # Endcards. Diese Phrasen nie ans Brain — sonst ruft Gemini
        # open_app('WDR mediagroup GmbH im Auftrag des WDR, 2020').
        if _STT_HALLUCINATION_RE.search(text):
            log.info("🚫 STT-Halluzination erkannt (%r) — skip Brain.", text[:80])
            await self._set_turn_state(TurnTakingState.LISTENING)
            return True

        # Turn language for EVERY output layer this turn (ack preamble, canned
        # phrases, TTS voice). An explicit ``brain.reply_language`` pin wins;
        # otherwise the transcribed TEXT decides and the STT tag breaks ties
        # (live bug 2026-06-10 23:12: ``[stt].language = "de"`` pins Groq
        # Whisper, which echoes the pin back — English speech was tagged
        # ``language=german``). Honoring the pin HERE is what stops a German
        # utterance mis-transcribed as English from dragging the whole chain
        # into English (forensic 2026-06-18). Normalized to codes
        # ("de"/"en"/"es") so the TTS voice-pin maps ({"de": "de-DE"}) stop
        # missing on name-shaped tags ("german").
        lang = self._output_language(getattr(transcript, "language", None), text)
        log.info("👤 User [%s]: %s", lang, text)

        # Continuation recombine: attach this utterance to a just-dispatched one
        # if the user kept talking while the brain was thinking/speaking. Runs
        # AFTER the hangup / wake-only / hallucination guards above (they already
        # returned) and BEFORE the ContinuationBuffer below, so the combined text
        # is re-classified for syntactic completeness as a whole.
        text, _continued_dispatch = self._maybe_recombine_continuation(text)

        await self._publish_event(
            TranscriptionUpdate(
                source_layer="speech.stt",
                text=transcript.text,
                is_final=True,
            )
        )

        # Continuation-Buffer (Spec: incomplete-prompt completion). If this
        # utterance ends open (trailing comma / conjunction / determiner /
        # preposition), hold it and wait up to 8s for the continuation. On
        # the next complete utterance, join + dispatch as ONE brain turn.
        # Without this ONE user task fragments into multiple sub-agent
        # missions (live regression 2026-05-26 12:13 — VAD cut "…wird," and
        # the continuation triggered a SEPARATE spawn_worker). Fail-open:
        # on any classifier exception we dispatch the utterance as-is so the
        # user is never silently swallowed (AD-OE6).
        #
        # A fresh utterance arrived → cancel any pending clarifying-question OR
        # continuation-drain timer from a previous incomplete fragment: the user
        # kept the floor, so neither must fire on top of them (and the drain must
        # not double-dispatch alongside this turn's join).
        self._cancel_clarify_question()
        self._cancel_continuation_drain()
        try:
            coalesced = self._continuation_buffer.process(text, language=lang)
        except Exception:  # noqa: BLE001 — fail-open by contract
            log.warning("Continuation-Buffer raised; failing open", exc_info=True)
            coalesced = text
        if coalesced is None:
            # Incomplete fragment held by the ContinuationBuffer (which has no
            # active timeout of its own). Arm the clarifying-question timer so a
            # user who trails off is never left in silence — after the grace
            # window Jarvis asks "Wie meinst du das genau?" instead of waiting
            # forever ("hört für immer zu" fix 2026-06-08; AD-OE6). A
            # continuation cancels it at the top of the next turn. Surface
            # WAITING_FOR_COMPLETION so the UI hints "…waiting for the rest".
            #
            # A genuine trail-off (REASON_TRAILING_ELLIPSIS) FORCES the question
            # even when the clarify feature is globally off — the maintainer
            # opted into that one case (2026-06-14). Every other incomplete
            # reason keeps the silent-hold default (2026-06-09 mandate).
            reason = getattr(self._continuation_buffer, "last_reason", "")
            force_clarify = reason == REASON_TRAILING_ELLIPSIS
            armed = self._arm_clarify_question(lang, force=force_clarify)
            if not armed:
                # No clarifying question was scheduled for this held fragment
                # (the silent-hold default for a non-trail-off incomplete with
                # the clarify feature off). The ContinuationBuffer has no timer
                # of its own, so without an autonomous flush the fragment hangs
                # in LISTENING until the session idle-timeout silently discards
                # it — the brain never sees it ("Jarvis hört für immer zu" wedge
                # 2026-06-19, session da25113a: the complete tag question
                # "…Montag, oder?" was held as a trailing conjunction and dropped
                # 30 s later, never answered). Arm a drain timer that DISPATCHES
                # the held fragment to the brain after the grace window so the
                # user always gets an answer attempt (AD-OE6 zero-silent-drop).
                self._arm_continuation_drain(lang)
            await self._set_turn_state(TurnTakingState.WAITING_FOR_COMPLETION)
            return True
        if coalesced != text:
            log.info(
                "Continuation-Buffer joined fragment(s) → %r",
                coalesced[:120],
            )
            text = coalesced

        # Privacy-Voice-Toggle (Wave-2 B7): matcht Privacy-Phrasen aus Config,
        # pausiert/resumed den VisionContextProvider und spricht kurzen ACK
        # BEVOR das Brain aufgerufen wird. Brain-Call wird uebersprungen.
        if self._vision_provider is not None:
            _action = self._match_privacy_phrase(text)
            if _action == "pause":
                log.info("🙈 Vision-Privacy: pause via Voice ('%s')", text)
                try:
                    self._vision_provider.pause()
                except Exception as exc:  # noqa: BLE001
                    log.warning("Vision-pause() fehlgeschlagen: %s", exc)
                await self._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
                try:
                    await self._speak(
                        "Ja, Ruben.",  # i18n-allow: bilingual TTS voice ack
                        language=lang,
                        kind=SPOKEN_KIND_PRIVACY,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("Privacy-ACK-speak fehlgeschlagen: %s", exc)
                await self._set_turn_state(TurnTakingState.LISTENING)
                return True
            if _action == "resume":
                log.info("👁 Vision-Privacy: resume via Voice ('%s')", text)
                try:
                    self._vision_provider.resume()
                except Exception as exc:  # noqa: BLE001
                    log.warning("Vision-resume() fehlgeschlagen: %s", exc)
                await self._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
                try:
                    await self._speak(
                        "Ich sehe wieder.",  # i18n-allow: bilingual TTS voice ack
                        language=lang,
                        kind=SPOKEN_KIND_PRIVACY,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("Privacy-ACK-speak fehlgeschlagen: %s", exc)
                await self._set_turn_state(TurnTakingState.LISTENING)
                return True

        if not skip_completion:
            text = await self._complete_or_buffer_context(text, lang=lang)
        if text is None:
            # If the completion buffer is now holding a fragment, surface the
            # dedicated state so the Orb/UI can hint "…waiting for the rest".
            # Otherwise (cancelled / disabled / parallel-design path) fall back
            # to plain LISTENING.
            _buf = getattr(self, "_completion_buffer", None)
            if _buf is not None and _buf.is_pending:
                await self._set_turn_state(TurnTakingState.WAITING_FOR_COMPLETION)
                # Surface the merged buffer text to the UI bubble so it shows
                # the user's so-far-spoken sentence across the pause — without
                # this the orb bubble would be stuck on the pre-final partial
                # and the user perceives the bubble as "lost". is_final=True
                # marks it as a stable user-side transcript (NOT a brain
                # dispatch — brain dispatch is driven by the return value of
                # _complete_or_buffer_context, not by this event).
                try:
                    await self._publish_event(
                        TranscriptionUpdate(
                            source_layer="speech.completion_buffer",
                            text=_buf.fragment,
                            is_final=True,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    log.debug("Pending-fragment bubble publish failed: %s", exc)
            else:
                await self._set_turn_state(TurnTakingState.LISTENING)
            return True

        # Skills-Brain-Integration: Phase Skills-1 — Pre-Brain-Hook.
        # Wenn ein Skill ein deterministisches Voice-Pattern matched, fuehren
        # wir ihn direkt aus, ohne Brain. Latenz-Win: ~50 ms statt ~800 ms.
        # Bei No-Match faellt der Code-Pfad in den normalen Brain-Call.
        if await self._try_skill_direct_trigger(text, lang):
            return True

        # User hat aufgehoert zu sprechen, Brain rechnet — Orb auf "think"
        await self._set_turn_state(TurnTakingState.PROCESSING)
        # Arm/refresh the continuation window with the text we are dispatching.
        self._arm_continuation(text, continued=_continued_dispatch)
        log.info("→ Brain …")
        if self._latency_tracker is not None:
            self._latency_tracker.mark(LatencyPhase.INTENT_DECISION)

        # Pre-Thinking-Ack Flash-Brain: spawn parallel acknowledgment task
        # BEFORE the main brain starts thinking. The Flash-Brain sees only
        # the raw utterance + persona prompt (no router/tool context) and
        # publishes its output as AnnouncementRequested(kind="preamble") on
        # the bus. The existing _on_announcement handler will run it
        # through TTS while the main brain is still working.
        # Task is fire-and-forget — failures are swallowed inside
        # _spawn_flash_brain_ack so they cannot affect the main brain path.
        # Defensive getattr: test fixtures bypass the ctor via
        # ``SpeechPipeline.__new__(SpeechPipeline)`` (see e.g.
        # tests/unit/speech/test_turn_taking.py:65) and don't always
        # set every attribute. Treat a missing _ack_brain as disabled.
        if getattr(self, "_ack_brain", None) is not None:
            asyncio.create_task(  # noqa: RUF006 — intentional fire-and-forget
                self._spawn_flash_brain_ack(text, lang),
                name="flash-brain-ack",
            )

        # Latenz-Sprint-1: Streaming-Pfad — Brain-Output wird Satz-fuer-Satz
        # an die TTS gereicht waehrend das Brain noch generiert. Speakt
        # selbst; danach return ohne den klassischen Filter+_speak-Pfad.
        # Master-Switch in [performance].streaming_tts (Default: True).
        if self._streaming_enabled():
            try:
                # No-progress (stall) guard instead of a total wall-clock cap:
                # a vision/tool turn that keeps working is never guillotined
                # mid-work; only a genuinely stalled provider speaks the
                # fallback (live bug 2026-06-01, see _run_brain_with_stall_guard).
                response, barged = await self._run_brain_with_stall_guard(
                    self._brain_streaming(text, lang),
                    interrupt_monitor=True,
                )
            except TimeoutError:
                if self._should_speak_stall_fallback():
                    log.warning(
                        "Brain-Stream stalled (no progress for %.1fs / ceiling "
                        "%.1fs) — speaking fallback",
                        self._brain_timeout_s,
                        self._brain_hard_timeout_s,
                    )
                    # AD-OE6 zero-silent-drop: a stalled brain that said NOTHING
                    # must be SPOKEN, not dropped to LISTENING in silence (live
                    # bug 2026-05-29: "Claude Code oeffnen" stalled, hung up mute).  # i18n-allow
                    await self._speak_brain_timeout(lang, site="stream_stall")
                else:
                    # Real answer already (partially) spoken this turn — prefer
                    # it; a canned phrase on top would overlap/garble the output
                    # the user is already hearing (live bug 2026-06-02).
                    log.warning(
                        "Brain-Stream stalled (no progress for %.1fs / ceiling "
                        "%.1fs) — real output already spoken, suppressing "
                        "fallback phrase",
                        self._brain_timeout_s,
                        self._brain_hard_timeout_s,
                    )
                await self._set_turn_state(TurnTakingState.LISTENING)
                return True
            except Exception as exc:  # noqa: BLE001
                log.exception("Brain-Stream fehlgeschlagen: %s", exc)
                await self._set_turn_state(TurnTakingState.LISTENING)
                return True
            log.info("🤖 Jarvis [%s] (streamed): %s", lang, response)
            if not response.strip():
                if barged:
                    # Interrupted before any answer (continuation interrupt or an
                    # early barge): stay silent — the next utterance recombines
                    # with this prompt. A clarifying question here would talk over
                    # the user who is still going.
                    return await self._finish_after_response(barged=barged)
                # AD-OE6 zero-silent-drop. A *total* provider-chain failure is
                # spoken; a fire-and-forget spawn stays silent (bus reports);
                # ANY other empty turn (function_call/CU without speech, empty
                # content) gets a spoken clarifying question instead of muting —
                # the dominant "Jarvis antwortet nie" cause (logs 2026-06-08).
                await self._handle_silent_brain_turn(lang, text)
                await self._set_turn_state(TurnTakingState.LISTENING)
                return True
            normalized = response.strip().rstrip("!.").strip().lower()
            # `response` is the RAW streamed full text (still carries the
            # sentinel); the spoken sentences were already scrubbed inside
            # _brain_streaming. Legacy exact farewells stay supported.
            is_hangup = contains_end_signal(response) or is_legacy_farewell(normalized)
            if is_hangup:
                log.info("🔚 Voice-Hangup via Brain-Signal (streamed) — lege auf.")
                self._trigger_voice_hangup(stop_player=False)
                return False
            return await self._finish_after_response(barged=barged)

        try:
            # Non-streaming fallback path (no ``generate_stream`` on the brain,
            # or ``[performance].streaming_tts=false``). There is no per-chunk /
            # tool-boundary progress signal here, so ``brain_timeout_s`` is
            # necessarily applied as a TOTAL wall-clock cap — unlike the streaming
            # path above, which uses it as a no-progress (stall) window. This path
            # is a production minority; the stall fix lives on the streaming path.
            response = await asyncio.wait_for(
                self._brain_with_ack(text, lang),
                timeout=self._brain_timeout_s,
            )
        except TimeoutError:
            log.warning(
                "Brain-Call timed out after %.1fs (non-streaming total cap) — "
                "speaking fallback",
                self._brain_timeout_s,
            )
            # AD-OE6 zero-silent-drop: speak the timeout instead of silent LISTENING.
            await self._speak_brain_timeout(lang, site="nonstream_total_cap")
            await self._set_turn_state(TurnTakingState.LISTENING)
            return True
        except Exception as exc:  # noqa: BLE001
            # Brain-Fehler (429, OAuth-Refresh, Netz) duerfen die Session
            # nicht toeten. User-Wunsch 2026-04-25: keine Standard-Phrase
            # ("Da ist beim Denken etwas schiefgelaufen ..."). Pipeline
            # schweigt, kehrt zurueck zu LISTENING; der Fehler wird im Log
            # sichtbar und ueber den Bus an die UI gemeldet.
            log.exception("Brain-Call fehlgeschlagen: %s", exc)
            await self._set_turn_state(TurnTakingState.LISTENING)
            return True
        if not response.strip():
            # AD-OE6 zero-silent-drop (non-streaming path). Total failure →
            # spoken; fire-and-forget spawn → silent (bus reports); any other
            # empty turn → spoken clarifying question instead of muting.
            await self._handle_silent_brain_turn(lang, text)
            await self._set_turn_state(TurnTakingState.LISTENING)
            return True

        # Paraphrase-Strip: "Ich verstehe, du moechtest..." wird abgeschnitten.
        # Butler-Feeling statt LLM-Echo. Prompt verbietet das zwar, aber
        # Gemini Flash produziert es trotzdem gelegentlich.
        response = _strip_paraphrase_prefix(response)
        if not response.strip() or _is_non_substantive_response(response):
            fallback = _smalltalk_fallback_for_non_substantive(text, lang)
            if fallback:
                response = fallback
            else:
                log.info("Filler-/ACK-Response unterdrueckt: %r", response)
                await self._set_turn_state(TurnTakingState.LISTENING)
                return True

        if not response.strip():
            log.info("Filler-/ACK-Response unterdrueckt: %r", response)
            await self._set_turn_state(TurnTakingState.LISTENING)
            return True

        # Hang-up intent must be read from the RAW brain response, BEFORE
        # scrub_for_voice strips the [[END_CALL]] sentinel below.
        _normalized_raw = response.strip().rstrip("!.").strip().lower()
        is_hangup = contains_end_signal(response) or is_legacy_farewell(_normalized_raw)

        # Phase-1-Output-Filter (Persona-Mandat): Tool-JSON, Stacktraces,
        # Engineering-Jargon, Self-Reference, Echo-/Filler-Opener vor TTS
        # rausnehmen. Defense-in-Depth — die Pre-Filter oben (paraphrase_prefix,
        # non_substantive) bleiben fuer schnelle Suppression, scrub_for_voice
        # ist die zentrale Schwarzliste.
        scrubbed = scrub_for_voice(response, language=lang)
        if scrubbed.actions:
            log.info(
                "🧹 Output-Filter [%s]: %s (fallback=%s)",
                lang, scrubbed.actions, scrubbed.fallback_used,
            )
        response = scrubbed.cleaned
        if not response.strip():
            log.info("Output-Filter hinterlaesst leeren Text — schweige.")
            await self._set_turn_state(TurnTakingState.LISTENING)
            return True

        log.info("🤖 Jarvis [%s]: %s", lang, response)

        # Jarvis spricht — Orb-Mode wechselt zur Speak-Wellenform
        await self._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
        barged = await self._speak(response, language=lang)
        if is_hangup:
            log.info("🔚 Voice-Hangup via Brain-Signal — lege auf.")
            self._trigger_voice_hangup(stop_player=False)
            return False
        return await self._finish_after_response(barged=barged)

    async def _complete_or_buffer_context(
        self, text: str, lang: str = "de"
    ) -> str | None:
        """Incomplete-prompt completion gate (precision-over-recall design).

        Spec: docs/superpowers/specs/2026-05-25-incomplete-prompt-completion-design.md

        Returns the text to dispatch to the brain, or ``None`` if the fragment
        was buffered (caller stays silent and returns to LISTENING /
        WAITING_FOR_COMPLETION).

        Behaviour:
        * Fresh turn, classifier sees no clear dangling marker → return ``text``
          unchanged (precision: complete or unsure → answer).
        * Fresh turn, classifier returns a verdict → buffer the fragment, arm
          the per-gap timeout, return ``None``. The pipeline stays silent and
          the mic stays open (user-mandated "still re-listen").
        * Buffer pending, continuation arrives → concatenate; if the joined
          text is now complete OR ``chain_count >= completion_max_chain`` →
          flush to brain (return joined text); else keep waiting.
        * Buffer pending, cancel phrase ("vergiss das" / "never mind") →
          discard and return ``None``.
        * Per-gap timeout (``completion_wait_ms``) fires elsewhere → speak a
          short follow-up cue and clear the buffer (AD-OE6 / zero silent drops).

        NOTE: The parallel-session helpers ``_schedule_pending_flush`` /
        ``_pending_flush_after_delay`` / ``_emit_completeness_signal`` from the
        sibling utterance-completeness design are still present in this file
        as orphans (no caller). They implement a DISCARD-on-timeout policy that
        conflicts with this method's FLUSH/SPEAK-FALLBACK directive. Reconciling
        the two design schools is a follow-up cleanup task for the user.
        """
        cfg = getattr(self._config, "voice", None)
        if cfg is None or not getattr(cfg, "completion_detection_enabled", True):
            return text  # feature disabled — passthrough

        buffer = getattr(self, "_completion_buffer", None)
        if buffer is None:
            # Defensive: __new__-built test stubs may skip ctor. Treat as off.
            return text

        max_chain = int(getattr(cfg, "completion_max_chain", 3))

        # --- Continuation path ---------------------------------------------
        if buffer.is_pending:
            if is_cancel(text):
                log.info("🛑 Completion buffer cancelled by user (%r)", text[:60])
                buffer.clear()
                self._cancel_completion_timeout()
                self._buffer_is_complete = False
                return None
            buffer.extend(text)
            verdict = is_incomplete(buffer.fragment, language=lang)
            self._cancel_completion_timeout()
            # Force-dispatch when the chain budget is exhausted, regardless of verdict.
            if buffer.chain_count >= max_chain:
                flushed = buffer.flush()
                self._buffer_is_complete = False
                log.info(
                    "✅ Completion chain-cap reached (chain=%d) → dispatch %r",
                    max_chain,
                    (flushed or "")[:80],
                )
                return flushed
            if verdict is None:
                # Combined now COMPLETE → dispatch the joined text IMMEDIATELY.
                # No grace-hold: holding a completed prompt with the mic open is
                # the "Jarvis keeps listening and never answers" regression (see
                # the fresh-COMPLETE note below).
                flushed = buffer.flush()
                self._buffer_is_complete = False
                log.info(
                    "✅ Combined COMPLETE (chain=%d) → dispatch %r",
                    buffer.chain_count,
                    (flushed or "")[:80],
                )
                return flushed
            # Still dangling — long wait for the next continuation.
            self._buffer_is_complete = False
            log.info(
                "⏳ Completion continuation still incomplete (chain=%d) — keep waiting.",
                buffer.chain_count,
            )
            self._schedule_completion_timeout(lang, is_complete=False)
            return None

        # --- Fresh turn path -----------------------------------------------
        verdict = is_incomplete(text, language=lang)
        if verdict is None:
            if _should_hold_complete_delegation_for_grace(text):
                complete_grace_ms = (
                    int(getattr(cfg, "complete_grace_ms", 1500)) if cfg else 1500
                )
                if complete_grace_ms > 0:
                    log.info(
                        "Delegation grace: complete-looking delegation buffered "
                        "for %d ms: %r",
                        complete_grace_ms,
                        text[:80],
                    )
                    buffer.start(text, language=lang)
                    self._buffer_is_complete = True
                    self._schedule_completion_timeout(lang, is_complete=True)
                    return None
            # Precision-over-recall + latency doctrine (CLAUDE.md intent→ACK
            # budget): a COMPLETE utterance goes STRAIGHT to the brain — no
            # buffering, no grace-hold, no added latency. completion.py's own
            # contract is "a complete prompt must NEVER be held back".
            #
            # The 2026-05-26 "grace-on-COMPLETE" experiment parked every complete
            # command in WAITING_FOR_COMPLETION for complete_grace_ms; while that
            # mic stayed open, room noise / TTS-tail extended the buffer into an
            # INCOMPLETE tail, which the timeout then SILENTLY DISCARDED — the
            # user-reported "Jarvis keeps listening and never answers" regression
            # (same class as BUG Voice-Turn-2026-05-02, guarded by
            # test_complete_text_returns_unchanged). Dispatch now; merge only
            # genuinely dangling fragments via the INCOMPLETE path below.
            return text
        log.info(
            "⏳ Pending completion — incomplete utterance buffered "
            "(reason=%s marker=%r)",
            verdict.reason,
            verdict.marker,
        )
        buffer.start(text, language=lang)
        self._buffer_is_complete = False
        self._schedule_completion_timeout(lang, is_complete=False)
        return None

    # --- Completion timeout helpers (zero-silent-drop fallback) ------------ #

    def _schedule_completion_timeout(self, lang: str, *, is_complete: bool = False) -> None:
        """Arm or re-arm the per-gap timer for the pending completion fragment.

        ``is_complete=True`` uses the short conversational grace window
        (``complete_grace_ms``, default 1500 ms) and dispatches to the brain
        on fire. ``is_complete=False`` uses the long discard window
        (``completion_wait_ms``, default 15 s) and silently drops on fire.
        """
        self._cancel_completion_timeout()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        cfg = getattr(self._config, "voice", None)
        if is_complete:
            wait_ms = int(getattr(cfg, "complete_grace_ms", 1500)) if cfg else 1500
        else:
            wait_ms = int(getattr(cfg, "completion_wait_ms", 15000)) if cfg else 15000
        delay_s = max(0.05, wait_ms / 1000.0)
        self._completion_timeout_task = loop.create_task(
            self._completion_timeout_fire(delay_s, lang),
            name="completion-timeout",
        )

    def _cancel_completion_timeout(self) -> None:
        task = getattr(self, "_completion_timeout_task", None)
        if task is not None and not task.done():
            task.cancel()
        self._completion_timeout_task = None

    async def _completion_timeout_fire(self, delay_s: float, lang: str) -> None:
        """Per-gap timeout. Behaviour depends on the buffer verdict:

        * INCOMPLETE (``_buffer_is_complete == False``): silently discard.
          User-mandated 2026-05-26 — a spoken cue mid-pause was experienced
          as Jarvis interrupting the user. The bubble + open mic already
          carry the "still listening" signal; a never-continued fragment is
          just dropped.
        * COMPLETE (``_buffer_is_complete == True``): dispatch to the brain
          via ``_handle_flushed_pending_text``. This is the conversational
          grace path — the user has had their short pause window and did
          not add anything, so the original COMPLETE text goes to the brain.
        """
        try:
            await asyncio.sleep(delay_s)
        except asyncio.CancelledError:
            return
        buffer = getattr(self, "_completion_buffer", None)
        if buffer is None:
            return
        fragment = buffer.flush()
        self._completion_timeout_task = None
        if not fragment:
            return
        was_complete = bool(getattr(self, "_buffer_is_complete", False))
        # Reset for next turn — BEFORE the dispatch call so a synchronous
        # re-entry sees the fresh-turn state.
        self._buffer_is_complete = False
        if was_complete:
            log.info(
                "⏳→📤 Complete-grace expired (%.1fs) — dispatching %r",
                delay_s,
                fragment[:80],
            )
            try:
                await self._handle_flushed_pending_text(fragment, lang)
            except Exception as exc:  # noqa: BLE001
                log.exception("Complete-grace dispatch failed: %s", exc)
            return
        log.info(
            "⏳→🤫 Incomplete timeout (%.1fs) — silently discarding stale fragment %r",
            delay_s,
            fragment[:80],
        )
        # No TTS, no state ping-pong, no interruption.

    # --- Clarifying-question timer (Zwischenfrage; AD-OE6 for the hold) ----- #

    def _arm_clarify_question(self, lang: str, *, force: bool = False) -> bool:
        """Arm the clarifying-question timer for a buffered incomplete fragment.

        The ``ContinuationBuffer`` holds an open-ended fragment with NO active
        timeout (it only drops the stale buffer lazily on the next
        ``process()`` call), so a user who trails off and never continues is
        otherwise left in silence forever — the "Jarvis hört für immer zu"
        report (2026-06-08). On fire, ``_clarify_question_fire`` speaks a short
        clarifying question instead of discarding silently (AD-OE6
        zero-silent-drop; supersedes the 2026-05-26 silent-discard mandate).
        Cancelled the moment the next utterance arrives, so a thinking-pause-
        then-continue is never interrupted. Gated by
        ``[voice].clarify_incomplete_enabled`` (set false → old silent
        behaviour).

        ``force=True`` bypasses that gate for the ONE case the maintainer
        explicitly opted into (2026-06-14): a TRAILED-OFF sentence
        (``REASON_TRAILING_ELLIPSIS``). All other incomplete reasons keep the
        silent-hold default, so the 2026-06-09 "don't interrogate me" mandate is
        preserved everywhere except a genuine trail-off.

        Returns ``True`` iff a clarifying-question timer was actually armed.
        ``_handle_utterance`` reads this to decide whether the held fragment
        still needs an autonomous drain timer (the silent-hold path returns
        ``False`` → it does), so a held fragment that gets no question is never
        left to hang until the idle-timeout (AD-OE6; "Jarvis hört für immer zu").
        """
        self._cancel_clarify_question()
        cfg = getattr(self._config, "voice", None)
        # Default False: if the field is absent (committed HEAD never carried it,
        # or a config-reload edge), the SAFE behaviour is "do NOT interrogate the
        # user" — the clarify question is opt-in only (maintainer mandate
        # 2026-06-09). A True default here was the live footgun: a config without
        # the field armed the question on every trailed-off / empty turn.
        if not force and (
            cfg is None
            or not getattr(cfg, "clarify_incomplete_enabled", False)
        ):
            # No voice config OR feature off → safe default: stay silent (do NOT
            # interrogate the user). Only an explicit ``force`` (trail-off) or an
            # explicitly-enabled flag arms the question.
            return False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        wait_ms = int(getattr(cfg, "clarify_after_ms", 2500)) if cfg else 2500
        delay_s = max(0.05, wait_ms / 1000.0)
        # Remember the force flag so a deferred re-arm (floor guard in
        # ``_clarify_question_fire``) preserves the trail-off opt-in even when the
        # global clarify flag is off — otherwise the re-arm would silently drop
        # the question for exactly the REASON_TRAILING_ELLIPSIS case it exists for.
        self._clarify_force = force
        self._clarify_timer_task = loop.create_task(
            self._clarify_question_fire(delay_s, lang),
            name="clarify-question",
        )
        return True

    def _cancel_clarify_question(self) -> None:
        task = getattr(self, "_clarify_timer_task", None)
        if task is not None and not task.done():
            task.cancel()
        self._clarify_timer_task = None

    async def _clarify_question_fire(self, delay_s: float, lang: str) -> None:
        """Per-gap timer: the user trailed off on an incomplete fragment and did
        not continue within the grace window. Ask a short clarifying question
        instead of dropping into silence (AD-OE6). Failures are swallowed — a
        fallback must never crash the turn.
        """
        try:
            await asyncio.sleep(delay_s)
        except asyncio.CancelledError:
            return
        self._clarify_timer_task = None
        buf = getattr(self, "_continuation_buffer", None)
        if buf is None or not buf.has_pending():
            # Continuation already arrived / buffer drained — nothing to ask.
            return
        # AD-OE5 floor guard (live incident 2026-06-17 14:47, session f6403ec0):
        # the user trailed off on "...liegt sie im..." → the fragment was held
        # (reason=trailing_ellipsis) and the clarify timer force-armed; 4 ms later
        # the user RESUMED speaking the continuation. The fixed grace then fired
        # 2.5 s INTO that continuation, spoke over the user, and discarded the held
        # first half (so the continuation reached the brain alone → confused
        # non-answer). The ``_cancel_clarify_question`` path only runs once the
        # NEXT utterance FINALISES — too late for a continuation that takes longer
        # than the grace to speak. While the user holds the floor (USER_SPEAKING /
        # WAITING_FOR_FINAL_TRANSCRIPT / WAITING_FOR_COMPLETION), DEFER: keep the
        # held fragment so the continuation coalesces on finalise, and re-arm so a
        # genuine trail-off-into-silence is still asked once the floor clears
        # (the "Jarvis listens forever" / AD-OE6 zero-silent-drop contract must
        # not regress). Mirrors the Flash-Brain ack / announcement floor guard
        # (``_USER_HOLDS_FLOOR_STATES``); never barge mid-utterance.
        if getattr(self, "_turn_state", TurnTakingState.IDLE) in _USER_HOLDS_FLOOR_STATES:
            log.info(
                "Clarify question deferred — user holds the floor (state=%s); "
                "re-arming so the continuation can coalesce.",
                self._turn_state.name,
            )
            self._arm_clarify_question(lang, force=getattr(self, "_clarify_force", True))
            return
        # Clear the stale fragment so it cannot pollute the next turn, THEN ask.
        buf.discard()
        picker_lang = _phrase_lang(lang)
        phrase = _CLARIFY_QUESTION_PHRASE[picker_lang]
        log.info("⏳→❓ Incomplete trailed off (%.1fs) — asking clarifying question.", delay_s)
        try:
            await self._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
            await self._speak(phrase, language=picker_lang, kind=SPOKEN_KIND_CLARIFY)
        except Exception as exc:  # noqa: BLE001 — fallback must never crash the turn
            log.warning("Clarify-question speak failed: %s", exc)
        finally:
            # Always hand the floor back, even if the speak above raised — a
            # stuck JARVIS_SPEAKING would leave the orb "speaking" while the user
            # talks (the _state_loop finally only corrects this at session end).
            try:
                await self._set_turn_state(TurnTakingState.LISTENING)
            except Exception:  # noqa: BLE001
                log.debug("Clarify-question state reset failed", exc_info=True)

    # --- Continuation drain timer (autonomous flush; AD-OE6 zero-silent-drop) - #

    def _arm_continuation_drain(self, lang: str) -> None:
        """Arm an autonomous drain timer for a silently-held continuation fragment.

        The ``ContinuationBuffer`` holds an open-ended fragment with NO timer of
        its own — it only drops a stale buffer lazily on the next ``process()``
        call. When the held fragment is NOT a trail-off (so no clarifying
        question is armed) AND no further utterance ever arrives, the fragment
        would hang in LISTENING until the session idle-timeout silently discards
        it, with the brain never called. Live wedge 2026-06-19 (session
        da25113a): the complete tag question "…morgen ist ja Montag, oder?" was
        classified as a trailing conjunction, held, and dropped ~30 s later —
        Jarvis "listened forever" and never answered.

        On fire, :meth:`_continuation_drain_fire` DISPATCHES the held fragment to
        the brain (not a clarifying question, not a silent drop) so the user
        always gets an answer attempt (AD-OE6). Cancelled the moment the next
        utterance arrives (``_handle_utterance``); deferred while the user holds
        the floor so it never pre-empts a continuation in progress. Fail-open: a
        missing event loop (sync teardown / tests) is a no-op. The drain delay
        matches the buffer's own discard deadline (``timeout_s``).
        """
        self._cancel_continuation_drain()
        buf = getattr(self, "_continuation_buffer", None)
        if buf is None or not buf.has_pending():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        # buf is non-None here (checked above) and ``timeout_s`` is a guaranteed
        # property — match the drain delay to the buffer's own discard deadline.
        delay_s = max(0.05, float(buf.timeout_s))
        self._continuation_drain_task = loop.create_task(
            self._continuation_drain_fire(delay_s, lang),
            name="continuation-drain",
        )

    def _cancel_continuation_drain(self) -> None:
        task = getattr(self, "_continuation_drain_task", None)
        if task is not None and not task.done():
            task.cancel()
        self._continuation_drain_task = None

    async def _continuation_drain_fire(self, delay_s: float, lang: str) -> None:
        """Per-hold timer: a fragment was held for a continuation that never came.

        After the grace window, dispatch the held fragment to the brain instead
        of leaving it to rot until the idle-timeout silently discards it
        (AD-OE6). Floor guard: while the user is ACTIVELY speaking the
        continuation (``_DRAIN_HOLDS_FLOOR`` — USER_SPEAKING /
        WAITING_FOR_FINAL_TRANSCRIPT) DEFER and re-arm, so the drain never
        pre-empts a continuation in progress. It deliberately does NOT defer on
        WAITING_FOR_COMPLETION (the held-and-idle state the drain exists to
        resolve), so it can never be starved into the very silent-hang it fixes.
        Failures are swallowed — a fallback must never crash the turn.
        """
        try:
            await asyncio.sleep(delay_s)
        except asyncio.CancelledError:
            return
        self._continuation_drain_task = None
        buf = getattr(self, "_continuation_buffer", None)
        if buf is None or not buf.has_pending():
            # Continuation already arrived / buffer drained — nothing to flush.
            return
        if getattr(self, "_turn_state", TurnTakingState.IDLE) in _DRAIN_HOLDS_FLOOR:
            log.info(
                "Continuation drain deferred — user is speaking the continuation "
                "(state=%s); re-arming so it can coalesce.",
                self._turn_state.name,
            )
            self._arm_continuation_drain(lang)
            return
        fragment = buf.flush_pending()
        if not fragment:
            return
        log.info(
            "⏳→📤 Continuation grace expired (%.1fs) without a follow-up — "
            "dispatching held fragment to the brain: %r",
            delay_s,
            fragment[:80],
        )
        try:
            await self._handle_flushed_pending_text(fragment, lang)
        except Exception as exc:  # noqa: BLE001 — fallback must never crash the turn
            log.exception("Continuation drain dispatch failed: %s", exc)

    async def _complete_or_buffer_context_legacy_orphan(
        self, text: str, lang: str = "de"
    ) -> str | None:
        """Legacy parallel-session entry point (DISCARD-on-timeout).

        Kept ONLY so the original implementation is reachable in tests/diagnostics
        without ripping it out of git history. Not called from production.
        """
        pending = getattr(self, "_pending_user_context", None)
        if pending is None:
            pending = []
            self._pending_user_context = pending

        # Read config defensively — the [speech.completeness] block may or
        # may not be present (parallel config agent owns that model).
        _completeness_cfg = getattr(
            getattr(getattr(self._config, "speech", None), "completeness", None),
            "__class__",  # just a probe — we read attrs individually below
            None,
        )
        _cfg_root = getattr(self._config, "speech", None)
        _ccfg = getattr(_cfg_root, "completeness", None)
        max_frags: int = int(getattr(_ccfg, "max_pending_fragments", 2))

        # Any new fragment cancels the existing discard timer — we restart it
        # (or don't, for COMPLETE) after classification.
        self._cancel_pending_flush()

        # --- Classify ---
        try:
            verdict = classify_completeness(
                text,
                lang=lang,
                endpoint_reason=getattr(self, "_last_endpoint_reason", None),
            )
        except Exception as exc:  # noqa: BLE001 — fail-open: AD-OE6
            log.warning(
                "classify_completeness raised unexpectedly (%s) — fail-open, passing text through",
                exc,
            )
            # Treat as COMPLETE: "when in doubt, execute"
            pending.clear()
            return " ".join([*pending, text]).strip() if pending else text

        # --- ABRUPT_ABORT ---
        if verdict.label is Completeness.ABRUPT_ABORT:
            log.info(
                "Completeness: ABRUPT_ABORT (reason=%s) for %r — clearing buffer.",
                verdict.reason,
                text[:60],
            )
            pending.clear()
            await self._emit_completeness_signal("abort", lang)
            return None

        # --- INCOMPLETE ---
        if verdict.label is Completeness.INCOMPLETE:
            log.info(
                "Completeness: INCOMPLETE (reason=%s) for %r — buffering.",
                verdict.reason,
                text[:60],
            )
            pending.append(text)
            # Enforce the fragment cap: drop the oldest entry if over limit.
            while len(pending) > max_frags:
                dropped = pending.pop(0)
                log.info("Pending-buffer cap (%d) exceeded — dropped oldest: %r", max_frags, dropped)
            combined_for_ui = " ".join(pending).strip()
            # Publish incomplete transcript for UI (reuses existing wire format —
            # no new enum, spec §8 / BUG-008 avoidance).
            if self._bus is not None:
                try:
                    asyncio.create_task(
                        self._bus.publish(
                            TranscriptionUpdate(
                                source_layer="speech.turn_taking",
                                text=combined_for_ui,
                                is_final=False,
                            )
                        ),
                        name="publish-incomplete-transcript",
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("Incomplete-context publish failed: %s", exc)
            await self._emit_completeness_signal("incomplete", lang)
            # Arm the DISCARD-ONLY timer (never flushes to brain).
            self._schedule_pending_flush()
            return None

        # --- COMPLETE ---
        # Merge any buffered pending fragments first.
        if pending:
            candidate = " ".join([*pending, text]).strip()
            log.info(
                "Completeness: COMPLETE (reason=%s), merging %d pending fragment(s) → %r",
                verdict.reason,
                len(pending),
                candidate[:80],
            )
            # Re-classify the combined candidate to guard against false merges.
            try:
                merged_verdict = classify_completeness(
                    candidate,
                    lang=lang,
                    endpoint_reason=None,  # C-signal only applies to the raw fragment
                )
            except Exception:  # noqa: BLE001
                merged_verdict = verdict  # fail-open: treat as COMPLETE

            if merged_verdict.label is Completeness.INCOMPLETE:
                # Combined is still incomplete — buffer the new text and wait.
                pending.append(text)
                while len(pending) > max_frags:
                    pending.pop(0)
                await self._emit_completeness_signal("incomplete", lang)
                self._schedule_pending_flush()
                return None

            # COMPLETE (or ABRUPT_ABORT — treat as "execute the buffered intent")
            pending.clear()
            return candidate

        # No pending buffer, fresh COMPLETE utterance.
        log.info(
            "Completeness: COMPLETE (reason=%s) for %r",
            verdict.reason,
            text[:60],
        )
        return text

    def _schedule_pending_flush(self) -> None:
        """Arm an auto-flush timer for the pending-context buffer.

        Idempotent: an existing timer is cancelled first so the deadline is
        always counted from the *latest* fragment.
        """
        self._cancel_pending_flush()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._pending_flush_task = loop.create_task(
            self._pending_flush_after_delay(),
            name="pending-context-flush",
        )

    def _cancel_pending_flush(self) -> None:
        task = getattr(self, "_pending_flush_task", None)
        if task is not None and not task.done():
            task.cancel()
        self._pending_flush_task = None

    async def _pending_flush_after_delay(self) -> None:
        """Discard-only timer for the pending-context buffer.

        IMPORTANT: this timer now ONLY discards the buffer — it never calls the
        brain. The old "auto-flush to brain after pending_context_flush_s" was
        the core bug (spec §2 / docs/superpowers/specs/2026-05-25-utterance-
        completeness-design.md): a half-command was executed after a timeout.

        A buffered fragment can only reach the brain through a subsequent COMPLETE
        utterance that merges it in ``_complete_or_buffer_context``. This timer
        is purely a safety drain so an abandoned fragment does not occupy the
        buffer forever.
        """
        try:
            await asyncio.sleep(self._pending_context_flush_s)
        except asyncio.CancelledError:
            return
        pending = getattr(self, "_pending_user_context", None)
        if not pending:
            return
        discarded = " ".join(pending).strip()
        pending.clear()
        if discarded:
            log.info(
                "Pending-context discard after %.1fs: %r (NOT sent to brain)",
                self._pending_context_flush_s,
                discarded[:80],
            )

    async def _handle_flushed_pending_text(self, text: str, lang: str = "de") -> None:
        """Dispatch a buffered COMPLETE text to the brain.

        Called by ``_completion_timeout_fire`` when the conversational grace
        window expires without a continuation. Mirrors the minimal post-buffer
        dispatch path in ``_handle_utterance``: state → PROCESSING, brain
        stream (or non-stream), TTS, state → LISTENING. Deliberately a thin
        slice — the heavy machinery in ``_handle_utterance`` (latency tracker,
        ack-brain, hangup detection, history) does NOT participate here; the
        fragment is dispatched as a stand-alone secondary turn.

        Spec invariant preserved: only COMPLETE fragments (verdict ``None``
        from ``is_incomplete``) reach the brain via this path — the
        ``_buffer_is_complete`` gate in ``_completion_timeout_fire`` enforces
        it. INCOMPLETE fragments still discard silently per spec §2.
        """
        if not text:
            return
        try:
            await self._set_turn_state(TurnTakingState.PROCESSING)
            log.info("→ Brain (from completion buffer)…")
            if self._streaming_enabled():
                # Same stall guard as the primary dispatch path — a buffered
                # completion must not be able to hang the session either. A true
                # stall here surfaces as TimeoutError, caught + logged below
                # (this secondary path stays silent on stall by design).
                await self._run_brain_with_stall_guard(
                    self._brain_streaming(text, lang)
                )
            else:
                reply = await self._brain.generate(text)
                if reply:
                    await self._speak(reply, language=lang, kind=SPOKEN_KIND_COMPLETION)
        except Exception as exc:  # noqa: BLE001 — AD-OE6: never crash the turn
            log.exception("Buffered-completion dispatch failed: %s", exc)
        finally:
            try:
                await self._set_turn_state(TurnTakingState.LISTENING)
            except Exception:  # noqa: BLE001
                pass

    async def _emit_completeness_signal(
        self,
        kind: str,
        lang: str,
    ) -> None:
        """Emit a short user-facing signal when an utterance is INCOMPLETE or ABRUPT_ABORT.

        Signal modality selection ("auto" mode):
        - ``earcon``:  non-blocking chime via ``self._player.play_pcm(CHIME_PCM, ...)``.
          Used when the assistant has NOT yet spoken in this session (fresh / first
          fragment) — low latency, non-verbal, non-interruptive.
        - ``spoken``:  very short phrase via ``self._speak(...)`` (through
          ``scrub_for_voice``). Used mid-conversation when the assistant has already
          spoken at least once — a spoken cue feels natural in a running dialogue.

        ``signal_mode`` in ``[speech.completeness]`` overrides the auto selection:
        ``"earcon"`` always earcon, ``"spoken"`` always spoken.

        Failure in this helper must NEVER crash the turn (AD-OE6): the whole
        method is wrapped in try/except so any signal bug stays silent.
        """
        try:
            # --- Read config (defensive; block may not be present yet) ---
            _cfg_root = getattr(self._config, "speech", None)
            _ccfg = getattr(_cfg_root, "completeness", None)
            signal_mode: str = str(getattr(_ccfg, "signal_mode", "auto"))

            # --- Decide modality ---
            session_spoken = getattr(self, "_session_has_assistant_spoken", False)
            if signal_mode == "earcon":
                use_earcon = True
            elif signal_mode == "spoken":
                use_earcon = False
            else:
                # "auto": earcon on fresh turn, spoken mid-conversation
                use_earcon = not session_spoken

            log.debug(
                "Completeness signal: kind=%s lang=%s mode=%s earcon=%s",
                kind, lang, signal_mode, use_earcon,
            )

            if use_earcon and self._earcons_enabled():
                # Non-blocking earcon: reuses the same CHIME_PCM that the wake
                # acknowledgment uses (imported at the top of this module).
                # Gated by the global "Sound effects" switch (checked before the
                # task is scheduled, so a muted run spawns no no-op coroutine).
                # play_pcm is async but we fire-and-forget to avoid adding latency
                # to the LISTENING re-entry.  Failure is swallowed below.
                try:
                    player = getattr(self, "_player", None)
                    if player is not None:
                        asyncio.create_task(
                            player.play_pcm(CHIME_PCM, sample_rate=CHIME_SAMPLE_RATE),
                            name="completeness-earcon",
                        )
                except Exception as earcon_exc:  # noqa: BLE001
                    log.debug("Completeness earcon failed: %s", earcon_exc)
            else:
                # Spoken cue — short, bilingual, TTS-clean.
                # "incomplete" → "Mhm?" / "abort" → "Okay."
                # These are *runtime* bilingual output strings (not artifacts),
                # so they stay bilingual per the voice-output policy.
                spoken_phrases: dict[str, dict[str, str]] = {
                    "incomplete": {"de": "Mhm?", "en": "Mhm?"},
                    "abort": {"de": "Okay.", "en": "Okay."},
                }
                lang_key = _phrase_lang(lang)
                phrase = spoken_phrases.get(kind, {}).get(lang_key, "Mhm?")
                try:
                    await self._speak(phrase, language=lang_key, kind=SPOKEN_KIND_BACKCHANNEL)
                except Exception as speak_exc:  # noqa: BLE001
                    log.debug("Completeness spoken cue failed: %s", speak_exc)
        except Exception as exc:  # noqa: BLE001 — AD-OE6: signal failure must never crash the turn
            log.warning("_emit_completeness_signal(%s) failed: %s", kind, exc)

    def _streaming_enabled(self) -> bool:
        """Master-Switch fuer den Latenz-Sprint-1 Streaming-TTS-Pfad.

        True nur wenn (a) ``[performance].streaming_tts = true`` in jarvis.toml
        UND (b) der injizierte Brain-Callback eine ``generate_stream``-Methode
        hat (= BrainManager). Mock-Brains und Echo-Fallbacks fallen automatisch
        auf den alten seriellen Pfad zurueck — kein Crash, kein Special-Case.
        """
        cfg = self._config
        if cfg is None:
            return False
        perf = getattr(cfg, "performance", None)
        if perf is None or not getattr(perf, "streaming_tts", False):
            return False
        return hasattr(self._brain, "generate_stream")

    async def _brain_streaming(self, text: str, lang: str) -> tuple[str, bool]:
        """Latenz-Sprint-1 + Look-Ahead-Pipelining: Streaming-Brain mit
        ueberlappender Sentence-TTS.

        Konsumiert ``brain.generate_stream(text)``, splittet satzweise und
        spielt jeden Satz aus — aber **entkoppelt Synthese von Playback**:
        ein Producer startet pro Satzgrenze sofort eine Synthese-Task, die
        ihre AudioChunks in einen eigenen Kanal streamt; ein einziger
        turn-weiter Consumer drainiert die Kanaele FIFO (= Satzreihenfolge,
        eine durchgaengige Stimme) in **genau einen** ``player.play_chunks``-
        Call pro Turn. Dadurch synthetisiert Satz N+1, *waehrend* Satz N noch
        abgespielt wird — die ~2 s Synthese-Wand jedes Satzes (Gemini/Grok/
        Cartesia) verschwindet hinter dem Playback des Vorgaengers, statt sich
        seriell aufzusummieren (Root-Cause TTS-Latenz-Deep-Dive 2026-05-28).
        Provider-agnostisch: lebt vollstaendig oberhalb der Plugin-Schicht.

        Stimmen-Konstanz bleibt erhalten: die Generierungs-*Einheit* aendert
        sich nicht (genau ein ``synthesize()`` pro Satz wie zuvor) — nur die
        Totzeit zwischen den Saetzen faellt weg. ``chunk_by_sentence=false`` /
        ``seed`` / ``temperature`` im Provider sind unberuehrt.

        Look-Ahead ist via ``[performance].tts_lookahead_sentences`` (Default
        1) gebounded — caps spekulative Synthese-Kosten auf dem 1-vCPU-VPS
        und begrenzt verschwendete Synthese bei Barge-Over auf einen Satz.

        Filter-Pflichten pro Satz: ``scrub_for_voice`` (Regex, AP-11) laeuft
        auf der Producer-Seite vor der Synthese. ``_strip_paraphrase_prefix``
        wird einmal vor dem ersten Satz appliziert.
        """
        full_text_parts: list[str] = []
        sentence_buffer = ""
        spoken_anything = False
        # Turn-level mirror of ``spoken_anything`` that survives this coroutine
        # being cancelled by the stall guard — the caller's ``except
        # TimeoutError`` reads it to decide whether a canned fallback phrase
        # would overlap the real answer. Reset so every streaming turn is clean.
        self._spoke_this_turn = False
        paraphrase_stripped = False
        brain_first_token_marked = False
        barged = False
        # Handoff flag for the thinking-phase interrupt monitor in
        # _run_brain_with_stall_guard: once playback starts, that monitor stands
        # down and the per-playback barge monitor (created below) takes over, so
        # only one extra mic runs at a time.
        self._brain_first_frame_played = False
        # Wave 0 (omni-latency): turn-local tracker handle + first-sentence
        # gates so the TTS phases are marked exactly once per turn (the
        # tracker keeps the earliest offset anyway, but the gates avoid one
        # LatencySpan bus event per sentence).
        tracker = self._latency_tracker
        tts_request_marked = False
        tts_first_chunk_marked = False

        lang_code: str | None = None
        if lang:
            lang_code = self._bcp47(lang)

        # Bounded look-ahead: at most ``lookahead`` synthesized-but-not-yet-
        # consumed sentences in flight. maxsize on the channel-of-channels
        # makes the producer block (back-pressure) once the cap is reached.
        # A test/runtime override on the instance wins; otherwise read
        # [performance].tts_lookahead_sentences; otherwise default to 1.
        lookahead = getattr(self, "_tts_lookahead_sentences", None)
        if lookahead is None:
            perf = getattr(self._config, "performance", None) if self._config else None
            lookahead = getattr(perf, "tts_lookahead_sentences", 1) if perf else 1
        lookahead = max(1, int(lookahead))
        sentence_channels: asyncio.Queue = asyncio.Queue(maxsize=lookahead)
        synth_tasks: list[asyncio.Task] = []

        async def _synth_into(channel: asyncio.Queue, sentence: str) -> None:
            """Synthesize one sentence, stream its chunks into ``channel``.

            Runs as an independent task so synthesis of sentence N+1 overlaps
            playback of N. A trailing ``None`` marks end-of-sentence. Honours
            the streaming providers (ElevenLabs) by forwarding chunks as they
            arrive instead of buffering the whole sentence first.
            """
            nonlocal tts_request_marked, tts_first_chunk_marked
            try:
                if tracker is not None and not tts_request_marked:
                    tts_request_marked = True
                    tracker.mark(LatencyPhase.TTS_REQUEST_SENT)
                try:
                    gen = self._tts.synthesize(sentence, language_code=lang_code)
                except TypeError:
                    gen = self._tts.synthesize(sentence)
                async for chunk in gen:
                    if tracker is not None and not tts_first_chunk_marked:
                        tts_first_chunk_marked = True
                        tracker.mark(LatencyPhase.TTS_FIRST_CHUNK)
                    await channel.put(chunk)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("TTS-Synthese fuer Satz fehlgeschlagen: %s", exc)
            finally:
                await channel.put(None)

        async def _merged_chunks() -> AsyncIterator[AudioChunk]:
            """Drain per-sentence channels FIFO into one continuous stream.

            FIFO over ``sentence_channels`` guarantees audio plays in sentence
            order regardless of which synthesis task finishes first — the key
            single-voice-continuity invariant (never as_completed)."""
            while True:
                channel = await sentence_channels.get()
                try:
                    if channel is None:
                        return  # end-of-turn sentinel
                    while True:
                        chunk = await channel.get()
                        if chunk is None:
                            break
                        # First real audio chunk reaches the player: the thinking
                        # phase is over, so the thinking-interrupt monitor in
                        # _run_brain_with_stall_guard stands down and the
                        # per-playback barge monitor takes over interruption.
                        self._brain_first_frame_played = True
                        yield chunk
                finally:
                    sentence_channels.task_done()

        async def _enqueue_sentence(sentence: str) -> None:
            nonlocal spoken_anything
            scrubbed = scrub_for_voice(sentence, language=lang)
            if scrubbed.actions:
                log.info(
                    "🧹 Output-Filter [stream:%s]: %s (fallback=%s)",
                    lang, scrubbed.actions, scrubbed.fallback_used,
                )
            cleaned = scrubbed.cleaned.strip()
            if not cleaned:
                return
            if not spoken_anything:
                await self._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
                spoken_anything = True
                # Real output is now committed to TTS — suppress any later
                # stall-fallback phrase so it can't be stacked on top.
                self._spoke_this_turn = True
            channel: asyncio.Queue = asyncio.Queue()
            synth_tasks.append(
                asyncio.create_task(_synth_into(channel, cleaned), name="tts-synth")
            )
            # Blocks once ``lookahead`` channels are outstanding — back-pressure.
            await sentence_channels.put(channel)

        async def _produce() -> None:
            nonlocal sentence_buffer, paraphrase_stripped, brain_first_token_marked
            try:
                # Pass the stall-guard heartbeat down so the tool-use loop can
                # ping it on each model-round + tool boundary (a vision/tool turn
                # streams little text — see _run_brain_with_stall_guard). Older
                # fakes / providers without the kwarg fall back transparently.
                if tracker is not None:
                    tracker.mark(LatencyPhase.BRAIN_REQUEST_SENT)
                # ``allow_voice_confirm=True``: this is a conversational voice
                # turn, so a consequential ask-tier tool is deferred into a spoken
                # yes/no confirmation instead of blocking on a UI approval no voice
                # user can give (forensic 2026-06-18). Graduated fallback: a brain
                # that accepts ``on_progress`` but NOT ``allow_voice_confirm`` (older
                # builds, test fakes) must still receive the stall-guard heartbeat —
                # dropping straight to the bare call here loses ``on_progress`` and
                # the no-first-frame ceiling would behead the working turn (BUG-032).
                try:
                    stream = self._brain.generate_stream(
                        text,
                        on_progress=self._mark_brain_progress,
                        allow_voice_confirm=True,
                    )
                except TypeError:
                    try:
                        stream = self._brain.generate_stream(
                            text, on_progress=self._mark_brain_progress,
                        )
                    except TypeError:
                        stream = self._brain.generate_stream(text)
                async for chunk in stream:
                    if not chunk:
                        continue
                    # Any streamed text is also progress — reset the deadline.
                    self._mark_brain_progress()
                    # Wave 0 (omni-latency): first real brain token = brain TTFT.
                    if not brain_first_token_marked:
                        brain_first_token_marked = True
                        if self._latency_tracker is not None:
                            self._latency_tracker.mark(LatencyPhase.BRAIN_FIRST_TOKEN)
                        if self._bus is not None:
                            asyncio.create_task(  # noqa: RUF006
                                self._bus.publish(BrainTTFT(source_layer="brain.stream"))
                            )
                    full_text_parts.append(chunk)
                    sentence_buffer += chunk

                    if not paraphrase_stripped:
                        stripped = _strip_paraphrase_prefix(sentence_buffer)
                        if stripped != sentence_buffer:
                            sentence_buffer = stripped
                        paraphrase_stripped = True

                    while True:
                        m = _STREAM_SENTENCE_END.search(sentence_buffer)
                        if m is None:
                            break
                        sentence = sentence_buffer[:m.end()].strip()
                        sentence_buffer = sentence_buffer[m.end():]
                        if sentence:
                            await _enqueue_sentence(sentence)

                # Wave 0 (omni-latency): the stream is exhausted — last token.
                if tracker is not None and brain_first_token_marked:
                    tracker.mark(LatencyPhase.BRAIN_LAST_TOKEN)
                # Final-Flush: trailing text without a closing sentence mark.
                tail = sentence_buffer.strip()
                if tail:
                    await _enqueue_sentence(tail)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # Brain-stream errors must not hang the consumer; the sentinel
                # in the finally guarantees playback of what was produced.
                log.exception("Brain-Stream-Produktion fehlgeschlagen: %s", exc)
            finally:
                # End-of-turn sentinel — awaited so it lands even when the
                # channel queue is at capacity (consumer keeps draining).
                await sentence_channels.put(None)

        produce_task = asyncio.create_task(_produce(), name="tts-produce-turn")
        play_task = asyncio.create_task(
            self._player.play_chunks(_merged_chunks()), name="tts-play-turn"
        )
        barge_task = asyncio.create_task(self._barge_monitor(), name="barge-monitor-turn")
        # Hangup is the hard kill-switch ("auflegen"): when the user hangs up
        # MID-TURN, ``_player.stop()`` alone does not free this wait — a tool-use
        # turn whose brain stream is still open keeps ``_merged_chunks`` waiting
        # for its end-sentinel, so ``play_task`` never completes. Without a
        # hangup waiter here the wait blocks until the (long) ceiling, so
        # ``_handle_utterance`` never returns, ``_active_session`` never reaches
        # its IDLE finally, and the supervisor — and the UI voice-state — wedge
        # on SPEAKING forever (live bug 2026-06-01: "shows SPEAKING the whole
        # time"; the user pressed hangup repeatedly with no effect). Treat it
        # exactly like a barge-in: stop the player and unwind the turn at once.
        # getattr-fallback: test fixtures build the pipeline via ``__new__`` and
        # don't set ``_hangup_event`` — a fresh never-set Event keeps the
        # behaviour identical to pre-fix (the waiter simply never fires).
        hangup_event = getattr(self, "_hangup_event", None) or asyncio.Event()
        hangup_task = asyncio.create_task(
            hangup_event.wait(), name="hangup-during-tts"
        )

        # A stalled output device (blocking ``stream.write``) or a stalled
        # producer can wedge playback. The watchdog aborts a wedged device in
        # ~5 s (vs the old 120 s ceiling) so the turn always unwinds and the
        # voice session can never freeze with ``self._state`` stuck at ACTIVE
        # (the wake loop only re-arms in IDLE).
        try:
            done = await self._await_playback(play_task, {barge_task, hangup_task})
            if not done:
                # Watchdog already aborted the wedged device + logged the reason;
                # fall through so the turn unwinds and the session re-arms.
                pass
            elif hangup_task in done and not hangup_task.cancelled():
                log.info("📵 Hangup during TTS — aborting turn")
                barged = True
                self._player.stop()
            elif (
                barge_task in done
                and not barge_task.cancelled()
                and barge_task.result()
            ):
                log.info("🛑 Barge-in — stoppe TTS-Playback")
                barged = True
                self._player.stop()
            elif play_task in done and not play_task.cancelled():
                # Whole turn played out naturally; surface any playback error.
                exc = play_task.exception()
                if exc is not None:
                    log.exception("Streaming-Playback-Fehler: %s", exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("Streaming-TTS-Turn-Fehler: %s", exc)
        finally:
            # Tear down everything still in flight: on barge cancel the
            # producer + all pending synth tasks (stop paying for look-ahead
            # the user barged over) and the merged consumer; on normal end
            # these are already done. ``player.stop()`` (above) aborts the
            # OutputStream so the cancelled play_task unwinds immediately.
            for t in (produce_task, play_task, barge_task, hangup_task, *synth_tasks):
                if not t.done():
                    t.cancel()
            for t in (produce_task, play_task, barge_task, hangup_task, *synth_tasks):
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            # Drop any buffered-but-unplayed sentence channels so a cancelled
            # turn can never replay ghost audio on the next turn.
            while not sentence_channels.empty():
                try:
                    sentence_channels.get_nowait()
                    sentence_channels.task_done()
                except asyncio.QueueEmpty:
                    break

        # Wave 0 (omni-latency): audio for this turn is fully played (or the
        # turn was barged over) — close the TTS span. Only meaningful when at
        # least one sentence reached TTS; an all-empty turn marks nothing.
        if tracker is not None and spoken_anything:
            tracker.mark(LatencyPhase.TTS_STREAM_DONE)
        if not barged:
            self._suppress_session_input_after_tts("response")
        return "".join(full_text_parts), barged

    def _brain_turn_failed(self) -> bool:
        """True when the brain flagged the just-finished turn as a total
        provider-chain failure.

        Reads ``BrainManager._last_turn_all_failed`` (set for exactly one turn
        when no provider could produce a token: missing key / depleted credits
        / rate-limited everywhere). Degrades to ``False`` for echo/mock brains
        without the flag, and — crucially — stays ``False`` for a legitimate
        ``suppress_response`` empty (fire-and-forget ``spawn_worker``), so the
        spoken fallback never false-fires on a normal spawn turn.
        """
        return bool(
            getattr(getattr(self, "_brain", None), "_last_turn_all_failed", False)
        )

    def _brain_turn_suppressed(self) -> bool:
        """True when the just-finished brain turn was a fire-and-forget
        ``suppress_response`` spawn (background ``spawn_worker`` mission).

        Reads ``BrainManager._last_turn_suppressed`` (set for exactly one turn).
        Such a turn produces empty text ON PURPOSE — its feedback arrives over
        the bus — so the pipeline must stay silent for it and must NOT speak a
        clarifying question. Degrades to ``False`` for echo/mock brains.
        """
        return bool(
            getattr(getattr(self, "_brain", None), "_last_turn_suppressed", False)
        )

    def _brain_turn_executed_action(self) -> bool:
        """True when the just-finished brain turn executed a DESKTOP-ACTION tool
        (computer_use / open_app / click / …) but produced no narration text.

        Reads ``BrainManager._last_turn_executed_action_tool`` (set for exactly
        one turn). Such a turn DID something on screen — the action landed — so
        the pipeline must speak a success confirmation, NOT a clarifying
        question (live bug 2026-06-09: a successful ``computer_use`` run that
        opened Chrome was answered with "Wie meinst du das genau?"). Degrades to
        ``False`` for echo/mock brains that do not expose the flag.
        """
        return bool(
            getattr(
                getattr(self, "_brain", None),
                "_last_turn_executed_action_tool",
                False,
            )
        )

    async def _handle_silent_brain_turn(self, lang: str, text: str = "") -> None:
        """AD-OE6 zero-silent-drop for a brain turn that produced no speech.

        Decides what (if anything) to say when the streamed/!generated response
        is empty, so the user is never dropped into silence after talking — the
        dominant live "Jarvis antwortet nie" cause (logs 2026-06-08), where a
        conversational turn made the router brain emit a ``function_call`` /
        Computer-Use action (or empty content) and the turn ended mute (the TTS
        playback watchdog then mis-read the absent frames as a device wedge).

        Branches, in priority order:
        * **Total provider-chain failure** → speak the dedicated "brain
          unreachable" message (unchanged behaviour).
        * **Fire-and-forget spawn** (``suppress_response``) → stay silent; the
          mission reports back over the bus.
        * **Anything else empty** (function_call without speech, empty content)
          → speak a short clarifying question. This both engages the user
          (their explicit "Zwischenfragen" wish) AND emits TTS frames, which
          un-sticks the playback watchdog's stale ``last_write_ns`` cascade.
          Gated by ``[voice].clarify_incomplete_enabled`` (off → old silence).
        """
        if self._brain_turn_failed():
            await self._speak_brain_unavailable(lang)
            return
        if self._brain_turn_suppressed():
            return  # legit background spawn — its feedback arrives over the bus
        if text and is_cancel(text):
            return  # user explicitly aborted ("vergiss das") — stay quiet
        # A SUCCESSFUL wordless desktop action (computer_use / open_app / …) is
        # NOT an empty/confused turn — the action landed on screen. Confirm it
        # ("Erledigt.") instead of asking a clarifying question (live bug
        # 2026-06-09: computer_use opened Chrome, then "Wie meinst du das
        # genau?" was spoken, so a success looked like incomprehension). This is
        # an AD-OE6 success ack, so it fires independently of the
        # clarify-question toggle below.
        if self._brain_turn_executed_action():
            picker_lang = _phrase_lang(lang)
            log.info(
                "✅ Brain ran a desktop action without narration — speaking "
                "confirmation instead of a clarifying question (AD-OE6)."
            )
            try:
                await self._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
                await self._speak(
                    _ACTION_DONE_PHRASE[picker_lang],
                    language=picker_lang,
                    kind=SPOKEN_KIND_ACTION_DONE,
                )
            except Exception as exc:  # noqa: BLE001 — ack must never crash the turn
                log.warning(
                    "Silent-brain-turn action-confirmation speak failed: %s", exc
                )
            return
        if getattr(self, "_playback_aborted_no_first_frame", False):
            # The no-first-frame TTS ceiling beheaded this turn — a FAILURE,
            # not a "confused" empty turn. Always audible (AD-OE6), independent
            # of the opt-in clarify toggle below: the 2026-06-09 mandate keeps
            # the interrogating question off, but an honest "taking longer"
            # notice is a different speech act (live bug 2026-06-10 14:34 — a
            # 20 s mute brain turn ended in silent LISTENING + idle hang-up).
            log.info(
                "⏱ Empty turn after a no-first-frame ceiling abort — speaking "
                "the timeout notice (AD-OE6)."
            )
            # Speak BEFORE clearing the beheaded mark so the timeout
            # instrumentation in _speak_brain_timeout reads no_first_frame=True
            # for this path — the field that pins the next occurrence to this
            # site must report the truth. The real per-turn stale-bleed guard is
            # the re-arm at turn start (_handle_utterance_turn), not this clear.
            await self._speak_brain_timeout(lang, site="empty_after_no_first_frame")
            self._playback_aborted_no_first_frame = False
            return
        cfg = getattr(self._config, "voice", None)
        # Default False (defense-in-depth): see _arm_clarify_question above. An
        # empty brain turn must never interrogate the user when the field is
        # absent — the clarify question is opt-in only.
        if cfg is not None and not getattr(cfg, "clarify_incomplete_enabled", False):
            return  # feature off → preserve the legacy silent behaviour
        picker_lang = _phrase_lang(lang)
        phrase = _CLARIFY_QUESTION_PHRASE[picker_lang]
        log.info("🤷 Brain produced no speech (not a spawn) — clarifying question (AD-OE6).")
        try:
            await self._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
            await self._speak(phrase, language=picker_lang, kind=SPOKEN_KIND_CLARIFY)
        except Exception as exc:  # noqa: BLE001 — fallback must never crash the turn
            log.warning("Silent-brain-turn clarify speak failed: %s", exc)

    async def _speak_brain_unavailable(self, lang: str) -> None:
        """Zero-silent-drop (AD-OE6): say out loud that the whole brain
        provider chain is down, instead of dropping back to LISTENING mute.

        Uses the curated, TTS-clean ``_BRAIN_UNAVAILABLE_PHRASE`` — the raw
        BrainManager diagnostic (URLs, "Sidebar -> API-Keys", jarvis.toml) is
        UI-only and must never be read aloud. ``_speak`` does not scrub, so the
        phrase is spoken verbatim. Failures here are swallowed: the fallback
        must never itself crash the turn.
        """
        picker_lang = _phrase_lang(lang)
        phrase = _BRAIN_UNAVAILABLE_PHRASE[picker_lang]
        try:
            await self._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
            await self._speak(phrase, language=picker_lang, kind=SPOKEN_KIND_UNAVAILABLE)
        except Exception as exc:  # noqa: BLE001
            log.warning("Brain-unavailable fallback speak failed: %s", exc)

    async def _speak_stt_unavailable(self, lang: str = "de") -> None:
        """Zero-silent-drop (AD-OE6) for STT: say we couldn't transcribe the
        utterance instead of dropping back to LISTENING mute when
        ``_transcribe_final`` exhausted its retries (sustained cloud rate-limit
        / outage). Mirrors ``_speak_brain_unavailable``. No transcript exists
        yet, so there is no detected language — default to German (the user's
        primary; runtime TTS auto-detects anyway). Failures here are swallowed:
        the fallback must never itself crash the turn.
        """
        picker_lang = _phrase_lang(lang)
        phrase = _STT_UNAVAILABLE_PHRASE[picker_lang]
        try:
            await self._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
            await self._speak(phrase, language=picker_lang, kind=SPOKEN_KIND_STT_UNAVAILABLE)
        except Exception as exc:  # noqa: BLE001
            log.warning("STT-unavailable fallback speak failed: %s", exc)

    async def _speak_realtime_unavailable(self) -> None:
        """Explain a duplex failure before continuing on the classic path."""
        lang = _phrase_lang(self._output_language(None, ""))
        phrase = _REALTIME_UNAVAILABLE_PHRASE[lang]
        try:
            await self._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
            await self._speak(
                phrase,
                language=lang,
                kind=SPOKEN_KIND_UNAVAILABLE,
            )
        except Exception as exc:  # noqa: BLE001 — fallback must never block recovery
            log.warning("Realtime-unavailable fallback speak failed: %s", exc)

    def _mark_brain_progress(self) -> None:
        """Record that the in-flight brain turn just made progress.

        Called on every streamed text chunk (``_brain_streaming``) and at every
        tool-use-loop boundary (``on_progress`` threaded down to ``ToolUseLoop``).
        Resets the stall deadline in ``_run_brain_with_stall_guard`` so a slow-
        but-working turn (vision upload + Gemini tool-use loop) is never cut off
        mid-work. Cheap + synchronous, so it is safe to call from the brain
        producer task (same event loop, single attribute write).
        """
        self._brain_last_progress = time.monotonic()

    async def _on_agent_progress(
        self, event: ObservationCaptured | ActionPlanned
    ) -> None:
        """Bus handler: a computer_use loop step happened (it captured a
        screenshot or executed a desktop action). The desktop loop runs as one
        opaque, text-silent tool call, so without this heartbeat the brain
        stall guard would (and did, live 2026-06-07) mistake a working 30 s+
        automation for a wedged provider and speak "Das hat zu lange gedauert".

        Marks brain progress (resets the no-progress stall window) AND records
        long-tool activity (suspends the absolute ceiling while the loop keeps
        stepping — see _run_brain_with_stall_guard). Must be ``async``: the bus
        silently drops a sync handler (live lesson 2026-06-02). The ``event`` is
        only a liveness signal, so its payload is intentionally unused.
        """
        self._mark_brain_progress()
        self._long_tool_last_activity = time.monotonic()

    def _should_speak_stall_fallback(self) -> bool:
        """Whether a stalled streaming turn should speak the canned timeout phrase.

        False once the real answer has already (partially) reached TTS this
        turn — a canned phrase on top would overlap / garble the output the user
        is already hearing (live bug 2026-06-02: real answer + standard phrase
        combined). True otherwise, so a genuinely silent stall is still spoken
        (AD-OE6 zero-silent-drop, live bug 2026-05-29). Defaults to True on a
        bare instance so the safe (spoken) branch wins when the flag is absent.
        """
        return not getattr(self, "_spoke_this_turn", False)

    async def _run_brain_with_stall_guard(
        self, coro: Awaitable[tuple[str, bool]], *, interrupt_monitor: bool = False
    ) -> tuple[str, bool]:
        """Await a streaming brain turn with a *no-progress* (stall) timeout
        instead of a hard total-wall-clock cap.

        Live bug 2026-06-01: a vision question ("What is this?") triggered a
        Gemini tool-use loop (image upload + context cache + function_call + tool
        execution). The whole turn legitimately exceeded the old 25 s TOTAL cap,
        so ``asyncio.wait_for`` cancelled the in-flight turn mid-work and spoke
        "That took too long, say it again" — Jarvis looked lazy while it was
        actually still working. Root cause: a single wall-clock cap cannot tell a
        genuinely STALLED provider (no progress, ever) apart from a slow-but-
        working one (steady tool/token progress).

        Fix: the deadline resets every time the turn makes progress
        (``_mark_brain_progress``). The fallback fires only after
        ``_brain_timeout_s`` of TRUE silence, or at the absolute
        ``_brain_hard_timeout_s`` ceiling (pathological drip-feed backstop).

        Raises ``TimeoutError`` on a true stall or at the hard ceiling — the same
        contract the caller's ``except TimeoutError`` fallback already expects.
        A brain coroutine that raises has its exception propagated unchanged.
        """
        # Defensive getattr: test fixtures build the pipeline via
        # ``SpeechPipeline.__new__`` and don't set every ctor attribute (same
        # pattern as ``_ack_brain``/``_muted`` elsewhere). Fall back to the ctor
        # defaults so the guard is self-sufficient on a bare instance.
        poll_s = getattr(self, "_brain_stall_poll_s", 0.5)
        stall_s = getattr(self, "_brain_timeout_s", 30.0)
        ceiling_s = getattr(self, "_brain_hard_timeout_s", 90.0)
        self._mark_brain_progress()
        # Each turn starts with the ceiling fully armed: only a computer_use
        # step THIS turn (ObservationCaptured/ActionPlanned → _on_agent_progress)
        # may suspend it — never a stale heartbeat bled over from a previous
        # desktop turn that finished moments ago.
        self._long_tool_last_activity = 0.0
        # Reset per turn (no stale bleed): the still-working heartbeat AND the
        # "a speakable token has reached TTS" flag that gates it. The heartbeat
        # runs until the FIRST speakable token (_spoke_this_turn), so a stale
        # True bled over from the previous turn must not suppress it on this
        # turn's first poll (BUG-032 stale-counter class). _brain_streaming
        # also clears it, but the guard's poll loop can run first.
        self._brain_thinking_heartbeat = 0.0
        self._spoke_this_turn = False
        start = time.monotonic()
        task: asyncio.Task[tuple[str, bool]] = asyncio.ensure_future(coro)
        monitor_task: asyncio.Task[bool] | None = None
        if (
            interrupt_monitor
            and getattr(self, "_continuation_interrupt_enabled", False)
            # Mute is an input-only contract: while muted the user has told us to
            # stop listening, so never open the thinking-interrupt monitor's
            # second mic (a mid-turn mute is handled below by standing it down).
            and not getattr(self, "_muted", False)
        ):
            self._brain_first_frame_played = False
            monitor_task = asyncio.create_task(
                self._barge_monitor(
                    grace_s=_CONTINUATION_THINKING_GRACE_S,
                    respect_input_suppression=True,
                ),
                name="thinking-interrupt-monitor",
            )
        # Hangup waiter: the bar's X (request_hangup → _hangup_event) must abort a
        # thinking turn at once — exactly as the TTS phase already does. Without
        # this the wake loop only consults _hangup_event while LISTENING, so a
        # hangup mid-think had no effect until the brain finished on its own
        # (live bug 2026-06-19). getattr-fallback mirrors _brain_streaming: test
        # fixtures build via __new__ and don't set _hangup_event; a fresh, never-
        # set Event keeps the behaviour identical (the waiter simply never fires).
        hangup_event = getattr(self, "_hangup_event", None) or asyncio.Event()
        hangup_task = asyncio.create_task(
            hangup_event.wait(), name="hangup-during-thinking"
        )
        try:
            while True:
                waiters = {task, hangup_task}
                if monitor_task is not None:
                    waiters.add(monitor_task)
                done, _pending = await asyncio.wait(waiters, timeout=poll_s)
                # Hard kill-switch: abort the thinking turn the instant the user
                # hangs up. The brain task is cancelled (bounded) in the finally.
                if hangup_task in done:
                    log.info("📵 Hangup during thinking — aborting brain turn")
                    return ("", True)
                if monitor_task is not None:
                    if getattr(self, "_brain_first_frame_played", False):
                        # Playback started — _brain_streaming's own barge monitor
                        # now owns interruption; stand our thinking monitor down.
                        monitor_task.cancel()
                        try:
                            await monitor_task
                        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                            pass
                        monitor_task = None
                    elif getattr(self, "_muted", False):
                        # Voice muted mid-think (orb double-click → _muted=True):
                        # the user said "stop listening to me". The thinking-
                        # interrupt monitor is an INPUT path, so it must honour mute
                        # exactly like the wake loop (_activation_allowed) and
                        # _speak do. Otherwise it aborts the turn on the muted
                        # second mic while the muted wake loop can NEVER capture the
                        # recombination utterance — silently killing a fully-worked
                        # turn (live bug 2026-07-01 "Was steht alles in meinen
                        # E-Mails drin?": 6 mails fetched, a barge on the muted mic
                        # → empty answer, empty transcript). Stand the monitor down
                        # unconditionally and let the brain finish; its answer still
                        # lands as text. Clearing it here (not just skipping the
                        # abort) also avoids a busy-spin on an already-done monitor.
                        monitor_task.cancel()
                        try:
                            await monitor_task
                        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                            pass
                        monitor_task = None
                    elif (
                        monitor_task in done
                        and not monitor_task.cancelled()
                        and monitor_task.result()
                    ):
                        # User spoke during thinking → abort the half-formed
                        # answer. The brain task is cancelled in the ``finally``
                        # (bounded — see _cancel_brain_task_bounded) so its
                        # truncated half never commits to history AND an inline
                        # action that ignores cancellation can never wedge the
                        # session; the next utterance recombines with this prompt.
                        log.info(
                            "✋ Continuation interrupt — user spoke during thinking, "
                            "aborting brain turn"
                        )
                        return ("", True)
                if task in done:
                    if monitor_task is not None:
                        monitor_task.cancel()
                        try:
                            await monitor_task
                        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                            pass
                    return task.result()
                now = time.monotonic()
                # Active TTS playback is itself a liveness signal. After the
                # brain's LAST token, _brain_streaming stays inside
                # _await_playback reading a long answer aloud — many seconds for
                # a long reply — and NOTHING bumps _brain_last_progress during
                # that tail. Keying the stall purely on brain-token progress
                # therefore guillotined the still-playing tail of a long answer
                # mid-sentence at exactly stall_s after the last token (live bug
                # 2026-06-19 16:27 "Wegzugsteuer": last token ~37 s, playback ran
                # to ~64 s, the abort fired 30 s after the last token). While the
                # player keeps writing audio sub-blocks (AudioPlayer.last_write_ns
                # advances within the stall window) the turn is working, not
                # wedged — so playback suspends BOTH the no-progress stall and the
                # absolute ceiling below, exactly as an active computer_use loop
                # suspends the ceiling. A genuinely wedged device leaves
                # last_write_ns frozen, so the liveness guard still fires (and the
                # dedicated device-wedge watchdog in _await_playback owns the fast
                # path). getattr-fallbacks keep this resilient on __new__-built
                # test fixtures that never set _player.
                player = getattr(self, "_player", None)
                last_write_ns = getattr(player, "last_write_ns", 0) or 0
                playback_active = last_write_ns > 0 and (
                    time.monotonic_ns() - last_write_ns
                ) < (stall_s * 1_000_000_000)
                stalled = (not playback_active) and (
                    now - self._brain_last_progress
                ) >= stall_s
                # "Still-working" heartbeat: until the brain hands its FIRST
                # speakable sentence to TTS (_spoke_this_turn), keep a dedicated
                # heartbeat fresh so the no-first-frame TTS ceiling — which
                # re-arms off it — cannot behead a brain that is still thinking
                # OR still in its tool loop. Two live bugs this must cover:
                #   • 2026-06-14 16:17 (Gemini built an 18k-token cache then
                #     thought ~17 s with no on_progress, since_progress_s=20.19):
                #     PRE-first-token thinking emits no heartbeat of its own.
                #   • 2026-06-30 ("München nach Bora Bora", since_progress_s=20.77):
                #     the brain ran ONE tool round (~10 s in) then worked a second
                #     ~20 s model roundtrip with no further ping and no token. The
                #     old gate (_brain_last_progress <= first_progress_floor) froze
                #     the heartbeat at that first round — so the 20 s ceiling
                #     beheaded the turn 10 s before the 30 s brain stall guard
                #     would have. A tool round is progress, but it is NOT a spoken
                #     token: only _spoke_this_turn means TTS actually has text, so
                #     only then may the no-first-frame ceiling judge a wedged
                #     provider.
                # It STOPS the instant a speakable token reaches TTS (so a wedged
                # TTS after real output is still aborted) and is bounded by the
                # absolute hard cap below (measured from `start`, never reset here)
                # so a truly hung brain still dies. The independent no-progress
                # `stalled` check above is unaffected — a brain that pings nothing
                # for stall_s still times out on schedule.
                if (
                    not getattr(self, "_spoke_this_turn", False)
                    and (now - start) < ceiling_s
                ):
                    self._brain_thinking_heartbeat = now
                # A computer_use loop runs as one opaque tool call that can
                # legitimately need minutes (open app → search → click → verify,
                # one screenshot round-trip per step). It reports each step via
                # ObservationCaptured/ActionPlanned (→ _on_agent_progress), so
                # while those heartbeats keep arriving the absolute ceiling is
                # suspended — a long desktop task is "as long as it needs", not
                # guillotined at 90 s (live bug 2026-06-07). The no-progress
                # stall above is the real liveness guard: once the loop wedges
                # and heartbeats stop for `stall_s`, BOTH re-engage and abort.
                long_tool_active = (
                    now - getattr(self, "_long_tool_last_activity", 0.0)
                ) < stall_s
                ceiling_hit = (
                    (not long_tool_active)
                    and (not playback_active)
                    and (now - start) >= ceiling_s
                )
                if stalled or ceiling_hit:
                    raise TimeoutError
        finally:
            if monitor_task is not None and not monitor_task.done():
                monitor_task.cancel()
                try:
                    await monitor_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                    pass
            if not hangup_task.done():
                hangup_task.cancel()
                try:
                    await hangup_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                    pass
            if not task.done():
                # Bounded cancel: a cancellable brain unwinds at once (its
                # truncated half never commits, and on a TimeoutError the caller
                # still sees the TimeoutError, not cancellation noise); a brain
                # blocked on an uncancellable inline action is ABANDONED after a
                # short grace so the voice session can never freeze (2026-06-19).
                await self._cancel_brain_task_bounded(task)

    async def _cancel_brain_task_bounded(self, task: asyncio.Task) -> None:
        """Cancel an in-flight brain turn and wait only a BOUNDED grace for it to
        unwind, then abandon it.

        A brain stream blocked on an inline action that ignores asyncio
        cancellation — a long ``computer_use`` step stops only via its own
        ``cancel_active_cu`` token, not task cancellation — would otherwise never
        finish. An unbounded ``await task`` then freezes the whole voice session:
        the wake loop stays inside ``_handle_utterance`` and never reaches its
        ``while not _hangup_event.is_set()`` check, so the bar's X
        (``request_hangup``) has nothing to interrupt (live bug 2026-06-19: a
        continuation interrupt during an "open X" turn wedged the pipeline; ~40
        ignored X presses, recovered only by an app restart).

        After the grace the task is left running — it unwinds on its own once the
        underlying action finishes or is stopped via ``cancel_active_cu`` (the
        hangup path already calls that) — so control ALWAYS returns to the loop.
        """
        task.cancel()
        grace = getattr(self, "_brain_cancel_grace_s", _BRAIN_CANCEL_GRACE_S)
        done, _pending = await asyncio.wait({task}, timeout=grace)
        if task not in done:
            # Retrieve the eventual result/exception so a late finish does not
            # log "exception was never retrieved"; the session has moved on.
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
            log.warning(
                "Brain turn ignored cancellation for %.1fs — abandoning it to "
                "free the voice session (an uncancellable inline action is still "
                "running; hang up to stop it).",
                grace,
            )

    async def _speak_brain_timeout(
        self, lang: str, *, site: str = "unspecified"
    ) -> None:
        """Zero-silent-drop (AD-OE6) for a brain turn that timed out: say it took
        too long instead of dropping back to LISTENING mute. Mirrors
        ``_speak_brain_unavailable``. Failures here are swallowed: the fallback
        must never itself crash the turn.

        ``site`` names which of the three triggers reached here
        ("stream_stall" / "nonstream_total_cap" / "empty_after_no_first_frame")
        so the consolidated WARN below attributes the next real occurrence
        instead of leaving the path to guesswork.

        Floor guard (live user report 2026-06-14 — Jarvis apologised for taking
        too long "right after" a sub-second turn): none of the three timeout
        paths can legitimately fire faster than the stall window, so a turn whose
        measured wall-clock is *under* ``_min_timeout_phrase_s`` is being driven
        by stale per-turn state (the no-first-frame mark — AP-19/BUG-032 class),
        not a real timeout. Refuse to speak; the caller already drops to silent
        LISTENING, which is the honest outcome for a fast turn that produced
        nothing. The sentinel anchor (0.0 = turn start never stamped) means we
        cannot PROVE the turn was fast, so we still speak (zero-silent-drop wins).
        """
        now = time.monotonic()
        turn_start = getattr(self, "_turn_start_monotonic", 0.0)
        elapsed = (now - turn_start) if turn_start > 0.0 else -1.0
        # Per-site floor: the no-first-frame path is beheaded at the shorter TTS
        # ceiling (20 s), so it must use a floor derived from THAT ceiling — using
        # the 30 s brain-stall floor here swallowed a real 20.83 s abort and left
        # the user in silence (live bug 2026-06-14). The stall/total-cap sites
        # genuinely fire at the brain stall window, so they keep that floor.
        if site == "empty_after_no_first_frame":
            floor = getattr(self, "_no_first_frame_floor_s", None)
            if floor is None:  # bare test instance that never ran __init__
                ceiling = getattr(
                    self, "_speak_playback_ceiling_s", _TTS_PLAYBACK_CEILING_S
                )
                floor = _NO_FIRST_FRAME_FLOOR_FRACTION * ceiling
            floor_source = "no_first_frame_ceiling"
        else:
            floor = getattr(
                self, "_min_timeout_phrase_s", getattr(self, "_brain_timeout_s", 30.0)
            )
            floor_source = "brain_stall_window"
        # Streaming flag is for attribution only; never let it crash the turn.
        try:
            streaming: bool | None = self._streaming_enabled()
        except Exception:  # noqa: BLE001 — instrumentation must never crash the turn
            streaming = None
        payload = (
            "site=%s elapsed_s=%.2f since_progress_s=%.2f no_first_frame=%s "
            "spoke_this_turn=%s streaming=%s floor_s=%.2f floor_source=%s"
        )
        fields = (
            site,
            elapsed,
            now - getattr(self, "_brain_last_progress", now),
            getattr(self, "_playback_aborted_no_first_frame", False),
            getattr(self, "_spoke_this_turn", False),
            streaming,
            floor,
            floor_source,
        )
        if turn_start > 0.0 and (elapsed + _TIMEOUT_FLOOR_EPSILON_S) < floor:
            log.warning(
                "brain-timeout phrase SUPPRESSED (turn ran under floor — stale "
                "state, not a real timeout; staying silent): " + payload,
                *fields,
            )
            return
        log.warning("brain-timeout phrase spoken: " + payload, *fields)
        picker_lang = _phrase_lang(lang)
        # Cause-aware, honest phrase (live complaint 2026-06-30): name a tool
        # cause when the turn was beheaded mid-tool-loop (no first frame) or a
        # desktop tool was demonstrably active; otherwise honestly admit no
        # answer was found. Resolved through the one output-language decision.
        tool_active = getattr(self, "_long_tool_last_activity", 0.0) > 0.0
        phrase = _resolve_timeout_phrase(site, lang, tool_active=tool_active)
        # The outcome is now terminal for this utterance: arm the double-answer
        # guard BEFORE speaking so a concurrently-abandoned brain stream that
        # later reaches ``_speak`` (kind="reply") is suppressed, never voiced as
        # a second answer for the same content. Set only here — past the floor
        # guard's early return — so a floor-suppressed (no-op) call never arms it.
        self._brain_timeout_spoken_this_turn = True
        try:
            await self._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
            await self._speak(phrase, language=picker_lang, kind=SPOKEN_KIND_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            log.warning("Brain-timeout fallback speak failed: %s", exc)

    async def _brain_with_ack(self, text: str, lang: str) -> str:
        """Brain-Call mit optionalem Zwischen-Ack.

        Startet Brain-Call und einen ``_task_ack_delay_s``-Timer parallel.
          - Brain schneller als Timer → direkt Antwort zurueck (kein Ack, keine Latenz).
          - Timer schneller → eine zufaellige JARVIS-Start-Ack-Phrase ("Sofort.",
            "Right away." …) aus dem pre-renderten Cache abspielen, dann auf
            Brain weiterwarten. Nach Ack-Playback wieder THINKING anzeigen,
            damit der Orb den richtigen Modus hat.
        """
        brain_task = asyncio.create_task(self._brain(text), name="brain")
        timer_task = asyncio.create_task(
            asyncio.sleep(self._task_ack_delay_s), name="ack-timer"
        )
        try:
            done, _pending = await asyncio.wait(
                {brain_task, timer_task}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            brain_task.cancel()
            timer_task.cancel()
            raise

        if brain_task in done:
            timer_task.cancel()
            return brain_task.result()

        # Timer war schneller — Brain denkt noch. Ack abspielen, dann weiterwarten.
        pcm = self._pick_task_ack_pcm(lang)
        if pcm:
            try:
                await self._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
                await self._player.play_pcm(pcm, sample_rate=GEMINI_TTS_SAMPLE_RATE)
                self._suppress_session_input_after_tts("task_ack")
            except Exception as exc:  # noqa: BLE001
                log.warning("Task-Ack-Playback fehlgeschlagen: %s", exc)
            await self._set_turn_state(TurnTakingState.PROCESSING)
        return await brain_task

    def _pick_task_ack_pcm(self, lang: str) -> bytes:
        """Liefert PCM-Bytes einer zufaelligen Start-Ack-Phrase fuer die Sprache."""
        picker_lang = _phrase_lang(lang)
        phrase = self._phrase_picker.pick("start_ack", picker_lang)  # type: ignore[arg-type]
        pcm = self._task_ack_pcm.get((picker_lang, phrase), b"")
        if pcm:
            log.info("🎙 Task-Ack [%s]: %s", picker_lang, phrase)
        else:
            log.debug("Task-Ack Cache-Miss fuer (%s, %s)", picker_lang, phrase)
        return pcm

    def _abort_playback_device(self) -> None:
        """Unblock + tear down the live output stream after a playback stall."""
        player = getattr(self, "_player", None)
        if player is None:
            return
        if hasattr(player, "abort_active"):
            player.abort_active()
        else:  # older player without the Wave-1 hook
            player.stop()

    async def _await_playback(
        self,
        play_task: asyncio.Task[Any],
        extra_tasks: set[asyncio.Task[Any]],
    ) -> set[asyncio.Task[Any]]:
        """Wait for a TTS playback task, aborting a wedged device fast.

        The dominant 60-156 s voice-hang root cause was a wedged output device:
        PortAudio's blocking ``stream.write`` got stuck in its ``to_thread``
        worker (which Python cannot cancel), so ``play_task`` never completed and
        the only escape was a 120 s ceiling. This poll loop watches the player's
        write-progress (``last_write_ns``): a mid-playback gap of
        ``_speak_playback_stall_s`` means the device is wedged → we call
        ``player.abort_active()`` (Pa_AbortStream) to unblock the write so the
        turn unwinds in ~5 s and the session re-arms. A ``_speak_playback_
        ceiling_s`` absolute backstop covers anything the progress signal misses.

        Returns the set of completed tasks, or an EMPTY set when it aborted on a
        stall/ceiling (the device has already been torn down + the event logged).
        Cross-platform: pure asyncio + PortAudio abort.
        """
        ceiling = getattr(self, "_speak_playback_ceiling_s", _TTS_PLAYBACK_CEILING_S)
        stall_s = getattr(self, "_speak_playback_stall_s", _TTS_PLAYBACK_STALL_S)
        watch = {play_task, *extra_tasks}
        start = time.monotonic()
        while True:
            done, _pending = await asyncio.wait(
                watch, timeout=0.25, return_when=asyncio.FIRST_COMPLETED
            )
            if done:
                return done
            player = getattr(self, "_player", None)
            last_write = getattr(player, "last_write_ns", 0) if player is not None else 0
            owner_missing = object()
            owner_task_id = (
                getattr(player, "last_write_owner_task_id", owner_missing)
                if player is not None
                else owner_missing
            )
            if owner_task_id is not owner_missing and owner_task_id != id(play_task):
                last_write = 0
            if last_write <= 0:
                # No first frame yet: the synthesize / first-frame window. A slow
                # TTS provider must NOT be misread as a device wedge — that false
                # abort (on every turn whose brain/synthesize took > stall_s) was
                # the "Jarvis listens forever / answer never heard" root cause.
                # Only a generous no-first-frame backstop applies here; it covers
                # a provider that never yields any audio at all.
                #
                # The turn is actively WORKING whenever EITHER heartbeat landed
                # AFTER this await began — in both cases the brain simply has not
                # started narrating yet, so there is legitimately nothing to play.
                # Re-arm the window from the LATER heartbeat so the ceiling bounds
                # silence since the LAST sign of life, never total work time.
                #   • ``_long_tool_last_activity`` — a computer_use step
                #     (ObservationCaptured/ActionPlanned → _on_agent_progress).
                #     Live bug 2026-06-09 ("öffne CapCut"): the CU loop was
                #     beheaded on step 4 at 20 s and the turn came back empty/mute.
                #   • ``_brain_last_progress`` — the brain's own round/token
                #     heartbeat (_mark_brain_progress, pinged on every tool-use-
                #     loop round AND every streamed token; the SAME signal the
                #     brain stall guard trusts). Live bug 2026-06-14 14:21 + 14:24
                #     ("weather in Melbourne"): a NON-desktop tool loop (geocode +
                #     DuckDuckGo + open-meteo, ~20 s of real work) emits no CU
                #     step, so before this the 20 s ceiling beheaded the working
                #     turn and the user heard "that took too long" + a hang-up.
                #   • ``_brain_thinking_heartbeat`` — the PRE-first-token think
                #     pulse from _run_brain_with_stall_guard. Live bug 2026-06-14
                #     16:17 ("Reise … Melbourne"): the deep brain built an
                #     18k-token cache then thought silently with no on_progress
                #     and no token, so the two heartbeats above never moved and
                #     the 20 s ceiling beheaded a working brain.
                # Strictly-greater keeps a heartbeat from BEFORE this await out of
                # the decision — per-unit re-arm, the BUG-032 stale-counter
                # lesson; the brain stall guard applies the same suspension to its
                # absolute ceiling. Once the brain finishes producing text both
                # heartbeats freeze, so a genuinely wedged TTS provider (text fed,
                # no audio) is still aborted ``ceiling`` s after the last progress.
                heartbeat = max(
                    getattr(self, "_long_tool_last_activity", 0.0),
                    getattr(self, "_brain_last_progress", 0.0),
                    getattr(self, "_brain_thinking_heartbeat", 0.0),
                )
                if heartbeat > start:
                    start = heartbeat
                if (time.monotonic() - start) >= ceiling:
                    log.warning(
                        "TTS produced no audio within %.0fs — aborting "
                        "(no first frame).", ceiling,
                    )
                    # Mark the turn as beheaded so the empty-turn handler can
                    # speak an audible timeout notice (AD-OE6) instead of
                    # dropping the user into silent LISTENING.
                    self._playback_aborted_no_first_frame = True
                    self._abort_playback_device()
                    return set()
                continue
            # Playback has started: only a genuine MID-playback gap (frames were
            # flowing, then froze) is a device wedge. An actively-progressing long
            # answer keeps last_write fresh and is never aborted — the old flat
            # total-time ceiling used to truncate any spoken turn over ~20 s.
            if _playback_progress_stalled(last_write, stall_s):
                log.warning(
                    "TTS playback stalled — no audio frames for %.1fs — aborting "
                    "device + unwinding turn (device-wedge recovery).", stall_s,
                )
                self._abort_playback_device()
                return set()

    def _emit_spoken(
        self,
        text: str,
        language: str | None,
        kind: str,
        detail: str | None = None,
    ) -> None:
        """Announce a VOICED phrase on the bus as a ``SpeechSpoken`` event.

        The Transcription log was previously blind to everything Jarvis speaks
        except the brain's normal reply (recorded via ``ResponseGenerated`` ->
        ``jarvis_text``). This publishes every OTHER voiced phrase so the
        passive ``SessionRecorder`` can document it in the transcript.

        Fire-and-forget (``asyncio.create_task``), mirroring the
        ``LatencyTurnComplete`` telemetry emit — the voice hot path never waits
        on the bus dispatch (AP-9 / AD-OE2). The ``reply`` sentinel is dropped
        because the reply is already in the transcript; empty text and a
        missing bus are no-ops, and every error is swallowed so a telemetry
        hiccup can never break a turn.
        """
        if kind == "reply" or not text or not text.strip():
            return
        bus = getattr(self, "_bus", None)
        if bus is None:
            return
        try:
            event = SpeechSpoken(
                source_layer="speech.pipeline",
                text=text,
                language=(language or "de"),
                spoken_kind=kind,
                detail=(detail or None),
            )
            asyncio.create_task(bus.publish(event))  # noqa: RUF006 — fire-and-forget
        except Exception:  # noqa: BLE001 — telemetry must never break the turn
            log.debug("SpeechSpoken emit failed", exc_info=True)

    #: Spoken readback for an OpenClaw background task that finished off the
    #: chat path. de/en/es so the readback follows the conversation language
    #: instead of a hardcoded German literal (forensic 2026-06-23). German
    #: strings are TTS product surface, not source artifacts (i18n-allow).
    _BG_READBACK_PHRASES: dict[str, dict[str, str]] = {
        "de": {
            "done": "Fertig.",  # i18n-allow
            "done_summ": "Fertig. {s}",  # i18n-allow
            "fail": "Das hat nicht geklappt. {e}",  # i18n-allow
            "unknown_err": "unbekannter Fehler",  # i18n-allow
        },
        "en": {
            "done": "Done.",
            "done_summ": "Done. {s}",
            "fail": "That didn't work. {e}",
            "unknown_err": "unknown error",
        },
        "es": {
            "done": "Listo.",
            "done_summ": "Listo. {s}",
            "fail": "Eso no funcionó. {e}",
            "unknown_err": "error desconocido",
        },
    }

    _BCP47: dict[str, str] = {"de": "de-DE", "en": "en-US", "es": "es-ES"}

    @classmethod
    def _bcp47(cls, lang: object) -> str | None:
        """Map a de/en/es turn-language code to a TTS BCP-47 locale, else None.

        Single source for the whole pipeline — replaces four hand-copied maps,
        one of which (the task-ack prerender) had silently dropped ``es``, so a
        Spanish turn there got no language pin and the multilingual TTS could
        code-switch.
        """
        return cls._BCP47.get(str(lang or "").lower())

    def _output_language(self, stt_language: object, text: str) -> str:
        """Resolve THIS turn's output language for EVERY spoken/written layer.

        Honors the live ``brain.reply_language`` pin (the desktop Languages
        view) so a user-selected language reaches the ack preamble, the canned
        status / clarify / timeout phrases and the TTS voice — not only the
        deep-brain reply (forensic 2026-06-18: a German utterance mis-heard as
        English text drove the whole chain English because the pipeline
        re-derived language from text/STT alone). ``auto``/unset mirrors the
        detected input language. Single source for the whole pipeline, per
        CLAUDE.md "Runtime Output Language". The live pin lives on the
        BrainManager (hot-reloaded via ``set_reply_language``); the config is
        only consulted when the brain callback does not expose the pin (tests /
        mock brains).
        """
        brain = getattr(self, "_brain", None)
        pin = getattr(brain, "reply_language", None)
        if pin is None:
            cfg = getattr(self, "_config", None)
            pin = getattr(getattr(cfg, "brain", None), "reply_language", None)
        # Conversation stickiness: a thin interjection ("Now") inherits the
        # running conversation language instead of flipping ack/phrases/TTS
        # (forensic 2026-06-18). The brain owns the sticky conversation language.
        conv = getattr(brain, "conversation_language", "")
        return resolve_output_language(
            pin, stt_language, text, conversation_language=conv
        )

    async def _speak(
        self, text: str, language: str | None = None, *, kind: str = "reply"
    ) -> bool:
        """Sprich Text aus — mit Barge-in-Monitor.

        `language` = "de"/"en" (Whisper-Code) wird zu "de-DE"/"en-US" gemappt
        und an TTS übergeben (voice bleibt gleich — Gemini-Voices sind
        sprachagnostisch).

        ``kind`` tags WHAT is being voiced for the Transcription log. The
        default ``"reply"`` is the ordinary brain answer (already documented as
        ``jarvis_text``, so not re-recorded); a canned phrase passes its
        specific kind (``"timeout"``, ``"clarify"``, ``"privacy"`` …) and is
        published as a ``SpeechSpoken`` event via ``_emit_spoken``.

        Parallel zum Playback läuft ``_barge_monitor`` auf einer separaten
        Mic-Instanz. Erkennt Silero-VAD dort User-Sprache (Threshold 0.8,
        3 consecutive Frames, 400 ms Grace-Period), wird das Player-Playback
        sofort gestoppt. Rückgabewert: ``True`` wenn gebargedet wurde.

        When muted (mascot doubleClick), we short-circuit: no synthesize
        call, no playback. We return ``False`` (no barge-in occurred) so
        callers that branch on ``barged`` behave consistently.
        """
        if getattr(self, "_muted", False):
            log.debug("_speak suppressed — voice muted")
            return False
        # Double-answer guard (live complaint 2026-06-30): once a timeout /
        # "couldn't finish" notice closed THIS utterance, an ordinary brain
        # ANSWER (kind="reply", the default) must NOT also be voiced — a stalled
        # tool that timed out and then re-answered for the same content. Gate
        # ONLY the brain answer: every canned notice (timeout / clarify / ack /
        # unavailable) and every background mission readback (completion /
        # subagent / announcement) carries its own kind and is intentionally
        # exempt, so a legitimately-spawned later result still speaks. The flag
        # re-arms at the next utterance finalize. Before ``_emit_spoken`` so a
        # suppressed answer — never actually voiced — is not logged.
        if kind == "reply" and getattr(self, "_brain_timeout_spoken_this_turn", False):
            log.info(
                "Brain answer suppressed — a timeout notice already closed this "
                "turn (no double-answer)."
            )
            return False
        # Document the voiced phrase in the session log (no-op for the reply
        # sentinel and empty text). After the mute check, so a suppressed
        # phrase — which is never actually voiced — is not recorded.
        self._emit_spoken(text, language, kind)
        # Track that the assistant has spoken at least once in this session.
        # Used by _emit_completeness_signal to pick earcon vs. spoken cue.
        self._session_has_assistant_spoken = True
        lang_code = self._bcp47(language)
        try:
            chunks = self._tts.synthesize(text, language_code=lang_code)
        except TypeError:
            chunks = self._tts.synthesize(text)

        play_task = asyncio.create_task(self._player.play_chunks(chunks), name="tts-play")
        barge_task = asyncio.create_task(self._barge_monitor(), name="barge-monitor")
        # Hangup is the hard kill-switch ("auflegen"): a hangup mid-phrase must
        # abort _speak at once. Without watching the event here, a stalled
        # output device — or a hangup fired during a fallback phrase
        # (_speak_brain_unavailable / _speak_brain_timeout) — keeps ``play_task``
        # pending until the no-first-frame ceiling, so ``_speak`` never returns and the
        # voice session wedges in JARVIS_SPEAKING (same root cause as
        # _brain_streaming; live bug 2026-06-01). Treat it like a barge-in.
        # getattr-fallback: test fixtures build the pipeline via ``__new__``
        # and don't set ``_hangup_event`` — a fresh never-set Event keeps the
        # behaviour identical to pre-fix (the waiter simply never fires).
        hangup_event = getattr(self, "_hangup_event", None) or asyncio.Event()
        hangup_task = asyncio.create_task(
            hangup_event.wait(), name="hangup-during-speak"
        )
        barged = False
        # A stalled output device (blocking ``stream.write``) or a stalled TTS
        # stream can wedge ``play_chunks``. The watchdog aborts a wedged device
        # in ~5 s (vs the old 120 s ceiling) so ``_speak`` always returns and the
        # voice session can never freeze with ``self._state`` stuck at ACTIVE
        # (the wake loop only re-arms in IDLE).
        try:
            done = await self._await_playback(play_task, {barge_task, hangup_task})
            if not done:
                # Watchdog already aborted the wedged device + logged the reason.
                pass
            elif hangup_task in done and not hangup_task.cancelled():
                log.info("📵 Hangup during TTS — aborting turn")
                self._player.stop()
                barged = True
            else:
                if barge_task in done and not barge_task.cancelled():
                    if barge_task.result():
                        log.info("🛑 Barge-in — stoppe TTS-Playback")
                        self._player.stop()
                        barged = True
                if (
                    barge_task in done
                    and not barge_task.cancelled()
                    and not barged
                    and not play_task.done()
                ):
                    # Barge monitor returned without barging (mic ended/error)
                    # but playback is still running — wait it out under the same
                    # device-wedge watchdog, still abortable by a hangup.
                    tail_done = await self._await_playback(play_task, {hangup_task})
                    if play_task not in tail_done:
                        if hangup_task in tail_done:
                            log.info("📵 Hangup during TTS — aborting turn")
                            barged = True
                        # else: watchdog already aborted the wedged device + logged.
                        self._player.stop()
        except Exception as exc:  # noqa: BLE001
            log.exception("Playback-Fehler: %s", exc)
        finally:
            for t in (play_task, barge_task, hangup_task):
                if not t.done():
                    t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        if not barged:
            self._suppress_session_input_after_tts("response")
        return barged

    async def _barge_monitor(
        self, *, grace_s: float = 1.5, respect_input_suppression: bool = False
    ) -> bool:
        """Lauscht auf einer zweiten Mic-Instanz, ob der User während Jarvis'
        TTS-Ausgabe zu sprechen beginnt. Returnt ``True`` sobald das der Fall ist.

        User-Feedback 2026-04-22: Barge-in feuerte fast sofort nach TTS-Start
        (z.B. ~600 ms) und wuergte die Antwort ab — Ursache war Speaker→Mic-
        Echo (Kopfhoerer-Leakage oder Open-Back). Ohne Hardware-AEC koennen
        wir nur per Heuristik filtern. Jetzt aggressiv konservativ:
          - 1500 ms Grace-Period (TTS-Start + initiale Echo-Phase)
          - Silero-Threshold 0.97 (nur bei *sehr* klarem Speech)
          - 12 consecutive Frames (~380 ms) — Echos sind kurz/fluktuierend
        Ergebnis: Barge-in greift nur wenn User klar ueber die TTS-Ausgabe
        spricht, nicht bei Lautsprecher-Rueckkopplung.
        """
        try:
            await asyncio.sleep(grace_s)
        except asyncio.CancelledError:
            return False

        detector = SileroEndpointer(speech_threshold=0.97)
        detector._ensure_model()

        try:
            async with MicrophoneCapture(
                device=self._input_device,
                max_queue_chunks=REALTIME_QUEUE_CHUNKS,
                device_priority=self._input_priority,
            ) as mic:
                residual = np.empty(0, dtype=np.float32)
                speech_run = 0
                async for chunk in mic.stream():
                    # Honour the global mute (orb double-click → _muted=True):
                    # while muted the user has told us to stop listening, so a
                    # barge must never fire on this second mic — mirrors
                    # _activation_allowed / _speak, which both short-circuit on
                    # _muted. Without this the thinking-interrupt monitor aborted a
                    # muted-but-still-working turn (live bug 2026-07-01).
                    if getattr(self, "_muted", False):
                        speech_run = 0
                        continue
                    # Echo-suppression (spec §4.2): while our own ACK/preamble
                    # audio is still within the post-TTS suppression window, skip
                    # detection so the thinking-interrupt monitor never mistakes
                    # the preamble's speaker->mic leakage for the user speaking.
                    # Pure read (no mutation) to avoid racing the session-input
                    # dropper that shares _input_suppressed_until_ns.
                    if respect_input_suppression:
                        until_ns = getattr(self, "_input_suppressed_until_ns", 0)
                        chunk_ts = getattr(chunk, "timestamp_ns", 0) or time.time_ns()
                        if until_ns > 0 and chunk_ts < until_ns:
                            speech_run = 0
                            continue
                    samples = pcm_bytes_to_np(chunk.pcm)
                    buf = np.concatenate([residual, samples])
                    n_full = len(buf) // VAD_FRAME_SAMPLES
                    if n_full == 0:
                        residual = buf
                        continue
                    frames = buf[: n_full * VAD_FRAME_SAMPLES].reshape(n_full, VAD_FRAME_SAMPLES)
                    residual = buf[n_full * VAD_FRAME_SAMPLES:]
                    for frame in frames:
                        prob = detector._prob(frame)
                        if prob >= 0.97:
                            speech_run += 1
                            if speech_run >= 12:
                                return True
                        else:
                            speech_run = 0
        except asyncio.CancelledError:
            return False
        except Exception as exc:  # noqa: BLE001
            log.warning("Barge-in-Monitor Fehler: %s", exc)
        return False


# ----------------------------------------------------------------------
# CLI-Entry
# ----------------------------------------------------------------------

def _load_env() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v and not os.environ.get(k):
            os.environ[k] = v


async def _main() -> None:
    import sys
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, OSError):
            pass

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    )
    _load_env()

    from jarvis.core import config as cfg
    config = cfg.load_config()

    stt = FasterWhisperProvider(
        model=config.stt.model,
        device=config.stt.device,
        compute_type=config.stt.compute_type,
        language=config.stt.language if config.stt.language != "auto" else None,
    )
    from jarvis.plugins.tts import build_tts_from_config
    tts = build_tts_from_config(config.tts)
    # Env-Override bleibt fuer Quick-Tests an der CLI bestehen.
    env_voice = os.environ.get("JARVIS_TTS_VOICE")
    if env_voice and hasattr(tts, "_default_voice"):
        tts._default_voice = env_voice
    from jarvis.brain.factory import build_default_brain
    brain = build_default_brain()

    _call_hk, _ptt_hk = config.trigger.resolve_hotkeys()
    pipeline = SpeechPipeline(
        call_hotkeys=_call_hk,
        ptt_hotkeys=_ptt_hk,
        hangup_hotkeys=(
            (config.trigger.hotkey_hangup,)
            if config.trigger.hotkey_hangup.strip()
            else ()
        ),
        wake_keywords=(),
        wake_threshold=0.15,
        stt=stt,
        tts=tts,
        brain_callback=brain,
        enable_whisper_wake=True,
        idle_timeout_s=config.trigger.session_idle_timeout_s,
        input_device=config.audio.input_device or None,
        output_device=config.audio.output_device or None,
    )
    print()
    print("=" * 64)
    print("  Personal Jarvis — Speech-Pipeline")
    print("=" * 64)
    print("  ANRUFEN :  sag dein Wake-Word           |  Ctrl+RightAlt+J  |  F3+F4")
    print("  AUFLEGEN:  sag 'auflegen'               |  F1+F2")
    print("  BEENDEN :  Ctrl+C im Terminal")
    print()
    print("  Wenn Jarvis dich hört: Ding-Ton + 'Ja?' zurück.")
    print("  Live-Score-Log zeigt dir ob Wake-Word triggert.")
    print("=" * 64)
    print()
    await pipeline.run()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\nBeendet.")
