"""REST API for user-facing app settings that the live runtime must honour.

Covers the Reply Language pin (desktop "Languages" view → Reply Language) and
the custom Wake Word (desktop "Settings" → Wake Word panel).

Endpoints:
    GET /api/settings/reply-language  → {"language": ..., "options": [...]}
    PUT /api/settings/reply-language  → switch the live BrainManager + persist
    GET /api/settings/wake-word       → {phrase, engine, custom_model_path,
                                         sensitivity, fuzzy_match_ratio, engines,
                                         instant_phrases, local_whisper_available}
    PUT /api/settings/wake-word       → persist to jarvis.toml [trigger.wake_word]
                                         (+ resolved-plan preview); restart required

Why a dedicated route (not localStorage): the reply language has to reach the
BrainManager so ``_build_system_prompt`` can emit the language directive — the
choice was previously stranded in the browser and silently ignored. Both the
voice and the chat path share one BrainManager, so this single setter covers
both. Mirrors the provider-switch pattern in ``provider_routes.py``.

Wired into the WebServer in ``server.py::_build_app`` via
    from .settings_routes import router as settings_router
    app.include_router(settings_router)
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from jarvis.brain.manager import SUPPORTED_REPLY_LANGUAGES
from jarvis.memory.wiki.integration import get_running_curator

if TYPE_CHECKING:
    from jarvis.core.config import WikiCuratorConfig

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ReplyLanguageBody(BaseModel):
    language: str = Field(..., min_length=1)
    persist: bool = Field(default=True, description="Persist as boot default in jarvis.toml")


def _require_brain(request: Request):
    brain = getattr(request.app.state, "brain", None)
    if brain is None or not hasattr(brain, "set_reply_language"):
        raise HTTPException(
            status_code=503,
            detail="Brain manager not available (likely headless mode)",
        )
    return brain


@router.get("/reply-language")
async def get_reply_language(request: Request) -> dict[str, object]:
    brain = _require_brain(request)
    return {
        "language": getattr(brain, "reply_language", "auto"),
        "options": list(SUPPORTED_REPLY_LANGUAGES),
    }


@router.put("/reply-language")
async def put_reply_language(body: ReplyLanguageBody, request: Request) -> dict[str, object]:
    brain = _require_brain(request)

    # The BrainManager owns validation (single source of truth). An unknown
    # code raises ValueError → surface as 400, live state untouched.
    try:
        brain.set_reply_language(body.language)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    lang = brain.reply_language

    # Best-effort in-memory cfg update so a later cfg read agrees.
    cfg = getattr(request.app.state, "config", None) or getattr(
        request.app.state, "cfg", None
    )
    if cfg is not None and getattr(cfg, "brain", None) is not None:
        try:
            cfg.brain.reply_language = lang  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — frozen model is not an error
            log.debug("in-memory cfg.brain.reply_language update skipped: %s", exc)

    # Persist as boot default. Best-effort: a read-only / locked jarvis.toml
    # must not break the live switch that already succeeded above.
    persisted = False
    if body.persist:
        try:
            from jarvis.core import config_writer

            config_writer.set_reply_language(lang)
            persisted = True
        except Exception as exc:  # noqa: BLE001
            log.warning("reply-language persist failed (live switch still applied): %s", exc)

    return {"ok": True, "language": lang, "persisted": persisted}


# ----------------------------------------------------------------------
# Team / hosted-proxy mode ([team_proxy]) — 2026-06-20 team-proxy spec §4.
# One global switch: when enabled with a url, every provider not in
# local_providers is routed through {url}/p/<id> with the per-user team token
# instead of a real vendor key. The token is a secret (slot team_proxy_token),
# stored via the normal /secrets route — never written to jarvis.toml here.
# ----------------------------------------------------------------------
class TeamProxyBody(BaseModel):
    enabled: bool = False
    url: str = ""
    local_providers: list[str] = Field(default_factory=list)


def _resolve_cfg(request: Request):
    return getattr(request.app.state, "config", None) or getattr(
        request.app.state, "cfg", None
    )


@router.get("/team-proxy")
async def get_team_proxy(request: Request) -> dict[str, object]:
    from jarvis.core import config as cfg_mod

    conf = _resolve_cfg(request) or cfg_mod.load_config()
    tp = conf.team_proxy
    token_set = bool(cfg_mod.get_secret("team_proxy_token", "TEAM_PROXY_TOKEN"))
    return {
        "enabled": bool(tp.enabled),
        "url": tp.url or "",
        "local_providers": list(tp.local_providers),
        "token_configured": token_set,
    }


@router.put("/team-proxy")
async def put_team_proxy(body: TeamProxyBody, request: Request) -> dict[str, object]:
    url = (body.url or "").strip()
    if body.enabled and not url:
        raise HTTPException(status_code=400, detail="Team mode requires a proxy url.")

    # Best-effort in-memory update so a later cfg read this session agrees. A
    # provider already holding a cached client keeps its endpoint until rebuilt
    # (provider switch / restart) — only new provider instances pick this up.
    conf = _resolve_cfg(request)
    if conf is not None and getattr(conf, "team_proxy", None) is not None:
        try:
            conf.team_proxy.enabled = bool(body.enabled)
            conf.team_proxy.url = url or None
            conf.team_proxy.local_providers = list(body.local_providers)
        except Exception as exc:  # noqa: BLE001 — frozen model is not an error
            log.debug("in-memory team_proxy update skipped: %s", exc)

    persisted = False
    try:
        from jarvis.core import config_writer

        config_writer.set_team_proxy(bool(body.enabled), url, list(body.local_providers))
        persisted = True
    except Exception as exc:  # noqa: BLE001 — a locked/read-only toml must not 500
        log.warning("team-proxy persist failed: %s", exc)

    return {
        "ok": True,
        "enabled": bool(body.enabled),
        "url": url,
        "local_providers": list(body.local_providers),
        "persisted": persisted,
    }


# ----------------------------------------------------------------------
# Interface (display) language — what the user SEES (every label/button).
# Distinct from the reply language. The frontend used to keep this only in
# localStorage; giving it a backend home lets a voice command / the Control API
# change it and the open UI switch live (a UiLanguageChanged event is forwarded
# over /ws). Key-free same-origin route, like reply-language.
# ----------------------------------------------------------------------

_UI_LANGUAGES: tuple[str, ...] = ("en", "de", "es")


class UiLanguageBody(BaseModel):
    language: str = Field(..., min_length=1)
    persist: bool = Field(default=True, description="Persist as boot default in jarvis.toml")


def _current_ui_language(request: Request) -> str:
    # Read fresh from disk so a value just written by voice/the Control API is
    # reflected; fall back to the boot config, then the "en" default.
    try:
        from jarvis.core.config import load_config

        return str(getattr(load_config().ui, "language", "en"))
    except Exception as exc:  # noqa: BLE001 — never 500 a settings read
        log.debug("ui-language fresh read failed, using boot config: %s", exc)
    cfg = getattr(request.app.state, "config", None)
    return str(getattr(getattr(cfg, "ui", None), "language", "en"))


@router.get("/ui-language")
async def get_ui_language(request: Request) -> dict[str, object]:
    return {"language": _current_ui_language(request), "options": list(_UI_LANGUAGES)}


@router.put("/ui-language")
async def put_ui_language(body: UiLanguageBody, request: Request) -> dict[str, object]:
    lang = (body.language or "").strip().lower()
    if lang not in _UI_LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown UI language {body.language!r} (allowed: {list(_UI_LANGUAGES)})",
        )

    persisted = False
    if body.persist:
        try:
            from jarvis.core import config_writer
            from jarvis.core.config import resolve_config_path

            # Honour JARVIS_CONFIG (cloud-first) so the write lands in the same
            # file load_config reads — no desktop/VPS split-brain.
            config_writer.set_ui_language(lang, path=resolve_config_path())
            persisted = True
        except Exception as exc:  # noqa: BLE001 — persist is best-effort
            log.warning("ui-language persist failed: %s", exc)

    cfg = getattr(request.app.state, "config", None)
    if cfg is not None and getattr(cfg, "ui", None) is not None:
        try:
            cfg.ui.language = lang  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — frozen model is not an error
            log.debug("in-memory cfg.ui.language update skipped: %s", exc)

    # Broadcast so EVERY open frontend (and other clients) switch live.
    bus = getattr(request.app.state, "bus", None)
    if bus is not None:
        try:
            from jarvis.core.events import UiLanguageChanged

            await bus.publish(UiLanguageChanged(language=lang))
        except Exception as exc:  # noqa: BLE001 — a bus hiccup must not fail the write
            log.warning("UiLanguageChanged publish failed: %s", exc)

    return {"ok": True, "language": lang, "persisted": persisted}


# ----------------------------------------------------------------------
# STT recognition language — the language Whisper TRANSCRIBES the user's voice
# into. Distinct from BOTH the UI language (what the user sees) and the reply
# language (what Jarvis answers in). ``auto`` lets Whisper detect the spoken
# language per utterance (the bilingual default); a concrete code forces it.
# This had NO UI/REST control before — the recognition language was stranded in
# jarvis.toml, so a user whose voice was mis-recognized had no way to fix it
# (forensic 2026-06-28: German spoken, English-only model, "Can't you me" garbage).
# Applies on the next voice bootstrap (a restart); the STT provider is built once.
# ----------------------------------------------------------------------

_STT_LANGUAGES: tuple[str, ...] = ("auto", "de", "en", "es")


class SttLanguageBody(BaseModel):
    language: str = Field(..., min_length=1)
    persist: bool = Field(default=True, description="Persist as boot default in jarvis.toml")


def _current_stt_language(request: Request) -> str:
    # Read fresh from disk so a value just written is reflected; fall back to the
    # boot config, then the "auto" bilingual default.
    try:
        from jarvis.core.config import load_config

        return str(getattr(load_config().stt, "language", "auto"))
    except Exception as exc:  # noqa: BLE001 — never 500 a settings read
        log.debug("stt-language fresh read failed, using boot config: %s", exc)
    cfg = getattr(request.app.state, "config", None)
    return str(getattr(getattr(cfg, "stt", None), "language", "auto"))


@router.get("/stt-language")
async def get_stt_language(request: Request) -> dict[str, object]:
    return {"language": _current_stt_language(request), "options": list(_STT_LANGUAGES)}


@router.put("/stt-language")
async def put_stt_language(body: SttLanguageBody, request: Request) -> dict[str, object]:
    lang = (body.language or "").strip().lower()
    if lang not in _STT_LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown STT language {body.language!r} (allowed: {list(_STT_LANGUAGES)})",
        )

    persisted = False
    if body.persist:
        try:
            from jarvis.core import config_writer
            from jarvis.core.config import resolve_config_path

            config_writer.set_stt_language(lang, path=resolve_config_path())
            persisted = True
        except Exception as exc:  # noqa: BLE001 — persist is best-effort
            log.warning("stt-language persist failed: %s", exc)

    cfg = getattr(request.app.state, "config", None)
    if cfg is not None and getattr(cfg, "stt", None) is not None:
        try:
            cfg.stt.language = lang  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — frozen model is not an error
            log.debug("in-memory cfg.stt.language update skipped: %s", exc)

    # The STT provider is built once at voice bootstrap, so a live turn keeps the
    # old language until the next voice restart. ``restart_required`` tells the UI
    # to surface that hint.
    return {
        "ok": True,
        "language": lang,
        "persisted": persisted,
        "restart_required": True,
    }


# ---------------------------------------------------------------------------
# Wake word (custom-wake-word feature). GET current + options; PUT to switch.
# Persisted to jarvis.toml [trigger.wake_word]; applies on the next voice
# bootstrap (a Jarvis restart). See docs/local-wakeword/CUSTOM-WAKE-WORD-DESIGN.md.
# ---------------------------------------------------------------------------


class WakeWordBody(BaseModel):
    # Optional tuning fields default to None (NOT a concrete value): the UI does
    # not always send them, and a concrete default here would make set_wake_word
    # write that value on every save, silently clobbering a hand-edited
    # jarvis.toml (e.g. resetting fuzzy_match_ratio 0.8 -> 0.5). With None, the
    # PUT handler omits the field and set_wake_word's None-guard preserves the
    # existing toml value (idempotent round-trip).
    phrase: str = Field(..., min_length=1, max_length=64)
    engine: str = Field(default="auto")
    custom_model_path: str | None = Field(default=None)
    sensitivity: float | None = Field(default=None, ge=0.0, le=1.0)
    fuzzy_match_ratio: float | None = Field(default=None, ge=0.5, le=1.0)
    persist: bool = Field(default=True, description="Persist to jarvis.toml")


def _config(request: Request):
    return getattr(request.app.state, "config", None) or getattr(
        request.app.state, "cfg", None
    )


def _local_whisper_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("faster_whisper") is not None


@router.get("/wake-word")
async def get_wake_word(request: Request) -> dict[str, object]:
    from jarvis.core.config import WakeWordConfig
    from jarvis.speech.wake_constants import INSTANT_WAKE_PHRASES, WAKE_ENGINES

    cfg = _config(request)
    ww = None
    if cfg is not None and getattr(cfg, "trigger", None) is not None:
        ww = getattr(cfg.trigger, "wake_word", None)
    if ww is None:
        ww = WakeWordConfig()
    return {
        "phrase": ww.phrase,
        "engine": ww.engine,
        "custom_model_path": ww.custom_model_path,
        "sensitivity": ww.sensitivity,
        "fuzzy_match_ratio": ww.fuzzy_match_ratio,
        "engines": list(WAKE_ENGINES),
        "instant_phrases": list(INSTANT_WAKE_PHRASES),
        "local_whisper_available": _local_whisper_available(),
    }


@router.put("/wake-word")
async def put_wake_word(body: WakeWordBody, request: Request) -> dict[str, object]:
    from types import SimpleNamespace

    from jarvis.speech.wake_constants import WAKE_ENGINES
    from jarvis.speech.wake_phrase import resolve_wake_plan

    engine = body.engine.strip().lower()
    if engine not in WAKE_ENGINES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown wake engine '{body.engine}'. Allowed: {', '.join(WAKE_ENGINES)}.",
        )

    # Preview the resolved plan so the UI can tell the user immediately whether
    # the chosen phrase will work as-is or degrade (e.g. no local Whisper).
    plan = resolve_wake_plan(
        SimpleNamespace(
            phrase=body.phrase,
            engine=engine,
            custom_model_path=body.custom_model_path,
            sensitivity=body.sensitivity,
            fuzzy_match_ratio=body.fuzzy_match_ratio,
        ),
        local_whisper_available=_local_whisper_available(),
    )

    # Best-effort in-memory cfg update so a later cfg read agrees pre-restart.
    # Only the fields the client actually sent (non-None) are applied, mirroring
    # the persistence path — an omitted optional field keeps its existing value.
    cfg = _config(request)
    if cfg is not None and getattr(cfg, "trigger", None) is not None:
        ww = getattr(cfg.trigger, "wake_word", None)
        updates: dict[str, object] = {"phrase": body.phrase, "engine": engine}
        if body.custom_model_path is not None:
            updates["custom_model_path"] = body.custom_model_path
        if body.sensitivity is not None:
            updates["sensitivity"] = body.sensitivity
        if body.fuzzy_match_ratio is not None:
            updates["fuzzy_match_ratio"] = body.fuzzy_match_ratio
        for key, value in updates.items():
            try:
                setattr(ww, key, value)
            except Exception as exc:  # noqa: BLE001 — frozen model is not an error
                log.debug("in-memory wake_word.%s update skipped: %s", key, exc)

    persisted = False
    if body.persist:
        try:
            from jarvis.core import config_writer

            config_writer.set_wake_word(
                body.phrase,
                engine=engine,
                custom_model_path=body.custom_model_path,
                sensitivity=body.sensitivity,
                fuzzy_match_ratio=body.fuzzy_match_ratio,
            )
            persisted = True
        except Exception as exc:  # noqa: BLE001
            log.warning("wake-word persist failed: %s", exc)

    # Live-apply to the running voice pipeline so the new wake word works
    # immediately — no app restart. This is the fix for "only Hey Jarvis works":
    # the wake model/matcher were previously wired once at startup, so a UI save
    # only took effect on the next boot. Best-effort: a headless/down pipeline
    # just means it applies on next start.
    applied_live = False
    pipeline = getattr(request.app.state, "speech_pipeline", None)
    if pipeline is not None and hasattr(pipeline, "set_wake_plan"):
        try:
            pipeline.set_wake_plan(plan)
            applied_live = True
        except Exception as exc:  # noqa: BLE001 — never fail the save on a live-apply hiccup
            log.warning("wake-word live-apply failed (persisted; applies on restart): %s", exc)

    return {
        "ok": True,
        "phrase": body.phrase,
        "engine": engine,
        "resolved_engine": plan.engine,
        "degraded": plan.degraded,
        "message": plan.message,
        "persisted": persisted,
        # When live-applied, the running pipeline already swapped the detector;
        # no restart needed. Otherwise it takes effect on the next voice start.
        "applied_live": applied_live,
        "restart_required": not applied_live,
    }


# ---------------------------------------------------------------------------
# Push-to-talk hotkey (editable). GET current + safe suggestions; PUT to change.
# Persisted to jarvis.toml [trigger].hotkey; applies on the next voice bootstrap
# (a Jarvis restart) — the bindings are armed once at SpeechPipeline start.
# ---------------------------------------------------------------------------

# Curated safe combos for the UI quick-picks. All hold-able (for PTT) and clear
# of OS-critical shortcuts. The CLAUDE.md guidance is "ctrl+right_alt+<letter>".
_HOTKEY_SUGGESTIONS = [
    "ctrl+right_alt+j",
    "ctrl+right_alt+k",
    "ctrl+right_alt+space",
    "ctrl+shift+space",
    "f3+f4",
]


class PttHotkeyBody(BaseModel):
    hotkey: str = Field(..., min_length=1, max_length=64)
    persist: bool = Field(default=True, description="Persist to jarvis.toml")


@router.get("/ptt-hotkey")
async def get_ptt_hotkey(request: Request) -> dict[str, object]:
    from jarvis.core.config import TriggerConfig

    cfg = _config(request)
    trig = getattr(cfg, "trigger", None) if cfg is not None else None
    default_hotkey = TriggerConfig().hotkey
    return {
        "hotkey": getattr(trig, "hotkey", default_hotkey) if trig else default_hotkey,
        "push_to_talk": bool(getattr(trig, "push_to_talk", True)) if trig else True,
        "default": default_hotkey,
        "suggestions": list(_HOTKEY_SUGGESTIONS),
    }


@router.put("/ptt-hotkey")
async def put_ptt_hotkey(body: PttHotkeyBody, request: Request) -> dict[str, object]:
    from jarvis.trigger.hotkey import validate_hotkey

    hotkey = body.hotkey.strip().lower()
    # Backend is the authority — a browser key-capture cannot be trusted to
    # filter OS-critical / unusable combos (AltGr detection is unreliable there).
    ok, reason = validate_hotkey(hotkey)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)

    # Best-effort in-memory cfg update so a later cfg read agrees pre-restart.
    cfg = _config(request)
    if cfg is not None and getattr(cfg, "trigger", None) is not None:
        try:
            cfg.trigger.hotkey = hotkey  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — frozen model is not an error
            log.debug("in-memory trigger.hotkey update skipped: %s", exc)

    persisted = False
    if body.persist:
        try:
            from jarvis.core import config_writer

            config_writer.set_ptt_hotkey(hotkey)
            persisted = True
        except Exception as exc:  # noqa: BLE001
            log.warning("ptt-hotkey persist failed: %s", exc)

    return {
        "ok": True,
        "hotkey": hotkey,
        "persisted": persisted,
        # Bindings are armed once at SpeechPipeline construction (resolve_hotkeys),
        # so a hotkey change needs a voice restart to take effect.
        "restart_required": True,
    }


# ---------------------------------------------------------------------------
# Voice keybinds (editable): Call / Hangup / Talk-PTT. GET all three + defaults;
# PUT one action at a time. Persisted to jarvis.toml [trigger] AND live-applied
# to the running voice pipeline (set_keybinds → HotkeyTrigger.rearm), so a
# change takes effect immediately without a restart; a headless/down pipeline
# falls back to "applies on next start". The legacy /ptt-hotkey route above
# stays for backward compatibility.
# ---------------------------------------------------------------------------


def _keybind_values(trig: object) -> dict[str, str]:
    """Current combo per action, falling back to TriggerConfig defaults."""
    from jarvis.core.config import TriggerConfig
    from jarvis.core.config_writer import KEYBIND_TOML_KEY

    d = TriggerConfig()
    out: dict[str, str] = {}
    for action, field in KEYBIND_TOML_KEY.items():
        default = getattr(d, field)
        out[action] = str(getattr(trig, field, default)) if trig is not None else default
    return out


class KeybindBody(BaseModel):
    action: str = Field(..., description="call | hangup | ptt")
    hotkey: str = Field(..., max_length=64)
    persist: bool = Field(default=True, description="Persist to jarvis.toml")


@router.get("/keybinds")
async def get_keybinds(request: Request) -> dict[str, object]:
    from jarvis.core.config import TriggerConfig

    cfg = _config(request)
    trig = getattr(cfg, "trigger", None) if cfg is not None else None
    d = TriggerConfig()
    # A change only needs a restart when there is no live pipeline to re-arm
    # (headless / not yet started). With a running pipeline, saves apply live.
    pipeline = getattr(request.app.state, "speech_pipeline", None)
    restart_required = pipeline is None or not hasattr(pipeline, "set_keybinds")
    return {
        "keybinds": _keybind_values(trig),
        "defaults": {"call": d.hotkey_call, "hangup": d.hotkey_hangup, "ptt": d.hotkey},
        "push_to_talk": bool(getattr(trig, "push_to_talk", True)) if trig else True,
        "suggestions": list(_HOTKEY_SUGGESTIONS),
        "restart_required": restart_required,
    }


@router.put("/keybinds")
async def put_keybind(body: KeybindBody, request: Request) -> dict[str, object]:
    from jarvis.core.config_writer import KEYBIND_ACTIONS, KEYBIND_TOML_KEY
    from jarvis.trigger.hotkey import validate_hotkey

    action = body.action.strip().lower()
    if action not in KEYBIND_ACTIONS:
        raise HTTPException(status_code=400, detail=f"unknown action: {action}")
    hotkey = body.hotkey.strip().lower()

    cfg = _config(request)
    trig = getattr(cfg, "trigger", None) if cfg is not None else None

    if hotkey:
        # The backend is the authority — a browser key-capture cannot be
        # trusted to filter OS-critical / unusable combos (AltGr detection is
        # unreliable there).
        ok, reason = validate_hotkey(hotkey)
        if not ok:
            raise HTTPException(status_code=400, detail=reason)

        # Collision check: one chord can't both answer and hang up. Exact
        # equality is not enough — the polling hotkey backend matches a combo
        # as soon as its keys are down, so a key-set SUBSET of another
        # action's combo fires both (call=f1 + hangup=f1+f2 → F1+F2 triggers
        # call AND hangup). Reject any subset/superset relation between the
        # key sets, in both directions.
        new_keys = {p.strip() for p in hotkey.split("+") if p.strip()}
        for other_action, other_combo in _keybind_values(trig).items():
            if other_action == action:
                continue
            other_keys = {
                p.strip() for p in other_combo.strip().lower().split("+") if p.strip()
            }
            if not other_keys:
                # The other action is itself unbound (Clear button) — an
                # empty key-set is a subset of every combo, so without this
                # guard EVERY save would be rejected as "overlapping" the
                # moment any one action is cleared.
                continue
            if new_keys <= other_keys or other_keys <= new_keys:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"'{hotkey}' overlaps with '{other_action}' "
                        f"('{other_combo.strip().lower()}') — pressing one would "
                        "trigger both. Pick keys that don't contain each other."
                    ),
                )
    # else: hotkey == "" is an explicit "unbind this action" request (Settings
    # Clear button) — skip validate_hotkey (that rule exists for "still
    # recording", not "cleared on purpose") and skip the collision check
    # (an unbound action cannot collide with anything).

    field = KEYBIND_TOML_KEY[action]
    if trig is not None:
        try:
            setattr(trig, field, hotkey)
        except Exception as exc:  # noqa: BLE001 — frozen model is not an error
            log.debug("in-memory trigger.%s update skipped: %s", field, exc)

    persisted = False
    if body.persist:
        try:
            from jarvis.core import config_writer

            config_writer.set_keybind(action, hotkey)
            persisted = True
        except Exception as exc:  # noqa: BLE001
            log.warning("keybind persist failed: %s", exc)

    # Live-apply to the running voice pipeline so the new combo (or the
    # cleared state) takes effect immediately — no app restart. Best-effort —
    # a headless/down pipeline just means it applies on next start.
    applied_live = False
    pipeline = getattr(request.app.state, "speech_pipeline", None)
    if pipeline is not None and hasattr(pipeline, "set_keybinds"):
        try:
            # An empty hotkey re-arms with an EMPTY list, not a list
            # containing "" — mirrors how the PTT action already represents
            # "off" internally.
            pipeline.set_keybinds(**{action: [hotkey] if hotkey else []})
            applied_live = True
        except Exception as exc:  # noqa: BLE001 — never fail the save on a live-apply hiccup
            log.warning("keybind live-apply failed (persisted; applies on restart): %s", exc)

    return {
        "ok": True,
        "action": action,
        "hotkey": hotkey,
        "persisted": persisted,
        # When live-applied the running trigger already re-armed; no restart
        # needed. Otherwise it takes effect on the next voice start.
        "applied_live": applied_live,
        "restart_required": not applied_live,
    }


# ---------------------------------------------------------------------------
# Assistant name (read-only). The name derives from the wake phrase; GET
# exposes the resolved name for the frontend bylines. There is no write endpoint.
# ---------------------------------------------------------------------------


@router.get("/assistant-name")
async def get_assistant_name(request: Request) -> dict[str, object]:
    """The assistant's resolved name. Read-only: the name derives from the wake
    phrase (set via PUT /api/settings/wake-word), there is no separate control."""
    from jarvis.brain.assistant_name import DEFAULT_ASSISTANT_NAME, resolve_assistant_name

    cfg = _config(request)
    return {
        "resolved": resolve_assistant_name(cfg),
        "default": DEFAULT_ASSISTANT_NAME,
    }


# ---------------------------------------------------------------------------
# Login autostart (the 7th cross-platform port). GET current state + support;
# PUT to toggle. Persisted to jarvis.toml [autostart].enabled AND applied live
# (install/remove the OS entry immediately — no restart). On a headless host the
# toggle persists honestly with supported=false. See
# docs/superpowers/specs/2026-05-30-cross-platform-autostart-design.md.
# ---------------------------------------------------------------------------


class AutostartBody(BaseModel):
    enabled: bool = Field(...)
    persist: bool = Field(default=True, description="Persist to jarvis.toml")


def _autostart_components(request: Request):
    """Resolve (enabled, caps, manager, spec) — cheap, non-blocking.

    The blocking part (reading/writing the OS entry — a PowerShell call on
    Windows) lives in ``manager.status/install/uninstall`` and MUST be run off
    the event loop via ``asyncio.to_thread`` (AP-18: never block the loop on a
    subprocess — it freezes the WS fan-out + voice bus).
    """
    from jarvis.autostart import make_autostart_manager, resolve_launch_spec
    from jarvis.platform.capabilities import detect_capabilities

    cfg = _config(request)
    autostart_cfg = getattr(cfg, "autostart", None) if cfg is not None else None
    enabled = bool(getattr(autostart_cfg, "enabled", True))
    caps = detect_capabilities()
    manager = make_autostart_manager(caps)
    spec = resolve_launch_spec(cfg)
    return enabled, caps, manager, spec


def _autostart_payload(enabled, caps, spec, status) -> dict[str, object]:
    # Which OS mechanism is currently active — lets the Windows UI offer the
    # "enable instant start" upgrade when only the throttled .lnk fallback is in
    # place (scheduled-task registration needs a one-time UAC prompt).
    mechanism = "none"
    if status.installed:
        if str(status.entry_path or "").startswith("Task Scheduler"):
            mechanism = "scheduled_task"
        elif caps.platform == "win32":
            mechanism = "shortcut"
        else:
            mechanism = "native"
    return {
        "enabled": enabled,
        "supported": status.supported,
        "installed": status.installed,
        "matches_spec": status.matches_spec,
        "platform": caps.platform,
        "mechanism": mechanism,
        "resolved_command": spec.command_line(),
        "entry_path": status.entry_path,
        "detail": status.detail,
    }


@router.get("/autostart")
async def get_autostart(request: Request) -> dict[str, object]:
    enabled, caps, manager, spec = _autostart_components(request)
    # status() shells out to PowerShell on Windows — keep it off the event loop.
    status = await asyncio.to_thread(manager.status, spec)
    return _autostart_payload(enabled, caps, spec, status)


@router.put("/autostart")
async def put_autostart(body: AutostartBody, request: Request) -> dict[str, object]:
    enabled = bool(body.enabled)

    # Best-effort in-memory cfg update so a later cfg read agrees pre-restart.
    cfg = _config(request)
    if cfg is not None and getattr(cfg, "autostart", None) is not None:
        try:
            cfg.autostart.enabled = enabled  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — frozen model is not an error
            log.debug("in-memory autostart.enabled update skipped: %s", exc)

    # Persist the intent. Best-effort: a read-only / locked jarvis.toml must not
    # break the live apply below.
    persisted = False
    if body.persist:
        try:
            from jarvis.core import config_writer

            config_writer.set_autostart(enabled)
            persisted = True
        except Exception as exc:  # noqa: BLE001
            log.warning("autostart persist failed (live apply still attempted): %s", exc)

    # Live-apply off the event loop: install/remove the OS entry now (PowerShell
    # on Windows is blocking). A failure is reported honestly, never raised.
    _, caps, manager, spec = _autostart_components(request)
    applied_live = False
    try:
        if enabled:
            # User-initiated → interactive: Windows may show a one-time UAC prompt
            # to register the instant-start logon task (declined → .lnk fallback).
            status = await asyncio.to_thread(manager.install, spec, interactive=True)
        else:
            status = await asyncio.to_thread(manager.uninstall, interactive=True)
        applied_live = status.supported
    except Exception as exc:  # noqa: BLE001 — never fail the toggle on an apply hiccup
        log.warning("autostart live-apply failed (persisted; applies on restart): %s", exc)
        status = await asyncio.to_thread(manager.status, spec)

    # Reuse the GET payload shape (so the response carries `mechanism` — the
    # frontend reads it to pick the right toast after "enable instant start" and
    # to decide whether to keep showing the upgrade affordance).
    return {
        **_autostart_payload(enabled, caps, spec, status),
        "ok": True,
        "applied_live": applied_live,
        "persisted": persisted,
        "restart_required": False,
    }


_OVERLAY_STYLES = ("jarvis_bar", "mascot", "none")


class OverlayStyleBody(BaseModel):
    style: str = Field(..., min_length=1)
    persist: bool = Field(default=True, description="Persist as boot default in jarvis.toml")


@router.get("/overlay-style")
async def get_overlay_style(request: Request) -> dict[str, object]:
    """Current on-screen overlay style + the selectable options."""
    cfg = _config(request)
    ui = getattr(cfg, "ui", None)
    current = getattr(ui, "orb_style", None) or "jarvis_bar"
    return {"style": current, "options": list(_OVERLAY_STYLES)}


@router.put("/overlay-style")
async def put_overlay_style(body: OverlayStyleBody, request: Request) -> dict[str, object]:
    """Switch the on-screen overlay (jarvis_bar / mascot / none).

    Persists [ui].orb_style and live-swaps the running surface via the
    DesktopApp (app.state.desktop_app.swap_overlay). When no live app is
    reachable (headless), the choice is persisted and applies on restart.
    """
    style = body.style.strip()
    # Backwards-compat: accept the legacy "whisper_bar" value (renamed to
    # "jarvis_bar" to drop a trademarked name) from any not-yet-rebuilt client.
    if style == "whisper_bar":
        style = "jarvis_bar"
    if style not in _OVERLAY_STYLES:
        raise HTTPException(status_code=400, detail=f"Unknown overlay style '{style}'")

    # Best-effort in-memory cfg update so a later read agrees pre-restart.
    cfg = _config(request)
    ui = getattr(cfg, "ui", None)
    if ui is not None:
        try:
            ui.orb_style = style  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — frozen model is not an error
            log.debug("in-memory orb_style update skipped: %s", exc)

    persisted = False
    if body.persist:
        try:
            from jarvis.core import config_writer

            config_writer.set_overlay_style(style)
            persisted = True
        except Exception as exc:  # noqa: BLE001
            log.warning("overlay-style persist failed (live apply still attempted): %s", exc)

    applied_live = False
    detail = ""
    desktop = getattr(request.app.state, "desktop_app", None)
    swap = getattr(desktop, "swap_overlay", None)
    if callable(swap):
        try:
            # swap_overlay touches Tk (spawns a daemon thread) — keep it off the loop.
            result = await asyncio.to_thread(swap, style)
            applied_live = (
                bool(result.get("applied_live")) if isinstance(result, dict) else bool(result)
            )
        except Exception as exc:  # noqa: BLE001 — never fail the toggle on an apply hiccup
            log.warning("overlay-style live-apply failed (persisted; applies on restart): %s", exc)
            detail = str(exc)

    return {
        "ok": True,
        "style": style,
        "persisted": persisted,
        "applied_live": applied_live,
        "restart_required": not applied_live,
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# Custom system prompt (personalize-your-assistant feature). The user can
# replace the packaged JARVIS persona with their own Markdown and reset back to
# the default with one click. Stored as a sidecar file (data/custom_system_prompt.md);
# reset is a delete. No restart needed: _build_system_prompt reads the override
# fresh each turn, so a save/reset applies on the next message.
# ---------------------------------------------------------------------------


class SystemPromptBody(BaseModel):
    # The full Markdown system prompt. Whitespace-only is rejected (a blank
    # persona would strip Jarvis of its instructions) — to clear, DELETE instead.
    content: str = Field(..., min_length=1)


def _system_prompt_payload() -> dict[str, object]:
    from jarvis.brain import persona_loader

    content = persona_loader.load_effective_persona_prompt()
    return {
        "content": content,
        "is_custom": persona_loader.has_custom_prompt(),
        "default": persona_loader.default_persona_prompt(),
        "char_count": len(content),
    }


@router.get("/system-prompt")
async def get_system_prompt() -> dict[str, object]:
    """Current effective system prompt + the packaged default (for reset)."""
    return _system_prompt_payload()


@router.put("/system-prompt")
async def put_system_prompt(body: SystemPromptBody) -> dict[str, object]:
    """Save a custom system prompt. Applies on the next turn (no restart)."""
    from jarvis.brain import persona_loader

    if not body.content.strip():
        raise HTTPException(
            status_code=400,
            detail="System prompt must not be empty. Use reset to restore the default.",
        )
    try:
        persona_loader.save_custom_prompt(body.content)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save: {exc}") from exc

    return {"ok": True, "restart_required": False, **_system_prompt_payload()}


@router.delete("/system-prompt")
async def delete_system_prompt() -> dict[str, object]:
    """Reset to the packaged default by removing the custom override."""
    from jarvis.brain import persona_loader

    removed = persona_loader.reset_custom_prompt()
    return {"ok": True, "removed": removed, "restart_required": False, **_system_prompt_payload()}


# ---------------------------------------------------------------------------
# Agent instructions (personal standing-instructions file — an AGENTS.md /
# CLAUDE.md equivalent). The user writes personal preferences here; the file is
# named after the assistant (e.g. Ruben.md) and injected into the brain system
# prompt as a block distinct from the persona. No restart needed: the brain reads
# it fresh each turn, so a save/reset applies on the next message.
# ---------------------------------------------------------------------------


class AgentInstructionsBody(BaseModel):
    # The full Markdown. Whitespace-only is rejected (to clear, DELETE instead).
    content: str = Field(..., min_length=1)


def _agent_instructions_payload(request: Request) -> dict[str, object]:
    from jarvis.brain import agent_instructions

    cfg = _config(request)
    content = agent_instructions.read_agent_instructions(cfg)
    exists = content is not None
    content = content or ""
    return {
        "content": content,
        "exists": exists,
        "filename": agent_instructions.instructions_filename(cfg),
        "template": agent_instructions.seed_template(cfg),
        "char_count": len(content),
    }


@router.get("/agent-instructions")
async def get_agent_instructions(request: Request) -> dict[str, object]:
    """Current agent instructions + the dynamic filename + a starter template."""
    return _agent_instructions_payload(request)


@router.put("/agent-instructions")
async def put_agent_instructions(
    body: AgentInstructionsBody, request: Request
) -> dict[str, object]:
    """Save the user's standing instructions. Applies on the next turn (no restart)."""
    from jarvis.brain import agent_instructions

    if not body.content.strip():
        raise HTTPException(
            status_code=400,
            detail="Instructions must not be empty. Use reset to clear them.",
        )
    try:
        agent_instructions.save_agent_instructions(_config(request), body.content)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save: {exc}") from exc

    return {"ok": True, "restart_required": False, **_agent_instructions_payload(request)}


