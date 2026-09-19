"""Deterministic teacher-mode commands (Japanese first, English as a peer).

A command is recognised only by an explicit phrase — the classroom is full of
speech, and ordinary sentences must reach the lesson log, not trigger a mode:

* ``plan``    - "mogi jugyou wo tsukutte: <topic>" / "make a mock lesson on <topic>"
* ``start``   - "jugyou wo hajimete (25 fun)" / "start the lesson (25 minutes)"
* ``summary`` - "Jarvis, matomete" / "Jarvis, summarize" (only while a lesson
  runs; outside a lesson "summarize" is an ordinary request)
* ``end``     - "jugyou wo owatte" / "end the lesson"
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PLAN_RE = re.compile(
    "(\u6a21\u64ec\u6388\u696d|\u6307\u5c0e\u6848|\u6388\u696d\u6848)"
    "(\u3092|\u306e)?(\u4f5c\u3063\u3066|\u4f5c\u6210|\u8003\u3048\u3066|\u6e96\u5099|\u3064\u304f\u3063\u3066|\u7528\u610f)"
)
_START_RE = re.compile("\u6388\u696d\u3092(\u958b\u59cb|\u59cb\u3081)")
_SUMMARY_RE = re.compile(
    "("
    "\u307e\u3068\u3081\u3066"
    "|\u8981\u7d04\u3057\u3066"
    "|\u6574\u7406\u3057\u3066"
    ")"
)
_END_RE = re.compile("\u6388\u696d\u3092(\u7d42\u308f|\u7d42\u4e86|\u304a\u308f)")
_MINUTES_RE = re.compile(r"(\d{1,3})\s*(\u5206|min)")

_EN_PLAN_RE = re.compile(
    r"\b(make|create|prepare|plan)\b.{0,20}\b(mock|practice)?\s*lesson\b",
    re.I,
)
_EN_START_RE = re.compile(r"\bstart\b.{0,10}\b(the\s+)?lesson\b", re.I)
_EN_SUMMARY_RE = re.compile(r"\b(summari[sz]e|sum (it )?up|recap)\b", re.I)
_EN_END_RE = re.compile(r"\b(end|finish|stop)\b.{0,10}\b(the\s+)?lesson\b", re.I)


@dataclass(frozen=True)
class TeacherCommand:
    kind: str  # "plan" | "start" | "summary" | "end"
    minutes: int | None = None
    topic: str = ""


def match_teacher_command(text: str, *, lesson_active: bool) -> TeacherCommand | None:
    """The teacher command in ``text``, or None."""
    t = (text or "").strip()
    if not t:
        return None
    minutes_m = _MINUTES_RE.search(t)
    minutes = int(minutes_m.group(1)) if minutes_m else None
    if _END_RE.search(t) or _EN_END_RE.search(t):
        return TeacherCommand("end") if lesson_active else None
    if lesson_active and (_SUMMARY_RE.search(t) or _EN_SUMMARY_RE.search(t)):
        return TeacherCommand("summary")
    if _PLAN_RE.search(t) or _EN_PLAN_RE.search(t):
        return TeacherCommand("plan", minutes=minutes, topic=t)
    if _START_RE.search(t) or _EN_START_RE.search(t):
        return TeacherCommand("start", minutes=minutes)
    return None


__all__ = ["TeacherCommand", "match_teacher_command"]
