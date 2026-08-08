"""Realtime voice through a user's authenticated ChatGPT Codex session.

The adapter intentionally contains no platform API key path.  It talks only to
the local Codex app-server, which owns ChatGPT authentication, and keeps all
imports from ``jarvis.*`` lazy so plugin discovery stays off the startup path.
The app-server realtime surface is experimental. Failures may cross only to
realtime fallbacks the user configured explicitly; otherwise the session
closes honestly instead of silently entering usage-billed API voice.
"""

from __future__ import annotations

import array
import asyncio
import base64
import importlib
import inspect
import logging
import time
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

_INPUT_RATE = 24_000
_OUTPUT_RATE = 24_000
# No audible frame for this long ends the turn. It is a BACKSTOP, not the
# boundary: the terminal response item ends a turn immediately. Measured
# in-reply pauses on ChatGPT-Live reach ~720 ms, so a shorter window would
# split a single answer at every breath.
_OUTPUT_QUIESCENCE_S = 1.2
# Cap on EVERY response granted "failopen", i.e. while no local grounding
# exists. On grounded hosts nothing unsolicited plays before the call's first
# user final at all (maintainer live test 2026-08-08 killed the
# bounded-greeting compromise: the opener's remainder re-earned grants and
# the first final adopted the model's own echo-monologue as "the answer").
# An ungrounded host cannot refuse anything, so this coarse bound is the
# ONLY opposition it has — first observed against the 13.9 s demo-monologue
# class (live 2026-08-06 17:03). Bounding only the opening response left
# every later ungrounded response unbounded, and that unbounded regime is
# why the two-AIs self-talk loop survived every grounded-path fix: a build
# failure or a mid-call recognizer death handed the far end unlimited
# responses for the rest of the call. A real answer fits comfortably under
# the bound; a monologue is cut and the far end re-earns nothing by it.
_OPENING_RESPONSE_MAX_S = 6.0
# A dead local recognizer STREAM is rebuilt at most this many times per call
# before the session accepts the (bounded) ungrounded regime above. The
# rebuild is cheap by design: the STT backend is cached process-wide in
# ``jarvis.realtime.input_transcription``, so a rebuild re-attaches to the
# live engine instead of paying a model load. Two attempts cover a transient
# stream death without letting a crash-looping engine spin forever.
_RECOGNIZER_REBUILDS_MAX = 2
# Backoff before rebuild attempt N (linear in N), so an engine that dies the
# moment it starts cannot hot-loop build/die inside one call.
_RECOGNIZER_REBUILD_BACKOFF_S = 0.5
# A locally DISCARDED utterance (under the endpointer's minimum voiced
# length) keeps its response window this long. The far end heard the sound
# regardless of the local verdict and often answers it; consuming the
# generation at the discard instant refused that answer as ungrounded and
# cut it (the BUG-124 amplifier). Expired lazily - a window nobody answers
# retires on the next authorization check.
_DISCARDED_UTTERANCE_GRACE_S = 4.0
# How long after the last audible provider frame a further frame still counts
# as the REST of the same answer rather than a new response.
#
# The quiescence backstop above closes the LOCAL turn on silence; it proves
# nothing about the provider's response, because ChatGPT-Live announces no
# reliable terminal item (see ``_TERMINAL_RESPONSE_ITEMS``). Treating the
# remainder of one answer as a brand-new ungrounded response is what made the
# grounding gate refuse it, latch, and silence the session for the rest of the
# call. This window must therefore stay comfortably above the backstop, and
# stay bounded so a genuinely new response after a quiet stretch is judged
# fresh again. The entitlement it extends is cancelled outright by any
# evidence the far end ended its turn, so a self-echo response is still
# refused (see ``_close_response``).
_RESPONSE_CONTINUATION_GRACE_S = 4.0
# A response the grounding gate REFUSED is reconsidered after this much
# provider activity. Without it a single refusal kept ``response_open`` true
# forever, every later frame inherited that one verdict, and the session
# stayed deaf until the call ended.
_REJECTED_RESPONSE_MAX_S = 2.0
_NORMALIZATION_QUEUE_MAX = 128
_REMOTE_CLEANUP_TIMEOUT_S = 1.5
_TURN_INTERRUPT_TIMEOUT_S = 1.5
# ChatGPT-Live renders a generation pause as silent PCM. This USED to be
# compressed away — correct for the retired v1 protocol, where audio arrived as
# sideband deltas FASTER than realtime, so dropping silence genuinely shortened
# the wait. On v3 the audio is a live WebRTC media track: 20 ms frames arrive at
# wall clock, and a dropped frame cannot fast-forward anything — it only starves
# the permanently open output stream, which is exactly the chopped voice users
# reported (measured 2026-08-02: six cuts inside one reply). Every chunk is
# forwarded verbatim now; the peak below only classifies a chunk as AUDIBLE, so
# that the quiescence backstop is not held open forever by a track that keeps
# sending silence between turns. Energy only, never transcript content (AP-27).
_OUTPUT_AUDIBLE_PEAK = 200
# CONFIRMED LIVE (notification-dump probe, 2026-08-06,
# scripts/codex_live_probe.py --dump): ChatGPT-Live v3 announces NO items at
# all — the complete notification vocabulary observed on a real answered call
# was ``thread/realtime/{started,sdp,transcript/delta,transcript/done}``. No
# ``response.*``, no ``input_audio_buffer.*``; responses exist only as audio
# and transcripts. The quiescence backstop is therefore not a fallback but
# THE turn boundary of this transport, and the playback path treats
# backstop-derived boundaries as first-class (prebuffer-free resume,
# sequenced back-to-back responses). This frozenset stays as dead-cheap
# future-proofing: should the server ever start announcing a terminal item,
# it is consumed correctly on arrival.
_TERMINAL_RESPONSE_ITEMS = frozenset({"response.done", "response.completed"})
_RESPONSE_OPENED_ITEMS = frozenset({"response.created", "response.in_progress"})
# Bound on the one-line-per-type unknown-item log, so a chatty protocol cannot
# turn observability into log spam.
_UNKNOWN_ITEM_LOG_MAX = 32
# Keep enough provider audio to recover a missing assistant transcript without
# allowing a broken media track to grow memory forever. The core scrub gate
# already bounds how long untranscribed audio may remain pending; this larger
# adapter-side ceiling preserves a complete ordinary reply for one-shot STT.
_OUTPUT_TRANSCRIPT_RECOVERY_MAX_BYTES = _OUTPUT_RATE * 2 * 60
_OUTPUT_TRANSCRIPT_RECOVERY_TIMEOUT_S = 3.0
# A provider copy of the real input transcript can lag the local recognizer a
# little. Past this bound, a new server-side user caption with no local energy
# inside an open response is not that duplicate: it is the silence/self-echo
# turn that made ChatGPT-Live continue both sides of a conversation forever.
_UNGROUNDED_RESPONSE_GRACE_S = 3.0
# How many CONSECUTIVE final captions with neither microphone energy nor a
# fresh local utterance justify tearing the transport down. One is survivable
# and common — server-side recognizers lag, and this repo documents 3-22 s of
# it for other providers — so rebuilding on the first one ended a healthy call
# every time the far end transcribed slowly. A run of them is the loop.
_UNGROUNDED_CAPTIONS_BEFORE_REBUILD = 2
# After a LOCAL interrupt, the far end predictably reacts with echo captions of
# its own truncated speech and continuation fragments. Inside this window those
# are consequences of OUR cut, not evidence of a self-dialogue loop — counting
# them toward the rebuild threshold turned every delegation into a transport
# rebuild storm (live 2026-08-04: 11 rejections, 3 rebuilds in 43 s).
#
# There are THREE local interrupt sources, and the window must cover all of
# them: a barge-in, a delegation retiring the competing native answer, and the
# grounding gate REFUSING an automatic response. The refusal was missing, and
# it is the one that fires in a storm: each refusal cut a turn, the far end
# captioned its own truncated words back as a user turn, and two of those
# tore the transport down — the rebuild storm of 2026-08-06 17:41, where the
# invented "user" line was literally the first words of the answer we had just
# cut ("Hi! Schön").  # i18n-allow: quoted German live caption, the evidence
# See BUG-124.
_POST_INTERRUPT_GRACE_S = 3.0
# A refusal storm is the honest self-dialogue signal: captions are polluted by
# the far end's reaction to our own cuts, refusals are not. This many refused
# responses with no grounded utterance in between, inside the window below,
# means the far end is talking to itself and the transport needs replacing.
# The window makes it a RATE: a long call may legitimately refuse the odd
# stray response without ever being in a loop.
_REFUSALS_BEFORE_REBUILD = 5
_REFUSAL_STORM_WINDOW_S = 10.0
# When a response's audible audio flows while the far end's own transcript is
# still absent, the session's scrub gate holds ALL of it fail-closed — and
# ChatGPT-Live's transcripts lag their audio by SECONDS (measured live
# 2026-08-05 20:42: the first audio sat 7.4 s in the gate). After this much
# audible audio with no transcript, the LOCAL recognizer transcribes the
# captured head and the text goes out as a SHADOW delta: the scrub gate
# judges real text, the audio releases, and the provider's own transcript
# stays the only one the user sees. The retry interval spaces attempts when
# the shared recognizer is busy with the microphone stream (AP-24: its own
# non-blocking lock makes a busy engine a skip, never a wedge).
_OUTPUT_EARLY_RECOVERY_AFTER_S = 1.2
_OUTPUT_EARLY_RECOVERY_RETRY_S = 1.0
# The early pass is a latency rescue, not an unbounded retry loop. Two
# single-flight attempts cover a recognizer that was briefly busy with the
# microphone; the response boundary owns the one authoritative terminal pass.
_OUTPUT_EARLY_RECOVERY_MAX_ATTEMPTS = 2
# The shadow attempt transcribes only this much of the response head: the
# gate needs SOME clean text, not the whole reply, and the recognizer's own
# time bound scales with the audio handed in — an uncapped, still-growing
# capture buffer let a retry legally take tens of seconds.
_SHADOW_RECOVERY_MAX_BYTES = _OUTPUT_RATE * 2 * 4
# How long a trusted injection's permit may wait for the response it provokes.
# Generous relative to the far end's latency, and bounded so an injection that
# was never answered cannot authorize an unrelated response later in the call.
_TRUSTED_PERMIT_GRACE_S = 15.0
_HISTORY_MAX_ITEMS = 12
_HISTORY_MAX_CHARS = 12_000
# Local-recognizer event kinds, mirrored from
# ``jarvis.realtime.input_transcription`` rather than imported, because this
# plugin module must stay free of ``jarvis.*`` imports so plugin discovery
# never drags the app onto the startup path (same rationale as ``_pcm16_peak``).
# A kind this adapter does not know must never fall through to the transcript
# branch: an unhandled control event there wiped the far end's preview and
# published an empty transcript for a turn the user really spoke.
_SPEECH_STARTED = "speech_started"
_TRANSCRIPT_FAILED = "transcript_failed"
_SPEECH_DISCARDED = "speech_discarded"
# The experimental v1 protocol was shut off server-side with the ChatGPT-Live
# launch (every v1 start now answers "403 Voice session access denied",
# verified live 2026-08-01). v3 is the ChatGPT-Live protocol: the SERVER
# chooses the model (a client `model` field is rejected outright), the client
# only picks the voice. The legacy model id is still accepted in config as a
# no-op so older pins keep working.
_LEGACY_V1_MODEL = "gpt-realtime-1.5"
_DEFAULT_VOICE = "cove"
# Server-confirmed v3 roster (the refusal for an unknown voice lists exactly
# these nine, verified live 2026-08-01). Since codex-cli 0.147.0 the server
# publishes its live roster through ``thread/realtime/listVoices``; this
# frozenset is the explicit FALLBACK for a client or server build without
# that method (see ``_live_voice_roster``), never the primary source.
_V3_VOICES = frozenset(
    {
        "arbor",
        "breeze",
        "cove",
        "ember",
        "juniper",
        "maple",
        "sol",
        "spruce",
        "vale",
    }
)

_THREAD_BASE_INSTRUCTIONS = (
    # The thread used to be told it was a dumb pipe ("never perform actions"),
    # which is why the voice had no identity, no project knowledge and no idea
    # that actions exist. The SECURITY boundary is unchanged — the Codex agent
    # itself still never touches tools or the filesystem — but the assistant
    # is now allowed to be an assistant. Its real persona, capabilities and
    # project context arrive as developer context at the start of every call.
    "You are the live voice of the user's own assistant on an open call. "
    "Speak naturally and conversationally, in short spoken sentences — never "
    "read out lists, markup, or boilerplate. Your persona, your capabilities "
    "and the current project context are delivered as developer context at "
    "the start of this call: follow them as your own identity and knowledge. "
    "Boundary: you are the VOICE, not the executor. Never run tools, shell "
    "commands, applications, plugins, skills, web search, MCP servers, or "
    "other agents yourself, and never read or write the filesystem. When the "
    "user asks for something to be DONE, request a handoff — the user's own "
    "supervisor performs it and reports back through you. Speak only the "
    "assistant side: produce exactly one response to the latest actual user "
    "audio, then stop and wait. Never invent, quote, role-play, or supply a "
    "user reply, even if silence or speaker feedback looks like another turn. "
    "Answer in the language spoken in the latest actual user audio unless "
    "developer context explicitly pins another; never switch languages "
    "because an English paraphrase, transcription, or example appears in "
    "context. Developer messages are silent configuration, never "
    "conversation: never acknowledge, answer, restate, or mention them — "
    "not even with a single word like 'okay' or 'understood' — and never "
    "treat a configuration message's arrival as a reason to speak. The ONE "
    "exception: a developer message that opens with 'This developer message "
    "IS a request to speak.' is a delivery order — speak its content as "
    "your own reply, in your own voice. Every other developer message "
    "deserves no spoken reaction; only the user's actual speech does."
)
_THREAD_DEVELOPER_INSTRUCTIONS = (
    "Execution boundary: do not call tools, shell commands, applications, "
    "plugins, skills, web search, MCP servers, or other agents, and do not "
    "read or write the filesystem. Every action goes to the client through a "
    "handoff. Conversation itself — answering, explaining, remembering what "
    "was said, using the developer context you were given — is your job."
)
#: English names for the locales this adapter has a natural phrasing for. This
#: is COSMETIC ONLY and gates nothing: an entry simply reads better to the
#: model than a bare tag. Any locale the resolver produces — today's, and every
#: one added later — is pinned through ``_language_pin_text`` below without
#: needing an entry here (CLAUDE.md §1: no closed de/en/es table, no per-layer
#: language default).
_LANGUAGE_ENDONYMS = {
    "ar": "Arabic",
    "de": "German",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "hi": "Hindi",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "nl": "Dutch",
    "pl": "Polish",
    "pt": "Portuguese",
    "ru": "Russian",
    "tr": "Turkish",
    "zh": "Chinese",
}


def _normalized_locale(language: object) -> str:
    """Bare lowercase language subtag, or "" when there is nothing to pin."""
    tag = str(language or "").strip().lower().replace("_", "-")
    return tag.split("-", 1)[0] if tag else ""


def _language_pin_text(language: object) -> str:
    """The developer instruction that pins ONE resolved output language.

    The turn's language is decided upstream by
    ``jarvis.core.turn_language.resolve_output_language`` — this layer only
    renders it, never re-derives it. It must therefore work for every locale
    that resolver can produce, which a fixed phrase table cannot: an unlisted
    locale used to be discarded silently, so the model kept answering in
    whatever it felt like and this session's own language state went stale.
    """
    locale = _normalized_locale(language)
    if not locale:
        return ""
    name = _LANGUAGE_ENDONYMS.get(locale)
    target = f"{name} ({locale})" if name else f"the language with IETF language tag {locale!r}"
    return (
        "For every following assistant audio and text response, reply only in "
        f"{target}. This is a voice-rendering instruction, not a request to "
        "use tools or perform an action. Apply it silently — never "
        "acknowledge it in a reply."
    )


def _opening_language_hint_text(default_language: object) -> str:
    """Render the non-pinning fallback used before the first real utterance."""
    locale = _normalized_locale(default_language)
    name = _LANGUAGE_ENDONYMS.get(locale)
    if name:
        fallback = f"{name} ({locale})"
    elif locale:
        fallback = f"the language with IETF language tag {locale!r}"
    else:
        return ""
    return (
        "Reply in the language the user actually speaks. If nobody has "
        "spoken yet, or the first utterance is too short to tell, use "
        f"{fallback}. Apply this silently — never acknowledge it."
    )