@router.delete("/agent-instructions")
async def delete_agent_instructions(request: Request) -> dict[str, object]:
    """Clear the user's standing instructions by deleting the file."""
    from jarvis.brain import agent_instructions

    removed = agent_instructions.reset_agent_instructions(_config(request))
    return {
        "ok": True,
        "removed": removed,
        "restart_required": False,
        **_agent_instructions_payload(request),
    }


async def _running_mission_summaries(
    manager: object, ids: list[str]
) -> list[dict[str, str]]:
    """``[{id, title}]`` for the given in-flight mission ids (title = prompt[:80]).

    Best-effort: a missing manager or a per-mission lookup failure degrades to an
    empty title rather than blocking the guard from reporting the id at all.
    """
    summaries: list[dict[str, str]] = []
    get = getattr(manager, "mission", None)
    for mid in ids:
        title = ""
        if callable(get):
            try:
                view = await get(mid)
                title = (getattr(view, "prompt", "") or "").strip()[:80]
            except Exception as exc:  # noqa: BLE001 — never block a restart on this
                log.debug("restart guard: prompt lookup failed for %s: %s", mid, exc)
        summaries.append({"id": mid, "title": title})
    return summaries


async def _run_off_pool(fn: Callable[[], object]) -> object:
    """Run a quick blocking callable on a fresh, dedicated thread.

    Deliberately does NOT use ``asyncio.to_thread`` / the shared default
    ``ThreadPoolExecutor``. A restart is the app's recovery path and must keep
    working precisely *when the app is unhealthy* — including when that shared
    pool is exhausted by un-cancellable hung threads.

    Forensic 2026-06-29: the custom-wake ctranslate2 transcription hung inside
    C code; its 8 s ``asyncio.timeout`` cancelled only the *await*, abandoning
    the pool thread mid-call (a running thread can't be killed in Python). The
    8 s re-poll storm leaked one default-pool worker every cycle until every
    worker was wedged, so ``await asyncio.to_thread(request_restart)`` queued
    behind the dead pool forever — the restart POST never returned and the
    button spun "Restarting…" with the window still up. A dedicated thread is
    immune to that starvation. Cross-platform: pool exhaustion hits any host
    (a slow CPU-only Whisper on a VPS reaches the same wall as a stuck GPU one).
    """
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[object] = loop.create_future()

    def _runner() -> None:
        try:
            result = fn()
        except BaseException as exc:  # noqa: BLE001 — relay verbatim to the awaiter
            loop.call_soon_threadsafe(_resolve, fut.set_exception, exc)
        else:
            loop.call_soon_threadsafe(_resolve, fut.set_result, result)

    def _resolve(setter: Callable[[object], None], value: object) -> None:
        if not fut.done():  # awaiter may have been cancelled meanwhile
            setter(value)

    threading.Thread(
        target=_runner, name="jarvis-restart-trigger", daemon=True
    ).start()
    return await fut


