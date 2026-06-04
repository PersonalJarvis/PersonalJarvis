"""Frozen bus events for the telephony path.

These mirror the conventions in ``jarvis/core/events.py``: ``frozen=True``
dataclasses subclassing :class:`Event` (so they inherit ``trace_id`` and
``timestamp_ns``). They are published on the shared :class:`EventBus` so the
flight recorder / wildcard subscribers observe phone calls just like mic
turns. Subscriber exceptions are swallowed by the bus (AP-18) — never raise
out of a handler.
"""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.core.events import Event


@dataclass(frozen=True, slots=True)
class TelephonyCallStarted(Event):
    """A Twilio Media Streams call became active (the ``start`` event arrived)."""

    call_sid: str = ""
    from_number: str = ""
    to_number: str = ""
    stream_sid: str = ""


@dataclass(frozen=True, slots=True)
class TelephonyCallTurn(Event):
    """One completed STT -> Brain -> TTS exchange within a call."""

    call_sid: str = ""
    transcript: str = ""
    response_text: str = ""
    outbound_frames: int = 0


@dataclass(frozen=True, slots=True)
class TelephonyCallEnded(Event):
    """A call ended (caller hung up, hangup phrase, cap, or error)."""

    call_sid: str = ""
    status: str = ""  # one of jarvis.telephony.constants.CALL_STATUSES
    duration_s: float = 0.0
    turns: int = 0
    reason: str = ""  # human-readable end reason for logs


__all__ = [
    "TelephonyCallEnded",
    "TelephonyCallStarted",
    "TelephonyCallTurn",
]
