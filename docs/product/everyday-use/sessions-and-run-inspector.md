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
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [sessions, run-inspector, voice, transcripts, diagnostics, tasks, jarvis-agents, outputs]
related: [chats, voice-conversations, troubleshooting, privacy-and-local-data]
---

**Transcription** and **Run Inspector** are two views of the same recorded
voice session. Transcription helps you read what was said. Run Inspector helps
you understand what Jarvis did, which tools or Jarvis-Agents joined the turn,
and where a delay or failure occurred.

Both views are read-only. Opening them does not repeat a request, run a tool,
or change the result.

## Before You Start

Finish at least one voice conversation. Jarvis records voice sessions by
default, and a completed session gives both views their most reliable data.
Text-only chats, scheduled task runs, and standalone Jarvis-Agent missions do
not create Run Inspector entries on their own.

> [!warning] A transcript or raw export can contain your words, tool previews,
> provider names, and error details. Review it before sharing, and never use
> voice or chat to provide a password, API key, token, or recovery code.

## Choose the Right View

| When you want to know | Open | What you get |
|---|---|---|
| What did I say, and what did Jarvis say? | **Transcription** | Voice sessions grouped into turns, including recognized requests, replies, and other spoken messages |
| Which service answered? | **Transcription** | The provider used for the completed turn, plus available token and cost totals |
| Did the request succeed? | **Run Inspector** | A **Success**, **Partial**, or **Failed** outcome for the run and each turn |
| Why was it slow or unsuccessful? | **Run Inspector** | Recorded latency, decisions, tools, approvals, timeline events, and errors |
| Which feature joined the turn? | **Run Inspector** | **Computer-Use**, **Jarvis-Agent**, **Skill**, and tool badges when those activities were recorded |

A **session** is the voice conversation around one or more turns. A **run** is
the inspection view built from that session. Run Inspector does not create a
second copy of the conversation or perform a second analysis with an AI
provider; it derives its panels from the events Jarvis already recorded.

## Review a Voice Session

1. **Open Transcription.** The left list shows recent voice sessions with a
   preview, time, duration, turn count, and how the session ended. The newest
   finished session is selected automatically when possible.
2. **Choose a session.** Select any list item to open its turns. A session
   marked **running** may still be incomplete, so finish the call before using
   it as a final record.
3. **Read the turns.** Each turn separates your recognized request from the
   assistant reply and other speech that Jarvis actually played. Available
   provider, model, token, cost, timing, and tool details appear beside the
   turn rather than inside the conversation text.
4. **Export only when needed.** Use the Text, Markdown, or JSON row to copy,
   save, or open the session. The desktop app saves a requested file to your
   Downloads folder; a browser session uses the browser's download or opens
   the export in a new tab.

Text is best for a plain record. Markdown keeps useful structure for notes.
JSON is intended for detailed inspection and can expose more recorded fields,
so check it carefully before sending it to anyone.

> [!note] Voice sessions are retained for 30 days by default and are then
> removed automatically. The current app has no delete action for one voice
> session. An export is a separate copy that remains until you delete it from
> its destination. Read [Privacy and Local Data](privacy-and-local-data) before
> recording or exporting sensitive conversations.

## Inspect What Happened

1. **Open Run Inspector.** The newest recorded run is selected automatically.
   The list shows a preview, turn count, duration, feature badges, and outcome.
2. **Read outcome and speed separately.** **Success** means the turn produced
   an answer without a recorded hard failure. **Partial** means Jarvis answered
   but a tool, action, or other part had a problem. **Failed** means the run did
   not produce a usable answer. A **slow** label or latency warning describes
   performance, not whether the result was correct.
3. **Check Triggered badges.** These show recorded tools and higher-level
   features involved in that turn. A Jarvis-Agent badge means the voice turn
   handed work to a Jarvis-Agent; it does not mean every later mission event is
   stored in this run.
4. **Expand Metrics & deep dive.** This summary can show total thinking and
   speaking time, tokens, interruptions, worst latency, cost by provider, and
   tool counts. A missing value means it was not recorded; it does not by
   itself mean that the step failed.