@router.post("/restart-app")
async def restart_app(request: Request, force: bool = False) -> dict[str, object]:
    """Cleanly self-restart the desktop app.

    Delivers a pending overlay-style change (bar <-> mascot) that cannot be
    applied live (BUG-031) without the user closing + reopening by hand. The
    DesktopApp spawns a detached relauncher and quits ~0.8 s later, so this
    request returns 200 first. Returns 503 on a headless host (no window).

    Mission guard: an app restart kills every in-flight mission (the process and
    its worker Job-Objects die). That is the dominant cause of "aborted" missions
    — a healthy-but-quiet worker looks like a hang, the app gets restarted, and
    the run is lost (forensic: 102 crash_recovery / app_shutdown deaths, all
    during active use, none correlated with system Standby). So unless the caller
    passes ``force=true``, a restart while missions run is refused with HTTP 409
    and the live mission list, letting the UI/CLI confirm before the kill. This
    protects every restart source at once: TopBar, taskbar settings, the
    ``jarvis-ctl restart`` CLI, and any parallel dev session hitting the endpoint.
    """
    if not force:
        kontrollierer = getattr(request.app.state, "kontrollierer", None)
        list_running = getattr(kontrollierer, "running_mission_ids", None)
        running = list(list_running()) if callable(list_running) else []
        if running:
            manager = getattr(request.app.state, "mission_manager", None)
            try:
                # A wedged mission manager (the very state a user restarts to
                # escape) must not hang the guard — bound the title lookup and
                # fall back to id-only summaries so the 409 still reaches the UI.
                missions = await asyncio.wait_for(
                    _running_mission_summaries(manager, running), timeout=2.0
                )
            except TimeoutError:
                missions = [{"id": mid, "title": ""} for mid in running]
            raise HTTPException(
                status_code=409,
                detail={"error": "missions_running", "missions": missions},
            )

    desktop = getattr(request.app.state, "desktop_app", None)
    fn = getattr(desktop, "request_restart", None)
    if not callable(fn):
        raise HTTPException(
            status_code=503, detail="self-restart unavailable on this host"
        )
    # Off the shared default pool — a restart must survive a pool exhausted by
    # hung threads (see ``_run_off_pool``). ``asyncio.to_thread`` would queue
    # behind the dead pool and hang the POST forever.
    scheduled = await _run_off_pool(fn)
    if not scheduled:
        raise HTTPException(
            status_code=503, detail="no desktop window to restart"
        )
    return {"ok": True, "restarting": True}


