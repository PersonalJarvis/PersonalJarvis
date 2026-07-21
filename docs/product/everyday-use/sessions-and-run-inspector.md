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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [sessions, run-inspector, voice, transcripts, diagnostics, tasks, jarvis-agents, outputs]
related: [chats, voice-conversations, troubleshooting, privacy-and-local-data]
---

**Transcription** and **Run Inspector** are two views built from saved voice
session data. Transcription shows the recognized conversation and recorded
spoken output. Run Inspector adds the captured routing, tool, timing, approval,
and error details that can help explain what happened.

These views do not contain an audio recording, and opening them does not repeat
a request or run a tool. Copying, downloading, or opening an export creates a
separate copy of the saved data.

## Before You Start

Complete at least one voice turn. A running session can appear in both views,
but its turns and totals may still change until the call ends. The local session
recorder is enabled by default; if its store is disabled or unavailable, Jarvis
can still handle a request but cannot rebuild these views afterward.

Text-only chats, scheduled task runs, and standalone Jarvis-Agent missions do
not create Run Inspector entries by themselves.

> [!warning] Transcripts and exports can contain your words, provider and model
> names, tool previews, approval details, and errors. Review an export before
> sharing it. Never provide a password, API key, token, or recovery code through
> voice or chat.

## Choose the Right View

| When you want to know | Open | What you get |
|---|---|---|
| What did I say, and what reply was recorded? | **Transcription** | Recognized requests, replies, and other recorded spoken phrases, grouped by voice turn |
| Which voice mode or provider completed the turn? | **Transcription** | Available mode, provider, model, token, cost, voice, and timing details |
| Did Jarvis record a functional problem? | **Run Inspector** | A **Success**, **Partial**, or **Failed** result, separate from latency status |
| Why was the turn slow or unsuccessful? | **Run Inspector** | Available latency stages, decisions, approvals, tools, timeline events, and errors |
| Which capability joined the turn? | **Run Inspector** | Recorded Computer-Use, agent, Skill, and tool badges |

A voice **session** starts with a voice interaction and ends at hangup. It can
contain several turns. Transcription hides a finished attempt that contains no
user or assistant text, while Run Inspector may still show that attempt for
diagnostics.

A **run** is not a second stored conversation. Jarvis builds it when you open
Run Inspector from the saved session rows and selected events. When available,
it also matches local command-usage records to a turn. No AI provider performs
a second analysis, and missing source data stays missing.

**Chats** is a combined history of text threads and meaningful voice sessions.
A voice item there shows the saved user and assistant turn text. Transcription
adds voice-specific metadata and supplemental spoken phrases. If you continue a
voice item by typing, the new message belongs to a text thread; it does not
rewrite the finished voice session.

## Review a Voice Session

1. **Open Transcription.** The session list shows the preview, time, duration,
   turn count, voice mode, and how the call ended. Jarvis selects the newest
   finished session when one is available.
2. **Choose a session.** A session labeled **running** is still in progress.
   End the call before treating its totals as final.
3. **Read each turn.** Your block contains the recognized request. The
   assistant block prefers a playback-confirmed reply when one was recorded;
   otherwise it shows the saved generated reply. Other playback-confirmed
   phrases appear under **Spoken output**. Available provider, model, token,
   cost, voice, tool, and timing details appear around the conversation.
4. **Copy or save one turn if needed.** Use **Copy** or the adjacent download
   action in that turn's header.
5. **Export a session when you need a separate copy.** Each **Text**,
   **Markdown**, and **JSON** row provides actions to copy, download, or open
   that format.

Text is a clean dialogue and omits telemetry and technical diagnostics.
Markdown keeps structure, metadata, spoken-output labels, and available
technical detail. JSON contains the full saved session header, turns, and raw
recorded events, so inspect it carefully before sharing.

In the desktop shell, a download is saved to your **Downloads** folder and the
open action uses a supported local app. In a regular browser, download uses the
browser and the open action displays the export in a new tab. An unavailable
local editor does not affect the saved session.

> [!note] On startup, Jarvis removes sessions whose start time is older than
> the configured retention period, which is 30 days by default. An installation
> can change or disable that cleanup. There is no action in the current app to
> delete one voice session. An exported copy remains at its destination until
> you delete it there.

## Inspect What Happened

1. **Open Run Inspector.** It selects the newest recorded run. Use the preview
   and time to find the matching voice session, then read the written outcome
   in the selected run's header.
