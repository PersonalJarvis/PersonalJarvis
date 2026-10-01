"""What the desktop pet's bell announces, and how each card is worded.

The pet shows a notification card (``OrbOverlay.push_notice``) when something
happens that the user is likely waiting for while they look elsewhere:

* a coding agent in an Agentic IDE pane finished, asks a question, exited or
  could not start (``jarvis.agentic_ide.notifications`` entries),
* a background Jarvis-Agent task finished or failed,
* a mission ended (approved, failed or timed out — a cancel was the user's own
  doing and is not news),
* a coding result for a Jarvis conversation is ready,
* a tool call waits for the user's approval (except approvals a chat surface
  answers on its own cards — the user is already looking at those),
* a scheduled workflow (routine) finished or failed,
* an error nothing recovers from.

Every function here is pure: it turns one event into ``(kind, title, detail)``
or ``None`` (not worth a card). ``kind`` is the card's icon — ``done``,
``attention``, ``error`` or ``info``. Titles are fixed labels per interface
language; every supported locale has its own entry and none is a fallback for
another.
"""

from __future__ import annotations

from typing import Any

from jarvis.ui.pets.status_line import condense

#: One card: its icon kind, bold title and muted detail line.
Notice = tuple[str, str, str]

NOTICE_TITLE_CHARS = 60
NOTICE_DETAIL_CHARS = 110

NOTICE_LABELS: dict[str, dict[str, str]] = {
    "agent_finished": {
        "en": "{name} finished",
        "de": "{name} ist fertig",  # i18n-allow
        "es": "{name} terminó",  # i18n-allow
    },
    "agent_asks": {
        "en": "{name} needs your answer",
        "de": "{name} wartet auf dich",  # i18n-allow
        "es": "{name} necesita tu respuesta",  # i18n-allow
    },
    "agent_exited": {
        "en": "{name} exited",
        "de": "{name} wurde beendet",  # i18n-allow
        "es": "{name} se cerró",  # i18n-allow
    },
    "agent_failed": {
        "en": "{name} could not start",
        "de": "{name} konnte nicht starten",  # i18n-allow
        "es": "{name} no pudo iniciarse",  # i18n-allow
    },
    "task_done": {
        "en": "Background task done",
        "de": "Hintergrundaufgabe erledigt",  # i18n-allow
        "es": "Tarea en segundo plano lista",  # i18n-allow
    },
    "task_failed": {
        "en": "Background task failed",
        "de": "Hintergrundaufgabe fehlgeschlagen",  # i18n-allow
        "es": "La tarea en segundo plano falló",  # i18n-allow
    },
    "mission_done": {
        "en": "Mission complete",
        "de": "Mission abgeschlossen",  # i18n-allow
        "es": "Misión completada",  # i18n-allow
    },
    "mission_failed": {
        "en": "Mission failed",
        "de": "Mission fehlgeschlagen",  # i18n-allow
        "es": "La misión falló",  # i18n-allow
    },
    "mission_timed_out": {
        "en": "Mission timed out",
        "de": "Mission abgelaufen",  # i18n-allow
        "es": "La misión agotó el tiempo",  # i18n-allow
    },
    "result_ready": {
        "en": "{name} has a result",
        "de": "Ergebnis von {name} ist da",  # i18n-allow
        "es": "{name} tiene un resultado",  # i18n-allow
    },
    "approval": {
        "en": "Approval needed",
        "de": "Freigabe nötig",  # i18n-allow
        "es": "Se necesita aprobación",  # i18n-allow
    },
    "routine_done": {
        "en": "Routine finished",
        "de": "Routine erledigt",  # i18n-allow
        "es": "Rutina terminada",  # i18n-allow
    },
    "routine_failed": {
        "en": "Routine failed",
        "de": "Routine fehlgeschlagen",  # i18n-allow
        "es": "La rutina falló",  # i18n-allow
    },
    "error": {
        "en": "Something went wrong",
        "de": "Etwas ist schiefgelaufen",  # i18n-allow
        "es": "Algo salió mal",  # i18n-allow
    },
    "agent": {
        "en": "Agent",
        "de": "Agent",  # i18n-allow
        "es": "Agente",  # i18n-allow
    },
}