class OpenExternalBody(BaseModel):
    url: str = Field(min_length=1, max_length=4096)


@router.post("/open-external")
async def open_external(body: OpenExternalBody) -> dict[str, object]:
    """Open an ``http(s)`` URL in the user's real default browser.

    The desktop shell embeds WebView2, which silently drops ``window.open`` /
    ``target="_blank"`` — so OAuth-authorize and token-creation pages never
    reached the browser and plugin connect appeared to "do nothing". The
    frontend calls this only when it detects the embedded shell (``window
    .__JARVIS_TOKEN``); a real browser tab opens the URL itself. Returns
    ``{"opened": false}`` on a headless host (no display) so the caller can
    fall back to ``window.open``.

    Scheme is validated to ``http``/``https`` here AND in ``open_url`` so no
    ``file:``/``javascript:``/app-protocol URL can ever be launched.
    """
    from urllib.parse import urlparse

    parsed = urlparse(body.url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise HTTPException(
            status_code=400, detail="only http/https URLs may be opened"
        )
    from jarvis.platform.open_path import open_url

    opened = await asyncio.to_thread(open_url, body.url)
    log.info("open-external: opened=%s url=%s", opened, body.url)
    return {"opened": bool(opened)}


# ---------------------------------------------------------------------------
# Taskbar section toggles: "Show bar at all times" (bar_persistent, live) and
# "Mute music while dictating" (ducking.enabled, live). Both persist to
# jarvis.toml and live-apply via app.state.desktop_app.
# ---------------------------------------------------------------------------


class BoolToggleBody(BaseModel):
    enabled: bool = Field(...)


@router.get("/bar-persistent")
async def get_bar_persistent(request: Request) -> dict[str, object]:
    cfg = _config(request)
    ui = getattr(cfg, "ui", None)
    return {"enabled": bool(getattr(ui, "bar_persistent", True))}


@router.put("/bar-persistent")
async def put_bar_persistent(body: BoolToggleBody, request: Request) -> dict[str, object]:
    enabled = bool(body.enabled)
    cfg = _config(request)
    ui = getattr(cfg, "ui", None)
    if ui is not None:
        try:
            ui.bar_persistent = enabled  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            log.debug("in-memory bar_persistent update skipped: %s", exc)
    persisted = False
    try:
        from jarvis.core import config_writer

        config_writer.set_bar_persistent(enabled)
        persisted = True
    except Exception as exc:  # noqa: BLE001
        log.warning("bar_persistent persist failed (live apply still attempted): %s", exc)
    applied_live = False
    desktop = getattr(request.app.state, "desktop_app", None)
    fn = getattr(desktop, "set_bar_persistent", None)
    if callable(fn):
        try:
            res = await asyncio.to_thread(fn, enabled)
            applied_live = (
                bool(res.get("applied_live")) if isinstance(res, dict) else bool(res)
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("bar_persistent live-apply failed: %s", exc)
    return {
        "ok": True,
        "enabled": enabled,
        "persisted": persisted,
        "applied_live": applied_live,
        "restart_required": not applied_live,
    }


@router.get("/mute-music")
async def get_mute_music(request: Request) -> dict[str, object]:
    cfg = _config(request)
    duck = getattr(cfg, "ducking", None)
    return {"enabled": bool(getattr(duck, "enabled", False))}


@router.put("/mute-music")
async def put_mute_music(body: BoolToggleBody, request: Request) -> dict[str, object]:
    enabled = bool(body.enabled)
    cfg = _config(request)
    duck = getattr(cfg, "ducking", None)
    if duck is not None:
        try:
            duck.enabled = enabled  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            log.debug("in-memory ducking.enabled update skipped: %s", exc)
    persisted = False
    try:
        from jarvis.core import config_writer

        config_writer.set_mute_music(enabled)
        persisted = True
    except Exception as exc:  # noqa: BLE001
        log.warning("mute_music persist failed (live apply still attempted): %s", exc)
    applied_live = False
    desktop = getattr(request.app.state, "desktop_app", None)
    ducker = getattr(desktop, "_ducker", None)
    setter = getattr(ducker, "set_enabled", None)
    if callable(setter):
        try:
            await setter(enabled)
            applied_live = True
        except Exception as exc:  # noqa: BLE001
            log.warning("mute_music live-apply failed: %s", exc)
    return {"ok": True, "enabled": enabled, "persisted": persisted, "applied_live": applied_live}


@router.get("/sound-effects")
async def get_sound_effects(request: Request) -> dict[str, object]:
    cfg = _config(request)
    ui = getattr(cfg, "ui", None)
    return {"enabled": bool(getattr(ui, "sound_effects", True))}


@router.put("/sound-effects")
async def put_sound_effects(body: BoolToggleBody, request: Request) -> dict[str, object]:
    """Global earcon master switch. Persists to [ui] sound_effects and applies
    live: the in-memory UI config is the same object the speech pipeline reads
    before every earcon, so the next tone honors the new value with no restart.
    """
    enabled = bool(body.enabled)
    cfg = _config(request)
    ui = getattr(cfg, "ui", None)
    applied_live = False
    if ui is not None:
        try:
            ui.sound_effects = enabled  # type: ignore[attr-defined]
            applied_live = True
        except Exception as exc:  # noqa: BLE001
            log.debug("in-memory sound_effects update skipped: %s", exc)
    persisted = False
    try:
        from jarvis.core import config_writer

        config_writer.set_sound_effects(enabled)
        persisted = True
    except Exception as exc:  # noqa: BLE001
        log.warning("sound_effects persist failed (live apply still attempted): %s", exc)
    return {
        "ok": True,
        "enabled": enabled,
        "persisted": persisted,
        "applied_live": applied_live,
    }


# ---------------------------------------------------------------------------
# Wiki curator model picker. GET current + selectable providers/models; PUT to
# change. The dedicated long-term-memory LLM is provider-agnostic: an empty
# provider falls back to brain.primary and an empty model falls back to that
# provider's CHEAP/FAST router model (mirrors the ack-brain follow_brain
# pattern). Persisted to jarvis.toml [memory.wiki.curator]; applied live to a
# running WikiCurator when one exists, else takes effect on the next ingest /
# restart. Reads/writes the EXISTING WikiCuratorConfig fields resolved through
# jarvis.memory.wiki.curator_llm._resolve_provider_and_model.
# ---------------------------------------------------------------------------


class WikiProviderBody(BaseModel):
    # Empty strings are meaningful: provider="" => brain.primary,
    # model="" => the provider's cheap/fast router model. The frontend sends a
    # concrete provider and either a concrete model or "" for "cheap default".
    provider: str = Field(default="", max_length=64)
    model: str = Field(default="", max_length=128)
    persist: bool = Field(default=True, description="Persist to jarvis.toml")


def _wiki_curator_cfg(request: Request) -> WikiCuratorConfig | None:
    cfg = _config(request)
    memory = getattr(cfg, "memory", None)
    wiki = getattr(memory, "wiki", None)
    return getattr(wiki, "curator", None)


def _available_brain_providers(request: Request) -> list[dict[str, object]]:
    """Selectable (provider, models) pairs for the Wiki picker.

    Provider list comes from the live BrainManager registry when reachable
    (same source as the brain-switch path), else from the TIER_DEFAULTS table.
    Each provider lists its cheap router model first, then its deep model, so
    the UI can offer "cheap default" plus an upgrade. The provider's own
    [brain.providers.<name>].model override (if set) is surfaced too.
    """
    from jarvis.brain.manager import TIER_DEFAULTS_BY_PROVIDER

    names: list[str] = []
    brain = getattr(request.app.state, "brain", None)
    if brain is not None and hasattr(brain, "available_providers"):
        try:
            names = list(brain.available_providers())
        except Exception:  # noqa: BLE001
            names = []
    if not names:
        names = sorted(TIER_DEFAULTS_BY_PROVIDER.get("router", {}))

    cfg = _config(request)
    providers_cfg = getattr(getattr(cfg, "brain", None), "providers", {}) or {}

    out: list[dict[str, object]] = []
    for name in names:
        models: list[str] = []
        # Cheap/fast first (what an empty model resolves to), then deep.
        for tier in ("router", "deep"):
            m = TIER_DEFAULTS_BY_PROVIDER.get(tier, {}).get(name)
            if m and m not in models:
                models.append(m)
        # Surface a user override from [brain.providers.<name>].model.
        override = getattr(providers_cfg.get(name), "model", "") if providers_cfg else ""
        if override and override not in models:
            models.insert(0, override)
        out.append({"provider": name, "models": models})
    return out


@router.get("/wiki-provider")
async def get_wiki_provider(request: Request) -> dict[str, object]:
    """Current Wiki-curator provider/model + the selectable matrix.

    Returns the RAW config values (empty string = "follow brain.primary" /
    "cheap default"); the frontend renders the empty state explicitly so the
    user sees they are tracking the main brain rather than a stale concrete
    pin.
    """
    curator = _wiki_curator_cfg(request)
    return {
        "provider": getattr(curator, "provider", "") or "",
        "model": getattr(curator, "model", "") or "",
        "available": _available_brain_providers(request),
    }


@router.put("/wiki-provider")
async def put_wiki_provider(body: WikiProviderBody, request: Request) -> dict[str, object]:
    provider = body.provider.strip()
    model = body.model.strip()

    # Resolve the selectable matrix ONCE: reused for validation below and for
    # the response body (avoids a second BrainManager round-trip per PUT).
    available = _available_brain_providers(request)

    # Validate the provider against the selectable matrix. An empty provider is
    # valid and means "follow brain.primary" (resolved later by the curator).
    if provider:
        known = {p["provider"] for p in available}
        if provider not in known:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown brain provider {body.provider!r} "
                    f"(available: {sorted(known)})."
                ),
            )

    # Persist FIRST: jarvis.toml is the only source of truth, so the in-memory
    # cfg must not show a value the disk never received (live would show the new
    # provider while a restart reverts it). Persist to [memory.wiki.curator]
    # (AP-7: lock + tempfile + BOM-safe via config_writer). Best-effort: a
    # read-only / locked toml must not break a live apply.
    persisted = False
    if body.persist:
        try:
            from jarvis.core import config_writer
            from jarvis.core.config import resolve_config_path

            config_writer.set_wiki_curator_provider(
                provider, model=model, path=resolve_config_path()
            )
            persisted = True
        except Exception as exc:  # noqa: BLE001
            log.warning("wiki-provider persist failed (live apply still attempted): %s", exc)

    # In-memory cfg update so a later cfg read agrees pre-restart — ONLY when the
    # disk write succeeded (or persist was not requested). Skipping it on a
    # persist failure keeps the live cfg in sync with what a restart will read.
    if persisted or not body.persist:
        curator_cfg = _wiki_curator_cfg(request)
        if curator_cfg is not None:
            for attr, value in (("provider", provider), ("model", model)):
                try:
                    setattr(curator_cfg, attr, value)
                except Exception as exc:  # noqa: BLE001 — frozen model is not an error
                    log.debug("in-memory wiki.curator.%s update skipped: %s", attr, exc)

    # Live-apply: a running WikiCurator holds a WikiCuratorLLM (._llm) whose
    # ._cfg is the WikiCuratorConfig and whose ._brain is a lazily-cached Brain.
    # Mutating ._cfg and clearing ._brain makes the NEXT ingest re-resolve the
    # provider/model through _resolve_provider_and_model — no restart needed.
    applied_live = False
    curator = get_running_curator()
    llm = getattr(curator, "_llm", None)
    live_cfg = getattr(llm, "_cfg", None)
    if live_cfg is not None:
        try:
            live_cfg.provider = provider
            live_cfg.model = model
            llm._brain = None  # force re-resolution on the next ingest
            llm._resolved_provider = None
            llm._resolved_model = None
            applied_live = True
        except Exception as exc:  # noqa: BLE001 — never fail the save on a live hiccup
            log.warning("wiki-provider live-apply failed (persisted; applies next ingest): %s", exc)

    return {
        "ok": True,
        "provider": provider,
        "model": model,
        "available": available,
        "persisted": persisted,
        "applied_live": applied_live,
        # The curator re-resolves on the next ingest; when not live-applied it
        # takes effect after the next ingest / restart.
        "restart_required": not applied_live,
    }


