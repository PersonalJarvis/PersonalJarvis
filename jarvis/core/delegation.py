"""Correlated delegation results, without a background model or provider call."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

from jarvis.core.events import AnnouncementRequested
from jarvis.core.protocols import current_chat_turn
from jarvis.core.turn_language import DEFAULT_LOCALE

RESULT_SOURCES = frozenset({"society.lead", "agentic_ide.readback"})
BATCH_SOURCE = "delegation.batch"
BATCH_WINDOW_S = 0.75
MAX_BATCH = 4
current_delegation_origin: ContextVar[dict[str, str] | None] = ContextVar(
    "current_delegation_origin", default=None,
)


def origin_metadata(*, language: str = "") -> dict[str, str]:
    """Capture the actual caller before a background task outlives its turn.

    An internal/scheduled chat cannot promote itself into a spoken user task.
    The metadata is supplied by the application, never a tool argument.
    """
    turn = current_chat_turn.get()
    return {
        "reply_surface": "voice" if turn is None else ("chat" if turn.direct_user else "none"),
        "reply_session_id": turn.session_id if turn is not None else "",
        "lang": language or DEFAULT_LOCALE,
    }


def bounded(text: str, limit: int = 4000) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    half = (limit - 5) // 2
    return text[:half] + "\n[…]\n" + text[-half:]


def result_announcement(
    *, source: str, request_id: str, name: str, request: str,
    status: str, report: str, language: str = "", evidence: str = "agent_report",
) -> AnnouncementRequested:
    """Keep provenance, the original request and the outcome together.

    The live conversation does the synthesis. Classic speech gets a short,
    deterministic excerpt; neither path needs a separate paid composer.
    """
    lang = language or DEFAULT_LOCALE
    labels = {
        "en": {"done": "reports", "completed": "has finished", "stopped": "was interrupted",
               "needs_input": "needs your input",
               "blocked": "is blocked", "failed": "failed", "exited": "has exited"},
        "de": {  # i18n-allow: deterministic spoken fallback
            "done": "meldet", "completed": "ist fertig", "stopped": "wurde unterbrochen",  # i18n-allow
            "needs_input": "braucht deine Antwort",  # i18n-allow
            "blocked": "ist blockiert", "failed": "ist fehlgeschlagen",  # i18n-allow
            "exited": "wurde beendet",  # i18n-allow
        },
        "es": {
            "done": "informa", "completed": "ha finalizado", "stopped": "fue interrumpido",
            "needs_input": "necesita tu respuesta", "blocked": "está bloqueado",
            "failed": "ha fallado", "exited": "ha terminado",
        },
    }
    table = labels.get(lang, labels["en"])
    label = table.get(status, table["done"])
    excerpt = " ".join(str(report or "").split()[:45])
    text = f"{name} {label}" + (f": {excerpt}" if excerpt else ".")
    material = json.dumps({
        "task_id": request_id, "agent": name, "status": status, "evidence": evidence,
        "request": bounded(request, 1800), "report": bounded(report),
    }, ensure_ascii=False)
    digest = hashlib.sha256(material.encode()).hexdigest()[:16]
    return AnnouncementRequested(
        trace_id=uuid5(NAMESPACE_URL, f"jarvis:delegation:{request_id}"),
        source_layer=source, kind="completion", priority="normal", language=lang,
        text=text,
        detail=f"request={request_id} status={status} result={digest}",
        report=material,
    )


class ResultInbox:
    """Coalesce distinct completed tasks; never wait for unfinished siblings."""

    def __init__(self) -> None:
        self.pending: OrderedDict[tuple, AnnouncementRequested] = OrderedDict()
        self.seen: OrderedDict[tuple, None] = OrderedDict()

    def add(self, event: AnnouncementRequested) -> bool:
        key = (event.source_layer, event.trace_id, event.detail)
        if key in self.seen or key in self.pending:
            return False
        self.pending[key] = event
        self.seen[key] = None
        while len(self.seen) > 2048:
            self.seen.popitem(last=False)
        return True

    def take(self) -> AnnouncementRequested | None:
        if not self.pending:
            return None
        events = [
            self.pending.popitem(last=False)[1]
            for _ in range(min(MAX_BATCH, len(self.pending)))
        ]
        # Allocate each report its own budget so clipping cannot erase a middle task.
        report = "\n\n".join(
            f"Result {i + 1}:\n{bounded(event.report or event.text, 5500 // len(events))}"
            for i, event in enumerate(events)
        )
        return replace(
            events[0], source_layer=BATCH_SOURCE,
            text=" ".join(event.text for event in events), report=report,
            detail="; ".join(event.detail or "" for event in events),
        )
