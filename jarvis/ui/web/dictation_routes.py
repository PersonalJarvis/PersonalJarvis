"""REST API for dictation mode — hold a key, speak, text lands where you type.

Endpoints (mounted by the WebServer in ``_build_app()``):

    GET    /api/dictation/status    → capability + live state + the shortcuts.
    POST   /api/dictation/start     → begin a dictation ({"target": ...}).
    POST   /api/dictation/stop      → finish the running one.
    POST   /api/dictation/paste-last → insert the last dictation again.
    GET    /api/dictation/history   → recent dictations (raw + cleaned).
    GET    /api/dictation/stats     → lifetime totals, today, day streak.
    DELETE /api/dictation/history   → purge everything (destructive).
    DELETE /api/dictation/history/{id} → drop one entry.
    POST   /api/dictation/history/{id}/discard → soft-delete (recoverable).
    POST   /api/dictation/history/{id}/restore → un-discard, re-transcribe.
    GET    /api/dictation/settings  → the [dictation] block.
    PUT    /api/dictation/settings  → change one or more keys.
    POST   /api/dictation/polish/test → dry-run the polish pass on a sample.

Delete has two shapes on purpose. ``DELETE /history/{id}`` keeps hard-delete
semantics because that is the contract anyone scripting ``jarvis api dictation``
already relies on; the UI's trash icon calls ``POST .../discard`` instead, so a
mis-click stays recoverable. Both of them, and the full purge, take the audio
sidecar with them — a "deleted" dictation the app still holds a recording of
would be a quiet lie.

Why REST and not only the WebSocket command the chat mic button uses: under the
CLI-first contract (CLAUDE.md §5) a capability that exists only in the UI is not
finished. Mounting this router also makes every action a
``jarvis api dictation <op>`` command for free — and *that* is the documented
fallback on Wayland, where the compositor owns global shortcuts and the app
cannot bind one itself.

No Brain dependency, so it works headless and with a MockBrain; on a host with
no microphone the status endpoint answers honestly instead of 500-ing.

**Sync or async is a deliberate choice per handler, not a style.** The history
is a JSON file that is parsed whole on every read and rewritten on every write,
and a purge unlinks every audio sidecar — blocking work that must never sit on
the event loop a live voice WebSocket shares. Two shapes, no third:

* Everything that only touches the history/stats files is a plain ``def``, so
  FastAPI runs it in its threadpool and the blocking call costs the loop
  nothing (the precedent this follows is ``dictionary_routes.py``).
* Everything that touches the *running pipeline* stays ``async def``, because
  those calls are loop-affine: ``start_dictation`` needs
  ``asyncio.get_running_loop()`` and would return a false "could not start"
  from a worker thread, and ``stop_dictation`` / ``set_keybinds`` set an
  ``asyncio.Event``. Where such a handler also has blocking work to do
  (``PUT /settings`` persisting, restore's re-transcription) that work goes
  through ``asyncio.to_thread`` — never half of it.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dictation", tags=["dictation"])

#: Serializes read-modify-write on the history across the per-request
#: ``DictationHistory`` instances. The store's own lock is per instance and
#: every handler builds a fresh one, so once these handlers run in FastAPI's
#: threadpool two of them really can interleave — where the old all-``async``
#: shape had the event loop serializing them for free. Same pattern, and the
#: same reason, as ``dictionary_routes._LOCK``.
_LOCK = threading.Lock()

#: What a Restore says when there is simply no provider to ask. Not an error:
#: the entry still comes back, it just comes back without its words. Phrased as
#: a fact about this host rather than as a failure of the request, because a
#: 500 here would look like a bug in something the user did nothing wrong in.
_NO_STT_DETAIL = (
    "No speech-to-text provider is reachable on this computer, so the saved "
    "audio could not be transcribed again. The entry itself was restored."
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _config(request: Request) -> Any:
    return getattr(request.app.state, "config", None)


def _pipeline() -> Any:
    """The live SpeechPipeline, or ``None`` (headless / voice disabled)."""
    try:
        from jarvis.core.runtime_refs import get_speech_pipeline

        return get_speech_pipeline()
    except Exception:  # noqa: BLE001 — a missing runtime ref is "no pipeline"
        return None


def _dictation_cfg(request: Request) -> Any:
    cfg = _config(request)
    dictation = getattr(cfg, "dictation", None) if cfg is not None else None
    if dictation is not None:
        return dictation
    from jarvis.core.config import DictationConfig

    return DictationConfig()


def _as_int(value: Any, fallback: int) -> int:
    """Best-effort int from a config value that a hand-edit may have mangled."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _chosen_language(request: Request) -> str:
    """``[dictation].language`` as an STT argument.

    ``"auto"`` is returned as-is rather than as ``None``: the provider contract
    treats an ABSENT argument as "no opinion", which falls back to whatever
    ``[stt].language`` is pinned to, while an explicit ``"auto"`` forces
    detection for that call. Dropping it here is what made a restore re-transcribe
    in the recognition language instead of the spoken one (live bug 2026-07-28).
    """
    chosen = str(getattr(_dictation_cfg(request), "language", "auto") or "").strip()
    return chosen.lower() or "auto"


