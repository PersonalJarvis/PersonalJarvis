"""Twilio telephony voice-agent package.

A caller dials a Twilio phone number and talks to Jarvis as a real-time voice
agent. The call audio is bridged via Twilio Media Streams (raw audio over a
WebSocket) so Jarvis can run its OWN STT -> Brain -> TTS stack and answer in
its OWN consistent Charon voice — exactly like the "Hey Jarvis" microphone
path (design spec ``docs/superpowers/specs/2026-05-24-twilio-telephony-design.md``).

Cloud-first doctrine: this package MUST NOT import ``sounddevice`` or the
``SpeechPipeline`` (both hard-import mic/speaker hardware). It composes the
three decoupled provider seams directly (``build_stt_from_config``,
``build_default_brain``, ``build_tts_from_config``).

The ``twilio`` SDK is an OPTIONAL extra (``pip install -e .[telephony]``).
Everything here degrades gracefully when it is not installed: ``is_available()``
returns ``False`` and the FastAPI routes return feature-disabled JSON instead
of crashing (AD-T8).
"""

from __future__ import annotations

import importlib.util

from .constants import CALL_STATUSES, CallStatusLiteral


def is_available() -> bool:
    """Return ``True`` when the optional ``twilio`` SDK is importable.

    Used by the routes for graceful degradation: when this is ``False`` the
    Twilio-facing endpoints (webhook, REST credential test, provisioning)
    cannot work, so they return a clear feature-disabled response rather than
    raising an ImportError 500. The in-process media path (audio transcode,
    session loop) does NOT need the SDK — only signature validation and the
    REST provisioning client do.
    """
    return importlib.util.find_spec("twilio") is not None


__all__ = [
    "CALL_STATUSES",
    "CallStatusLiteral",
    "is_available",
]