def _startup_prompt(
    instructions: object,
    *,
    language: object,
    language_is_pinned: bool,
) -> str:
    """Build the complete live-model contract before media can produce audio."""
    language_instruction = (
        _language_pin_text(language)
        if language_is_pinned
        else _opening_language_hint_text(language)
    )
    return "\n\n".join(
        section
        for section in (
            _THREAD_BASE_INSTRUCTIONS,
            _THREAD_DEVELOPER_INSTRUCTIONS,
            str(instructions or "").strip(),
            language_instruction,
        )
        if section
    )


# Model-facing note that replaces the truncate client event ChatGPT-Live does
# not have. Always English: it addresses the model, never the user, and the
# spoken reply's language is pinned separately by ``_LANGUAGE_UPDATE_TEXT``.
_TRUNCATION_NOTE = (
    "The user interrupted your previous spoken answer after about {ms} ms of "
    "audio. Everything you said after that point was never heard. Do not "
    "repeat or summarize the unheard remainder; continue from the user's new "
    "input instead. This is a rendering correction, not a request to use "
    "tools or perform an action."
)
_UNGROUNDED_TURN_MESSAGES = {
    "de": (  # i18n-allow: German runtime warning selected from the resolved turn language
        "Die Realtime-Verbindung wird neu aufgebaut, "  # i18n-allow: runtime warning
        "weil eine Antwort ohne lokal bestätigte "  # i18n-allow: runtime warning
        "Spracheingabe erkannt wurde."  # i18n-allow: runtime warning
    ),
    "en": (
        "The realtime connection is being rebuilt because a response without "
        "locally grounded microphone speech was detected."
    ),
    "es": (
        "La conexión en tiempo real se está restableciendo porque se detectó "
        "una respuesta sin voz confirmada localmente."
    ),
}


def _ungrounded_turn_message(language: object) -> str:
    """User-facing rebuild notice in the resolved turn language.

    Unlike the model-facing pin above this one has to be TRANSLATED, so an
    unlisted locale falls back to ``DEFAULT_LOCALE`` rather than being
    machine-rendered — the documented behaviour for a layer that genuinely
    cannot serve a language (CLAUDE.md §1).
    """
    return _UNGROUNDED_TURN_MESSAGES.get(
        _normalized_locale(language), _UNGROUNDED_TURN_MESSAGES["en"]
    )


@dataclass(frozen=True, slots=True)
class _PcmChunk:
    pcm: bytes
    sample_rate: int
    timestamp_ns: int = 0
    channels: int = 1


@dataclass(frozen=True, slots=True)
class _ProviderEvent:
    type: str
    audio: _PcmChunk | None = None
    text: str | None = None
    is_final: bool = False
    ms_played: int | None = None
    error: str | None = None
    recoverable: bool = False
    reconnect_advised: bool = False
    item_id: str | None = None
    call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    handoff_id: str | None = None
    provider_turn_id: str | None = None
    # output_transcript_delta only: locally recovered vetting material — see
    # RealtimeEvent.shadow in jarvis/realtime/protocol.py.
    shadow: bool = False
    # input_transcript finals only: how much VOICED audio the local endpointer
    # measured for this utterance. Feeds the session's first-turn language
    # duration gate (a sub-half-second misheard fragment must not set the
    # call language). 0 = unknown; other adapters never set it.
    voiced_ms: int = 0


def _notification_parts(notification: Any) -> tuple[str, dict[str, Any]]:
    """Accept the production notification record and lightweight test fakes."""
    if isinstance(notification, dict):
        method = str(notification.get("method", "") or "")
        params = notification.get("params", {})
    else:
        method = str(getattr(notification, "method", "") or "")
        params = getattr(notification, "params", {})
    return method, params if isinstance(params, dict) else {}


def _pcm16_peak(pcm: bytes) -> int:
    """Loudest sample in a PCM16 chunk, at C speed.

    Mirrors ``jarvis.realtime.session._pcm16_peak``; copied rather than imported
    because this plugin module must stay free of ``jarvis.*`` imports so plugin
    discovery never drags the app onto the startup path.
    """
    usable = len(pcm) - (len(pcm) % 2)
    if usable < 2:
        return 0
    samples = array.array("h")
    samples.frombytes(pcm[:usable])
    return max(max(samples), -min(samples))


def _thread_id_from_result(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    thread = result.get("thread")
    if not isinstance(thread, dict):
        return ""
    return str(thread.get("id", "") or "").strip()


def _history_initial_items(history: Any) -> list[dict[str, str]]:
    """Return bounded role-bearing history for the atomic v3 session start."""
    if not isinstance(history, (list, tuple)):
        return []
    records: list[dict[str, str]] = []
    for item in history[-_HISTORY_MAX_ITEMS:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "") or "").strip().lower()
        text = str(item.get("text", "") or "").strip()
        if role not in {"user", "assistant"} or not text:
            continue
        records.append({"role": role, "text": text[:2_000]})
    while sum(len(record["text"]) for record in records) > _HISTORY_MAX_CHARS and len(records) > 1:
        records.pop(0)
    return records


def _safe_error(exc: BaseException, *, max_chars: int = 500) -> str:
    text = " ".join(str(exc).split())
    return (text or type(exc).__name__)[:max_chars]


# One process-wide WARNING when the local recognizer's constructor rejects
# the ``language`` keyword, DEBUG afterwards. The in-tree recognizer accepts
# it since 2026-08-08, so this fires only against an older out-of-tree
# module — but a swallowed capability probe at DEBUG is exactly how the
# un-pinned session language survived unreported for days (AP-30).
_language_kwarg_rejected_warned = False


def _roster_voice_names(result: Any) -> frozenset[str]:
    """Every voice name found in a ``RealtimeVoicesList`` result.

    The result carries per-protocol lists (``v1``/``v2`` today) plus default
    fields; which list feeds the live v3 surface is not documented, so every
    list-valued entry contributes. Validation stays a membership check — an
    over-broad union can only accept a voice the server itself announced.
    """
    if not isinstance(result, dict):
        return frozenset()
    names: set[str] = set()
    for value in result.values():
        if not isinstance(value, (list, tuple)):
            continue
        for entry in value:
            if isinstance(entry, dict):
                entry = entry.get("id", "") or entry.get("name", "")
            name = str(entry or "").strip().lower()
            if name:
                names.add(name)
    return frozenset(names)


async def _live_voice_roster(client: Any, thread_id: str) -> frozenset[str]:
    """The server's own realtime voice roster, or the audited static one.

    Probed as a capability, never a version string (AP-21/AP-22): a client
    without ``realtime_list_voices``, a server build that rejects the method
    (a JSON-RPC refusal surfaces with a ``code`` attribute), or an unusable
    result all degrade to ``_V3_VOICES`` — the roster the server itself
    confirmed live 2026-08-01. A failure WITHOUT an RPC error code is a
    transport failure, not a roster verdict, and propagates so the open
    fails honestly instead of validating against a guess.
    """
    lister = getattr(client, "realtime_list_voices", None)
    if not callable(lister):
        return _V3_VOICES
    try:
        result = await lister(thread_id)
    except Exception as exc:
        if getattr(exc, "code", None) is None:
            raise
        log.info(
            "Codex app-server rejected thread/realtime/listVoices (%s); "
            "using the audited static voice roster",
            _safe_error(exc),
        )
        return _V3_VOICES
    names = _roster_voice_names(result)
    if not names:
        log.info(
            "Codex app-server returned no usable realtime voice roster; "
            "using the audited static one"
        )
        return _V3_VOICES
    return names


def _client_realtime_start_accepts(client: Any, parameter: str) -> bool:
    """Whether the client's ``realtime_start`` declares ``parameter`` by name.

    A NAMED-parameter probe rather than the file's usual TypeError probe, and
    the difference is load-bearing: a client that merely takes ``**kwargs``
    would swallow the field without ever sending it, and a silently lost
    startup contract is exactly the failure a capability probe exists to
    prevent (AP-21/AP-30). Only an explicit declaration counts as support.
    """
    method = getattr(client, "realtime_start", None)
    if not callable(method):
        return False
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        return False
    return parameter in parameters


def _handoff_text(item: dict[str, Any]) -> str:
    direct = str(item.get("input_transcript", "") or item.get("inputTranscript", "") or "").strip()
    if direct:
        return direct
    active = item.get("active_transcript", item.get("activeTranscript", []))
    if not isinstance(active, list):
        return ""
    for entry in reversed(active):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("role", "") or "").strip().lower() != "user":
            continue
        text = str(entry.get("text", "") or "").strip()
        if text:
            return text
    return ""