def _normalized_language(tag: str) -> str:
    """One code per history row, resolved through the canonical helper.

    ``de`` / ``en`` / ``es`` collapse to their code; ``auto`` / ``unknown`` /
    ``und`` and the empty string all mean "not established" and store as ``""``.
    Anything else — a language the shared resolver does not model — is KEPT,
    lower-cased and reduced to its primary subtag. Coercing it would be worse
    than the drift being fixed: relabelling a Japanese dictation as English is a
    lie, and dropping the tag erases the only record of what was heard.
    """
    raw = str(tag or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered in ("auto", "unknown", "und"):
        return ""
    try:
        from jarvis.core.turn_language import normalize_language_tag

        code = normalize_language_tag(lowered)
    except Exception:  # noqa: BLE001 — an unavailable resolver keeps the tag
        return lowered.split("-")[0]
    if code and code != "unknown":
        return code
    return lowered.split("-")[0]


def _restore_stt(pipeline: Any) -> Any:
    """The provider a Restore transcribes with — the DICTATION one, not voice.

    The dictation lane holds its own instance: no ``[stt].bias_prompt`` (which
    the config documents as a silence-hallucination amplifier) and its own
    cross-family fallback chain. A restore that reached past it to the voice
    provider would transcribe the same audio under different decoder priming
    than the dictation that produced it, which is the one thing a "give me that
    back" button must not do.

    Falls back to the voice provider on an older pipeline object (and on the
    duck-typed doubles the route tests build), so this can only ever add
    correctness, never remove a working path.
    """
    if pipeline is None:
        return None
    builder = getattr(pipeline, "_dictation_stt", None)
    if callable(builder):
        try:
            instance = builder()
        except Exception as exc:  # noqa: BLE001 — fall through to the voice one
            log.debug("dictation-specific STT unavailable for restore: %s", exc)
        else:
            if instance is not None:
                return instance
    return getattr(pipeline, "_utterance_stt", None)


async def _format_restored_text(
    raw: str, *, reported: str, request: Request
) -> tuple[str, str]:
    """Run a re-transcription through the delivery chain. ``(text, language)``.

    A Restore that produced different text than the original delivery would be
    a quiet lie about what the button does, so this applies the SAME steps, in
    the same order and with the same decisions: resolve the language, remove
    fillers, repair the punctuation our own segment boundaries broke, and —
    when it is switched on and reachable — polish and, if a translation target
    is configured, translate. Each step owns its own decision in its own module;
    nothing is re-derived here.

    Fail-open at every step, like the delivery path: the user asked for their
    words back, and a formatting bug must never be the reason they do not get
    them.
    """
    from jarvis.dictation.cleanup import clean_transcript, tidy_transcript

    cfg = _dictation_cfg(request)
    pinned = str(getattr(cfg, "language", "auto") or "auto")
    language = pinned if pinned.strip().lower() not in ("", "auto") else reported
    try:
        # The decision itself lives with the live delivery path, so the two
        # cannot drift into cleaning the same recording under different rules.
        # The pipeline module is already imported by the time we get here — a
        # provider answered, which means a pipeline is running — so this costs
        # nothing; the guard is for the case where it somehow is not, and a
        # failed import must never turn a restore into a 500.
        from jarvis.speech.pipeline import resolve_dictation_language

        language = resolve_dictation_language(
            pinned=pinned, reported=reported, text=raw
        )
    except Exception:  # noqa: BLE001 — fall back to pin-then-tag
        log.debug("restore language resolution unavailable", exc_info=True)

    text = raw
    try:
        outcome = clean_transcript(
            raw,
            language=language,
            remove_fillers=bool(getattr(cfg, "remove_fillers", True)),
            max_removed_fraction=float(
                getattr(cfg, "filler_max_removed_fraction", 0.25)
            ),
        )
        text = outcome.text
    except Exception:  # noqa: BLE001 — never lose the text to a cleanup bug
        log.warning("restore cleanup failed; keeping the raw transcript", exc_info=True)
    try:
        text = tidy_transcript(text)
    except Exception:  # noqa: BLE001 — a tidy bug never costs the words
        log.debug("restore tidy failed; keeping the untidied text", exc_info=True)

    try:
        from jarvis.dictation.polish import (
            polish_enabled,
            polish_transcript,
            resolve_translate_target,
        )

        # The translation target is resolved through the SAME function the live
        # delivery uses, for the same reason the language is: a Restore that
        # handed back the original language while the live path translates would
        # be a quiet lie about what the button does.
        translate_to = resolve_translate_target(cfg)
        if text.strip() and (polish_enabled(cfg) or translate_to):
            pipeline = _pipeline()
            terms = ()
            getter = getattr(pipeline, "_dictation_protected_terms", None)
            if callable(getter):
                try:
                    terms = tuple(getter())
                except Exception as exc:  # noqa: BLE001 — a guard input, not a gate
                    log.debug("restore protected terms unavailable: %s", exc)
            result = await polish_transcript(
                text,
                language=language,
                cfg=cfg,
                protected_terms=terms,
                style=str(getattr(cfg, "polish_style", "neutral") or "neutral"),
                translate_to=translate_to,
            )
            text = result.text
            log.info(
                "restore %s: %s (%s, %d ms).",
                f"translation to {translate_to}" if translate_to else "polish",
                result.status,
                result.provider or "no provider",
                result.latency_ms,
            )
    except Exception:  # noqa: BLE001 — never lose the text to the polish pass
        log.warning("restore polish failed; keeping the unpolished text", exc_info=True)

    return text, language


async def _retranscribe_from_audio(
    entry: Any, *, language: str, request: Request
) -> tuple[str, str, str, str | None]:
    """Transcribe a kept audio sidecar again. ``(raw, text, language, detail)``.

    ``raw`` is what the provider returned and ``text`` is what the delivery
    chain makes of it — the same split the live path keeps, so the history row
    still holds the words as heard while the user gets the formatted version.

    ``detail`` is a plain-English explanation of why nothing came back, or
    ``None`` when it did. Every failure path returns one instead of raising:
    the caller turns it into a normal 200 with ``retranscribed: false``, so a
    host without a provider gets an honest sentence rather than a 500.
    """
    from jarvis.dictation.audio import load_dictation_audio

    stt = _restore_stt(_pipeline())
    if stt is None:
        return "", "", "", _NO_STT_DETAIL

    # Reading and decoding a WAV is blocking work; it must not sit on the event
    # loop that a live voice turn shares.
    pcm = await asyncio.to_thread(load_dictation_audio, entry.audio_path)
    if not pcm:
        return "", "", "", "The saved audio for this dictation could not be read."

    try:
        try:
            transcript = await stt.transcribe_pcm(pcm, language=language)
        except TypeError:
            # A provider that predates the keyword — the contract allows a bare
            # ``transcribe_pcm(pcm)``. Falling back beats calling the user's
            # language choice a failure (precedent: rolling_whisper_wake).
            transcript = await stt.transcribe_pcm(pcm)
    except Exception as exc:  # noqa: BLE001 — a failed restore is never a 500
        log.warning("dictation restore transcription failed: %s", exc, exc_info=True)
        return "", "", "", f"Transcribing the saved audio failed: {exc}"

    # ``raw_text`` first: Restore re-runs the SAME delivery chain as the live
    # lane (filler removal under the user's switch, punctuation repair, polish),
    # so it has to start from the same string the lane starts from. Starting
    # from a provider-cleaned one would quietly restore different words than the
    # dictation produced - and ``raw`` is also what the history shows as the
    # original.
    raw = str(
        getattr(transcript, "raw_text", "") or getattr(transcript, "text", "") or ""
    ).strip()
    reported = str(getattr(transcript, "language", "") or "")
    if not raw:
        return (
            "", "", reported,
            "The saved audio produced no text — it may be silence.",
        )
    text, resolved = await _format_restored_text(
        raw, reported=reported, request=request
    )
    return raw, text, resolved, None


def _read_for_restore(entry_id: str) -> tuple[Any, bool]:
    """``(entry, has_audio)`` for one id — ``(None, False)`` when it is gone.

    Blocking on both counts: it parses the whole history file and then stats
    the audio sidecar. Bundled into one helper so the restore handler, which
    has to stay ``async`` for the transcription await, reaches the filesystem
    through a single ``to_thread`` hop instead of three.
    """
    from jarvis.dictation.audio import audio_exists
    from jarvis.dictation.history import DictationHistory

    with _LOCK:
        entry = DictationHistory().get(entry_id)
    if entry is None:
        return None, False
    return entry, audio_exists(entry.audio_path)


def _write_restore(entry_id: str, changes: dict[str, Any]) -> Any:
    """Apply a restore's changes. Blocking — it rewrites the history file."""
    from jarvis.dictation.history import DictationHistory

    with _LOCK:
        return DictationHistory().update(entry_id, **changes)


# ----------------------------------------------------------------------
# Request models
# ----------------------------------------------------------------------


class StartBody(BaseModel):
    target: str = Field(
        default="auto",
        description=(
            # No assistant name here on purpose: this description is served in
            # /docs and in the generated CLI help, and a user-visible string
            # never carries a fixed brand (CLAUDE.md §4). The sentence is about
            # this app's own window, so it needs no name at all.
            "auto = follow [dictation].target (insert, unless this app's own "
            "window is the one in front); insert = always paste into the app "
            "in front; chat = only publish the transcript (fills the chat "
            "composer)"
        ),
    )


class PasteLastBody(BaseModel):
    """Which saved dictation to insert again. Empty body = the newest one."""

    entry_id: str | None = Field(
        default=None,
        description=(
            "Id of a history entry to insert. Omit for the most recent "
            "dictation that still has text."
        ),
    )


class SettingsBody(BaseModel):
    """Partial update — only the keys present are changed."""

    mode: str | None = None
    target: str | None = None
    insert_method: str | None = None
    paste_chord: str | None = None
    paste_delay_ms: int | None = None
    paste_delay_after_ms: int | None = None
    restore_clipboard: bool | None = None
    remove_fillers: bool | None = None
    filler_max_removed_fraction: float | None = None
    max_seconds: float | None = None
    partial_interval_s: float | None = None
    segment_seconds: float | None = None
    final_quality_pass: bool | None = Field(
        default=None,
        description=(
            "Re-transcribe the complete recording after release and deliver "
            "that quality pass instead of the short live-preview segments"
        ),
    )
    final_window_seconds: float | None = Field(
        default=None,
        description="Length of each final transcription window (5-60 seconds)",
    )
    final_overlap_seconds: float | None = Field(
        default=None,
        description="Audio shared by adjacent final windows (0-5 seconds)",
    )
    code_switching: bool | None = Field(
        default=None,
        description=(
            "Let the final recognizer detect language from the audio instead "
            "of locking the request to one configured language"
        ),
    )
    history_enabled: bool | None = None
    history_max_entries: int | None = None
    history_retention_days: int | None = None
    language: str | None = Field(
        default=None,
        description=(
            "Language dictation is transcribed in: auto (detect per utterance, "
            "right for almost everyone) or one supported locale"
        ),
    )
    keep_failed_audio: bool | None = Field(
        default=None,
        description=(
            "Keep the raw audio of a dictation that produced nothing usable, so "
            "it can be transcribed again. Never kept for a successful one."
        ),
    )
    audio_retention_days: int | None = None
    audio_max_files: int | None = None
    # The polish pass. Every key here is also in ``DICTATION_SETTING_KEYS``, so
    # it persists through this one route with no writer change — but FastAPI
    # DROPS body keys a model does not declare, so a key missing from here is a
    # setting the UI appears to save and silently loses on the next restart.
    polish: bool | None = Field(
        default=None,
        description=(
            "Let a fast model re-read the transcript and write it the way you "
            "would have typed it: punctuation, sentence breaks, filler and "
            "false starts removed. The words and the meaning are preserved, "
            "and the raw transcript is always kept in the history. Falls back "
            "to the plain transcript when no model is reachable."
        ),
    )
    polish_provider: str | None = Field(
        default=None,
        description=(
            "Which model family writes the polished text: auto (use whichever "
            "key you already have) or a specific family"
        ),
    )
    polish_model: str | None = Field(
        default=None,
        description="Model id for the chosen family; empty = that family's default",
    )
    polish_timeout_ms: int | None = Field(
        default=None,
        description=(
            "How long the polish pass may take before the plain transcript is "
            "delivered instead (200-5000 ms)"
        ),
    )
    polish_max_input_chars: int | None = None
    polish_min_words: int | None = None
    polish_max_output_tokens: int | None = None
    polish_temperature: float | None = None
    polish_drift_max_shrink: float | None = None
    polish_drift_max_growth: float | None = None
    polish_style: str | None = Field(
        default=None,
        description="Register the polished text is written in",
    )
    polish_precision: bool | None = Field(
        default=None,
        description=(
            "Also sharpen the word choice, not just the writing: a vague "
            "placeholder becomes the specific word you meant, padding collapses "
            "into the plain verb. Simple and exact, never ornate — it will not "
            "reach for a longer synonym to sound impressive. In exchange it "
            "relaxes one safety check (a rare word may now be replaced), so it "
            "is off unless you turn it on. Applies to translated dictations too."
        ),
    )
    polish_conversation: bool | None = Field(
        default=None,
        description=(
            "Also tidy up the transcripts of ordinary conversations, not just "
            "dictation. It never slows a reply down: the assistant answers your "
            "raw words and the tidied version arrives afterwards, for the "
            "transcript view and the session record. What was actually said is "
            "always kept alongside. Needs the wording clean-up above."
        ),
    )
    # The translate pass. Same FastAPI trap as the polish keys above: an
    # undeclared body key is dropped before the handler ever sees it.
    translate: bool | None = Field(
        default=None,
        description=(
            "Deliver every dictation in one fixed language, whatever language "
            "you speak. Speak German, get English. Runs in the same pass as the "
            "wording clean-up, so it costs no extra wait; if no model answers "
            "in time the transcript arrives in the language you spoke."
        ),
    )
    translate_target: str | None = Field(
        default=None,
        description=(
            "The language dictations are delivered in while translate is on. A "
            "language code such as en or de; no auto, because there is nothing "
            "to detect on the output side"
        ),
    )
    translate_drift_max_shrink: float | None = None
    translate_drift_max_growth: float | None = None
    persist: bool = Field(
        default=True, description="Also write the change to jarvis.toml"
    )


# ----------------------------------------------------------------------
# Status + control
# ----------------------------------------------------------------------


@router.get("/status")
async def get_status(request: Request) -> dict[str, Any]:
    """Can dictation run here, is it running, and where would text go?

    Answers honestly on a host that cannot do it rather than hiding the
    feature: ``available`` false plus a ``reason`` the UI can show.
    """
    pipeline = _pipeline()
    cfg = _config(request)
    trigger = getattr(cfg, "trigger", None) if cfg is not None else None
    dictation = _dictation_cfg(request)

    available = False
    active = False
    reason = ""
    if pipeline is None:
        reason = "No speech pipeline is running (headless, or voice is disabled)."
    else:
        try:
            available = bool(pipeline.dictation_available())
            active = bool(pipeline.dictation_active())
            if not available:
                reason = "No microphone or no speech-to-text provider is configured."
        except Exception as exc:  # noqa: BLE001 — never 500 a status probe
            log.debug("dictation availability probe failed: %s", exc)
            reason = "Dictation status could not be read."

    # Whether the transcript could actually be pasted into another app. This is
    # the honest part: on Wayland, on a headless host, or in front of an
    # elevated window it cannot, and saying so up front beats a silent no-op.
    insertion: dict[str, Any] = {"can_insert": False, "reason": "", "detail": ""}
    try:
        from jarvis.dictation.insert import describe_target

        report = describe_target()
        insertion = {
            "can_insert": report.can_insert,
            "reason": report.reason,
            "detail": report.detail,
        }
    except Exception as exc:  # noqa: BLE001
        log.debug("insertion probe failed: %s", exc)

    return {
        "available": available,
        "active": active,
        "reason": reason,
        "hotkey": str(getattr(trigger, "hotkey_dictate", "") or ""),
        # The hands-free key is its own action, not a mode of the hold key, so
        # both can be armed at once. Reported separately for the same reason:
        # a UI that had to infer it from ``mode`` could not show the two rows.
        "hotkey_toggle": str(getattr(trigger, "hotkey_dictate_toggle", "") or ""),
        # "Insert the last dictation again" — its own action and its own row,
        # because it needs neither a microphone nor a provider and therefore
        # stays useful on a host where dictation itself cannot run.
        "hotkey_paste_last": str(getattr(trigger, "hotkey_paste_last", "") or ""),
        "mode": str(getattr(dictation, "mode", "hold")),
        "target": str(getattr(dictation, "target", "auto")),
        "insertion": insertion,
    }


@router.post("/start")
async def start(body: StartBody, request: Request) -> dict[str, Any]:
    """Begin a dictation. 409 when the mic is busy, 503 when there is none.

    Must stay ``async``: ``start_dictation`` calls ``get_running_loop()`` and
    creates the session task on it, so from a threadpool worker it would
    return a false "could not start" on a host that dictates fine.
    """
    pipeline = _pipeline()
    if pipeline is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Dictation needs a running speech pipeline with a microphone; "
                "this host has none."
            ),
        )
    # "auto" defers to the configured target, which the pipeline resolves
    # against the live foreground window at the moment recording starts.
    target = body.target if body.target in ("chat", "insert") else str(
        getattr(_dictation_cfg(request), "target", "auto") or "auto"
    )
    try:
        started = bool(pipeline.start_dictation(target=target))
    except Exception as exc:  # noqa: BLE001
        log.warning("dictation start failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Dictation could not start.") from exc
    if not started:
        raise HTTPException(
            status_code=409,
            detail=(
                "Dictation could not start — a voice session is running, "
                "dictation is already active, or the microphone is not ready."
            ),
        )
    return {"ok": True, "active": True, "target": target}


