"""Which finished things the desktop pet confirms with a card, and how.

The pet's done cards are about Jarvis and nothing else. A card appears when:

* a typed chat with Jarvis finished a turn (``JarvisChatTurnFinished``) — the
  card is what was asked over the start of Jarvis's answer;
* a task Jarvis itself started finished: a background Jarvis-Agent task
  (``JarvisAgentBackgroundCompleted``) or a coding job Jarvis delegated to an
  agent (``DelegationResultReady``).

A coding agent the user runs on their own, a mission, a routine or an error
somewhere in the app is not Jarvis answering, and gets no card. Spoken turns
get none either: the answer was heard, and echoing it reads as noise.

Every function is pure: one event in, ``(kind, title, detail)`` or ``None``
out. ``kind`` is ``done`` (a check) or ``error`` (a cross).
"""

from __future__ import annotations

from typing import Any

from jarvis.ui.pets.status_line import condense

#: One card: its icon kind, bold title and the answer under it.
Notice = tuple[str, str, str]

NOTICE_TITLE_CHARS = 70
NOTICE_DETAIL_CHARS = 160

NOTICE_LABELS: dict[str, dict[str, str]] = {
    "task_done": {
        "en": "Task done",
        "de": "Aufgabe erledigt",  # i18n-allow
        "es": "Tarea hecha",  # i18n-allow
        "zh": "任务已完成",  # i18n-allow
    },
    "task_failed": {
        "en": "Task failed",
        "de": "Aufgabe fehlgeschlagen",  # i18n-allow
        "es": "La tarea falló",  # i18n-allow
        "zh": "任务失败",  # i18n-allow
    },
    "result_ready": {
        "en": "{name} is done",
        "de": "{name} ist fertig",  # i18n-allow
        "es": "{name} terminó",  # i18n-allow
        "zh": "{name} 已完成",  # i18n-allow
    },
    "result_failed": {
        "en": "{name} failed",
        "de": "{name} ist gescheitert",  # i18n-allow
        "es": "{name} falló",  # i18n-allow
        "zh": "{name} 失败了",  # i18n-allow
    },
    "agent": {
        "en": "The agent",
        "de": "Der Agent",  # i18n-allow
        "es": "El agente",  # i18n-allow
        "zh": "智能体",  # i18n-allow
    },
    "no_answer": {
        "en": "Jarvis could not answer",
        "de": "Jarvis konnte nicht antworten",  # i18n-allow
        "es": "Jarvis no pudo responder",  # i18n-allow
        "zh": "Jarvis 无法回答",  # i18n-allow
    },
}


def label(key: str, language: str, **values: str) -> str:
    """One label in ``language`` (English when the locale is unknown)."""
    labels = NOTICE_LABELS[key]
    text = labels.get(language, labels["en"])
    return text.format(**values) if values else text


def _title(text: object) -> str:
    return condense(str(text or ""), max_chars=NOTICE_TITLE_CHARS)


def _detail(text: object) -> str:
    return condense(str(text or ""), max_chars=NOTICE_DETAIL_CHARS)


def for_chat_turn(event: Any, language: str) -> Notice | None:
    """``JarvisChatTurnFinished``: the question over the start of the answer."""
    asked = _title(getattr(event, "user_text", ""))
    reply = _detail(getattr(event, "reply_text", ""))
    if str(getattr(event, "status", "done")) == "error":
        return "error", asked or label("no_answer", language), reply
    if not (asked or reply):
        return None
    return "done", asked or reply, reply if asked else ""


def for_background_task(event: Any, language: str) -> Notice:
    """``JarvisAgentBackgroundCompleted``: what was asked over the result."""
    asked = getattr(event, "utterance", "")
    success = bool(getattr(event, "success", False))
    title = _title(asked) or label("task_done" if success else "task_failed", language)
    if success:
        return "done", title, _detail(getattr(event, "summary", ""))
    return "error", title, _detail(getattr(event, "error", "") or getattr(event, "summary", ""))


def for_delegation_result(event: Any, language: str) -> Notice:
    """``DelegationResultReady``: a coding job Jarvis handed to an agent came back."""
    name = " ".join(str(getattr(event, "agent_name", "") or "").split()) or label("agent", language)
    text = _detail(getattr(event, "text", "") or getattr(event, "report", ""))
    status = str(getattr(event, "status", "") or "").lower()
    if status in ("failed", "error"):
        return "error", _title(label("result_failed", language, name=name)), text
    return "done", _title(label("result_ready", language, name=name)), text


__all__ = [
    "NOTICE_LABELS",
    "Notice",
    "for_background_task",
    "for_chat_turn",
    "for_delegation_result",
    "label",
]
