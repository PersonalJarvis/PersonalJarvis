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
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import numpy as np

from jarvis.audio.capture import MicrophoneCapture, pcm_bytes_to_np
from jarvis.audio.chime import CHIME_PCM, CHIME_SAMPLE_RATE, DISCONNECT_PCM, READY_PCM
from jarvis.audio.device_init import wait_for_stable_audio_devices
from jarvis.audio.player import AudioPlayer
from jarvis.audio.vad import VAD_FRAME_SAMPLES, SileroEndpointer
from jarvis.audio.vad_reasons import FORCED_CUT_REASONS
from jarvis.brain.output_filter import scrub_for_voice
from jarvis.core.events import (
    AnnouncementRequested,
    AudioOutFirst,
    BrainTTFT,
    DictationTranscript,
    ListeningStarted,
    OpenClawAnnouncement,
    OpenClawBackgroundCompleted,
    TranscriptFinal,
    TranscriptionUpdate,
    UtteranceCaptured,
    VoiceMuteChanged,
    VoiceMuteToggleRequested,
    VoiceSessionEnded,
    VoiceSessionStarted,
    WakeWordDetected,
)
from jarvis.core.protocols import AudioChunk, Transcript
from jarvis.plugins.stt.fwhisper import FasterWhisperProvider
from jarvis.plugins.tts.gemini_flash_tts import GEMINI_TTS_SAMPLE_RATE, GeminiFlashTTS
from jarvis.plugins.wake.openwakeword_provider import OpenWakeWordProvider
from jarvis.sessions.constants import (
    HANGUP_ERROR,
    HANGUP_HOTKEY,
    HANGUP_IDLE_TIMEOUT,
    HANGUP_SHUTDOWN,
    HANGUP_TURN_COMPLETE,
    HANGUP_VOICE_PATTERN,
)
from jarvis.skills.schema import SkillDirectTriggered
from jarvis.skills.skill_context import try_get_skill_context
from jarvis.skills.trigger_matcher import TriggerMatcher
from jarvis.speech.completeness import (
    Completeness,
    classify_completeness,
)
from jarvis.speech.completion import is_cancel, is_incomplete
from jarvis.speech.continuation_buffer import ContinuationBuffer
from jarvis.speech.hangup import (
    HANGUP_RE,
    contains_end_signal,
    is_legacy_farewell,
)
from jarvis.speech.pending_buffer import PendingPromptBuffer
from jarvis.speech.persona import PhrasePicker, iter_all_start_ack
from jarvis.speech.rolling_whisper_wake import RollingWhisperWake
from jarvis.speech.wake_verifier import verify_wake_with_stt
from jarvis.telemetry.latency import LatencyPhase, LatencyTracker
from jarvis.trigger.hotkey import HotkeyTrigger

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus
    from jarvis.state.supervisor import Supervisor


log = logging.getLogger("jarvis.speech.pipeline")

# Long-dictation accumulation guardrails. When the VAD force-cuts a long
# continuous utterance (reason in FORCED_CUT_REASONS), the pipeline buffers
# the PCM fragments and only finalizes at a natural endpoint. These caps stop
# a stuck mic / endless speaker-bleed from accumulating forever.
_MAX_CARRY_SECONDS = 60.0
_MAX_CARRY_PCM_BYTES = 16_000 * 2 * 60  # 16 kHz * int16 * 60 s ≈ 1.9 MB


BrainCallback = Callable[[str], Awaitable[str]]


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
        "Entschuldige, Alex — ich erreiche gerade keines meiner Sprachmodelle. "
        "Bitte prüf kurz, ob bei den Anbietern noch Guthaben ist."
    ),
    "en": (
        "Sorry, Alex — I can't reach any of my language models right now. "
        "Please check whether your providers still have credit."
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
}

# AD-OE6 zero-silent-drop fallback for a brain TURN that times out. Live bug
# 2026-05-29: "kannst du Claude Code öffnen" stalled the Gemini stream; the
# brain-timeout path returned to LISTENING in SILENCE (and idle_timeout
# pre-empted brain_timeout, so the turn just hung up with no feedback). Short,
# bilingual, TTS-clean (``_speak`` does not scrub).
_BRAIN_TIMEOUT_PHRASE: dict[str, str] = {
    "de": "Das hat gerade zu lange gedauert, Alex. Sag es bitte noch einmal.",
    "en": "That took too long just now, Alex. Could you say it again?",
}

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