@router.post("/stop")
async def stop() -> dict[str, Any]:
    """Finish the running dictation. Idempotent: stopping nothing is not an error.

    ``async`` for the same reason as ``start``: the stop signal is an
    ``asyncio.Event``, which belongs to the pipeline's loop thread.
    """
    pipeline = _pipeline()
    if pipeline is None:
        return {"ok": True, "stopped": False, "active": False}
    try:
        stopped = bool(pipeline.stop_dictation())
    except Exception as exc:  # noqa: BLE001
        log.warning("dictation stop failed: %s", exc, exc_info=True)
        stopped = False
    return {"ok": True, "stopped": stopped, "active": False}


@router.post("/paste-last")
def paste_last(body: PasteLastBody, request: Request) -> dict[str, Any]:
    """Insert the most recent dictation into the focused field again.

    The recovery action for a paste that landed nowhere. It reads the local
    history rather than the clipboard, and that is not an implementation
    detail: a successful paste deliberately puts the PREVIOUS clipboard
    content back, so the transcript is off the clipboard within a second. The
    history is the only durable copy — which is also why this refuses honestly
    when the history is switched off instead of keeping a hidden copy behind
    the user's privacy setting.

    Needs no microphone, no speech-to-text and no speech pipeline, so it works
    on a host where dictation itself cannot run. It goes through the SAME
    delivery path a fresh dictation uses, so the two can never drift apart.

    Where insertion is impossible — Wayland, a headless host, an elevated
    window in front, macOS secure input — this is still a 200: the text goes
    to the clipboard and the answer carries the plain-English sentence
    explaining what happened, exactly as a normal dictation would. 409 means
    the history is off, 404 means there is nothing saved to paste.

    Plain ``def`` on purpose: it parses the whole history file and sleeps
    around the paste chord, which is blocking work FastAPI absorbs in its
    threadpool but which must never sit on the loop a live voice turn shares.
    """
    from jarvis.dictation.insert import insert_last_dictation

    result = insert_last_dictation(
        entry_id=body.entry_id, settings=_dictation_cfg(request)
    )
    if result.reason == "history_disabled":
        raise HTTPException(status_code=409, detail=result.detail)
    if result.reason == "not_found":
        raise HTTPException(status_code=404, detail=result.detail)

    insertion = result.insert
    return {
        "ok": result.ok,
        "entry_id": result.entry_id,
        # The text travels in the body so a CLI or SSH user still gets their
        # words on a host where nothing can be typed into anything.
        "text": result.text,
        "status": getattr(insertion, "status", "unavailable"),
        "detail": result.detail,
        "method": getattr(insertion, "method", ""),
        "clipboard_holds_text": bool(
            getattr(insertion, "clipboard_holds_text", False)
        ),
        "clipboard_restored": bool(getattr(insertion, "clipboard_restored", False)),
    }


