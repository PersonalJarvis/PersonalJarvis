---
schema_version: "1"
name: plugin-google_calendar
description: Read and manage events in the user's Google Calendar.
when_to_use: Use when the user mentions Google Calendar or wants to check, create, move, or delete calendar events or appointments.
category: productivity
plugin_id: google_calendar
intent_verbs: [zeig, lies, such, erstell, plan, verschieb, lösch]  # i18n-allow
intent_objects: [kalender, google-kalender, google-calendar, gcal, termin, termine, meeting, ereignis]  # i18n-allow
triggers:
  # Word boundaries are load-bearing, not cosmetic. Without them "termin" fired
  # inside "TERMINals", so every sentence about the coding workspace pulled a
  # calendar skill into the turn (live 2026-07-29 17:04, band=fire, score 1.0,
  # on "Coding Terminals" — BUG-121). `termin(?!al)` keeps Termin / Termine /
  # Terminen while refusing Terminal / Terminals.
  - type: voice
    pattern: "(google.?(kalender|calendar)|gcal|(in )?(meine[nm]? )?\\b(kalender|termin(?!al)\\w*|meeting)s?\\b)"  # i18n-allow
requires_tools: [google_calendar]
risk_policy:
  default_tier: monitor
---

Use the connected Google Calendar tools to read and manage the user's events.

- Check the schedule before creating; reference an event by title plus date/time.
- Writes (create / update / delete) run without a confirmation prompt by design —
  reads are safe, writes are monitored and audited.
- Summarize plainly: title, date and time, attendees, location.