def label(key: str, language: str, **values: str) -> str:
    """One title in ``language`` (English when the locale is unknown)."""
    labels = NOTICE_LABELS[key]
    text = labels.get(language, labels["en"])
    return text.format(**values) if values else text


def _title(text: str) -> str:
    return condense(text, max_chars=NOTICE_TITLE_CHARS)


def _detail(*parts: object) -> str:
    joined = " · ".join(str(p).strip() for p in parts if str(p or "").strip())
    return condense(joined, max_chars=NOTICE_DETAIL_CHARS)


def _name(value: object, language: str) -> str:
    name = " ".join(str(value or "").split())
    return name or label("agent", language)


def for_pane_entry(entry: Any, language: str) -> Notice | None:
    """An Agentic IDE notification (``agentic_ide.notifications.Notification``)."""
    kind = str(getattr(entry, "kind", "") or "")
    name = _name(getattr(entry, "display_name", "") or getattr(entry, "agent", "") or "", language)
    pane = str(getattr(entry, "pane", "") or "")
    detail = _detail(pane, getattr(entry, "detail", ""))
    if kind == "completed":
        return "done", _title(label("agent_finished", language, name=name)), detail
    if kind == "needs_input":
        return "attention", _title(label("agent_asks", language, name=name)), detail
    if kind == "failed":
        return "error", _title(label("agent_failed", language, name=name)), detail
    if kind == "exited":
        return "info", _title(label("agent_exited", language, name=name)), detail
    return None


def for_background_task(event: Any, language: str) -> Notice:
    """``JarvisAgentBackgroundCompleted``."""
    if bool(getattr(event, "success", False)):
        detail = _detail(getattr(event, "summary", "") or getattr(event, "utterance", ""))
        return "done", label("task_done", language), detail
    detail = _detail(getattr(event, "error", "") or getattr(event, "utterance", ""))
    return "error", label("task_failed", language), detail


def for_mission(event: Any, language: str) -> Notice | None:
    """``MissionCompleted`` — a cancel is the user's own act and stays quiet."""
    status = str(getattr(event, "status", "") or "")
    summary = getattr(event, "summary_de" if language == "de" else "summary_en", "") or ""
    summary = summary or getattr(event, "summary_en", "") or ""
    if status == "approved":
        return "done", label("mission_done", language), _detail(summary)
    if status == "failed":
        return (
            "error",
            label("mission_failed", language),
            _detail(getattr(event, "reason", "") or summary),
        )
    if status == "timed_out":
        return "error", label("mission_timed_out", language), _detail(summary)
    return None


def for_delegation_result(event: Any, language: str) -> Notice | None:
    """``DelegationResultReady`` — a coding result addressed to a conversation."""
    name = _name(getattr(event, "agent_name", ""), language)
    status = str(getattr(event, "status", "") or "").lower()
    text = getattr(event, "text", "") or getattr(event, "report", "")
    if status in ("failed", "error"):
        return "error", _title(label("agent_failed", language, name=name)), _detail(text)
    return "done", _title(label("result_ready", language, name=name)), _detail(text)


def for_approval(event: Any, language: str) -> Notice | None:
    """``ActionApprovalRequired`` — skipped when a chat answers its own cards."""
    if getattr(event, "approval_ref", None):
        return None
    tool = str(getattr(event, "tool_name", "") or "").split("__")[-1]
    tool = tool.replace("_", " ").replace("-", " ").strip()
    tool = tool[:1].upper() + tool[1:]
    return (
        "attention",
        label("approval", language),
        _detail(tool, getattr(event, "args_preview", "")),
    )


def for_workflow(event: Any, language: str, name: str = "") -> Notice:
    """``WorkflowCompleted`` — ``name`` is the routine's display name if known."""
    if bool(getattr(event, "success", False)):
        return "done", label("routine_done", language), _detail(name)
    return "error", label("routine_failed", language), _detail(name, getattr(event, "error", ""))


def for_error(event: Any, language: str) -> Notice | None:
    """``ErrorOccurred`` — only one nothing recovers from."""
    if bool(getattr(event, "recoverable", True)):
        return None
    return "error", label("error", language), _detail(getattr(event, "message", ""))


__all__ = [
    "NOTICE_LABELS",
    "Notice",
    "for_approval",
    "for_background_task",
    "for_delegation_result",
    "for_error",
    "for_mission",
    "for_pane_entry",
    "for_workflow",
    "label",
]
