---
schema_version: "1"
name: morning-routine
version: "2.0.0"
description: >-
  Delivers the user's spoken morning briefing: today's calendar, unread
  email summary, weather, and anything urgent. Use when the user asks for
  a morning briefing or day overview, says good morning, starts their day,
  or asks "what's on today" / "wie sieht mein Tag aus".
when_to_use: >-
  Use when the user says "starte die Morgenroutine", "guten Morgen",
  "good morning", "morning briefing", "Tagesueberblick", "what's my day
  looking like", or asks for their schedule first thing in the day.
category: productivity
tags: [daily, routine, mail, calendar, weather]
author: builtin
license: MIT
triggers:
  - type: voice
    pattern: "(morgenroutine|morgen[-\\s]?briefing|morning routine|morning briefing|start day|tages(ue|ü)berblick)"
    language: [de, en]
  - type: voice
    pattern: "^(guten morgen|good morning)[.!\\s]*$"
    language: [de, en]
  - type: schedule
    cron: "0 7 * * *"
    language: [de, en]
requires_tools: []
risk_policy:
  default_tier: monitor
config:
  email_limit: 5
  calendar_days_ahead: 1
  weather_location: "Berlin"
  weather_format: metric
token_budget_estimate: 3000
execution: inline
---

# Morning Routine

Deliver a short spoken morning briefing — an overview of the day in under
30 seconds. No walls of text, no reading out 20 emails. Work through the
steps below with the tools you actually have; skip a step gracefully
(one short clause, e.g. "calendar is not connected yet") when its
integration is unavailable. Never invent data.

## Steps

1. **Calendar.** If a calendar plugin or tool is connected (e.g. a
   `google-calendar/...` tool), fetch today's events
   ({{ config.calendar_days_ahead }} day ahead).
   - 0 events: say the calendar is free today.
   - 1 event: name it with its time.
   - Several: give the count and the next one with its time.
   - If the next event starts within 60 minutes, add a heads-up.

2. **Email.** If the Gmail plugin is connected (tools namespaced
   `gmail/...`), check unread mail (limit {{ config.email_limit }}).
   - 0 unread: "inbox is empty".
   - Up to 3: mention sender and subject briefly.
   - More: give the count and the 1-2 most important senders.
   Never read full bodies; never read out secrets or codes.

3. **Weather.** If a web search tool is available, get today's weather for
   {{ config.weather_location }} ({{ config.weather_format }}). One clause:
   condition, high/low. Skip silently if unavailable.

4. **Anything urgent.** If a running mission or an unread important
   notification is visible to you, mention it in one sentence.

## Answer format

Compose ONE flowing, friendly briefing of 3-5 short sentences — natural
spoken language, no lists, no markdown, no tool names. End with the most
actionable item (e.g. the next appointment). Answer in the user's
language.
