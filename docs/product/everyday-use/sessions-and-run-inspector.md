---
title: "Sessions and Run Inspector"
slug: sessions-and-run-inspector
summary: "See the difference between conversation history and a detailed run trace, then use both to understand an answer."
section: "Everyday use"
section_order: 2
order: 4
diataxis: explanation
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [sessions, run-inspector, voice, transcripts, diagnostics, tasks, jarvis-agents, outputs]
related: [chats, voice-conversations, troubleshooting, privacy-and-local-data]
---

Use **Transcription** to review what was saved from a voice conversation. Use
**Run Inspector** to understand how that conversation was handled: its
recorded setup, timing, decisions, tools, approvals, errors, and events.

Both views are read-only. Opening a run does not ask the Brain again, repeat a
tool, or continue the conversation. These views store text and selected event
details, not microphone audio.

## Know the Terms

| Term | Meaning |
|---|---|
| **Conversation** | The exchange you experience: a text thread or a recorded voice session. |
| **Session** | One voice call from activation to hangup, possibly with several turns. |
| **Turn** | One request and its answer or action inside a session. |
| **Run** | A diagnostic view assembled from a saved session and its events when opened. |
| **Task or agent mission** | Later work tracked in Tasks, the Agent view, or Outputs. |

Text-only chats, scheduled tasks, and standalone missions do not create runs
by themselves. **Chats** combines text threads and meaningful voice sessions;
Transcription adds voice-specific detail.

## Before You Start

Complete one short voice turn and hang up. Running totals can still change.
The local recorder is enabled by default; if unavailable, voice can work but
these views cannot reconstruct the call.

> [!warning] Transcripts and events can contain your words, names, providers,
> tool previews, approvals, errors, paths, or window hints. Never provide a
> secret through voice or chat, and review anything before sharing it.

## Choose the Right View

| Question | Open | Start with |
|---|---|---|
| What did I say and hear? | **Transcription** | User and assistant blocks |
| Which setup handled it? | Both | Transcript metadata, then **Run environment** |
| Did it succeed, and why was it slow? | **Run Inspector** | Outcome, metrics, Latency, and Errors |
| Which feature, tool, or approval joined? | **Run Inspector** | Badges, **What happened**, and Tools |
| What was recorded in order? | **Run Inspector** | Session-level and turn Events |

## Review a Voice Session

1. Open **Transcription** and choose a newest-first row by preview and time.
   Rows also show duration, turns, mode, and hangup reason.
2. Check the header for mode, language, providers, totals, and start/end time.
3. Read each turn. The assistant block prefers the reply confirmed as played;
   status messages and readbacks appear under **Spoken output**. A pending
   safety question says **Awaiting confirmation**.
4. Review available provider, model, tier, voice, tools, latency, tokens, and
   cost. Missing metadata means unknown, not that nothing ran. Copy one turn or
   use a session export:

| Format | Best for | Includes |
|---|---|---|
| **Text** | Clean dialogue | Spoken text with little formatting |
| **Markdown** | Structured notes | Conversation, labels, and metadata |
| **JSON** | Technical review | Saved header, turns, and event payloads |

Each format can be copied, downloaded, or opened. Desktop downloads go to
**Downloads**; a browser uses its download flow or a new tab. Transcription
hides finished attempts with no saved user, assistant, or spoken text, while
Run Inspector can retain those attempts for diagnostics.

## Inspect a Run from Top to Bottom

1. Match the run by preview and start time. Its row summarizes duration, turns,
   cost, errors, feature badges, outcome, and worst latency.
2. Read outcome and speed separately. **Success** means no captured functional
   problem; **Partial** records a failed tool or recoverable problem, or an
   answer alongside a harder error; **Failed** records a hard problem without
   an answer. This does not certify factual correctness. Latency is separate.
3. Open **Run environment** for the recorded mode, surface, activation and
   hangup source, language, providers, models, tiers, voices, and audio rates.
   It shows the old run, not today's settings. Display badges use your current
   wake-derived assistant name; historical wake details stay in Environment.
