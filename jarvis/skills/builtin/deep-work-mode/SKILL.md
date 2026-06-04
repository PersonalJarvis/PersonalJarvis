---
schema_version: "1"
name: deep-work-mode
version: "1.0.0"
description: |
  Activates focus mode: Windows Do-Not-Disturb on, minimize all windows except
  the active one, Slack status to "Focus", Spotify Deep-Focus playlist, 90-minute timer.
  Trigger: hotkey Ctrl+Alt+D or voice "deep work mode" / "fokusmodus" / "starte fokus".
category: productivity
tags: [focus, dnd, timer, slack]
author: builtin
license: MIT
triggers:
  - type: hotkey
    combo: "ctrl+alt+d"
    language: [de, en]
  - type: voice
    # R10 mitigation 2026-05-01: standalone "fokus" / "konzentration" removed
    # (too generic, triggered by "ja, fokus." STT hallucinations). Imperative
    # verbs "starte" / "aktiviere" or explicit compounds "fokusmodus" /
    # "deep work mode" are mandatory.
    pattern: "^(deep[-\\s]?work([-\\s]?mode| modus)?|fokus[-\\s]?modus|konzentrations[-\\s]?modus|starte (deep work|fokus(modus)?|konzentration(smodus)?)|aktiviere (deep work|fokus(modus)?))$"
    language: [de, en]
requires_tools:
  - windows-mcp/set_do_not_disturb
  - windows-mcp/minimize_all
  - remember
risk_policy:
  default_tier: monitor
  per_tool_overrides:
    windows-mcp/set_do_not_disturb: safe
    windows-mcp/minimize_all: ask
    remember: safe
config:
  duration_minutes: 90
  spotify_playlist: "Deep Focus"
  slack_status_text: "In the zone — back at {end_time}"
  slack_status_emoji: ":headphones:"
  minimize_others: true
token_budget_estimate: 2000
---

# Deep Work Mode

A 90-minute sprint with maximum quiet. The mode is **fully automatic** —
no confirmation prompts, because you are in the middle of a context switch. That is
why the risk tier is `safe` for DND; only `minimize_all` is `ask` because it can
behave destructively.

## Flow

### 1. Activate DND

TOOL: windows-mcp/set_do_not_disturb {"enabled": true, "duration_minutes": {{config.duration_minutes}}}

Windows Focus Assistant: "Priority only" for the configured duration.
Toast notifications are silenced.

### 2. Tidy up windows (optional)

If `config.minimize_others == true`:

TOOL: windows-mcp/minimize_all {"except_foreground": true}

Remembers the current window list for the restore on exit.

### 3. Set Slack status (if Slack is configured)

TOOL: slack-mcp/set_status {"text": "{{config.slack_status_text}}", "emoji": "{{config.slack_status_emoji}}", "expiration": "{{end_time_epoch}}"}

If slack-mcp is unavailable: skip silently, no error.

### 4. Start Spotify (if Spotify MCP is configured)

TOOL: spotify-mcp/play_playlist {"name": "{{config.spotify_playlist}}", "device": "auto"}

Fallback: no action if there is no Spotify.

### 5. Set the timer

TOOL: remember {"namespace": "active-modes", "key": "deep-work", "value": {"started_at": "{{now.iso}}", "ends_at": "{{end_time.iso}}", "duration_min": {{config.duration_minutes}}}}

The orchestrator automatically schedules a cron trigger on `ends_at`, which fires
the `deep-work-mode-end` skill (a separate skill file, not included here —
shipping in v1.1).

### 6. Confirm

Short TTS output:
- DE: "Deep Work an fuer {duration} Minuten. Viel Erfolg."
- EN: "Deep work on for {duration} minutes. Go."

Prosody: calm, focused, not euphoric.

## Ending

End early via voice "ende fokus" / "stop focus" or Ctrl+Alt+D again.
The end handler (its own skill in v1.1) does the following:
1. DND off.
2. Reset the Slack status.
3. Stop Spotify (optional).
4. Short stats: "90 Minuten Fokus. Ein paar Tiefensprints weniger."

## Fallbacks / Edge Cases

- Already in deep work? → No-op, TTS: "Schon im Fokus, noch {mins} Minuten."
- Windows MCP not installed? → Abort the skill, TTS hint, link to the wizard.
- User has an active call (Teams/Zoom): `windows-mcp/set_do_not_disturb` respects
  it and sets "Alarms only" instead of "Priority only" — no interruption.

## Do Not

- No aggressive actions such as "close browser tabs" without explicit consent.
- No automatic restart after 90 minutes — the user should decide for themselves.
- No popup nags ("are you sure?") — that is the death of the flow.

## Trace

Registry traced: start event, one event per step, end event including `duration_ms`.
The user sees in the tray log: "deep-work-mode: aktiv bis 14:30."