2. **Read outcome and latency separately.** **Success** means the captured data
   contains no functional problem that changes the outcome. It does not certify
   that the answer is correct. **Partial** means Jarvis recorded a failed tool,
   a recoverable error, or an answer alongside a harder problem. **Failed**
   means it recorded a hard error or denied action without an answer. A
   **slow** label or latency warning describes speed, not functional outcome.
3. **Check Triggered badges.** They summarize recorded tools and higher-level
   capabilities for that turn. The agent badge uses the name derived from your
   configured assistant name. It means the voice turn handed work to a
   Jarvis-Agent, not that the run contains the mission's full later history.
4. **Expand Metrics & deep dive.** When data exists, this section shows total
   thinking and speaking time, tokens, interruptions, worst latency status,
   cost by provider, and tool counts. Zero or a missing section can mean that
   no matching event was recorded.
5. **Expand Forensics on a turn.** Available panels show latency stages, the
   decision path, tool status, timeline events, and errors. These panels are a
   partial record, not a complete log of every internal action.
6. **Use Export raw (JSON) carefully.** This opens the saved session JSON, not
   a polished Run Inspector report. Derived outcomes and metrics, matched
   command-usage data, mission history, and files created during the work are
   not added to this export.

## How It Fits Together

1. **A voice interaction starts a session.** A wake activation, the **Call**
   shortcut, or **Speak** from a conversation can start it. Speech recognition
   supplies the text saved for your side of a turn.
2. **Jarvis answers or acts.** The active Brain can answer, use a tool, request
   approval, create a task, or hand longer work to a Jarvis-Agent. When a
   fallback provider completes the turn, the completed provider is saved with
   the turn when that information is available.
3. **The recorder observes selected events.** It saves recognized text,
   responses, spoken phrases, timing, decisions, actions, and errors to the
   local session store. It does not control the voice pipeline and does not
   save microphone audio for these views.
4. **Transcription presents the voice record.** It combines the saved turn
   fields with recorded spoken phrases. [Chats](chats) can list the same voice
   session beside separate text threads.
5. **Run Inspector builds a diagnostic view.** It derives outcomes, activity
   badges, metrics, and forensic panels from the data that was captured.
6. **Other features own later work.** A created task continues in **Tasks**. A
   delegated mission continues in **Jarvis-Agents**, and its files appear in
   **Outputs**. A terminal agent completion spoken after hangup can attach to
   the most recently finished voice transcript, provided another session has
   not started first. The rest of the later lifecycle stays in its own feature.

## Check That It Works

1. Start a short voice conversation, ask one harmless question, wait for the
   reply, and end the call.
2. Open **Transcription** and confirm that the newest finished session shows
   your recognized request and the recorded assistant reply.
3. Open **Run Inspector** and select the item with the same preview and time.
   Confirm that the header shows an outcome and the run contains the same turn.

The views agree when they describe the same saved session. Run Inspector may
show fewer details when a particular event or matching command record was not
captured.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **No voice sessions yet** after a voice test | The call is still open, no transcript-bearing turn finished, or the recorder is unavailable | End the call, wait briefly, and reopen Transcription. Then try one short voice turn. |
| **Session recorder is disabled.** or **Run recorder is disabled.** | This app instance has no available session store | This view has no recorder switch. Check [Troubleshooting](troubleshooting) or ask the person who manages the installation. Unrecorded sessions cannot be restored. |
| A run appears without a matching Transcription item | The finished voice attempt contains no saved user or assistant text | Use Run Inspector for the available diagnostic events. Transcription intentionally hides empty finished attempts. |
| A running session has incomplete turns or totals | The active turn has not finished | End the call or select the newest finished session, then reopen the view. |
| Run Inspector shows **Partial**, but you received an answer | A tool, approval, or another recorded part had a problem | Expand **Forensics**, then review **Tools** and **Errors** for that turn. |
| A transcript exists, but a forensic panel is empty | That voice path or older session did not record that event type | Use the transcript as the conversation record. A missing panel does not prove that an action never happened. |
| A task, mission, or generated file is not in the run | Its later lifecycle belongs to another feature | Check **Tasks**, **Jarvis-Agents**, or **Outputs** using the request time and description. |
| An older voice session disappeared | It was older than the retention cutoff when Jarvis next started | Check any export you saved. Automatic removal cannot be undone in the app. |

## Next Steps

- Read [Chats](chats) to understand the combined text and voice history.
- Read [Voice Conversations](voice-conversations) to learn how a spoken turn
  moves through speech recognition, the Brain, tools, and speech output.
- Open [Jarvis-Agents](jarvis-agents) to follow delegated work after the voice
  turn that started it has ended.
- Review [Privacy and Local Data](privacy-and-local-data) for local storage,
  retention, deletion limits, and export safety.
