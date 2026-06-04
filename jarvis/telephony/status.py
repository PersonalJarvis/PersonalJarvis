"""Runtime registry for active + recent telephony calls (``TelephonyManager``).

Lives at ``app.state.telephony_manager`` (set in ``server.py``). Holds:

  * the active per-call sessions keyed by ``CallSid`` (so the WS handler can
    bind a socket to an in-flight call and so ``/status`` can report
    ``active_calls``),
  * a bounded ring buffer of recent finished calls for ``GET /calls``,
  * the minted per-call WS secrets so the media socket can authenticate the
    secret it receives in ``start.customParameters``.

A runtime assertion ties the ``CallRecord.status`` field to ``CALL_STATUSES``
(five-layer enum guard, AD-T7) so a typo'd status string fails loudly here
rather than silently drifting from the TS layer.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, field_validator

from .constants import CALL_STATUSES, CallStatusLiteral

if TYPE_CHECKING:
    from .session import TelephonyCallSession


class CallRecord(BaseModel):
    """One row in the recent-calls ring buffer (the wire shape of /calls)."""

    call_sid: str = ""
    from_number: str = ""
    to_number: str = ""
    started_at: float = 0.0
    ended_at: float | None = None
    duration_s: float = 0.0
    status: CallStatusLiteral = "ringing"
    turns: int = 0

    @field_validator("status")
    @classmethod
    def _status_in_enum(cls, v: str) -> str:
        # Five-layer enum guard: a value that is not in the Python source of
        # truth must never reach the wire (AD-T7 / AP-4).
        assert v in CALL_STATUSES, f"unknown call status {v!r}"
        return v

    def to_api(self) -> dict[str, object]:
        """Serialise with the contract's ``from``/``to`` keys."""
        return {
            "call_sid": self.call_sid,
            "from": self.from_number,
            "to": self.to_number,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_s": round(self.duration_s, 2),
            "status": self.status,
            "turns": self.turns,
        }


@dataclass
class _PendingCall:
    """A call whose webhook fired but whose media socket hasn't connected yet."""

    secret: str
    from_number: str = ""
    to_number: str = ""
    created_at: float = field(default_factory=time.time)


class TelephonyManager:
    """Process-wide registry of telephony calls.

    Thread/async note: all mutations happen on the asyncio event loop (FastAPI
    handlers), so no lock is needed; the structures are plain Python containers.
    """

    def __init__(self, *, recent_capacity: int = 50, pending_ttl_s: float = 120.0) -> None:
        self._active: dict[str, TelephonyCallSession] = {}
        self._pending: dict[str, _PendingCall] = {}
        self._recent: deque[CallRecord] = deque(maxlen=recent_capacity)
        self._pending_ttl_s = pending_ttl_s
        self._reachable: bool | None = None
        self._reachable_error: str | None = None
        self._reachable_checked_at: float = 0.0

    # -- pending (webhook -> media-socket handshake) ------------------------

    def register_pending(
        self, call_sid: str, secret: str, *, from_number: str = "", to_number: str = ""
    ) -> None:
        """Record a minted WS secret for a call we just answered via /voice."""
        self._evict_stale_pending()
        self._pending[call_sid] = _PendingCall(
            secret=secret, from_number=from_number, to_number=to_number
        )

    def consume_pending(self, call_sid: str, secret: str) -> _PendingCall | None:
        """Validate + remove a pending call's secret when its socket connects.

        Returns the pending record on a secret match, else ``None``. The secret
        is compared in constant time by the caller (``security.constant_time_equals``).
        """
        pending = self._pending.get(call_sid)
        if pending is None:
            return None
        if pending.secret != secret:
            return None
        return self._pending.pop(call_sid, None)

    def peek_pending(self, call_sid: str) -> _PendingCall | None:
        return self._pending.get(call_sid)

    def _evict_stale_pending(self) -> None:
        now = time.time()
        stale = [
            sid for sid, p in self._pending.items() if now - p.created_at > self._pending_ttl_s
        ]
        for sid in stale:
            self._pending.pop(sid, None)

    # -- active sessions ---------------------------------------------------

    def register_active(self, call_sid: str, session: TelephonyCallSession) -> None:
        self._active[call_sid] = session

    def unregister_active(self, call_sid: str) -> None:
        self._active.pop(call_sid, None)

    @property
    def active_calls(self) -> int:
        return len(self._active)

    def active_session(self, call_sid: str) -> TelephonyCallSession | None:
        return self._active.get(call_sid)

    # -- recent-call ring buffer ------------------------------------------

    def record_call(self, record: CallRecord) -> None:
        """Append a finished (or updated) call to the ring buffer.

        If a record with the same ``call_sid`` is already present (e.g. it was
        added at ``start`` and is now finalised at ``stop``), it is replaced in
        place so the buffer never double-counts a call.
        """
        for i, existing in enumerate(self._recent):
            if existing.call_sid == record.call_sid:
                self._recent[i] = record
                return
        self._recent.append(record)

    def recent_calls(self, limit: int = 20) -> list[dict[str, object]]:
        """Return the most-recent calls first, as API dicts."""
        items = list(self._recent)[-limit:]
        items.reverse()
        return [r.to_api() for r in items]

    # -- reachability cache ------------------------------------------------

    def set_reachable(self, reachable: bool, error: str | None = None) -> None:
        self._reachable = reachable
        self._reachable_error = error
        self._reachable_checked_at = time.time()

    @property
    def reachable(self) -> bool | None:
        return self._reachable

    @property
    def reachable_error(self) -> str | None:
        return self._reachable_error


__all__ = ["CallRecord", "TelephonyManager"]