async def _close_subscription(subscription: Any) -> None:
    close = getattr(subscription, "close", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


def _consume_cleanup_task(task: asyncio.Task[Any]) -> None:
    with suppress(asyncio.CancelledError, Exception):
        task.result()


async def _cancel_background_task(task: asyncio.Task[Any] | None) -> None:
    """Cancel and consume one locally owned startup task."""
    if task is None:
        return
    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task


async def _warm_input_transcriber(transcriber: Any) -> None:
    """Prime local STT concurrently with the independent transport startup."""
    warm = getattr(transcriber, "warm", None)
    if not callable(warm):
        return
    started_at = time.monotonic()
    await warm()
    log.info(
        "RT-SPAWN span=transcriber_warm ms=%d",
        int((time.monotonic() - started_at) * 1000.0),
    )


async def _bounded_remote_call(
    client: Any,
    method_name: str,
    thread_id: str,
    *args: str,
) -> bool:
    """Run one cleanup/control RPC without letting it hold session teardown."""
    method = getattr(client, method_name, None)
    if not callable(method):
        log.warning("Codex app-server client lacks required %s cleanup", method_name)
        return False
    task = asyncio.create_task(method(thread_id, *args))
    done, _pending = await asyncio.wait({task}, timeout=_REMOTE_CLEANUP_TIMEOUT_S)
    if not done:
        task.cancel()
        # Reap the canceled coroutine within a small second bound so it cannot
        # leak an unobserved exception into loop shutdown. A cleanup RPC that
        # suppresses cancellation still cannot hold session teardown open.
        reaped, _still_pending = await asyncio.wait({task}, timeout=0.1)
        if reaped:
            _consume_cleanup_task(task)
        else:
            task.add_done_callback(_consume_cleanup_task)
        log.warning(
            "Codex subscription %s timed out after %.1fs",
            method_name,
            _REMOTE_CLEANUP_TIMEOUT_S,
        )
        return False
    try:
        task.result()
    except Exception:  # noqa: BLE001 - cleanup continues to the next boundary
        log.warning("Codex subscription %s failed", method_name, exc_info=True)
        return False
    return True


async def _poison_client(client: Any) -> None:
    method = getattr(client, "poison", None) or getattr(client, "close", None)
    if not callable(method):
        log.error("Codex app-server cleanup failed and the client cannot be poisoned")
        return
    try:
        result = method()
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001 - containment attempt is logged explicitly
        log.error("Codex app-server poison failed", exc_info=True)


async def _cleanup_remote_thread(client: Any, thread_id: str) -> bool:
    """Stop realtime and unload its ephemeral app-server thread, in order."""
    if not thread_id:
        return True
    stopped = await _bounded_remote_call(client, "realtime_stop", thread_id)
    unsubscribed = await _bounded_remote_call(
        client,
        "thread_unsubscribe",
        thread_id,
    )
    clean = stopped and unsubscribed
    if not clean:
        await _poison_client(client)
    return clean


class _CodexSubscriptionRealtimeSession:
    supports_tool_updates = False
    supports_direct_tools = False
    creates_responses_automatically = True
    isolates_response_generations = True
    rebuild_on_transport_death = True
    direct_speech_is_authoritative = True

    def __init__(
        self,
        *,
        client: Any,
        subscription: Any,
        thread_id: str,
        answer_sdp: str,
        audio_endpoint: Any = None,
        input_transcriber: Any = None,
        input_transcriber_factory: Any = None,
        grounding_unavailable_reason: str = "",
        language: str = "en",
        voice: str = "",
        initial_context: str = "",
    ) -> None:
        self._voice = str(voice or "").strip()
        self._client = client
        self._subscription = subscription
        self._thread_id = thread_id
        self.session_id = thread_id
        self.answer_sdp = answer_sdp
        self.realtime_version = ""
        # Owns the media path: ChatGPT-Live carries audio ONLY over WebRTC.
        self._audio_endpoint = audio_endpoint
        # ChatGPT-Live emits a user transcript, but it can hallucinate captions
        # from silence or speaker echo. Local energy-gated STT is authoritative
        # for the bar and every transcript-driven Jarvis integration.
        self._input_transcriber = input_transcriber
        # Zero-arg callable producing a replacement recognizer (or ``None``)
        # for the bounded mid-call rebuild. The build itself is cheap — the
        # STT backend is cached process-wide — so a dead stream no longer has
        # to end grounding for the rest of the call.
        self._input_transcriber_factory = (
            input_transcriber_factory if callable(input_transcriber_factory) else None
        )
        # Why the recognizer BUILD failed, when it did. Non-empty means the
        # whole call runs ungrounded from the first frame and the receive
        # loop owes the user one honest surface notice for it — a WARNING in
        # the log was the only trace before, and a silently disarmed
        # grounding gate is how the self-talk loop kept coming back (AP-30).
        self._grounding_unavailable_reason = str(grounding_unavailable_reason or "").strip()
        self._closed = False
        self._last_input_item_id = ""
        self._assistant_delta_text = ""
        # The far end's own guess at what the user said. Kept as a live preview
        # and promoted to the recorded turn only if the local recognizer fails.
        self._server_user_preview = ""
        self._language = str(language or "en").strip().lower()
        self._active_codex_turn_id = ""
        self._handoff_interrupt_pending = False
        # ``send_text``/``send_speech`` intentionally create provider output
        # without a fresh microphone turn (announcements and trusted action
        # readbacks).  Each successful call arms exactly one exception to the
        # automatic-response grounding gate in ``receive``, valid only for a
        # bounded window — an injection the far end never answered must not
        # authorize an unrelated response later in the call.
        self._trusted_output_permits = 0
        self._trusted_output_permit_at = 0.0
        # Barge-in barrier. ``turn/interrupt`` only reaches an app-server TURN
        # and an ordinary ChatGPT-Live response never announces one, so the
        # local half of an interrupt is the authoritative one: bumping this
        # counter makes ``receive`` drop every remaining frame of the response
        # that was cut off. Read by the receive loop, which keeps its own copy.
        self._output_drop_barrier = 0
        # Set by ``interrupt(retire_input_entitlement=True)`` (delegation
        # paths only), consumed by the receive loop's barrier handler: the
        # delegated utterance's response entitlement is withdrawn so the far
        # end's own answer to it can never start after the cut. A plain
        # barge-in must NOT set this — at that moment the generation already
        # belongs to the user's NEW utterance.
        self._retire_entitlement_pending = False
        # Set by ``interrupt``, read and cleared by ``truncate``: the played
        # position only means something for a response that was actually cut.
        self._interrupt_pending_truncation = False
        # Last persona/context text actually delivered, so a re-issued
        # identical one is not sent again mid-call.
        self._delivered_context = str(initial_context or "").strip()
        # Last turn-scoped directive delivered whole. Tracked separately from
        # the context diff: the three per-turn directives are mutually
        # exclusive, so a change is delivered as a full REPLACEMENT block —
        # a line-diff would leave contradictory "this current turn" texts
        # standing in the append-only thread.
        self._delivered_turn_directive = ""
        # One INFO line per session for the discarded tool declarations.
        self._tools_discard_noted = False
        # False while the local recognizer stream is DEAD. It is the only
        # grounding source this transport has, so losing it must degrade to
        # the same fail-open (now bounded) behaviour as a host that never had
        # one — otherwise the gate refuses and interrupts every remaining
        # answer of the call. No longer a one-way latch: a successful bounded
        # rebuild (see ``_RECOGNIZER_REBUILDS_MAX``) sets it back to True.
        self._local_grounding_ok = True
        # Session-lifetime counters for the end-of-session postmortem event.
        # Bumped at the same sites that already log the condition; the session
        # layer reads them once at teardown via ``diagnostics()``. Counters,
        # never text — transcripts have no business in telemetry.
        self._diag: Counter[str] = Counter()

    def _local_grounding_active(self) -> bool:
        """Whether locally grounded input is available AND still trustworthy."""
        return self._input_transcriber is not None and self._local_grounding_ok

    def diagnostics(self) -> dict[str, int]:
        """Postmortem counters, merged with the media endpoint's own.

        Read once by ``RealtimeVoiceSession.end()``; every key is a plain int
        so the frozen bus event can carry it. Best-effort by contract — a
        broken endpoint must never break teardown.
        """
        merged: dict[str, int] = {k: int(v) for k, v in self._diag.items()}
        endpoint_diag = getattr(self._audio_endpoint, "diagnostics", None)
        if callable(endpoint_diag):
            try:
                merged.update({k: int(v) for k, v in endpoint_diag().items()})
            except Exception:  # noqa: BLE001 — diagnostics never break teardown
                log.debug("audio endpoint diagnostics unavailable", exc_info=True)
        return merged

    async def _append_trusted(self, write: Any, *, arms_response: bool) -> Any:
        """Run one provider write through the single trusted-write path.

        ``arms_response`` separates the two kinds of write that share this one
        RPC, and getting it wrong is a self-dialogue hole in either direction:

        * ``True`` — an injection whose PURPOSE is to be spoken now
          (``send_text``, ``send_speech``: announcements, action readbacks).
          It arms exactly one exception to the grounding gate in ``receive``.
        * ``False`` — inert mid-call context the model is given but must not
          answer on its own (context deltas, the language pin, the truncation
          note). The thread's own base instructions say to respond only to
          real user audio, so a response to one of these IS the self-echo turn
          the gate exists to refuse. Arming a permit here banked one at every
          session open and handed it to the first invented answer of the call.

        A write that never lands gives its permit back, so a failed injection
        cannot authorize an unrelated response later either.
        """
        if arms_response:
            self._trusted_output_permits += 1
            self._trusted_output_permit_at = asyncio.get_running_loop().time()
        try:
            return await write()
        except BaseException:
            if arms_response:
                self._trusted_output_permits = max(0, self._trusted_output_permits - 1)
            raise

    async def _interrupt_active_codex_turn(self) -> None:
        turn_id = self._active_codex_turn_id
        if not turn_id:
            return
        self._active_codex_turn_id = ""
        self._handoff_interrupt_pending = False
        method = getattr(self._client, "turn_interrupt", None)
        if not callable(method):
            log.warning("Codex app-server client cannot interrupt handoff turn")
            return
        try:
            await asyncio.wait_for(
                method(self._thread_id, turn_id),
                timeout=_TURN_INTERRUPT_TIMEOUT_S,
            )
        except Exception:  # noqa: BLE001 - containment flags remain the backstop
            log.warning(
                "Codex subscription handoff turn interrupt failed",
                exc_info=True,
            )

    async def send_audio(self, chunk: Any) -> None:
        if self._closed:
            raise RuntimeError("Codex subscription realtime session is closed")
        sample_rate = int(getattr(chunk, "sample_rate", 0) or 0)
        channels = int(getattr(chunk, "channels", 1) or 1)
        if sample_rate <= 0:
            raise ValueError("Codex subscription realtime audio needs a sample rate")
        if channels != 1:
            raise ValueError("Codex subscription realtime accepts mono PCM only")
        pcm = bytes(getattr(chunk, "pcm", b"") or b"")
        if not pcm:
            return
        if self._audio_endpoint is None:
            raise RuntimeError("Codex subscription realtime has no media path for microphone audio")
        # ChatGPT-Live (v3) has NO audio client event: the sideband append that
        # the retired v1 protocol used is rejected outright ("Invalid value:
        # 'input_audio.append'"). Microphone audio rides the media track.
        self._audio_endpoint.send_pcm(pcm, sample_rate)
        if self._input_transcriber is not None:
            self._input_transcriber.feed(pcm, sample_rate)

    def _server_user_transcript_is_plausible(self) -> bool:
        """Was there microphone energy behind this server-side transcript?

        ChatGPT-Live transcribes the user itself, and like every recognizer it
        invents caption-style text on silence and on the echo of its own voice
        — observed live as "[exhale]", "blurred gray", "a_lee pixelated image",
        each recorded as something the user had said and answered in earnest.
        The test is word-agnostic (AP-27): the local endpointer already knows,
        from audio energy alone, whether anybody was speaking. Content rules
        cannot do this job — a hallucination is spelled like a sentence.
        """
        transcriber = self._input_transcriber
        if not self._local_grounding_active():
            # No endpointer (or a dead one), so no evidence either way; a deaf
            # bar would be a worse failure than an occasional invented line.
            return True
        checker = getattr(transcriber, "speech_recently", None)
        if not callable(checker):
            return True
        return bool(checker())

    async def receive(self) -> AsyncIterator[_ProviderEvent]:
        # Stay below app-server's bounded subscription queue so a stalled
        # consumer propagates backpressure instead of silently buffering an
        # unbounded amount of audio in a second layer.
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=_NORMALIZATION_QUEUE_MAX)
        timer_tasks: set[asyncio.Task[None]] = set()
        completion_task: asyncio.Task[None] | None = None
        completion_generation = 0
        completion_emitted = False
        # Armed by ``_begin_response`` when a NEW response opens over a
        # previous one whose audio played but never got a boundary (closed
        # silently by an invented caption or a stale-refusal cleanup). Without
        # this the two responses splice RAW into one playback stream - the
        # 15/140 ms seams measured live 2026-08-06. The receive loop drains it
        # into a local turn boundary BEFORE the new response's audio flows;
        # the playback side then resumes prebuffer-free, so the sequenced
        # boundary costs ~0 ms.
        sequence_boundary_pending = False
        stream_ended = False
        local_transcript_failed = False
        user_final_emitted = False
        # The "we could not transcribe this turn" notice is emitted once per
        # turn, but it must NOT latch ``user_final_emitted``: a genuine
        # transcript that lands afterwards is the real text of that same turn
        # and used to be discarded because the placeholder had already claimed
        # the slot.
        missing_boundary_emitted = False
        # Consecutive FINAL server captions that had neither microphone energy
        # nor a fresh local utterance behind them. One is normal on a laggy
        # link; a run of them is the self-dialogue loop.
        ungrounded_final_captions = 0
        # Consecutive REFUSED responses and when that run started. Unlike the
        # caption counter above this signal is not polluted by the far end's
        # reaction to our own cuts, so it — not the captions — is what decides
        # a self-dialogue loop (BUG-124).
        refusal_run = 0
        refusal_run_started_at = 0.0
        # Whether this run already logged that it stopped interrupting.
        refusal_storm_noted = False
        # One-shot marker for the silent-line variant of a refusal storm:
        # every response was refused before a frame ever played, so the
        # transport is fine and the gate is winning - a rebuild would only
        # buy a fresh greeting to refuse (and, via the relapse rule, could
        # end a call whose user is simply quiet; the no-idle-hangup mandate).
        silent_line_noted = False
        # Set by the refusal path once the run proves a loop; the receive loop
        # turns it into the user-facing reconnect advice on its next pass
        # (``_begin_response`` is not a generator and cannot yield).
        self_dialogue_detected = False
        grounding_loss_reported = False
        # Bounded mid-call recognizer rebuild (see _RECOGNIZER_REBUILDS_MAX):
        # attempts started so far, and the in-flight background build. The
        # build runs off this loop — its backoff sleep inside the receive
        # loop would stall audio pumping for its whole duration.
        recognizer_rebuilds_started = 0
        rebuild_task: asyncio.Task[None] | None = None
        assistant_audio = bytearray()
        assistant_audio_active = False
        assistant_transcript_seen = False
        # Exactly one terminal STT pass is allowed per provider response. A
        # failed pass must not be repeated by each competing local boundary
        # (quiescence, sequence drain, terminal item): native inference may
        # still be finishing in a worker thread, and retrying it here is the
        # AP-24 wedge class.
        terminal_recovery_attempted = False
        # Early shadow recovery (see _OUTPUT_EARLY_RECOVERY_AFTER_S): when the
        # response's first audible frame arrived, when recovery last finished,
        # and whether this response already produced its one shadow delta.
        # The recognizer runs as a background task delivering through the
        # queue — an inline await here blocked the whole receive pump for the
        # recognizer's own time bound, stalling audio and delaying the user's
        # speech edge by seconds (independent review R1, measured).
        first_audible_at = 0.0
        early_recovery_at = 0.0
        early_shadow_done = False
        shadow_attempts = 0
        shadow_task: asyncio.Task[None] | None = None
        # True between the local endpointer's speech-start and its final (or
        # failed/discarded) transcript: while the user is mid-utterance the
        # shared recognizer belongs to the MICROPHONE — a shadow attempt that
        # steals it turns the user's turn into a lost transcript (review R2).
        user_utterance_open = False
        # ChatGPT-Live's server VAD can start a response to silence or to its
        # own speaker echo.  Local input is the authority for whether a NEW
        # automatic response has a user behind it.  A generation is consumed
        # only when its response closes, so transcript previews that arrive
        # during the legitimate response remain visible.
        local_input_generation = 0
        consumed_input_generation = 0
        active_response_generation = 0
        response_open = False
        response_allowed = False
        response_opened_at = 0.0
        # Which authorization opened the current response: "" (none yet),
        # "fresh", "greeting", "permit", "continuation" or "failopen". Feeds
        # the opening bound below and every grant/refusal log line.
        response_grant = ""
        # The call's first user FINAL anchors the opening rules: a response
        # the server opens before any final exists is a greeting, not an
        # answer - whatever grounding it can claim (probe round 1: the
        # opening monologue consumed the user's first utterance and the real
        # answer was then refused as ungrounded).
        first_user_final_seen = False
        call_response_index = 0
        # Local, monotone response identity. ChatGPT-Live media can precede
        # its sideband turn/started id, so the remote turn id cannot safely
        # pair early PCM with later transcript. This identity is allocated at
        # the first observed response evidence and rides audio, transcript and
        # the terminal boundary together.
        response_identity = ""
        sequence_boundary_response_id = ""
        # A discarded utterance's still-open response window (see
        # _DISCARDED_UTTERANCE_GRACE_S): the generation it covers and when
        # the discard happened.
        discarded_generation = 0
        discarded_at = 0.0
        # The grounded utterance whose answer may still be streaming, and
        # whether the far end has since proven that answer ended. Together
        # with the last audible frame they decide whether a frame arriving
        # after the local backstop is the REST of that answer or a new
        # response that needs its own grounding.
        entitled_generation = 0
        entitlement_spent = True
        last_output_activity = 0.0
        # When the currently open response was REFUSED, so the refusal can be
        # reconsidered instead of outliving the response it applied to.
        response_rejected_at = 0.0
        # Local copy of the interrupt barrier; a mismatch means ``interrupt``
        # ran and everything still queued for the cut response is stale.
        output_barrier = self._output_drop_barrier
        # Monotonic deadline of the post-interrupt grace window (see
        # ``_POST_INTERRUPT_GRACE_S``); 0.0 while no interrupt is recent.
        interrupt_grace_until = 0.0
        # Item types this session has already reported as unhandled. The v3 item
        # vocabulary is only partly known, and silently swallowing the rest is
        # why neither the real terminal-response item nor the never-observed
        # handoff item could be identified from a whole call's log (AP-30).
        seen_unknown_items: set[str] = set()

        async def _pump_notifications() -> None:
            try:
                async for notification in self._subscription:
                    await queue.put(("notification", notification))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - normalized by consumer
                await queue.put(("stream_error", exc))
            else:
                await queue.put(("stream_end", None))

        async def _pump_local_input() -> None:
            """Feed locally recognized USER speech into the same event stream.

            The server transcript remains only a live preview/failure fallback.
            Emitting locally grounded events here makes the bar, indicators,
            and every transcript-driven Jarvis integration trustworthy.
            """
            transcriber = self._input_transcriber
            if transcriber is None:
                return
            try:
                while True:
                    event = await transcriber.next_event()
                    if event is None:
                        # Clean end: the recognizer was closed with the session.
                        return
                    await queue.put(("local_input", event))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - normalized by the consumer
                # NOT a clean end. This is the transport's ONLY grounding
                # source, so simply returning left the gate refusing and
                # interrupting every remaining answer of the call — a silent
                # mute that looked like the provider had stopped talking
                # (AP-30). The consumer attempts a bounded rebuild first and
                # only then degrades to the bounded fail-open path a host
                # without any recognizer takes, saying so out loud.
                await queue.put(("local_input_failed", exc))

        async def _pump_media_audio() -> None:
            """Feed decoded RTP audio into the same normalized event stream.

            This is the ONLY audio source on ChatGPT-Live: the sideband
            ``outputAudio`` notification the retired v1 protocol used is never
            emitted (verified live — 956 RTP frames, zero sideband deltas).
            """
            endpoint = self._audio_endpoint
            if endpoint is None:
                return
            try:
                while True:
                    pcm = await endpoint.next_output_pcm()
                    if pcm is None:
                        await queue.put(("media_end", None))
                        return
                    await queue.put(("media_audio", pcm))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - normalized by consumer
                await queue.put(("stream_error", exc))

        def _reset_assistant_capture() -> None:
            nonlocal assistant_audio_active, assistant_transcript_seen
            nonlocal first_audible_at, early_recovery_at, early_shadow_done
            nonlocal shadow_attempts, shadow_task, terminal_recovery_attempted
            assistant_audio.clear()
            assistant_audio_active = False
            assistant_transcript_seen = False
            first_audible_at = 0.0
            early_recovery_at = 0.0
            early_shadow_done = False
            shadow_attempts = 0
            terminal_recovery_attempted = False
            # A still-running attempt belongs to the response that just
            # ended; letting it run would only suppress the NEXT response's
            # attempt through the single-flight handle until it delivered a
            # result the anchor check discards anyway.
            if shadow_task is not None and not shadow_task.done():
                shadow_task.cancel()
            shadow_task = None

        def _expire_discarded_grace() -> None:
            """Retire a discarded utterance's window once nobody answered it."""
            nonlocal consumed_input_generation, discarded_generation
            if discarded_generation and discarded_generation <= consumed_input_generation:
                discarded_generation = 0
                return
            if (
                discarded_generation
                and asyncio.get_running_loop().time() - discarded_at > _DISCARDED_UTTERANCE_GRACE_S
            ):
                consumed_input_generation = max(consumed_input_generation, discarded_generation)
                discarded_generation = 0

        def _fresh_local_input_exists() -> bool:
            _expire_discarded_grace()
            return bool(
                not self._local_grounding_active()
                or local_input_generation > consumed_input_generation
            )

        def _trusted_permit_available() -> bool:
            """A trusted injection is only good for the response it provokes.

            The far end answers an injection within a second or two. A permit
            that outlives that window belongs to an injection nobody answered
            and must not be spent by an unrelated response much later.
            """
            if self._trusted_output_permits <= 0:
                return False
            elapsed = asyncio.get_running_loop().time() - self._trusted_output_permit_at
            if elapsed <= _TRUSTED_PERMIT_GRACE_S:
                return True
            log.debug(
                "Discarding %d trusted output permit(s) unspent after %.1fs",
                self._trusted_output_permits,
                elapsed,
            )
            self._trusted_output_permits = 0
            return False

        def _entitled_turn_continues() -> bool:
            """Is this frame the rest of an answer the user already earned?

            ChatGPT-Live streams one answer with pauses and announces no
            reliable end, so the local backstop closes turns on silence alone.
            Without this the tail of an ordinary reply was judged as a fresh
            ungrounded response, refused, and the refusal then applied to
            everything after it.

            It can only ever extend an entitlement a locally energy-grounded
            utterance created, it expires with
            ``_RESPONSE_CONTINUATION_GRACE_S`` of provider silence, and any
            evidence the far end ended its turn cancels it outright — so it
            cannot hand a self-echo response a way in.
            """
            if entitlement_spent or entitled_generation <= 0:
                return False
            if last_output_activity <= 0.0:
                return False
            elapsed = asyncio.get_running_loop().time() - last_output_activity
            return elapsed <= _RESPONSE_CONTINUATION_GRACE_S

        def _rejected_response_is_stale() -> bool:
            return bool(
                response_rejected_at > 0.0
                and asyncio.get_running_loop().time() - response_rejected_at
                >= _REJECTED_RESPONSE_MAX_S
            )

        async def _begin_response(source: str) -> bool:
            """Authorize one response from fresh local input or trusted injection."""
            nonlocal active_response_generation, response_allowed, response_open
            nonlocal response_opened_at, response_rejected_at
            nonlocal entitled_generation, entitlement_spent
            nonlocal interrupt_grace_until, refusal_run, refusal_run_started_at
            nonlocal refusal_storm_noted, self_dialogue_detected
            nonlocal sequence_boundary_pending
            nonlocal response_grant, call_response_index
            nonlocal silent_line_noted
            nonlocal response_identity, sequence_boundary_response_id
            _expire_discarded_grace()
            if response_open:
                if response_allowed or not _rejected_response_is_stale():
                    return response_allowed
                # A refusal must never outlive the response it refused. The
                # verdict below is re-derived from scratch; nothing here makes
                # an ungrounded response acceptable, it only stops ONE refusal
                # from deciding the rest of the call.
                _close_response(spent=True)
            response_open = True
            response_opened_at = asyncio.get_running_loop().time()
            if last_output_activity > 0.0:
                splice_gap_ms = (response_opened_at - last_output_activity) * 1000.0
                if splice_gap_ms < 1_500.0:
                    # Two responses would splice into ONE playback stream here
                    # with no marker between them. A user-reported ~2 s garble
                    # (2026-08-05 20:12) has exactly this shape; seams of 15
                    # and 140 ms were then measured live 2026-08-06. When the
                    # previous response's audio PLAYED and never got a
                    # boundary, arm the sequencing drain: the receive loop
                    # emits a local turn boundary before this response's audio
                    # flows, converting the raw splice into a clean seam the
                    # prebuffer-free playback resume makes inaudible.
                    self._diag["response_splices"] += 1
                    if not completion_emitted:
                        sequence_boundary_pending = True
                        sequence_boundary_response_id = response_identity
                    log.info(
                        "Codex subscription realtime opened a new response "
                        "%d ms after the previous response's audio "
                        "(source=%s)%s",
                        int(splice_gap_ms),
                        source,
                        (
                            " — sequencing a local turn boundary between them"
                            if not completion_emitted
                            else " — previous response already closed cleanly"
                        ),
                    )
            response_rejected_at = 0.0
            active_response_generation = 0
            call_response_index += 1
            response_identity = f"{self._thread_id}:response:{call_response_index}"
            response_grant = ""
            if not self._local_grounding_active():
                response_allowed = True
                response_grant = "failopen"
            elif local_input_generation > consumed_input_generation and first_user_final_seen:
                # NOTHING unsolicited ever plays before the call's first user
                # FINAL. The bounded-greeting compromise this replaces lost
                # its live test (maintainer, 2026-08-08): the greeting's
                # remainder re-earned a grant the moment speech re-opened,
                # the first final then ADOPTED it as "the answer", and what
                # played was the model echoing the user's own words and
                # acknowledging itself ("geht ab? ... Okay."). The control
                # adapter (openai-realtime) never adopts unsolicited
                # responses and shows zero role-play - this is that policy.
                # The user's question keeps its entitlement while the opener
                # is refused (a refusal never consumes), and the first final
                # re-judges a refused-open response immediately (see the
                # local-input branch), so the real answer still flows.
                active_response_generation = local_input_generation
                entitled_generation = local_input_generation
                entitlement_spent = False
                response_allowed = True
                response_grant = "fresh"
            elif _trusted_permit_available():
                self._trusted_output_permits -= 1
                self._diag["trusted_permit_responses"] += 1
                response_allowed = True
                response_grant = "permit"
            elif _entitled_turn_continues():
                active_response_generation = entitled_generation
                response_allowed = True
                response_grant = "continuation"
                log.debug(
                    "Codex subscription realtime treats %s as the continuation "
                    "of the answer already grounded in utterance %d",
                    source,
                    entitled_generation,
                )
            else:
                response_allowed = False
                response_rejected_at = response_opened_at
                # Evaluate the window BEFORE this refusal arms its own, or the
                # log would call every refusal a post-interrupt continuation.
                post_interrupt = _within_interrupt_grace()
                now = response_opened_at
                if now - refusal_run_started_at > _REFUSAL_STORM_WINDOW_S:
                    refusal_run = 0
                    refusal_run_started_at = now
                    refusal_storm_noted = False
                refusal_run += 1
                self._diag["ungrounded_responses_refused"] += 1
                if post_interrupt:
                    # Expected fallout of OUR own cut: the far end streams the
                    # remainder of the retired answer for a while. Dropping it
                    # is routine, not an anomaly.
                    log.info(
                        "Codex subscription realtime dropped the far end's "
                        "post-interrupt continuation (%s)",
                        source,
                    )
                else:
                    log.warning(
                        "Codex subscription realtime rejected an automatic response "
                        "without a fresh local user utterance (%s); refusal %d of "
                        "the current run",
                        source,
                        refusal_run,
                    )
                # A refusal IS a local interrupt, and the far end answers one
                # with echo captions of its own truncated speech. Without this
                # window those captions counted as invented user turns and two
                # of them tore the transport down (BUG-124).
                interrupt_grace_until = now + _POST_INTERRUPT_GRACE_S
                if refusal_run <= 1:
                    await self._interrupt_active_codex_turn()
                elif not refusal_storm_noted:
                    # Cutting a turn makes ChatGPT-Live start a new one, which
                    # is refused, which is cut: the interrupt is what keeps the
                    # cycle spinning. The refused audio is dropped before it can
                    # ever reach the user either way, so past the first refusal
                    # the far end is simply left to run out.
                    refusal_storm_noted = True
                    log.info(
                        "Codex subscription realtime is in a refusal run — "
                        "dropping the far end's automatic responses without "
                        "interrupting, so a cut cannot provoke the next one"
                    )
                if (
                    refusal_run >= _REFUSALS_BEFORE_REBUILD
                    and not self_dialogue_detected
                    and local_input_generation > 0
                ):
                    # The discriminator: somebody REALLY spoke on this
                    # transport, so a storm now means the call went mute-broken
                    # (BUG-124's 17:39 death) and a fresh transport is the way
                    # out. A storm on a line nobody ever spoke into is just
                    # the far end talking AT silence - handled below.
                    self_dialogue_detected = True
                    self._diag["self_dialogue_rebuilds"] += 1
                    log.warning(
                        "Codex subscription refused %d automatic responses "
                        "within %.0f s with no locally grounded speech in "
                        "between; the far end is talking to itself and the "
                        "realtime transport needs replacing",
                        refusal_run,
                        _REFUSAL_STORM_WINDOW_S,
                    )
                    try:
                        # Wake the receive loop NOW: it owns the yield, and a
                        # loop that has gone quiet would otherwise leave the
                        # verdict unreported until the far end happens to send
                        # again — the silent-mute failure this detector exists
                        # to end. A full queue already guarantees that wake-up,
                        # so dropping the nudge there changes nothing.
                        queue.put_nowait(("self_dialogue", None))
                    except asyncio.QueueFull:
                        # A full queue already wakes the receive loop (see
                        # above) — the nudge would be redundant, not lost.
                        pass
                elif (
                    refusal_run >= _REFUSALS_BEFORE_REBUILD
                    and not self_dialogue_detected
                    and not silent_line_noted
                ):
                    silent_line_noted = True
                    log.info(
                        "Codex subscription keeps refusing the far end's "
                        "automatic responses on a silent line (%d so far); "
                        "no refused frame was ever played, so the transport "
                        "stays - a rebuild would only buy a fresh greeting "
                        "to refuse",
                        refusal_run,
                    )
            if response_allowed:
                # An authorized response proves there is no loop: whatever the
                # run was counting ended here.
                refusal_run = 0
                refusal_storm_noted = False
            return response_allowed

        def _close_response(*, spent: bool) -> None:
            """Close the open response; ``spent`` retires its entitlement.

            Only the far end can prove its response is over — a terminal item,
            a cancel, a new response it announces, a handoff, or an invented
            user caption (which is exactly the model claiming the turn ended
            and a new user turn began). The local quiescence backstop knows
            the LOCAL turn is over and nothing more, so it closes without
            spending; that is what lets the rest of one answer through after
            a pause longer than the backstop.
            """
            nonlocal consumed_input_generation, active_response_generation
            nonlocal response_allowed, response_open, response_opened_at
            nonlocal response_rejected_at, entitlement_spent
            if response_allowed and active_response_generation:
                # Always advances: one utterance authorizes ONE response, and
                # the continuation path above — never a second grounding
                # claim on the same utterance — is what reopens it.
                consumed_input_generation = max(
                    consumed_input_generation, active_response_generation
                )
            if spent:
                entitlement_spent = True
            active_response_generation = 0
            response_allowed = False
            response_open = False
            response_opened_at = 0.0
            response_rejected_at = 0.0

        def _finish_response() -> None:
            """Local turn boundary: closes the response, keeps the entitlement."""
            _close_response(spent=False)

        async def _drain_sequence_boundary() -> list[_ProviderEvent]:
            """Events that close a spliced-over response behind a boundary.

            Runs right after ``_begin_response`` armed the pending flag for a
            back-to-back open: recovers whatever the OLD response left (its
            missing input boundary, its transcript head) and closes it with a
            ``turn_complete`` BEFORE the new response's audio flows. The
            caller yields these in order; the capture reset makes the new
            response start clean.
            """
            nonlocal sequence_boundary_pending, completion_emitted
            nonlocal sequence_boundary_response_id
            if not sequence_boundary_pending:
                return []
            sequence_boundary_pending = False
            boundary_response_id = sequence_boundary_response_id
            sequence_boundary_response_id = ""
            events: list[_ProviderEvent] = []
            missing_input = _missing_input_boundary()
            if missing_input is not None:
                events.append(missing_input)
            recovered = await _recover_output_transcript(
                response_id=boundary_response_id
            )
            if recovered is not None:
                events.append(recovered)
            self._diag["sequenced_boundaries"] += 1
            log.info(
                "Codex subscription realtime sequenced a back-to-back "
                "response behind a local turn boundary"
            )
            events.append(
                _ProviderEvent(
                    type="turn_complete",
                    provider_turn_id=boundary_response_id or None,
                )
            )
            _reset_assistant_capture()
            self._assistant_delta_text = ""
            # The boundary belongs to the OLD response; the one just opened
            # is live, so the completion flag stays clear for it.
            completion_emitted = False
            return events

        def _note_output_activity() -> None:
            nonlocal last_output_activity
            last_output_activity = asyncio.get_running_loop().time()

        def _interrupt_barrier_moved() -> bool:
            """True once per ``interrupt`` call, for the receive loop."""
            nonlocal output_barrier
            if self._output_drop_barrier == output_barrier:
                return False
            output_barrier = self._output_drop_barrier
            return True

        def _within_interrupt_grace() -> bool:
            """Whether a local interrupt is recent enough to explain fallout."""
            return (
                interrupt_grace_until > 0.0
                and asyncio.get_running_loop().time() < interrupt_grace_until
            )

        def _caption_is_too_early_to_judge() -> bool:
            """Whether an ungrounded caption landed too soon to count as evidence.

            A caption arriving in the first moments of a freshly opened response
            is a race between two streams, not proof of an invented turn: the
            far end may simply have transcribed the utterance that opened this
            very response. Such a caption still closes the response, but it does
            not advance the self-dialogue run — the next one decides.
            """
            return bool(
                response_open
                and response_allowed
                and response_opened_at > 0.0
                and asyncio.get_running_loop().time() - response_opened_at
                < _UNGROUNDED_RESPONSE_GRACE_S
            )

        async def _recover_output_transcript(
            *, response_id: str = ""
        ) -> _ProviderEvent | None:
            """Run the one terminal STT pass for a missing transcript."""
            nonlocal assistant_transcript_seen, terminal_recovery_attempted
            if (
                assistant_transcript_seen
                or terminal_recovery_attempted
                or not assistant_audio
            ):
                return None
            terminal_recovery_attempted = True
            self._diag["output_terminal_recovery_attempts"] += 1
            recover = getattr(self._input_transcriber, "transcribe_audio", None)
            if not callable(recover):
                self._diag["output_transcript_recovery_failures"] += 1
                return None
            try:
                # No outer wait_for: the recognizer bounds itself in proportion
                # to the audio handed in, and cancelling that await from here
                # abandons a worker thread INSIDE the native engine, which is
                # the exact wedge AP-24 describes. A skip (``RecognizerBusy``)
                # and a genuine overrun (``TimeoutError``) both arrive as
                # ordinary exceptions and are handled below.
                text = str(
                    await recover(bytes(assistant_audio), sample_rate=_OUTPUT_RATE) or ""
                ).strip()
            except Exception:  # noqa: BLE001 - the fail-closed gate remains active
                self._diag["output_transcript_recovery_failures"] += 1
                log.warning(
                    "Codex subscription output transcript recovery failed",
                    exc_info=True,
                )
                return None
            if not text:
                self._diag["output_transcript_recovery_failures"] += 1
                log.warning("Codex subscription output transcript recovery returned no text")
                return None
            assistant_transcript_seen = True
            self._diag["output_terminal_recovery_successes"] += 1
            log.info(
                "Recovered a missing Codex subscription output transcript "
                "from %d bytes of provider audio",
                len(assistant_audio),
            )
            return _ProviderEvent(
                type="output_transcript_delta",
                text=text,
                is_final=True,
                provider_turn_id=response_id or response_identity or None,
            )

        async def _recover_shadow_transcript(head: bytes) -> str:
            """Locally transcribe the captured head for gate vetting only.

            Unlike ``_recover_output_transcript`` this neither latches
            ``assistant_transcript_seen`` nor produces display text: the
            provider's own (late) transcript stays the one the user sees.
            AP-24: ``transcribe_audio`` bounds itself and skips when the
            shared engine is busy — a raised skip is an ordinary retry.
            Accepted residual risk (review R3): a local MIStranscription that
            happens to look like a hard leak can cancel a correct answer; the
            cancel reason names shadow recovery, so the incident is
            diagnosable, and the odds are far below the daily cost of a 7 s
            opening hold.
            """
            recover = getattr(self._input_transcriber, "transcribe_audio", None)
            if not callable(recover):
                return ""
            try:
                return str(await recover(head, sample_rate=_OUTPUT_RATE) or "").strip()
            except Exception:  # noqa: BLE001 - the fail-closed hold simply continues
                log.debug(
                    "Codex subscription early shadow recovery skipped",
                    exc_info=True,
                )
                return ""

        async def _run_shadow_recovery(
            anchor: float,
            response_id: str,
            head: bytes,
        ) -> None:
            """Background shadow attempt; the result rides the ordinary queue.

            The anchor is the response's ``first_audible_at`` at task start:
            a result whose anchor no longer matches belongs to a response
            that has since closed and must never vet a different one's audio.
            """
            text = await _recover_shadow_transcript(head)
            await queue.put(("shadow_transcript", (anchor, response_id, text)))

        async def _run_recognizer_rebuild(attempt: int) -> None:
            """Build a replacement recognizer off the receive loop.

            The linear backoff spaces attempts so an engine that dies on
            start cannot hot-loop; the result rides the ordinary queue
            because this loop owns every piece of grounding state. A failed
            build delivers ``None`` — a counted verdict, never a crash.
            """
            await asyncio.sleep(_RECOGNIZER_REBUILD_BACKOFF_S * attempt)
            # Read once: the scheduler only starts this task with a factory
            # present, and the local name keeps that invariant checkable.
            factory = self._input_transcriber_factory
            replacement: Any = None
            try:
                replacement = factory() if factory is not None else None
                if replacement is not None:
                    await _warm_input_transcriber(replacement)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the exhaustion path reports it
                if replacement is not None:
                    with suppress(Exception):
                        await replacement.close()
                replacement = None
                log.warning(
                    "Codex subscription recognizer rebuild attempt %d failed",
                    attempt,
                    exc_info=True,
                )
            await queue.put(("recognizer_rebuilt", replacement))

        def _grounding_unavailable_notice(detail: str) -> _ProviderEvent:
            """The ONE honest surface notice for an ungrounded call (AP-30).

            Same shape as every other user-facing degradation this adapter
            reports: a recoverable error event the session surfaces without
            ending the call. The caller guards it with
            ``grounding_loss_reported`` so a call degrades out loud exactly
            once, whichever path got it there.
            """
            self._diag["grounding_unavailable_notices"] += 1
            return _ProviderEvent(
                type="error",
                error=(
                    f"Local speech recognition is unavailable: {detail} "
                    "The assistant keeps answering, but every automatic "
                    f"response is bounded to {_OPENING_RESPONSE_MAX_S:.0f}s "
                    "and Jarvis-side transcript features stay idle."
                ),
                recoverable=True,
            )

        def _missing_input_boundary() -> _ProviderEvent | None:
            """One placeholder per turn when neither source produced text.

            It deliberately does NOT latch ``user_final_emitted``: this is a
            "we have nothing yet" notice, and the far end's own transcript of
            the same audio regularly lands afterwards. Latching made that late
            genuine text unreachable forever, so the turn the user really spoke
            was permanently recorded as empty.
            """
            nonlocal missing_boundary_emitted
            if not local_transcript_failed or user_final_emitted:
                return None
            if missing_boundary_emitted:
                return None
            missing_boundary_emitted = True
            return _ProviderEvent(
                type="input_transcript",
                text="",
                is_final=True,
                error=(
                    "Local and provider input transcription were unavailable for this spoken turn."
                ),
                item_id=self._last_input_item_id or None,
            )

        async def _emit_after_idle(generation: int) -> None:
            await asyncio.sleep(_OUTPUT_QUIESCENCE_S)
            await queue.put(("completion", generation))

        def _cancel_completion() -> None:
            nonlocal completion_task, completion_generation
            completion_generation += 1
            if completion_task is not None and not completion_task.done():
                completion_task.cancel()
            completion_task = None

        def _arm_completion() -> None:
            nonlocal completion_task, completion_generation
            _cancel_completion()
            generation = completion_generation
            completion_task = asyncio.create_task(
                _emit_after_idle(generation),
                name=f"codex-realtime-quiescence-{self._thread_id}",
            )
            timer_tasks.add(completion_task)
            completion_task.add_done_callback(timer_tasks.discard)

        async def _retire_self_dialogue_turn() -> None:
            """Silence the looping far end before the transport is replaced.

            Shared by both detectors — the refusal run and the caption run —
            so a rebuild always leaves the same state behind whichever proved
            the loop.
            """
            nonlocal completion_emitted
            _cancel_completion()
            completion_emitted = True
            _reset_assistant_capture()
            await self._interrupt_active_codex_turn()
            _close_response(spent=True)

        # Start local input first. A user may finish speaking while the
        # handshake is still draining already-buffered server notifications;
        # scheduling the network pump first could otherwise reject the valid
        # response before the queued local speech boundary gets normalized.
        input_task = asyncio.create_task(
            _pump_local_input(),
            name=f"codex-realtime-local-input-{self._thread_id}",
        )
        pump_task = asyncio.create_task(
            _pump_notifications(),
            name=f"codex-realtime-notifications-{self._thread_id}",
        )
        media_task = asyncio.create_task(
            _pump_media_audio(),
            name=f"codex-realtime-media-{self._thread_id}",
        )
        try:
            if self._input_transcriber is None and self._grounding_unavailable_reason:
                # The recognizer BUILD failed, so the whole call runs in the
                # bounded ungrounded regime and there is nothing to rebuild.
                # Degradation is announced at session start, not buried in a
                # build-time log line: a silently disarmed grounding gate is
                # THE reason past self-talk fixes did not stick (AP-30). An
                # injected recognizer of None without a reason is a caller's
                # explicit choice and stays silent, exactly as before.
                grounding_loss_reported = True
                yield _grounding_unavailable_notice(f"{self._grounding_unavailable_reason}.")
            while True:
                queue_kind, payload = await queue.get()
                if self_dialogue_detected:
                    # Raised by ``_begin_response``, which cannot yield. The
                    # far end is answering itself; a clean transport is the
                    # only recovery, and saying so is what keeps a mute call
                    # from looking healthy (AP-30).
                    await _retire_self_dialogue_turn()
                    yield _ProviderEvent(
                        type="error",
                        error=_ungrounded_turn_message(self._language),
                        recoverable=True,
                        reconnect_advised=True,
                    )
                    yield _ProviderEvent(
                        type="turn_complete",
                        provider_turn_id=response_identity or None,
                    )
                    return
                if _interrupt_barrier_moved():
                    # Barge-in. ChatGPT-Live keeps streaming the rest of the
                    # answer the user talked over, so retire the cut response
                    # here: its remainder is dropped by the ordinary grounding
                    # gate below instead of being played over the person who
                    # interrupted it.
                    log.info(
                        "Codex subscription realtime dropped the remainder of an "
                        "interrupted response (protocol %s)",
                        self.realtime_version or "unknown",
                    )
                    _cancel_completion()
                    _reset_assistant_capture()
                    _close_response(spent=True)
                    if self._retire_entitlement_pending:
                        # A DELEGATION took this turn: the utterance it was
                        # dispatched for must not re-authorize the far end's
                        # own answer AFTER the cut. Closing the open response
                        # alone left that entitlement standing whenever the
                        # interrupt beat the response's first frame, and the
                        # "retired" answer simply started afterwards. Guarded
                        # by the explicit flag because on a BARGE-IN the
                        # current generation already belongs to the user's
                        # NEW utterance — retiring it there silenced the
                        # answer to the very question the user just asked
                        # (independent review 2026-08-05).
                        self._retire_entitlement_pending = False
                        consumed_input_generation = max(
                            consumed_input_generation, local_input_generation
                        )
                    interrupt_grace_until = (
                        asyncio.get_running_loop().time() + _POST_INTERRUPT_GRACE_S
                    )
                    self._assistant_delta_text = ""
                    completion_emitted = True
                if queue_kind == "shadow_transcript":
                    shadow_task = None
                    early_recovery_at = asyncio.get_running_loop().time()
                    anchor, shadow_response_id, shadow_text = payload
                    if (
                        shadow_text
                        and anchor == first_audible_at
                        and shadow_response_id == response_identity
                        and not early_shadow_done
                        and not assistant_transcript_seen
                        and response_open
                        and response_allowed
                    ):
                        early_shadow_done = True
                        self._diag["output_shadow_recovery_successes"] += 1
                        log.info(
                            "Codex subscription realtime released the opening "
                            "hold with a locally recovered shadow transcript "
                            "%d ms after the response's first audible frame",
                            int((early_recovery_at - first_audible_at) * 1000),
                        )
                        yield _ProviderEvent(
                            type="output_transcript_delta",
                            text=shadow_text,
                            is_final=False,
                            shadow=True,
                            provider_turn_id=shadow_response_id or None,
                        )
                    elif shadow_text:
                        log.debug("Discarding a stale or superseded shadow transcript")
                    elif shadow_attempts >= _OUTPUT_EARLY_RECOVERY_MAX_ATTEMPTS:
                        self._diag["output_shadow_recovery_exhausted"] += 1
                    continue
                if queue_kind == "local_input_failed":
                    # The ONLY grounding source died mid-call. Until a
                    # rebuild lands, degrade to the same bounded fail-open
                    # behaviour a host without any recognizer has
                    # (``_local_grounding_active``), or the gate would refuse
                    # and interrupt every remaining answer — a mute the user
                    # cannot distinguish from a dead provider.
                    if not self._local_grounding_ok:
                        continue
                    self._local_grounding_ok = False
                    # No stream, no open utterance — and the shadow recovery
                    # gate must not stay locked on a state the dead stream
                    # can never close (one-shot transcribe_audio may still
                    # work even when the event stream died).
                    user_utterance_open = False
                    _close_response(spent=False)
                    if (
                        self._input_transcriber_factory is not None
                        and recognizer_rebuilds_started < _RECOGNIZER_REBUILDS_MAX
                        and rebuild_task is None
                    ):
                        # Not the one-way latch this used to be: the process-
                        # wide STT cache makes a rebuild cheap, so a transient
                        # stream death costs seconds of bounded fail-open
                        # instead of the whole call's grounding.
                        recognizer_rebuilds_started += 1
                        self._diag["recognizer_rebuild_attempts"] += 1
                        log.warning(
                            "Local input transcription stream failed; "
                            "rebuilding the recognizer (attempt %d of %d) — "
                            "the grounding gate fails OPEN, bounded, until "
                            "it returns",
                            recognizer_rebuilds_started,
                            _RECOGNIZER_REBUILDS_MAX,
                            exc_info=payload if isinstance(payload, BaseException) else None,
                        )
                        rebuild_task = asyncio.create_task(
                            _run_recognizer_rebuild(recognizer_rebuilds_started),
                            name=f"codex-recognizer-rebuild-{self._thread_id}",
                        )
                        continue
                    log.warning(
                        "Local input transcription stream failed with no "
                        "rebuild left; the Codex subscription grounding gate "
                        "now fails OPEN (bounded) for the rest of this call",
                        exc_info=payload if isinstance(payload, BaseException) else None,
                    )
                    if not grounding_loss_reported:
                        grounding_loss_reported = True
                        yield _grounding_unavailable_notice(
                            "local speech recognition stopped during this "
                            f"call: {type(payload).__name__}: "
                            f"{_safe_error(payload)}."
                        )
                    continue
                if queue_kind == "recognizer_rebuilt":
                    rebuild_task = None
                    replacement = payload
                    if self._closed and replacement is not None:
                        with suppress(Exception):
                            await replacement.close()
                        continue
                    if replacement is None:
                        if (
                            self._input_transcriber_factory is not None
                            and recognizer_rebuilds_started < _RECOGNIZER_REBUILDS_MAX
                        ):
                            # The dead pump delivers no further failure event,
                            # so the next attempt starts here, not in the
                            # failure handler.
                            recognizer_rebuilds_started += 1
                            self._diag["recognizer_rebuild_attempts"] += 1
                            rebuild_task = asyncio.create_task(
                                _run_recognizer_rebuild(recognizer_rebuilds_started),
                                name=(f"codex-recognizer-rebuild-{self._thread_id}"),
                            )
                            continue
                        log.warning(
                            "Codex subscription exhausted its %d recognizer "
                            "rebuild(s); the grounding gate stays OPEN "
                            "(bounded) for the rest of this call",
                            _RECOGNIZER_REBUILDS_MAX,
                        )
                        if not grounding_loss_reported:
                            grounding_loss_reported = True
                            yield _grounding_unavailable_notice(
                                "local speech recognition could not be "
                                "rebuilt after it stopped mid-call."
                            )
                        continue
                    # Success: the new recognizer takes over grounding and
                    # the local-input pump restarts on it. The old instance
                    # is closed best-effort — its stream is already dead and
                    # the shared STT backend outlives any one instance.
                    retired = self._input_transcriber
                    self._input_transcriber = replacement
                    self._local_grounding_ok = True
                    self._diag["recognizer_rebuilds_restored"] += 1
                    if retired is not None:
                        with suppress(Exception):
                            await retired.close()
                    input_task = asyncio.create_task(
                        _pump_local_input(),
                        name=f"codex-realtime-local-input-{self._thread_id}",
                    )
                    log.info(
                        "Codex subscription restored local grounding with a "
                        "rebuilt recognizer (attempt %d of %d)",
                        recognizer_rebuilds_started,
                        _RECOGNIZER_REBUILDS_MAX,
                    )
                    continue
                if queue_kind == "local_input":
                    if payload.kind == _SPEECH_STARTED:
                        local_input_generation += 1
                        user_utterance_open = True
                        local_transcript_failed = False
                        user_final_emitted = False
                        missing_boundary_emitted = False
                        ungrounded_final_captions = 0
                        # Somebody really spoke into this host's microphone —
                        # the one signal the far end cannot fake. Whatever the
                        # refusal run was counting, it was not a loop.
                        refusal_run = 0
                        refusal_storm_noted = False
                        _cancel_completion()
                        completion_emitted = False
                        self._assistant_delta_text = ""
                        self._server_user_preview = ""
                        _reset_assistant_capture()
                        # A locally energy-grounded utterance always reopens the
                        # gate, whatever the response machine was doing. This is
                        # the unconditional escape from a stuck verdict: the one
                        # signal the far end cannot fake is the user actually
                        # making a sound into this host's microphone.
                        #
                        # It closes WITHOUT spending: a discarded utterance (a
                        # cough) revokes its own generation below, and the reply
                        # it interrupted must then be able to continue.
                        _close_response(spent=False)
                        yield _ProviderEvent(type="speech_started")
                    elif payload.kind == _SPEECH_DISCARDED:
                        # The endpointer opened an utterance and then judged it
                        # too short to be speech. It carries no text and must
                        # never become a turn — but it must not destroy the
                        # far end's preview either, which is the fallback for
                        # the REAL utterance around it, nor leave the turn that
                        # ``speech_started`` already announced hanging open.
                        # The far end HEARD the sound whatever the local
                        # verdict says, so its answer keeps a bounded window
                        # instead of being refused on the spot (the BUG-124
                        # amplifier: a 200 ms "hm?" got its real answer cut).
                        user_utterance_open = False
                        discarded_generation = local_input_generation
                        discarded_at = asyncio.get_running_loop().time()
                        log.debug(
                            "Local endpointer discarded a %d ms utterance; "
                            "its response window stays open %.1f s",
                            int(getattr(payload, "voiced_ms", 0) or 0),
                            _DISCARDED_UTTERANCE_GRACE_S,
                        )
                        if not response_open and not completion_emitted:
                            completion_emitted = True
                            yield _ProviderEvent(
                                type="turn_complete",
                                provider_turn_id=response_identity or None,
                            )
                    elif payload.kind == _TRANSCRIPT_FAILED:
                        # The local recognizer could not deliver this
                        # utterance. A turn the user really spoke must not
                        # vanish, so the far end's preview is promoted — it
                        # covers the same audio and the endpointer already
                        # vouched for it. Silence here would strand the turn.
                        user_utterance_open = False
                        # The endpointer vouched for a COMPLETE real
                        # utterance, so the call's opening phase ends here
                        # even though no clean text exists - otherwise a
                        # recognizer hiccup on turn 1 would keep refusing
                        # every answer of the call.
                        first_user_final_seen = True
                        if response_open and not response_allowed:
                            _close_response(spent=False)
                            log.info(
                                "Codex subscription re-judges the open "
                                "refused response after a failed local "
                                "transcript proved a real utterance"
                            )
                        preview = self._server_user_preview
                        self._server_user_preview = ""
                        local_transcript_failed = True
                        if preview and not user_final_emitted:
                            user_final_emitted = True
                            log.info(
                                "Local recognizer delivered nothing; using the "
                                "provider's own transcript for this turn"
                            )
                            yield _ProviderEvent(
                                type="input_transcript",
                                text=preview,
                                is_final=True,
                                item_id=self._last_input_item_id or None,
                            )
                    else:
                        self._server_user_preview = ""
                        if payload.is_final:
                            user_utterance_open = False
                            local_transcript_failed = False
                            user_final_emitted = True
                            first_user_final_seen = True
                            if response_open and not response_allowed:
                                # A REFUSED response is still open while the
                                # user's final just landed - the far end may
                                # already be answering the question it heard.
                                # Close the refusal now (never consuming) so
                                # the very next frame is re-judged against
                                # the fresh entitlement, instead of the
                                # answer waiting out the stale-refusal
                                # window and losing its head.
                                _close_response(spent=False)
                                log.info(
                                    "Codex subscription re-judges the open "
                                    "refused response now that a user final "
                                    "landed"
                                )
                        yield _ProviderEvent(
                            type="input_transcript",
                            text=payload.text,
                            is_final=payload.is_final,
                            voiced_ms=int(getattr(payload, "voiced_ms", 0) or 0),
                        )
                    continue
                if queue_kind == "media_audio":
                    pcm = bytes(payload or b"")
                    if not pcm:
                        continue
                    # Only AUDIBLE audio keeps the turn open. The media track
                    # also carries silence between turns, so re-arming on every
                    # chunk would mean the backstop could never fire and a turn
                    # whose terminal item never arrives would hang forever.
                    #
                    # Audible audio also ARMS it, not just re-arms: a reply
                    # whose transcript never lands would otherwise leave no
                    # boundary at all, and a turn that never ends holds the
                    # half-duplex gate open — the microphone stays shut and
                    # Jarvis goes deaf for the rest of the call.
                    audible = _pcm16_peak(pcm) >= _OUTPUT_AUDIBLE_PEAK
                    if audible:
                        if not await _begin_response("media audio"):
                            continue
                        for boundary_event in await _drain_sequence_boundary():
                            yield boundary_event
                        # Energy only, never transcript content (AP-27): this
                        # stamp is what tells a pause inside one answer apart
                        # from a quiet stretch between responses.
                        _note_output_activity()
                        if completion_emitted:
                            _reset_assistant_capture()
                            completion_emitted = False
                        assistant_audio_active = True
                        _arm_completion()
                    elif not response_open:
                        # Preserve a legitimate response's quiet onset, but do
                        # not let the permanently-open track's between-turn
                        # silence authorize a response by itself.
                        if (
                            self._local_grounding_active()
                            and not _fresh_local_input_exists()
                            and not _trusted_permit_available()
                        ):
                            continue
                        if not await _begin_response("media prelude"):
                            continue
                        for boundary_event in await _drain_sequence_boundary():
                            yield boundary_event
                    elif not response_allowed:
                        continue
                    if (
                        assistant_audio_active
                        and not assistant_transcript_seen
                        and len(assistant_audio) < _OUTPUT_TRANSCRIPT_RECOVERY_MAX_BYTES
                    ):
                        remaining = _OUTPUT_TRANSCRIPT_RECOVERY_MAX_BYTES - len(assistant_audio)
                        assistant_audio.extend(pcm[:remaining])
                    if audible and assistant_audio_active and not first_audible_at:
                        first_audible_at = asyncio.get_running_loop().time()
                    if response_allowed and first_audible_at and response_grant == "failopen":
                        # An ungrounded host cannot refuse ANY response (no
                        # grounding exists), so EVERY failopen response keeps
                        # the coarse cap — not just the opener. Bounding only
                        # response #1 left the far end unbounded from response
                        # #2 on, which is where the echo-driven two-AIs loop
                        # ran unopposed whenever the recognizer was absent or
                        # dead (see _OPENING_RESPONSE_MAX_S). On grounded
                        # hosts no failopen grant exists at all.
                        opening_age = asyncio.get_running_loop().time() - first_audible_at
                        if opening_age > _OPENING_RESPONSE_MAX_S:
                            if call_response_index == 1 and not first_user_final_seen:
                                # Keep the opener's dedicated postmortem key:
                                # a bounded OPENING still means "monologue
                                # before any question", a different diagnosis
                                # than a bounded mid-call response.
                                self._diag["opening_responses_bounded"] += 1
                            else:
                                self._diag["ungrounded_responses_bounded"] += 1
                            log.warning(
                                "Codex subscription bounded an ungrounded "
                                "response at %.1fs - no local grounding "
                                "exists to judge it (grant=%s, response=%d)",
                                opening_age,
                                response_grant,
                                call_response_index,
                            )
                            await self._interrupt_active_codex_turn()
                            interrupt_grace_until = (
                                asyncio.get_running_loop().time() + _POST_INTERRUPT_GRACE_S
                            )
                            _cancel_completion()
                            completion_emitted = True
                            yield _ProviderEvent(type="turn_complete")
                            _reset_assistant_capture()
                            _close_response(spent=True)
                            response_grant = ""
                            continue
                    if (
                        audible
                        and assistant_audio_active
                        and not assistant_transcript_seen
                        and not early_shadow_done
                        and first_audible_at
                        and assistant_audio
                        and shadow_task is None
                        and not user_utterance_open
                        and shadow_attempts
                        < _OUTPUT_EARLY_RECOVERY_MAX_ATTEMPTS
                    ):
                        # The session's scrub gate is holding every one of
                        # these chunks fail-closed until SOME transcript
                        # arrives, and the far end's own can lag by seconds.
                        # Recover a shadow transcript locally so the gate can
                        # judge real text now (see the constants above) — in a
                        # background task, so this loop keeps pumping audio
                        # and the user's speech edge on time (review R1), and
                        # only while the microphone has no open utterance of
                        # its own on the shared recognizer (review R2).
                        now = asyncio.get_running_loop().time()
                        if (
                            now - first_audible_at >= _OUTPUT_EARLY_RECOVERY_AFTER_S
                            and now - early_recovery_at >= _OUTPUT_EARLY_RECOVERY_RETRY_S
                        ):
                            head = bytes(assistant_audio[:_SHADOW_RECOVERY_MAX_BYTES])
                            shadow_attempts += 1
                            self._diag["output_shadow_recovery_attempts"] += 1
                            shadow_task = asyncio.create_task(
                                _run_shadow_recovery(
                                    first_audible_at,
                                    response_identity,
                                    head,
                                ),
                                name=f"codex-shadow-recovery-{self._thread_id}",
                            )
                    yield _ProviderEvent(
                        type="audio_delta",
                        audio=_PcmChunk(
                            pcm=pcm,
                            sample_rate=_OUTPUT_RATE,
                            channels=1,
                        ),
                        provider_turn_id=response_identity or None,
                    )
                    continue
                if queue_kind == "completion":
                    if payload != completion_generation:
                        continue
                    completion_task = None
                    if not completion_emitted:
                        missing_input = _missing_input_boundary()
                        if missing_input is not None:
                            yield missing_input
                        recovered = await _recover_output_transcript(
                            response_id=response_identity
                        )
                        if recovered is not None:
                            yield recovered
                        completion_emitted = True
                        self._diag["quiescence_boundary_turns"] += 1
                        yield _ProviderEvent(
                            type="turn_complete",
                            provider_turn_id=response_identity or None,
                        )
                        _reset_assistant_capture()
                        _finish_response()
                    if stream_ended:
                        if not self._closed:
                            yield _ProviderEvent(
                                type="error",
                                error=("Codex app-server notification stream ended unexpectedly"),
                                recoverable=True,
                            )
                        return
                    continue

                if queue_kind == "stream_error":
                    _cancel_completion()
                    exc = payload
                    yield _ProviderEvent(
                        type="error",
                        error=(
                            "Codex app-server notification stream failed: "
                            f"{type(exc).__name__}: {_safe_error(exc)}"
                        ),
                        recoverable=True,
                    )
                    return

                if queue_kind == "media_end":
                    _cancel_completion()
                    if not self._closed:
                        yield _ProviderEvent(
                            type="error",
                            error="Codex realtime media track ended unexpectedly",
                            recoverable=True,
                        )
                    return

                if queue_kind == "stream_end":
                    stream_ended = True
                    if completion_task is not None:
                        continue
                    if not self._closed:
                        yield _ProviderEvent(
                            type="error",
                            error=("Codex app-server notification stream ended unexpectedly"),
                            recoverable=True,
                        )
                    return

                notification = payload
                method, params = _notification_parts(notification)
                event_thread = str(params.get("threadId", "") or "")
                if event_thread and event_thread != self._thread_id:
                    continue

                if method == "turn/started":
                    turn = params.get("turn")
                    if isinstance(turn, dict):
                        self._active_codex_turn_id = str(turn.get("id", "") or "").strip()
                    if self._handoff_interrupt_pending:
                        await self._interrupt_active_codex_turn()
                    elif response_open and not response_allowed:
                        # Media can beat the sideband ``turn/started`` notice.
                        # Complete the rejection once the interruptible id is
                        # finally known.
                        await self._interrupt_active_codex_turn()
                    continue

                if method in {"turn/completed", "turn/failed"}:
                    turn = params.get("turn")
                    completed_id = (
                        str(turn.get("id", "") or "").strip() if isinstance(turn, dict) else ""
                    )
                    if not completed_id or completed_id == self._active_codex_turn_id:
                        self._active_codex_turn_id = ""
                    # Boundary instrumentation (AP-30): whether this notice is
                    # a reliable per-RESPONSE boundary on ChatGPT-Live is still
                    # unconfirmed (the transcript-part trap of 2026-08-02 makes
                    # guessing expensive), so record its relation to the open
                    # response — a future live log decides whether it may close
                    # the turn outright instead of the quiescence backstop.
                    log.info(
                        "Codex subscription realtime observed %s (turn=%r, "
                        "response_open=%s, allowed=%s, quiescence_armed=%s)",
                        method,
                        completed_id or "?",
                        response_open,
                        response_allowed,
                        completion_task is not None,
                    )
                    if (
                        response_open
                        and response_allowed
                        and completion_task is None
                        and not completion_emitted
                    ):
                        # The far end says its turn is over while no quiescence
                        # timer is running (e.g. the reply's last chunk never
                        # crossed the audible threshold). Without arming here,
                        # that turn would never close locally and the
                        # half-duplex gate would hold the microphone forever.
                        _arm_completion()
                    continue

                if method == "thread/realtime/started":
                    # The start RPC result is empty in exact-0.146; the live
                    # protocol version exists only on this notification.
                    self.realtime_version = str(params.get("version", "") or "").strip()
                    log.info(
                        "Codex subscription realtime negotiated protocol %s "
                        "(voice=%s; ChatGPT-Live picks the model server-side)",
                        self.realtime_version or "unknown",
                        self._voice or _DEFAULT_VOICE,
                    )
                    continue

                if method == "thread/realtime/transcript/delta":
                    role = str(params.get("role", "") or "").lower()
                    delta = str(params.get("delta", "") or "")
                    if not delta:
                        continue
                    if role == "user":
                        energy_plausible = self._server_user_transcript_is_plausible()
                        fresh_input = _fresh_local_input_exists()
                        if not energy_plausible or not fresh_input:
                            log.debug(
                                "Dropping a server user transcript delta: energy=%s fresh=%s",
                                energy_plausible,
                                fresh_input,
                            )
                            # The far end claimed a user turn this host cannot
                            # confirm. Whatever it says next belongs to THAT
                            # turn, so the real turn's response is closed and
                            # its entitlement retired here — closing matters as
                            # much as retiring: a still-open response would
                            # otherwise carry its "allowed" verdict straight
                            # into the invented answer.
                            _close_response(spent=True)
                            continue
                        _cancel_completion()
                        completion_emitted = False
                        yield _ProviderEvent(
                            type="input_transcript",
                            text=delta,
                            is_final=False,
                            item_id=self._last_input_item_id or None,
                        )
                    elif role == "assistant":
                        if not await _begin_response("assistant transcript"):
                            continue
                        for boundary_event in await _drain_sequence_boundary():
                            yield boundary_event
                        # Text arriving proves the same answer is still being
                        # produced, even while its audio pauses.
                        _note_output_activity()
                        # A later transcript delta proves the previous
                        # transcript-part ``done`` was not a turn boundary.
                        _cancel_completion()
                        completion_emitted = False
                        assistant_transcript_seen = True
                        assistant_audio.clear()
                        self._assistant_delta_text += delta
                        yield _ProviderEvent(
                            type="output_transcript_delta",
                            text=delta,
                            is_final=False,
                            provider_turn_id=response_identity or None,
                        )
                    continue

                if method == "thread/realtime/transcript/done":
                    role = str(params.get("role", "") or "").lower()
                    text = str(params.get("text", "") or "")
                    if role == "user":
                        energy_plausible = self._server_user_transcript_is_plausible()
                        fresh_input = _fresh_local_input_exists()
                        if not energy_plausible or not fresh_input:
                            # Two different questions, deliberately NOT fused.
                            # A caption may only become a turn ON ITS OWN when
                            # both hold. But when a fresh local utterance
                            # exists, the endpointer has ALREADY vouched that a
                            # human spoke, and the far end simply transcribed
                            # it with its own latency — that caption is the
                            # documented fallback for this turn and is retained
                            # even though it arrived past the energy window.
                            if fresh_input:
                                self._server_user_preview = text
                                ungrounded_final_captions = 0
                                log.info(
                                    "A late server user transcript arrived past "
                                    "the energy window; retained as the fallback "
                                    "for the utterance the endpointer confirmed"
                                )
                                _close_response(spent=True)
                                continue
                            # Neither energy nor a fresh utterance: this is the
                            # far end inventing a user turn. ONE is survivable
                            # (links are laggy); a RUN of them is the
                            # self-dialogue loop and needs a clean transport.
                            if _caption_is_too_early_to_judge():
                                log.info(
                                    "Ignoring an ungrounded server user "
                                    "transcript that raced its own response "
                                    "window: %r",
                                    text[:80],
                                )
                                _close_response(spent=True)
                                continue
                            if _within_interrupt_grace():
                                # A cut response's echo of its own truncated
                                # speech, not the far end inventing a turn:
                                # never let OUR interrupt feed the rebuild
                                # counter.
                                log.info(
                                    "Ignoring an ungrounded server user caption "
                                    "inside the post-interrupt window: %r",
                                    text[:80],
                                )
                                _close_response(spent=True)
                                continue
                            ungrounded_final_captions += 1
                            if ungrounded_final_captions >= _UNGROUNDED_CAPTIONS_BEFORE_REBUILD:
                                diagnostic = (
                                    "Codex subscription detected %d consecutive "
                                    "automatic server turns without locally "
                                    "grounded microphone speech; rebuilding the "
                                    "realtime transport to stop a self-dialogue "
                                    "loop."
                                )
                                log.warning(
                                    diagnostic + " Caption=%r",
                                    ungrounded_final_captions,
                                    text[:80],
                                )
                                await _retire_self_dialogue_turn()
                                yield _ProviderEvent(
                                    type="error",
                                    error=_ungrounded_turn_message(self._language),
                                    recoverable=True,
                                    reconnect_advised=True,
                                )
                                yield _ProviderEvent(
                                    type="turn_complete",
                                    provider_turn_id=response_identity or None,
                                )
                                return
                            self._diag["ungrounded_captions_dropped"] += 1
                            log.info(
                                "Ignoring a server user transcript with no "
                                "microphone energy and no fresh local utterance "
                                "(%d in a row): %r",
                                ungrounded_final_captions,
                                text[:80],
                            )
                            # An invented user turn closes the real turn's
                            # response and retires its entitlement.
                            _close_response(spent=True)
                            continue
                        ungrounded_final_captions = 0
                        _cancel_completion()
                        completion_emitted = False
                        # The local recognizer owns the FINAL text: it is the
                        # one the user configured, with their dictionary and
                        # bias prompt, and it is what every other Jarvis
                        # feature hears. This stays a live preview unless that
                        # recognizer reports it could not deliver.
                        local_owns_final = self._local_grounding_active()
                        if user_final_emitted:
                            continue
                        if local_owns_final and not local_transcript_failed:
                            self._server_user_preview = text
                        else:
                            user_final_emitted = True
                            local_transcript_failed = False
                        yield _ProviderEvent(
                            type="input_transcript",
                            text=text,
                            is_final=not local_owns_final or user_final_emitted,
                            item_id=self._last_input_item_id or None,
                        )
                    elif role == "assistant":
                        if not await _begin_response("assistant transcript"):
                            continue
                        for boundary_event in await _drain_sequence_boundary():
                            yield boundary_event
                        _note_output_activity()
                        assistant_transcript_seen = True
                        assistant_audio.clear()
                        # Emit only a missing suffix so transcript consumers do
                        # not see the final text twice.
                        suffix = text
                        if text.startswith(self._assistant_delta_text):
                            suffix = text[len(self._assistant_delta_text) :]
                        if suffix:
                            yield _ProviderEvent(
                                type="output_transcript_delta",
                                text=suffix,
                                is_final=True,
                                provider_turn_id=response_identity or None,
                            )
                        self._assistant_delta_text = ""
                        # ChatGPT-Live emits this per transcript PART, not per
                        # response. Treating it as a turn boundary drained
                        # playback, armed a ~0.9 s echo window and re-armed the
                        # scrub gate three times inside ONE answer (measured
                        # 2026-08-02 08:21:58 / 08:22:00 / 08:22:05) — the
                        # chopped voice, and a microphone deaf between the
                        # pieces. The turn ends on the terminal response item,
                        # or on the quiescence backstop.
                        if not completion_emitted:
                            _arm_completion()
                    continue

                if method == "thread/realtime/outputAudio/delta":
                    if self._audio_endpoint is not None:
                        # ChatGPT-Live never emits this; if a future protocol
                        # revision mirrors audio again, playing BOTH sources
                        # would double every word. The media track wins.
                        continue
                    # Legacy sideband path (retired v1 protocol).
                    audio = params.get("audio")
                    if not isinstance(audio, dict):
                        _cancel_completion()
                        yield _ProviderEvent(
                            type="error",
                            error="Codex app-server emitted an invalid audio payload",
                        )
                        return
                    try:
                        pcm = base64.b64decode(str(audio.get("data", "") or ""), validate=True)
                        sample_rate = int(audio.get("sampleRate", 0) or 0)
                        channels = int(audio.get("numChannels", 1) or 1)
                    except (ValueError, TypeError) as exc:
                        _cancel_completion()
                        yield _ProviderEvent(
                            type="error",
                            error=(
                                "Codex app-server emitted malformed realtime audio: "
                                f"{_safe_error(exc)}"
                            ),
                        )
                        return
                    if not pcm or sample_rate <= 0 or channels != 1:
                        _cancel_completion()
                        yield _ProviderEvent(
                            type="error",
                            error=(
                                "Codex app-server emitted unsupported realtime audio "
                                f"(sample_rate={sample_rate}, channels={channels})"
                            ),
                        )
                        return
                    if completion_task is not None:
                        _arm_completion()
                    yield _ProviderEvent(
                        type="audio_delta",
                        audio=_PcmChunk(
                            pcm=pcm,
                            sample_rate=sample_rate,
                            channels=channels,
                        ),
                        provider_turn_id=response_identity or None,
                    )
                    continue

                if method == "thread/realtime/itemAdded":
                    item = params.get("item")
                    if not isinstance(item, dict):
                        continue
                    item_type = str(item.get("type", "") or "")
                    if item_type == "input_audio_buffer.speech_started":
                        _cancel_completion()
                        completion_emitted = False
                        self._assistant_delta_text = ""
                        self._last_input_item_id = str(item.get("item_id", "") or "")
                        if not self._local_grounding_active():
                            local_transcript_failed = False
                            user_final_emitted = False
                            missing_boundary_emitted = False
                        _reset_assistant_capture()
                        yield _ProviderEvent(
                            type="speech_started",
                            item_id=self._last_input_item_id or None,
                        )
                    elif item_type == "response.cancelled":
                        _cancel_completion()
                        cancelled_response_was_allowed = response_allowed
                        # This is a response boundary even when Jarvis caused
                        # the cancellation. A later ``response.done`` belongs
                        # to the same generation and must not be mistaken for
                        # another ungrounded response.
                        completion_emitted = True
                        self._assistant_delta_text = ""
                        _close_response(spent=True)
                        if cancelled_response_was_allowed:
                            yield _ProviderEvent(type="interrupted")
                    elif item_type == "handoff_request":
                        # Exact Codex 0.146 routes this item into a normal Codex
                        # turn even with clientManagedHandoffs enabled; that flag
                        # suppresses only automatic output delivery. Interrupt
                        # the turn as soon as its id is visible and hand control
                        # to Jarvis's deterministic supervisor.
                        _cancel_completion()
                        # The model yielded control; whatever it produces next
                        # needs its own grounding, never this turn's — and the
                        # yielded utterance's entitlement retires with it, or
                        # the model's own answer to the handed-off request
                        # could still start later.
                        _close_response(spent=True)
                        consumed_input_generation = max(
                            consumed_input_generation, local_input_generation
                        )
                        self._handoff_interrupt_pending = True
                        direct_turn_id = str(
                            item.get("turn_id", "") or item.get("turnId", "") or ""
                        ).strip()
                        if direct_turn_id:
                            self._active_codex_turn_id = direct_turn_id
                        await self._interrupt_active_codex_turn()
                        yield _ProviderEvent(
                            type="handoff_requested",
                            text=_handoff_text(item) or None,
                            handoff_id=str(
                                item.get("handoff_id", "") or item.get("handoffId", "") or ""
                            ).strip()
                            or None,
                            provider_turn_id=(direct_turn_id or None),
                        )
                    elif item_type in _TERMINAL_RESPONSE_ITEMS:
                        _cancel_completion()
                        if completion_emitted:
                            # The quiescence backstop already closed this same
                            # response.  A delayed protocol marker is an
                            # acknowledgement, not a new ungrounded response.
                            _reset_assistant_capture()
                            continue
                        allowed = await _begin_response("terminal response item")
                        if not completion_emitted and allowed:
                            missing_input = _missing_input_boundary()
                            if missing_input is not None:
                                yield missing_input
                            recovered = await _recover_output_transcript(
                                response_id=response_identity
                            )
                            if recovered is not None:
                                yield recovered
                            completion_emitted = True
                            self._diag["terminal_item_turns"] += 1
                            log.info(
                                "Codex subscription realtime turn ended on item %r (protocol %s)",
                                item_type,
                                self.realtime_version or "unknown",
                            )
                            yield _ProviderEvent(
                                type="turn_complete",
                                provider_turn_id=response_identity or None,
                            )
                            _reset_assistant_capture()
                        # Protocol proof that the response ended: the strongest
                        # boundary there is, so the entitlement retires here.
                        _close_response(spent=True)
                    elif item_type in _RESPONSE_OPENED_ITEMS:
                        _cancel_completion()
                        completion_emitted = False
                        _reset_assistant_capture()
                        # The far end announcing a NEW response is proof the
                        # previous one ended; close it first so the new one is
                        # judged on its own grounding instead of inheriting the
                        # old verdict.
                        _close_response(spent=True)
                        await _begin_response(item_type)
                    elif item_type and item_type not in seen_unknown_items:
                        # Type NAME only, once per type, bounded — never the
                        # payload, which can carry transcript text.
                        if len(seen_unknown_items) < _UNKNOWN_ITEM_LOG_MAX:
                            seen_unknown_items.add(item_type)
                            log.info(
                                "Codex subscription realtime saw an unhandled "
                                "realtime item type %r",
                                item_type,
                            )
                    continue

                if method == "thread/realtime/error":
                    _cancel_completion()
                    message = str(params.get("message", "") or "").strip()
                    yield _ProviderEvent(
                        type="error",
                        error=message or "Codex subscription realtime transport failed",
                    )
                    return

                if method == "thread/realtime/closed":
                    _cancel_completion()
                    if self._closed:
                        return
                    reason = str(params.get("reason", "") or "").strip()
                    yield _ProviderEvent(
                        type="error",
                        error=(
                            "Codex subscription realtime transport closed"
                            + (f": {reason}" if reason else "")
                        ),
                    )
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize adapter failures
            yield _ProviderEvent(
                type="error",
                error=(
                    "Codex app-server notification handling failed: "
                    f"{type(exc).__name__}: {_safe_error(exc)}"
                ),
            )
        finally:
            _cancel_completion()
            for pump in (pump_task, media_task, input_task):
                if not pump.done():
                    pump.cancel()
            for task in tuple(timer_tasks):
                if not task.done():
                    task.cancel()
            if shadow_task is not None and not shadow_task.done():
                shadow_task.cancel()
            if rebuild_task is not None and not rebuild_task.done():
                rebuild_task.cancel()
            await asyncio.gather(
                pump_task,
                media_task,
                input_task,
                *((shadow_task,) if shadow_task is not None else ()),
                *((rebuild_task,) if rebuild_task is not None else ()),
                *tuple(timer_tasks),
                return_exceptions=True,
            )

    async def update_session(
        self,
        *,
        instructions: str | None = None,
        language: str | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
        turn_directive: str | None = None,
        standing_directive: str | None = None,
    ) -> None:
        # Tools cannot be declared here, and that is a transport fact rather
        # than a policy choice: the app-server realtime RPC surface is only
        # start / appendAudio / appendText / appendSpeech / stop, with custom
        # fields refused outright, so there is no way to deliver a v3
        # session.update. The handoff item stays the one model-initiated action
        # channel. Say so once instead of discarding wordlessly (AP-30).
        if tools:
            # INFO once per session, DEBUG afterwards: the discard repeats on
            # every turn, but a session that silently swallows the entire tool
            # surface is exactly the AP-30 failure mode.
            already_noted = self._tools_discard_noted
            self._tools_discard_noted = True
            log.log(
                logging.DEBUG if already_noted else logging.INFO,
                "Codex subscription realtime cannot declare %d tool(s): the "
                "app-server realtime RPC surface has no session.update — "
                "actions travel as handoffs/delegation instead",
                len(tools),
            )
        del tools
        # INSTRUCTIONS, however, are the assistant's identity and project
        # knowledge — dropping them was why the voice knew nothing about its own
        # project. ChatGPT-Live accepts developer context, so a changed persona
        # is delivered mid-call instead of being discarded. Everything a turn
        # needs — context delta, current-turn block, language pin — travels as
        # ONE developer item: each separate item was one more thing the model
        # audibly acknowledged ("Alles klar…" spliced into the live answer,
        # 2026-08-05 19:13).
        normalized_pin = _normalized_locale(language)
        pin_text = _language_pin_text(normalized_pin)
        await self._deliver_context(
            instructions,
            turn_directive=turn_directive,
            standing_directive=standing_directive,
            language_pin=pin_text,
        )
        if pin_text:
            self._language = normalized_pin

    async def _deliver_context(
        self,
        instructions: str | None,
        *,
        turn_directive: str | None = None,
        standing_directive: str | None = None,
        language_pin: str = "",
    ) -> None:
        """Give the model Jarvis's own persona, capabilities and context.

        The complete initial block is supplied atomically through the audited
        v3 start ``prompt`` before media can produce audio. The transport is
        APPEND-only after that, and the session refreshes the full ~20k-char
        block on every final user transcript (its clock line alone changes
        every minute), so re-sending the whole block grew the live thread by a
        full persona per turn. Only lines that actually changed are appended.

        The TURN-scoped directive is the exception to line-diffing: its three
        states (required / pending / discouraged) are mutually exclusive, so a
        change is delivered as one whole block that explicitly REPLACES every
        earlier current-turn instruction — a diff plus a blanket "everything
        stays in force" left contradictory turn directives standing in the
        thread (independent review 2026-08-05).
        """
        text = str(instructions or "").strip()
        directive = str(turn_directive or "").strip()
        standing = str(standing_directive or "").strip()
        directive_lines = set(directive.splitlines()) if directive else set()
        standing_lines = set(standing.splitlines()) if standing else set()
        sections: list[str] = []
        if text and text != self._delivered_context:
            if not self._delivered_context:
                sections.append(text)
            else:
                previous_lines = set(self._delivered_context.splitlines())
                changed = [
                    line
                    for line in text.splitlines()
                    if line.strip()
                    and line not in previous_lines
                    and line not in directive_lines
                    and line not in standing_lines
                ]
                if changed:
                    sections.append(
                        "\n".join(
                            (
                                "Developer context update — apply the "
                                "following changed lines SILENTLY (never "
                                "acknowledge them) on top of the persona and "
                                "project context already given; unchanged "
                                "parts of THAT context stay in force:",
                                *changed,
                            )
                        )
                    )
        if directive and directive != self._delivered_turn_directive:
            sections.append(
                "Current-turn instructions — these REPLACE every earlier "
                "instruction about how to handle the current turn; apply "
                "them silently, never acknowledge them aloud:\n" + directive
            )
        if standing:
            # Re-asserted EVERY delivery, unchanged or not: three live calls
            # in a row (2026-08-05) and probe round 1 (2026-08-06, a 16 s
            # opening monologue over the user's first question) proved a
            # rule stated once at open does not hold on this channel, while
            # the per-turn language pin demonstrably does — repetition is
            # what makes a rule real here. The constant carries BOTH halves
            # (silence rule + its speak-request exception) so they can never
            # ship apart again (the b181d92f regression).
            sections.append(standing)
        if language_pin:
            # Reasserted every turn even when unchanged: the server can
            # freeze an automatic response before the larger refresh above
            # takes effect, and this compact final section is what wins.
            sections.append(language_pin)
        if not sections:
            # Identical, or only removals/reordering an append cannot retract.
            if text:
                self._delivered_context = text
            if directive:
                self._delivered_turn_directive = directive
            return
        payload = "\n\n".join(sections)
        try:
            await self._append_trusted(
                lambda: self._client.realtime_append_text(
                    self._thread_id, payload, role="developer"
                ),
                arms_response=False,
            )
        except Exception:  # noqa: BLE001 - a mute persona must not kill the call
            log.warning(
                "Codex subscription realtime could not deliver its context; "
                "the assistant continues without the persona",
                exc_info=True,
            )
            return
        if text:
            self._delivered_context = text
        if directive:
            self._delivered_turn_directive = directive

    async def request_response(self, *, required_tool: str | None = None) -> None:
        # The upstream VAD creates responses automatically.  A required tool
        # stays with the Jarvis delegate rather than becoming a direct Codex
        # app-server action.
        del required_tool

    async def send_text(self, text: str) -> None:
        await self._append_trusted(
            lambda: self._client.realtime_append_text(self._thread_id, str(text), role="developer"),
            arms_response=True,
        )

    async def send_speech(self, text: str) -> None:
        """Queue trusted verbatim speech without starting a Codex model turn."""
        await self._append_trusted(
            lambda: self._client.realtime_append_speech(self._thread_id, str(text)),
            arms_response=True,
        )

    async def truncate(self, audio_end_ms: int) -> None:
        """Tell the model how much of its answer the user actually heard.

        ChatGPT-Live has no truncate client event — the app-server realtime
        RPC surface is start / appendAudio / appendText / appendSpeech / stop
        and a custom field is refused outright, so the position cannot travel
        as ``conversation.item.truncate`` does on the OpenAI Realtime adapter.
        It travels as an inert developer note instead, because the alternative
        (the previous no-op) left the model believing its whole answer was
        heard: the next turn then continued from words the user never got.

        Only sent for a response an ``interrupt`` actually cut, and only when
        something was played — otherwise there is nothing to correct.
        """
        played_ms = max(0, int(audio_end_ms or 0))
        if not self._interrupt_pending_truncation:
            return
        self._interrupt_pending_truncation = False
        if played_ms <= 0:
            return
        try:
            await self._append_trusted(
                lambda: self._client.realtime_append_text(
                    self._thread_id,
                    _TRUNCATION_NOTE.format(ms=played_ms),
                    role="developer",
                ),
                arms_response=False,
            )
        except Exception:  # noqa: BLE001 - a stale horizon must not kill the call
            log.warning(
                "Codex subscription realtime could not report the interrupted "
                "playback position; the model may repeat unheard audio",
                exc_info=True,
            )

    async def interrupt(self, *, retire_input_entitlement: bool = False) -> None:
        """Stop the assistant on barge-in, with or without a Codex turn id.

        ``turn/interrupt`` addresses an app-server TURN, and an ordinary
        ChatGPT-Live response never announces one — ``turn/started`` covers
        Codex agent turns, and the realtime item vocabulary at the top of this
        module is still unconfirmed. Relying on it alone made barge-in a no-op:
        the assistant kept talking into an already-open microphone, which is
        precisely the self-echo the grounding gate then has to clean up.

        The local half is therefore unconditional and is the part that always
        works: raising the barrier makes ``receive`` retire the cut response,
        so every frame the far end still sends for it is dropped instead of
        played over the person who interrupted.

        ``retire_input_entitlement`` separates the two callers, and getting it
        wrong is audible either way. A DELEGATION owns the utterance it was
        dispatched for, so the far end's own answer to that utterance must
        never start later — the delegation paths pass True. A BARGE-IN happens
        after the user's NEW utterance already bumped the local input
        generation; retiring it there consumed the new question's one response
        entitlement and the assistant fell permanently silent (found in
        independent review 2026-08-05).
        """
        self._output_drop_barrier += 1
        if retire_input_entitlement:
            self._retire_entitlement_pending = True
        self._interrupt_pending_truncation = True
        await self._interrupt_active_codex_turn()

    async def send_tool_result(self, call_id: str, name: str, result: dict[str, Any]) -> None:
        del call_id, name, result
        raise RuntimeError("Codex subscription realtime does not execute tools directly")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await _cleanup_remote_thread(self._client, self._thread_id)
        finally:
            try:
                await _close_subscription(self._subscription)
            finally:
                try:
                    if self._input_transcriber is not None:
                        await self._input_transcriber.close()
                finally:
                    if self._audio_endpoint is not None:
                        await self._audio_endpoint.close()