# ---------------------------------------------------------------------------
# Voice silence window (the user-tunable "think buffer"). GET current + bounds;
# PUT to change. Persisted to jarvis.toml [speech].vad_silence_ms AND live-applied
# to the running SpeechPipeline (set_silence_window_ms → SileroEndpointer), so a
# change takes effect immediately without a restart; a headless/down pipeline
# falls back to "applies on next start". Range 500–5000 ms, default 1500.
# ---------------------------------------------------------------------------

_SILENCE_WINDOW_MIN = 500
_SILENCE_WINDOW_MAX = 5000
_SILENCE_WINDOW_DEFAULT = 1500


class SilenceWindowBody(BaseModel):
    ms: int = Field(..., ge=_SILENCE_WINDOW_MIN, le=_SILENCE_WINDOW_MAX)
    persist: bool = Field(default=True, description="Persist as boot default in jarvis.toml")


def _current_silence_window_ms(request: Request) -> int:
    cfg = _config(request)
    speech = getattr(cfg, "speech", None)
    return int(getattr(speech, "vad_silence_ms", _SILENCE_WINDOW_DEFAULT))


@router.get("/silence-window")
async def get_silence_window(request: Request) -> dict[str, object]:
    return {
        "ms": _current_silence_window_ms(request),
        "default": _SILENCE_WINDOW_DEFAULT,
        "min": _SILENCE_WINDOW_MIN,
        "max": _SILENCE_WINDOW_MAX,
    }


