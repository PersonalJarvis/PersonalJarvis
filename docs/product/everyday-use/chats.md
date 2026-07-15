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
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [chat, conversations, history, context, files, voice]
related: [sessions-and-run-inspector, outputs-and-files, instructions-and-persona]
---

Use **Chats** for quick questions, follow-up work, and conversations you want
to revisit. The view combines saved text conversations with recorded voice
sessions, while keeping generated files and detailed run information in their
own areas.

This page also explains the current limits of chat history. In the current
version, what you see during a live conversation and what returns after you
reopen it are not always the same.

## Before You Start

- Open **API Keys** and confirm that at least one Brain provider shows
  **Works**. A provider is the service that prepares Jarvis's answer.
- Wait until the message field is available. You can type while the desktop
  voice feature is still getting ready.

> [!warning] Never send a password, API key, access token, recovery code, or
> other credential in a chat or dropped file. Enter credentials only in the
> protected connection fields provided by the app.

## Use a Chat

1. **Open Chats.** Select **Chats** in the sidebar. You see **History**, the
   conversation area, and the message field.

2. **Start with a clean view.** Select **New chat**. The visible conversation
   clears. Jarvis creates a new text entry when you send the first message.

3. **Send your request.** Type in the message field, then press **Enter** or
   choose **Send**. Press **Shift+Enter** to add a line without sending.

4. **Watch the result.** Your message appears immediately. A thinking status
   may show the current activity, followed by the assistant reply. A tool or a
   longer job can add more progress before the final result.

5. **Continue with a follow-up.** Refer to the current topic instead of
   repeating it. Jarvis uses the recent live conversation as context while the
   app remains open.

### Dictate Instead of Typing

Choose **Dictate**, speak, and then choose **Stop dictation**. The transcript
is placed in the message field for review; it is not sent until you choose
**Send** or press **Enter**.

Dictation needs the desktop speech feature. When it is unavailable, continue
by typing.

## Organize Conversation History

History refreshes automatically and places the newest activity first. Entries
are grouped under **Today**, **Yesterday**, and **Earlier**. A **Text** or
**Voice** badge shows which kind of record you are opening.

| Action | What you see | What happens to context |
|---|---|---|
| Select **New chat** | An empty conversation area | A new text record starts on the first send, but the shared live Brain context is not explicitly cleared. |
| Open a **Text** entry | Its saved messages return | Recent saved messages replace the Brain's current context for the next reply. |
| Open a **Voice** entry | The recorded spoken turns appear | The voice transcript becomes context; typing creates a separate text record and leaves the voice record unchanged. |
| Select **Speak in this conversation** | Jarvis starts listening when desktop voice is ready | Recent saved messages are supplied to the voice conversation. |
| Select **Delete** on a text entry | The text entry disappears | That local text thread and its saved messages are removed. Voice entries cannot be deleted from Chats. |

Drag the divider beside History to change its width. Double-click the divider
to return to the default width. The chosen width is remembered by the app.

Text history is stored locally on the machine and survives a normal restart.
It is not a cross-device chat-sync service. Text threads more than one year old
are removed during startup maintenance; voice sessions use their own retention
rules.

### Current Context Limits

**New chat is currently an organization boundary, not a guaranteed context
reset.** It clears the visible panel and starts a different saved thread, but
it does not call the Brain's history reset. Details from the previous live
conversation can therefore influence the next reply.

Opening an existing entry is a stronger switch because Jarvis replaces its
live context with the recent messages found in that record. However, the saved
text record is currently incomplete, as explained below. Do not rely on
**New chat** to isolate information that must stay separate.

## Add Files and Other Context

You can give Jarvis a file, image, Portable Document Format (PDF) document,
selected text, or link for the next request.

1. Drag the item into the Jarvis window. The Jarvis drop target appears.
2. Release it and wait for **Added to conversation**.
3. Send a message that explains what you want, such as asking for a summary or
   a comparison.

A drop is silent: it adds context but does not make Jarvis answer on its own.
Text-based files are read as bounded text, PDF extraction is best effort, and
images require a Brain provider that can understand images. For an unsupported
binary file, Jarvis may receive only its name, type, and size. One drop can
contain up to 25 MB in total.

Dropped content is temporary live context. It is not copied into the saved
chat transcript and does not become an **Outputs** file. Add it again after
reopening a conversation or restarting the app if Jarvis still needs it.

## Understand What Is Saved

The current app uses separate stores for text chats and voice sessions. It
also keeps a short live context inside the active Brain so follow-up questions
can make sense before you leave.

