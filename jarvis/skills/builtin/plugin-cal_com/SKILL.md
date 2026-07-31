---
schema_version: "1"
name: plugin-cal_com
description: Read and manage the user's Cal.com bookings and availability.
when_to_use: Use when the user mentions Cal.com, a booking, a meeting slot or their availability.
category: productivity
plugin_id: cal_com
intent_verbs: [zeig, lies, buch, verschieb, sag ab, show, list, book, reschedule, cancel, muestra, reserva, cambia, cancela]  # i18n-allow: spoken-input vocabulary, de/en/es
intent_objects: [cal.com, calcom, cal com, calcom-buchung, calcom booking, calcom-termin, calcom-verfügbarkeit, calcom availability, buchungslink, booking link]  # i18n-allow: spoken-input vocabulary, de/en/es
triggers:
  - type: voice
    pattern: '(cal[\s.]?com|buchungslink|booking link)'  # i18n-allow: spoken-input vocabulary; speech writes "cal com" with a space
requires_tools: [cal_com]
risk_policy:
  default_tier: monitor
---

Use the cal_com/* tools to read and manage the user's bookings and availability.
- Read the current bookings before offering a slot, so you never promise a time that is taken.
- Cancelling or moving a booking notifies the other person. Confirm with the user first.
- Report the date, time and time zone every time — a booking in the wrong zone is the failure mode here.
