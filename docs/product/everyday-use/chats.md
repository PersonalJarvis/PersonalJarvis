---
title: "Chats"
slug: chats
summary: "Organize conversations, continue earlier work, add files, and understand how chats relate to sessions and outputs."
section: "Everyday use"
section_order: 2
order: 1
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [chat, conversations, history, context, files, voice]
related: [sessions-and-run-inspector, outputs-and-files, instructions-and-persona, dictation]
---

Use **Chats** for questions, follow-ups, and conversations you may revisit.
History combines local text threads and retained voice transcripts; files,
detailed runs, and durable knowledge stay elsewhere.

The current text history is incomplete: assistant and system messages are
saved, but typed prompts are not. The live screen can therefore contain more
than the thread restores later.

## Before You Start

- Under **API Keys & Providers > Brain**, select **Test** on a local or
  connected provider and wait for **Works**.
- Wait until the composer accepts text; voice may still be warming.
- For the composer microphone, also prepare a **Voice input** provider and
  microphone.

> [!warning] Never send credentials, recovery codes, or private keys in chat or
> a dropped file. Use only the protected connection fields for that service.

## Start and Continue a Chat

1. Select **Chats**. History is left; the conversation and composer are right.
2. Select **New chat**. An untouched empty view is not saved. Its text thread is
   created when you send the first message.
3. Type a clear request. Press **Enter** or select **Send**. Use **Shift+Enter**
   for a line break.
4. Watch **Thinking** or the current tool step. The reply appears under your
   assistant name. **Thought for...** may summarize steps, but is not saved.
5. Ask a follow-up. The running Brain uses recent in-memory context.

**New chat** clears the visible messages, not the Brain's recent context. Use a
self-contained opening prompt when changing topics. Restart first when strict
separation from the previous topic matters.

## Organize History

History refreshes newest first under **Today**, **Yesterday**, and **Earlier**.
**Text** and **Voice** badges identify the record type.

| Action | Result |
|---|---|
| Select a **Text** entry | Restores its saved messages and seeds recent ones into the Brain for the next typed turn |
| Select a **Voice** entry | Restores user and assistant transcript turns; typing starts a separate text thread with that context |
| Select **Speak in this conversation** | Starts desktop voice with recent saved turns when voice is ready |
| Hover over a **Text** row and select **Delete** | Permanently removes that local thread and its saved messages |
| Drag the History divider | Changes pane width; double-click restores the default |

Chats has no rename, pin, folder, or search control. Titles are designed to use
the first saved user message, but live prompts are not currently stored, so
entries often remain **New Chat**. Use their time to distinguish them and open
one before deleting. Voice entries follow session retention.

Local text threads are not device-synced and are pruned at startup after 365
inactive days.

## Type with the Composer Microphone

The microphone beside **Send** transcribes; it does not start a voice call:

1. Place the caret in the composer and select **Dictate**.
2. Speak during **Listening... speak now**. Interim words append to the current
   draft.
3. Select **Stop dictation**, review the text, then select **Send**.

Until **Send**, the Brain has not received the draft.

System-wide **Dictation** instead starts from **Voice > Dictation** or a
shortcut, writes into the focused field in any app, and never asks the Brain to
answer. Its history, recovery, statistics, language, polish, translation, and
retention are separate. Text placed in chat remains a draft. See [Dictate Into
Any App](dictation).

## Add Temporary File Context

Chats has no attachment picker. Drag a file, image, PDF, selected text, or link
over the floating bar, mascot, or lower-right target. Release it at **Drop to
brief**, wait for **Added to conversation**, then send an instruction. A drop
alone never starts a turn.

- One drop is limited to 25 MB in total.
- UTF-8 text is shortened to about 8,000 characters per item and 12,000 total.
- PDF extraction is best effort. On failure, only basic file details remain.
- Images wait for the next request; text-only providers receive only the note.
- Other file types contribute name, media type, and size. A dropped link is
  text, not a request to fetch the page.

