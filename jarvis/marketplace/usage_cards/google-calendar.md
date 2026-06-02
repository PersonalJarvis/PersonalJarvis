---
plugin_id: google-calendar
keywords: termin, termine, kalender, calendar, meeting, appointment, schedule, heute, morgen
---
Use the google-calendar/* tools to read and manage the user's calendar.
- For "today"/"heute": call list_events with timeMin/timeMax set to the user's
  local day boundaries (their timezone), not UTC midnight.
- Summarize: time + title only, chronological, no IDs.
- Create/delete events directly (full autonomy); state what you did afterwards.