4. Read feature badges for Computer Use, Skill, the assistant-name Agent, and
   tool or CLI activity. Then open **Metrics & deep dive** for thinking,
   speaking, tokens, interruptions, latency, provider cost, tools, and event
   counts. **usage not measured** is not a measured zero.
5. Read each turn card for the request, reply, progress, triggered features,
   and facts such as endpoint reason, prompt cache, interruption, context size,
   and trace ID.
6. Expand **Forensics** where needed:

   - **Decision path** explains routing, risk, fallback, and mission choices.
     **model** rationale was recorded from the Brain; **rule** comes from a
     recorded fact.
   - **Latency** shows phase timing and warning or breach status.
   - **Tools** shows caller, risk, approval source, duration, exit status, and
     redacted, length-limited command/output previews.
   - **Events** shows recorded payloads in order. Filter or search the
     lifecycle, speech, brain, tool, agent, vision, latency, error, and system
     lanes, or copy visible rows as JSONL.
   - **Errors** shows source, layer, message, and recovery information.

**Session-level events** cover activation, session open/close, and changes
between turns. Turn streams show a warning when capped at 500 rows.

## Export and Diagnose Safely

**Export raw (JSON)** opens the full saved session JSON, not a polished Run
Inspector report. It includes all saved events, but not derived outcomes,
metrics, matched command usage, later mission history, or generated files.

Credential-shaped values in previews are redacted, previews are capped, and
secret-configuration events are excluded. Conversation text, errors, paths,
and window titles can still be sensitive.

For support, share only the timestamp, environment labels, outcome, and
smallest relevant error. Reproduce harmlessly when possible, and prefer
filtered JSONL or a cropped screenshot over the full export.

> [!note] At startup, sessions older than the configured retention period are
> removed; the default is 30 days. Cleanup can be changed or disabled. There is
> no per-session delete action, and exports remain until you delete them.

## How It Fits Together

1. Activation opens a session; each request creates a turn.
2. The recorder stores text, totals, and selected events, not microphone audio.
3. Transcription presents the conversation; [Chats](chats) can show the same
   voice session beside text threads.
4. Run Inspector derives diagnostics without another AI analysis. Later task,
   agent, and file activity remains in its owning feature.

## Check That It Works

1. Ask one harmless voice question, hear the reply, and hang up.
2. Confirm the request and named-assistant reply in **Transcription**.
3. Match its preview and time in **Run Inspector**, then open Environment and
   the turn's Events tab. Extra diagnostic detail is expected.

## Troubleshooting

| What you see | What it means | What to do |
|---|---|---|
| No session after a voice test | The call is open, no text finished, or recording is unavailable | Hang up, wait, and reopen Transcription |
| Recorder is disabled | This instance has no session store | Ask its owner to enable recording; missed calls cannot be restored |
| A run has no Transcription row | It has diagnostics but no meaningful saved text | Use Run Inspector |
| Environment or Forensics is empty | That path did not record the field | Treat it as unknown, not proof nothing happened |
| **Partial** appears with an answer | A tool, approval, or other part failed | Open Tools and Errors |
| **usage not measured** appears | No turn-level usage event exists | Check session totals; it does not mean zero |
| Events are truncated | The turn exceeded 500 displayed rows | Filter them or inspect local session JSON |
| Later work is absent | Another feature owns it | Check Tasks, the Agent view, or Outputs |
| An old session disappeared | Startup retention removed it | Check saved exports; removal cannot be undone |

## Next Steps

- Read [Chats](chats) for combined text and voice history.
- Read [Voice Conversations](voice-conversations) for the path from speech to
  an answer or action.
- Read [Troubleshooting](troubleshooting) to collect wider app diagnostics.
- Read [Privacy and Local Data](privacy-and-local-data) for storage, retention,
  deletion limits, and export safety.
