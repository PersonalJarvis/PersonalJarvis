"""Process-wide references to the live runtime objects the brain tools need.

The App-Control tools (``describe-app-settings``, ``switch-provider``,
``manage-mcp-server``) run *inside* the BrainManager's tool loop, but they need
to reach runtime objects that are owned by the app/server layer and only exist
*after* the brain is built:

- the live ``BrainManager`` (to switch the brain provider live, no restart),
- the live ``SpeechPipeline`` (to hot-swap the TTS provider),
- the live ``MCPRegistry`` (to reload/start MCP servers after an mcp.json edit).

This module is the single, centralised home for those references — the same
lazy-singleton pattern already used for the MissionManager / Kontrollierer in
``jarvis/brain/factory.py`` (``set_mission_manager`` etc.), but collected in one
low-layer module so the tools import *one* thing and the bootstrap sites set
*one* thing.

A ``None`` getter return is honest: "not yet wired / not available in this
runtime" (e.g. a headless brain build before the server bootstrap, or voice
disabled so there is no SpeechPipeline). Callers degrade to "persisted, restart
required" rather than crashing — the anti-silent-drop contract (AD-OE6).

Thread-safety: assignment of a single object reference is atomic in CPython, and
these are write-once-at-bootstrap / read-many. No lock needed.
"""
from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

# Each ref is a 1-element list so the setter can rebind without ``global``.
_BRAIN_MANAGER: list[Any] = []
_SPEECH_PIPELINE: list[Any] = []
_MCP_REGISTRY: list[Any] = []

# The currently-running CHAT brain turn, as ``(task, loop)``. Registered by the
# chat dispatcher (``desktop_app._on_user_message``) for the lifetime of one
# ``await generate(...)`` and cancelled edge-triggered by the voice-hangup
# chokepoint so the bar's X stops a chat turn too — not just a voice turn (live
# bug 2026-06-19: ~27 ignored X presses while a chat turn kept thinking).
_ACTIVE_CHAT_TURN: list[Any] = []


def _set(ref: list[Any], value: Any) -> None:
    if ref:
        ref[0] = value
    else:
        ref.append(value)


def set_brain_manager(manager: Any) -> None:
    """Register the live BrainManager (called from the brain factory)."""
    _set(_BRAIN_MANAGER, manager)


def get_brain_manager() -> Any | None:
    """The live BrainManager, or ``None`` if not yet built."""
    return _BRAIN_MANAGER[0] if _BRAIN_MANAGER else None


def set_speech_pipeline(pipeline: Any) -> None:
    """Register the live SpeechPipeline (called from the desktop-app bootstrap)."""
    _set(_SPEECH_PIPELINE, pipeline)


def get_speech_pipeline() -> Any | None:
    """The live SpeechPipeline, or ``None`` (headless / voice disabled)."""
    return _SPEECH_PIPELINE[0] if _SPEECH_PIPELINE else None


def set_mcp_registry(registry: Any) -> None:
    """Register the live MCPRegistry (called from the server/launcher bootstrap)."""
    _set(_MCP_REGISTRY, registry)


def get_mcp_registry() -> Any | None:
    """The live MCPRegistry, or ``None`` if MCP support is not wired."""
    return _MCP_REGISTRY[0] if _MCP_REGISTRY else None


# The live FastAPI app (set by WebServer._build_app). The ``app-command`` tool
# executes Command-Registry commands through it in-process (httpx ASGI
# transport — full route validation, no TCP), so a voice command runs the
# exact same code path as the UI button for the same action.
_WEB_APP: list[Any] = []


def set_web_app(app: Any) -> None:
    """Register the live FastAPI app (called from the WebServer build)."""
    _set(_WEB_APP, app)


def get_web_app() -> Any | None:
    """The live FastAPI app, or ``None`` before the server is built."""
    return _WEB_APP[0] if _WEB_APP else None


# Wake-model load coordination (boot speed). When a CUSTOM wake phrase boots,
# the light base/cpu wake model load competes for CPU/disk with non-urgent
# boot-storm housekeeping (the deferred DocRegistry/SkillRegistry disk scans).
# Letting that housekeeping yield until the wake model is loaded makes the wake
# word hear-ready sooner — the user's "window -> Jarvis-Bar -> rest" order. Two
# flags so the gate is a NO-OP unless a wake model is actually loading (headless
# / voice-off must never wait): ``_WAKE_MODEL_EXPECTED`` is set the moment voice
# boot decides it will load a local wake model; ``_WAKE_MODEL_READY`` once it has.
_WAKE_MODEL_EXPECTED: list[bool] = []
_WAKE_MODEL_READY: list[bool] = []


