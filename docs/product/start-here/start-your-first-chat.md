---
title: "Start Your First Chat"
slug: start-your-first-chat
summary: "Send a first message, attach useful context, and understand how the conversation is saved."
section: "Start here"
section_order: 1
order: 5
diataxis: tutorial
status: active
owner: maintainers
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [chat, conversations, attachments, history, context]
related: [chats, start-your-first-voice-conversation, sessions-and-run-inspector]
---

Use **Chats** when you want an answer now and a conversation you can build on.
In this tutorial, you will send a request, add useful context, and see exactly
what the current app saves when you leave the conversation.

The basic flow is short: send a message, watch the status, read the reply, and
find the text conversation in **History**. The live chat shows both sides of
the conversation. The saved text thread is more limited: it currently keeps
assistant and system replies, but not the text you typed.

## Before You Start

- Finish first-run setup and open the main app.
- Open **API Keys**, choose the **Brain** tab, and test at least one provider
  until it shows **Works**. A provider is the service that answers your
  request.
- Choose a harmless example topic, such as planning a meal or organizing a
  study session.

> [!warning] Never put a password, API key, access token, recovery code, or
> other credential in a chat or attached file. Enter credentials only in the
> protected fields in **API Keys** or the connection screen for that service.

## Start the Conversation

1. **Open Chats.** Select **Chats** in the sidebar. The view contains the
   **History** list and a message field. If voice is still getting ready, you
   can already use text chat once the app is connected.

2. **Open an empty chat view.** Select **New chat**. The messages on screen
   clear. Jarvis creates a saved text thread when you send the first message,
   so an untouched empty view does not add an entry to History. **New chat**
   does not currently clear the Brain's recent in-memory context, so make the
   first prompt self-contained when you change topics.

3. **Send one clear request.** Type a request such as `Create a simple
   three-step plan for a quiet weekend.` Press **Enter** or choose the
   paper-plane button labeled **Send**. Use **Shift+Enter** to add a new line
   without sending.

4. **Watch for the visible result.** Your message appears in the conversation.
   While Jarvis works, the status area may show thinking or a short progress
   step. The completed answer then appears below the assistant name.

5. **Ask a follow-up.** Send `Make the second step suitable for rainy weather.`
   Jarvis uses its recent in-memory context, so you do not need to repeat the
   original plan during this run. The first sent message creates the text
   thread. Completed assistant or system replies are added to its saved
   transcript.

## Add Useful Context

You can drop a file, image, PDF, selected text, or link before your next
message. Chats does not currently have a keyboard-accessible attachment picker.

1. Drag the item over the assistant bar, mascot, or lower-right drop target. A
   recognized drag expands the target and shows **Drop to brief** followed by
   your assistant's name.
2. Release the item and look for **Added to conversation**.
3. Send a request that says what to do with it, such as `Summarize the attached
   notes in five bullets.` Jarvis does not answer the drop by itself; it waits
   for this real request.

- Supported text files are decoded as UTF-8. Jarvis keeps at most about 8,000
  characters from each text item and 12,000 characters across the context note.
- Jarvis tries to extract text from a PDF. If extraction is unavailable or the
  PDF is unreadable, it keeps only basic file details.
- An image is sent with the next real request. A Brain provider with image
  support can inspect it; a text-only provider receives only the context note.
- Other file types contribute their name, media type, and size, not their full
  contents. A dragged link is added as text and is not fetched by the drop.
- The files in one drop can total up to 25 MB. The smaller text-context limits
  above still apply.

> [!note] A dropped item lives in the running Brain's memory, not in a saved
> attachment library or transcript. An image is used once, with your next
> request. Opening a saved conversation replaces remembered dropped text with
> that conversation's saved messages, and restarting clears all dropped
> context. Attach the source again when you need it later.

## Reopen the Chat

1. Select **New chat** to clear the visible conversation.
2. Find the newest **Text** entry under **History**. Entries are grouped as
   **Today**, **Yesterday**, or **Earlier**.
3. Select the text conversation. Its saved assistant or system replies return,
   and Jarvis uses those saved messages as context for the next turn.

**Current saving limitation:** typed prompts are not written to saved text
threads. Dropped context, progress steps, and thought disclosures are not saved
there either. After reopening, your prompt is missing and the title usually
remains **New Chat**. Repeat essential details before continuing and keep
important source instructions in your own notes. Text threads are stored
locally; the app removes threads that have been inactive for more than 365 days
when it starts.

You can also select a **Voice** entry to read its transcript. On a desktop with
voice available, **Speak in this conversation** starts a voice session with the
saved transcript as context. In other modes, Jarvis shows **Voice isn't
available in this mode**. If you type after opening a voice entry, Jarvis starts
a new text thread instead of changing the original voice record.

## How It Fits Together

1. **Chats receives your request.** Typing and dictation send text through the
   same chat path. Opening a saved conversation first replaces the Brain's
   recent context with that conversation's saved messages.
2. **The Brain provider prepares the answer.** It can use recent messages and
   any context you just dropped. If the preferred provider fails, Jarvis tries
   other reachable Brain providers available to this installation. If none can
   answer, it reports that the Brain is unavailable.
3. **Tools and safety checks can join the turn.** A request may call a tool or
   ask for confirmation before an action. A simple answer returns directly to
   the chat.
4. **Longer work can move to Jarvis-Agents.** You follow that work separately,
   and generated files appear in **Outputs**. Dragging an Outputs card to the
   assistant adds the card's task details, status, and available summary as a
   new chat turn. It does not attach the generated files themselves.
5. **History and run details serve different needs.** History is the quick way to
   reopen saved text replies and voice conversations. **Transcription** and
   **Run Inspector** show a more detailed voice transcript or run trace when
   you need to understand how a result was produced.

## Check That It Works

1. Start a new chat and send `Reply only with: chat is ready.`
2. Confirm that your message and a matching reply appear in the conversation.
3. Select **New chat**, then open the newest **Text** entry from **History**.

The live chat is working when both messages appear before you leave it. Saving
is working when the reply returns after reopening. The test prompt does not
return because typed prompts are not currently stored in text threads.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| The message field says **Starting…** or **Offline** | The app is still connecting, or its connection is unavailable | Wait for startup to finish. If it stays offline, restart the app normally and check the status again. |
| Your message appears but no answer arrives, or the thinking status stops | A Brain provider or requested tool may be unavailable or may have timed out | Open **API Keys**, test the Brain providers, and activate one that shows **Works**. Then send one short, text-only request. |
| Dropping an item produces no answer | A drop adds context silently; it does not send a request | Keep the file total below 25 MB, wait for **Added to conversation**, then type what Jarvis should do with it. |
| Your prompt is missing after reopening a text chat | The current text-thread path stores the reply but not the typed prompt | Repeat the important context and keep a separate copy of instructions you need later. |
| A new chat refers to the previous topic | **New chat** cleared the screen but not the Brain's in-memory context | Begin with a self-contained prompt that says you are changing topics. Restart the app first when strict separation matters. |

## Next Steps

- Read [Chats](chats) to organize, continue, and delete text conversations and
  understand conversation context in more detail.
- Follow [Start Your First Voice Conversation](start-your-first-voice-conversation)
  to move from typing to microphone input safely.
- Use [Sessions and Run Inspector](sessions-and-run-inspector) when you need a
  voice transcript or a closer look at how a run behaved.
- Read [Outputs and Files](outputs-and-files) to find, preview, and reopen files
  created by longer Jarvis-Agent work.
