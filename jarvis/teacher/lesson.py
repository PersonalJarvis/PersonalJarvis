"""Lesson state, prompts and files for teacher mode.

A lesson keeps only TEXT: what the recognizer transcribed, with a timestamp.
No audio is stored anywhere. Files land in ``<user_data_dir>/lessons``:
``<stamp>-plan.md``, ``<stamp>-summary-<n>.md`` and ``<stamp>-report.md``.

The prompts are English (LLM-facing, like every directive in this repo) and
ask for the output in the lesson's language.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jarvis.core.paths import user_data_dir

DEFAULT_MINUTES = 25
#: The newest part of the transcript a summary reads (the model's window is
#: shared with Jarvis's own prompt; the lesson's gist fits in this much).
MAX_TRANSCRIPT_CHARS = 6000

_MUSIC_RE = re.compile("(\u97f3\u697d|music|musik)", re.IGNORECASE)

MUSIC_PERSPECTIVES = (
    "melody", "rhythm", "tempo", "dynamics", "timbre", "structure",
    "imagery/scene", "emotion", "expression",
)
#: The same elements under the names Japanese music lessons use.
MUSIC_PERSPECTIVES_JA = (
    "\u65cb\u5f8b",
    "\u30ea\u30ba\u30e0",
    "\u30c6\u30f3\u30dd",
    "\u5f37\u5f31",
    "\u97f3\u8272",
    "\u69cb\u6210",
    "\u60c5\u666f",
    "\u611f\u60c5",
    "\u8868\u73fe",
)


def lessons_dir() -> Path:
    path = user_data_dir() / "lessons"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Utterance:
    at: float
    text: str


@dataclass
class LessonSession:
    topic: str = ""
    minutes: int = DEFAULT_MINUTES
    language: str = "ja"
    started_at: float = field(default_factory=time.time)
    stamp: str = field(default_factory=lambda: datetime.now().strftime("%Y%m%d-%H%M"))
    utterances: list[Utterance] = field(default_factory=list)
    summaries: int = 0

    @property
    def is_music(self) -> bool:
        return bool(_MUSIC_RE.search(self.topic))

    def add(self, text: str) -> None:
        cleaned = (text or "").strip()
        if cleaned:
            self.utterances.append(Utterance(time.time(), cleaned))

    def elapsed_min(self, now: float | None = None) -> float:
        return ((now or time.time()) - self.started_at) / 60.0

    def remaining_min(self, now: float | None = None) -> float:
        return max(0.0, self.minutes - self.elapsed_min(now))

    def transcript(self, limit: int = MAX_TRANSCRIPT_CHARS) -> str:
        lines = [
            f"[{(u.at - self.started_at) / 60.0:4.1f} min] {u.text}" for u in self.utterances
        ]
        text = "\n".join(lines)
        return text[-limit:] if len(text) > limit else text


def _language_name(code: str) -> str:
    return {"ja": "Japanese", "de": "German", "es": "Spanish"}.get(code, "English")


PLAN_SYSTEM = (
    "You are an experienced teacher and teacher trainer. Write a complete "
    "lesson plan for a mock lesson. Output Markdown only, in {language}, with "
    "exactly these sections as headings: learning objectives; introduction; "
    "development (main activities); summary/closing; teacher lines (what the "
    "teacher actually says, quoted); key questions to ask; expected student "
    "responses; board plan; slides (one line per slide); time allocation (a "
    "table that adds up to the lesson length); assessment (criteria and how "
    "they are observed). Be concrete and usable in a real classroom."
)


def plan_prompt(request: str, minutes: int) -> str:
    return (
        f"Lesson length: {minutes} minutes.\n"
        f"The teacher's request (subject, grade, topic): {request}\n"
        "Write the lesson plan now."
    )


SUMMARY_SYSTEM = (
    "You are a co-teacher listening to a live lesson. The transcript below "
    "mixes the teacher and the students; there is no speaker label. Produce a "
    "SHORT summary for the classroom screen in {language}, Markdown, with "
    "these headings and 1-4 bullets each: common opinions; differing "
    "opinions; open questions; key terms; next question candidates (2-3 "
    "questions the teacher could ask next). {music}Do not invent statements "
    "that are not in the transcript. If the transcript is empty, say so."
)


def summary_prompt(lesson: LessonSession) -> str:
    return (
        f"Topic: {lesson.topic or '(not given)'}\n"
        f"Elapsed: {lesson.elapsed_min():.0f} min of {lesson.minutes} "
        f"(remaining {lesson.remaining_min():.0f} min).\n"
        f"Transcript:\n{lesson.transcript() or '(nothing recorded yet)'}"
    )


def music_clause(lesson: LessonSession) -> str:
    if not lesson.is_music:
        return ""
    return (
        "This is a MUSIC lesson: add a heading 'by musical element' that sorts "
        "the students' remarks under these elements (only the ones that "
        "occur), using exactly these names: "
        + ", ".join(MUSIC_PERSPECTIVES_JA if lesson.language == "ja" else MUSIC_PERSPECTIVES)
        + ". "
    )


REPORT_SYSTEM = (
    "You are a co-teacher writing the record of a lesson that just ended. "
    "Markdown, in {language}, with these headings: lesson record (what "
    "happened, in order); actual time allocation (phases with their real "
    "minutes, from the transcript timestamps); student opinions; points to "
    "improve; suggestions for next time. {music}Base everything on the "
    "transcript; do not invent events."
)


def report_prompt(lesson: LessonSession) -> str:
    return (
        f"Topic: {lesson.topic or '(not given)'}\n"
        f"Planned length: {lesson.minutes} min; actual: {lesson.elapsed_min():.0f} min.\n"
        f"Transcript:\n{lesson.transcript(limit=MAX_TRANSCRIPT_CHARS * 2) or '(empty)'}"
    )


def save(stamp: str, suffix: str, content: str) -> Path:
    path = lessons_dir() / f"{stamp}-{suffix}.md"
    path.write_text(content, encoding="utf-8")
    return path


__all__ = [
    "DEFAULT_MINUTES",
    "LessonSession",
    "MUSIC_PERSPECTIVES",
    "PLAN_SYSTEM",
    "REPORT_SYSTEM",
    "SUMMARY_SYSTEM",
    "lessons_dir",
    "music_clause",
    "plan_prompt",
    "report_prompt",
    "save",
    "summary_prompt",
    "_language_name",
]
