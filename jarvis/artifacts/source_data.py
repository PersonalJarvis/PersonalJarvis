"""The facts an artifact about the user's OWN data is built from — fetched by
the harness, never left to the worker to imagine.

The worker that writes an artifact runs in an empty worktree on an external
CLI. It has no Gmail, no calendar, no contacts: when the brief asks for "a
morning briefing from my calendar and my important mail" and carries no
data, the strongest model on the box does what every model does with a
confident brief and no facts — it writes a page full of plausible people,
subjects and appointments (forensic 2026-08-27, mission 01a0426e-8d79: a
"Dr. Markus Weber", a "NovaTech SLA", a "Sarah Keller" — none of them real).

So the facts are gathered HERE, on the app side, before the brief is
composed, the way the brain manager pre-fetches the activity timeline rather
than trusting a fast model to call the tool: deterministic, harness-side, and
through the ``ToolExecutor`` (never a direct ``Tool.execute`` — AP-3), so the
risk-tier and audit path are honoured. A read that the plugin cannot serve —
not connected, expired token, timeout — becomes an *unavailable* section the
brief carries verbatim, so the page says "Gmail is not connected" instead of
inventing an inbox.

Two halves:

* :func:`plan_source_data` — pure, regex-only (AP-11): which sources the
  request names. The vocabulary is input-matching (DE/EN/ES), which the
  language policy allows on the input surface.
* :func:`fetch_source_data` — the reads, bounded in count, time and size, and
  :func:`render_source_data`, the brief section the worker reads.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceNeed:
    """One kind of the user's own data an artifact request names."""

    key: str
    """Stable id: ``inbox`` / ``calendar``."""
    tool: str
    """The router tool that serves it (``gmail`` / ``google_calendar``)."""
    label: str
    """How the brief names it."""


INBOX: Final = SourceNeed("inbox", "gmail", "Gmail inbox")
CALENDAR: Final = SourceNeed("calendar", "google_calendar", "Google Calendar")

#: Every source the planner knows, in the order the brief lists them.
SOURCES: Final[tuple[SourceNeed, ...]] = (CALENDAR, INBOX)

