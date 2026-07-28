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


def _pinned_language(request: Request) -> str | None:
    """``[dictation].language`` as an STT argument — ``None`` means "detect"."""
    pinned = str(getattr(_dictation_cfg(request), "language", "auto") or "").strip()
    lowered = pinned.lower()
    return lowered if lowered and lowered != "auto" else None


async def _retranscribe_from_audio(
    entry: Any, *, language: str | None
) -> tuple[str, str, str | None]:
    """Transcribe a kept audio sidecar again. ``(text, language, detail)``.

    ``detail`` is a plain-English explanation of why nothing came back, or
    ``None`` when it did. Every failure path returns one instead of raising:
    the caller turns it into a normal 200 with ``retranscribed: false``, so a
    host without a provider gets an honest sentence rather than a 500.
    """
    from jarvis.dictation.audio import load_dictation_audio

    pipeline = _pipeline()
    # The pipeline's final-transcription provider is the right one to reuse:
    # it is already wrapped with the user's spoken-vocabulary corrections, so a
    # restore spells names the same way the original dictation would have.
    stt = getattr(pipeline, "_utterance_stt", None) if pipeline is not None else None
    if stt is None:
        return "", "", _NO_STT_DETAIL

    # Reading and decoding a WAV is blocking work; it must not sit on the event
    # loop that a live voice turn shares.
    pcm = await asyncio.to_thread(load_dictation_audio, entry.audio_path)
    if not pcm:
        return "", "", "The saved audio for this dictation could not be read."

    try:
        if language is None:
            transcript = await stt.transcribe_pcm(pcm)
        else:
            try:
                transcript = await stt.transcribe_pcm(pcm, language=language)
            except TypeError:
                # A provider that predates the keyword — the contract allows a
                # bare ``transcribe_pcm(pcm)``. Falling back beats calling the
                # user's language pin a failure (precedent: rolling_whisper_wake).
                transcript = await stt.transcribe_pcm(pcm)
    except Exception as exc:  # noqa: BLE001 — a failed restore is never a 500
        log.warning("dictation restore transcription failed: %s", exc, exc_info=True)
        return "", "", f"Transcribing the saved audio failed: {exc}"

    text = str(getattr(transcript, "text", "") or "").strip()
    detected = str(getattr(transcript, "language", "") or "")
    if not text:
        return "", detected, "The saved audio produced no text — it may be silence."
    return text, detected, None


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
        text, detected, detail = await _retranscribe_from_audio(
            entry, language=_pinned_language(request)
        )
        if text:
            retranscribed = True
            changes.update(
                raw_text=text,
                text=text,
                word_count=count_words(text),
                language=detected or entry.language,
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
    from jarvis.core.config import DICTATION_LANGUAGES
    from jarvis.core.config_writer import DICTATION_SETTING_KEYS
    from jarvis.dictation.insert import (
        CUSTOM_CHORD_KEYS,
        CUSTOM_CHORD_MODIFIERS,
        PASTE_CHORDS,
    )

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

    # In-memory first so a running pipeline sees the change even if the write
    # fails (a read-only config file must not silently drop the setting).
    for key in updates:
        try:
            setattr(dictation, key, getattr(validated, key))
        except Exception as exc:  # noqa: BLE001 — frozen model is not an error
            log.debug("in-memory dictation.%s update skipped: %s", key, exc)

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

    # Live-apply what the running pipeline caches at construction time.
    applied_live = False
    pipeline = _pipeline()
    if pipeline is not None:
        try:
            pipeline._dictation_cfg = dictation
            pipeline._dictation_max_s = float(validated.max_seconds)
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