# ----------------------------------------------------------------------
# History
# ----------------------------------------------------------------------


@router.get("/history")
def get_history(
    limit: int = 50,
    include_discarded: bool = Query(
        default=False,
        description=(
            "Also return entries the user discarded. The UI asks for them "
            "because they are the ones Restore exists for."
        ),
    ),
) -> dict[str, Any]:
    """Recent dictations, newest first — raw text alongside the cleaned text.

    Local-only data. It exists so a filler-cleanup can be audited after the
    fact ("did it drop a word I actually said?") and so a transcript survives
    an insertion that had to fall back to the clipboard.

    Discarded entries are hidden by default, which is what a script reading
    "the history" expects. The UI opts back in: filtering them out there would
    strand the Restore button that makes the soft delete worth having.

    The wire shape never carries ``audio_path`` — a filesystem path in a JSON
    body is an information leak that buys the client nothing, so the entry
    reports ``audio_available`` instead.
    """
    from jarvis.dictation.history import DictationHistory

    capped = max(1, min(int(limit or 50), 500))
    with _LOCK:
        entries = DictationHistory().list_all(include_discarded=include_discarded)
    entries = entries[:capped]
    return {"entries": [e.to_dict() for e in entries], "count": len(entries)}


@router.get("/stats")
def get_stats(request: Request) -> dict[str, Any]:
    """Lifetime dictation totals, today's numbers and the day streak.

    ``source`` is the honest part and the UI must label the panel from it:

    * ``lifetime`` — the never-pruned counter sidecar answered, so the totals
      really are all-time.
    * ``window`` — no sidecar exists yet (an install that predates it), so the
      numbers were derived from the rolling history window. They are real, they
      are just bounded by the retention settings, and calling a 30-day slice
      "all time" would be a lie the user has no way to catch.

    ``window`` reports the retention settings the fallback is bounded by, so
    the UI can name the period instead of guessing at it.
    """
    from jarvis.dictation.history import DictationHistory
    from jarvis.dictation.stats import DEFAULT_BY_DAY_LIMIT, summarize_entries

    history = DictationHistory()
    with _LOCK:
        counters = history.stats()
        if counters.exists:
            payload = counters.summary(by_day_limit=DEFAULT_BY_DAY_LIMIT)
        else:
            payload = summarize_entries(
                history.list_all(), by_day_limit=DEFAULT_BY_DAY_LIMIT
            )

    dictation = _dictation_cfg(request)
    payload["window"] = {
        "days": _as_int(getattr(dictation, "history_retention_days", 30), 30),
        "max_entries": _as_int(getattr(dictation, "history_max_entries", 200), 200),
    }
    return payload