5. **Expand Forensics on a turn.** Review the available latency stages,
   decision path, tool result, timeline, and error entries. These panels are
   evidence for troubleshooting, not a promise that every internal event is
   captured.
6. **Use Export raw (JSON) carefully.** The export opens the underlying session
   record, including turns and raw recorded events. It is not a polished report
   and does not include files created during the work.

## How It Fits Together

1. **A voice request starts the record.** Wake activation, the voice control,
   or push-to-talk opens a voice session. Speech recognition supplies the text
   shown for your side of the turn.
2. **Jarvis answers or acts.** The active Brain provider may answer directly,
   use a tool, request approval, create a task, or hand longer work to a
   Jarvis-Agent. If a preferred provider is unavailable and another compatible
   provider completes the turn, the completed provider is the one shown in the
   session details.
3. **The recorder observes the turn.** It saves selected conversation,
   timing, decision, action, and error events without controlling the voice
   pipeline. If recording is unavailable, the request can still run, but these
   views cannot reconstruct it later.
4. **Transcription presents the conversation.** Use it as the readable voice
   history. A voice entry may also appear in **Chats**, but text-chat history is
   stored separately and does not become a Run Inspector run.
5. **Run Inspector derives the trace.** It turns the same saved events into
   outcomes, activity badges, metrics, and forensic panels. It may show that a
   task was created or a Jarvis-Agent was started during the voice turn.
6. **The connected feature owns later work.** A scheduled task continues in
   **Tasks**. A delegated mission continues in **Jarvis-Agents**. Files they
   create appear in **Outputs**. Those later records do not get folded into the
   original voice run merely because the voice request started them. A spoken
   Jarvis-Agent completion can still be attached to the just-finished voice
   transcript when it arrives after hangup.

## Check That It Works

1. Start a short voice conversation, ask one harmless question, wait for the
   reply, and end the call.
2. Open **Transcription** and confirm that the newest finished session shows
   your recognized request and the assistant reply.
3. Open **Run Inspector** and select the item with the same preview and time.
   Confirm that it shows at least one turn and an outcome.

The two views work together when they describe the same session: Transcription
shows the readable exchange, while Run Inspector adds only the details that
were recorded for that run.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **No sessions yet** after a voice test | The call is still open, no usable turn was recorded, or the recorder is unavailable | End the call, wait briefly, and open Transcription again. Then make one short new voice test. |
| **Session recorder is disabled** or **Run recorder is disabled** | This installation started without the local session store | The current desktop view has no in-app recorder switch. Follow [Troubleshooting](troubleshooting) or contact the person who manages the installation, then create a new test session; unrecorded sessions cannot be restored. |
| A running session looks empty or has incomplete totals | The latest turn has not finished and its aggregates are not final | Select the newest finished session or end the current call, then reopen the view. |
| Run Inspector says **Partial**, but you received an answer | Jarvis answered while a tool, approval, or another recorded part failed | Expand **Forensics**, then review **Tools** and **Errors** for the affected turn. |
| A transcript exists, but a forensic panel is empty | The older run or active voice path did not emit that kind of event | Use the transcript as the conversation record. Do not treat a missing metric as proof that the action never happened. |
| A task, mission, or generated file is not in the run | Its later lifecycle belongs to another feature | Check **Tasks**, **Jarvis-Agents**, or **Outputs** using the request time and description. |
| An older voice session disappeared | It passed the default 30-day retention window | Check any export you intentionally saved. The app cannot restore a session after automatic removal. |

## Next Steps

- Read [Chats](chats) to understand how text history and voice entries appear
  together without becoming the same stored record.
- Read [Voice Conversations](voice-conversations) to see how activation,
  speech recognition, the Brain, tools, and speech output form a turn.
- Open [Jarvis-Agents](jarvis-agents) to follow delegated work after the voice
  turn that started it has ended.
- Use [Outputs and Files](outputs-and-files) to find generated files, which stay
  separate from transcript and raw-run exports.
- Review [Privacy and Local Data](privacy-and-local-data) for retention,
  deletion limits, exports, and records that belong to other features.
