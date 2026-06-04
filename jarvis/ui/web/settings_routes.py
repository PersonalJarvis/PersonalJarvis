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

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from jarvis.brain.manager import SUPPORTED_REPLY_LANGUAGES

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
            detail="Brain-Manager nicht verfügbar (vermutlich Headless-Mode)",
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
# PUT one action at a time. Persisted to jarvis.toml [trigger]; applies on the
# next voice bootstrap (a Jarvis restart) — bindings are armed once at pipeline
# start. The legacy /ptt-hotkey route above stays for backward compatibility.
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
    hotkey: str = Field(..., min_length=1, max_length=64)
    persist: bool = Field(default=True, description="Persist to jarvis.toml")


@router.get("/keybinds")
async def get_keybinds(request: Request) -> dict[str, object]:
    from jarvis.core.config import TriggerConfig

    cfg = _config(request)
    trig = getattr(cfg, "trigger", None) if cfg is not None else None
    d = TriggerConfig()
    return {
        "keybinds": _keybind_values(trig),
        "defaults": {"call": d.hotkey_call, "hangup": d.hotkey_hangup, "ptt": d.hotkey},
        "push_to_talk": bool(getattr(trig, "push_to_talk", True)) if trig else True,
        "suggestions": list(_HOTKEY_SUGGESTIONS),
        "restart_required": True,
    }


@router.put("/keybinds")
async def put_keybind(body: KeybindBody, request: Request) -> dict[str, object]:
    from jarvis.core.config_writer import KEYBIND_ACTIONS, KEYBIND_TOML_KEY
    from jarvis.trigger.hotkey import validate_hotkey

    action = body.action.strip().lower()
    if action not in KEYBIND_ACTIONS:
        raise HTTPException(status_code=400, detail=f"unknown action: {action}")
    hotkey = body.hotkey.strip().lower()

    # The backend is the authority — a browser key-capture cannot be trusted to
    # filter OS-critical / unusable combos (AltGr detection is unreliable there).
    ok, reason = validate_hotkey(hotkey)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)

    cfg = _config(request)
    trig = getattr(cfg, "trigger", None) if cfg is not None else None

    # Collision check: one chord can't both answer and hang up.
    for other_action, other_combo in _keybind_values(trig).items():
        if other_action != action and other_combo.strip().lower() == hotkey:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{hotkey}' is already bound to '{other_action}' — "
                    "pick a different combo."
                ),
            )

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

    return {
        "ok": True,
        "action": action,
        "hotkey": hotkey,
        "persisted": persisted,
        # Bindings are armed once at SpeechPipeline construction, so a keybind
        # change needs a voice restart to take effect.
        "restart_required": True,
    }


# ---------------------------------------------------------------------------
# Assistant name (persona identity). GET current (explicit + resolved); PUT to
# change. Persisted to jarvis.toml [persona].name; applies on the next
# BrainManager build (a Jarvis restart). Empty = derive from the wake phrase.
# ---------------------------------------------------------------------------


class AssistantNameBody(BaseModel):
    # Empty string is allowed and meaningful: "" = auto-derive from wake phrase.
    name: str = Field(default="", max_length=40)
    persist: bool = Field(default=True, description="Persist to jarvis.toml")


@router.get("/assistant-name")
async def get_assistant_name(request: Request) -> dict[str, object]:
    from jarvis.brain.assistant_name import DEFAULT_ASSISTANT_NAME, resolve_assistant_name

    cfg = _config(request)
    persona = getattr(cfg, "persona", None) if cfg is not None else None
    explicit = (getattr(persona, "name", "") or "") if persona is not None else ""
    return {
        "name": explicit,                              # the explicit override ("" = auto)
        "resolved": resolve_assistant_name(cfg),       # what the assistant actually calls itself
        "default": DEFAULT_ASSISTANT_NAME,
    }


