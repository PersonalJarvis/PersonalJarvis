Warning: truncated output (original token count: 236827)
Total output lines: 18220

"""Speech pipeline with call/hangup state and parallel wake detection.

Wake detection runs in IDLE through two paths sharing one microphone fanout:

1. openWakeWord -- fast (15-30 ms), but less robust across pronunciations.
2. Whisper wake -- robust (800-1200 ms) and natively multilingual.

Activation feedback is visual while capture is live, avoiding both an input
dead zone and speaker echo in the recorded utterance. Global call/hangup
hotkeys remain active alongside wake detection.
"""
from __future__ import annotations

import asyncio
import enum
import hashlib
import json
import logging
import os
import random
import re
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

import numpy as np

from jarvis.audio import level_tap, mic_level
from jarvis.audio.capture import (
    REALTIME_QUEUE_CHUNKS,
    MicrophoneCapture,
    capture_chunks_for_duration,
    pcm_bytes_to_np,
)
from jarvis.audio.chime import (
    CHIME_PCM,
    CHIME_SAMPLE_RATE,
    DISCONNECT_PCM,
    READY_PCM,
)
from jarvis.audio.device_init import wait_for_stable_audio_devices
from jarvis.audio.effects import bind_shared_audio_player
from jarvis.audio.player import AudioPlayer
from jarvis.audio.vad import SileroEndpointer
from jarvis.audio.vad_reasons import FORCED_CUT_REASONS
from jarvis.brain.output_filter import FALLBACK_PHRASES, ScrubResult, scrub_for_voice
from jarvis.brain.scrub_verdict import is_harmless_scrub_residue
from jarvis.brain.turn_planner import plan_turn
from jarvis.core.cancellation import cancel_and_reap, raise_if_cancelling
from jarvis.core.events import (
    CU_PROGRESS_EVENTS,
    DICTATION_REFUSAL_REASONS,
    ActionPlanned,
    ActionProposed,
    AnnouncementRequested,
    AudioOutFirst,
    BrainTTFT,
    DictationCompleted,
    DictationPromptModePauseToggleRequested,
    DictationRefused,
    DictationStarted,
    DictationTranscribing,
    DictationTranscript,
    JarvisAgentAnnouncement,
    JarvisAgentBackgroundCompleted,
    LatencyTurnComplete,
    ListeningStarted,
    MessageSent,
    ObservationCaptured,
    PetVisibilityToggleRequested,
    SpeechSpoken,
    TranscriptFinal,
    TranscriptionUpdate,
    UtteranceCaptured,
    VoiceBootStatus,
    VoiceInputHeld,
    VoiceMuteChanged,
    VoiceMuteToggleRequested,
    VoiceSessionEnded,
    VoiceSessionStarted,
    VoiceSpeakerMuteChanged,
    WakeCandidateDetected,
    WakeWordDetected,
)
from jarvis.core.protocols import AudioChunk, Transcript
from jarvis.core.redact import safe_preview
from jarvis.core.turn_language import (
    DEFAULT_LOCALE,
    normalize_language_tag,
    resolve_output_language,
)
from jarvis.plugins.stt.fwhisper import FasterWhisperProvider
from jarvis.plugins.tts.gemini_flash_tts import GEMINI_TTS_SAMPLE_RATE, GeminiFlashTTS
from jarvis.plugins.wake.openwakeword_provider import (
    OpenWakeWordProvider,
)
from jarvis.sessions.constants import (
    HANGUP_DESKTOP_FALLBACK,
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
    SPOKEN_KIND_REPLY,
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
from jarvis.speech.continuation_buffer import REASON_MIC_RESUMED, ContinuationBuffer
from jarvis.speech.continuation_window import ContinuationWindow
from jarvis.speech.echo_guard import SelfEchoGuard
from jarvis.speech.hangup import (
    HANGUP_RE,
    contains_end_signal,
    is_legacy_farewell,
    matched_hangup_pattern,
    supports_semantic_hangup,
)
from jarvis.speech.pending_buffer import PendingPromptBuffer
from jarvis.speech.persona import PhrasePicker, iter_all_start_ack
from jarvis.speech.rolling_whisper_wake import RollingWhisperWake
from jarvis.speech.stt_failure import (
    classify_stt_failure,
    normalize_stt_failure,
    stt_failure_message,
)
from jarvis.speech.usage_meter import meter_stt, meter_tts
from jarvis.speech.usage_sink import SpeechSpendRecorder
from jarvis.speech.wake_verifier import (
    CUSTOM_WAKE_MIN_RMS,
    pcm_tail_rms,
    verify_wake_with_stt,
)
from jarvis.telemetry.latency import LatencyPhase, LatencyTracker
from jarvis.trigger.hotkey import HotkeyTrigger
from jarvis.voice.instant_ack import (
    PROGRESS_AFTER_S,
    InstantAckPlan,
    ToolActivity,
    classify_tool_activity,
    compose_contextual_ack,
    note_spoken,
    pick_instant_ack_text,
    pick_progress_text,
    plan_instant_ack,
)

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus
    from jarvis.state.supervisor import Supervisor


log = logging.getLogger("jarvis.speech.pipeline")

# Supervisor state for "a realtime transport is opening but cannot carry the
# call yet". A subscription transport spends 15-45 s here, and the session is
# already accepted into LISTENING by then — so without its own state every
# surface claims the user is being heard while the provider has not accepted a
# single frame (ST-7). The orb surface already speaks this vocabulary
# (``jarvis/overlay/surface.py``); the supervisor enum and the orb bus bridge
# are what carry it there. ``Supervisor.set_state`` ignores a state it does not
# know, so this degrades to the previous behaviour with one warning rather than
# raising on an install whose enum predates it.
_REALTIME_CONNECTING_STATE = "CONNECTING"


async def _run_voice_critical_thread(fn: Callable[[], Any]) -> Any:
    """Run blocking voice startup work outside the shared default executor.

    Wake and local-STT implementations legitimately use ``asyncio.to_thread``
    for native inference. A slow or un-cancellable inference can occupy every
    default-pool worker, so queueing realtime session assembly there creates a
    false LISTENING state: the microphone is buffered, but the provider cannot
    begin accepting that audio until a worker becomes free. A fresh daemon
    thread keeps this voice-critical control path independent and cannot hold
    process shutdown open if a platform credential backend itself wedges.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()

    def _resolve(setter: Callable[[Any], None], value: Any) -> None:
        if not future.done():
            setter(value)

    def _runner() -> None:
        try:
            result = fn()
        except BaseException as exc:  # noqa: BLE001 - relay to async caller
            callback = future.set_exception
            value: Any = exc
        else:
            callback = future.set_result
            value = result
        try:
            loop.call_soon_threadsafe(_resolve, callback, value)
        except RuntimeError:
            # The owning loop may close while an un-cancellable native call is
            # still unwinding. The daemon thread can then finish silently.
            pass

    threading.Thread(
        target=_runner,
        name="jarvis-voice-critical",
        daemon=True,
    ).start()
    return await future


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


#: The local endpointer's own window, used whenever the config asks for
#: "automatic" (``speech.vad_silence_ms = 0``). A realtime transport resolves
#: automatic to the PROVIDER's factory turn detection; a local VAD has no
#: vendor to defer to, so it keeps the 1.5 s rule this pipeline shipped with.
_LOCAL_SILENCE_WINDOW_DEFAULT_MS = 1500


def _local_silence_window_ms(ms: int | None) -> int:
    """Resolve a configured silence window for the LOCAL VAD.

    0 / None / a negative value all mean "automatic" and resolve to the
    pipeline's own window; anything else is an explicit user choice and is
    clamped to the config field's bounds so a typo cannot cut a talker off
    between words.
    """
    try:
        raw = int(ms) if ms is not None else 0
    except (TypeError, ValueError):
        # A non-numeric config value is a typo, not a fault: the built-in
        # window keeps endpointing usable.
        raw = 0
    if raw <= 0:
        return _LOCAL_SILENCE_WINDOW_DEFAULT_MS
    return max(500, min(5000, raw))


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
# finished background mission / sub-agent / worker / Jarvis-Agent result. These
# punch through the hangup gate (AD-OE5/OE6 zero-silent-drop) and cancel any
# pending "still on it" heartbeat. ``subagent`` is the attributed sibling of
# ``completion``: same delivery semantics, but rendered as its own transcript
# track ("Jarvis Sub-Agent / Output").
_READBACK_KINDS: frozenset[str] = frozenset(
    {SPOKEN_KIND_COMPLETION, SPOKEN_KIND_SUBAGENT}
)


#: Readback sources that never speak outside a call (see ``_is_agent_reply``).
#: A mission the user just asked for is not here: its answer may still punch
#: through the hangup gate (AD-OE5/OE6). Explicit delegations wait for a call
#: and a conversational pause; their results never open a call themselves.
_HELD_FOR_CALL_SOURCES: frozenset[str] = frozenset(
    {
        "society.lead",
        "agentic_ide.readback",
        "delegation.batch",
        "tasks.runner",
        "workflows.runner",
        "workflows.scheduler",
        "desktop_app.conductor",
        "skills.cron",
    }
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
    "de": (
        "Entschuldige, ich habe dich akustisch gerade nicht verstanden. "
        "Sag es bitte noch einmal."
    ),
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
# for "taking too long" with no reason. Honesty over guessing (AGENTS.md §1.4):
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
# language decision via ``_resolve_timeout_phrase`` below (AGENTS.md §1 — no
# per-layer language re-derivation).
_TIMEOUT_TOOL_STALL_PHRASE: dict[str, str] = {
    "de": (
        "Ich habe rechtzeitig keine Antwort bekommen. Ein Tool, auf das ich "
        "gewartet habe, hat nicht reagiert."
    ),
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
    a per-layer re-derivation (AGENTS.md §1). String-only, no LLM call (AP-11).
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
    """HTTP status of an STT error, or ``None`` when it was not an HTTP error.

    Delegates to ``jarvis.plugins.stt.errors.status_from_exception``, which is
    the ONE place the shapes are enumerated: our own ``STTHTTPError.status``
    first, then the google-genai SDK's ``.code``, then the
    ``httpx.HTTPStatusError`` family's ``.response.status_code``. Keeping a
    second table here is precisely the drift AP-4 is about — and the reason the
    retry ladder used to work for exactly one provider was that this function
    knew only the last of those three shapes.

    The import is lazy and the legacy duck-type survives as the fallback, so
    this stays correct on a host where the plugin package cannot be imported at
    all (a stripped install, an import error mid-reload). Errors are rare, so
    the cached import costs nothing worth measuring.
    """
    try:
        from jarvis.plugins.stt.errors import status_from_exception
    except Exception:  # noqa: BLE001 — an unimportable plugin package is not fatal
        status = getattr(exc, "status", None)
        if isinstance(status, int) and not isinstance(status, bool):
            return status
        return getattr(getattr(exc, "response", None), "status_code", None)
    return status_from_exception(exc)


def _is_transient_stt_error(exc: BaseException) -> bool:
    """True when an STT error should clear without changing provider setup.

    A busy local engine is the normal AP-24 handoff race: cancelling an
    ``asyncio.to_thread`` wrapper cannot stop the native preview already using
    the model, so the final call must retry after that preview releases it.
    """
    return (
        _stt_error_status(exc) in _STT_TRANSIENT_STATUS
        or classify_stt_failure(exc) == "engine_busy"
    )


def _stt_crossover_would_leave_the_machine() -> bool:
    """Whether an AUTOMATIC STT crossover would be this host's first upload.

    Delegates to ``jarvis.dictation.polish_client.stt_runs_on_device`` — the
    SAME predicate the dictation polish pass consults before it may send a
    transcript to a cloud model, which in turn asks the repo's existing
    ``stt_family_id`` rather than matching provider names (AP-21). One question
    with two answers is how two lanes end up disagreeing, and this pair
    disagreed in the worse direction: the polish pass refused to upload the
    TEXT while the crossover was still free to upload the AUDIO that text was
    derived from. A recording is strictly more sensitive than its transcript —
    it carries the voice itself and whatever else was audible in the room — so
    refusing the smaller of the two while permitting the larger is not a
    position that can be defended to a user.

    Fails CLOSED, exactly like the predicate it delegates to: a host whose
    recognizer cannot be determined keeps its audio. The two mistakes are not
    symmetric. Guessing "local" costs a crossover on a turn that had already
    failed and that the user hears fail. Guessing "cloud" uploads the recording
    of somebody who picked an on-device recognizer to prevent precisely that,
    and nothing on screen would ever tell them.
    """
    try:
        from jarvis.dictation.polish_client import stt_runs_on_device

        return stt_runs_on_device()
    except Exception as exc:  # noqa: BLE001 — an unanswerable question keeps the audio
        log.warning(
            "Could not determine whether the configured recognizer runs on this "
            "machine (%s); declining the automatic STT crossover so the audio "
            "stays here.",
            exc,
        )
        return True


def _resolve_stt_fallback_chain(stt_cfg: Any, configured: str) -> tuple[str, ...]:
    """Provider ids to cross to when ``configured`` fails at CALL time.

    Both consumers use ``configured_fallback_names`` for the setting semantics,
    so the utterance wrapper and this last-resort resolver cannot disagree about
    whether fallback is automatic, pinned, or disabled (AP-31).

    Three settings, all of them the user's call:

    * ``auto`` (the default) asks the key-aware resolver for one provider per
      OTHER credential family. Crossing inside a family buys nothing: one dead
      key takes every id that reads it down together (AP-22). It crosses only
      while the configured recognizer is itself a cloud one — see below.
    * a concrete provider id pins the crossover to that provider, even when it
      shares a family with the configured one — the user asked for it by name,
      and refusing a direct instruction because we think it is a poor one is
      how a setting stops being a setting. It is dropped only when it IS the
      configured provider, which would just be the same failure twice.
    * an empty value disables crossing entirely and keeps the honest
      single-provider failure, for anyone who would rather see an error than
      have their audio sent somewhere they did not expect.

    On top of those, one privacy floor that only the ``auto`` branch is subject
    to: when the configured recognizer transcribes on THIS machine, the
    automatic crossover is declined, because every id the key-aware resolver
    can offer is a cloud family (it excludes the local engine by contract) and
    an automatic upload of the raw audio is the one thing an on-device
    recognizer was chosen to prevent. A pinned provider id is untouched by
    this: that is an explicit instruction, and honouring it is the difference
    between a safe default and a policy — the same line the dictation polish
    pass draws for the transcript.

    Never raises: this runs on paths that are already handling a failure, so an
    unimportable plugin package or an unreadable keyring costs the crossover,
    never the transcription. Names only, nothing built (AP-26).
    """
    from jarvis.speech.stt_fallback import configured_fallback_names

    setting = str(getattr(stt_cfg, "fallback", "auto") or "").strip()
    if setting.lower() != "auto":
        return configured_fallback_names(stt_cfg, configured, ())
    if _stt_crossover_would_leave_the_machine():
        log.info(
            "STT crossover declined: [stt].provider = %s transcribes on this "
            "machine, and every provider the automatic chain can offer is a "
            "cloud family — so the fallback would be the first thing to send "
            "this audio off the host. The transcription degrades to the "
            "configured recognizer alone; set [stt].fallback to a provider id "
            "to allow the crossing deliberately.",
            configured or "the configured recognizer",
        )
        return ()
    try:
        from jarvis.plugins.stt import resolve_keyed_stt_fallback

        return configured_fallback_names(
            stt_cfg,
            configured,
            resolve_keyed_stt_fallback(configured),
        )
    except Exception as exc:  # noqa: BLE001 — no chain is worse than no transcript
        log.warning(
            "STT crossover chain could not be resolved (%s); this transcription "
            "degrades to the configured provider alone.",
            exc,
        )
        return ()


def _stt_retry_delay(exc: BaseException | None, attempt: int) -> float:
    """Backoff before the next final-STT attempt.

    Honours the delay the SERVER asked for when there is one, otherwise capped
    exponential backoff. Always within ``[0, cap]``.

    ``STTHTTPError`` already parsed the header into ``retry_after`` seconds, and
    it understands BOTH forms RFC 9110 allows — the delta-seconds one every
    provider sends directly and the HTTP-date one a CDN or gateway in front of
    one sends. Reading that attribute first is therefore not a shortcut: parsing
    the raw header here only ever understood the delta form, so a date-form
    header silently fell through to blind backoff. The header read stays as the
    fallback for the providers that raise a plain ``httpx.HTTPStatusError``.
    """
    requested = getattr(exc, "retry_after", None)
    if isinstance(requested, (int, float)) and not isinstance(requested, bool):
        return min(_STT_RETRY_CAP_S, max(0.0, float(requested)))
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


_SESSION_START_BUFFER_MAX_BYTES = 16_000 * 2 * 30  # 30 s of mono PCM16
_REALTIME_POST_OUTPUT_ECHO_GUARD_S = 0.5
_REALTIME_OUTPUT_LATENCY_CAP_S = 5.0
# The post-output echo tail splits into two phases. Only its leading part is
# physically audible speaker audio (the device's reported output latency plus
# an acoustic-decay/capture-buffering margin); microphone frames captured there
# are genuine echo territory and stay detector-only. Frames captured in the
# REMAINDER of the tail are silent-room or user speech: they are buffered and
# forwarded to the provider when the tail expires instead of being dropped —
# dropping them was the "the first 1-2 s after Jarvis stops talking are deaf"
# defect (a user answering immediately lost the whole utterance onset unless
# it survived the strict local barge confirm).
_REALTIME_HW_ECHO_TAIL_MARGIN_S = 0.25
# Bound on buffered tail audio; the tail itself is capped at
# _REALTIME_POST_OUTPUT_ECHO_GUARD_S + _REALTIME_OUTPUT_LATENCY_CAP_S (~5.5 s
# of 16 kHz mono PCM16 ≈ 176 kB), so this never truncates in practice.
_REALTIME_TAIL_PENDING_MAX_BYTES = 256_000
# How long the microphone must stay quiet after a turn is committed before
# speaking into the silent THINKING wait counts as an interruption rather than
# the tail of the utterance that started it. The provider commits on ITS OWN
# VAD, mid-sentence when a filler pause fools it, so the first moments after a
# commit are exactly when the user is most likely to still be talking (the turn
# fragmentation the session's own _user_is_speaking hold answers). Long enough
# to clear that overlap, short enough that a deliberate interruption a beat
# after Jarvis goes quiet still lands.
_THINKING_BARGE_QUIET_S = 0.35


def _feed_live_mic_level(chunk: AudioChunk) -> None:
    """Publish one captured frame's RMS to the native overlay level channel."""
    if not mic_level.has_subscribers():
        return
    samples = pcm_bytes_to_np(chunk.pcm)
    if samples.size:
        mic_level.feed(float(np.sqrt(np.mean(np.square(samples)))))


class _ChunkSource(Protocol):
    """Anything one lane can drain audio frames from.

    Satisfied by both ``MicrophoneCapture`` (a lane that opened the device
    itself) and ``_SessionInputBuffer`` (a lane that took over a stream some
    other lane already had open). Having the two behind one shape is what lets
    the dictation lane accept either without a second native input stream.
    """

    def stream(self) -> AsyncIterator[AudioChunk]: ...


# How long a dictation waits for an ambiguous wake-microphone lease to end
# before it gives up. Only reachable if the handoff itself failed; the honest
# outcome is a refused dictation, never a second stream on the same device.
_DICTATION_WAKE_RELEASE_TIMEOUT_S = 3.0

# Longest gap between two "dictation key is down" reports that still counts as
# ONE hold. The polling hotkey backend re-reports a held chord every few tens of
# milliseconds, and the edge-driven backends (pynput / Quartz) report a press
# exactly once per real chord-down — so nothing legitimate lands anywhere near
# this window, while a press past it can only be a fresh press or a key-up that
# was never delivered. See ``SpeechPipeline._on_dictate_press``.
_DICTATE_HOLD_REPEAT_GRACE_S = 2.0

# Teardown of a voice session's child tasks (the microphone pump, the provider
# wait, the hangup waiter): how long each one is re-cancelled before the
# teardown abandons it and moves on (BUG-185). On Python 3.11 a cancel can be
# swallowed by ``asyncio.wait_for`` (CPython gh-86296); a bare ``await task``
# after ONE cancel then waits forever, which is exactly how the pipeline stood
# stuck in its teardown for an entire afternoon with the microphone deaf. The
# budget is short on purpose — the dictation handover gives a session
# ``_DICTATION_HANDOVER_TIMEOUT_S`` to release the device — and the heartbeat
# doubles as the timer that keeps a cancel from being lost on the Windows
# proactor loop (BUG-081).
_SESSION_TASK_REAP_BUDGET_S = 3.0
_SESSION_TASK_REAP_HEARTBEAT_S = 0.5

# A hangup request older than this with the session still active is not "one
# already pending" — it is a teardown that has not completed, and the next
# explicit stop gesture must say so instead of being ignored at debug level.
_HANGUP_LATCH_STALE_S = 10.0

# The voice hold key (push-to-talk) has the same physics and the same lost
# key-up failure mode as the dictation hold; sharing the window keeps the two
# self-healing latches from drifting apart. See ``SpeechPipeline._on_ptt_press``.
_PTT_HOLD_REPEAT_GRACE_S = _DICTATE_HOLD_REPEAT_GRACE_S

# How long an explicit dictation key press waits for a live voice conversation
# to give the microphone back before it gives up and says so.
#
# The press WINS over the session (see ``_begin_dictation_handover``), but the
# wait has to be bounded: a teardown that wedges must not leave the shortcut
# permanently dead — the very failure mode this whole lane exists to remove. A
# hangup stops the player immediately and unwinds through the normal session
# teardown, which is itself bounded, so anything past this window is a wedge and
# an honest refusal beats an endless spinner.
_DICTATION_HANDOVER_TIMEOUT_S = 5.0

# Poll interval while waiting for that handover. There is no single event that
# means "the session released the input device" — the state machine reaches IDLE
# only after its capture context has exited — so the wait watches that state.
# Small enough to be imperceptible, large enough not to spin the loop.
_DICTATION_HANDOVER_POLL_S = 0.02

# How many times the FINAL dictation transcription is attempted, and how long it
# waits between attempts. Only the final call is retried: every earlier one is a
# probe tick that gets another chance a second later anyway, while this one is
# the last thing that ever sees the audio — the buffer is gone once the session
# returns. Three attempts over ~1.2 s covers a transient 429 / socket reset
# without making a genuinely dead provider feel like a hang.
_DICTATION_FINAL_ATTEMPTS = 3
_DICTATION_FINAL_RETRY_DELAY_S = 0.6

# Fallback recording ceiling for a MISSING or unparseable ``[dictation]
# max_seconds``. A configured 0.0 is not missing — it is the user asking for no
# ceiling at all — and is preserved rather than replaced by this.
_DICTATION_DEFAULT_MAX_S = 1800.0

# The hold-key watchdog (BUG-191). While a HOLD-started dictation records, the
# hotkey backend is asked whether the chord is still physically down. The
# press/release EDGES can be lost between the OS poller and this pipeline — a
# checker restart mid-hold, a handler that raised, a registry re-arm — and a
# lane that only ever hears edges then records until its 30-minute cap with
# nothing the user presses able to stop it. The keyboard itself cannot go
# stale: a chord that reads "up" for this long, with no release edge having
# arrived, IS the release, and the recording is finished the way a release
# would finish it. One second is far longer than any poll jitter and far
# shorter than a user notices; the poll is cheap (one ``GetAsyncKeyState``
# sweep). A backend that cannot see the keyboard answers ``None`` and the
# watchdog stands down — it never invents a release.
_DICTATE_HOLD_LOST_RELEASE_S = 1.0
_DICTATE_HOLD_WATCH_POLL_S = 0.25

# How long the wake word stays blocked when the recording itself is unbounded.
# The block exists so a wedged dictation task cannot leave the wake word deaf
# until the app is restarted (BUG-037), so it may never be unbounded even when
# the recording is. An hour is far past any real dictation while still being a
# deadline that arrives.
_DICTATION_UNBOUNDED_WAKE_BLOCK_S = 3600.0

# Ceiling on ONE final retry wait. The final pass runs while the user is already
# waiting for their text, so it may not inherit the probe's patient backoff.
_DICTATION_FINAL_RETRY_MAX_S = 2.0

# Per-call ceiling on ONE dictation transcription, derived from the audio being
# sent. Nothing bounded these calls before, which is only survivable while every
# provider misbehaves in the same polite way: google-genai forces ``timeout=None``
# onto its own HTTP client AND runs the request in an uncancellable thread, so a
# Gemini user whose call never came back had the dictation lane wedged after the
# microphone had already closed — no text, no error, no end.
#
# The bound SCALES with the piece rather than being a constant, because the same
# helper transcribes an 8 s segment and, in the unsegmented legacy mode, a
# recording that may be minutes long: one fixed number is either uselessly large
# for the first or a guaranteed truncation for the second. The multiplier allows
# comfortably worse than real time (a CPU-only local Whisper is the slow case;
# every cloud provider returns in a fraction of it), so the ceiling can only ever
# be reached by something that is genuinely stuck.
_DICTATION_TRANSCRIBE_TIMEOUT_PER_AUDIO_S = 2.0

# A CUDA model load + first decode measured ~11 s on the reference desktop.
# Joining longer than this means the post-ready warmer itself is unhealthy;
# abandon its whole provider instance rather than racing its native session.
_DICTATION_WARMUP_JOIN_TIMEOUT_S = 20.0

# ...unless the provider says it is still BUILDING its native engine. That is
# not an unhealthy warmer, it is an honest cold start: a large local checkpoint
# on a cold file cache measured 90+ s (live forensic 2026-08-24, whisper-large-v3
# on CUDA). Replacing the instance there does not shorten the wait — it throws
# the half-built engine away and starts the same load from zero, so the user
# waits twice and the first dictation comes back empty. While the engine reports
# progress we keep waiting, up to this hard ceiling; only silence past it is a
# genuine wedge worth replacing (AP-24).
_DICTATION_WARMUP_COLD_LOAD_TIMEOUT_S = 180.0


def _dictation_retry_worthwhile(exc: BaseException | None) -> bool:
    """Whether re-sending the SAME audio to this provider could work.

    The dictation lane used to retry every failure three times, 0.6 s apart —
    a dead key exactly as eagerly as a rate limit. That is wrong in both
    directions: it hammers a provider that has already said "no, and not later"
    (401 bad key, 402 out of credit, 400 unusable audio) while making the user
    wait ~1.8 s to be told something the first answer already said, and it
    re-fires into a rate-limit window far too early to be inside it.

    A transient HTTP status (429 / 5xx) is worth another attempt. So is a
    failure with NO status at all — a dropped socket, a TLS reset, a
    provider-side read timeout — because those are exactly the blips a second
    attempt survives. Everything else is a definitive refusal and stops the
    ladder.

    OUR OWN ceiling is the deliberate exception. Reaching it means this piece
    already got more than twice its own length in wall-clock time and the
    provider produced nothing, which is a wedge, not a blip — and asking again
    would double a wait the user is already sitting through with the microphone
    closed. The audio is kept as a sidecar either way, so "Restore" is a far
    better answer than three ceilings in a row. Note this catches only the
    ``TimeoutError`` raised by our ``wait_for``: a provider's own timeout
    exception is a transport error with no status and is still retried.
    """
    if exc is None:
        return False
    if isinstance(exc, TimeoutError):
        return False
    status = _stt_error_status(exc)
    if status is None:
        return True
    return status in _STT_TRANSIENT_STATUS


def _align_pcm(offset: int) -> int:
    """``offset`` rounded down to a whole int16 sample boundary."""
    return max(0, offset - (offset % 2))


def _capture_counter(source: object, name: str) -> int:
    """A cumulative capture counter, wherever the stream keeps it.

    A dictation that borrows the wake stream sees a handoff buffer whose
    ``capture`` attribute is the microphone; one that opened its own stream
    sees the microphone directly. Either way a missing counter reads as zero —
    an honest degrade on a source that cannot report it.
    """
    for holder in (source, getattr(source, "capture", None)):
        if holder is None:
            continue
        value = getattr(holder, name, None)
        if value is not None:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def _transcript_end_s(transcript: object) -> float | None:
    """Where the provider's transcript ENDS on its own clock, in seconds.

    Read from the ``segments`` a verbose response carries (Groq / OpenAI
    Whisper: ``{"start", "end", "text", …}`` per segment). ``None`` when the
    provider sent none — a Gemini-class answer, a provider predating the
    field — so the caller falls back to the energy-versus-token heuristic.
    Never raises: a malformed segment is treated as absent.
    """
    best: float | None = None
    for segment in getattr(transcript, "segments", ()) or ():
        if not isinstance(segment, dict):
            continue
        try:
            end = float(segment.get("end", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if end > 0.0 and (best is None or end > best):
            best = end
    return best


def resolve_dictation_language(*, pinned: str, reported: str, text: str) -> str:
    """Which language a finished dictation is treated as. Never raises.

    Module-level and public because TWO paths finish a dictation — the live
    delivery in ``_finish_dictation`` and the Restore route's re-transcription —
    and a second copy of this decision is how the same recording would end up
    cleaned under two different rule sets depending on which button produced it.

    Three signals, and the ORDER between them is the whole fix:

    1. **The user's pin wins, always.** It is the one signal a person can set,
       so overruling it would leave them no way to be right.
    2. **Otherwise the TEXT outranks a provider tag that CONTRADICTS it.** The
       cloud Whisper APIs report a language on every request and report it
       confidently when it is wrong: the live 2026-07-28 history has German
       dictations tagged "English". The old gate only consulted the text when
       the tag was UNPARSEABLE, and ``normalize_language_tag`` turns "English"
       into "en", never "unknown" — so a confidently wrong tag always won. What
       that costs is not cosmetic: the English filler list used to contain "er",
       a top-frequency German pronoun, so a German sentence tagged English came
       back with its pronouns deleted and the cleanup reported a clean success.
    3. **A tag we cannot place stays exactly as it is.** ``detect_text_language``
       only knows de/en/es, so letting it overrule a "French" or "ja" tag would
       relabel that dictation as whichever of the three it scored highest on and
       then run THAT language's filler rules over it. Keeping the tag makes the
       cleanup a documented no-op (``reason="no_rules"``), which is the honest
       answer for ~95 of the 100 recognition languages this feature supports.

    **The answer is a CODE whenever one exists.** A tag we can place comes back
    as ``de`` / ``en`` / ``es``, never as the provider's own spelling of it.
    This is not cosmetic. The cloud Whisper APIs answer with the language NAME
    ("German"), every consumer downstream keys off this string, and the polish
    pass's rare-token guard — the one that stops a formatter from replacing
    words the user actually said — looks its vocabulary up by two-letter code
    and silently disables itself on anything else. Returning the raw tag
    therefore armed that guard only when the provider had been WRONG (the
    detector then supplied a real code) and disarmed it on every dictation the
    provider got RIGHT, which is the overwhelming majority. Live evidence:
    "kannst du es bitte mein download video abspeichern" was delivered as
    "Kannst du es bitte herunterladen?" — three content words replaced, no
    guard fired.  # i18n-allow: verbatim German transcript under test
    A tag with no code stays verbatim, so the honest no-op in point 3 is
    unchanged.

    A provider that reports nothing at all is the one case where the text is the
    only signal there is: ``[dictation].language = auto`` (the shipped default)
    plus a silent provider used to leave the cleanup with "no rules for this
    language", so every hesitation sound the user made shipped straight into the
    text while the setting said the cleanup was on.

    Everything resolves through the canonical resolver's helpers — no layer
    invents its own detection (AGENTS.md §1).
    """
    pin = str(pinned or "").strip().lower()
    if pin not in ("", "auto"):
        return pin
    tag = str(reported or "")
    try:
        from jarvis.core.turn_language import resolve_transcript_language

        # Points 2 and 3 above live in the canonical resolver, which the voice
        # lane's transcript filter reads too — one decision, not two copies
        # that drift the first time either is touched (AGENTS.md §1).
        code = resolve_transcript_language(tag, text)
        if code != "unknown":
            if code != normalize_language_tag(tag):
                log.debug(
                    "dictation language resolved from the transcript: %s "
                    "(the provider reported %r)",
                    code,
                    tag,
                )
            # The tag stands as a CODE whenever it has one. Reaching that case
            # is the NORMAL one (the provider agreed with the text), which is
            # exactly why handing the provider's own spelling on from here
            # disarmed the guards on nearly every dictation.
            return code
    except Exception:  # noqa: BLE001 — a detection hiccup is not fatal
        log.debug("dictation language detection failed", exc_info=True)
    return tag


#: How sure the on-device detector must be before its reading is allowed to pin
#: the rest of a session. The engine answers ~1.0 on a few seconds of clear
#: speech and drops sharply on noise or silence, so this rejects the readings
#: that would pin the WRONG language while costing nothing on real speech.
_RECOGNITION_PIN_MIN_PROBABILITY = 0.6

#: The same question asked of a reading that CONTRADICTS the language already
#: steering this session. Deliberately stricter than the first-reading gate: the
#: anchor exists to stop a short clip redirecting a session, and an escape hatch
#: that opens as easily as the first reading would hand that back.
_RECOGNITION_SWITCH_MIN_PROBABILITY = 0.85

#: Consecutive contradicting readings needed before the session actually
#: switches. One confident-but-wrong reading is ordinary; two in a row on
#: different stretches of audio is a speaker.
_RECOGNITION_SWITCH_STREAK = 2


def accept_recognition_correction(
    *, current: str, language: str, probability: float, streak: int
) -> bool:
    """Whether an on-device reading may OVERRULE the session's language.

    This is the missing half of :func:`accept_recognition_reading`, and its
    absence was a trap that closed and never reopened. The session language is
    seeded from the stored history, so on any dictation after the first it is
    already set — and the only code that accepted an audio reading ran under
    ``if not session_language``. A session that started on the wrong language
    therefore could not be told, by anything, that it was wrong.

    What made it self-sustaining rather than merely wrong is the loop it closed.
    A recogniser handed ``language="en"`` does not mislabel German audio, it
    TRANSLATES it, so the result really is English; that English is stored as an
    ``en`` row; the anchor reads those rows and answers ``en`` for the next
    dictation. Measured on the live history: 13 consecutive dictations over
    roughly two hours (2026-07-29 15:20-17:18) came back as fluent English from
    a speaker talking German throughout, and nothing in the run could break out
    of it.

    So a contradicting reading is allowed to win, but has to earn it — a higher
    confidence than a first reading needs, sustained over
    :data:`_RECOGNITION_SWITCH_STREAK` consecutive readings, so one confident
    mistake on a noisy stretch cannot flip a correct session.
    """
    code = str(language or "").strip().lower()
    if not code or code in ("auto", "unknown", "und", "nn"):
        return False
    if code == str(current or "").strip().lower():
        return False
    try:
        if float(probability) < _RECOGNITION_SWITCH_MIN_PROBABILITY:
            return False
    except (TypeError, ValueError):
        # Same reasoning as accept_recognition_reading: an engine reporting no
        # usable confidence is saying "not sure", and refusing IS the handling.
        return False
    return int(streak) >= _RECOGNITION_SWITCH_STREAK


def resolve_recognition_language(*, pinned: str, session_language: str) -> str:
    """Which language to ASK the provider for on the next piece of audio.

    Distinct from :func:`resolve_dictation_language`, which labels a finished
    dictation. This one runs BEFORE a transcription and decides what the
    recogniser is told, which is a different question with a different failure:

    ``auto`` reaches a provider as "no language field", i.e. "detect it
    yourself". Whisper detects from the audio it is given, and a dictation is
    uploaded in ~4 s segments — far too little for a confident reading. On a
    short segment the model does not merely mislabel the language, it
    TRANSLATES: the same German recording came back verbatim when posted whole
    and as fluent English when posted in segments, and re-running one segment
    flipped between the two (measured against openai/whisper-large-v3 through
    OpenRouter, 2026-07-29). Nothing downstream can undo that — a translated
    sentence IS English to every text-based detector — so the repair has to
    happen before the call.

    The fix is to stop asking twice. Once a session has an AUDIO-derived
    reading of what is being spoken, every later piece is told that language
    explicitly instead of gambling on four seconds of context. Auto-detect is
    preserved where it belongs: the session still starts on ``auto``, and the
    reading is renewed from the audio as the session runs, so switching
    language mid-dictation still lands (the bilingual mandate — a static pin
    was the 2026-06-14 bug and is not what this restores).

    Precedence, and the order is the point:

    1. **A user's pin wins.** It is the one signal a person can set.
    2. **Otherwise this session's own reading**, when there is one.
    3. **Otherwise ``auto``** — a provider that detects well on the audio it
       has is not made worse by being asked to.
    """
    pin = str(pinned or "").strip().lower()
    if pin and pin != "auto":
        return pin
    session = str(session_language or "").strip().lower()
    return session or "auto"


def accept_recognition_reading(*, language: str, probability: float) -> str:
    """The session language an on-device reading justifies; ``""`` for none.

    Gate-keeps :func:`resolve_recognition_language`'s second precedence step.
    A reading only earns the right to steer later segments when the detector
    was actually sure, because the cost of accepting a bad one is a whole
    dictation pinned to a language nobody spoke.
    """
    code = str(language or "").strip().lower()
    if not code or code in ("auto", "unknown", "und", "nn"):
        return ""
    try:
        if float(probability) < _RECOGNITION_PIN_MIN_PROBABILITY:
            return ""
    except (TypeError, ValueError):
        # Deliberately quiet: a provider that reports no usable confidence is
        # answering "I am not sure", which is exactly the case this gate exists
        # to reject. Refusing the reading IS the handling, and logging it would
        # fire once per segment on every provider that omits the field.
        return ""
    return code


# After this many consecutive pieces have exhausted every attempt, the provider
# is not flaky, it is down — and continuing to retry each remaining piece would
# make a 300 s dictation spend minutes proving it. Stop and say so: the audio is
# kept as a sidecar, so "Restore" can transcribe it again once the provider is
# back, which is a far better answer than a silent minute of waiting.
_DICTATION_FINAL_DEAD_PIECES = 2

# The floor a final-pass transcript must reach, in spoken tokens per second of
# VOICED audio, before it is believed to cover its window. Even slow, careful
# dictation runs well above one word per second while actually speaking (pauses
# are excluded — only the voiced runs count), so a window that comes back below
# this is missing speech, not hearing a slow speaker. gpt-4o-class recognizers
# have a reproducible failure mode behind it: audio with a sustained
# mid-recording pause is transcribed up to the pause and everything after it is
# silently dropped (live 2026-07-31 — an 11.6 s recording with a breath after
# the first sentence came back as that sentence alone, twice in a row). The
# verdict is judged on ENERGY versus token count, never on what the text says
# (AP-27's lesson); the repair re-reads the window split at its pauses and the
# longer reading wins.
#
# Raised from 1.0 after measuring what it actually caught: across 797 live
# dictations the repair fired ONCE. Real speech in that sample runs at a median
# 2.5 tokens per second of WHOLE recording — pauses included, so the voiced rate
# is higher still. A floor of 1.0 therefore demanded that a recognizer swallow
# some 60 % of a window before anyone even looked, which is why every ordinary
# truncation (a dropped last sentence is a fifth of the text, not two thirds)
# went unexamined.
#
# 1.3 is where the two costs cross. Tokens-per-WHOLE-second is a lower bound on
# tokens-per-voiced-second, so the share of the sample under a threshold bounds
# the re-read rate from above: at most 12.9 % here, against 7.8 % at the old
# floor, and a false positive only ever costs one extra read whose result is
# discarded unless it carries MORE speech. In words per minute of ACTUAL speech
# — every pause already excluded — the floor now sits at 78 wpm, against the
# 150 wpm median of the sample. Anything under that is worth one confirming
# read (``test_a_healthy_window_is_not_reread`` pins the healthy side).
_DICTATION_TRUNCATION_TOKENS_PER_VOICED_S = 1.3

# How much audio the CAPTURE may lose before the dictation says so. Frames the
# queue dropped are not a transcription failure — they never became audio at
# all, so no retry, no re-read and no Restore can bring them back; the only
# honest thing left is to tell the user their recording has a hole in it.
#
# The floor is a WORD. At the measured 2.5 tokens per second of speech one word
# is ~400 ms, so anything under half a second cannot have taken a whole one and
# saying "words are missing" would be the wrong kind of true. Across the same
# 797 live dictations this marks 10.8 % of them — every one a real loss, and
# every one previously delivered as if it were complete.
_DICTATION_DROPPED_AUDIO_NOTICE_S = 0.5

# How far the live probe backs off after a failed transcription, and the ceiling
# it backs off to. A provider refusing calls — the everyday case is a rate limit,
# HTTP 429 — used to be answered by asking again at the same interval a second
# later, which is what turns a brief limit into a session-long one. Worse, every
# refused call leaves its segment open, so the open tail keeps growing and each
# retry uploads MORE audio than the last: the loop digs its own hole. Backing off
# lets the limit expire while the recording itself continues untouched.
_DICTATION_ERROR_BACKOFF_MIN_S = 1.5
_DICTATION_ERROR_BACKOFF_MAX_S = 12.0

# Queue depth for a dictation that opens its own microphone. The capture queue
# drops the OLDEST chunk when it overflows — correct for wake/VAD, where stale
# audio is worse than missing audio, and exactly wrong here: for a dictation
# every frame is the user's words. ~10 s of slack absorbs a provider stall
# without deleting speech. Express it as time so capture-block tuning never
# silently shrinks the safety margin.
_DICTATION_CAPTURE_QUEUE_CHUNKS = capture_chunks_for_duration(10.0)

# How many final-pass windows may be in flight at once on a provider that
# advertises ``supports_concurrent_requests`` (the cloud HTTP ones). The final
# pass used to read its windows strictly one after another, so the wait after
# key release grew with the recording: measured on the live history, 2.2 s
# median for a 25-50 s dictation and 4.6 s past 50 s, against 0.66 s for a
# short one. Three keeps well inside every provider's per-minute limit while
# the windows already finalized during the recording (see
# ``_prefetch_final_windows``) mean there is rarely more than one left to read
# at release anyway. A native engine never sees concurrency: the gate is one
# wide unless the provider says otherwise (AP-24).
_DICTATION_CONCURRENT_READS = 3

# When a provider's transcript carries segment timestamps, a window whose
# transcript ENDS this many seconds before its speech does has had its tail
# dropped — the recognizer stopped early and said nothing about it. The tail
# alone is then re-read from half a second before the transcript's end, so the
# head the provider got right is kept verbatim and only the missing part is
# asked for again. Judged on the transcript's own clock against the window's
# ENERGY (where the speech really ends), never on what the words say (AP-27).
# One and a half seconds of speech is three or four words — a loss worth a
# request — while the ~0.5-1 s a recognizer's last timestamp ordinarily trails
# the true end of speech stays well inside it.
_DICTATION_TAIL_DROP_MIN_S = 1.5
_DICTATION_TAIL_REREAD_BACK_S = 0.5

# How long the release waits for the incremental polish worker to finish the
# windows it already has — normally the last one, whose formatting started the
# moment it was read. Past that the worker is retired and its completed prefix
# is retained, leaving only unfinished text for the bounded final polish pass.
_DICTATION_PREFIX_POLISH_WAIT_S = 3.0

# Dictation notifications update UI state; they must not inherit the bus's
# five-second observer timeout before a finished transcript can be delivered.
_DICTATION_EVENT_TIMEOUT_S = 1.0
_DICTATION_POLISH_CANCEL_WAIT_S = 0.25


class _SessionInputBuffer:
    """Replayable bounded handoff for one continuously captured mic stream.

    Capture begins before ``VoiceSessionStarted`` is published, so the Jarvis
    Bar never advertises LISTENING ahead of the microphone. Frames arriving
    while start subscribers, realtime session assembly, or a provider handshake
    run are retained in order. Each new consumer starts
    at sequence zero, allowing classic STT to replay the opening if a realtime
    provider fails before accepting it. The byte cap is time-format based; if a
    consumer ever falls behind it, the stream fails explicitly instead of
    silently dropping the beginning of the user's command.
    """

    def __init__(
        self,
        *,
        initial: tuple[AudioChunk, ...] = (),
        max_buffer_bytes: int = _SESSION_START_BUFFER_MAX_BYTES,
        capture: MicrophoneCapture | None = None,
    ) -> None:
        #: The still-open stream behind this handoff, when the frames come from
        #: a live microphone rather than a test's hand-fed list. Carried purely
        #: so a consumer that takes the stream over can re-bound its queue for
        #: the kind of consumer IT is — a dictation records every frame, while
        #: the wake detector this stream was opened for wants shallow and fresh
        #: (``MicrophoneCapture.set_queue_depth``). ``None`` means there is
        #: nothing to re-bound, which every caller treats as fine.
        self.capture = capture
        self._chunks: deque[tuple[int, AudioChunk]] = deque()
        self._max_buffer_bytes = max(1, int(max_buffer_bytes))
        self._retained_bytes = 0
        # Latest timestamp among evicted frames. None means at least one frame
        # had no trustworthy capture timestamp, so skipping a gap is unsafe.
        self._evicted_through_ns: int | None = 0
        self._next_seq = 0
        self._updated = asyncio.Event()
        self._pump_task: asyncio.Task[None] | None = None
        self._error: BaseException | None = None
        self._source_done = False
        self._closed = False
        self._active_consumers = 0
        self.released = asyncio.Event()
        for chunk in initial:
            self._append(chunk, publish_level=False)

    def start(self, source: AsyncIterator[AudioChunk]) -> None:
        if self._pump_task is not None:
            return
        self._pump_task = asyncio.create_task(
            self._pump(source), name="voice-session-mic-buffer"
        )

    def put(self, chunk: AudioChunk) -> None:
        if not self._closed:
            # While no voice engine consumes the stream, this buffer is the
            # only place that can animate the visible startup bar. An active
            # classic/realtime consumer owns level feeding; switching back here
            # between consumers keeps fallback teardown from flattening the bar.
            self._append(chunk, publish_level=self._active_consumers == 0)

    def _append(self, chunk: AudioChunk, *, publish_level: bool) -> None:
        chunk_bytes = len(chunk.pcm)
        while (
            self._chunks
            and self._retained_bytes + chunk_bytes > self._max_buffer_bytes
        ):
            _seq, dropped = self._chunks.popleft()
            self._retained_bytes = max(0, self._retained_bytes - len(dropped.pcm))
            timestamp = int(dropped.timestamp_ns or 0)
            if timestamp <= 0 or self._evicted_through_ns is None:
                self._evicted_through_ns = None
            else:
                self._evicted_through_ns = max(self._evicted_through_ns, timestamp)
        self._chunks.append((self._next_seq, chunk))
        self._retained_bytes += chunk_bytes
        self._next_seq += 1
        if publish_level:
            _feed_live_mic_level(chunk)
        self._updated.set()

    async def _pump(self, source: AsyncIterator[AudioChunk]) -> None:
        try:
            async for chunk in source:
                self.put(chunk)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - replay on the consumer
            self.finish(error=exc)
        finally:
            self.finish()

    def finish(self, *, error: BaseException | None = None) -> None:
        """Mark a manually fed source as terminal and wake its consumers."""
        if error is not None and self._error is None:
            self._error = error
        self._source_done = True
        self._updated.set()

    async def stream(
        self, *, discard_before_ns: Callable[[], int] | None = None
    ) -> AsyncIterator[AudioChunk]:
        self._active_consumers += 1
        cursor = 0
        try:
            while True:
                if self._chunks:
                    earliest = self._chunks[0][0]
                    if cursor < earliest:
                        discard_before = discard_before_ns() if discard_before_ns else 0
                        can_skip_suppressed = (
                            cursor > 0
                            and self._evicted_through_ns is not None
                            and 0 < self._evicted_through_ns < discard_before
                        )
                        if not can_skip_suppressed:
                            raise RuntimeError(
                                "Voice input exceeded the 30-second replay window; "
                                "refusing to drop the command prefix."
                            )
                        # An inline reply pauses VAD while capture keeps running.
                        # Its post-TTS echo guard already rejects these frames;
                        # apply that SAME cutoff before enforcing replay bounds.
                        # New user audio and first-consumer startup are protected.
                        log.debug(
                            "Skipped %d expired voice-input frames after assistant playback.",
                            earliest - cursor,
                        )
                        cursor = earliest
                    offset = cursor - earliest
                    if 0 <= offset < len(self._chunks):
                        _seq, chunk = self._chunks[offset]
                        cursor += 1
                        yield chunk
                        continue
                if self._closed or self._source_done:
                    if self._error is not None:
                        raise self._error
                    return
                self._updated.clear()
                if self._chunks and cursor < self._next_seq:
                    continue
                await self._updated.wait()
        finally:
            self._active_consumers = max(0, self._active_consumers - 1)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._updated.set()
        self.released.set()
        task = self._pump_task
        self._pump_task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@asynccontextmanager
async def _wake_capture_with_release(
    capture: MicrophoneCapture,
    released: asyncio.Event,
) -> AsyncIterator[MicrophoneCapture]:
    """Signal only after the wake microphone has fully closed."""
    released.clear()
    try:
        async with capture as mic:
            yield mic
    finally:
        released.set()


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

# The pipeline's Thinking-pause hold (maintainer directive 2026-08-18: "wait
# for a clear pause; when I keep talking, append"). The VAD already waits the
# configured pause before it ends an utterance — but its final TRANSCRIPT
# arrives a recognizer round-trip later, and by then the user may audibly be
# into the next sentence. Dispatching at that moment answers half a request
# and lets the instant ack talk over the user's second half; the recombine
# window then cancels and re-dispatches the joined text — a wasted brain call
# and an audible stumble. So a complete utterance whose final lands while the
# VAD reports speech again is HELD (``ContinuationBuffer.hold``) and joined
# with the next utterance instead: one dispatch, no interruption. Bounded by
# the drain timer, which defers only while the user holds the floor.
#
# How long a mic-held text waits for its continuation once the floor is free
# again (the next utterance transcribed to nothing, or never came). Short:
# the VAD's own pause has already been paid; this only bridges the gap
# between "the floor is free" and "nothing more is coming".
_MIC_HOLD_DRAIN_S = 1.0
# A VAD false start (a cough, a chair) is a promise of speech that never came:
# release the held text at once — a frame later than the false-start verdict.
_MIC_HOLD_RELEASE_S = 0.15


# Hang-up patterns + the END_CALL sentinel live in jarvis/speech/hangup.py
# (shared, stdlib-only, also imported by jarvis/telephony/session.py). HANGUP_RE,
# contains_end_signal and is_legacy_farewell are imported at the top of this module.

# Latenz-Sprint-1: Satzgrenzen-Splitter fuer den Streaming-TTS-Pfad.
# Matched whitespace/newline DIREKT NACH einem Satzendezeichen — also den
# Uebergang von Satz n nach Satz n+1. Final-Flush am Stream-Ende uebernimmt
# das letzte Fragment ohne folgendes Whitespace.
#
# OF-12: a period plus whitespace is only a CANDIDATE boundary, never a
# boundary by itself. Ordinals ("Am 1. Januar", "El 31. de enero"), spaced
# abbreviations ("z. B.", "e. g.", "p. m.") and titles ("Dr. Meier",
# "Sr. Lopez") all carry a mid-sentence period, and splitting there made TTS
# speak "Am eins." as its own utterance. Every candidate is therefore
# validated by ``_is_stream_sentence_break`` below.
#
# The match deliberately does NOT require a following character. Waiting for
# the first character of the next sentence would hold sentence 1 hostage to
# the brain's next token — and a tool-use turn streams "Ich schaue nach. " and
# then goes quiet for seconds, which is exactly when the user must hear
# something. Every rule that can decide from the text BEFORE the gap decides
# immediately; the following character only refines a candidate when it
# already happens to be in the buffer.
_STREAM_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")

# Tokens whose trailing period is practically NEVER a sentence end. One shared
# set for every locale: the turn language is a hint, not a guarantee, and
# assistant prose mixes locales freely (a German answer quoting an English
# product name). Deliberately NOT listed are abbreviations that usually DO
# close a sentence ("etc.", "usw.") — for those the split is correct and must
# stay. Single-letter tokens need no entry: they are rejected as a class,
# which covers "z. B.", "u. a.", "d. h.", "e. g.", "i. e.", "a. m.", "p. m."
# and initials like "J. R. R.".
_STREAM_ABBREVIATIONS: frozenset[str] = frozenset({
    # Titles / forms of address (de/en/es) — always followed by a name.
    "dr", "prof", "dipl", "ing", "hr", "fr", "st", "sankt",
    "mr", "mrs", "ms", "jr",
    "sr", "sra", "srta", "ud", "uds",
    # Reference markers — always followed by a number or a caption.
    "nr", "num", "núm", "abb", "fig", "tab", "vol", "pág",
    # Qualifier / connector abbreviations — always mid-sentence.
    "bzw", "bspw", "ggf", "evtl", "inkl", "exkl", "zzgl", "vgl", "sog",
    "ca", "approx", "aprox", "mind", "max", "min", "mio", "mrd", "ej", "vs",
})

# Punctuation a new sentence may OPEN with; skipped when looking for the first
# real character behind a candidate boundary.
_STREAM_SENTENCE_OPENERS = "\"'“”„‚‘’«»‹›(<[{¿¡-–—*_#"

# A one- or two-digit run before the period is an ordinal ("Am 1. Januar",
# "El 31. de enero"), not a sentence end. A year ("2026.") is four digits and
# keeps its boundary.
_STREAM_ORDINAL_TAIL_RE = re.compile(r"(?<!\d)\d{1,2}\.$")
# The plain-letter token right before the period, if any.
_STREAM_ABBREV_TAIL_RE = re.compile(r"([^\W\d_]+)\.$")


def _stream_opens_sentence(rest: str) -> bool:
    """True when ``rest`` starts with something that can OPEN a sentence.

    A lowercase letter means the period belonged to an abbreviation or a
    decimal, never to a sentence end. Scripts without case distinction (CJK,
    Arabic) are accepted, otherwise those locales would never split at all.

    ``rest`` is empty while the next token is still in flight. That is NOT a
    rejection: the boundary keeps the behaviour it had before this refinement
    existed, so a pausing brain can never sit on a finished sentence.
    """
    for ch in rest:
        if ch.isspace() or ch in _STREAM_SENTENCE_OPENERS:
            continue
        return ch.isdigit() or (ch.isalpha() and not ch.islower())
    return True


def _is_stream_sentence_break(buffer: str, gap_start: int, gap_end: int) -> bool:
    """Decide whether the whitespace gap ``[gap_start:gap_end)`` ends a sentence.

    ``gap_start - 1`` is the terminator character matched by
    ``_STREAM_SENTENCE_END``. Only the period is ambiguous — "!", "?" and "…"
    have no abbreviation shape and always close a sentence.
    """
    if buffer[gap_start - 1] != ".":
        return True
    head = buffer[:gap_start]
    if _STREAM_ORDINAL_TAIL_RE.search(head):
        return False
    match = _STREAM_ABBREV_TAIL_RE.search(head)
    if match is not None:
        word = match.group(1)
        if len(word) == 1 or word.casefold() in _STREAM_ABBREVIATIONS:
            return False
    return _stream_opens_sentence(buffer[gap_end:])


def _is_whole_text_fallback(original: str, scrubbed: ScrubResult) -> bool:
    """True when the scrub replaced the WHOLE input with the generic phrase.

    ``scrub_for_voice`` is a whole-TURN filter: when one of its guards fires
    (stack trace, raw repr, shell command, post-scrub residue) it throws the
    input away and returns the canned error phrase for ALL of it. Per sentence
    that verdict is wrong, so the streaming path has to recognise it (OF-11).

    ``fallback_used`` is the documented signal; the phrase-table comparison is
    a second, independent check so this stays correct if a future guard
    forgets the flag. An answer that genuinely SAYS the phrase (in markdown,
    say) must never be mistaken for one — so the phrase only counts when the
    original did not carry it in the first place.
    """
    if scrubbed.fallback_used:
        return True
    if not scrubbed.actions:
        return False
    cleaned = scrubbed.cleaned.strip()
    if not cleaned or cleaned not in set(FALLBACK_PHRASES.values()):
        return False
    return cleaned not in original


def _next_stream_sentence_break(buffer: str) -> int | None:
    """Cut index just past the next real sentence boundary in ``buffer``.

    The caller slices ``buffer[:cut]`` as the finished sentence and keeps
    ``buffer[cut:]`` as the rest. ``None`` = no boundary in the buffer yet.
    """
    pos = 0
    while True:
        match = _STREAM_SENTENCE_END.search(buffer, pos)
        if match is None:
            return None
        if _is_stream_sentence_break(buffer, match.start(), match.end()):
            return match.end()
        pos = match.end()


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
# The short-recording + whole-utterance verdict itself lives beside the pattern
# in that same leaf module, because the realtime input recognizer needs exactly
# this judgement too and a second copy would drift (BUG-008).
from jarvis.speech.wake_constants import (  # noqa: E402
    STT_HALLUCINATION_RE as _STT_HALLUCINATION_RE,
)
from jarvis.speech.wake_constants import (  # noqa: E402
    is_silence_hallucination as _is_silence_hallucination,
)

# Paraphrase prefixes Gemini/Claude put in front of an answer when unsure.
# Cut off as post-processing before the TTS call.
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
    for current_word, incoming_word in zip(
        current_words, incoming_words, strict=False
    ):
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
        1
        for current_word, incoming_word in zip(
            suffix, incoming_words, strict=False
        )
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


def _wake_catchup_budget(detector: Any) -> tuple[int, int]:
    """Resolve a wake detector's fanout budgets: (coalesce chunks, queue depth).

    Both are TIME budgets the detector declares in seconds and this turns into
    capture blocks — ``capture_chunks_for_duration`` is the one place that
    knows the block size, so a block-size change can never silently shrink a
    "1 s batch" to 320 ms again (that is exactly what happened when the block
    went from 100 ms to 32 ms under a chunk-count attribute).

    * ``coalesce_catchup_s`` > 0: the detector can consume variable buffer
      sizes, so a backlog may be drained as ONE batch covering that much
      audio (see ``_queue_iter``). Absent/0 → no coalescing (1).
    * ``intake_backlog_s``: how far behind live audio the detector may fall
      before the fanout drops its oldest frame. Honoured ONLY for a detector
      that coalesces — one that cannot catch up must stay near-present on the
      generic ``REALTIME_QUEUE_CHUNKS`` budget, or a deeper queue would only
      mean a later wake. Floored at the generic budget.
    """
    coalesce = 1
    try:
        coalesce_s = float(getattr(detector, "coalesce_catchup_s", 0.0) or 0.0)
    except (TypeError, ValueError):
        coalesce_s = 0.0
    if coalesce_s > 0.0:
        coalesce = max(2, capture_chunks_for_duration(coalesce_s))
    depth = REALTIME_QUEUE_CHUNKS
    if coalesce > 1:
        try:
            backlog_s = float(getattr(detector, "intake_backlog_s", 0.0) or 0.0)
        except (TypeError, ValueError):
            backlog_s = 0.0
        if backlog_s > 0.0:
            depth = max(REALTIME_QUEUE_CHUNKS, capture_chunks_for_duration(backlog_s))
    return coalesce, depth


async def _queue_iter(
    q: asyncio.Queue, *, coalesce_max_chunks: int = 1
) -> AsyncIterator[AudioChunk]:
    """Adapt a queue into an async iterator for wake detectors.

    ``coalesce_max_chunks`` > 1 turns a BACKLOG into catch-up batches: after
    the blocking get, every chunk already waiting in the queue (up to the cap)
    is drained non-blocking and concatenated into ONE AudioChunk. A detector
    that falls behind live audio on a busy desktop CPU (live forensic
    2026-07-21: oww_q pinned at 50 → the wake word was heard FIVE SECONDS
    late) then pays its per-chunk loop/decode overhead once per batch instead
    of once per 100 ms block and catches back up to the present instead of
    grinding through stale audio at the same rate it arrives. When the
    consumer is keeping up the queue is empty and every chunk passes through
    untouched, so the caught-up hot path is byte-identical to before. Only a
    detector that declares the capability (``coalesce_catchup_s``, resolved by
    ``_wake_catchup_budget``) gets batches — a provider with fixed frame-size
    expectations keeps the per-chunk contract.
    """
    while True:
        chunk = await q.get()
        if chunk is None:  # Sentinel
            return
        if coalesce_max_chunks > 1 and not q.empty():
            parts = [chunk.pcm]
            ended = False
            while len(parts) < coalesce_max_chunks:
                try:
                    nxt = q.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if nxt is None:  # Sentinel mid-drain: flush, then end
                    ended = True
                    break
                parts.append(nxt.pcm)
            if len(parts) > 1:
                chunk = AudioChunk(
                    pcm=b"".join(parts),
                    sample_rate=chunk.sample_rate,
                    timestamp_ns=chunk.timestamp_ns,
                    channels=chunk.channels,
                )
            if ended:
                yield chunk
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


def _owns_ambient_duties() -> bool:
    """May this process arm the wake word and the global hotkeys?

    Only the default instance of the app does (``jarvis.core.instance``); a
    second instance started beside it for development must not compete for the
    microphone or the key combos. Lazy import keeps the pipeline's import cost
    unchanged; the lookup is a dict read.
    """
    from jarvis.core.instance import current_instance

    return current_instance().owns_ambient_duties


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
        # Raised from 350 to 1500 ms so natural thinking pauses do not fragment
        # long instructions. Short hang-up commands remain fast because the
        # detector runs before the brain call.
        vad_silence_ms: int = 1500,
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
        # Dictation mode: hold (or toggle) to speak, the transcript is inserted
        # into whatever text field has focus. Empty (the default) means no
        # shortcut is armed — dictation is then started from the bar, the UI or
        # the CLI. Deliberately its OWN binding rather than a revival of the
        # deprecated ptt slot: dictation never reaches the brain.
        dictate_hotkeys: tuple[str, ...] = (),
        dictate_mode: str = "hold",
        # Hands-free dictation: press once to start, press again to stop. Its
        # OWN binding, independent of ``dictate_mode`` — a user may have a hold
        # key and a toggle key armed at the same time, and the legacy
        # ``[dictation].mode = "toggle"`` still only governs ``dictate_hotkeys``.
        dictate_toggle_hotkeys: tuple[str, ...] = (),
        # "Insert the last dictation again" — its own binding, because it needs
        # neither a microphone nor a speech-to-text provider and therefore stays
        # useful on a host where dictation itself cannot run. It was editable in
        # the Voice → Shortcuts tab for a while WITHOUT arriving here: the save
        # persisted, the UI showed the new combo, and the key did nothing —
        # forever, restart included (the AP-4 trap: one layer never told).
        paste_last_hotkeys: tuple[str, ...] = (),
        # Hide / show the desktop pet ([trigger].hotkey_pet_toggle). Dispatched
        # here like every other global shortcut; the pipeline only publishes
        # ``PetVisibilityToggleRequested`` and the overlay bridge does the rest.
        pet_toggle_hotkeys: tuple[str, ...] = (),
        # Resolved DictationConfig (jarvis.core.config.DictationConfig) or None.
        # None keeps every legacy call site and test on the built-in defaults.
        dictation_config: Any = None,
    ) -> None:
        self._call_hotkeys = call_hotkeys
        self._ptt_hotkeys = ptt_hotkeys
        self._hangup_hotkeys = hangup_hotkeys
        self._dictate_hotkeys = list(dictate_hotkeys)
        self._dictate_toggle_hotkeys = list(dictate_toggle_hotkeys)
        self._paste_last_hotkeys = list(paste_last_hotkeys)
        self._pet_toggle_hotkeys = list(pet_toggle_hotkeys)
        # Speaker mute = TTS volume 0. The muted-ness last broadcast as
        # ``VoiceSpeakerMuteChanged``; ``set_tts_volume`` runs on the REST
        # threadpool AND the overlay's Tk thread, so the compare-and-set that
        # decides "did it flip?" holds this lock. ``None`` = nothing broadcast
        # yet, so the first change is compared against the configured volume.
        self._speaker_mute_lock = threading.Lock()
        self._speaker_muted_last: bool | None = None
        # One paste at a time — two overlapping ones race over the clipboard
        # restore and the loser puts the wrong content back (see _on_paste_last).
        self._paste_last_busy = False
        self._dictate_mode = "toggle" if str(dictate_mode).lower() == "toggle" else "hold"
        self._dictation_cfg = dictation_config
        # Push-to-talk runtime state. ``_ptt_mode`` arms the raw-recording path
        # in ``_active_session``; ``_ptt_release_event`` is the up-edge signal
        # that ends the recording. A held key never auto-ends via the VAD — the
        # release (or the safety cap) is the only natural endpoint. The cap
        # guards against a stuck-key / lost-release-edge wedging the mic open.
        self._ptt_mode = False
        self._ptt_release_event = asyncio.Event()
        self._ptt_max_hold_s = 60.0
        # When the PTT chord last reported itself down — the self-healing
        # latch's clock (see ``_on_ptt_press``). 0.0 = never.
        self._ptt_key_seen_at = 0.0
        # A DELIBERATE user activation edge (PTT down, CALL hotkey, the
        # "Speak in this conversation" button) is pending in ``_call_event``.
        # The state loop consumes this to exempt exactly that call from the
        # post-hangup wake lock: the lock exists so Jarvis' own speaker tail
        # cannot re-trigger the WAKE WORD (audio echo), and a key press has no
        # echo path. Dropping explicit presses under the lock made recording
        # start only after the lock expired — the user was already talking and
        # the opening 1-3 s of every quick follow-up were missing (live
        # 2026-07-31).
        self._explicit_call_pending = False
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
        # The short-lived task that ends a live voice conversation so this
        # dictation can have the microphone. Alive only between the key press
        # and the moment ``_dictation_task`` exists (or the handover is refused),
        # and never both at once. See ``_begin_dictation_handover``.
        self._dictation_handover_task: asyncio.Task[None] | None = None
        # A dictation started beside a live call, and whether that call's input
        # is held for it. See ``_live_call_owns_microphone``.
        self._dictation_beside_call = False
        self._voice_input_held = False
        # 0.0 is a real value here — "no ceiling" — so it must NOT be coerced
        # to a default the way an absent or malformed setting is. The old
        # ``or 300.0`` did exactly that and made the off switch unreachable
        # (AP-31: a switch whose value is ignored). Only a missing or
        # unparseable setting falls back.
        raw_max_s = getattr(
            self._dictation_cfg, "max_seconds", _DICTATION_DEFAULT_MAX_S
        )
        try:
            configured_max_s = float(raw_max_s)
        except (TypeError, ValueError):
            # A hand-edited jarvis.toml must never fail to boot (AP-16), but it
            # must also not fail QUIETLY: the user typed something here and is
            # about to get a ceiling they did not ask for.
            log.warning(
                "[dictation].max_seconds is not a number (%r); using %.0fs. "
                "Set 0 for no recording ceiling.",
                raw_max_s,
                _DICTATION_DEFAULT_MAX_S,
            )
            configured_max_s = _DICTATION_DEFAULT_MAX_S
        self._dictation_max_s = max(0.0, configured_max_s)
        # Strong references to fire-and-forget bus publishes (see
        # ``_publish_event_soon``); entries remove themselves when they finish.
        self._detached_publishes: set[asyncio.Task[None]] = set()
        # Deadline that BOUNDS the wake-word block a dictation imposes. 0.0 is
        # "no block", so an instance that never dictates can never go deaf.
        self._dictation_wake_block_until = 0.0
        # Whether the current dictation already published its terminal
        # ``DictationCompleted``. Starts True ("nothing owed") so no teardown
        # can fire a completion for a dictation that never ran.
        self._dictation_completion_published = True
        # Where the finished transcript goes for the CURRENT dictation run:
        # "chat" only publishes the transcript event (the chat composer's mic
        # button), "insert" additionally pastes it into the focused field of
        # whatever app is in front. Set per start_dictation call, never global.
        self._dictation_target = "chat"
        # The dictation lane's OWN transcription provider — built on the first
        # press, never at boot (AP-26), and deliberately not the voice one: see
        # ``_dictation_stt`` for why the voice bias prompt must not reach a
        # dictation. ``None`` means "not built yet", which is also how a live
        # provider/language switch invalidates it.
        self._dictation_stt_instance: Any = None
        # The VOICE lane's cross-family fallback (AP-22), and the reason both
        # halves are ``None``/empty here rather than resolved at boot: this is
        # last-resort machinery for a turn that has ALREADY failed, and a
        # working provider must never pay for it. Nothing is resolved until a
        # final transcription has exhausted its retry ladder, nothing is built
        # until the resolved chain is actually walked, and both are then kept so
        # a provider that stays down does not re-read the keyring every turn.
        # ``None`` means "not resolved yet"; an empty tuple means "resolved, and
        # this host has one keyed family" — the two must stay distinguishable or
        # a single-key install re-resolves on every failed turn.
        self._voice_stt_fallback_chain: tuple[str, ...] | None = None
        self._voice_stt_fallback_instances: dict[str, Any] = {}
        # True while a hold-mode dictation key is physically down, as far as
        # the press/release EDGES have told us. Mirrors ``_ptt_mode`` for the
        # dictation lane. (The Windows poller fires on_press ONCE per chord-down
        # — ``HotkeyChecker.run`` gates it on ``key_state != 1`` — so this is an
        # edge latch, not a heartbeat; the physical truth is asked separately
        # through ``HotkeyTrigger.chord_is_down`` while a hold records.)
        self._dictate_key_down = False
        # Monotonic time of the last press edge that reported the key as down.
        # It is what makes the latch above self-healing: see
        # ``_on_dictate_press`` and ``_DICTATE_HOLD_REPEAT_GRACE_S``.
        self._dictate_key_seen_at = 0.0
        # The live ``HotkeyTrigger`` while the pipeline runs — the hold-key
        # watchdog asks it whether the dictation chord is physically down.
        self._hotkey_trigger: Any = None
        # Which door the running dictation came through (``hold_key``,
        # ``toggle_key``, ``ws``, ``rest``, ``api``). Logged on start so the
        # next "it never stopped" report can be read, and it decides whether the
        # hold-key watchdog applies: only a hold-started recording is owed a
        # release edge.
        self._dictation_started_by = ""
        # Set by ``request_dictation_stop(discard=True)`` — the bar's close-X.
        # The recording ends through the ordinary stop event, and this flag
        # makes the session treat it as a hangup: nothing transcribed, nothing
        # delivered. Reset at every commit.
        self._dictation_discard_requested = False
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
        # Runtime fallback (AP-22). ``build_stt_from_config`` only crosses to
        # another provider when the configured one has no KEY — a build-time
        # decision. A provider that fails at CALL time (a 429 mid-dictation, an
        # expired key, a 503) had no way out, so one rate limit deleted the
        # user's words while three other keyed providers sat unused. The chain
        # is names-only until something actually fails, so this costs the boot
        # path nothing (AP-26).
        #
        # Skipped entirely for an on-device recognizer, and this is the SAME
        # privacy floor ``_resolve_stt_fallback_chain`` applies: every alternate
        # ``alternate_provider_names`` can offer a locally-configured user is a
        # cloud one (it appends the local engine only when it is NOT the
        # configured provider), so this wrapper is the earliest and widest of
        # the automatic doors out of the machine — it sits on the utterance
        # provider every voice turn uses. Someone who chose an on-device
        # recognizer chose it so their recordings would stay here; an automatic
        # upload on the first 429 is not a fallback they agreed to. Pinning
        # ``[stt].fallback`` to a provider id still crosses, through the
        # resolver above.
        if (
            config is not None
            and getattr(config, "stt", None) is not None
            and not _stt_crossover_would_leave_the_machine()
        ):
            try:
                from jarvis.speech.stt_fallback import wrap_stt_with_fallback

                self._utterance_stt = wrap_stt_with_fallback(
                    self._utterance_stt, config.stt
                )
            except Exception as exc:  # noqa: BLE001 — never block voice boot
                log.warning("STT fallback chain unavailable: %s", exc)
        # Live transcript preview uses the cheap local probe when available.
        # In lightweight mode there is no local Whisper, but the post-wake
        # utterance STT may still exist (cloud provider). Keep that path alive
        # so the Listening bubble does not stay stuck on "...".
        self._probe_stt: Any = self._stt or self._utterance_stt
        # User STT dictionary (dictation-tool-style custom vocabulary): wrap the
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
        # Keep the user's activation preference separate from the detector
        # selected by the current wake plan. A phrase/model can change while
        # always-on listening is disabled; that must update the prepared plan
        # without silently opening the microphone. Older/duck-typed callers
        # without a TriggerConfig keep the legacy behaviour where a later
        # set_wake_plan() arms the resolved detector.
        trigger_cfg = getattr(config, "trigger", None)
        configured_wake_enabled = getattr(trigger_cfg, "wake_word_enabled", None)
        self._wake_word_enabled: bool | None = (
            bool(configured_wake_enabled)
            if configured_wake_enabled is not None
            else None
        )
        # A non-default instance of the desktop app (``jarvis.core.instance``,
        # the "dev" app beside the live one) never listens for the wake word
        # and never arms a global hotkey — the microphone and the key combos
        # belong to the default app; two listeners would both answer. The
        # in-window mic button and the chat still work, so voice can be tested.
        if not _owns_ambient_duties():
            self._wake_word_enabled = False
            enable_openwakeword = False
            enable_whisper_wake = False
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
                model_paths=getattr(wake_plan, "vosk_model_paths", ()),
                keyword=wake_plan.oww_keyword,
                early_candidate_listener=self._vosk_early_candidate_listener,
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
        self._base_brain: BrainCallback = brain_callback or _echo_brain
        self._brain: BrainCallback = self._base_brain
        # This profile owns only conversational text generation. The mature
        # cross-platform STT, sentence-TTS, playback receipts, follow-up
        # listening and barge-in machinery below stays the single audio path.
        if config is not None:
            from jarvis.voice.subscription_profile import (  # noqa: PLC0415
                CodexSubscriptionVoiceBrain,
                subscription_voice_selected,
            )

            if subscription_voice_selected(config):
                self._brain = CodexSubscriptionVoiceBrain(self._brain, config)
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
            # 0 is the config's "automatic" setting: every voice engine keeps
            # its own factory timing, and for a local VAD that IS this
            # pipeline's built-in window — a local endpointer has no vendor
            # default to inherit, so "automatic" resolves to the 1.5 s rule.
            silence_ms=_local_silence_window_ms(vad_silence_ms),
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
        # Strong reference to the one live preview request. At an endpoint the
        # request is cancelled and drained before final STT starts, preventing a
        # stale preview and the final upload from competing for one connection,
        # one native inference lock, or one provider rate-limit slot.
        self._probe_task: asyncio.Task[None] | None = None
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
        bind_shared_audio_player(self._player)
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
        self._assistant_work_count = 0
        self._termination_producer = ""
        self._termination_detail: dict[str, Any] = {}
        self._idle_deadline_monotonic: float | None = None
        self._previous_turn_state: str | None = None
        self._post_tts_listen_suppression_s = post_tts_listen_suppression_s
        self._input_suppressed_until_ns: int = 0
        self._continue_listening_after_response = continue_listening_after_response
        self._session_end_reason: str | None = None
        self._ack_phrase = ack_phrase

        self._state = PipelineState.IDLE
        self._call_event = asyncio.Event()
        self._hangup_event = asyncio.Event()
        # Wake and session capture use the same physical input device. A wake
        # stream must close before the session stream opens, while frames heard
        # after the visible wake candidate are retained for the session.
        self._wake_capture_released = asyncio.Event()
        self._wake_capture_released.set()
        self._wake_stop_event = asyncio.Event()
        self._wake_cancel_event = asyncio.Event()
        self._wake_handoff_ready = asyncio.Event()
        self._wake_handoff_buffer: _SessionInputBuffer | None = None
        self._wake_preroll_active = False
        self._wake_preroll_confirmed = False
        # Candidate verification is bounded; retain its short post-reveal
        # window in full so the first spoken word can never fall off a
        # chunk-count deque before the live handoff is accepted.
        self._pending_session_preroll: deque[AudioChunk] = deque()
        # ``request_hangup`` is also called by the JarvisBar's dedicated Tk
        # thread. asyncio primitives are not thread-safe, so ``run()`` records
        # their owning loop and external callers marshal the hangup onto it.
        self._runtime_loop: asyncio.AbstractEventLoop | None = None
        # A thread-safe edge latch keeps repeated X clicks from queueing the
        # expensive hard-stop path more than once during the same teardown.
        self._external_hangup_pending = threading.Event()
        # Distinguish a close that arrived after a wake stream was offered from
        # an older no-op hangup while truly idle. Both otherwise share the same
        # asyncio hangup event when the state loop begins.
        self._wake_handoff_hangup_pending = threading.Event()
        self._current_voice_session_id: str | None = None
        # Configured voice mode and effective in-flight engine are deliberately
        # separate. A settings write can happen while a call is already open;
        # without this runtime state the UI used to report "Realtime" while the
        # existing call continued through classic STT -> Brain -> TTS.
        self._active_voice_mode: str | None = None
        self._active_realtime_provider: str = ""
        self._active_realtime_model: str = ""
        self._active_realtime_handle: Any | None = None
        self._voice_engine_transitioning: bool = False
        self._reopen_after_engine_change: bool = False
        self._engine_change_reason: str = ""
        # Optional event-bus integration. With no bus, transition and emit
        # calls are backward-compatible no-ops.
        self._bus = bus
        # Speech spend. The meter wraps the providers rather than the dozen
        # call sites that use them, so a synthesis added later is counted
        # without anyone remembering to count it. Only the two that bill are
        # wrapped: ``self._stt`` is the local wake/VAD Whisper, free and
        # called many times a second, and metering it would be noise on the
        # hot path for a number that is always zero.
        # No bus means no spend can be published (no sink), but the providers
        # are still wrapped: the same wrapper feeds the provider-health record
        # behind the status dots, which must not depend on the cost sink.
        self._speech_spend = SpeechSpendRecorder(bus) if bus is not None else None
        self._tts = meter_tts(self._tts, self._speech_spend, trace_id=self._speech_trace)
        self._utterance_stt = meter_stt(
            self._utterance_stt, self._speech_spend, trace_id=self._speech_trace
        )
        self._supervisor = supervisor
        # Permanent Vision (Wave 2 B7) is optional. Without an injected
        # provider, every vision hook is a no-op.
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
        self._agent_reply_inflight: AnnouncementRequested | None = None
        self._agent_reply_inflight_text = ""
        self._agent_reply_retries: list[AnnouncementRequested] = []
        self._agent_reply_retry_task: asyncio.Task[None] | None = None
        # Optional pre-rendered acknowledgement PCM, populated during warmup.
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
        # the watchdog on JarvisAgentBackgroundCompleted so successful
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
        # 30 -> 20 s (2026-08-17, instant-ack contract): the user heard the
        # handover line at the start; twenty seconds of nothing afterwards
        # is where "is it still working?" begins.
        self._spawn_watchdog_delay_s: float = 20.0
        self._heartbeat_interval_s: float = 60.0
        self._heartbeat_max_count: int = 3
        self._heartbeat_recent: deque[str] = deque(maxlen=2)

        # TTS announcement bridge (Phase 5 CL-13): router/tools emit
        # `AnnouncementRequested` when they want to give the user an interim
        # announcement (e.g. "Starting a sub-agent, one moment."), without
        # going through the brain path. The handler speaks directly via TTS.
        if self._bus is not None:
            self._bus.subscribe(AnnouncementRequested, self._on_announcement)
            # Fire-and-forget Jarvis-Agent: when a background run finishes,
            # a proactive voice announcement ("Sir, done. <summary>") — so
            # the user finds out even if they did something else in the
            # meantime.
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
            # Prompt Mode toggle from the Jarvis bar's sparkle. The pipeline
            # holds the live dictation config, so it owns the flip and answers
            # with DictationPromptModeChanged for every mirror of the switch.
            self._bus.subscribe(
                DictationPromptModePauseToggleRequested,
                self._on_prompt_mode_pause_toggle_requested,
            )
            # Wave 0 (omni-latency): perceived time-to-first-audio (ack OR
            # brain, whichever speaks first) feeds the per-turn latency tracker.
            self._bus.subscribe(AudioOutFirst, self._on_audio_out_first)
            # Instant-ack progress line (2026-08-17): remember which tool the
            # turn is actually running so a long wait gets an HONEST "still
            # searching" instead of a generic filler.
            self._bus.subscribe(ActionProposed, self._on_action_proposed)
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
        # Prompt-free dictation STT is a separate provider from voice/wake STT.
        # It is warmed only after honest readiness and tracked for shutdown.
        self._dictation_warmup_task: asyncio.Task | None = None
        self._dictation_warmup_provider: Any = None
        self._dictation_warmup_succeeded_provider: Any = None
        self._dictation_warmup_reschedule = False
        self._dictation_warmup_shutdown = False
        self._audio_topology_task: asyncio.Task | None = None

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
        log.info("TTS live switch: %s -> %s (caches invalidated)", old, new)
        # The replacement is metered like the one it replaces, or spend would
        # stop being recorded the first time someone changed voice.
        self._tts = meter_tts(new_tts, self._speech_spend, trace_id=self._speech_trace)
        self._ack_pcm = b""
        self._task_ack_pcm.clear()

    def set_stt_provider(self, provider: str, *, model: str | None = None) -> bool:
        """Live-apply an utterance STT provider without restarting the app.

        The wake recognizer is intentionally left alone: it is a local,
        latency-sensitive detector with a separate lifecycle. Voice turns and
        dictation use the newly built provider from the next transcription.
        A dictation already in flight keeps its existing local reference and
        finishes normally, which avoids reaching into a native inference
        engine while it is running (AP-24).

        The new provider is fully constructed before any live reference or
        configuration value is changed. A failed build therefore leaves the
        current provider usable and returns ``False`` to the REST route.
        """
        normalized = str(provider or "").strip()
        cfg_stt = getattr(getattr(self, "_config", None), "stt", None)
        if not normalized or cfg_stt is None:
            return False

        updates: dict[str, Any] = {"provider": normalized}
        if model is not None:
            updates["model"] = model
        try:
            candidate = cfg_stt.model_copy(update=updates)
        except AttributeError:
            # Lightweight test doubles may not be Pydantic models. Preserve
            # their remaining attributes through a read-only overlay.
            class _CandidateConfig:
                def __getattr__(_self, name: str) -> Any:
                    if name in updates:
                        return updates[name]
                    return getattr(cfg_stt, name)

            candidate = _CandidateConfig()

        try:
            from jarvis.plugins.stt import (
                build_stt_from_config,
                provider_runs_on_device,
            )

            rebuilt = build_stt_from_config(candidate)
            actual = str(getattr(rebuilt, "name", "") or "").strip()
            if actual and actual != normalized:
                log.error(
                    "STT live switch resolved %s instead of requested %s; "
                    "keeping the current provider.",
                    actual,
                    normalized,
                )
                return False

            if not provider_runs_on_device(normalized):
                from jarvis.speech.stt_fallback import wrap_stt_with_fallback

                rebuilt = wrap_stt_with_fallback(rebuilt, candidate)

            from jarvis.speech.stt_dictionary import wrap_stt_with_dictionary

            rebuilt = wrap_stt_with_dictionary(rebuilt)
        except Exception as exc:  # noqa: BLE001 — preserve the working provider
            log.error(
                "STT live switch to %s failed; keeping the current provider: %s",
                normalized,
                exc,
                exc_info=True,
            )
            return False

        previous = getattr(self, "_utterance_stt", None)
        # Re-wrapped like ``set_tts`` does, or hearing spend would stop being
        # recorded the first time someone changed the STT provider.
        self._utterance_stt = meter_stt(
            rebuilt, getattr(self, "_speech_spend", None), trace_id=self._speech_trace
        )
        # A lightweight/cloud-first pipeline has no local preview recognizer,
        # so its preview must follow the selected provider. A heavy wake path
        # keeps its dedicated local probe.
        if getattr(self, "_stt", None) is None or getattr(
            self, "_probe_stt", None
        ) is previous:
            self._probe_stt = rebuilt
        try:
            cfg_stt.provider = normalized
            if model is not None:
                cfg_stt.model = model
        except Exception as exc:  # noqa: BLE001 — runtime cut-over already succeeded
            log.debug("In-memory STT config update skipped: %s", exc)

        # Dictation owns a prompt-free instance and a separate fallback chain;
        # both must be rebuilt from the new provider on the next key press.
        self._reset_dictation_stt()
        self._schedule_dictation_warmup()
        log.info(
            "STT live switch applied: %s -> %s (dictation cache invalidated).",
            type(previous).__name__ if previous is not None else "none",
            normalized,
        )
        return True

    def set_stt_language(self, language: str) -> bool:
        """Live-apply a new recognition language. Returns True when it took.

        The utterance recogniser carries its language from construction, so
        changing the setting used to do nothing at all until the app was
        restarted — while the settings route reported the change as applied,
        because "applied" only ever meant the WAKE plan. The honest outcome is
        either a recogniser that actually speaks the new language now, or a
        False the caller can turn into "restart to apply".

        Rebuilding is cheap: cloud providers are a constructor plus an HTTP
        client, and the local faster-whisper loads its weights lazily on the
        first transcription. The swap is a clean cut-over like ``set_tts`` — an
        in-flight transcription keeps the old instance and finishes normally
        (AP-24: never reach into a native engine that is mid-inference).

        The WAKE recogniser (``self._stt``) is deliberately untouched: it is
        acoustically tied to the wake phrase and has its own language setting.
        """
        cfg_stt = getattr(getattr(self, "_config", None), "stt", None)
        if cfg_stt is None:
            return False
        try:
            cfg_stt.language = language
        except Exception as exc:  # noqa: BLE001 — a frozen model is not an error
            log.debug("in-memory stt.language update skipped: %s", exc)
        try:
            from jarvis.plugins.stt import build_stt_from_config

            rebuilt = build_stt_from_config(cfg_stt)
        except Exception as exc:  # noqa: BLE001 — never break voice on a settings click
            log.warning("STT language live-switch failed to build a provider: %s", exc)
            return False
        if rebuilt is None:
            return False

        try:
            from jarvis.speech.stt_dictionary import wrap_stt_with_dictionary

            rebuilt = wrap_stt_with_dictionary(rebuilt)
        except Exception as exc:  # noqa: BLE001 — corrections are not load-bearing here
            log.debug("STT dictionary wrapper unavailable on live switch: %s", exc)

        previous = self._utterance_stt
        # Same rule as ``set_stt_provider``: the meter wraps the new instance.
        self._utterance_stt = meter_stt(
            rebuilt, getattr(self, "_speech_spend", None), trace_id=self._speech_trace
        )
        # The preview probe follows only when it was the SAME object — in the
        # local-Whisper path it is the wake model, which must keep its own
        # language (AP-27: the wake never rides on the utterance setting).
        if previous is not None and self._probe_stt is previous:
            self._probe_stt = rebuilt
        # The dictation lane holds its OWN instance (no voice bias prompt), so a
        # switch that only rebuilt the voice one would leave dictation
        # transcribing in the previous recognition language for the rest of the
        # process — a setting that appears to apply and silently does not.
        self._reset_dictation_stt()
        self._schedule_dictation_warmup()
        log.info(
            "STT-Live-Switch: recognition language is now %r (%s)",
            language,
            type(rebuilt).__name__,
        )
        return True

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
            setter(_local_silence_window_ms(ms))

    def set_tts_volume(self, volume: float, *, source: str = "") -> None:
        """Live-apply a new master TTS output volume (0.0–1.0) — no restart.

        Delegates to ``AudioPlayer.set_volume`` so a Settings change is audible
        on the next spoken sub-block. No-op-safe when the player is absent
        (headless / not yet started) — the value still persisted and applies on
        the next start.

        Also the ONE place speaker mute is decided (mute = volume 0): when the
        change flips the voice between silent and audible it broadcasts
        ``VoiceSpeakerMuteChanged`` so every surface (the pet's speaker disc,
        the in-app speaker button) mirrors it. Callers run on the REST
        threadpool and on the overlay's Tk thread, so the flip is decided under
        a lock and the publish is marshalled onto the pipeline's loop — this
        never blocks and never awaits.
        """
        target = float(volume)
        lock = getattr(self, "_speaker_mute_lock", None)
        if lock is None:
            # Pipelines built via ``__new__`` in tests skip ``__init__``.
            lock = self._speaker_mute_lock = threading.Lock()
        with lock:
            last = getattr(self, "_speaker_muted_last", None)
            was_muted = self.get_tts_volume() <= 0.0 if last is None else last
            # Remembered even without a player, so a headless mute/unmute
            # reads back what was asked for instead of the boot value.
            self._tts_volume_requested = max(0.0, min(1.0, target))
            player = getattr(self, "_player", None)
            setter = getattr(player, "set_volume", None)
            if callable(setter):
                setter(target)
            muted = target <= 0.0
            self._speaker_muted_last = muted
            if muted != was_muted:
                log.info(
                    "Speaker %s (source=%s)", "muted" if muted else "unmuted", source or "unknown"
                )
                self._publish_event_soon(VoiceSpeakerMuteChanged(muted=muted, source=source))

    def get_tts_volume(self) -> float:
        """The master TTS output volume in effect right now (0.0–1.0).

        The live player's volume when there is a player, else the last value
        ``set_tts_volume`` was asked for, else the configured ``[tts].volume``,
        else full volume. The orb/pet speaker disc reads this to decide whether
        a click mutes or unmutes; without it every click read "audible" and the
        voice could be muted but never brought back.
        """
        player = getattr(self, "_player", None)
        live = getattr(player, "_volume", None)
        if isinstance(live, int | float) and not isinstance(live, bool):
            return max(0.0, min(1.0, float(live)))
        requested = getattr(self, "_tts_volume_requested", None)
        if isinstance(requested, int | float):
            return float(requested)
        configured = getattr(getattr(getattr(self, "_config", None), "tts", None), "volume", None)
        try:
            return max(0.0, min(1.0, float(configured))) if configured is not None else 1.0
        except (TypeError, ValueError):  # Invalid legacy volume values use the audible default.
            return 1.0

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
        wake_preference = getattr(self, "_wake_word_enabled", None)
        wake_enabled = True if wake_preference is None else bool(wake_preference)
        prev = getattr(self._wake_plan, "oww_keyword", None)
        self._wake_plan = plan
        self._wake_matcher = getattr(plan, "matcher", None)
        self._wake_phrase_label = getattr(plan, "phrase", None) or "the wake word"
        engine = getattr(plan, "engine", "openwakeword")

        # A Faster-Whisper wake provider reads its phrase bias on every call.
        # Update it through a capability method so switching between arbitrary
        # stt_match phrases cannot keep transcribing with the old wake word.
        # Clear the bias on non-stt plans because the same provider can also
        # serve the live transcript preview.
        set_initial_prompt = getattr(self._stt, "set_initial_prompt", None)
        if callable(set_initial_prompt):
            prompt = getattr(plan, "phrase", None) if engine == "stt_match" else None
            set_initial_prompt(prompt)

        # Build a local Whisper engine on demand for the stt_match path. The
        # provider __init__ is light (the model loads lazily on first
        # transcription), so this does not block the caller.
        if (
            wake_enabled
            and getattr(plan, "needs_local_whisper", False)
            and self._stt is None
        ):
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
            # the explicit, honest "wake off, use the Call shortcut" mode
            # (product rule 2026-07-04), NOT a dead listener. Do NOT fall back to
            # the bundled branded 'Hey Rhasspy'
            # model — listening for a word the user never says is the bug we are
            # removing. Installing the local speech pack (any word) or a custom
            # .onnx re-arms the wake via a later set_wake_plan.
            self._openwakeword_enabled = False
            self._whisper_wake_enabled = False
            if self._whisper_wake is not None and self._wake_matcher is not None:
                self._whisper_wake._pattern = self._wake_matcher  # noqa: SLF001
            log.info(
                "Wake-Live-Switch: no local model for %r — wake word OFF; "
                "the Call shortcut is the activation. Install the local "
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
                model_paths=getattr(plan, "vosk_model_paths", ()),
                keyword=plan.oww_keyword,
                early_candidate_listener=self._vosk_early_candidate_listener,
            )
            self._openwakeword_enabled = wake_enabled
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
            self._openwakeword_enabled = wake_enabled
            # OWW stands alone for a live switch (lightweight default). The heavy
            # RollingWhisperWake backstop is a boot-time opt-in (heavy_local_whisper),
            # not part of a live wake-word change — turn it off here so switching
            # back to "Hey Jarvis" does not leave a stale custom-phrase matcher
            # running. Keep the pattern in sync in case it is re-enabled.
            if self._whisper_wake is not None and self._wake_matcher is not None:
                self._whisper_wake._pattern = self._wake_matcher  # noqa: SLF001
            self._whisper_wake_enabled = False
        else:  # stt_match — arbitrary phrase via local-Whisper transcript match
            if not wake_enabled:
                self._openwakeword_enabled = False
                self._whisper_wake_enabled = False
            elif self._stt is not None:
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
                # activation is the Call shortcut. This is an explicit,
                # user-visible mode, not a silent dead listener; installing or
                # repairing the local speech pack re-arms the custom phrase via a
                # later set_wake_plan.
                self._openwakeword_enabled = False
                self._whisper_wake_enabled = False
                log.warning(
                    "Wake-Live-Switch: stt_match requested but no local Whisper "
                    "could be built for %r — wake word OFF; use the Call shortcut. "
                    "Install or repair the local speech pack (works "
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

    def set_wake_activation(self, enabled: bool) -> None:
        """Enable or park always-on wake listening without restarting.

        The configured plan stays resident while disabled so hotkeys and the
        rest of the audio pipeline remain untouched. Enabling re-applies that
        plan, lazily preparing any detector it needs, and both transitions wake
        the parked/running wake loop through the existing reload event.
        """
        self._wake_word_enabled = bool(enabled) and _owns_ambient_duties()
        plan = getattr(self, "_wake_plan", None)
        if self._wake_word_enabled and plan is not None:
            self.set_wake_plan(plan)
            return

        self._openwakeword_enabled = False
        self._whisper_wake_enabled = False
        self._wake_reload_event.set()

    def set_keybinds(
        self,
        *,
        call: list[str] | None = None,
        hangup: list[str] | None = None,
        ptt: list[str] | None = None,
        dictate: list[str] | None = None,
        dictate_toggle: list[str] | None = None,
        paste_last: list[str] | None = None,
        pet_toggle: list[str] | None = None,
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

        Every keyword here is spelled exactly like its entry in
        ``config_writer.KEYBIND_ACTIONS``: the settings route calls
        ``set_keybinds(**{action: [...]})``, so a keyword that drifts from the
        action string turns a successful save into a silent no-op — and a
        keyword that is MISSING turns it into a ``TypeError`` the route catches
        and reports as "applies on restart", which is worse: it is a promise
        nothing downstream keeps. Every action in ``KEYBIND_ACTIONS`` therefore
        needs a keyword here, and a binding in ``_build_hotkey_bindings``.
        """
        if call is not None:
            self._call_hotkeys = list(call)
        if hangup is not None:
            self._hangup_hotkeys = list(hangup)
        if ptt is not None:
            self._ptt_hotkeys = list(ptt)
        if dictate is not None:
            self._dictate_hotkeys = list(dictate)
        if dictate_toggle is not None:
            self._dictate_toggle_hotkeys = list(dictate_toggle)
        if paste_last is not None:
            self._paste_last_hotkeys = list(paste_last)
        if pet_toggle is not None:
            self._pet_toggle_hotkeys = list(pet_toggle)
        log.info(
            "Keybinds live-switched: CALL=[%s] PTT=[%s] HANGUP=[%s] DICTATE=[%s] "
            "DICTATE-TOGGLE=[%s] PASTE-LAST=[%s] PET-TOGGLE=[%s]",
            ", ".join(self._call_hotkeys),
            ", ".join(self._ptt_hotkeys) or "off",
            ", ".join(self._hangup_hotkeys),
            ", ".join(self._dictate_hotkeys) or "off",
            ", ".join(self._dictate_toggle_hotkeys) or "off",
            ", ".join(getattr(self, "_paste_last_hotkeys", None) or []) or "off",
            ", ".join(getattr(self, "_pet_toggle_hotkeys", None) or []) or "off",
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
            bindings, edge_events = self._build_hotkey_bindings()
            await trigger.rearm(bindings, push_to_talk=edge_events)

    def _build_hotkey_bindings(self) -> tuple[dict[str, list[str]], set[str]]:
        """The live binding table + the set of bindings wanting BOTH key edges.

        One producer for both arming sites (``run`` at start, ``_hotkey_reload_loop``
        on a live keybind change). They used to build this dict twice; a third
        action made that duplication a drift bug waiting to happen — a shortcut
        that works at boot but not after a Settings save, or the reverse.
        """
        edge_events: set[str] = set()
        if not _owns_ambient_duties():
            # Global hotkeys are the default app's; see __init__.
            return {"call": [], "hangup": []}, edge_events
        bindings: dict[str, list[str]] = {
            "call": list(self._call_hotkeys),
            "hangup": list(self._hangup_hotkeys),
        }
        if self._ptt_hotkeys:
            bindings["ptt"] = list(self._ptt_hotkeys)
            edge_events.add("ptt")
        if self._dictate_hotkeys:
            bindings["dictate"] = list(self._dictate_hotkeys)
            # Hold mode needs the release edge to know when to submit; toggle
            # mode deliberately gets the legacy single-fire-on-release contract
            # so holding the key does not start and stop repeatedly.
            if self._dictate_mode == "hold":
                edge_events.add("dictate")
        # ``getattr`` default: unit tests build pipelines via ``__new__`` and set
        # only the attributes they care about, a widely used pattern in this repo.
        dictate_toggle = getattr(self, "_dictate_toggle_hotkeys", None) or []
        if dictate_toggle:
            bindings["dictate_toggle"] = list(dictate_toggle)
            # Never an edge binding: the whole point of a toggle is one event
            # per press. Asking for both edges would start on the down edge and
            # stop again on the up edge, i.e. turn it back into push-to-talk.
        paste_last = getattr(self, "_paste_last_hotkeys", None) or []
        if paste_last:
            # Single-fire on release, like every non-hold action: holding the
            # key must paste once, not once per polling tick.
            bindings["paste_last"] = list(paste_last)
        pet_toggle = getattr(self, "_pet_toggle_hotkeys", None) or []
        if pet_toggle:
            # Single-fire on release too: one press hides or shows the pet once.
            bindings["pet_toggle"] = list(pet_toggle)
        return bindings, edge_events

    # ------------------------------------------------------------------
    # Bus / supervisor helpers — a no-op when nothing is configured
    # ------------------------------------------------------------------

    async def _transition(self, new_state: str) -> None:
        # This only DISPLAYS state, so nothing in it may end a call. A pipeline
        # assembled without a supervisor (headless, and every unit test that
        # builds one field by field) must still run the call to completion —
        # before this was defensive, adding one status update to the realtime
        # path aborted the whole session loop on a missing attribute.
        supervisor = getattr(self, "_supervisor", None)
        if supervisor is not None:
            try:
                await supervisor.set_state(new_state)
            except Exception as exc:  # noqa: BLE001
                log.warning("Supervisor-Transition zu %s fehlgeschlagen: %s", new_state, exc)
        # Permanent-Vision Privacy-Hook (Wave-2 B7): bei IDLE pausieren,
        # bei ACTIVE-Familie (LISTENING/THINKING/SPEAKING) resumen.
        try:
            self._maybe_toggle_vision_on_state(new_state)
        except Exception:  # noqa: BLE001
            log.debug(
                "Vision toggle for state %s skipped (pipeline without vision)",
                new_state,
                exc_info=True,
            )

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
            self._previous_turn_state = previous.value
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
        if new_state is TurnTakingState.LISTENING:
            self._schedule_delegation_results()
        if new_state is TurnTakingState.IDLE:
            for task_name in ("_agent_reply_retry_task", "_delegation_result_task"):
                retry = getattr(self, task_name, None)
                if retry is not None and retry is not asyncio.current_task():
                    retry.cancel()
                    await asyncio.gather(retry, return_exceptions=True)
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
                if new_state is TurnTakingState.IDLE and self._is_agent_reply(event):
                    self._defer_agent_reply(event)
                    continue
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

    def _assistant_work_in_flight(self) -> bool:
        """Owned off-loop work keeps idle expiry from closing a busy session."""
        return bool(getattr(self, "_assistant_work_count", 0))

    def _termination_snapshot(self, reason: str) -> dict[str, Any]:
        """Bounded diagnostics, without user text, collected before teardown."""
        from jarvis.harness.computer_use_context import cu_mission_active

        continuing = getattr(self, "_continue_listening_after_response", None)
        state = getattr(self, "_turn_state", None)
        detail = dict(getattr(self, "_termination_detail", {}))
        detail.update({
            "hangup_reason": reason,
            "producer": getattr(self, "_termination_producer", "")
            or "speech.pipeline._state_loop.finalize",
            "continue_listening_after_response": continuing,
            "single_turn_mode": None if continuing is None else not continuing,
            "idle_timeout_s": (
                getattr(self, "_idle_timeout_s", None)
                if getattr(self, "_idle_hangup_enabled", True) else 0
            ),
            "idle_deadline_monotonic": getattr(self, "_idle_deadline_monotonic", None),
            "last_activity_monotonic": getattr(self, "_last_user_activity_monotonic", None),
            "last_answer_floor_monotonic": getattr(self, "_last_answer_floor_monotonic", None),
            "turn_state": getattr(state, "value", None),
            "previous_turn_state": getattr(self, "_previous_turn_state", None),
            "assistant_work_active": self._assistant_work_in_flight(),
            "tool_active": None,
            "screen_active": None,
            "computer_use_active": cu_mission_active(),
        })
        return detail

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
        # The microphone's own word on "the user is talking again" — read by
        # the turn handler when a final transcript lands (Thinking-pause hold).
        # Cleared at the next endpoint, so it always describes speech AFTER
        # the utterance currently being transcribed.
        self._vad_speech_after_endpoint = True
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
        self._vad_speech_after_endpoint = True

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
        # This utterance is over; whatever speech follows is the NEXT one.
        self._vad_speech_after_endpoint = False
        self._cancel_stale_probe()
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
            # ...and a text held for that promised speech is released now:
            # nothing is coming, the user gets their answer at once instead
            # of after the drain grace.
            self._release_mic_hold_soon()

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

    def _cancel_stale_probe(self) -> None:
        """Cancel an in-flight preview when the utterance has ended."""
        task = getattr(self, "_probe_task", None)
        if task is None or task.done():
            return
        current = asyncio.current_task(loop=task.get_loop())
        if task is not current:
            task.cancel()

    async def _drain_stale_probe(self) -> None:
        """Finish preview cancellation before dispatching the final STT call."""
        task = getattr(self, "_probe_task", None)
        if task is None:
            return
        current = asyncio.current_task()
        if task is current:
            return
        if not task.done():
            task.cancel()
        try:
            outcome = (await asyncio.gather(task, return_exceptions=True))[0]
            if isinstance(outcome, Exception):
                log.debug("Stale STT preview cleanup failed: %s", outcome)
            elif isinstance(outcome, BaseException) and not isinstance(
                outcome, asyncio.CancelledError
            ):
                raise outcome
        finally:
            if getattr(self, "_probe_task", None) is task:
                self._probe_task = None
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
        task = loop.create_task(
            self._stt_probe_async(pcm, generation, tail_loud),
            name="stt-stability-probe",
        )
        self._probe_task = task

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
            # The RAW decode, deliberately. This probe asks "did anything get
            # said in the tail?" and answers with text LENGTH plus the
            # hallucination markers. A provider-cleaned string breaks both:
            # a tail of hesitation ("ähm ...") cleans to nothing,  # i18n-allow: input
            # would read as silence, so Jarvis would cut off the one person
            # who is visibly still thinking.
            raw_text = (
                getattr(transcript, "raw_text", "")
                or getattr(transcript, "text", "")
                or ""
            ).strip()
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

    def _capture_permission_allowed(self) -> bool:
        """Return the live local-capture gate without applying Jarvis mute."""
        gate = getattr(self, "_activation_gate", None)
        if gate is None:
            return True
        try:
            return bool(gate())
        except Exception as exc:  # noqa: BLE001
            log.warning("Voice capture permission gate failed closed: %s", exc)
            return False

    def _dictation_blocks_activation(self) -> bool:
        """True while a running dictation must keep the wake word silent.

        The maintainer's contract: a dictation turn owns the microphone, so the
        words being dictated must never trip the wake word (and two native input
        streams must never race for the same device — BUG-014 / AP-24).

        This is a STATE gate, not the content gate AP-27 forbids: it asks
        whether an application task is alive, with zero reference to audio or to
        any transcript, exactly like the three gates it sits beside
        (``_wake_lock_until``, mute, ``_state != IDLE``). It changes WHEN wake is
        listened to, never HOW a candidate is judged.

        Two independent things must BOTH hold for wake to stay blocked:

        1. A dictation task is alive — either the recording itself
           (``_dictation_task``) or the handover that is ending a voice
           conversation to get the microphone for one
           (``_dictation_handover_task``). Derived from ``task.done()``, never a
           bare bool somebody has to remember to clear — a crashed, cancelled or
           returned task is ``done()``, so the block lifts by itself.
        2. The watchdog deadline has not passed. ``_dictation_wake_block_until``
           is a timestamp set at start, mirroring ``_wake_lock_until``. A task
           that somehow hangs forever therefore still cannot deafen the wake
           word beyond it.

        The handover half matters as much as the recording half: ending the
        conversation returns the pipeline to IDLE with the wake loop free to
        re-arm, and a wake word firing in that gap would take the microphone
        straight back from the user who just pressed the dictation key.

        All defaults fail OPEN (no task → False, missing deadline → 0.0 →
        False), because the failure this guards against is BUG-037: permanently
        deaf with no visible cause and only a restart to fix.
        """
        task = getattr(self, "_dictation_task", None)
        if task is None or task.done():
            task = getattr(self, "_dictation_handover_task", None)
        if task is None or task.done():
            return False
        return time.time() < float(getattr(self, "_dictation_wake_block_until", 0.0))

    def _activation_block_reason(self) -> str:
        """The honest English reason ``_activation_allowed`` is saying no.

        One source for every log line that reports the closed gate. It exists
        because those lines used to hardcode two guesses ("muted" /
        "window not visible?"), and the wrong guess once misled a live freeze
        diagnosis. Returns an empty string when the gate is open.
        """
        if getattr(self, "_muted", False):
            return "voice is muted"
        if self._dictation_blocks_activation():
            return "a dictation is running"
        if not self._capture_permission_allowed():
            return "microphone capture is not permitted (desktop window not visible?)"
        return ""

    def _activation_allowed(self) -> bool:
        """True when external UI/lifecycle state permits voice activation.

        While muted (mascot doubleClick → ``_muted=True``) we always
        return False so the wake-loop ignores every detection. The loop
        keeps spinning; unmuting is one bool flip away.

        A running dictation closes the same gate (``_dictation_blocks_activation``)
        — one edit reaches all three wake gates (loop entry, pre-emit, state
        loop) plus the push-to-talk and ``request_voice_session`` entry points,
        because this predicate is the only one all of them consult.

        ``getattr`` defaults to False for pipelines constructed via
        ``__new__`` (used by privacy/vision unit tests that bypass
        ``__init__``) — those instances are never muted by definition.

        NB: ``dictation_available`` / ``start_dictation`` deliberately consult
        ``_capture_permission_allowed`` and NOT this predicate. Routing them
        through here would make a dictation forbid its own successor.
        """
        if getattr(self, "_muted", False):
            return False
        if self._dictation_blocks_activation():
            return False
        from jarvis.live.runtime import owns_microphone

        if owns_microphone(except_session_id=getattr(self, "_current_voice_session_id", None)):
            return False
        return self._capture_permission_allowed()

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

    async def _on_prompt_mode_pause_toggle_requested(
        self, event: DictationPromptModePauseToggleRequested
    ) -> None:
        """Pause or resume Prompt Mode and broadcast the new state.

        Idempotent like the mute toggle: the caller does not know the current
        state. Deliberately NOT a settings write — the value in jarvis.toml is
        left alone, so the settings card keeps showing the switch on and one
        more click on the bar brings the rewriting back.
        """
        from jarvis.dictation.prompt_mode_switch import toggle_prompt_mode_pause

        await toggle_prompt_mode_pause(
            cfg=getattr(self, "_dictation_cfg", None),
            bus=self._bus,
            source=event.source or "unknown",
        )

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

    def _publish_event_soon(self, event: Any) -> None:
        """Publish from SYNCHRONOUS code without blocking the caller.

        ``start_dictation`` and the dictation teardown both have to announce
        themselves from places that cannot ``await``: a plain method, and a
        ``finally`` in a task that may already be cancelled. Scheduling the
        publish as its own task keeps both honest — a cancelled dictation still
        gets its terminal event out, and a broken subscriber can never propagate
        back into the caller (AP-18).

        A missing bus or a missing loop is a clean no-op: unit tests build
        pipelines via ``__new__`` and must not need an event bus to exercise the
        dictation lane.
        """
        if getattr(self, "_bus", None) is None:
            return

        def _spawn(loop: asyncio.AbstractEventLoop) -> None:
            # Hold a strong reference until the publish finishes: the event loop
            # only keeps a weak one, so a fire-and-forget task can be collected
            # mid-flight and the event would simply vanish.
            pending = getattr(self, "_detached_publishes", None)
            if pending is None:
                pending = set()
                self._detached_publishes = pending
            task = loop.create_task(self._publish_event(event))
            pending.add(task)
            task.add_done_callback(pending.discard)

        try:
            _spawn(asyncio.get_running_loop())
            return
        except …116827 tokens truncated…) - capture_closed_at) * 1000.0)}",
                    f"truncation_repairs:{truncation_repairs}",
                    f"tail_repairs:{tail_repairs}",
                    f"pause_trim_ms:{round(pause_trim_bytes * 1000 / bytes_per_second)}",
                    f"capture_overflows:{capture_overflows}",
                    "polish_mode:"
                    + (
                        "incremental"
                        if prefix_polish_raw and not prefix_polish_failed
                        else "whole"
                    ),
                    f"polish_deltas:{prefix_polish_deltas}",
                    f"code_switching:{'on' if code_switching else 'off'}",
                    f"capture_restarts:{capture_restart_count}",
                    *audio_preprocessing.audit(),
                ),
                audio_sample_rate_hz=quality_metrics.sample_rate_hz,
                audio_rms=quality_metrics.rms,
                audio_clipping_ratio=quality_metrics.clipping_ratio,
                audio_dropouts=quality_metrics.dropout_count,
                audio_dropout_ms=quality_metrics.dropout_duration_ms,
            )
            final_text = result
            log.info(
                "🎙️ dictation ended (%d chars, %.1fs spoken / %.1fs elapsed, "
                "target=%s, %.1fs silence skipped%s%s%s%s).",
                len(final_text),
                duration_s,
                wall_clock_s,
                target,
                skipped_silence_bytes / bytes_per_second,
                ", hung up" if hung_up else "",
                (
                    f", stt error: {stt_error} ({stt_error_detail or 'no detail'})"
                    if stt_error
                    else ""
                ),
                f", capture: {capture_error}" if capture_error else "",
                # The two numbers that separate "one flaky call, fully
                # recovered" from "the user is missing half a minute". Both are
                # printed even when the outcome reads as a success, because the
                # session that started this whole fix looked like a success in
                # every log line it produced.
                (
                    f", LOST {lost_audio_s:.1f}s after "
                    f"{len(stt_failures)} failed call(s)"
                    if lost_audio_bytes
                    else (
                        f", {len(stt_failures)} failed call(s) recovered"
                        if stt_failures
                        else ""
                    )
                ),
            )
        except Exception as exc:  # noqa: BLE001 — dictation must never break voice
            log.warning("dictation session crashed (non-fatal)", exc_info=True)
            if finished:
                # The crash happened after (or inside) the delivery half, which
                # already published its own final transcript. Re-entering it
                # would double-publish and could recurse into the same fault.
                return
            # Everything before delivery: route the failure THROUGH the normal
            # finish path instead of returning silently. It publishes the same
            # empty final transcript the old handler did, and additionally
            # records a `failed` history row — the worst failure this feature
            # has used to be its most invisible one.
            try:
                quality_metrics = audio_quality.snapshot(
                    reported_dropouts=capture_reported_dropouts
                )
                await self._finish_dictation(
                    raw_text="",
                    language=language,
                    # Audio-derived here too: a crash mid-dictation must not
                    # write a row whose duration is mostly the crash.
                    duration_s=len(buffer) / bytes_per_second,
                    target=target,
                    hung_up=False,
                    stt_error=classify_stt_failure(exc),
                    audio=bytes(buffer),
                    audio_sample_rate_hz=quality_metrics.sample_rate_hz,
                    audio_rms=quality_metrics.rms,
                    audio_clipping_ratio=quality_metrics.clipping_ratio,
                    audio_dropouts=quality_metrics.dropout_count,
                    audio_dropout_ms=quality_metrics.dropout_duration_ms,
                )
            except Exception:  # noqa: BLE001 — the fallback must not raise either
                log.debug("dictation failure record failed", exc_info=True)
                try:
                    await self._publish_event(
                        DictationTranscript(
                            source_layer="speech.dictation", text="", is_final=True
                        )
                    )
                except Exception:  # noqa: BLE001, S110
                    # The primary failure is already logged; this event is optional.
                    pass
        finally:
            # Terminal-event guarantee. Consumers open on ``DictationStarted``
            # and close on ``DictationCompleted``, so any path that ends without
            # a completion leaves a surface showing a dictation that is not
            # running — and, until the task is collected, the wake-word block
            # depends on the same task, not on this event, so the two can never
            # disagree. ``asyncio.CancelledError`` is a ``BaseException`` and
            # skips the handler above; a crash inside ``_finish_dictation`` can
            # also abort before it publishes. Both land here. Scheduled rather
            # than awaited, because awaiting inside a cancelled task is not
            # reliable.
            #
            # Cancellation can interrupt capture, preview or formatting as well
            # as a final window. Retire every task owned by this recording so
            # discarded speech cannot keep decoding or formatting in the back.
            stop_event.set()
            for pending in (*final_tasks.values(), probe_task, prefix_polish_task):
                if pending is not None and not pending.done():
                    pending.cancel()
            if not getattr(self, "_dictation_completion_published", True):
                self._dictation_completion_published = True
                quality_metrics = audio_quality.snapshot(
                    reported_dropouts=capture_reported_dropouts
                )
                self._publish_event_soon(
                    DictationCompleted(
                        source_layer="speech.dictation",
                        outcome="cancelled",
                        detail="The dictation ended before it produced text.",
                        # Same measure as every other exit: what was recorded,
                        # not how long the machinery took to give up.
                        duration_s=len(buffer) / bytes_per_second,
                        audio_sample_rate_hz=quality_metrics.sample_rate_hz,
                        audio_rms=quality_metrics.rms,
                        audio_clipping_ratio=quality_metrics.clipping_ratio,
                        audio_dropouts=quality_metrics.dropout_count,
                        audio_dropout_ms=quality_metrics.dropout_duration_ms,
                    )
                )

    #: How much audio a CLOUD reading needs before it is allowed to steer later
    #: dictations. Under this, the provider is guessing from too little context
    #: — which is the defect this anchor exists to route around, so accepting a
    #: short reading here would let the guess teach itself.
    _LANGUAGE_ANCHOR_MIN_S = 6.0

    #: How many readings the anchor remembers. Small enough to follow a speaker
    #: who switches language within a few dictations, large enough that one
    #: mis-detected clip cannot flip it on its own.
    _LANGUAGE_ANCHOR_HISTORY = 5

    def _remember_dictation_language(self, language: str, *, on_device: bool) -> None:
        """Record one reading of what this user dictates in. Never raises.

        The anchor behind ``_recent_dictation_language``. An ``on_device``
        reading is trusted outright — a local decoder cannot have translated the
        audio, so its answer is about the SOUND. A cloud reading is a claim
        about a transcript that may itself be a translation, so the caller only
        offers one when there was enough audio to make the claim worth
        something, and it still only ever gets a vote rather than the decision.
        """
        code = str(language or "").strip().lower()
        if not code or code in ("auto", "unknown", "und"):
            return
        readings = getattr(self, "_dictation_language_readings", None)
        if readings is None:
            from collections import deque

            readings = deque(maxlen=self._LANGUAGE_ANCHOR_HISTORY)
            self._dictation_language_readings = readings
        # An on-device reading is worth the whole window, because it is the one
        # signal a translation cannot fake: one of them outvotes a run of cloud
        # guesses instead of waiting its turn behind them.
        readings.extend([code] * (readings.maxlen if on_device else 1))

    def _prime_dictation_language_anchor(self) -> None:
        """Seed the anchor from the stored history, once per process.

        Without this the anchor is empty after every app start, so the first
        short dictation of a session is back to asking two seconds of audio
        which language it is — the exact case this exists to avoid. The history
        already holds the answer: what this person dictated in yesterday is a
        better opening guess than nothing at all.

        Cheap enough to do inline (measured: ~1.6 ms for a 147 KB history) and
        it happens on the first dictation, never at boot (AP-26). Failure is a
        no-op: an unreadable history costs an opening guess, never a dictation.
        """
        if getattr(self, "_dictation_anchor_primed", False):
            return
        self._dictation_anchor_primed = True
        try:
            from jarvis.dictation.history import DictationHistory

            entries = DictationHistory().list_all(include_discarded=True)
            recent = [
                entry
                for entry in entries
                if float(getattr(entry, "duration_s", 0.0) or 0.0)
                >= self._LANGUAGE_ANCHOR_MIN_S
            ][: self._LANGUAGE_ANCHOR_HISTORY]
            # Oldest first, so the newest readings are the ones the bounded
            # window keeps — a person who switched language last week must not
            # be anchored to the language they used before that.
            for entry in reversed(recent):
                self._remember_dictation_language(
                    str(getattr(entry, "language", "") or ""), on_device=False
                )
        except Exception:  # noqa: BLE001 — an opening guess is never worth a failure
            log.debug("dictation language anchor priming failed", exc_info=True)

    def _recent_dictation_language(self) -> str:
        """The language recent dictations agree on, or ``""`` when unclear.

        Read at the START of a dictation, where it becomes the session language
        a short recording could never establish for itself. A MAJORITY rather
        than the last reading, so a single mis-detected clip cannot redirect the
        next one; ties resolve to ``""``, which simply restores auto-detect.
        """
        self._prime_dictation_language_anchor()
        readings = getattr(self, "_dictation_language_readings", None)
        if not readings:
            return ""
        from collections import Counter

        counts = Counter(readings).most_common()
        if len(counts) > 1 and counts[0][1] == counts[1][1]:
            return ""
        return counts[0][0]

    async def _publish_dictation_event(self, event: Any) -> bool:
        """Deliver to healthy observers in order without waiting on a dead UI.

        The bus fans out concurrently, so an unrelated stalled observer cannot
        delay a healthy recipient. The bounded await still joins cancellation;
        no publication task is left behind to reorder a later dictation.
        """
        try:
            await asyncio.wait_for(
                self._publish_event(event), timeout=_DICTATION_EVENT_TIMEOUT_S
            )
        except TimeoutError:
            log.warning(
                "Dictation event %s exceeded %.1fs; abandoning stalled observers.",
                type(event).__name__,
                _DICTATION_EVENT_TIMEOUT_S,
            )
            return False
        return True

    async def _finish_dictation(
        self,
        *,
        raw_text: str,
        language: str,
        duration_s: float,
        target: str,
        hung_up: bool,
        stt_error: str | None = None,
        lost_audio_s: float = 0.0,
        dropped_audio_s: float = 0.0,
        audio: bytes | None = None,
        stt_providers: tuple[str, ...] = (),
        stt_models: tuple[str, ...] = (),
        detected_languages: tuple[str, ...] = (),
        stt_latency_ms: int = 0,
        stt_calls: int = 0,
        stt_errors: tuple[str, ...] = (),
        stt_audit: tuple[str, ...] = (),
        audio_sample_rate_hz: int = 0,
        audio_rms: float = 0.0,
        audio_clipping_ratio: float = 0.0,
        audio_dropouts: int = 0,
        audio_dropout_ms: int = 0,
        polished_prefix: tuple[str, str] | None = None,
    ) -> str:
        """Clean, deliver and record one finished dictation. Returns the text.

        ``polished_prefix`` is ``(raw, formatted)`` for the leading part of the
        recording the session already cleaned and formatted while the user was
        speaking (the incremental polish). When ``raw`` is a strict prefix of
        ``raw_text``, only the remaining tail is formatted here — with the
        formatted prefix as context — and the two are joined; otherwise the
        whole text is formatted as before. The Restore route passes nothing.

        Split out of ``_dictation_session`` so the delivery half is testable
        without a microphone. Every step degrades on its own: a failed cleanup
        falls back to the raw transcript, a failed insertion falls back to "it
        is on your clipboard", and a failed history write costs nothing but the
        history entry.

        ``stt_error`` is the transcription failure, if there was one. It is what
        makes ``failed`` distinguishable from ``empty``, so it is carried into
        the completion event and the history row rather than logged and dropped.
        It is normalised to a reason code HERE rather than trusted from the
        caller: this method is the one place the value is persisted and
        published, and a raw provider string reaching it is exactly how a Python
        exception class and a vendor URL ended up rendered under a user's own
        dictated words. A backstop at the store beats a rule every future caller
        has to remember.

        ``lost_audio_s`` is how many seconds of the recording were attempted
        and never read — see the ``partial`` entry in
        ``jarvis.dictation.outcomes``. It is a SEPARATE argument from
        ``stt_error`` on purpose, and the distinction is the whole of the fix:
        an error says something went wrong, this says something was lost, and
        only the second one is a reason to stop calling the dictation a success
        and to keep its audio. Defaults to zero so every existing caller (and
        the crash path below) keeps today's behaviour exactly.

        ``dropped_audio_s`` is the OTHER way to end up missing words, and it is
        kept apart from ``lost_audio_s`` because the two owe the user different
        sentences. Lost audio was recorded and never read — a retry could still
        read it, which is what Restore offers. Dropped audio was never recorded:
        the capture queue overflowed while the loop was busy and those frames
        are gone, so promising Restore would point at a button that cannot help.
        Both degrade the outcome to ``partial``; only the first one promises
        anything.

        ``audio`` is the session's raw PCM; it is written to a local sidecar
        only when the dictation left the user missing words AND they allow it
        (``[dictation].keep_failed_audio``), which is what a later Restore
        transcribes again.
        """
        finish_started = time.perf_counter()
        stt_error = normalize_stt_failure(stt_error)
        cleaned = raw_text
        removed_words = 0
        cleanup_reason = ""
        cfg = getattr(self, "_dictation_cfg", None)

        # A user-pinned ``[dictation].language`` outranks whatever the provider
        # reported. Without this the cleanup rules see the provider's guess —
        # and a provider that answers "unknown" leaves every dictation with
        # reason="no_rules", i.e. no cleanup at all despite an explicit pin.
        pinned = str(getattr(cfg, "language", "auto") or "auto").strip().lower()
        # Which language the cleanup rules — and the polish pass — run in. The
        # decision itself lives in ``resolve_dictation_language`` at module
        # level, because the Restore route re-transcribes the same audio and has
        # to reach the same answer. A hangup transcribed nothing, so there is no
        # text to resolve from and the reported tag stands.
        effective_language = resolve_dictation_language(
            pinned=pinned,
            reported=language,
            text="" if hung_up else raw_text,
        )

        # Teach the anchor what this user dictates in, so the NEXT recording —
        # which may be two seconds of "carry on" — is not left asking a clip too
        # short to answer. Only a long recording votes: a short one is exactly
        # the case the provider gets wrong, and letting it vote would let the
        # guess confirm itself. The reading is the resolved language, so a user
        # pin outranks everything here as it does everywhere else.
        if not hung_up and duration_s >= self._LANGUAGE_ANCHOR_MIN_S:
            try:
                self._remember_dictation_language(
                    effective_language, on_device=False
                )
            except Exception:  # noqa: BLE001 — an anchor is a hint, never a gate
                log.debug("dictation language anchor update failed", exc_info=True)

        if raw_text and not hung_up:
            try:
                from jarvis.dictation.cleanup import clean_transcript

                outcome = clean_transcript(
                    raw_text,
                    language=effective_language,
                    remove_fillers=bool(getattr(cfg, "remove_fillers", True)),
                    max_removed_fraction=float(
                        getattr(cfg, "filler_max_removed_fraction", 0.25)
                    ),
                )
                cleaned = outcome.text
                removed_words = outcome.removed_words
                cleanup_reason = outcome.reason
            except Exception:  # noqa: BLE001 — never lose the text to a cleanup bug
                log.warning("dictation cleanup failed; using the raw transcript",
                            exc_info=True)
                cleaned = raw_text

            # Punctuation repair runs UNCONDITIONALLY, next to the cleanup and
            # deliberately outside its result. The two are not variants of one
            # step: filler removal is a user preference that only applies to
            # three languages, while the damage this repairs — "gesprochen....
            # ist", a sentence restarting in lower case — is manufactured by our
            # own segmented transcription and therefore exists in every
            # dictation, in every language, whether or not a filler was found.
            # Gating it on the cleanup's outcome (which is where it used to sit)
            # is why the live history is full of stretches nothing ever touched.
            try:
                from jarvis.dictation.cleanup import tidy_transcript

                cleaned = tidy_transcript(cleaned)
            except Exception:  # noqa: BLE001 — a tidy bug never costs the words
                log.debug("dictation tidy failed; using the untidied transcript",
                          exc_info=True)

        # Two kinds of text must never reach a document, and both used to.
        #
        # (a) Whisper's silence boilerplate. Every Whisper-family model answers a
        #     near-silent microphone with the same handful of subtitle credits
        #     and video outros — 12 of the first 26 rows of the live history are
        #     "Thank you." or "Thank you for watching!", none of them spoken. The
        #     repo has owned the marker list for months and the voice lane
        #     filters on it in four places; the dictation lane never did.
        # (b) A transcript with no WORDS in it. A bare "." was pasted into a live
        #     document (history row 2026-07-28T18:10:30) and the clipboard was
        #     then restored over it, so the user was left holding a stray full
        #     stop and no way back to what they had copied.
        #
        # Blanking the text HERE, rather than only skipping the insertion, is
        # deliberate: the final transcript published below also feeds the chat
        # composer and the bar, and a dictation that reports "empty" while its
        # text already sits in the composer is the same defect in a different
        # window. ``raw_text`` is untouched, so the history row still shows what
        # the provider returned and a later Restore can transcribe the audio
        # again. Fail-open like every other step here: a broken gate costs the
        # gate, never the user's words.
        rejected_detail = ""
        if cleaned.strip() and not hung_up:
            try:
                from jarvis.dictation.cleanup import count_words

                if count_words(cleaned) == 0:
                    rejected_detail = (
                        "The dictation produced punctuation but no words, so "
                        "nothing was inserted."
                    )
                elif _is_silence_hallucination(cleaned, duration_s):
                    rejected_detail = (
                        "The audio was too quiet to resolve, so nothing was "
                        "inserted."
                    )
                if rejected_detail:
                    log.info(
                        "dictation delivery refused (%.1fs of audio, %r): %s",
                        duration_s,
                        cleaned[:80],
                        rejected_detail,
                    )
                    cleaned = ""
            except Exception:  # noqa: BLE001 — a broken guard never eats the text
                log.debug("dictation delivery gate failed", exc_info=True)
                rejected_detail = ""

        # The generative polish pass — the second read-over that turns a
        # transcript into written prose (punctuation, sentence structure, the
        # capitalisation our own ~8 s segment boundaries destroy). No regex can
        # do this, which is why the deterministic cleanup above closes only half
        # the gap.
        #
        # It sits HERE, after the delivery gates and before the publish, for two
        # reasons. After the gates, so we never spend a model call polishing
        # boilerplate we are about to throw away. Before the publish, so the
        # chat composer, the Jarvis Bar and the insertion all see the SAME
        # string — a polished paste next to an unpolished composer is the kind
        # of divergence nobody can explain afterwards. ``raw_text`` is untouched
        # and still reaches the history, so the user can always recover the
        # words they actually said.
        #
        # Fail-open twice over: ``polish_transcript`` itself never raises and
        # returns the raw text on every non-``applied`` status, and this block
        # additionally catches anything the import or the call surfaces. The
        # import is INSIDE the function (AP-26) exactly like ``clean_transcript``
        # above — nothing about the polish pass may reach the boot path.
        # The same pass also delivers the dictation in a fixed language when
        # ``[dictation].translate`` is on — one model call does both, so a
        # translation costs the same single round trip a polish does.
        polish_status = ""
        polish_provider = ""
        polish_latency_ms = 0
        if cleaned.strip() and not hung_up:
            try:
                from jarvis.dictation.polish import (
                    polish_enabled,
                    polish_transcript,
                    resolve_translate_target,
                )

                # Resolved HERE and not inside the pass so both callers of the
                # pass — this one and the Restore route — reach the same answer
                # from the same function, exactly like ``effective_language``
                # above. It reads the CONFIG only, deliberately NOT
                # ``effective_language``: deciding per dictation whether to
                # translate, based on a recognizer tag that is documented to be
                # wrong, is what made the delivered language alternate between
                # two with nothing the user touched explaining it.
                translate_to = resolve_translate_target(cfg)
                prefix_raw, prefix_text = polished_prefix or ("", "")

                # Prompt Mode outranks both passes: the whole dictation is
                # rewritten into an English brief for a coding agent, so
                # neither the formatter nor the translator has anything left
                # to do — and the incremental prefix, formatted while the
                # user was still speaking, is superseded by the brief. When
                # it cannot deliver (no writer, timeout, an answer that is
                # not a prompt) the dictation falls through to the passes
                # below exactly as if the switch were off.
                from jarvis.dictation.prompt_mode import (
                    STATUS_PROMPTED,
                    compose_prompt,
                    prompt_mode_enabled,
                )

                prompted = False
                if prompt_mode_enabled(cfg):
                    result = await compose_prompt(
                        cleaned,
                        cfg=cfg,
                        protected_terms=self._dictation_protected_terms(),
                        language=effective_language,
                    )
                    prompted = result.status == STATUS_PROMPTED
                    log.info(
                        "dictation prompt mode: %s (%s, %d ms%s).",
                        result.status,
                        result.provider or "no writer",
                        result.latency_ms,
                        f", {result.reason}" if result.reason else "",
                    )
                    if prompted:
                        cleaned = result.text
                        polish_status = result.status
                        polish_provider = result.provider
                        polish_latency_ms = result.latency_ms

                incremental = bool(
                    not prompted
                    and prefix_raw
                    and not translate_to
                    and polish_enabled(cfg)
                    and raw_text.startswith(prefix_raw)
                )
                if prompted:
                    pass  # delivered above; the passes below are superseded
                elif incremental:
                    # Only the tail is left: clean it the same way the whole
                    # text was cleaned above and format it in the light of the
                    # already-formatted prefix.
                    tail_raw = raw_text[len(prefix_raw) :].strip()
                    tail_text = ""
                    polish_status = "applied"
                    if tail_raw:
                        try:
                            from jarvis.dictation.cleanup import (
                                clean_transcript,
                                tidy_transcript,
                            )

                            tail_outcome = clean_transcript(
                                tail_raw,
                                language=effective_language,
                                remove_fillers=bool(getattr(cfg, "remove_fillers", True)),
                                max_removed_fraction=float(
                                    getattr(cfg, "filler_max_removed_fraction", 0.25)
                                ),
                            )
                            tail_text = tidy_transcript(tail_outcome.text)
                        except Exception:  # noqa: BLE001 — never lose the tail
                            tail_text = tail_raw
                        if tail_text.strip():
                            result = await polish_transcript(
                                tail_text,
                                language=effective_language,
                                cfg=cfg,
                                protected_terms=self._dictation_protected_terms(),
                                style=str(getattr(cfg, "polish_style", "neutral") or "neutral"),
                                preceding_text=prefix_text,
                            )
                            tail_text = result.text
                            polish_status = result.status
                            polish_provider = result.provider
                            polish_latency_ms = result.latency_ms
                    cleaned = " ".join(
                        part for part in (prefix_text, tail_text) if part.strip()
                    ).strip()
                    log.info(
                        "dictation polish: incremental — %d chars formatted while "
                        "speaking, tail %s (%s, %d ms).",
                        len(prefix_text),
                        polish_status,
                        polish_provider or "no provider",
                        polish_latency_ms,
                    )
                elif polish_enabled(cfg) or translate_to:
                    result = await polish_transcript(
                        cleaned,
                        language=effective_language,
                        cfg=cfg,
                        protected_terms=self._dictation_protected_terms(),
                        style=str(getattr(cfg, "polish_style", "neutral") or "neutral"),
                        translate_to=translate_to,
                    )
                    cleaned = result.text
                    polish_status = result.status
                    polish_provider = result.provider
                    polish_latency_ms = result.latency_ms
                    log.info(
                        "dictation %s: %s (%s, %d ms%s).",
                        f"translation to {translate_to}" if translate_to else "polish",
                        polish_status,
                        polish_provider or "no provider",
                        polish_latency_ms,
                        f", {result.reason}" if result.reason else "",
                    )
                else:
                    polish_status = "off"
            except Exception:  # noqa: BLE001 — never lose the text to the polish pass
                log.warning(
                    "dictation polish failed; using the unpolished transcript",
                    exc_info=True,
                )
                polish_status = "provider_error"

        # Resolve ``auto`` now: the foreground window at DELIVERY time is the
        # one the user means, not the one that happened to be in front when
        # recording started.
        #
        # Resolved BEFORE the publish below because the answer RIDES on that
        # event. The UI sees a final transcript on both routes, so without it
        # there is no way to tell "this belongs in the app" from "this is
        # already being pasted into another program" — and a UI that guessed
        # would write a dictation meant for a foreign window into whatever
        # Jarvis field last had focus.
        resolved_target = target
        if target not in ("insert", "chat"):
            try:
                from jarvis.dictation.insert import resolve_target

                resolved_target = resolve_target(target)
            except Exception:  # noqa: BLE001 — an unreadable foreground is not fatal
                log.debug("dictation target resolution failed", exc_info=True)
                resolved_target = "insert"

        formatting_ms = round((time.perf_counter() - finish_started) * 1000.0)
        notification_ms = 0
        insertion_ms = 0
        final_notification_status = "complete"
        insert_result = None

        async def notify_final() -> None:
            nonlocal notification_ms, final_notification_status
            started = time.perf_counter()
            try:
                complete = await self._publish_dictation_event(
                    DictationTranscript(
                        source_layer="speech.dictation",
                        text=cleaned,
                        is_final=True,
                        target=resolved_target,
                    )
                )
                if not complete:
                    final_notification_status = "observer_timeout"
            except Exception:
                final_notification_status = "failed"
                log.warning("dictation final publish failed", exc_info=True)
            finally:
                notification_ms = round((time.perf_counter() - started) * 1000.0)

        async def insert_external() -> None:
            nonlocal insert_result, insertion_ms
            started = time.perf_counter()
            try:
                insert_result = await asyncio.to_thread(self._insert_dictation, cleaned)
            except Exception:
                log.warning("dictation insertion failed", exc_info=True)
            finally:
                insertion_ms = round((time.perf_counter() - started) * 1000.0)

        # Healthy UI clients receive the final text even if OS insertion fails.
        # An external paste starts alongside notification: its destination must
        # not drift while an unrelated UI observer is holding up the bus.
        try:
            if resolved_target == "insert" and cleaned.strip() and not hung_up:
                await asyncio.gather(notify_final(), insert_external())
            else:
                await notify_final()
        except Exception as exc:  # noqa: BLE001
            log.warning("dictation final delivery failed: %s", exc)

        outcome_name = "chat"
        detail = ""
        method = ""
        if hung_up:
            outcome_name = "cancelled"
        elif not cleaned.strip():
            # Nothing to deliver. WHY there is nothing is the whole point of
            # this branch: a provider error is a "failed" the user can act on
            # (fix the key, switch provider), silence is just an "empty".
            outcome_name = "failed" if stt_error else "empty"
            if stt_error:
                # ``detail`` is the sentence surfaces without a locale of their
                # own show verbatim (the Jarvis Bar, the CLI) — and until now a
                # failed dictation left it empty, so the bar said nothing at all
                # about why the words never arrived. The localized half is the
                # reason CODE on ``error``, which the UI translates.
                detail = stt_failure_message(stt_error)
            elif rejected_detail:
                # Nothing arrived because what arrived was not speech. Saying so
                # is the difference between "your microphone heard nothing" and
                # an unexplained empty result after the user clearly spoke.
                detail = rejected_detail
        elif resolved_target == "insert":
            if insert_result is None:
                outcome_name = "failed"
                detail = "The text could not be inserted."
                if bool(getattr(cfg, "history_enabled", True)):
                    detail += " It is available in dictation history."
            else:
                outcome_name = insert_result.status
                detail = insert_result.detail
                method = insert_result.method

        # A dictation that delivered SOME words and permanently lost others is
        # not a success, whichever way the surviving fragment was delivered.
        # This is the maintainer's original complaint: 37.7 s of speech, a 429
        # from the provider, three words in the field, outcome ``inserted`` —
        # and because a success is not recoverable, the audio was deleted and
        # the history offered no Restore. Unrecoverable by construction.
        #
        # It is keyed on the LOSS, never on ``stt_error``: a failure on the last
        # probe tick whose tail then fell under the minimum segment size leaves
        # a stale error on a complete transcript, and degrading that would be
        # the same lie told backwards. ``hung_up`` still wins — the user
        # cancelled, and every unread second after that is their decision, not a
        # fault. An empty result stays ``failed``/``empty``: there is no
        # "partial" of nothing.
        if lost_audio_s > 0 and not hung_up and cleaned.strip():
            outcome_name = "partial"
            # Only promise Restore when the recording is actually being kept.
            # ``[dictation].keep_failed_audio`` is the user's switch, and a
            # message pointing at a button that will not be there is a worse
            # answer than the plain statement of what was lost.
            lost_note = (
                f"About {lost_audio_s:.1f}s of the recording could not be "
                "transcribed, so words are missing."
            )
            if bool(getattr(cfg, "keep_failed_audio", True)):
                lost_note += (
                    " The audio was kept — use Restore in the dictation "
                    "history to try again."
                )
            why = stt_failure_message(stt_error) if stt_error else ""
            # ``detail`` is the sentence locale-less surfaces (the Jarvis Bar,
            # the CLI) show verbatim, so the delivery detail is kept rather than
            # replaced: "words are missing" and "it went to the clipboard" are
            # both true and the user needs both. The localized half stays the
            # reason CODE on ``error``, which the UI translates.
            detail = " ".join(part for part in (lost_note, why, detail) if part)
            log.warning(
                "dictation degraded to partial: %.1fs of audio never "
                "transcribed (%s); the recording is kept for Restore.",
                lost_audio_s,
                stt_error or "unknown",
            )

        # The other hole, and the one nothing used to mention: frames the
        # CAPTURE dropped. When the event loop stalls longer than the mic
        # queue holds, ``_safe_put`` deletes the oldest frame per arrival and
        # that speech never becomes audio anyone can read — measured across 797
        # live dictations at 56 % of them losing something and one losing 7.4 s
        # out of 15.4 s, every one of them delivered as a clean success.
        #
        # Said FIRST when both holes are present: this is the unrecoverable
        # one. It also deliberately does not mention Restore — the audio it
        # would re-read is the audio that was never recorded.
        if (
            dropped_audio_s >= _DICTATION_DROPPED_AUDIO_NOTICE_S
            and not hung_up
            and cleaned.strip()
        ):
            outcome_name = "partial"
            detail = " ".join(
                part
                for part in (
                    f"About {dropped_audio_s:.1f}s of audio was lost while "
                    "recording, so words are missing.",
                    detail,
                )
                if part
            )
            log.warning(
                "dictation degraded to partial: %.1fs of audio never reached "
                "the recording (capture queue overflow) — not recoverable.",
                dropped_audio_s,
            )

        delivery_audit = (
            *stt_audit,
            f"formatting_wait_ms:{formatting_ms}",
            f"final_notification_wait_ms:{notification_ms}",
            f"insertion_wait_ms:{insertion_ms}",
            f"finish_wait_ms:{round((time.perf_counter() - finish_started) * 1000.0)}",
            f"final_notification:{final_notification_status}",
        )

        # Mark the turn closed BEFORE the publish attempt: this flag answers
        # "does the teardown still owe a terminal event", and a publish that
        # raised is not a reason to fire a second, contradictory completion.
        self._dictation_completion_published = True
        try:
            await self._publish_dictation_event(
                DictationCompleted(
                    source_layer="speech.dictation",
                    text=cleaned,
                    raw_text=raw_text,
                    outcome=outcome_name,
                    detail=detail,
                    method=method,
                    language=effective_language,
                    duration_s=duration_s,
                    removed_words=removed_words,
                    error=stt_error,
                    polish_status=polish_status,
                    polish_provider=polish_provider,
                    polish_latency_ms=polish_latency_ms,
                    stt_providers=stt_providers,
                    stt_models=stt_models,
                    detected_languages=detected_languages,
                    stt_latency_ms=max(0, int(stt_latency_ms)),
                    stt_calls=max(0, int(stt_calls)),
                    stt_errors=stt_errors,
                    stt_audit=delivery_audit,
                    audio_sample_rate_hz=max(0, int(audio_sample_rate_hz)),
                    audio_rms=max(0.0, float(audio_rms)),
                    audio_clipping_ratio=max(0.0, float(audio_clipping_ratio)),
                    audio_dropouts=max(0, int(audio_dropouts)),
                    audio_dropout_ms=max(0, int(audio_dropout_ms)),
                )
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("dictation completion publish failed: %s", exc)

        await self._record_dictation(
            raw_text=raw_text,
            cleaned=cleaned,
            language=effective_language,
            duration_s=duration_s,
            outcome_name=outcome_name,
            method=method,
            removed_words=removed_words,
            cleanup_reason=cleanup_reason,
            stt_error=stt_error,
            audio=audio,
            polish_status=polish_status,
            polish_provider=polish_provider,
            polish_latency_ms=polish_latency_ms,
            stt_providers=stt_providers,
            stt_models=stt_models,
            detected_languages=detected_languages,
            stt_latency_ms=max(0, int(stt_latency_ms)),
            stt_calls=max(0, int(stt_calls)),
            stt_errors=stt_errors,
            stt_audit=delivery_audit,
            audio_sample_rate_hz=max(0, int(audio_sample_rate_hz)),
            audio_rms=max(0.0, float(audio_rms)),
            audio_clipping_ratio=max(0.0, float(audio_clipping_ratio)),
            audio_dropouts=max(0, int(audio_dropouts)),
            audio_dropout_ms=max(0, int(audio_dropout_ms)),
        )
        return cleaned

    async def _record_dictation(
        self,
        *,
        raw_text: str,
        cleaned: str,
        language: str,
        duration_s: float,
        outcome_name: str,
        method: str,
        removed_words: int,
        cleanup_reason: str,
        stt_error: str | None,
        audio: bytes | None,
        polish_status: str = "",
        polish_provider: str = "",
        polish_latency_ms: int = 0,
        stt_providers: tuple[str, ...] = (),
        stt_models: tuple[str, ...] = (),
        detected_languages: tuple[str, ...] = (),
        stt_latency_ms: int = 0,
        stt_calls: int = 0,
        stt_errors: tuple[str, ...] = (),
        stt_audit: tuple[str, ...] = (),
        audio_sample_rate_hz: int = 0,
        audio_rms: float = 0.0,
        audio_clipping_ratio: float = 0.0,
        audio_dropouts: int = 0,
        audio_dropout_ms: int = 0,
    ) -> None:
        """Write the history row and, when warranted, the audio sidecar.

        Split out of ``_finish_dictation`` because it is the only part that
        touches the disk: the caller has already published everything the UI
        needs, so nothing in here is allowed to change what the user sees. All
        of it is best-effort — a failed history write costs the history entry
        and nothing else.

        The ``polish_*`` values are offered to the store and dropped when it
        does not want them yet — see the ``TypeError`` branch below. A history
        row is worth more than the three fields describing how it was formatted.
        """
        from jarvis.dictation.outcomes import is_recoverable

        cfg = getattr(self, "_dictation_cfg", None)
        # A dictation that produced nothing is recorded too when its outcome
        # says the user LOST something (partial / failed / cancelled / empty):
        # that row is the only place a later Restore can start from.
        if not (raw_text or is_recoverable(outcome_name)):
            return
        if not getattr(cfg, "history_enabled", True):
            return

        entry = None
        history = None
        try:
            from jarvis.dictation.history import DictationHistory

            history = DictationHistory()
            fields: dict[str, Any] = dict(
                raw_text=raw_text,
                text=cleaned,
                language=language,
                duration_s=duration_s,
                outcome=outcome_name,
                method=method,
                removed_words=removed_words,
                cleanup_reason=cleanup_reason,
                error=stt_error,
                max_entries=int(getattr(cfg, "history_max_entries", 200)),
                retention_days=int(getattr(cfg, "history_retention_days", 30)),
            )
            extra = {
                "polish_status": polish_status,
                "polish_provider": polish_provider,
                "polish_latency_ms": polish_latency_ms,
                "stt_providers": stt_providers,
                "stt_models": stt_models,
                "detected_languages": detected_languages,
                "stt_latency_ms": max(0, int(stt_latency_ms)),
                "stt_calls": max(0, int(stt_calls)),
                "stt_errors": stt_errors,
                "stt_audit": stt_audit,
                "audio_sample_rate_hz": max(0, int(audio_sample_rate_hz)),
                "audio_rms": max(0.0, float(audio_rms)),
                "audio_clipping_ratio": max(0.0, float(audio_clipping_ratio)),
                "audio_dropouts": max(0, int(audio_dropouts)),
                "audio_dropout_ms": max(0, int(audio_dropout_ms)),
            }
            try:
                entry = await asyncio.to_thread(history.add, **fields, **extra)
            except TypeError:
                # The store predates these fields. Writing the row WITHOUT them
                # is the right degradation: the user's words are the payload,
                # and "how it was formatted" is metadata about the payload. The
                # alternative — letting the TypeError fall into the handler
                # below — silently drops the whole dictation from the history,
                # which is a far worse answer to a version skew.
                log.debug(
                    "dictation history does not carry the polish fields yet; "
                    "storing the row without them."
                )
                entry = await asyncio.to_thread(history.add, **fields)
        except Exception:  # noqa: BLE001 — history is never worth a failure
            log.debug("dictation history write failed", exc_info=True)
            return

        # Audio is the most sensitive thing this application ever stores, so it
        # is written on exactly one path: the user allowed it AND the dictation
        # left them missing words (``RECOVERABLE_OUTCOMES``). Never on a plain
        # success — but ``partial`` is in that set precisely because the words
        # it did deliver are not the ones it lost.
        if (
            entry is None
            or history is None
            or not audio
            or not bool(getattr(cfg, "keep_failed_audio", True))
            or not is_recoverable(outcome_name)
        ):
            return
        try:
            from jarvis.dictation.audio import prune_audio, save_dictation_audio

            path = await asyncio.to_thread(
                save_dictation_audio, entry.id, audio, directory=history.audio_dir
            )
            if path is not None:
                await asyncio.to_thread(
                    history.update, entry.id, audio_path=str(path)
                )
            # Retention runs after the write, off the delivery path: the
            # transcript is already in front of the user by now.
            await asyncio.to_thread(
                prune_audio,
                max_files=int(getattr(cfg, "audio_max_files", 20)),
                retention_days=int(getattr(cfg, "audio_retention_days", 7)),
                directory=history.audio_dir,
            )
        except Exception:  # noqa: BLE001 — a lost recovery option, not a lost dictation
            log.debug("dictation audio sidecar failed", exc_info=True)

    def _insert_dictation(self, text: str):
        """Blocking insertion, run off the event loop. Never raises.

        ``insert_text`` sleeps around the paste chord and talks to the OS
        clipboard, so it must not run on the pipeline's loop — a 250 ms block
        there is a stutter in the voice path.
        """
        from jarvis.dictation.insert import InsertResult, insert_text

        cfg = getattr(self, "_dictation_cfg", None)
        try:
            return insert_text(
                text,
                method=str(getattr(cfg, "insert_method", "clipboard")),
                paste_chord=str(getattr(cfg, "paste_chord", "auto")),
                delay_ms=int(getattr(cfg, "paste_delay_ms", 120)),
                delay_after_ms=int(getattr(cfg, "paste_delay_after_ms", 120)),
                restore_clipboard=bool(getattr(cfg, "restore_clipboard", True)),
            )
        except Exception as exc:  # noqa: BLE001 — degrade to an honest report
            log.warning("dictation insertion failed: %s", exc, exc_info=True)
            return InsertResult(
                status="unavailable",
                detail=(
                    "The text could not be inserted here. It is kept in the "
                    "dictation history."
                ),
                clipboard_holds_text=False,
            )

    async def _session_input_stream(
        self, chunks: AsyncIterator[AudioChunk]
    ) -> AsyncIterator[AudioChunk]:
        """Filter mic frames captured during/after Jarvis TTS output."""
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
                log.info("TTS echo guard: dropped %d mic chunk(s).", dropped)
                dropped = 0
            # Realtime sessions bypass the VAD, where mic_level.feed normally
            # lives, so feed the live loudness here — after the mute and echo
            # filters, so suppressed audio honestly shows dark bars. Same
            # normalized RMS as the VAD/PTT sites; zero-cost without overlay.
            if mic_level.has_subscribers():
                samples = pcm_bytes_to_np(chunk.pcm)
                if samples.size:
                    mic_level.feed(float(np.sqrt(np.mean(np.square(samples)))))
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
        log.info("TTS echo lock armed: %.1fs (%s).", seconds, reason)

    # --- Self-echo TEXT guard (BUG-084) --------------------------------- #
    # The logic lives in jarvis.speech.echo_guard.SelfEchoGuard so the
    # realtime session shares ONE implementation (BUG-089) instead of a
    # drifting copy; these thin delegates keep the pipeline's historical
    # call sites and test surface unchanged.

    def _echo_text_guard(self) -> SelfEchoGuard:
        guard = getattr(self, "_self_echo_guard", None)
        if guard is None:
            guard = SelfEchoGuard()
            self._self_echo_guard = guard
        return guard

    def _register_assistant_speech(self, text: str) -> None:
        """Remember what Jarvis is about to voice as an echo-guard reference."""

        self._echo_text_guard().register(text)

    def _touch_assistant_speech_activity(self) -> None:
        """Stamp 'assistant audio was active around now' for the echo guard."""

        self._echo_text_guard().touch()

    def _looks_like_self_echo(self, text: str) -> bool:
        """True when ``text`` is (fuzzily) contained in Jarvis' recent speech.

        See ``SelfEchoGuard.is_echo`` for the containment contract (activity
        window, fuzzy cutoff anchors, fail-open novelty allowance).
        """

        return self._echo_text_guard().is_echo(text)

    @property
    def _assistant_speech_activity_ns(self) -> int:
        """Guard activity stamp, proxied for historical direct pokes."""

        return self._echo_text_guard().activity_ns

    @_assistant_speech_activity_ns.setter
    def _assistant_speech_activity_ns(self, value: int) -> None:
        self._echo_text_guard().touch(int(value), force=True)

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
        lasse dafuer einen Jarvis-Agent-Subagent ...") is a promise, not the
        answer -- the actual answer arrives 30-90 s later as the mission
        readback via ``JarvisAgentBackgroundCompleted``. Hanging up after the
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
        self._termination_producer = "speech.pipeline._finish_after_response.single_turn"
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

        Both of those dead ends now go through ``_transcribe_final_crossing``
        first (AP-22, F3). The retry ladder only ever asked the SAME provider
        again, so a depleted key or a rate limit that outlived it ended the turn
        even with two other keyed families sitting in the keyring — the exact
        single-provider brick the dictation lane was fixed for and the voice
        lane was not. The happy path is untouched by design: a successful call
        returns from inside the loop, and nothing about the crossover — not the
        keyring read, not the chain resolution, not a provider construction —
        happens until a turn has already failed.
        """
        # A VAD preview may still own the provider connection/inference lock on
        # the endpoint edge. Cancel and drain it before the authoritative upload
        # so the final request never queues behind obsolete audio or triggers a
        # same-provider rate-limit retry.
        await self._drain_stale_probe()
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
                    # Not retryable HERE is not the same as hopeless: a 401 or a
                    # 402 is final for THIS key and says nothing about the other
                    # families the user holds. The crossover makes that call; a
                    # 400 (the provider understood the audio and refused it) is
                    # classified as not crossable and returns None as before.
                    return await self._transcribe_final_crossing(pcm, exc)
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
        return await self._transcribe_final_crossing(pcm, last_exc)

    async def _transcribe_final_crossing(
        self, pcm: bytes, exc: BaseException | None
    ) -> Transcript | None:
        """Last resort for a final transcription: try another CREDENTIAL family.

        Reached only from the two dead ends of ``_transcribe_final``, so every
        cost in here is paid by a turn that was already lost. That is what makes
        it acceptable to read the keyring and construct a client at all — both
        run in a worker thread, because a keyring lookup on a host with a locked
        Secret Service blocks for seconds and this sits on the loop the
        microphone is drained on.

        Crossing is by FAMILY, never by model: a second id that reads the same
        key is the same 429 twice (AP-22). ``_resolve_stt_fallback_chain``
        guarantees one provider per family and honours ``[stt].fallback``, so
        the user can pin a crossover target or refuse crossing outright.

        Returns ``None`` when there is nothing to cross to, when the failure is
        one no other provider survives, or when every alternate failed too — the
        caller then speaks its apology exactly as it does today.
        """
        if exc is None:
            return None
        from jarvis.speech.stt_failure import is_crossable_failure

        reason = classify_stt_failure(exc)
        if not is_crossable_failure(reason):
            # A 400-class refusal: this provider understood the request and said
            # no. Another one would say no to the same bytes and charge the user
            # a second call for the privilege.
            log.debug("STT crossover skipped: %s is not a crossable failure.", reason)
            return None

        stt_cfg = getattr(getattr(self, "_config", None), "stt", None)
        if stt_cfg is None:
            log.debug("STT crossover skipped: no [stt] configuration to resolve from.")
            return None
        configured = str(getattr(stt_cfg, "provider", "") or "").strip()

        chain: tuple[str, ...] | None = getattr(self, "_voice_stt_fallback_chain", None)
        if chain is None:
            # Resolved once and then remembered — the EMPTY answer included, so
            # a single-key install does not re-read its keyring on every failed
            # turn. ``_reset_dictation_stt`` drops it when the provider or the
            # recognition language changes.
            chain = await asyncio.to_thread(
                _resolve_stt_fallback_chain, stt_cfg, configured
            )
            self._voice_stt_fallback_chain = chain
            log.info(
                "STT voice crossover armed: %s -> %s",
                configured or "the configured provider",
                ", ".join(chain) or "<none: this host has one keyed STT family>",
            )
        if not chain:
            return None

        instances: dict[str, Any] | None = getattr(
            self, "_voice_stt_fallback_instances", None
        )
        if instances is None:
            instances = {}
            self._voice_stt_fallback_instances = instances

        for name in chain:
            provider: Any = instances.get(name)
            if provider is None:
                try:
                    from jarvis.plugins.stt import build_named_stt_provider

                    provider = await asyncio.to_thread(
                        build_named_stt_provider, name, stt_cfg
                    )
                except Exception as build_exc:  # noqa: BLE001 — try the next family
                    log.warning(
                        "STT crossover: %s could not be built (%s); trying the "
                        "next family.",
                        name,
                        build_exc,
                    )
                    continue
                # The user's spoken-vocabulary corrections are post-STT string
                # work, and the voice provider carries them. Without this the
                # one turn that crossed families would be the one turn that
                # spelled their colleague's name wrong — a divergence nobody
                # could explain afterwards, since nothing on screen says which
                # provider answered.
                try:
                    from jarvis.speech.stt_dictionary import wrap_stt_with_dictionary

                    provider = wrap_stt_with_dictionary(provider)
                except Exception as wrap_exc:  # noqa: BLE001 — not load-bearing
                    log.warning(
                        "STT dictionary wrapper unavailable for the %s "
                        "crossover (%s); transcribing without the corrections.",
                        name,
                        wrap_exc,
                    )
                instances[name] = provider
            try:
                transcript = await asyncio.wait_for(
                    provider.transcribe_pcm(pcm), timeout=self._stt_final_timeout_s
                )
            except Exception as cross_exc:  # noqa: BLE001 — try the next family
                log.warning(
                    "STT crossover to %s failed as well (%s); trying the next "
                    "family.",
                    name,
                    cross_exc,
                )
                continue
            log.info(
                "STT crossover succeeded: %s answered after %s failed (%s).",
                name,
                configured or "the configured provider",
                reason,
            )
            return transcript
        log.error(
            "STT crossover exhausted every keyed family after %s failed (%s); "
            "this turn has no transcript.",
            configured or "the configured provider",
            reason,
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
        # The playback receipt matcher is scoped to one utterance. Clear it
        # before any early return so a later identical phrase cannot inherit
        # the previous turn's instant-ack attribution.
        self._instant_ack_spoken_text = None
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
            # Close this turn's speech buckets: one priced event per stage and
            # provider, however many sentences the reply was split into.
            recorder = getattr(self, "_speech_spend", None)
            if recorder is not None:
                recorder.flush()

    def _speech_trace(self) -> str:
        """The turn a metered speech call belongs to, or "" between turns."""
        tracker = getattr(self, "_latency_tracker", None)
        return str(getattr(tracker, "trace_id", "") or "")

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
        # Only speech the VAD reports from HERE on counts as "the user resumed"
        # for the Thinking-pause hold — a stale flag from a path that never
        # endpointed (push-to-talk, a hotkey call) must not hold this turn.
        self._vad_speech_after_endpoint = False
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
        hangup_match = HANGUP_RE.search(text)
        self._last_user_activity_monotonic = time.monotonic()
        self._termination_detail = {
            "hangup_pattern_matched": hangup_match is not None,
            "hangup_pattern": matched_hangup_pattern(text) if hangup_match else None,
            "end_call_signal": None,
            "legacy_farewell_matched": None,
        }
        if hangup_match:
            self._termination_producer = "speech.pipeline._handle_utterance_turn.explicit_hangup"
            log.info("Voice-Hangup via Regex (%r) - lege auf.", text)
            self._trigger_voice_hangup()
            return False

        # STT-Halluzinations-Guard: Whisper transkribiert bei Speaker-Leak /
        # leisem Mic manchmal Werbe-Outros, Copyright-Strings, YouTube-
        # Endcards. Diese Phrasen nie ans Brain — sonst ruft Gemini
        # open_app('WDR mediagroup GmbH im Auftrag des WDR, 2020').
        if _STT_HALLUCINATION_RE.search(text):
            log.info("🚫 STT hallucination detected (%r) — skip brain.", text[:80])
            await self._set_turn_state(TurnTakingState.LISTENING)
            return True

        # Self-echo TEXT guard (BUG-084): an utterance that is (fuzzily)
        # nothing but words Jarvis itself just voiced is speaker echo that
        # slipped the acoustic gates — answering it is how the assistant ends
        # up in a multi-turn conversation with itself. Skip the brain, keep
        # listening; a genuine user turn that adds anything new always passes.
        if self._looks_like_self_echo(text):
            log.info("🔁 Own speaker echo suppressed (%r) — skip brain.", text[:80])
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

        # Re-read this turn as prose, beside the brain rather than in front of
        # it. Deliberately HERE and not next to the ``TranscriptFinal`` publish
        # above: everything between the two is a gate that decides this
        # utterance is not worth answering — a bare wake word, a hangup, a
        # Whisper hallucination, our own speaker echo — and polishing text we
        # just decided to throw away spends a model call on nothing.
        #
        # ``transcript.text`` rather than the recombined ``text``, so the string
        # published for matching is the one the session recorder stored as this
        # turn's ``user_text``.
        self._spawn_turn_polish(transcript.text, language=lang)

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

        # Thinking-pause hold: the words are complete, but the MICROPHONE says
        # the user is already talking again (the VAD reported speech after
        # this utterance's endpoint, while the recognizer was still working).
        # Dispatching now would answer half a request and let the instant ack
        # talk over the second half; hold the text and let the next utterance
        # join it — one dispatch for the whole request. Bounded: a drain timer
        # dispatches the held text once the floor is free, and a VAD false
        # start releases it at once (``_release_mic_hold_soon``).
        if self._mic_says_user_resumed():
            self._hold_for_resumed_speech(text, lang)
            return True

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
        from jarvis.brain.factory import prepare_society_context
        from jarvis.society.intent import is_inventory_question

        await prepare_society_context()
        if getattr(self, "_ack_brain", None) is not None and not is_inventory_question(text):
            asyncio.create_task(  # noqa: RUF006 — intentional fire-and-forget
                self._spawn_flash_brain_ack(text, lang),
                name="flash-brain-ack",
            )

        # Instant acknowledgment (2026-08-17): the turn's first sign of life
        # for heavy work, decided HERE from the deterministic turn plan —
        # never after the router's first model round. See
        # jarvis/voice/instant_ack.py for the contract.
        self._arm_instant_ack(text, lang)

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
            requested_hangup = contains_end_signal(response) or is_legacy_farewell(normalized)
            self._termination_detail.update({
                "end_call_signal": contains_end_signal(response),
                "legacy_farewell_matched": is_legacy_farewell(normalized),
            })
            is_hangup = requested_hangup and supports_semantic_hangup(text)
            if requested_hangup and not is_hangup:
                log.warning(
                    "Ignored streamed brain hangup signal: the user did not "
                    "express conversation-closing intent."
                )
            if is_hangup:
                self._termination_producer = (
                    "speech.pipeline._handle_utterance_turn.streamed_end_signal"
                )
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
                self._brain_with_ack(
                    text,
                    lang,
                    consume_pending_voice_attachments=True,
                ),
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
        requested_hangup = contains_end_signal(response) or is_legacy_farewell(_normalized_raw)
        self._termination_detail.update({
            "end_call_signal": contains_end_signal(response),
            "legacy_farewell_matched": is_legacy_farewell(_normalized_raw),
        })
        is_hangup = requested_hangup and supports_semantic_hangup(text)
        if requested_hangup and not is_hangup:
            log.warning(
                "Ignored brain hangup signal: the user did not express "
                "conversation-closing intent."
            )

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
        if is_harmless_scrub_residue(scrubbed):
            # The whole turn was filler ("Tolle Frage!"), an honorific or a
            # self-reference, so the residue guard emptied it and returned the
            # generic error phrase. Nothing failed — the brain simply said
            # nothing of substance, and claiming an error would be a lie. Stay
            # silent (the pre-filters above already treat a substance-free turn
            # that way) and log what was dropped.
            log.info(
                "Output filter [%s]: the turn carried no substance (%s) — "
                "staying silent instead of speaking the error phrase: %r",
                lang,
                scrubbed.actions,
                response[:80],
            )
            await self._set_turn_state(TurnTakingState.LISTENING)
            return True
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
            self._termination_producer = "speech.pipeline._handle_utterance_turn.end_signal"
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
            # Precision-over-recall + latency doctrine (AGENTS.md intent→ACK
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

    def _mic_says_user_resumed(self) -> bool:
        """The VAD reported speech AFTER the utterance now being finalized.

        Two readings of the same microphone, either suffices: the explicit
        flag ``_on_vad_speech_start`` / ``_on_vad_silence_cancel`` raise and
        ``_on_vad_endpoint`` clears, and the turn state itself, which those
        callbacks move to USER_SPEAKING (the transcription of THIS utterance
        runs in WAITING_FOR_FINAL_TRANSCRIPT, so USER_SPEAKING at final time
        can only mean a newer start).
        """
        if bool(getattr(self, "_vad_speech_after_endpoint", False)):
            return True
        return (
            getattr(self, "_turn_state", TurnTakingState.IDLE)
            is TurnTakingState.USER_SPEAKING
        )

    def _hold_for_resumed_speech(self, text: str, lang: str) -> None:
        """Park a complete utterance until the sentence the user began finishes.

        The next final joins it through the ordinary ``ContinuationBuffer``
        path; the drain timer (deferred while the floor is held) dispatches it
        if that final never carries words; a VAD false start releases it at
        once. The turn state is left to the VAD — it already reads
        USER_SPEAKING, which is the truth.
        """
        buf = getattr(self, "_continuation_buffer", None)
        if buf is None:
            return
        try:
            buf.hold(text, language=lang)
        except Exception:  # noqa: BLE001 — a hold that fails must not lose the turn
            log.warning("Thinking-pause hold failed; dispatching as-is", exc_info=True)
            return
        # A recombine earlier this turn parked the prior dispatched text for a
        # history drop that ``_arm_continuation`` would apply at dispatch. The
        # held text WILL dispatch (joined, or drained) — outside this turn, so
        # apply the drop now; leaving it would let the brain see the prior
        # fragment twice, once as its own turn and once inside the join.
        prior = getattr(self, "_continuation_pending_drop", None)
        if prior:
            self._continuation_pending_drop = None
            brain = getattr(self, "_brain", None)
            if brain is not None and hasattr(brain, "drop_last_turn"):
                try:
                    brain.drop_last_turn(prior)
                except Exception:  # noqa: BLE001 — history hygiene never crashes the turn
                    log.debug("drop_last_turn failed (non-fatal)", exc_info=True)
        log.info(
            "⏸ Thinking-pause hold: the user is already speaking again — holding "
            "%r for the rest of the sentence",
            text[:80],
        )
        self._arm_continuation_drain(lang, delay_s=_MIC_HOLD_DRAIN_S)

    def _release_mic_hold_soon(self) -> None:
        """A promised continuation will not come (VAD false start): drain now."""
        buf = getattr(self, "_continuation_buffer", None)
        if buf is None or not buf.has_pending():
            return
        if getattr(buf, "last_reason", "") != REASON_MIC_RESUMED:
            return
        # The language the hold was armed with (always set by the hold); the
        # single resolver decides otherwise, exactly as a fresh turn would.
        lang = getattr(self, "_mic_hold_language", "")
        if not lang:
            try:
                lang = self._output_language(None, "")
            except Exception:  # noqa: BLE001 — never let a release crash the callback
                lang = ""
        log.info(
            "Thinking-pause hold: the speech that held the text was a false "
            "start — releasing it now"
        )
        self._arm_continuation_drain(lang, delay_s=_MIC_HOLD_RELEASE_S)

    def _arm_continuation_drain(self, lang: str, *, delay_s: float | None = None) -> None:
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
        matches the buffer's own discard deadline (``timeout_s``) unless the
        caller names a shorter one (the Thinking-pause hold, whose pause the
        VAD already paid).
        """
        self._cancel_continuation_drain()
        buf = getattr(self, "_continuation_buffer", None)
        if buf is None or not buf.has_pending():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        # Remember the language of the held text so a later release from a
        # sync VAD callback (no ``lang`` in hand) drains it in the same one.
        self._mic_hold_language = lang
        # buf is non-None here (checked above) and ``timeout_s`` is a guaranteed
        # property — match the drain delay to the buffer's own discard deadline.
        if delay_s is None:
            delay_s = float(buf.timeout_s)
        delay_s = max(0.05, float(delay_s))
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
            # Same delay as before: a short Thinking-pause release stays short
            # across deferrals instead of growing into the full buffer grace.
            self._arm_continuation_drain(lang, delay_s=delay_s)
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
                log.info(
                    "Pending-buffer cap (%d) exceeded — dropped oldest: %r",
                    max_frags,
                    dropped,
                )
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
        self._assistant_work_count = getattr(self, "_assistant_work_count", 0) + 1
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
                try:
                    generate_call = self._brain.generate(
                        text, consume_pending_voice_attachments=True
                    )
                except TypeError:
                    # Compatibility for small test/provider adapters that still
                    # expose the pre-modality Brain protocol.
                    generate_call = self._brain.generate(text)
                reply = await generate_call
                if reply:
                    await self._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
                    await self._speak(reply, language=lang, kind=SPOKEN_KIND_COMPLETION)
        except Exception as exc:  # noqa: BLE001 — AD-OE6: never crash the turn
            log.exception("Buffered-completion dispatch failed: %s", exc)
        finally:
            self._assistant_work_count -= 1
            self._last_answer_floor_monotonic = time.monotonic()
            try:
                await self._set_turn_state(TurnTakingState.LISTENING)
            except Exception:  # noqa: BLE001, S110
                # The dispatch failure above is already logged with its traceback.
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
        # OF-11: sentences the output filter threw away (whole-text fallback or
        # empty after scrub). Every entry is logged when it happens; the list
        # exists so the END of the turn can tell "filtered a clause out of a
        # healthy answer" from "filtered the entire answer away".
        dropped_sentences: list[str] = []
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
        # The answer as it forms, for the screen: each scrubbed sentence is
        # handed to the UI the moment the brain finishes it — seconds before
        # its audio is synthesised and SpeechSpoken confirms it. Scrubbed
        # text only, so a leaked tool call or raw repr never flashes on screen.
        reply_delta = None
        if self._bus is not None:
            from jarvis.core.text_stream import TextDeltaPublisher

            _tracker = getattr(self, "_latency_tracker", None)
            reply_delta = TextDeltaPublisher(
                self._bus,
                channel="voice",
                trace_id=getattr(_tracker, "trace_id", None),
                source_layer="speech.pipeline",
            )

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
                # Echo-guard reference (BUG-084): every voiced sentence may
                # come back through the mic as speaker echo.
                self._register_assistant_speech(sentence)
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

        async def _play_sentences() -> None:
            """Play synthesized sentences FIFO and commit confirmed text.

            The built-in player keeps its device stream open across calls, so a
            per-sentence receipt preserves voice continuity while providing an
            exact text unit for the audible transcript.
            """
            async def _sentence_chunks(
                source: asyncio.Queue,
            ) -> AsyncIterator[AudioChunk]:
                while True:
                    chunk = await source.get()
                    if chunk is None:
                        return
                    self._brain_first_frame_played = True
                    yield chunk

            while True:
                item = await sentence_channels.get()
                try:
                    if item is None:
                        return
                    channel, sentence = item

                    result = await self._player.play_chunks(_sentence_chunks(channel))
                    if self._playback_confirmed(result):
                        self._emit_spoken(sentence, lang, SPOKEN_KIND_REPLY)
                finally:
                    sentence_channels.task_done()

        async def _speak_sentence(cleaned: str) -> None:
            """Hand one ALREADY-SCRUBBED sentence to synthesis + playback."""
            nonlocal spoken_anything
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
            await sentence_channels.put((channel, cleaned))

        async def _enqueue_sentence(sentence: str) -> None:
            """Scrub one streamed sentence and queue what survives.

            OF-11: ``scrub_for_voice`` judges a WHOLE TURN. Applied per
            sentence its all-or-nothing verdicts (stack trace, raw repr, shell
            command, post-scrub residue) would splice the canned error phrase
            into the middle of an otherwise healthy answer — one short
            sentence losing its only noun was enough. So a sentence-level
            fallback is dropped here and the phrase is re-raised ONCE for the
            completed turn, and only if nothing at all reached TTS.
            """
            scrubbed = scrub_for_voice(sentence, language=lang)
            if scrubbed.actions:
                log.info(
                    "🧹 Output-Filter [stream:%s]: %s (fallback=%s)",
                    lang, scrubbed.actions, scrubbed.fallback_used,
                )
            if _is_whole_text_fallback(sentence, scrubbed):
                dropped_sentences.append(sentence)
                log.warning(
                    "🧹 Output-Filter [stream:%s]: sentence DROPPED — whole-text "
                    "fallback fired on a single sentence, actions=%s, text=%r",
                    lang, scrubbed.actions, sentence[:120],
                )
                return
            cleaned = scrubbed.cleaned.strip()
            if not cleaned:
                # Never swallow output silently (AGENTS.md §7): the answer
                # loses a clause here, so the drop has to be visible.
                dropped_sentences.append(sentence)
                log.warning(
                    "🧹 Output-Filter [stream:%s]: sentence DROPPED — empty after "
                    "scrub, actions=%s, text=%r",
                    lang, scrubbed.actions, sentence[:120],
                )
                return
            if reply_delta is not None:
                reply_delta.feed(cleaned if not reply_delta.text else f" {cleaned}")
            await _speak_sentence(cleaned)

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
                # user can give (forensic 2026-06-18). Graduated fallback drops the
                # newest attachment keyword first, preserving voice confirmation on
                # pre-attachment adapters. Older adapters that accept only
                # ``on_progress`` must still receive the stall-guard heartbeat —
                # dropping straight to the bare call here loses ``on_progress`` and
                # the no-first-frame ceiling would behead the working turn (BUG-032).
                try:
                    stream = self._brain.generate_stream(
                        text,
                        on_progress=self._mark_brain_progress,
                        allow_voice_confirm=True,
                        consume_pending_voice_attachments=True,
                    )
                except TypeError:
                    try:
                        stream = self._brain.generate_stream(
                            text,
                            on_progress=self._mark_brain_progress,
                            allow_voice_confirm=True,
                        )
                    except TypeError:
                        try:
                            stream = self._brain.generate_stream(
                                text,
                                on_progress=self._mark_brain_progress,
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
                        cut = _next_stream_sentence_break(sentence_buffer)
                        if cut is None:
                            break
                        sentence = sentence_buffer[:cut].strip()
                        sentence_buffer = sentence_buffer[cut:]
                        if sentence:
                            await _enqueue_sentence(sentence)

                # Wave 0 (omni-latency): the stream is exhausted — last token.
                if tracker is not None and brain_first_token_marked:
                    tracker.mark(LatencyPhase.BRAIN_LAST_TOKEN)
                # Final-Flush: trailing text without a closing sentence mark.
                tail = sentence_buffer.strip()
                if tail:
                    await _enqueue_sentence(tail)

                # OF-11, whole-turn half: the per-sentence drops above kept the
                # canned phrase out of a healthy answer. If they left the turn
                # completely silent, the whole-text verdict was right after all
                # — say the phrase exactly ONCE, or the user hears nothing.
                if dropped_sentences and not spoken_anything:
                    log.warning(
                        "🧹 Output-Filter [stream:%s]: whole turn filtered away "
                        "(%d sentence(s)) — speaking the fallback phrase once",
                        lang, len(dropped_sentences),
                    )
                    await _speak_sentence(
                        FALLBACK_PHRASES.get(
                            _phrase_lang(lang), FALLBACK_PHRASES["de"]
                        )
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # Brain-stream errors must not hang the consumer; the sentinel
                # in the finally guarantees playback of what was produced.
                log.exception("Brain-Stream-Produktion fehlgeschlagen: %s", exc)
            finally:
                if reply_delta is not None:
                    await reply_delta.flush(done=True)
                # End-of-turn sentinel — awaited so it lands even when the
                # channel queue is at capacity (consumer keeps draining).
                await sentence_channels.put(None)

        produce_task = asyncio.create_task(_produce(), name="tts-produce-turn")
        play_task = asyncio.create_task(_play_sentences(), name="tts-play-turn")
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
                log.info("🛑 Barge-in — stopping TTS playback")
                barged = True
                self._player.stop()
            elif (
                barge_task in done
                and not barge_task.cancelled()
                and not play_task.done()
            ):
                # Barge monitor returned WITHOUT barging (mic ended/error)
                # while the answer is still playing. Falling through to the
                # ``finally`` here used to cancel the producer + playback and
                # behead a perfectly healthy streamed answer (latent; exposed
                # once the monitor could finish fast, BUG-084). Mirror
                # ``_speak``: wait playback out under the same device-wedge
                # watchdog, still abortable by a hangup.
                tail_done = await self._await_playback(play_task, {hangup_task})
                if play_task not in tail_done:
                    if hangup_task in tail_done and not hangup_task.cancelled():
                        log.info("📵 Hangup during TTS — aborting turn")
                        barged = True
                    # else: watchdog already aborted the wedged device + logged.
                    self._player.stop()
                elif not play_task.cancelled():
                    exc = play_task.exception()
                    if exc is not None:
                        log.exception("Streaming playback error: %s", exc)
            elif play_task in done and not play_task.cancelled():
                # Whole turn played out naturally; surface any playback error.
                exc = play_task.exception()
                if exc is not None:
                    log.exception("Streaming playback error: %s", exc)
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
                except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                    # Owned playback tasks were explicitly cancelled above.
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
        # Echo-guard activity stamp (BUG-084) — unconditional: a barged turn
        # ends with NO post-TTS suppression (a real interrupter's words must
        # not be dropped), which is exactly the window where a FALSE barge's
        # echo tail becomes the next "user" turn. The text guard needs to
        # know audio was just active to catch that.
        self._touch_assistant_speech_activity()
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

    async def _brain_with_ack(
        self,
        text: str,
        lang: str,
        *,
        consume_pending_voice_attachments: bool = False,
    ) -> str:
        """Brain-Call mit optionalem Zwischen-Ack.

        Startet Brain-Call und einen ``_task_ack_delay_s``-Timer parallel.
          - Brain schneller als Timer → direkt Antwort zurueck (kein Ack, keine Latenz).
          - Timer schneller → eine zufaellige JARVIS-Start-Ack-Phrase ("Sofort.",
            "Right away." …) aus dem pre-renderten Cache abspielen, dann auf
            Brain weiterwarten. Nach Ack-Playback wieder THINKING anzeigen,
            damit der Orb den richtigen Modus hat.
        """
        generate = getattr(self._brain, "generate", None)
        if consume_pending_voice_attachments and callable(generate):
            try:
                brain_call = generate(
                    text, consume_pending_voice_attachments=True
                )
            except TypeError:
                # Compatibility for older Brain adapters without turn modality.
                brain_call = generate(text)
        else:
            brain_call = self._brain(text)
        brain_task = asyncio.create_task(brain_call, name="brain")
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
                #     (a weather question): a NON-desktop tool loop (geocode +
                #     DuckDuckGo + open-meteo, ~20 s of real work) emits no CU
                #     step, so before this the 20 s ceiling beheaded the working
                #     turn and the user heard "that took too long" + a hang-up.
                #   • ``_brain_thinking_heartbeat`` — the PRE-first-token think
                #     pulse from _run_brain_with_stall_guard. Live bug 2026-06-14
                #     16:17 (a trip-research turn): the deep brain built an
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

    def _resolve_realtime_surface_tts(self, provider: str) -> Any | None:
        """TTS for re-rendering a realtime turn locally (mode separation).

        STRICT separation (maintainer mandate 2026-07-17): resolves ONLY the
        active realtime provider's own TTS family, keyed through the realtime
        credential slots and speaking the session voice. The pipeline ``[tts]``
        instance is never a candidate — ``None`` means "keep the turn
        text-only". Cached per provider; never raises.
        """
        key = (provider or "").strip().lower()
        if not key:
            return None
        cached = getattr(self, "_realtime_surface_tts_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        try:
            from jarvis.plugins.tts import build_realtime_surface_tts

            built = build_realtime_surface_tts(self._config, key)
        except Exception:  # noqa: BLE001 — the emergency voice must never break the turn
            built = None
        if built is not None:
            # Every readback and announcement spoken DURING a realtime call
            # goes through this voice; on a realtime-first box that is nearly
            # all of the TTS spend, and it was the one voice nobody metered.
            built = meter_tts(
                built, getattr(self, "_speech_spend", None), trace_id=self._speech_trace
            )
        self._realtime_surface_tts_cache = (key, built)
        return built

    def _emit_spoken(
        self,
        text: str,
        language: str | None,
        kind: str,
        detail: str | None = None,
        tts: Any | None = None,
    ) -> None:
        """Publish one playback-confirmed phrase to the audible transcript.

        Callers own the playback receipt and invoke this only after audio was
        accepted. Normal replies and supplemental phrases share this event.
        Publishing remains fire-and-forget so the passive recorder cannot add
        latency to the voice path. Empty text and a missing bus are no-ops.
        """
        if not text or not text.strip():
            return
        bus = getattr(self, "_bus", None)
        if bus is None:
            return
        try:
            # Which voice actually spoke: every classic-path phrase renders
            # through self._tts, whose providers record their resolved voice
            # per utterance (last_voice protocol, 2026-07-17). Read after
            # playback so a fallback takeover reports the TAKEOVER voice.
            # A caller that rendered through a DIFFERENT instance (realtime
            # surface fallback) passes it in so the label stays honest.
            if tts is None:
                tts = getattr(self, "_tts", None)
            voice = getattr(tts, "last_voice", None)
            voice_provider = getattr(tts, "last_voice_provider", None) or getattr(
                tts, "name", None
            )
            event = SpeechSpoken(
                source_layer="speech.pipeline",
                text=text,
                language=(language or "de"),
                spoken_kind=kind,
                detail=(detail or None),
                voice=voice,
                voice_provider=voice_provider if voice else None,
            )
            # The audible transcript is published only after the player has
            # accepted the corresponding audio. This is the reliable end of
            # the instant-ack path; ACK_FIRST_TOKEN alone only measures when
            # the line was queued and can precede a dropped/stale announcement.
            if text.strip() == getattr(self, "_instant_ack_spoken_text", None):
                tracker = getattr(self, "_latency_tracker", None)
                if tracker is not None:
                    tracker.mark(LatencyPhase.ACK_PLAYBACK_CONFIRMED)
            asyncio.create_task(bus.publish(event))  # noqa: RUF006 — fire-and-forget
        except Exception:  # noqa: BLE001 — telemetry must never break the turn
            log.debug("SpeechSpoken emit failed", exc_info=True)

    def _playback_confirmed(self, result: object) -> bool:
        """Interpret a player receipt without breaking compatible older players.

        The built-in player returns an explicit boolean. Older compatible
        players returned ``None`` after successful consumption, so only those
        non-built-in implementations retain the legacy success interpretation.
        """
        if isinstance(self._player, AudioPlayer):
            return result is True
        return result is not False

    #: Spoken readback for a Jarvis-Agent background task that finished off the
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
        AGENTS.md "Runtime Output Language". The live pin lives on the
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
        """Speak text aloud — with a barge-in monitor.

        `language` = "de"/"en" (Whisper code) is mapped to "de-DE"/"en-US"
        and passed to TTS (the voice stays the same — Gemini voices are
        language-agnostic).

        ``kind`` tags what is being voiced for the Transcription log. The
        default ``"reply"`` is the ordinary brain answer; canned phrases pass
        their specific kind. Both are published only after playback succeeds.

        ``_barge_monitor`` runs on a separate mic instance in parallel with
        playback. When Silero VAD detects user speech there (threshold 0.8,
        3 consecutive frames, 400 ms grace period), player playback stops
        immediately. Return value: ``True`` when barged in.

        When muted (mascot doubleClick), we short-circuit: no synthesize
        call, no playback. We return ``False`` (no barge-in occurred) so
        callers that branch on ``barged`` behave consistently.
        """
        if getattr(self, "_muted", False):
            # INFO, not debug: a muted drop makes the whole turn inaudible
            # while the transcript claims Jarvis spoke — forensics must see
            # the audible gap without a debug build (live 2026-07-21 17:45).
            log.info(
                "_speak suppressed — voice muted (kind=%s, %d chars stay "
                "text-only)",
                kind,
                len(text),
            )
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
        # Track that the assistant has spoken at least once in this session.
        # Used by _emit_completeness_signal to pick earcon vs. spoken cue.
        self._session_has_assistant_spoken = True
        lang_code = self._bcp47(language)
        # Echo-guard reference (BUG-084): whatever we voice may come back
        # through the mic as speaker echo.
        self._register_assistant_speech(text)
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
            log.exception("Playback failure: %s", exc)
        finally:
            for t in (play_task, barge_task, hangup_task):
                if not t.done():
                    t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: S110
                    # Owned playback tasks were explicitly cancelled above.
                    pass
        if (
            play_task.done()
            and not play_task.cancelled()
            and play_task.exception() is None
            and self._playback_confirmed(play_task.result())
        ):
            self._emit_spoken(text, language, kind)
        # Echo-guard activity stamp (BUG-084) — unconditional: after a FALSE
        # barge (our own echo confirmed as "user speech") there is no post-TTS
        # suppression at all, so the text guard is the only thing standing
        # between the echo tail and a brain turn.
        self._touch_assistant_speech_activity()
        if not barged:
            self._suppress_session_input_after_tts("response")
        return barged

    async def _barge_monitor(
        self, *, grace_s: float = 1.5, respect_input_suppression: bool = False
    ) -> bool:
        """Listen on a second mic instance for the user speaking over Jarvis'
        TTS output. Returns ``True`` as soon as that is confirmed.

        History: barge-in used to fire almost immediately after TTS start
        (~600 ms) and truncated the answer — speaker→mic echo (user feedback
        2026-04-22). The conservative policy (1.5 s grace, Silero 0.97, 12
        consecutive frames ≈ 380 ms) fixed headphone leakage but NOT open
        laptop speakers next to a built-in mic: there the assistant's own
        voice is loud, sustained, and perfectly speech-shaped, so Silero —
        which cannot tell whose voice it hears — confirmed a "barge" against
        the assistant itself. That false confirm truncated the audible answer
        AND (because a barge keeps the session listening with no post-TTS
        suppression) let the echo tail become the next "user" turn: the
        BUG-084 self-talk loop on the Mac test machine.

        This monitor therefore reuses the shared
        ``DesktopRealtimeBargeInDetector`` (the BUG-062 realtime fix) instead
        of a bare Silero loop, gaining its two defenses:

        - the static RMS energy pre-gate (quiet frames never reach ONNX), and
        - the BUG-084 adaptive echo floor, calibrated per answer from the
          grace-window frames — which during playback ARE our own echo.

        Every ``feed`` runs in a worker thread: the per-frame ONNX inference
        used to run synchronously on the voice event loop and starved the
        ~120 ms playback write batches on slow CPUs (BUG-062 cause #2 —
        constant stutter / multi-second pauses on the Intel-Mac test machine).
        """
        from jarvis.realtime.desktop import DesktopRealtimeBargeInDetector

        detector = DesktopRealtimeBargeInDetector(
            grace_s=grace_s,
            output_active=level_tap.playback_active,
        )
        try:
            # Model load off the event loop — it shares the turn with live
            # audio playback.
            await asyncio.to_thread(detector.warmup)
        except asyncio.CancelledError:
            return False
        detector.start_output()

        try:
            async with MicrophoneCapture(
                device=self._input_device,
                max_queue_chunks=REALTIME_QUEUE_CHUNKS,
                device_priority=self._input_priority,
            ) as mic:
                async for chunk in mic.stream():
                    # Honour the global mute (orb double-click → _muted=True):
                    # while muted the user has told us to stop listening, so a
                    # barge must never fire on this second mic — mirrors
                    # _activation_allowed / _speak, which both short-circuit on
                    # _muted. Without this the thinking-interrupt monitor aborted a
                    # muted-but-still-working turn (live bug 2026-07-01).
                    if getattr(self, "_muted", False):
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
                            continue
                    if await asyncio.to_thread(detector.feed, chunk.pcm) is not None:
                        return True
        except asyncio.CancelledError:
            return False
        except Exception as exc:  # noqa: BLE001
            log.warning("Barge-in monitor error: %s", exc)
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
    # The env override stays available for quick CLI tests.
    env_voice = os.environ.get("JARVIS_TTS_VOICE")
    if env_voice and hasattr(tts, "_default_voice"):
        tts._default_voice = env_voice
    from jarvis.brain.factory import build_default_brain
    brain = build_default_brain()

    _call_hk, _ptt_hk = config.trigger.resolve_hotkeys()
    pipeline = SpeechPipeline(
        call_hotkeys=_call_hk,
        ptt_hotkeys=_ptt_hk,
        dictate_hotkeys=(
            (config.trigger.hotkey_dictate,)
            if config.trigger.hotkey_dictate.strip()
            else ()
        ),
        dictate_toggle_hotkeys=(
            (config.trigger.hotkey_dictate_toggle,)
            if config.trigger.hotkey_dictate_toggle.strip()
            else ()
        ),
        paste_last_hotkeys=(
            (config.trigger.hotkey_paste_last,)
            if config.trigger.hotkey_paste_last.strip()
            else ()
        ),
        pet_toggle_hotkeys=(
            (config.trigger.hotkey_pet_toggle,)
            if config.trigger.hotkey_pet_toggle.strip()
            else ()
        ),
        dictate_mode=config.dictation.mode,
        dictation_config=config.dictation,
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
        # Mirror the production wiring (desktop_app / watchdog): the config
        # field is canonical; without this line the constructor default
        # (False) silently overrides the shipped conversation-mode default.
        continue_listening_after_response=not config.trigger.single_turn_mode,
        idle_timeout_s=config.trigger.session_idle_timeout_s,
        input_device=config.audio.input_device or None,
        output_device=config.audio.output_device or None,
    )
    print()
    print("=" * 64)
    print("  Personal Jarvis — Speech pipeline")
    print("=" * 64)
    print("  CALL    :  say your wake word           |  Ctrl+RightAlt+J  |  F3+F4")
    # i18n-allow: quoted German hang-up trigger phrase
    print("  HANG UP :  say 'auflegen'               |  F1+F2")
    print("  QUIT    :  Ctrl+C in the terminal")
    print()
    print("  When Jarvis hears you: chime + a short spoken ack back.")
    print("  The live score log shows whether the wake word triggers.")
    print("=" * 64)
    print()
    await pipeline.run()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\nBeendet.")