@router.put("/silence-window")
async def put_silence_window(body: SilenceWindowBody, request: Request) -> dict[str, object]:
    ms = int(body.ms)  # already range-validated by the Pydantic Field

    # Best-effort in-memory cfg update so a later cfg read agrees pre-restart.
    cfg = _config(request)
    if cfg is not None and getattr(cfg, "speech", None) is not None:
        try:
            cfg.speech.vad_silence_ms = ms  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — frozen model is not an error
            log.debug("in-memory speech.vad_silence_ms update skipped: %s", exc)

    persisted = False
    if body.persist:
        try:
            from jarvis.core import config_writer
            from jarvis.core.config import resolve_config_path

            config_writer.set_silence_window_ms(ms, path=resolve_config_path())
            persisted = True
        except Exception as exc:  # noqa: BLE001 — persist is best-effort
            log.warning("silence-window persist failed (live apply still attempted): %s", exc)

    # Live-apply to the running voice pipeline so the new window works
    # immediately — no app restart. Best-effort: a headless/down pipeline just
    # means it applies on next start.
    applied_live = False
    pipeline = getattr(request.app.state, "speech_pipeline", None)
    if pipeline is not None and hasattr(pipeline, "set_silence_window_ms"):
        try:
            pipeline.set_silence_window_ms(ms)
            applied_live = True
        except Exception as exc:  # noqa: BLE001 — never fail the save on a live-apply hiccup
            log.warning("silence-window live-apply failed (persisted; applies on restart): %s", exc)

    return {
        "ok": True,
        "ms": ms,
        "default": _SILENCE_WINDOW_DEFAULT,
        "persisted": persisted,
        "applied_live": applied_live,
        "restart_required": not applied_live,
    }