def signal_wake_model_expected() -> None:
    """Mark that voice boot WILL load a local wake model (so housekeeping waits)."""
    _set(_WAKE_MODEL_EXPECTED, True)


def signal_wake_model_ready() -> None:
    """Mark the wake model as loaded — releases any housekeeping gate."""
    _set(_WAKE_MODEL_READY, True)


def is_wake_model_expected() -> bool:
    return bool(_WAKE_MODEL_EXPECTED and _WAKE_MODEL_EXPECTED[0])


def is_wake_model_ready() -> bool:
    return bool(_WAKE_MODEL_READY and _WAKE_MODEL_READY[0])


async def await_wake_model_ready(timeout: float = 12.0) -> bool:
    """Yield until the wake model is loaded (bounded), so non-urgent boot
    housekeeping does not steal CPU/disk from the wake-model load.

    NO-OP when no wake model is loading (headless / voice off): returns
    immediately so those paths never regress. Polling (not an asyncio.Event) so
    it is safe regardless of which loop set the flag. Returns True if it became
    ready, False on timeout.
    """
    if not is_wake_model_expected():
        return True
    waited = 0.0
    while waited < timeout:
        if is_wake_model_ready():
            return True
        await asyncio.sleep(0.25)
        waited += 0.25
    return False


def set_active_chat_turn(task: asyncio.Task[Any], loop: asyncio.AbstractEventLoop) -> None:
    """Arm the bar's X for THIS chat turn.

    Called by the chat dispatcher right before it awaits ``brain.generate(...)``.
    The owning ``loop`` is stored alongside the task so the cancel can be
    scheduled thread-safely — ``request_hangup`` runs on the Tk overlay thread,
    not the asyncio loop.
    """
    if _ACTIVE_CHAT_TURN:
        _ACTIVE_CHAT_TURN[0] = (task, loop)
    else:
        _ACTIVE_CHAT_TURN.append((task, loop))


def clear_active_chat_turn(task: asyncio.Task[Any]) -> None:
    """Disarm the X once the turn is done — but only for THIS task.

    A late ``finally`` from a finished turn must never retract the registration
    of the turn that is running now, or it would silently disarm the X.
    """
    if _ACTIVE_CHAT_TURN and _ACTIVE_CHAT_TURN[0][0] is task:
        _ACTIVE_CHAT_TURN.clear()


def cancel_active_chat_turn() -> bool:
    """Cancel the in-flight chat turn, if any. The single chat-abort chokepoint.

    Returns ``True`` when a turn was armed (a cancel was scheduled), ``False``
    when nothing was running. Edge-triggered — it fires exactly when the X is
    pressed — so there is no stale-``Event`` problem: an idle chat path is a
    clean no-op. The cancel is marshalled onto the task's owning loop via
    ``call_soon_threadsafe`` because the caller may be on the Tk thread; a dead
    loop is swallowed (the turn is gone anyway). Cancelling also disarms, so a
    second X press is a no-op.
    """
    if not _ACTIVE_CHAT_TURN:
        return False
    task, loop = _ACTIVE_CHAT_TURN[0]
    _ACTIVE_CHAT_TURN.clear()
    if task.done():
        return False
    try:
        loop.call_soon_threadsafe(task.cancel)
    except RuntimeError:
        # Loop already closed / not running — the turn cannot still be alive.
        logger.debug("cancel_active_chat_turn: owning loop is gone (non-fatal)")
        return False
    return True


def _reset_for_tests() -> None:
    """Clear all refs. Test-only helper (fixtures call this in teardown)."""
    _BRAIN_MANAGER.clear()
    _SPEECH_PIPELINE.clear()
    _MCP_REGISTRY.clear()
    _WEB_APP.clear()
    _ACTIVE_CHAT_TURN.clear()
    _WAKE_MODEL_EXPECTED.clear()
    _WAKE_MODEL_READY.clear()