@router.put("/assistant-name")
async def put_assistant_name(body: AssistantNameBody, request: Request) -> dict[str, object]:
    from jarvis.brain.assistant_name import resolve_assistant_name

    name = body.name.strip()

    # Best-effort in-memory cfg update so a later cfg read agrees pre-restart.
    cfg = _config(request)
    if cfg is not None and getattr(cfg, "persona", None) is not None:
        try:
            cfg.persona.name = name  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — frozen model is not an error
            log.debug("in-memory persona.name update skipped: %s", exc)

    persisted = False
    if body.persist:
        try:
            from jarvis.core import config_writer

            config_writer.set_assistant_name(name)
            persisted = True
        except Exception as exc:  # noqa: BLE001
            log.warning("assistant-name persist failed: %s", exc)

    return {
        "ok": True,
        "name": name,
        "resolved": resolve_assistant_name(cfg),
        "persisted": persisted,
        # The system prompt is assembled once per BrainManager, so a name change
        # needs a restart to take effect.
        "restart_required": True,
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
    return {
        "enabled": enabled,
        "supported": status.supported,
        "installed": status.installed,
        "matches_spec": status.matches_spec,
        "platform": caps.platform,
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
            status = await asyncio.to_thread(manager.install, spec)
        else:
            status = await asyncio.to_thread(manager.uninstall)
        applied_live = status.supported
    except Exception as exc:  # noqa: BLE001 — never fail the toggle on an apply hiccup
        log.warning("autostart live-apply failed (persisted; applies on restart): %s", exc)
        status = await asyncio.to_thread(manager.status, spec)

    return {
        "ok": True,
        "enabled": enabled,
        "supported": status.supported,
        "installed": status.installed,
        "matches_spec": status.matches_spec,
        "platform": caps.platform,
        "resolved_command": spec.command_line(),
        "entry_path": status.entry_path,
        "applied_live": applied_live,
        "persisted": persisted,
        "detail": status.detail,
        "restart_required": False,
    }


_OVERLAY_STYLES = ("whisper_bar", "mascot", "none")


class OverlayStyleBody(BaseModel):
    style: str = Field(..., min_length=1)
    persist: bool = Field(default=True, description="Persist as boot default in jarvis.toml")


@router.get("/overlay-style")
async def get_overlay_style(request: Request) -> dict[str, object]:
    """Current on-screen overlay style + the selectable options."""
    cfg = _config(request)
    ui = getattr(cfg, "ui", None)
    current = getattr(ui, "orb_style", None) or "whisper_bar"
    return {"style": current, "options": list(_OVERLAY_STYLES)}


@router.put("/overlay-style")
async def put_overlay_style(body: OverlayStyleBody, request: Request) -> dict[str, object]:
    """Switch the on-screen overlay (whisper_bar / mascot / none).

    Persists [ui].orb_style and live-swaps the running surface via the
    DesktopApp (app.state.desktop_app.swap_overlay). When no live app is
    reachable (headless), the choice is persisted and applies on restart.
    """
    style = body.style.strip()
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
            applied_live = bool(result.get("applied_live")) if isinstance(result, dict) else bool(result)
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


@router.post("/restart-app")
async def restart_app(request: Request) -> dict[str, object]:
    """Cleanly self-restart the desktop app.

    Delivers a pending overlay-style change (bar <-> mascot) that cannot be
    applied live (BUG-031) without the user closing + reopening by hand. The
    DesktopApp spawns a detached relauncher and quits ~0.8 s later, so this
    request returns 200 first. Returns 503 on a headless host (no window).
    """
    desktop = getattr(request.app.state, "desktop_app", None)
    fn = getattr(desktop, "request_restart", None)
    if not callable(fn):
        raise HTTPException(
            status_code=503, detail="self-restart unavailable on this host"
        )
    scheduled = await asyncio.to_thread(fn)
    if not scheduled:
        raise HTTPException(
            status_code=503, detail="no desktop window to restart"
        )
    return {"ok": True, "restarting": True}


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