# ---------------------------------------------------------------------------
# Master TTS output volume — how loudly Jarvis speaks.
#
# GET to read, PUT to change. A 0.0–1.0 amplitude gain (1.0 = full, the
# historical unattenuated behaviour), the same unit as [tts].volume; the UI
# renders it as a 0–100% slider. Persisted to jarvis.toml [tts].volume AND
# live-applied to the running SpeechPipeline (set_tts_volume → AudioPlayer), so
# a change is audible immediately without a restart; a headless/down pipeline
# falls back to "applies on next start". Provider-independent (applied in the
# shared player, so it covers every TTS provider and ack chimes alike).
# ---------------------------------------------------------------------------

_TTS_VOLUME_DEFAULT = 1.0


class TtsVolumeBody(BaseModel):
    volume: float = Field(..., ge=0.0, le=1.0)
    persist: bool = Field(default=True, description="Persist as boot default in jarvis.toml")


def _current_tts_volume(request: Request) -> float:
    cfg = _config(request)
    tts = getattr(cfg, "tts", None)
    try:
        return max(0.0, min(1.0, float(getattr(tts, "volume", _TTS_VOLUME_DEFAULT))))
    except (TypeError, ValueError):
        return _TTS_VOLUME_DEFAULT


@router.get("/tts-volume")
async def get_tts_volume(request: Request) -> dict[str, object]:
    return {
        "volume": _current_tts_volume(request),
        "default": _TTS_VOLUME_DEFAULT,
        "min": 0.0,
        "max": 1.0,
    }