@router.post("/history/{entry_id}/discard")
def discard_history_entry(entry_id: str) -> dict[str, Any]:
    """Hide one entry without deleting it — the recoverable trash icon.

    Soft on purpose. ``discarded`` is a boolean beside the outcome rather than
    an outcome of its own, because an entry can be both ``inserted`` and
    discarded, and folding the two into one string makes that unrepresentable.
    """
    from jarvis.dictation.history import DictationHistory

    history = DictationHistory()
    with _LOCK:
        if history.get(entry_id) is None:
            raise HTTPException(status_code=404, detail="No dictation has that id.")
        updated = history.set_discarded(entry_id, True)
    if updated is None:
        raise HTTPException(
            status_code=500, detail="The dictation entry could not be updated."
        )
    return {"ok": True, "entry": updated.to_dict()}


@router.post("/history/{entry_id}/restore")
async def restore_history_entry(entry_id: str, request: Request) -> dict[str, Any]:
    """Un-discard one entry and, when there is text to win back, re-transcribe.

    Two different jobs behind one button, because from the user's side they are
    one thing ("give me that back"):

    1. A discarded entry that still has its text simply stops being hidden.
    2. An entry that ended with nothing — a provider 401, a wedged engine, a
       transcript that came back empty — is transcribed again from the audio
       that was kept for exactly this moment.

    Never a 500 on a missing provider. A host with no speech-to-text reachable
    still restores the entry and says why the words did not come back; that is
    a disappointment, not a failed request.

    The one handler here that genuinely has to await, so it is also the one
    that has to thread by hand: every filesystem touch goes through
    ``asyncio.to_thread``, and the lock is taken inside those helpers rather
    than held across the transcription — a restore that waits on a slow
    provider must not freeze every other history call for the duration.
    """
    from jarvis.dictation.cleanup import count_words

    entry, has_audio = await asyncio.to_thread(_read_for_restore, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="No dictation has that id.")

    has_text = bool(entry.text or entry.raw_text)
    if not has_text and not has_audio:
        raise HTTPException(
            status_code=409,
            detail=(
                "There is nothing to restore: this dictation has no text and no "
                "saved audio. Keeping audio for failed dictations is what makes "
                "one recoverable."
            ),
        )

    changes: dict[str, Any] = {"discarded": False}
    detail: str | None = None
    retranscribed = False
    if not has_text:
        raw, text, detected, detail = await _retranscribe_from_audio(
            entry, language=_chosen_language(request), request=request
        )
        if text:
            retranscribed = True
            changes.update(
                # The provider's words and the formatted words are stored
                # separately, exactly as a live dictation stores them: the raw
                # column is what makes "give me the text I actually said"
                # answerable after a polish pass has run.
                raw_text=raw,
                text=text,
                word_count=count_words(text),
                # Normalized rather than stored verbatim: the same recording
                # transcribed twice can come back tagged "German" once and "de"
                # the next time, and a consumer doing ``{"de": ...}.get(lang)``
                # misses on the first. The store normalizes on write as well —
                # this is the belt to that brace, and it also keeps the value
                # the RESPONSE carries consistent with what was resolved.
                language=_normalized_language(detected or entry.language),
                # The recorded failure is over — but the OUTCOME stays what it
                # was. The dictation really did fail to reach the window the
                # user was typing in; rewriting it to "inserted" would invent
                # a delivery that never happened.
                error=None,
            )

    updated = await asyncio.to_thread(_write_restore, entry_id, changes)
    if updated is None:
        raise HTTPException(
            status_code=500, detail="The dictation entry could not be updated."
        )
    return {
        "ok": True,
        "entry": updated.to_dict(),
        "retranscribed": retranscribed,
        "detail": detail,
    }