class CodexSubscriptionRealtimeProvider:
    """Structural provider entry point backed by ChatGPT-managed Codex auth."""

    name = "codex-subscription-realtime"
    credential_family = "openai-chatgpt-subscription"
    supports_direct_tools = False
    supports_realtime = True
    implicit_usage_fallback_allowed = False
    # Jarvis owns the WebRTC peer in-process now, so the UI no longer has to
    # broker a signalling offer (and a headless host needs no browser at all).
    requires_webrtc_offer = False
    # Declared handshake need (capability, AP-21): a COLD start spawns
    # app-server, verifies the live account, re-audits config, and negotiates
    # WebRTC — measured 15-25s on a mid-range desktop. The shared 12s ceiling
    # beheaded every cold call into a pipeline fallback (log 2026-08-01).
    handshake_budget_s = 45.0
    # A delegate readback on this transport is spoken by ChatGPT-Live, whose
    # audio measurably lags its own start: the session's scrub gate held a
    # response's first audible frame 7.4 s (live 2026-08-05 20:42), while the
    # shared delegate-readback floor is 2.5 s. Undeclared, the watchdog fired
    # first every time the far end was slow — the surface TTS took the turn
    # in the wrong voice, or (with no realtime-scoped surface TTS) the real
    # audio answer was withheld outright and the user heard nothing. Declared
    # budget, same AP-21 doctrine as handshake_budget_s.
    readback_render_budget_s = 12.0
    input_sample_rate = _INPUT_RATE
    output_sample_rate = _OUTPUT_RATE
    credential_candidates: tuple[tuple[str, str | None], ...] = ()

    @classmethod
    async def verify_activation(cls, cfg: Any) -> None:
        """Run the provider's authoritative account/config activation gate.

        A successful cold verification deliberately keeps the shared
        app-server warm. Closing it here made provider selection pay one cold
        start and the first call immediately pay another. A failed probe still
        cleans up the child it started; an already-ready client may be serving
        another Codex feature and is never closed here.
        """
        app_server_module = importlib.import_module("jarvis.codex_app_server")
        codex_cfg = getattr(cfg, "codex", None)
        binary_path = str(getattr(codex_cfg, "binary_path", "") or "").strip() or None
        client = app_server_module.get_shared_codex_app_server(binary_path)
        was_ready = bool(getattr(client, "ready", False))
        try:
            await client.require_chatgpt_login()
        except Exception:
            # A cold call may have raced this gate onto the same client while
            # we awaited: active thread subscriptions mean someone else is
            # using it now — closing would cut their session mid-setup.
            if not was_ready and not getattr(client, "_subscriptions", None):
                await client.close()
            raise

    @classmethod
    async def warm_transport(cls, cfg: Any) -> None:
        """Pay the app-server cold start BEFORE a call, never inside one.

        Measured 2026-08-02: the process spawn plus live account verification
        happened inside the handshake and cost ~1.5 s of its 3.0 s. Until now
        the only warm path was the UI's provider-selection route, so a user
        who simply says the wake word pays it every time the app restarts.

        Best-effort by contract: a failure here changes nothing except that
        the next call pays what it pays today, so it must never propagate.
        """
        try:
            await cls.verify_activation(cfg)
        except Exception:  # noqa: BLE001 - warming is advisory, never fatal
            log.warning(
                "Codex subscription realtime could not warm its transport; "
                "the first call will pay the cold start",
                exc_info=True,
            )

    def __init__(
        self,
        *,
        client: Any = None,
        audio_endpoint_factory: Any = None,
        input_transcriber_factory: Any = None,
        binary_path: str | None = None,
    ) -> None:
        self._client = client
        # Injected in tests; production builds the in-process WebRTC endpoint.
        self._audio_endpoint_factory = audio_endpoint_factory
        self._input_transcriber_factory = input_transcriber_factory
        self._binary_path = str(binary_path or "").strip() or None

    @classmethod
    def from_runtime_config(cls, cfg: Any) -> CodexSubscriptionRealtimeProvider:
        binary_path = str(getattr(getattr(cfg, "codex", None), "binary_path", "") or "").strip()
        return cls(binary_path=binary_path or None)

    @classmethod
    def external_login_ready(cls, cfg: Any = None) -> bool:
        """Return the dedicated-profile snapshot; app-server verifies it live."""
        try:
            # Dynamic import preserves the plugin boundary: discovery imports
            # no ``jarvis.*`` module until this explicit capability probe.
            app_server_module = importlib.import_module("jarvis.codex_app_server")
            binary_path = str(getattr(getattr(cfg, "codex", None), "binary_path", "") or "").strip()
            if app_server_module.codex_subscription_activation_block():
                # The live account gate refused this login permanently;
                # advertising the provider as available would build sessions
                # that can never start (and mislead GET /voice-mode).
                return False
            status = app_server_module.codex_subscription_auth_snapshot(binary_path or None)
            if getattr(status, "reason_code", "") == "busy":
                # Transiently unknown, not "no login": failing closed here
                # flips voice-mode availability (and its 400) on a healthy
                # install. Fail open — opening a session runs the
                # authoritative live account verification anyway, and a
                # genuinely missing login still stops the call honestly.
                return True
            return bool(
                getattr(status, "available", False)
                and getattr(status, "chatgpt_authenticated", False)
            )
        except Exception:  # noqa: BLE001 - discovery degrades to other providers
            log.warning("Codex ChatGPT login status probe failed", exc_info=True)
            return False

    async def can_open_duplex_session(self) -> bool:
        # The factory already performed the bounded external-login probe. The
        # authoritative handshake below starts app-server, which independently
        # verifies the live ChatGPT account. Repeating the CLI probe here costs
        # seconds on a cold call without strengthening the trust boundary.
        return True

    async def open_session(self, cfg: Any) -> _CodexSubscriptionRealtimeSession:
        """Open a session, preferring the fast host-only media path.

        Host candidates alone connect on an ordinary network and cost no
        gathering time; STUN costs a fixed ~5 s wait that used to sit in front
        of every call and swallow the user's first sentence. A network that
        genuinely needs a reflexive candidate gets one on the retry.
        """
        transport_module = importlib.import_module("jarvis.realtime.webrtc_transport")
        attempts: tuple[Any, ...] = (None, transport_module.stun_ice_servers)
        last_error: BaseException | None = None
        # Built and primed ONCE for both attempts: the recognizer is pure
        # local state, and rebuilding + re-warming it on the STUN retry paid
        # seconds for nothing.
        input_transcriber, grounding_unavailable_reason = self._build_input_transcriber_outcome(cfg)
        warm_task: asyncio.Task[Any] | None = None
        if callable(getattr(input_transcriber, "warm", None)):
            warm_task = asyncio.create_task(
                _warm_input_transcriber(input_transcriber),
                name="codex-realtime-transcriber-warm",
            )
        try:
            for index, ice_factory in enumerate(attempts):
                try:
                    session = await self._open_session_once(
                        cfg,
                        None if ice_factory is None else ice_factory(),
                        input_transcriber=input_transcriber,
                        input_transcriber_warm_task=warm_task,
                        grounding_unavailable_reason=grounding_unavailable_reason,
                        # The STUN retry may ride the audit that passed seconds
                        # ago on this very open; a first attempt never does.
                        ride_recent_audit=index > 0,
                    )
                    if index > 0:
                        # Postmortem marker: this call paid the full re-open (a
                        # second thread_start bundle included) to reach STUN.
                        session._diag["stun_retries"] = index
                        log.info("RT-SPAWN stun_retry=%d", index)
                    return session
                except transport_module.WebRtcMediaPathUnavailable as exc:
                    last_error = exc
                    if index + 1 < len(attempts):
                        log.warning(
                            "Realtime media path did not connect on host candidates "
                            "(%s); retrying with a STUN server",
                            exc,
                        )
                        continue
                    raise
            raise RuntimeError(  # pragma: no cover - loop returns or raises
                "Codex subscription realtime could not open a media path"
            ) from last_error
        finally:
            await _cancel_background_task(warm_task)

    def _build_input_transcriber(self, cfg: Any = None) -> Any:
        """Local user-speech recognition, or ``None`` when unavailable."""
        transcriber, _reason = self._build_input_transcriber_outcome(cfg)
        return transcriber

    def _build_input_transcriber_outcome(self, cfg: Any = None) -> tuple[Any, str]:
        """(recognizer or ``None``, non-empty degradation reason on failure).

        ChatGPT-Live also guesses at user speech, but local energy-gated STT is
        authoritative. Without it silence/echo captions could become commands,
        while transcript-driven Jarvis integrations would have no grounded text.
        A BUILD failure therefore returns its reason alongside the ``None`` so
        the session can announce the degraded (bounded fail-open) call on the
        surface instead of only in this log (AP-30). An injected factory owns
        its own construction and degradation story — a ``None`` from it is the
        caller's explicit choice, never a failure to report.

        The session's recognition language is handed over when the recognizer
        accepts it. Probed rather than assumed (AP-21): an older recognizer
        without the parameter keeps working on its own configured language
        instead of the whole call losing its only grounding source over a
        keyword argument. The in-tree recognizer accepts ``language`` since
        2026-08-08, so the probe protects out-of-tree modules only.
        """
        if self._input_transcriber_factory is not None:
            return self._input_transcriber_factory(), ""
        input_language = _normalized_locale(getattr(cfg, "input_language", ""))
        if input_language == "auto":
            input_language = ""
        try:
            module = importlib.import_module("jarvis.realtime.input_transcription")
            if input_language:
                try:
                    return (
                        module.LocalInputTranscriber(
                            sample_rate=_INPUT_RATE, language=input_language
                        ),
                        "",
                    )
                except TypeError:
                    # One WARNING per process, DEBUG afterwards: the dropped
                    # capability repeats on every call, but swallowing it at
                    # DEBUG is how the un-pinned session language went
                    # unnoticed while the recognizer heard the wrong
                    # language (AP-30).
                    global _language_kwarg_rejected_warned
                    already_warned = _language_kwarg_rejected_warned
                    _language_kwarg_rejected_warned = True
                    log.log(
                        logging.DEBUG if already_warned else logging.WARNING,
                        "Local input transcription does not accept a session "
                        "language; the call keeps the recognizer's configured "
                        "language instead of %r",
                        input_language,
                    )
            return module.LocalInputTranscriber(sample_rate=_INPUT_RATE), ""
        except Exception as exc:  # noqa: BLE001 - the call still works, just deaf
            log.warning(
                "Local input transcription could not be started; the voice "
                "answers but Jarvis-side transcript features stay idle",
                exc_info=True,
            )
            return None, f"{type(exc).__name__}: {_safe_error(exc)}"

    async def _open_session_once(
        self,
        cfg: Any,
        ice_servers: Any,
        *,
        input_transcriber: Any = None,
        input_transcriber_warm_task: asyncio.Task[Any] | None = None,
        grounding_unavailable_reason: str = "",
        ride_recent_audit: bool = False,
    ) -> _CodexSubscriptionRealtimeSession:
        # Jarvis owns the media path in-process. The UI could only ever broker
        # a signalling-shaped offer (no microphone), which ChatGPT-Live cannot
        # use: on v3 the audio IS the WebRTC track.
        # RT-SPAWN spans use each operation's own start so overlapping work
        # stays measurable instead of producing misleading near-zero deltas.
        def _stamp(name: str, started_at: float) -> None:
            log.info(
                "RT-SPAWN span=%s ms=%d",
                name,
                int((time.monotonic() - started_at) * 1000.0),
            )

        audio_endpoint: Any = None
        client = self._client
        offer_task: asyncio.Task[str] | None = None
        subscription: Any = None
        thread_id = ""
        try:
            if self._audio_endpoint_factory is not None:
                audio_endpoint = self._audio_endpoint_factory(ice_servers)
            else:
                transport_module = importlib.import_module("jarvis.realtime.webrtc_transport")
                audio_endpoint = transport_module.RealtimeWebRtcAudioEndpoint(ice_servers)
            offer_started_at = time.monotonic()
            offer_task = asyncio.create_task(
                audio_endpoint.create_offer(), name="codex-realtime-create-offer"
            )

            if client is None:
                app_server_module = importlib.import_module("jarvis.codex_app_server")
                client = app_server_module.get_shared_codex_app_server(self._binary_path)
            # ``thread_start`` lazily calls app-server ``ensure_started``. That
            # performs the authoritative capability probe plus live
            # ``account/read`` verification before accepting this thread.
            thread_started_at = time.monotonic()
            thread_result = await client.thread_start(
                base_instructions=_THREAD_BASE_INSTRUCTIONS,
                developer_instructions=_THREAD_DEVELOPER_INSTRUCTIONS,
                ephemeral=True,
            )
            thread_id = _thread_id_from_result(thread_result)
            if not thread_id:
                raise RuntimeError("Codex app-server did not return a thread id")
            _stamp("thread_start", thread_started_at)
            offer_sdp = await offer_task
            _stamp("offer_create", offer_started_at)
            subscription = client.subscribe(thread_id)

            configured_model = str(getattr(cfg, "model", "") or "").strip()
            if configured_model and configured_model not in {
                "auto",
                _LEGACY_V1_MODEL,
            }:
                # v3 rejects a client model outright; an unknown leftover pin
                # must not brick the call — the server chooses the model.
                log.info(
                    "Codex subscription realtime ignores the configured model "
                    "%r: ChatGPT-Live (v3) selects the model server-side",
                    configured_model,
                )
            voice = str(getattr(cfg, "voice", "") or "").strip().lower() or _DEFAULT_VOICE
            # The server's live roster is authoritative when it can be read
            # (thread/realtime/listVoices, codex-cli 0.147.0); the audited
            # static frozenset stays the fallback for a client or server
            # without the method. Capability-probed, never version-gated
            # here (AP-21/AP-22) — the app-server client owns the wire.
            if voice not in await _live_voice_roster(client, thread_id):
                raise RuntimeError(
                    "Codex subscription realtime has an unsupported voice configured"
                )
            initial_context = str(getattr(cfg, "instructions", "") or "").strip()
            start_prompt = _startup_prompt(
                initial_context,
                language=getattr(cfg, "language", ""),
                language_is_pinned=bool(getattr(cfg, "language_is_pinned", False)),
            )
            initial_items = _history_initial_items(getattr(cfg, "history", ()))
            realtime_started_at = time.monotonic()
            start_kwargs: dict[str, Any] = {
                "output_modality": "audio",
                "offer_sdp": offer_sdp,
                "prompt": "",
                "trusted_prompt": start_prompt,
                "initial_items": initial_items,
                # v3 (ChatGPT-Live): the server chooses the model; sending a
                # client model is rejected with "Field `session.model` is not
                # allowed" (verified live 2026-08-01). The dead v1 protocol
                # answered every start with 403 since the ChatGPT-Live launch.
                "model": None,
                "voice": voice,
                "version": "v3",
                "include_startup_context": False,
                "client_managed_handoffs": True,
            }
            if _client_realtime_start_accepts(client, "realtime_start_instructions"):
                # codex-cli 0.147.0's realtimeStartInstructions slot carries
                # the startup contract as SESSION instructions instead of a
                # trusted first prompt — injected prompt context is what the
                # model audibly acknowledged mid-open. The client owns the
                # wire gate and omits the field (naming it in its own log)
                # for an older approved binary; the thread-level base and
                # developer instructions from thread_start still hold there
                # and the persona re-arrives with the first update_session
                # delivery, so that degradation is honest, never silent.
                start_kwargs["realtime_start_instructions"] = start_prompt
                start_kwargs["trusted_prompt"] = ""
            start = await client.realtime_start(thread_id, **start_kwargs)
            answer_sdp = str(getattr(start, "answer_sdp", "") or "").strip()
            if not answer_sdp:
                raise RuntimeError("Codex app-server did not return a WebRTC answer SDP")
            _stamp("realtime_start_sdp", realtime_started_at)
            media_started_at = time.monotonic()
            await audio_endpoint.apply_answer(answer_sdp)
            # Fail here rather than mid-call: without a live media path the
            # session would look connected and stay mute in both directions.
            await audio_endpoint.wait_connected()
            _stamp("media_connect", media_started_at)
            session = _CodexSubscriptionRealtimeSession(
                client=client,
                subscription=subscription,
                thread_id=thread_id,
                answer_sdp=answer_sdp,
                audio_endpoint=audio_endpoint,
                input_transcriber=(
                    input_transcriber
                    if input_transcriber is not None
                    else self._build_input_transcriber(cfg)
                ),
                # The bounded mid-call rebuild reuses the ordinary build path,
                # which reattaches to the process-wide STT cache — so a
                # rebuild costs a stream restart, not a model load.
                input_transcriber_factory=lambda: self._build_input_transcriber(cfg),
                grounding_unavailable_reason=grounding_unavailable_reason,
                language=str(getattr(cfg, "language", "en") or "en"),
                voice=voice,
                initial_context=initial_context,
            )
            # The recognizer was primed alongside offer/thread startup. Wait
            # for that independent work before exposing the session so the
            # first utterance never pays its cold-start cost.
            if input_transcriber_warm_task is not None:
                await input_transcriber_warm_task
            else:
                await _warm_input_transcriber(session._input_transcriber)
            return session
        except BaseException:
            await _cancel_background_task(offer_task)
            try:
                await _cleanup_remote_thread(client, thread_id)
            finally:
                try:
                    if subscription is not None:
                        await _close_subscription(subscription)
                finally:
                    if audio_endpoint is not None:
                        await audio_endpoint.close()
            raise


__all__ = ["CodexSubscriptionRealtimeProvider"]