@router.put("/tts-volume")
async def put_tts_volume(body: TtsVolumeBody, request: Request) -> dict[str, object]:
    volume = float(body.volume)  # already range-validated by the Pydantic Field

    # Best-effort in-memory cfg update so a later cfg read agrees pre-restart.
    cfg = _config(request)
    if cfg is not None and getattr(cfg, "tts", None) is not None:
        try:
            cfg.tts.volume = volume  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — frozen model is not an error
            log.debug("in-memory tts.volume update skipped: %s", exc)

    persisted = False
    if body.persist:
        try:
            from jarvis.core import config_writer
            from jarvis.core.config import resolve_config_path

            config_writer.set_tts_volume(volume, path=resolve_config_path())
            persisted = True
        except Exception as exc:  # noqa: BLE001 — persist is best-effort
            log.warning("tts-volume persist failed (live apply still attempted): %s", exc)

    # Live-apply to the running voice pipeline so the new volume is audible
    # immediately — no app restart. Best-effort: a headless/down pipeline just
    # means it applies on next start.
    applied_live = False
    pipeline = getattr(request.app.state, "speech_pipeline", None)
    if pipeline is not None and hasattr(pipeline, "set_tts_volume"):
        try:
            pipeline.set_tts_volume(volume)
            applied_live = True
        except Exception as exc:  # noqa: BLE001 — never fail the save on a live-apply hiccup
            log.warning("tts-volume live-apply failed (persisted; applies on restart): %s", exc)

    return {
        "ok": True,
        "volume": volume,
        "default": _TTS_VOLUME_DEFAULT,
        "persisted": persisted,
        "applied_live": applied_live,
        "restart_required": not applied_live,
    }