@router.delete(
    "/history",
    openapi_extra={"x-jarvis-dangerous": True},
)
def clear_history() -> dict[str, Any]:
    """Purge the whole dictation history. Irreversible.

    Deliberately total: the entries, every kept audio sidecar and the lifetime
    counters all go, which is why the UI copy has to say the day streak resets.
    Leaving the counters standing after someone asked for their dictation
    history to be deleted would be a quiet lie about what the app still knows.
    """
    from jarvis.dictation.history import DictationHistory

    with _LOCK:
        cleared = bool(DictationHistory().clear())
    return {"ok": cleared}


@router.delete("/history/{entry_id}")
def delete_history_entry(entry_id: str) -> dict[str, Any]:
    """Drop one entry and its audio (idempotent — an absent id is not an error).

    Hard delete, kept that way on purpose: this is the contract anyone
    scripting ``jarvis api dictation`` already has. The recoverable version the
    UI's trash icon uses is ``POST /history/{id}/discard``.
    """
    from jarvis.dictation.history import DictationHistory

    with _LOCK:
        removed = bool(DictationHistory().delete(entry_id))
    return {"removed": removed}


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    """The live ``[dictation]`` block plus the accepted values per key.

    ``choices`` is what every dropdown in the UI is built from, and it is
    hand-maintained: a key added to ``DICTATION_SETTING_KEYS`` without an entry
    here renders an empty list the user cannot pick anything out of. The
    language and paste-chord lists are the exceptions — they are derived from
    ``DICTATION_LANGUAGES`` and ``PASTE_CHORDS`` so adding one means touching
    one place.

    ``custom`` describes the keys that also accept a RECORDED value, and it
    carries the accepted token vocabulary rather than expecting the frontend to
    keep its own copy — a hand-mirrored key list is the AP-4 drift trap, and
    the cost of getting it wrong here is a recorder that happily captures a key
    the actuator cannot send, which then fails silently at paste time.
    """
    from jarvis.core.config import (
        DICTATION_LANGUAGES,
        POLISH_STYLES,
        TRANSLATION_TARGETS,
    )
    from jarvis.core.config_writer import DICTATION_SETTING_KEYS
    from jarvis.dictation.insert import (
        CUSTOM_CHORD_KEYS,
        CUSTOM_CHORD_MODIFIERS,
        PASTE_CHORDS,
    )
    from jarvis.dictation.polish_client import POLISH_FAMILIES

    dictation = _dictation_cfg(request)
    values = {key: getattr(dictation, key, None) for key in DICTATION_SETTING_KEYS}
    return {
        "settings": values,
        "choices": {
            "mode": ["hold", "toggle"],
            "target": ["auto", "insert", "chat"],
            "insert_method": ["clipboard", "type"],
            "paste_chord": ["auto", *PASTE_CHORDS],
            "language": list(DICTATION_LANGUAGES),
            # Both derived from their single source of truth rather than typed
            # out here: a hand-mirrored list is the AP-4 drift trap, and the
            # cost of getting it wrong is a dropdown offering a value the
            # backend rejects (or hiding one it accepts).
            "polish_style": list(POLISH_STYLES),
            "polish_provider": ["auto", *(family.id for family in POLISH_FAMILIES)],
            # No "auto" here, unlike ``language``: the output side has nothing
            # to detect, so an auto entry would be a dropdown option that
            # silently does nothing (AP-31).
            "translate_target": list(TRANSLATION_TARGETS),
        },
        "custom": {
            "paste_chord": {
                "allowed": True,
                "separator": "+",
                "modifiers": sorted(set(CUSTOM_CHORD_MODIFIERS.values())),
                "keys": sorted(set(CUSTOM_CHORD_KEYS.values())),
                # The honest label for the feature. Jarvis does not paste — it
                # asks the app in front to paste by sending this combination,
                # so a combination that app does not bind does nothing, and the
                # result is reported as "paste_sent", never as "inserted".
                "detail": (
                    "The paste shortcut of the app you dictate into. A "
                    "shortcut that app does not use does nothing, and there is "
                    "no way to tell from here — so the text is left on your "
                    "clipboard instead of being cleaned up afterwards."
                ),
            }
        },
    }


