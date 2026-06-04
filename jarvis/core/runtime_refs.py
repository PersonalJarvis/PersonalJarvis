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

from typing import Any

# Each ref is a 1-element list so the setter can rebind without ``global``.
_BRAIN_MANAGER: list[Any] = []
_SPEECH_PIPELINE: list[Any] = []
_MCP_REGISTRY: list[Any] = []


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


def _reset_for_tests() -> None:
    """Clear all refs. Test-only helper (fixtures call this in teardown)."""
    _BRAIN_MANAGER.clear()
    _SPEECH_PIPELINE.clear()
    _MCP_REGISTRY.clear()
