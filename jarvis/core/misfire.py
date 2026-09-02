"""Misfire policy shared by every scheduler in the app (BUG-212).

A scheduled slot the app was not running for — the box was off, asleep, or
the process was restarted hours later — must NOT be "caught up" whenever the
process next comes up. That is how the 07:30 Morning Briefing arrived at
10:15, 15:04 and 20:49 on different days: the slot was stored the evening
before, the app was not up at 07:30, and the first scheduler tick after boot
fired the stale slot immediately.

The rule, the same for the workflow cron scheduler (``jarvis/workflows``),
the recurring task scheduler (``jarvis/tasks``) and the Conductor:

* a slot that is late by at most :data:`MISFIRE_GRACE_S` still fires — a
  routine due at 07:30 on a machine that took ten minutes to boot is still a
  morning routine;
* a slot older than that is **missed**: it is recorded as such, never run,
  and the schedule continues from the NEXT occurrence relative to now.

One-shot triggers (a reminder "in 30 seconds", an ``at_time`` task) are not
covered by this rule and keep firing late: a reminder must survive a crash
(ADR-0005, H9) and there is no next occurrence to skip to.
"""
from __future__ import annotations

from typing import Final

#: How late a recurring slot may fire before it counts as missed. Thirty
#: minutes covers a slow boot or a short sleep; anything longer is no longer
#: the slot the user asked for (a "morning" briefing at 15:00 is not one).
MISFIRE_GRACE_S: Final[float] = 30 * 60.0
MISFIRE_GRACE_NS: Final[int] = int(MISFIRE_GRACE_S * 1e9)


def is_missed(due_ns: int, now_ns: int, *, grace_ns: int = MISFIRE_GRACE_NS) -> bool:
    """True when a slot due at ``due_ns`` is too old to still fire at ``now_ns``."""
    return due_ns + grace_ns < now_ns


def late_by_s(due_ns: int, now_ns: int) -> float:
    """Seconds between the slot and now — for the missed-run record."""
    return max(0.0, (now_ns - due_ns) / 1e9)
