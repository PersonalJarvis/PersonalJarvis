"""When a created agent reviews its chat for memories and skills.

Reviewing after every single answer costs one model call per turn on the
agent's own seat. The agent instead collects its person's turns in a review
window and reviews the whole window at once when one of these holds:

* the person gave a clear learning signal: an explicit "remember", a lasting
  language choice, a correction or frustration (deterministic, no model);
* :data:`REVIEW_EVERY_USER_TURNS` turns of the person accumulated;
* :data:`SKILL_REVIEW_TOOL_STEPS` tool steps accumulated, enough real work
  for a reusable procedure;
* the window is older than :data:`WINDOW_MAX_AGE_MS` (checked on the next
  turn, never by a timer: an idle agent costs nothing).

Turns started by Jarvis, teammates or routines keep their per-turn review.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Final

__all__ = [
    "REVIEW_EVERY_USER_TURNS",
    "SKILL_REVIEW_TOOL_STEPS",
    "WINDOW_MAX_AGE_MS",
    "ReviewWindow",
    "learning_signal",
    "window_events",
]

REVIEW_EVERY_USER_TURNS: Final[int] = 6
SKILL_REVIEW_TOOL_STEPS: Final[int] = 8
WINDOW_MAX_AGE_MS: Final[int] = 12 * 3_600_000

#: A correction or frustration is a first-class learning signal. Input
#: vocabulary of the three product languages; a false positive only reviews
#: a little earlier.
_SIGNAL: Final[re.Pattern[str]] = re.compile(
    r"\b(?:remember|from now on|don'?t ever|stop doing|that'?s wrong|that is wrong|"
    r"not like that|i told you|you always|you never|wrong again|next time|in (?:the )?future|"
    r"i(?:'d)? prefer|"
    r"merk dir|ab jetzt|ab sofort|nie wieder|nicht so|das ist falsch|"  # i18n-allow
    r"hab ich (?:dir )?(?:doch )?gesagt|schon wieder|n(?:ä|ae)chstes mal|in zukunft|"  # i18n-allow
    r"ich bevorzuge|"  # i18n-allow
    r"recuerda|a partir de ahora|así no|eso está mal|ya te dije|otra vez|"  # i18n-allow
    r"la próxima vez|en el futuro|prefiero)\b",  # i18n-allow
    re.IGNORECASE,
)
#: How a board message reads in the receiver's chat (``frame_incoming`` /
#: ``frame_assignment``): never the person's own words.
_FRAMED: Final[re.Pattern[str]] = re.compile(r"^\[[a-z_]+ from [^\]\n]+\]")


def learning_signal(user_texts: list[str]) -> bool:
    """Whether the person said something the agent should learn from now."""
    from .memory_intent import requested_memory
    from .reply_preference import durable_language_request

    for text in user_texts:
        if requested_memory(text) is not None or durable_language_request(text):
            return True
        if _SIGNAL.search(text):
            return True
    return False


def window_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The window's evidence without board messages posing as the person."""
    out: list[dict[str, Any]] = []
    for event in events:
        if event.get("kind") == "user_message":
            text = str((event.get("payload") or {}).get("text") or "")
            if _FRAMED.match(text.lstrip()):
                continue
        out.append(event)
    return out


@dataclass(slots=True)
class ReviewWindow:
    """The unreviewed part of one chat, persisted in the society meta table."""

    since_seq: int = 0
    user_turns: int = 0
    tool_steps: int = 0
    started_ms: int = 0

    @staticmethod
    def key(session_id: str) -> str:
        return f"review:window:{session_id}"

    @classmethod
    def parse(cls, raw: str) -> ReviewWindow:
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        return cls(
            since_seq=int(data.get("since_seq") or 0),
            user_turns=int(data.get("user_turns") or 0),
            tool_steps=int(data.get("tool_steps") or 0),
            started_ms=int(data.get("started_ms") or 0),
        )

    def dump(self) -> str:
        return json.dumps(
            {
                "since_seq": self.since_seq,
                "user_turns": self.user_turns,
                "tool_steps": self.tool_steps,
                "started_ms": self.started_ms,
            }
        )

    def add_turn(self, events: list[dict[str, Any]], now: int) -> None:
        if not self.user_turns:
            seqs = [int(e.get("seq") or 0) for e in events if e.get("seq")]
            self.since_seq = min(seqs) if seqs else 0
            self.started_ms = now
        self.user_turns += 1
        self.tool_steps += sum(1 for e in events if e.get("kind") == "tool_call")

    def due(self, user_texts: list[str], now: int) -> bool:
        return (
            learning_signal(user_texts)
            or self.user_turns >= REVIEW_EVERY_USER_TURNS
            or self.tool_steps >= SKILL_REVIEW_TOOL_STEPS
            or (self.started_ms > 0 and now - self.started_ms >= WINDOW_MAX_AGE_MS)
        )
