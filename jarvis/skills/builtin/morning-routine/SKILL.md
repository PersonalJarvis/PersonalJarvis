---
schema_version: "1"
name: morning-routine
version: "1.0.0"
description: |
  Morning check-in: calendar briefing, email triage, weather, build status, Slack unread.
  Trigger: "guten morgen" / "good morning" / "starte morgenroutine" or daily at 07:00.
category: productivity
tags: [daily, routine, mail, calendar, weather]
author: builtin
license: MIT
triggers:
  - type: voice
    # R10 mitigation 2026-05-01: anchored patterns + imperative variants
    # for power users. "guten morgen" / "good morning" stay as natural
    # greeting activation (anchored ^...$ prevents partial matches like "guten morgen, schatz").
    pattern: "^(guten morgen|good morning|start day|starte (die )?morgen[-\\s]?routine|begin morning routine|run morning routine)$"
    language: [de, en]
  - type: schedule
    cron: "0 7 * * *"
    language: [de, en]
requires_tools:
  - gmail-mcp/list_unread
  - google-calendar-mcp/list_today
  - fetch-mcp/fetch_weather
  - remember
risk_policy:
  default_tier: monitor
  per_tool_overrides:
    gmail-mcp/list_unread: safe
    google-calendar-mcp/list_today: safe
    fetch-mcp/fetch_weather: safe
config:
  email_limit: 5
  calendar_days_ahead: 1
  weather_location: "Berlin"
  weather_format: metric
token_budget_estimate: 3000
---

# Morning Routine

A friendly morning briefing. Triggered either by voice ("guten morgen" (good morning) / "good morning")
or automatically at 07:00 via cron. Goal: give an overview of the day, mailbox,
and weather in under 30 seconds — no walls of text, no reading out 20 emails.

## Workflow

The supervisor runs the steps sequentially. Errors in individual steps
are not fatal — if Slack is missing, for example, the block is skipped and a message is spoken:
"Slack nicht verfuegbar, ueberspringe." (Slack not available, skipping.)

### 1. Calendar of the day

TOOL: google-calendar-mcp/list_today {"days_ahead": 1, "include_cancelled": false}

Turn the result into a TTS-suitable summary:

- 0 events: "Heute ist dein Kalender frei." (Today your calendar is free.)
- 1 event:  "Heute ein Termin: {title} um {time}." (Today one appointment: {title} at {time}.)
- several:  "Heute {n} Termine. Der naechste: {first.title} um {first.time}." (Today {n} appointments. Next: {first.title} at {first.time}.)

If the next event starts in less than 60 minutes, additionally warn:
"Achtung, {title} startet in {mins} Minuten." (Heads up, {title} starts in {mins} minutes.)

### 2. Email triage

TOOL: gmail-mcp/list_unread {"limit": 5, "important_only": false}

Format:
- 0 unread:  "Posteingang leer." (Inbox empty.)
- <= 3 unread: list the subject lines and senders.
- >  3 unread: "{n} ungelesene Mails, davon {important_n} wichtig." ({n} unread emails, {important_n} important.)

If the tool delivers an `importance_score`, sort descending and read out
only the top 3. The remainder is summarized in a single line.

### 3. Weather

TOOL: fetch-mcp/fetch_weather {"location": "{{config.weather_location}}", "format": "{{config.weather_format}}"}

Compact format: "{condition} bei {temp_c}C, Hoch {high_c}, Tief {low_c}. {precip_hint}"
precip_hint: "Regenwahrscheinlichkeit {p}%" (Rain probability {p}%) when p > 30, otherwise omitted.

### 4. Build / CI status (optional)

If `github-mcp` is available: fetch the last 3 workflow runs of the main repo.
If everything is green, just say "CI: alles gruen" (CI: all green). On errors: name the failing job.

### 5. Remember

TOOL: remember {"namespace": "routine-log", "key": "morning-{{date.iso}}", "value": {"calendar_count": ..., "unread_count": ..., "weather": ...}}

### 6. Summary

The supervisor summarizes the steps in **one** TTS output.
At most ~40 words — equivalent to approximately 15 seconds of speech.

Prosody hints for TTS:
- Start calm, no high energy. "Guten Morgen. ..." (Good morning. ...)
- Emphasize numbers (`<emphasis level="moderate">`).
- A short breath pause between blocks (`<break time="250ms"/>`).
- Slightly rising tone at the end: "... Schoenen Start in den Tag." (... Have a great start to the day.)

## Fallbacks

- No Google account configured: "Kalender/Mail nicht angebunden, ueberspringe." (Calendar/Mail not connected, skipping.)
- Weather API unavailable: "Wetter gerade nicht verfuegbar." (Weather currently not available.)
- No internet: offline parts only (build status from the last cache, memory recap).

## Do not do

- Do not read out the complete email list — that is noise.
- Do not name every event participant — only the title.
- Do not weave in news feeds without explicit user opt-in (privacy).

## Trace output

The runner writes one `SkillStepExecuted` event per step. The flight recorder
persists the duration, the tool name, and whether a fallback was active. With
`jarvis --debug` the user sees: "morning-routine: 4 Schritte, 1 fallback, 1.8s." (morning-routine: 4 steps, 1 fallback, 1.8s.)
