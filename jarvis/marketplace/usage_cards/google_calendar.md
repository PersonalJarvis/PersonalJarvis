---
plugin_id: google_calendar
keywords: termin, termine, kalender, calendar, meeting, appointment, schedule, heute, morgen, übermorgen, woche, wochenende, tagesplan, plan, vorhaben, was habe ich, was steht an, was steht drin, eintragen, eintrag, verschieben, absagen, nachhilfe, prüfung, klausur  # i18n-allow
---
Use the google_calendar tool to read and manage the user's calendar. This is a
direct, router-tier action — NEVER spawn a worker / sub-agent for a calendar
request, because the worker has no calendar access; call the google_calendar
tool yourself.

- "today"/"heute", "tomorrow"/"morgen", "this week": call list_events with
  time_min/time_max set to the user's LOCAL day/week boundaries (their timezone,
  with the offset, e.g. 2026-06-29T00:00:00+02:00), not UTC midnight.
- list_events scans ALL the user's calendars (primary + secondary like "School"),
  so do not assume a missing event means an empty day — read the returned list.
- Summarize spoken answers as time + title only, in chronological order, no IDs.
- Create / move / delete events directly (full autonomy, no confirmation); state
  plainly what you did afterwards. When updating or deleting an event that
  list_events returned with a non-primary calendar_id, pass that calendar_id back.
- If the tool says the calendar is not connected, tell the user to connect Google
  Calendar in the Plugins view — do not pretend the day is empty.