@router.put("/settings")
async def put_settings(body: SettingsBody, request: Request) -> dict[str, Any]:
    """Change one or more ``[dictation]`` keys.

    Validated against ``DictationConfig`` BEFORE anything is written, so an
    out-of-range delay or an unknown mode is a 400 rather than a config file
    the app then refuses to boot from. Applies live to the running pipeline;
    ``max_seconds`` and the shortcut itself take effect immediately, and the
    rest are read per dictation anyway.

    Stays ``async`` for the live-apply, not for the write: ``set_keybinds``
    sets an ``asyncio.Event`` and so belongs on the loop thread, while writing
    ``jarvis.toml`` (lock + tempfile + replace, once per changed key) does not
    — that part is pushed to a worker thread.

    Saving also drops what the polish pass has learned about this host, so a
    changed provider, a repaired key or a local model that was just started
    takes effect on the very next dictation.
    """
    from jarvis.core.config import DictationConfig

    updates = {
        key: value
        for key, value in body.model_dump(exclude={"persist"}).items()
        if value is not None
    }
    if not updates:
        raise HTTPException(status_code=400, detail="No settings were provided.")

    if "paste_chord" in updates:
        # The model validator falls back to "auto" instead of raising, because
        # a hand-edited config must never fail to load (AP-16). That is the
        # wrong answer for someone who just recorded a shortcut, though: they
        # would see the setting silently revert. So the same normalizer runs
        # here, where its rejection sentence can actually reach the user.
        from jarvis.dictation.insert import normalize_paste_chord

        canonical, problem = normalize_paste_chord(str(updates["paste_chord"]))
        if problem:
            raise HTTPException(status_code=400, detail=problem)
        updates["paste_chord"] = canonical

    if "polish_provider" in updates:
        # Same reasoning as the block above, for the same reason it is needed.
        # ``DictationConfig`` accepts any string here on purpose (checking it
        # would put the provider registry on the config-load path, AP-26), and
        # ``resolve_polish_chain`` then ignores an unrecognised id in favour of
        # the auto order — the right answer for a hand-edited file (AP-16), the
        # wrong one for someone who just clicked a provider card: the save
        # returned 200, the UI said "saved", and the pin did nothing (AP-31).
        #
        # Two vocabularies reach this key: the polish FAMILY id the config
        # stores ("openai") and the provider-card id the UI is built from
        # ("openai-polish"), which differ because a bare "openai" is already the
        # brain card. A card id is a well-meant pin, so it is translated rather
        # than refused — that also keeps an older frontend working against this
        # backend. Anything neither vocabulary knows is refused OUT LOUD.
        from jarvis.dictation.polish_client import POLISH_FAMILIES, family_by_id
        from jarvis.ui.web.provider_spec import dictation_family_id

        raw = str(updates["polish_provider"] or "").strip().lower()
        canonical = raw or "auto"
        if canonical != "auto" and family_by_id(canonical) is None:
            canonical = dictation_family_id(canonical) or canonical
        if canonical != "auto" and family_by_id(canonical) is None:
            known = ", ".join(family.id for family in POLISH_FAMILIES)
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown dictation polish provider {raw!r}. Use 'auto' "
                    f"(pick whichever key you have) or one of: {known}."
                ),
            )
        updates["polish_provider"] = canonical

    dictation = _dictation_cfg(request)
    current = {
        key: getattr(dictation, key)
        for key in DictationConfig.model_fields
        if hasattr(dictation, key)
    }
    try:
        validated = DictationConfig(**{**current, **updates})
    except Exception as exc:  # noqa: BLE001 — pydantic ValidationError and friends
        raise HTTPException(status_code=400, detail=f"Invalid setting: {exc}") from exc

    persisted = False
    if body.persist:
        from jarvis.core import config_writer

        def _persist() -> None:
            for key in updates:
                config_writer.set_dictation_setting(key, getattr(validated, key))

        try:
            await asyncio.to_thread(_persist)
            persisted = True
        except Exception as exc:  # noqa: BLE001
            log.warning("dictation settings persist failed: %s", exc)
            # A live-only change looks successful until the next restart, when
            # the old value returns from disk. That is worse than refusing the
            # save: the settings screen would explicitly promise durability it
            # did not achieve. Apply nothing live and make the failure visible
            # to every client (desktop, CLI, or another REST consumer).
            raise HTTPException(
                status_code=500,
                detail="Dictation settings could not be saved. No live setting was changed.",
            ) from exc

    # Apply only after the durable write succeeds. ``persist=false`` remains a
    # deliberate live-only API operation; the desktop always sends true.
    for key in updates:
        try:
            setattr(dictation, key, getattr(validated, key))
        except Exception as exc:  # noqa: BLE001 — frozen model is not an error
            log.debug("in-memory dictation.%s update skipped: %s", key, exc)

    # The polish pass caches what it learned about this host: the resolved
    # provider chain, every credential it read, the local endpoints that did not
    # answer, and a breaker a dead provider opened. All of it survives until
    # something says otherwise, and a save is that something — a user who just
    # switched provider, repaired a key or started their local model must not
    # wait out a 120 s cooldown earned by the old setup. Unconditional on
    # purpose: the pass reads more of this block than the polish_* keys alone
    # (the language, the recognizer behind it), and re-sweeping once costs a
    # few milliseconds on a path the user is already waiting on.
    try:
        from jarvis.dictation.polish import reset_polish_state

        reset_polish_state()
    except Exception as exc:  # noqa: BLE001 — never fail a save on a cache drop
        log.warning("polish state reset after a dictation save failed: %s", exc)

    # Live-apply what the running pipeline caches at construction time.
    applied_live = False
    pipeline = _pipeline()
    if pipeline is not None:
        try:
            pipeline._dictation_cfg = dictation
            pipeline._dictation_max_s = float(validated.max_seconds)
            # The dictation lane caches its own STT instance, built from this
            # config (its bias prompt, its fallback chain). Dropping it here is
            # what makes a change take effect on the next press instead of on
            # the next restart — the difference between a setting and a setting
            # that appears to work.
            reset = getattr(pipeline, "_reset_dictation_stt", None)
            if callable(reset):
                reset()
            if "mode" in updates:
                pipeline._dictate_mode = validated.mode
                # The mode decides whether the binding wants both key edges,
                # so the trigger has to re-arm for a hold<->toggle switch.
                if hasattr(pipeline, "set_keybinds"):
                    pipeline.set_keybinds()
            applied_live = True
        except Exception as exc:  # noqa: BLE001 — never fail a save on live-apply
            log.warning("dictation settings live-apply failed: %s", exc)

    return {
        "ok": True,
        "settings": {
            key: getattr(validated, key)
            for key in DictationConfig.model_fields
        },
        "persisted": persisted,
        "applied_live": applied_live,
    }