Dropped content stays in the running Brain, not saved attachments or
**Outputs**. Images are used once. Resuming or restarting replaces this context.

## Resume Voice or Text

Opening History replaces recent Brain context with that record. Text restores
assistant/system messages; voice restores both sides of recorded turns.

> [!note] Typed prompts, dropped material, pre-reply acknowledgments, thinking
> progress, and thought summaries are not currently restored from a text
> thread. Repeat the goal, source, and important limits before continuing an
> older text chat. Do not treat **New chat** as a privacy boundary.

Typing from a voice entry creates a separate text thread. **Speak in this
conversation** starts voice from recent saved turns. If unavailable, continue
by typing.

## Understand Providers and Status

The sidebar reports provider and model; authoritative Brain cards and **Test**
are under **API Keys & Providers**.

The preferred Brain receives the prompt and context. A compatible fallback may
receive the same content. If none answers, thinking ends with a visible system
diagnostic. Fix or switch providers before retrying.

## Keep Chats, Outputs, Sessions, and Wiki Separate

- **Chats** holds the live exchange and currently supported saved messages.
- **Sessions and Run Inspector** holds voice transcripts and detailed run
  evidence.
- **Outputs** holds files created by delegated work. Dragging an output card to
  the drop target starts a chat turn with its status and available summary, not
  the generated files themselves.
- **Wiki** holds selected durable knowledge; chat is not automatically memory.
  See [Wiki and Memory](wiki-and-memory) or [Use UltraWiki](ultrawiki).
- **Instructions and Persona** holds standing behavior guidance that applies
  beyond one thread.

## Privacy and Deletion Boundaries

Local history does not make processing local. Providers may receive messages,
extracted text, image bytes, or mic audio according to the selected paths.

Deleting a text thread does not retract provider requests or remove original
files, Outputs, Wiki entries, voice sessions, or backups. See [Privacy and Local
Data](privacy-and-local-data).

## How It Fits Together

1. Typed or reviewed mic text becomes a user request only after **Send**.
2. The Brain combines that request with live, resumed, and dropped context.
3. A compatible fallback may take over; tools can add progress or request
   approval before a result.
4. Direct replies return to Chats. Delegated files and detailed run evidence go
   to Outputs and Sessions.
5. History restores only stored messages; Wiki is the separate durable-memory
   path.

## Check That It Works

1. Select **New chat** and send `Reply only with: chat is ready.`
2. Confirm your prompt and **chat is ready** appear and Thinking ends.
3. Select **New chat**, open the newest **Text** entry, and confirm the assistant
   reply returns. The typed prompt is expected to be absent.
4. Select **Dictate**, say a harmless sentence, stop, and confirm it remains an
   editable unsent draft.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| Composer says **Starting...** or **Offline** | The live connection is warming or unavailable | Wait; if it persists, restart the app and check again |
| Message appears but no reply follows | The Brain, provider, or a requested tool failed or timed out | Test Brain providers, then retry one short text-only request |
| Composer mic hears nothing | Its microphone, permission, speech pipeline, or Voice input provider is unavailable | Check **Settings > Audio devices**, permissions, and Voice input health |
| Replies return without prompts after resume | User prompts are not in the current stored text roles | Restate essential context and keep important source instructions elsewhere |
| New chat mentions the old topic | Visible reset did not clear live Brain history | Start self-contained or restart for strict separation |
| Drop produces no reply | Drops add context silently | Wait for **Added to conversation**, then send an instruction |
| **Speak in this conversation** fails | Desktop voice is warming, disabled, or unavailable on this host | Continue by typing and check voice readiness |

## Next Steps

- Follow [Start Your First Chat](start-your-first-chat) for a short guided test.
- Read [Dictate Into Any App](dictation) for insertion, recovery, and retention.
- Use [Outputs and Files](outputs-and-files) and [Sessions and Run Inspector](sessions-and-run-inspector)
  for generated files and detailed run evidence.
- Read [Instructions and Persona](instructions-and-persona) for lasting guidance
  that does not depend on chat history.
