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
  - type: voice
    pattern: "(google.?(kalender|calendar)|gcal|(in )?(meine[nm]? )?(kalender|termin|meeting))"  # i18n-allow
requires_tools: [google_calendar]
risk_policy:
  default_tier: monitor
---

Use the connected Google Calendar tools to read and manage the user's events.

- Check the schedule before creating; reference an event by title plus date/time.
- Writes (create / update / delete) run without a confirmation prompt by design —
  reads are safe, writes are monitored and audited.
- Summarize plainly: title, date and time, attendees, location.
