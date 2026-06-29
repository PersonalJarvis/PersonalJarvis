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
import sys

from .constants import CALL_STATUSES, CallStatusLiteral

# L7: Python 3.13 removed the stdlib ``audioop`` module, which the media transcode
# path (session.py / audio.py) imports. On 3.13+ it needs the ``audioop-lts``
# backport (which re-provides the ``audioop`` name). Probed in is_available() so the
# feature reports honestly instead of 500-ing on the first media socket.
_PY313_PLUS: bool = sys.version_info >= (3, 13)


def is_available() -> bool:
    """Return ``True`` when telephony can actually run on this host.

    Used by the routes for graceful degradation: when this is ``False`` the
    Twilio-facing endpoints (webhook, REST credential test, provisioning)
    return a clear feature-disabled response rather than raising an ImportError
    500. Requires the optional ``twilio`` SDK AND — on Python 3.13+ — the
    ``audioop`` module (stdlib pre-3.13, else the ``audioop-lts`` backport), which
    the in-process media transcode path needs.
    """
    if importlib.util.find_spec("twilio") is None:
        return False
    if _PY313_PLUS and importlib.util.find_spec("audioop") is None:
        return False
    return True


__all__ = [
    "CALL_STATUSES",
    "CallStatusLiteral",
    "is_available",
]