#: The transcript the polish dry-run sends. Deliberately shaped like the real
#: defect and not like a demo sentence: two segments joined mid-thought, the
#: recognizer's own trailing ellipsis, a lower-case restart, a filler, a false
#: start the speaker corrected, and a spoken number. English, because it is a
#: fixed artifact in the repo rather than user content — the pass itself works
#: in whatever language it is handed.
_POLISH_SAMPLE = (
    "so um i think we should ship the report on tuesday ... actually "
    "wednesday and send it to three people on the team then we can talk "
    "about the rest tomorrow"
)

#: The sample used INSTEAD when precision mode is on. The one above exercises
#: the formatter and nothing else — it is already made of plain, specific words,
#: so a precision run returns the same sentence the ordinary run does, and the
#: user reads that as "the switch I just turned on does nothing" (AP-31). A dry
#: run has to show the thing it is testing.
#:
#: Shaped like the defect precision mode exists for, and it still carries every
#: defect the sample above does so the test never gets WEAKER when the mode is
#: on: fillers, a lower-case restart, no punctuation, a spoken number. On top of
#: those it adds what only precision may touch — two hedges ("basically",
#: "kind of"), two padding constructions ("make a decision", "give an
#: explanation") and one circumlocution for a thing that has a name ("the thing
#: that stores all our customer data").
_POLISH_PRECISION_SAMPLE = (
    "so um i basically want to make a decision about the thing that stores "
    "all our customer data and i think we should kind of give an explanation "
    "to the three people who are in charge of it tomorrow"
)


@router.post("/polish/test")
async def test_polish(request: Request) -> dict[str, Any]:
    """Run one fixed sample through the resolved polish chain and report it.

    The honest answer to "is this switched on, who answers, and how long does it
    cost me" — which nothing else can give, because the pass is invisible by
    design when it works and silently falls back to the raw text when it does
    not. It uses the live ``[dictation]`` config, so what it measures is what a
    real dictation would get, and it deliberately does NOT persist anything or
    touch the history: it is a probe, not an action, so it carries no
    destructive-route marker.

    Never 500s. A host with no key in any family gets ``status: unavailable``
    and the sample back unchanged — the same answer a dictation would get, and
    the honest one on an install that never configured a text model.
    """
    from jarvis.dictation.polish import (
        polish_enabled,
        polish_transcript,
        precision_enabled,
        resolve_translate_target,
    )

    cfg = _dictation_cfg(request)
    # Whatever a real dictation would get, including the translation — the probe
    # is the only way to SEE this feature, so it has to run the same pass. The
    # fixed sample is English, so a target of English exercises the
    # already-in-target path, which is the honest answer for that setup.
    translate_to = resolve_translate_target(cfg)
    # The sample follows the mode, so the dry run always demonstrates the
    # settings actually in force rather than a fixed subset of them.
    sample = (
        _POLISH_PRECISION_SAMPLE if precision_enabled(cfg) else _POLISH_SAMPLE
    )
    if not polish_enabled(cfg) and not translate_to:
        # Reported rather than refused: "you switched it off" is a complete
        # answer to "why is my dictation not being polished", and a 409 here
        # would make the settings screen render an error for a working config.
        return {
            "status": "off",
            "provider": "",
            "model": "",
            "latency_ms": 0,
            "reason": "",
            "sample_in": sample,
            "sample_out": sample,
        }

    pipeline = _pipeline()
    terms: tuple[str, ...] = ()
    getter = getattr(pipeline, "_dictation_protected_terms", None)
    if callable(getter):
        try:
            terms = tuple(getter())
        except Exception as exc:  # noqa: BLE001 — a guard input, never a gate
            log.debug("polish test protected terms unavailable: %s", exc)

    result = await polish_transcript(
        sample,
        language="en",
        cfg=cfg,
        protected_terms=terms,
        style=str(getattr(cfg, "polish_style", "neutral") or "neutral"),
        translate_to=translate_to,
    )
    return {
        "status": result.status,
        "provider": result.provider,
        "model": result.model,
        "latency_ms": result.latency_ms,
        # Machine-readable cause when a guard or the transport refused. Shown
        # next to the status because "rejected_drift" without "lost_term" tells
        # a user nothing they can act on.
        "reason": result.reason,
        "sample_in": sample,
        "sample_out": result.text,
    }