| Item | Visible during the live chat | Available after reopening |
|---|---|---|
| A typed or connected-channel user message | Yes | **No, not reliably in the current version** |
| An assistant reply | Yes | Yes, in the text thread |
| A system error shown as a chat message | Yes | Yes, in the text thread |
| A pre-reply acknowledgement, thinking status, or reasoning trace | Sometimes | No, not as durable chat history |
| A dropped file, image, link, or selected text | Used as temporary context | No |
| A recorded voice turn | Yes | Yes, as a separate voice session while retained |
| A file created by a Jarvis-Agent | Linked from the work result | In **Outputs**, not inside chat history |

> [!note] The incoming-message path currently publishes user messages to the
> live screen but does not write them to the text-chat store. Assistant and
> system replies are written. After reopening a text chat, your prompts may be
> missing, the title may remain **New Chat**, and restored context may contain
> answers without the questions that produced them.

Until this is fixed, keep important source instructions in your own notes and
repeat essential context before continuing an older text conversation. This
limitation also affects user text arriving through a connected chat channel.

## Chats, Sessions, and Outputs

These areas preserve different parts of the same piece of work:

- **Chats** is the conversation view. Use it for live messages, saved text
  replies, and quick access to voice transcripts.
- **Sessions and Run Inspector** explains what happened during a recorded
  voice session or a particular run, including details that do not belong in a
  simple chat transcript.
- **Outputs** contains files created by Jarvis or Jarvis-Agents. A chat reply
  can point to an output, but the file remains separate so you can preview,
  download, or open it safely.
- **Instructions and Persona** contains standing guidance that can shape every
  answer. It is different from temporary chat context and from saved history.

Dragging a completed work card from **Outputs** into the Jarvis drop target
brings a short description of that work into the live conversation. It does
not move or duplicate the generated files.

## How It Fits Together

1. **You start a turn.** Typing, reviewed dictation, a resumed conversation,
   or a connected channel supplies the user message. A dropped item can supply
   extra context for the next real message.
2. **The active Brain prepares the response.** It receives recent live context,
   any resumed messages, temporary dropped content, and your standing
   instructions. If the preferred provider is unavailable, Jarvis can use
   another compatible provider you configured; otherwise it shows an honest
   Brain error.
3. **Tools and safety rules can join the turn.** Jarvis may use an available
   tool or ask for confirmation before an action. Longer work can move to a
   Jarvis-Agent while Chats remains the place for conversation.
4. **The result goes to the matching area.** A direct answer appears in Chats,
   a spoken exchange is recorded as a voice session, and created files appear
   in Outputs.
5. **Reopening restores only the saved record.** Jarvis supplies recent stored
   messages to the Brain, but current text-chat persistence can omit the user
   side of the exchange. Repeat missing context before relying on a follow-up.

## Check That It Works

1. Select **New chat** and send a harmless request that needs a short answer.
2. Confirm that your message appears, a thinking status starts, and an
   assistant reply follows.
3. Select **New chat**, then open the newest **Text** entry in History.

The live chat works when both sides of the exchange appear before you leave.
Current persistence works as implemented when the assistant reply returns
after reopening. Your original prompt may be absent because of the confirmed
saving limitation.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| The message field says **Starting** or **Offline** | The local app connection is still warming or unavailable | Wait for startup to finish. If every area stays offline, restart the app normally and check again. |
| Your message appears but no reply arrives | No compatible Brain provider is ready, or the provider or a tool timed out | Open **API Keys**, test the affected provider, and choose another ready provider family when available. Then retry with a short text-only request. |
| A reopened text chat shows replies but not your prompts | The current incoming-message path did not persist the user messages | Repeat the essential request before continuing and keep important instructions outside chat history. |
| A new chat still seems to remember the previous topic | **New chat** changed the visible and saved thread but did not clear shared live Brain history | State the new context explicitly. Do not use New chat as an isolation boundary in the current version. |
| A dropped item produces no answer | Drops add context silently, or the item was empty, too large, or unsupported | Wait for **Added to conversation**, keep the total below 25 MB, then send a clear request about the item. |
| **Speak in this conversation** is unavailable | Voice is still warming, disabled, or unsupported in this mode | Continue by typing. Check the desktop voice status before trying again. |

## Next Steps

- Read [Sessions and Run Inspector](sessions-and-run-inspector) to distinguish
  a simple conversation record from a detailed voice or run trace.
- Use [Outputs and Files](outputs-and-files) to find, preview, and reopen files
  created during longer work.
- Review [Instructions and Persona](instructions-and-persona) to set lasting
  response guidance without treating chat history as a permanent instruction
  store.