# Input-matching vocabulary (DE/EN/ES) — nouns for the user's mail and
# calendar. Deliberately nouns only: "mail me the page" is not a request for
# inbox data, and an over-trigger would hand the user's mail to a build about
# something else.
_INBOX_RE: Final = re.compile(
    r"\b(?:gmail|e-?mails?|mails?|inbox|posteingang|postfach|"  # i18n-allow: input vocab
    r"correos?(?:\s+electr[oó]nicos?)?|bandeja\s+de\s+entrada)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)
_CALENDAR_RE: Final = re.compile(
    r"\b(?:kalender|calendar|calendario|termine?n?|meetings?|agenda|"  # i18n-allow: input vocab
    r"citas?|reuni[oó]n(?:es)?|besprechung(?:en)?)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)
# A request that asks for a SAMPLE is the one case where made-up items are
# wanted — and then the brief tells the worker to label them as such.
_SAMPLE_RE: Final = re.compile(
    r"\b(?:sample|demo|mock-?ups?|dummy|fake|fictional|placeholders?|templates?|"
    r"beispiel\w*|platzhalter|vorlage|"  # i18n-allow: input vocab
    r"ejemplos?|ficticio\w*|plantilla)\b",  # i18n-allow: input vocab
    re.IGNORECASE,
)

_NEED_PATTERNS: Final[tuple[tuple[SourceNeed, re.Pattern[str]], ...]] = (
    (CALENDAR, _CALENDAR_RE),
    (INBOX, _INBOX_RE),
)


def plan_source_data(text: str) -> tuple[SourceNeed, ...]:
    """The sources a request names, in :data:`SOURCES` order; empty when none."""
    haystack = text or ""
    return tuple(need for need, pattern in _NEED_PATTERNS if pattern.search(haystack))


def wants_sample_data(text: str) -> bool:
    """True when the request explicitly asks for sample / demo / template content."""
    return bool(_SAMPLE_RE.search(text or ""))


# --- Fetching --------------------------------------------------------------------

#: Mail newer than this many days is what a briefing is about.
INBOX_WINDOW_DAYS: Final = 2
#: Messages read in full (list → get per id); the brief stays a brief.
INBOX_MAX_MESSAGES: Final = 12
#: Body text kept per message, after the plugin's own cap.
INBOX_BODY_CHARS: Final = 400
#: Calendar window: today and tomorrow, from local midnight.
CALENDAR_WINDOW_DAYS: Final = 2
CALENDAR_MAX_EVENTS: Final = 30
#: A plugin that does not answer within this stays "unavailable" — the build
#: must not hang on a dead token refresh.
PER_SOURCE_TIMEOUT_S: Final = 45.0


@dataclass(frozen=True)
class SourceSection:
    """What one source yielded — or why it yielded nothing."""

    need: SourceNeed
    status: str
    """``ok`` (items follow), ``empty`` (read fine, nothing there), ``unavailable``."""
    items: tuple[dict[str, Any], ...] = ()
    note: str = ""
    """The window that was read, or why the read failed — shown on the page."""


@dataclass(frozen=True)
class SourceData:
    sections: tuple[SourceSection, ...] = field(default_factory=tuple)
    fetched_at: datetime | None = None

    @property
    def needs(self) -> tuple[SourceNeed, ...]:
        return tuple(s.need for s in self.sections)


def unavailable_source_data(
    needs: Sequence[SourceNeed], *, reason: str = "this build had no access to the user's accounts"
) -> SourceData:
    """Every named source marked unavailable — the honest brief when nothing
    could be fetched (no executor wired, no tools built)."""
    return SourceData(
        sections=tuple(SourceSection(need, "unavailable", note=reason) for need in needs),
    )


async def fetch_source_data(
    needs: Sequence[SourceNeed],
    *,
    tools: Mapping[str, Any],
    executor: Any,
    trace_id: UUID,
    utterance: str,
    now: datetime,
) -> SourceData:
    """Read every named source through *executor* (a ``ToolExecutor``).

    Never raises: a missing tool, a failed read, a timeout or a crash becomes
    an ``unavailable`` section with the reason. Sources are read one after the
    other — two OAuth-backed plugins refreshing tokens at once is how a token
    store gets clobbered.
    """
    sections: list[SourceSection] = []
    for need in needs:
        tool = tools.get(need.tool)
        if tool is None:
            sections.append(
                SourceSection(
                    need, "unavailable", note=f"the {need.label} plugin is not connected"
                )
            )
            continue
        try:
            section = await asyncio.wait_for(
                _fetch_one(need, tool, executor, trace_id=trace_id, utterance=utterance, now=now),
                timeout=PER_SOURCE_TIMEOUT_S,
            )
        except TimeoutError:
            log.warning(
                "artifact source data: %s did not answer in %.0fs",
                need.tool,
                PER_SOURCE_TIMEOUT_S,
            )
            section = SourceSection(
                need,
                "unavailable",
                note=f"the {need.label} plugin did not answer within {PER_SOURCE_TIMEOUT_S:.0f} s",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — one dead source must not sink the build
            log.warning(
                "artifact source data: %s read crashed: %s", need.tool, exc, exc_info=True
            )
            section = SourceSection(
                need,
                "unavailable",
                note=f"reading {need.label} failed: {type(exc).__name__}: {exc}",
            )
        sections.append(section)
    return SourceData(sections=tuple(sections), fetched_at=now)


async def _run(
    executor: Any, tool: Any, args: dict[str, Any], *, trace_id: UUID, utterance: str
) -> Any:
    return await executor.execute(
        tool,
        args,
        user_utterance=utterance,
        trace_id=trace_id,
        rationale="facts for an artifact about the user's own data",
    )


def _failed(result: Any) -> str | None:
    """The error text of a failed ToolResult, or None when it succeeded."""
    if result is None:
        return "no result"
    if bool(getattr(result, "success", False)):
        return None
    return str(getattr(result, "error", "") or "the read failed")


async def _fetch_one(
    need: SourceNeed,
    tool: Any,
    executor: Any,
    *,
    trace_id: UUID,
    utterance: str,
    now: datetime,
) -> SourceSection:
    if need.key == "inbox":
        return await _fetch_inbox(need, tool, executor, trace_id=trace_id, utterance=utterance)
    if need.key == "calendar":
        return await _fetch_calendar(
            need, tool, executor, trace_id=trace_id, utterance=utterance, now=now
        )
    return SourceSection(need, "unavailable", note=f"no reader for {need.key}")


async def _fetch_inbox(
    need: SourceNeed, tool: Any, executor: Any, *, trace_id: UUID, utterance: str
) -> SourceSection:
    window = f"the last {INBOX_WINDOW_DAYS} days"
    listing = await _run(
        executor,
        tool,
        {
            "action": "list_messages",
            "query": f"newer_than:{INBOX_WINDOW_DAYS}d",
            "max_results": INBOX_MAX_MESSAGES,
        },
        trace_id=trace_id,
        utterance=utterance,
    )
    error = _failed(listing)
    if error is not None:
        return SourceSection(need, "unavailable", note=f"reading {need.label} failed: {error}")
    out = getattr(listing, "output", None)
    messages = out.get("messages") if isinstance(out, dict) else None
    ids = [
        str(m["id"])
        for m in (messages or [])
        if isinstance(m, dict) and m.get("id")
    ][:INBOX_MAX_MESSAGES]
    if not ids:
        return SourceSection(need, "empty", note=f"no mail arrived in {window}")
    items: list[dict[str, Any]] = []
    failures = 0
    for message_id in ids:
        got = await _run(
            executor,
            tool,
            {"action": "get_message", "message_id": message_id},
            trace_id=trace_id,
            utterance=utterance,
        )
        if _failed(got) is not None or not isinstance(getattr(got, "output", None), dict):
            failures += 1
            continue
        items.append(_slim_message(got.output))
    if not items:
        return SourceSection(
            need, "unavailable", note=f"{failures} of {len(ids)} messages could not be read"
        )
    note = f"{len(items)} messages from {window}"
    if failures:
        note += f" ({failures} more could not be read)"
    return SourceSection(need, "ok", items=tuple(items), note=note)


def _slim_message(raw: Mapping[str, Any]) -> dict[str, Any]:
    body = str(raw.get("body") or "")
    if len(body) > INBOX_BODY_CHARS:
        body = body[:INBOX_BODY_CHARS].rstrip() + "…"
    labels = raw.get("labelIds") or []
    return {
        "from": str(raw.get("from") or ""),
        "to": str(raw.get("to") or ""),
        "subject": str(raw.get("subject") or ""),
        "date": str(raw.get("date") or ""),
        "unread": "UNREAD" in labels if isinstance(labels, (list, tuple)) else False,
        "snippet": str(raw.get("snippet") or ""),
        "body": body,
    }


async def _fetch_calendar(
    need: SourceNeed,
    tool: Any,
    executor: Any,
    *,
    trace_id: UUID,
    utterance: str,
    now: datetime,
) -> SourceSection:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=CALENDAR_WINDOW_DAYS)
    window = f"{start.date().isoformat()} to {(end - timedelta(days=1)).date().isoformat()}"
    result = await _run(
        executor,
        tool,
        {
            "action": "list_events",
            "time_min": start.isoformat(),
            "time_max": end.isoformat(),
            "max_results": CALENDAR_MAX_EVENTS,
        },
        trace_id=trace_id,
        utterance=utterance,
    )
    error = _failed(result)
    if error is not None:
        return SourceSection(need, "unavailable", note=f"reading {need.label} failed: {error}")
    out = getattr(result, "output", None)
    events = out.get("events") if isinstance(out, dict) else None
    items = tuple(e for e in (events or []) if isinstance(e, dict))[:CALENDAR_MAX_EVENTS]
    if not items:
        return SourceSection(need, "empty", note=f"no events between {window}")
    return SourceSection(need, "ok", items=items, note=f"{len(items)} events between {window}")


# --- The brief section ------------------------------------------------------------

_STATUS_WORD: Final[dict[str, str]] = {
    "ok": "available",
    "empty": "nothing there",
    "unavailable": "UNAVAILABLE",
}


def render_source_data(data: SourceData) -> str:
    """The ``## Source data`` section of the brief — the only facts the page
    may show about the user's own data. Empty string when nothing was named."""
    if not data.sections:
        return ""
    when = (
        f"at {data.fetched_at.isoformat(timespec='minutes')}"
        if data.fetched_at is not None
        else "for this build"
    )
    lines: list[str] = [
        "## Source data — the only facts about the user's own data",
        f"Jarvis read the user's connected accounts {when}. The page shows THESE "
        "items and nothing else for the sections they cover. A section marked "
        "'nothing there' or 'UNAVAILABLE' stays empty on the page and shows its "
        "note in one plain sentence — never a made-up item in its place.",
    ]
    for section in data.sections:
        lines.append("")
        status = _STATUS_WORD.get(section.status, section.status)
        lines.append(f"### {section.need.label} — {status}")
        if section.note:
            lines.append(section.note)
        if section.status == "ok" and section.items:
            lines.append("```json")
            lines.append(json.dumps(list(section.items), ensure_ascii=False, indent=1))
            lines.append("```")
    return "\n".join(lines)


__all__ = [
    "CALENDAR",
    "INBOX",
    "SOURCES",
    "SourceData",
    "SourceNeed",
    "SourceSection",
    "fetch_source_data",
    "plan_source_data",
    "render_source_data",
    "unavailable_source_data",
    "wants_sample_data",
]