# STT-Halluzinations-Marker: typische YouTube-Endcards, Werbe-Outros,
# Copyright-Strings die Whisper bei leerem Mic oder Speaker-Leak
# manchmal "halluziniert". Blockiert vor dem Brain-Call.
_STT_HALLUCINATION_RE = re.compile(
    r"\b("
    r"im\s+auftrag\s+des|"
    r"untertitel\s+(von|der|im\s+auftrag)|"
    r"untertitelung\s+des\s+(zdf|wdr|ndr|swr|br|ard|arte)"
    r"(\s+(fuer|für|fur)\s+funk)?(\s*,?\s*\d{4})?|"
    r"(eine\s+)?(sendung|produktion|redaktion|programm)\s+"
    r"(des|der|von)\s+(zdf|wdr|ndr|swr|br|ard|arte)"
    r"(\s*,?\s*\d{4})?|"
    r"(zdf|wdr|ndr|swr|br|ard|arte)\s+"
    r"(fernsehen|mediagroup|rundfunk)(\s*,?\s*\d{4})?|"
    r"(norddeutscher|westdeutscher|bayerischer)\s+rundfunk|"
    r"mediagroup|"
    r"abonnier(e|t|en)?\s+(den|meinen)\s+kanal|"
    r"thanks\s+for\s+watching|"
    r"thank\s+you|"
    r"vielen\s+dank|"
    r"mm-?hmm|"
    r"please\s+subscribe|"
    r"copyright\s+\d{4}|"
    r"all\s+rights\s+reserved|"
    r"www\.|https?://"
    r")\b",
    re.IGNORECASE,
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
    if lang.lower().startswith("de"):
        return "Mir geht's gut, Alex. Was machen wir als Naechstes?"
    return "I'm good, Alex. What's next?"


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
        wake_keywords: tuple[str, ...] = ("hey_jarvis",),
        wake_threshold: float = 0.10,
        stt: FasterWhisperProvider | None = None,
        tts: GeminiFlashTTS | None = None,
        wake: OpenWakeWordProvider | None = None,
        brain_callback: BrainCallback | None = None,
        vad_silence_ms: int = 1200,   # User-Feedback 2026-04-22 (2): 350ms schnitt bei Denkpausen ab. 1200ms erlaubt Atempausen. Kurze Commands wie 'auflegen' bleiben schnell, weil HANGUP_RE vor dem Brain-Call greift.
        stt_final_timeout_s: float = 8.0,
        # Hard cap on a single brain call. Without this, a stalled provider
        # (Gemini hang, OAuth refresh stuck, network blip) leaves the
        # pipeline forever in PROCESSING — exact user-reported symptom
        # "Jarvis stopped thinking and never replied". Well above the
        # 95th-percentile Sonnet/Gemini turn (~6 s with tool use).
        # MUST be < idle_timeout_s (30 s): live bug 2026-05-29 had this at 40 s,
        # so when "Claude Code öffnen" stalled the Gemini stream, the 30 s idle
        # timeout tore the session down BEFORE this 40 s cap fired its spoken
        # fallback → silent hangup. At 25 s the timeout fires first and speaks.
        brain_timeout_s: float = 25.0,
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
        self._tts = tts or GeminiFlashTTS()
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
        self._wake_phrase_label = getattr(wake_plan, "phrase", None) or "Hey Jarvis"
        # Live-apply signal: set_wake_plan() flips this so a running
        # _run_parallel_wake aborts early and _wake_loop re-arms with the new
        # detector/model/matcher — the wake word changes WITHOUT an app restart.
        self._wake_reload_event = asyncio.Event()
        if wake is not None:
            self._wake = wake
        elif wake_plan is not None:
            self._wake = OpenWakeWordProvider(
                keywords=(wake_plan.oww_keyword,),
                activation_threshold=wake_plan.threshold,
                model_path=wake_plan.oww_model_path,
            )
        else:
            self._wake = OpenWakeWordProvider(
                keywords=wake_keywords, activation_threshold=wake_threshold
            )
        # Stays off whenever there is no local Whisper engine (lightweight
        # path), so the heartbeat reports whisper=off instead of a phantom on.
        self._whisper_wake_enabled = enable_whisper_wake and self._stt is not None
        # Rolling-Window Whisper: transkribiert alle 500ms die letzten 2.5s
        # Audio und matched das Wake-Pattern. Kein VAD-Endpoint-Dependency →
        # robust auch bei leisem Mic. RollingWhisperWake needs a local Whisper
        # engine; in the lightweight path (self._stt is None) it is always off.
        # When a wake_plan is set, its matcher drives the phrase match so a
        # custom phrase ("Computer") is detected instead of "jarvis".
        if enable_whisper_wake and self._stt is not None:
            if self._wake_matcher is not None:
                self._whisper_wake = RollingWhisperWake(
                    self._stt, pattern=self._wake_matcher
                )
            else:
                self._whisper_wake = RollingWhisperWake(self._stt)
        else:
            self._whisper_wake = None
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
        self._pending_context_flush_s = max(0.5, float(pending_context_flush_s))
        self._pending_flush_task: asyncio.Task[None] | None = None
        self._vad = SileroEndpointer(
            silence_ms=vad_silence_ms,
            # Hard-cap of the whole utterance. Original default was 30 s
            # which felt like an eternity when speaker bleed kept Silero
            # busy. 8 s is well above any natural single-turn user
            # utterance — the STT probe path normally fires within ~2.5 s,
            # so this cap is the last safety net, not the primary path.
            # User feedback 2026-05-09: 12 s still felt "way too long" in
            # the very rare cases where neither silence nor probe fired.
            max_utterance_s=8,
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
        self._probe_required_stable: int = 1
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
        self._player = AudioPlayer(device=output_device, bus=bus)
        # Kept so warm-up can re-resolve the output device against a freshly
        # re-enumerated PortAudio table (post-reboot idx-drift cure, BUG-014).
        self._output_device = output_device
        self._input_device = input_device
        self._idle_timeout_s = idle_timeout_s
        self._post_tts_listen_suppression_s = post_tts_listen_suppression_s
        self._input_suppressed_until_ns: int = 0
        self._continue_listening_after_response = continue_listening_after_response
        self._session_end_reason: str | None = None
        self._ack_phrase = ack_phrase

        self._state = PipelineState.IDLE
        self._call_event = asyncio.Event()
        self._hangup_event = asyncio.Event()
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
        self._last_wake_keyword: str = ""
        # 2026-05-26: timestamp of the last priority="interrupt"
        # announcement, used by ``_on_announcement`` to gate preamble-class
        # announcements that would otherwise produce cross-surface voice
        # incoherence.  See diagnosis in
        # docs/plans/voice-phrase-mismatch-2026-05-26/README.md and the
        # ``suppress_preamble_after_interrupt_ms`` knob on AckBrainConfig.
        self._last_interrupt_announcement_ts: float | None = None
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
        self._spawn_watchdog_delay_s: float = 90.0

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
                OpenClawBackgroundCompleted, self._on_background_completed
            )
            # Iron-Man-Style Spawn-Ansage: dynamisch aus action/target geformt.
            self._bus.subscribe(OpenClawAnnouncement, self._on_spawn_announcement)
            # Mute toggle from any trigger surface (mascot doubleClick,
            # future hotkey/REST). The handler flips ``self._muted`` and
            # republishes the authoritative state on the bus.
            self._bus.subscribe(
                VoiceMuteToggleRequested, self._on_mute_toggle_requested
            )
            # Wave 0 (omni-latency): perceived time-to-first-audio (ack OR
            # brain, whichever speaks first) feeds the per-turn latency tracker.
            self._bus.subscribe(AudioOutFirst, self._on_audio_out_first)

        # Skills-Brain-Integration: Direct-Trigger + Cron. Ohne gesetzten
        # SkillContext bleiben beide Pfade no-op.
        self._trigger_matcher: TriggerMatcher | None = None
        self._cron_task: asyncio.Task | None = None
        self._cron_stop: asyncio.Event = asyncio.Event()

        # ContinuationBuffer (Spec docs/superpowers/specs/
        # 2026-05-25-incomplete-prompt-completion-design.md): coalesces a
        # syntactically open-ended utterance (trailing comma / conjunction /
        # determiner / preposition) with the next utterance into ONE brain
        # turn. Prevents the live regression 2026-05-26 12:13 where ONE user
        # task ("Subagent spawnen, …baut, in der …beschrieben wird,") was VAD-
        # cut at the comma and the continuation triggered a SEPARATE
        # spawn_worker — producing multiple sub-agent missions for one task.
        self._continuation_buffer: ContinuationBuffer = ContinuationBuffer()

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
        self._wake_phrase_label = getattr(plan, "phrase", None) or "Hey Jarvis"
        engine = getattr(plan, "engine", "openwakeword")

        # Build a local Whisper engine on demand for the stt_match path. The
        # provider __init__ is light (the model loads lazily on first
        # transcription), so this does not block the caller.
        if getattr(plan, "needs_local_whisper", False) and self._stt is None:
            try:
                stt_cfg = getattr(self._config, "stt", None)
                if stt_cfg is not None:
                    lang = getattr(stt_cfg, "language", None)
                    lang = None if lang in ("", "auto") else lang
                    self._stt = FasterWhisperProvider(
                        model=getattr(stt_cfg, "model", "distil-large-v3"),
                        device=getattr(stt_cfg, "device", "cuda"),
                        compute_type=getattr(stt_cfg, "compute_type", "int8_float16"),
                        language=lang,
                    )
                else:
                    self._stt = FasterWhisperProvider()
                if self._probe_stt is None:
                    self._probe_stt = self._stt
                log.info("Wake-Live-Switch: built local Whisper for custom phrase.")
            except Exception as exc:  # noqa: BLE001 — degrade, never crash the switch
                log.warning("Wake-Live-Switch: local Whisper build failed: %s", exc)

        if engine in ("openwakeword", "custom_onnx"):
            self._wake = OpenWakeWordProvider(
                keywords=(plan.oww_keyword,),
                activation_threshold=plan.threshold,
                model_path=plan.oww_model_path,
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
            self._openwakeword_enabled = False
            if self._stt is not None:
                self._whisper_wake = RollingWhisperWake(
                    self._stt, pattern=self._wake_matcher
                )
                self._whisper_wake_enabled = True
            else:
                # No local Whisper available: cannot detect an arbitrary phrase.
                self._whisper_wake_enabled = False
                log.warning(
                    "Wake-Live-Switch: stt_match requested but no local Whisper; "
                    "wake detection is now inactive until restart."
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

    async def _set_turn_state(self, new_state: TurnTakingState) -> None:
        previous = getattr(self, "_turn_state", TurnTakingState.IDLE)
        if previous != new_state:
            log.info("turn-state: %s -> %s", previous.value, new_state.value)
        self._turn_state = new_state
        await self._transition(self._supervisor_state_for_turn(new_state))

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

    def _schedule_turn_state(self, state: TurnTakingState) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._set_turn_state(state), name=f"turn-state-{state.value}")

    def _on_vad_speech_start(self) -> None:
        log.info("voice activity start")
        self._schedule_turn_state(TurnTakingState.USER_SPEAKING)

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

    def _reset_probe_state(self) -> None:
        self._probe_last_text = ""
        self._probe_live_text = ""
        self._probe_stable_count = 0
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
            tail_is_empty = (
                not text
                or len(text) < self._probe_min_text_len
                or _STT_HALLUCINATION_RE.search(text) is not None
            )
            if tail_is_empty:
                if not tail_loud:
                    # Quiet empty tail = the user paused to think, not speaker
                    # bleed. Do NOT bypass silence_ms via request_endpoint();
                    # defer to the natural silence endpoint so the user keeps
                    # the floor (the "no time to think" bug, 2026-05-25). The
                    # relative-silence calibration guarantees the silence timer
                    # is already accumulating, so the turn will still end.
                    log.info(
                        "STT probe: quiet empty tail (text=%r) → defer to silence (user may continue)",
                        text[:40],
                    )
                    return
                log.info(
                    "STT probe: empty tail (text=%r conf=%.2f) → force endpoint",
                    text[:40],
                    confidence,
                )
                self._vad.request_endpoint()
                self._reset_probe_state()
                return

            # Signal 2: identical to last tail → nothing new arrived.
            self._probe_live_text = _merge_partial_transcript(
                getattr(self, "_probe_live_text", ""),
                raw_text,
            )
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
        if new_value:
            try:
                self._player.stop()
            except Exception:  # noqa: BLE001
                log.debug("player.stop on mute swallowed", exc_info=True)
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
        """Pre-Brain-Hook: Voice-Pattern-Match auf installierte Skills.

        Returns True wenn ein Skill direkt getriggert + ausgefuehrt + via TTS
        beantwortet wurde — der Caller soll dann den Brain-Pfad ueberspringen.
        Returns False wenn kein Match (Brain-Pfad geht weiter wie bisher).
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

        # Letzte non-empty Capture-Group ist der "Inhalt" (z.B. der Tail
        # nach dem Trigger-Wort: "merk dir: <content>"). Skills wie
        # memory-save erwarten {{content}} im Jinja-Render-Context.
        content = ""
        groups = regex_match.groups()
        for grp in reversed(groups):
            if grp and grp.strip():
                content = grp.strip()
                break

        log.info("Skill direkt-getriggert: '%s' fuer '%s'", matched.name, text)
        await self._emit_skill_direct(matched.name, "voice_direct")
        await self._set_turn_state(TurnTakingState.PROCESSING)
        try:
            result = await skill_ctx.runner.run(
                matched,
                args={
                    "_trigger": "voice_direct",
                    "utterance": text,
                    "content": content,
                    "detected_language": lang if lang in ("de", "en") else "unknown",
                },
            )
            if result.success:
                body = (result.rendered_body or "").strip()
                summary = body[:400] if body else "Skill ausgefuehrt."
            else:
                summary = result.error or "Skill konnte nicht ausgefuehrt werden."
        except Exception as exc:  # noqa: BLE001
            log.exception("Skill-Direct-Run fehlgeschlagen: %s", exc)
            summary = "Skill konnte nicht ausgefuehrt werden."
        await self._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
        await self._speak(summary, language=lang)
        await self._set_turn_state(TurnTakingState.LISTENING)
        return True

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
        """Cron-Scheduler fuer Skills (Phase Skills-1).

        Laeuft als parallele asyncio-Task, yielded Skill wenn ein Cron-Trigger
        feuert, fuehrt Skill ohne Brain-Pfad aus. Kein TTS-Echo direkt — wenn
        ein Cron-Skill was sagen will muss er ``AnnouncementRequested`` emitten.
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
                    await self._emit_skill_direct(skill.name, "cron")
                    result = await ctx.runner.run(skill, args={"_trigger": "cron"})
                    log.info(
                        "Cron-Skill '%s' completed: success=%s",
                        skill.name, result.success,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.exception("Cron-Skill '%s' failed: %s", skill.name, exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("Skill-Cron-Loop crashed: %s", exc)

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
        if hangup is not None and hangup.is_set():
            log.info(
                "Announcement nach Hangup unterdrückt: %r", event.text[:80]
            )
            return
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
        is_preamble = getattr(event, "kind", None) == "preamble"
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
        ann_lang = (event.language or "de").lower()
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
        try:
            lang_code = None
            if event.language:
                lang_code = {"de": "de-DE", "en": "en-US", "es": "es-ES"}.get(
                    event.language.lower()
                )
            try:
                chunks = self._tts.synthesize(scrubbed.cleaned, language_code=lang_code)
            except TypeError:
                chunks = self._tts.synthesize(scrubbed.cleaned)
            await self._player.play_chunks(chunks)
        except Exception as exc:  # noqa: BLE001
            log.warning("Announcement-Speak fehlgeschlagen: %s", exc)

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
            try:
                async for sentence in run_stream(utterance, language=language):
                    if not sentence:
                        continue
                    # Gate: brain already speaking/done -> ack is redundant, stop.
                    if self._turn_state in (
                        TurnTakingState.JARVIS_SPEAKING,
                        TurnTakingState.LISTENING,
                        TurnTakingState.IDLE,
                    ):
                        log.info(
                            "Flash-Brain ack suppressed — brain already speaking "
                            "(state=%s)",
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
                if self._turn_state in (
                    TurnTakingState.JARVIS_SPEAKING,
                    TurnTakingState.LISTENING,
                    TurnTakingState.IDLE,
                ):
                    log.info(
                        "Flash-Brain ack suppressed — brain answered "
                        "faster than %d ms (state=%s)",
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
        self, event: OpenClawBackgroundCompleted
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
        # Hangup-Gate: an OpenClaw mission that completes after the user
        # hung up keeps its result (UI/event-store), but the voice readback
        # is dropped. The mission itself ran in its own subprocess + Job
        # Object — hangup never killed it, only mutes the readback.
        if self._hangup_event.is_set():
            log.info(
                "Background-completed nach Hangup unterdrückt (success=%s)",
                event.success,
            )
            return
        if event.success and event.summary:
            summ = event.summary.strip()
            if len(summ) > 200:
                summ = summ[:200].rsplit(" ", 1)[0] + "…"
            text = f"Fertig. {summ}"
        elif event.success:
            text = "Fertig."
        else:
            err_short = (event.error or "unbekannter Fehler")[:80]
            text = f"Das hat nicht geklappt. {err_short}"
        # Defense-in-Depth: Summary/Error kann aus dem OpenClaw-Pfad kommen
        # und Engineering-Tokens (Sub-Agent, Subprocess, MCP) enthalten.
        # scrub_for_voice filtert die raus, sonst leakt Worker-Mechanik
        # in den Voice-Kanal (vgl. Mandat-Pfad #2 Output-Filter).
        scrubbed = scrub_for_voice(text, language="de")
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
        # Laufendes Playback stoppen damit die Ansage prompt durchkommt.
        try:
            self._player.stop()
        except Exception as exc:  # noqa: BLE001
            log.warning("Player-Stop vor Background-Ansage fehlgeschlagen: %s", exc)
        try:
            try:
                chunks = self._tts.synthesize(cleaned, language_code="de-DE")
            except TypeError:
                chunks = self._tts.synthesize(cleaned)
            await self._player.play_chunks(chunks)
        except Exception as exc:  # noqa: BLE001
            log.warning("Background-completed Voice-Ansage failed: %s", exc)

    async def _on_spawn_announcement(self, event: OpenClawAnnouncement) -> None:
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
        # CRIT-5 (User-Wahl 2026-05-17): schedule a long-mission watchdog
        # so the user gets a single "Bin noch dran." after 90 s if the
        # mission has not completed. _on_background_completed cancels
        # this in the happy path.
        self._schedule_spawn_watchdog()
        return

    def _schedule_spawn_watchdog(self) -> None:
        """Start a 90 s timer that emits one discrete progress phrase if
        the OpenClaw mission has not completed yet. FIFO-cancelled by
        ``_on_background_completed``."""
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            self._spawn_watchdog_body(),
            name=f"spawn-watchdog-{len(self._spawn_watchdog_tasks)}",
        )
        self._spawn_watchdog_tasks.append(task)

    def _live_spawn_watchdogs(self) -> list[asyncio.Task[None]]:
        """Drop finished spawn-watchdog tasks; return the still-live ones.

        A watchdog counts as a "background mission in flight" only while it is
        still counting down toward its single progress phrase. Once it has fired
        (or been cancelled) it is ``done()`` and must no longer hold the voice
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

    async def _spawn_watchdog_body(self) -> None:
        """Sleep ``_spawn_watchdog_delay_s`` then fire one progress phrase.

        Quietly exits on CancelledError (happy path: mission finished
        before the timer fired). Respects the global voice mute --
        muted users get no surprise speech.

        On EVERY terminal path (fired, muted-skip, bus-None, cancelled) the task
        removes itself from ``_spawn_watchdog_tasks``. That list is the
        "background mission in flight" signal read by ``_active_session``'s
        idle-timeout override and by ``_finish_after_response``; a
        done-but-still-listed task would hold the voice session open forever,
        because the success path never publishes the
        ``OpenClawBackgroundCompleted`` event that would otherwise drain it.
        """
        try:
            try:
                await asyncio.sleep(self._spawn_watchdog_delay_s)
            except asyncio.CancelledError:
                return
            if getattr(self, "_muted", False):
                log.debug("Spawn-watchdog: muted, skipping progress phrase")
                return
            if self._bus is None:
                return
            log.info(
                "Spawn-watchdog: mission >%.0fs ohne Completion — sage 'Bin noch dran.'",
                self._spawn_watchdog_delay_s,
            )
            try:
                await self._bus.publish(
                    AnnouncementRequested(
                        text="Bin noch dran.",
                        language="de",
                        priority="normal",
                    )
                )
            except Exception:  # noqa: BLE001
                log.warning(
                    "Spawn-watchdog: AnnouncementRequested publish failed",
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
                tasks_to_cancel: list[asyncio.Task] = [hotkey_task]
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

    async def _warmup(self) -> None:
        log.info("Warm-up: Whisper / Silero / Wake-Word / TTS …")
        # Settle the audio device table BEFORE any stream opens — a too-early
        # autostart otherwise pins a partial PortAudio enumeration for the
        # whole process and the wake chime / TTS evaporate (BUG-014 class,
        # 2026-05-25). Runs first so the mic + speaker resolve correctly.
        await self._stabilize_audio_devices()
        # Lightweight path has no local Whisper to pre-load.
        if self._stt is not None:
            self._stt._ensure_model()
        self._vad._ensure_model()
        if self._openwakeword_enabled:
            await self._wake.start()
        self._tts._ensure_client()
        # ACK pre-rendern — bei leerer Phrase skippen (User-Wunsch: keine
        # gesprochene Wake-Reaktion, nur der Chime). Gemini-TTS mit "" wuerde
        # sowieso einen API-Fehler werfen.
        if self._ack_phrase:
            try:
                log.info("Pre-rendere ACK-Phrase '%s' …", self._ack_phrase)
                chunks: list[AudioChunk] = []
                async for c in self._tts.synthesize(self._ack_phrase):
                    chunks.append(c)
                self._ack_pcm = b"".join(c.pcm for c in chunks)
                log.info("ACK-Phrase gecached (%d KB).", len(self._ack_pcm) // 1024)
            except Exception as exc:  # noqa: BLE001
                log.warning("ACK pre-render fehlgeschlagen (%s) — nur Chime als Feedback.", exc)
                self._ack_pcm = b""
        else:
            log.info("ACK-Phrase deaktiviert — nur Chime beim Wake.")
            self._ack_pcm = b""
        # Task-Ack-Phrasen pre-rendern (JARVIS-Stil: "Sofort.", "Right away." …).
        # Werden später bei langsamen Brain-Calls (>1.5s) parallel zur Rechenzeit
        # abgespielt, damit der User hörbares Feedback bekommt ohne Latenz-Aufschlag.
        await self._prerender_task_acks()
        log.info("Warm-up fertig.")
        # Audible "ready" cue: tells the user exactly when listening starts
        # after a (cold) boot, closing the warm-up race where "Hey Jarvis"
        # said too early silently does nothing.
        await self._play_ready_cue()

    async def _stabilize_audio_devices(self) -> None:
        """Wait for the audio device enumeration to settle, then re-resolve the
        output device against the now-fresh PortAudio table.

        Permanent cure for the post-reboot device-index drift (BUG-014 class):
        Jarvis can autostart before Windows finishes enumerating audio
        endpoints, freezing a partial table that points the speaker index at a
        stale/silent device. Fully guarded — must never block or break boot.
        """
        try:
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
        VPS / when no output device exists — never raises."""
        try:
            await self._player.play_pcm(READY_PCM, sample_rate=CHIME_SAMPLE_RATE)
        except Exception as exc:  # noqa: BLE001
            log.debug("Boot-Ready-Sound übersprungen (%s).", exc)

    async def _prerender_task_acks(self) -> None:
        lang_map = {"de": "de-DE", "en": "en-US"}
        phrases = iter_all_start_ack()
        log.info("Pre-rendere %d Task-Ack-Phrasen …", len(phrases))
        ok = 0
        for lang, phrase in phrases:
            try:
                chunks: list[AudioChunk] = []
                try:
                    it = self._tts.synthesize(phrase, language_code=lang_map.get(lang))
                except TypeError:
                    it = self._tts.synthesize(phrase)
                async for c in it:
                    chunks.append(c)
                pcm = b"".join(c.pcm for c in chunks)
                if pcm:
                    self._task_ack_pcm[(lang, phrase)] = pcm
                    ok += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("Task-Ack pre-render '%s' (%s) fehlgeschlagen: %s", phrase, lang, exc)
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

    def _trigger_voice_hangup(self, *, stop_player: bool = True) -> None:
        """Hard-stop the voice channel — the single hangup chokepoint.

        User intent (2026-05-20): "auflegen" is an absolute kill switch.
        No matter what Jarvis is currently saying, announcing, or queueing,
        a hangup must silence the voice channel immediately. Background
        OpenClaw missions keep running (they live in their own subprocess
        + Job Object); only their *voice readback* is suppressed via the
        ``_hangup_event`` gate on the bus-driven announcement handlers.

        ``stop_player=False`` is used when the brain itself emitted the
        farewell ("Goodbye, Alex.") — we let that final utterance play.
        """
        if stop_player:
            try:
                self._player.stop()
            except Exception as exc:  # noqa: BLE001
                log.warning("Player-Stop bei Hangup fehlgeschlagen: %s", exc)
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
        # Discard any pending continuation fragment so it can't leak into the
        # next voice session (the user has explicitly ended this one).
        try:
            self._continuation_buffer.discard()
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
        if not self._wake_listening_enabled():
            log.warning(
                "Beide Wake-Detektoren deaktiviert — Wake-Loop schlaeft permanent. "
                "Voice geht nur per Hotkey."
            )
            await asyncio.Event().wait()
        gate_blocked_logged_at = 0.0
        while True:
            if not self._activation_allowed():
                now = time.time()
                if now - gate_blocked_logged_at > 30.0:
                    log.info(
                        "Wake-Loop wartet — Activation-Gate geschlossen "
                        "(Desktop-Fenster nicht sichtbar?)."
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
                    getattr(self, "_wake_phrase_label", "Hey Jarvis"),
                )
                await self._run_parallel_wake()
            except Exception as exc:  # noqa: BLE001
                log.exception("Wake-Loop Fehler: %s", exc)
                await asyncio.sleep(0.5)

    async def _verify_oww_hit(self, pcm_snapshot: bytes) -> bool:
        """Second-stage gate: ask the utterance STT whether the few seconds
        leading up to an OpenWakeWord hit actually contained "hey/hi/hallo +
        jarv". Returns True if the strict prefix is in the transcript.

        Failure modes degrade open (return True with a warning) so that a
        misconfigured STT, a network blip, or a rate-limit response cannot
        brick the wake on a quiet hardware setup — we'd rather accept the
        occasional bare-"Jarvis" false positive than have the user shout into
        a dead listener. ``verify_wake_with_stt`` itself returns False on STT
        exceptions, which is the desired suppression behaviour for transient
        errors when an STT is wired up.
        """
        if not self._require_hey_prefix:
            return True
        # The STT re-verification exists ONLY for the jarvis family (the
        # hey_jarvis model also fires on bare "Jarvis" — BUG-009). A specific
        # pretrained model (alexa/mycroft/rhasspy) or a custom model IS its own
        # discriminator; re-transcribing with the German-pinned STT would
        # mis-spell the wake word and wrongly reject valid hits ("only Hey
        # Jarvis works"). Trust the model for those.
        plan = getattr(self, "_wake_plan", None)
        if plan is not None and not getattr(plan, "verify_prefix", True):
            return True
        if self._utterance_stt is None:
            log.warning(
                "require_hey_prefix=True but no utterance STT — accepting OWW hit"
            )
            return True
        matched, _ = await verify_wake_with_stt(
            self._utterance_stt,
            pcm_snapshot,
            matcher=getattr(self, "_wake_matcher", None),
        )
        return matched

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

        async with MicrophoneCapture(device=self._input_device) as mic:
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
                            verified = await self._verify_oww_hit(bytes(ring_bytes))
                            if not verified:
                                log.info(
                                    "🚫 WAKE verworfen — kein 'Hey'-Prefix im "
                                    "Transkript der letzten ~3 s"
                                )
                                break
                        log.info("🎙 WAKE bestätigt über %s", result)
                        if self._state == PipelineState.IDLE:
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
            self._state = PipelineState.ACTIVE
            # Reset per-session completeness signal state: a new session
            # starts "fresh" so the first INCOMPLETE gets an earcon, not a
            # spoken cue. (The spoken-cue path is for mid-conversation use.)
            self._session_has_assistant_spoken = False
            session_id = str(uuid4())
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
                # PTT is one-shot per hold — disarm before the next session so a
                # stale flag can never reroute a later wake-word session into
                # the raw-recording path.
                self._ptt_mode = False
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
                # Disconnect-Sound als hörbares Hangup-Signal
                try:
                    await self._player.play_pcm(
                        DISCONNECT_PCM, sample_rate=CHIME_SAMPLE_RATE
                    )
                except Exception:  # noqa: BLE001
                    pass
                # Cooldown setzen damit Speaker-Echo nicht sofort re-triggert
                self._wake_lock_until = time.time() + self._post_hangup_lock_s
                log.info("📵 AUFGELEGT — zurück zu IDLE (Wake-Lock %.1fs).",
                         self._post_hangup_lock_s)

    async def _play_ack(self, *, ptt: bool = False) -> None:
        """Chime + pre-rendertes 'Ja?' — Gesamtdauer ~400-600 ms.

        Push-to-talk plays ONLY the chime: the user is already holding the key
        and talking, so a spoken "Ja?" would talk over their opening words. The
        chime is immediate feedback that recording is live; speech is not.
        """
        try:
            await self._player.play_pcm(CHIME_PCM, sample_rate=CHIME_SAMPLE_RATE)
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
        if self._ptt_mode:
            return await self._ptt_session()
        async with MicrophoneCapture(device=self._input_device) as mic:
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
                            timeout=self._idle_timeout_s,
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
                        if self._live_spawn_watchdogs():
                            log.info(
                                "Idle-Timeout reached but a background mission is "
                                "in flight - keeping the voice session open."
                            )
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
        async with MicrophoneCapture(device=self._input_device) as mic:
            mic_open_at = time.monotonic()
            await self._set_turn_state(TurnTakingState.LISTENING)
            await self._publish_event(ListeningStarted(source_layer="speech"))

            async def _drain() -> None:
                # Raw capture — no _session_input_stream TTS-echo filter: PTT
                # plays only a short chime (no spoken ACK), and the user is
                # holding the key with deliberate intent to record now.
                async for chunk in mic.stream():
                    buffer.extend(chunk.pcm)

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
            async with MicrophoneCapture(device=self._input_device) as mic:

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
            or self._live_spawn_watchdogs()
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
        # -------------------------------------------------------------------
        # Wave 0 (omni-latency): anchor a fresh per-turn latency tracker at
        # utterance finalize. perf_counter marks are free; emission is
        # fire-and-forget so the hot path never blocks on telemetry.
        lat_cfg = getattr(self._config, "latency", None)
        self._latency_first_audio_marked = False
        self._latency_tracker = LatencyTracker(
            self._bus,
            uuid4(),
            enabled=getattr(lat_cfg, "enabled", True),
        )
        utt_stt_name = type(self._utterance_stt).__name__
        log.info("→ Transkribiere (%.1f KB) via %s …", len(pcm) / 1024, utt_stt_name)
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
        await self._publish_event(
            TranscriptFinal(source_layer="speech.stt", transcript=transcript)
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

        log.info("👤 User [%s]: %s", transcript.language, text)

        # Whisper-detectierte Sprache (z.B. "de", "en") → für TTS + Logging
        await self._publish_event(
            TranscriptionUpdate(
                source_layer="speech.stt",
                text=transcript.text,
                is_final=True,
            )
        )

        lang = (transcript.language or "en").lower()

        # Continuation-Buffer (Spec: incomplete-prompt completion). If this
        # utterance ends open (trailing comma / conjunction / determiner /
        # preposition), hold it and wait up to 8s for the continuation. On
        # the next complete utterance, join + dispatch as ONE brain turn.
        # Without this ONE user task fragments into multiple sub-agent
        # missions (live regression 2026-05-26 12:13 — VAD cut "…wird," and
        # the continuation triggered a SEPARATE spawn_worker). Fail-open:
        # on any classifier exception we dispatch the utterance as-is so the
        # user is never silently swallowed (AD-OE6).
        try:
            coalesced = self._continuation_buffer.process(text, language=lang)
        except Exception:  # noqa: BLE001 — fail-open by contract
            log.warning("Continuation-Buffer raised; failing open", exc_info=True)
            coalesced = text
        if coalesced is None:
            await self._set_turn_state(TurnTakingState.LISTENING)
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
                    await self._speak("Ja, Alex.", language=lang)
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
                    await self._speak("Ich sehe wieder.", language=lang)
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
                response, barged = await asyncio.wait_for(
                    self._brain_streaming(text, lang),
                    timeout=self._brain_timeout_s,
                )
            except TimeoutError:
                log.warning(
                    "Brain-Stream timed out after %.1fs — speaking fallback",
                    self._brain_timeout_s,
                )
                # AD-OE6 zero-silent-drop: a stalled brain must be SPOKEN, not
                # dropped back to LISTENING in silence (live bug 2026-05-29:
                # "Claude Code öffnen" stalled and hung up with no feedback).
                await self._speak_brain_timeout(lang)
                await self._set_turn_state(TurnTakingState.LISTENING)
                return True
            except Exception as exc:  # noqa: BLE001
                log.exception("Brain-Stream fehlgeschlagen: %s", exc)
                await self._set_turn_state(TurnTakingState.LISTENING)
                return True
            log.info("🤖 Jarvis [%s] (streamed): %s", lang, response)
            if not response.strip():
                # AD-OE6 zero-silent-drop: a *total* provider-chain failure
                # must be spoken, not swallowed (BUG-020 4th recurrence). A
                # legitimate empty (suppress_response fire-and-forget spawn)
                # stays silent — its feedback arrives over the bus.
                if self._brain_turn_failed():
                    await self._speak_brain_unavailable(lang)
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
            response = await asyncio.wait_for(
                self._brain_with_ack(text, lang),
                timeout=self._brain_timeout_s,
            )
        except TimeoutError:
            log.warning(
                "Brain-Call timed out after %.1fs — speaking fallback",
                self._brain_timeout_s,
            )
            # AD-OE6 zero-silent-drop: speak the timeout instead of silent LISTENING.
            await self._speak_brain_timeout(lang)
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
            # Kein Response → weiter zuhoeren. AD-OE6: a total provider-chain
            # failure must be spoken; a legitimate suppress_response empty
            # (fire-and-forget spawn) stays silent.
            if self._brain_turn_failed():
                await self._speak_brain_unavailable(lang)
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
                await self._brain_streaming(text, lang)
            else:
                reply = await self._brain.generate(text)
                if reply:
                    await self._speak(reply, language=lang)
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

            if use_earcon:
                # Non-blocking earcon: reuses the same CHIME_PCM that the wake
                # acknowledgment uses (imported at the top of this module).
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
                lang_key = "de" if lang.startswith("de") else "en"
                phrase = spoken_phrases.get(kind, {}).get(lang_key, "Mhm?")
                try:
                    await self._speak(phrase, language=lang_key)
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
        paraphrase_stripped = False
        brain_first_token_marked = False
        barged = False

        lang_code: str | None = None
        if lang:
            lang_code = {"de": "de-DE", "en": "en-US", "es": "es-ES"}.get(lang.lower())

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
            try:
                try:
                    gen = self._tts.synthesize(sentence, language_code=lang_code)
                except TypeError:
                    gen = self._tts.synthesize(sentence)
                async for chunk in gen:
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
            channel: asyncio.Queue = asyncio.Queue()
            synth_tasks.append(
                asyncio.create_task(_synth_into(channel, cleaned), name="tts-synth")
            )
            # Blocks once ``lookahead`` channels are outstanding — back-pressure.
            await sentence_channels.put(channel)

        async def _produce() -> None:
            nonlocal sentence_buffer, paraphrase_stripped, brain_first_token_marked
            try:
                async for chunk in self._brain.generate_stream(text):
                    if not chunk:
                        continue
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

        try:
            done, _pending = await asyncio.wait(
                {play_task, barge_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if (
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
            for t in (produce_task, play_task, barge_task, *synth_tasks):
                if not t.done():
                    t.cancel()
            for t in (produce_task, play_task, barge_task, *synth_tasks):
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

    async def _speak_brain_unavailable(self, lang: str) -> None:
        """Zero-silent-drop (AD-OE6): say out loud that the whole brain
        provider chain is down, instead of dropping back to LISTENING mute.

        Uses the curated, TTS-clean ``_BRAIN_UNAVAILABLE_PHRASE`` — the raw
        BrainManager diagnostic (URLs, "Sidebar -> API-Keys", jarvis.toml) is
        UI-only and must never be read aloud. ``_speak`` does not scrub, so the
        phrase is spoken verbatim. Failures here are swallowed: the fallback
        must never itself crash the turn.
        """
        picker_lang = "de" if lang.startswith("de") else "en"
        phrase = _BRAIN_UNAVAILABLE_PHRASE[picker_lang]
        try:
            await self._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
            await self._speak(phrase, language=picker_lang)
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
        picker_lang = "de" if lang.startswith("de") else "en"
        phrase = _STT_UNAVAILABLE_PHRASE[picker_lang]
        try:
            await self._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
            await self._speak(phrase, language=picker_lang)
        except Exception as exc:  # noqa: BLE001
            log.warning("STT-unavailable fallback speak failed: %s", exc)

    async def _speak_brain_timeout(self, lang: str) -> None:
        """Zero-silent-drop (AD-OE6) for a brain turn that timed out: say it took
        too long instead of dropping back to LISTENING mute. Mirrors
        ``_speak_brain_unavailable``. Failures here are swallowed: the fallback
        must never itself crash the turn.
        """
        picker_lang = "de" if lang.startswith("de") else "en"
        phrase = _BRAIN_TIMEOUT_PHRASE[picker_lang]
        try:
            await self._set_turn_state(TurnTakingState.JARVIS_SPEAKING)
            await self._speak(phrase, language=picker_lang)
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
        picker_lang = "de" if lang.startswith("de") else "en"
        phrase = self._phrase_picker.pick("start_ack", picker_lang)  # type: ignore[arg-type]
        pcm = self._task_ack_pcm.get((picker_lang, phrase), b"")
        if pcm:
            log.info("🎙 Task-Ack [%s]: %s", picker_lang, phrase)
        else:
            log.debug("Task-Ack Cache-Miss fuer (%s, %s)", picker_lang, phrase)
        return pcm

    async def _speak(self, text: str, language: str | None = None) -> bool:
        """Sprich Text aus — mit Barge-in-Monitor.

        `language` = "de"/"en" (Whisper-Code) wird zu "de-DE"/"en-US" gemappt
        und an TTS übergeben (voice bleibt gleich — Gemini-Voices sind
        sprachagnostisch).

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
        # Track that the assistant has spoken at least once in this session.
        # Used by _emit_completeness_signal to pick earcon vs. spoken cue.
        self._session_has_assistant_spoken = True
        lang_code: str | None = None
        if language:
            mapping = {"de": "de-DE", "en": "en-US", "es": "es-ES"}
            lang_code = mapping.get(language.lower())
        try:
            chunks = self._tts.synthesize(text, language_code=lang_code)
        except TypeError:
            chunks = self._tts.synthesize(text)

        play_task = asyncio.create_task(self._player.play_chunks(chunks), name="tts-play")
        barge_task = asyncio.create_task(self._barge_monitor(), name="barge-monitor")
        barged = False
        try:
            done, _pending = await asyncio.wait(
                {play_task, barge_task}, return_when=asyncio.FIRST_COMPLETED
            )
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
                await play_task
        except Exception as exc:  # noqa: BLE001
            log.exception("Playback-Fehler: %s", exc)
        finally:
            for t in (play_task, barge_task):
                if not t.done():
                    t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        if not barged:
            self._suppress_session_input_after_tts("response")
        return barged

    async def _barge_monitor(self) -> bool:
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
            await asyncio.sleep(1.5)
        except asyncio.CancelledError:
            return False

        detector = SileroEndpointer(speech_threshold=0.97)
        detector._ensure_model()

        try:
            async with MicrophoneCapture(device=self._input_device) as mic:
                residual = np.empty(0, dtype=np.float32)
                speech_run = 0
                async for chunk in mic.stream():
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
        hangup_hotkeys=("f1+f2",),
        wake_keywords=("hey_jarvis",),
        wake_threshold=0.15,
        stt=stt,
        tts=tts,
        brain_callback=brain,
        enable_whisper_wake=True,
        input_device=config.audio.input_device or None,
        output_device=config.audio.output_device or None,
    )
    print()
    print("=" * 64)
    print("  Personal Jarvis — Speech-Pipeline")
    print("=" * 64)
    print("  ANRUFEN :  sag 'Hey Jarvis' / 'Jarvis'  |  Ctrl+RightAlt+J  |  F3+F4")
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
