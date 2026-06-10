---
schema_version: "1"
name: deep-work-mode
version: "2.0.0"
description: >-
  Activates a distraction-free focus sprint: quiet notifications where
  possible, focus music if Spotify is connected, and a clear spoken start
  signal with the sprint duration. Use when the user wants to focus,
  start deep work, or enter concentration mode.
when_to_use: >-
  Use when the user says "fokusmodus", "starte fokus", "deep work mode",
  "konzentrationsmodus", "aktiviere deep work", or presses the focus
  hotkey. Not for a casual "ich muss mich konzentrieren" remark without a
  clear activation intent.
category: productivity
tags: [focus, dnd, timer, slack]
author: builtin
license: MIT
triggers:
  - type: hotkey
    combo: "ctrl+alt+d"
    language: [de, en]
  - type: voice
    pattern: "^(deep[-\\s]?work([-\\s]?mode| modus)?|fokus[-\\s]?modus|konzentrations[-\\s]?modus|starte (deep work|fokus(modus)?|konzentration(smodus)?)|aktiviere (deep work|fokus(modus)?))$"
    language: [de, en]
requires_tools: []
risk_policy:
  default_tier: monitor
config:
  duration_minutes: 90
  spotify_playlist: "Deep Focus"
token_budget_estimate: 2000
execution: inline
---

# Deep Work Mode

Start a {{ config.duration_minutes }}-minute focus sprint with as little
friction as possible — the user is mid context switch, so act, don't
interrogate. Use only capabilities that are actually available; skip the
rest with one short clause. Never invent actions you did not perform.

## Steps

1. **Quiet the desktop.** If a do-not-disturb or notification control
   capability is available on this machine, enable it for
   {{ config.duration_minutes }} minutes. If none is available, tell the
   user in one clause that notifications must be muted manually.

2. **Focus music.** If the Spotify plugin is connected (tools namespaced
   `spotify/...`), start the "{{ config.spotify_playlist }}" playlist.
   Otherwise skip silently.

3. **Status.** If the Slack plugin is connected (tools namespaced
   `slack/...`), set the user's status to focused with an end time
   {{ config.duration_minutes }} minutes from now. Otherwise skip
   silently.

## Answer format

One short spoken confirmation: what was activated and for how long —
e.g. that the sprint runs for {{ config.duration_minutes }} minutes and
what was muted or started. One sentence, no list, no tool names. Answer
in the user's language.
