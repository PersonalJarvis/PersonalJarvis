"""Google Calendar pull adapter — every event on every calendar of the account.

Of everything Calendar is, this pulls what belongs in a *memory*: each event's
own text — what it was called, when it happened, where, who organised it, who
was invited, and what the description said. The walk covers EVERY calendar the
account subscribes to (``calendarList``), in a deterministic order (sorted by
calendar id), and each calendar's events oldest to newest
(``singleEvents=true`` + ``orderBy=startTime``), so an interrupted backfill
resumes at the calendar it stopped in.

Scope honesty — what the token allows vs. what is imported:

- **Recurring events are imported as their expanded instances**, which is the
  only form the API can order by start time. Expansion needs a time ceiling:
  the walk covers everything from 1970 up to TWO YEARS ahead of the sync.
  Instances of a never-ending series beyond that horizon are structurally out
  of reach for a bounded import; the horizon rolls forward with every sync,
  so they arrive as time does.
- **Cancelled events are skipped** — a tombstone carries no text to remember.
- **Attachments and conference links are not imported**; the event's
  ``htmlLink`` permalink leads to them.
- **Incremental rides the ``updated`` cursor** via ``metadata["mtime_ns"]``
  (the key the sync runner already advances): a numeric checkpoint becomes
  ``updatedMin`` one day earlier, so a boundary can never skip an event.
  The API's native incremental channel is ``syncToken``, which is finer but
  is an opaque per-calendar token the bridge's single numeric cursor cannot
  hold — the documented fallback is exactly this ``updatedMin`` narrowing,
  and a full backfill remains cheap because event pages carry complete
  event data (no per-item fetch).

Credentials come from the marketplace token the user connected in the Plugins
store — this adapter never asks for a second one and never logs the token.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from jarvis.ultrawiki.adapters import _google as g
from jarvis.ultrawiki.types import ConnectorContext, RawItem

log = logging.getLogger(__name__)

__all__ = [
    "INTEGRATION_ID",
    "GoogleAdapterError",
    "google_calendar_pull_adapter",
    "item_from_event",
]

#: Re-exported so callers can catch the family's error type from this module.
GoogleAdapterError = g.GoogleAdapterError

#: The plugin-bridge candidate id this adapter serves.
INTEGRATION_ID = "plugin:google_calendar"

_PLUGIN_ID = "google_calendar"
_PRODUCT = "Google Calendar"
_API = "https://www.googleapis.com/calendar/v3"

#: Everything since the epoch — "far past" without inventing a start of time.
_TIME_MIN = "1970-01-01T00:00:00Z"

#: Expansion ceiling for recurring events (see the module docstring).
_LOOKAHEAD_DAYS = 730

#: The API's own page maxima.
_CALENDAR_PAGE_SIZE = 250
_EVENT_PAGE_SIZE = 2500

#: A large meeting can carry hundreds of attendees; the record names this
#: many and counts the rest — a memory needs "who", not a mailing list dump.
_ATTENDEE_DISPLAY_CAP = 25


def _when(edge: Any) -> str:
    """An event boundary as ISO-8601 UTC-compatible text; empty when absent.

    Timed events carry ``dateTime`` (with offset — kept as-is, the contract
    allows offsets); all-day events carry only ``date`` and are normalised to
    midnight UTC so the timestamp stays machine-comparable.
    """
    if not isinstance(edge, dict):
        return ""
    stamp = str(edge.get("dateTime") or "").strip()
    if stamp:
        return stamp
    day = str(edge.get("date") or "").strip()
    return f"{day}T00:00:00Z" if day else ""


def _attendee_line(event: dict[str, Any]) -> str:
    attendees = [row for row in event.get("attendees") or [] if isinstance(row, dict)]
    if not attendees:
        return ""
    names = [
        str(row.get("displayName") or row.get("email") or "").strip()
        for row in attendees
    ]
    names = [name for name in names if name]
    if not names:
        return ""
    shown = names[:_ATTENDEE_DISPLAY_CAP]
    rest = len(names) - len(shown)
    line = f"Attendees ({len(names)}): {', '.join(shown)}"
    if rest > 0:
        line += f" (+{rest} more)"
    return line


def item_from_event(
    calendar_id: str, calendar_label: str, event: dict[str, Any]
) -> RawItem | None:
    """One event as a ``RawItem``; ``None`` when it is cancelled or unusable.

    The body is composed rather than copied raw: a bare description loses
    which calendar the event lived on, when it ran, and who was there — and
    those are exactly what a later question ("when did I meet Ada?") turns on.
    """
    if str(event.get("status") or "") == "cancelled":
        return None
    event_id = str(event.get("id") or "").strip()
    permalink = str(event.get("htmlLink") or "").strip()
    if not event_id or not permalink:
        return None

    start = _when(event.get("start"))
    end = _when(event.get("end"))
    title = str(event.get("summary") or "").strip() or "(untitled event)"
    organizer = event.get("organizer") or {}
    organizer_name = str(
        organizer.get("displayName") or organizer.get("email") or ""
    ).strip()
    location = str(event.get("location") or "").strip()
    description = str(event.get("description") or "").strip()
    if "<" in description and ">" in description:
        # Calendar descriptions may carry HTML; the memory wants readable text.
        description = g.strip_html(description)
    recurring_id = str(event.get("recurringEventId") or "").strip()
    updated = str(event.get("updated") or "")

    header = f"{calendar_label} · event · {start}" if calendar_label else f"event · {start}"
    if end:
        header += f" until {end}"
    lines = [header]
    if location:
        lines.append(f"Location: {location}")
    if organizer_name:
        lines.append(f"Organizer: {organizer_name}")
    attendee_line = _attendee_line(event)
    if attendee_line:
        lines.append(attendee_line)
    if description:
        lines.append("")
        lines.append(description)
    body, truncated = g.cap_body("\n".join(lines).strip())

    return RawItem(
        # Calendar-scoped: the same invitation appears on several calendars
        # with the SAME event id, and each copy must upsert as its own row.
        external_id=f"{calendar_id}:{event_id}",
        title=title,
        body=body,
        permalink=permalink,
        # WHEN IT HAPPENED — the event's own start, not its last edit. The
        # edit time rides in metadata to drive the cursor.
        timestamp_utc=start,
        # Instances of one recurring series thread together.
        thread_key=f"{calendar_id}:{recurring_id or event_id}",
        author_raw=organizer_name,
        metadata={
            "mtime_ns": g.to_ns(updated),
            "calendar_id": calendar_id,
            "calendar_label": calendar_label,
            "status": str(event.get("status") or ""),
            "recurring_event_id": recurring_id,
            "attendees": len(event.get("attendees") or []),
            "truncated": truncated,
        },
    )


async def _calendars(client: httpx.AsyncClient, token: str) -> list[tuple[str, str]]:
    """Every subscribed calendar as ``(id, label)``, sorted by id.

    Sorted so the cross-calendar walk order is deterministic — the resume
    convention leans on it — and does not reshuffle when the API reorders
    its listing.
    """
    rows: list[tuple[str, str]] = []
    page_token: str | None = None
    while True:
        params: dict[str, Any] = {"maxResults": _CALENDAR_PAGE_SIZE}
        if page_token:
            params["pageToken"] = page_token
        payload = await g.google_get_json(
            client, f"{_API}/users/me/calendarList", token, _PRODUCT, params=params
        )
        for row in payload.get("items") or []:
            if not isinstance(row, dict):
                continue
            calendar_id = str(row.get("id") or "").strip()
            if not calendar_id:
                continue
            label = str(row.get("summaryOverride") or row.get("summary") or "").strip()
            rows.append((calendar_id, label or calendar_id))
        page_token = str(payload.get("nextPageToken") or "") or None
        if not page_token:
            break
    rows.sort(key=lambda pair: pair[0])
    return rows


async def google_calendar_pull_adapter(
    ctx: ConnectorContext,
    checkpoint: str | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[RawItem]:
    """Yield every event on every calendar, calendar by calendar, oldest first.

    ``checkpoint`` doubles as the resume/cursor convention: a numeric value is
    the incremental cursor (nanoseconds → ``updatedMin``); anything else is
    the last imported external id (``<calendar id>:<event id>``) from an
    interrupted backfill — calendars sorted BEFORE that calendar are complete
    and are skipped, the checkpoint's own calendar is re-walked from its start
    (event pages carry full data, so re-listing one calendar is cheap and the
    already-imported rows upsert as ``unchanged``). ``transport`` is a test
    seam; production leaves it ``None``.
    """
    token = g.google_token(_PLUGIN_ID, _PRODUCT)
    cutoff = g.cursor_cutoff(checkpoint)
    resume_calendar = ""
    if checkpoint and cutoff is None and ":" in checkpoint:
        resume_calendar = checkpoint.rsplit(":", 1)[0]
    time_max = (datetime.now(UTC) + timedelta(days=_LOOKAHEAD_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    yielded = 0

    async with httpx.AsyncClient(timeout=g.TIMEOUT, transport=transport) as client:
        for calendar_id, calendar_label in await _calendars(client, token):
            if resume_calendar and calendar_id < resume_calendar:
                continue  # completed before the interruption
            page_token: str | None = None
            while True:
                params: dict[str, Any] = {
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "timeMin": _TIME_MIN,
                    "timeMax": time_max,
                    "maxResults": _EVENT_PAGE_SIZE,
                }
                if cutoff:
                    params["updatedMin"] = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
                if page_token:
                    params["pageToken"] = page_token
                payload = await g.google_get_json(
                    client,
                    f"{_API}/calendars/{quote(calendar_id, safe='')}/events",
                    token,
                    _PRODUCT,
                    params=params,
                )
                for row in payload.get("items") or []:
                    if not isinstance(row, dict):
                        continue
                    item = item_from_event(calendar_id, calendar_label, row)
                    if item is not None:
                        yielded += 1
                        yield item
                page_token = str(payload.get("nextPageToken") or "") or None
                if not page_token:
                    break

    log.info(
        "Google Calendar adapter: yielded %d item(s) for %s (%s)",
        yielded,
        ctx.source_id,
        "incremental" if cutoff else "backfill",
    )
