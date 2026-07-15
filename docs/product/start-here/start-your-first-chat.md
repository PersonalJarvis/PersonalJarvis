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
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [chat, conversations, attachments, history, context]
related: [chats, start-your-first-voice-conversation, sessions-and-run-inspector]
---

Use **Chats** when you want an answer now and a conversation you can build on.
In this tutorial, you will send a request, add useful context, and see exactly
what the current app saves when you leave the conversation.

The basic flow is short: **you send a message -> Jarvis shows its progress ->
the reply appears -> a text conversation appears in History**. The live chat
shows both sides of the conversation. The current saved transcript is more
limited: assistant and system replies are saved, but your incoming text may be
missing when you reopen the conversation.

## Before You Start

- Finish first-run setup and open the main app.
- In **API Keys**, make sure at least one Brain provider shows **Works**. A
  provider is the service that answers your request.
- Choose a harmless example topic, such as planning a meal or organizing a
  study session.

> [!warning] Never put a password, API key, access token, recovery code, or
> other credential in a chat or attached file. Enter credentials only in the
> protected fields in **API Keys** or the connection screen for that service.

## Start the Conversation

1. **Open Chats.** Select **Chats** in the sidebar. You see **History** on one
   side and the message field at the bottom. If voice is still getting ready,
   you can already use text chat.

2. **Create a clean conversation.** Select **New chat**. The conversation area
   clears. Jarvis creates the saved text conversation when you send its first
   message, so an untouched empty chat does not clutter History.

3. **Send one clear request.** Type a request such as `Create a simple
   three-step plan for a quiet weekend.` Press **Enter** or choose the
   paper-plane **Send** button. Use **Shift+Enter** when you want a new line
   without sending.

4. **Watch for the visible result.** Your message appears in the conversation.
   While Jarvis works, the status area may show thinking or a short progress
   step. The completed answer then appears below the assistant name.

5. **Ask a follow-up.** Send `Make the second step suitable for rainy weather.`
   Jarvis uses the current live conversation, so you do not need to repeat the
   original plan while that conversation remains open. The first sent message
   creates the text conversation; completed assistant or system replies are
   added to its saved transcript.

## Add Useful Context

You can add a file, image, PDF, selected text, or link before your next
message.

1. Drag the item into the Jarvis window. A drop target appears with **Drop to
   brief** followed by your assistant's name.
2. Release the item and look for **Added to conversation**.
3. Send a request that says what to do with it, such as `Summarize the attached
   notes in five bullets.` Jarvis does not answer the drop by itself; it waits
   for this real request.

Text and readable PDF content can be included as context. A compatible Brain
provider can also use an image with the next request. For another file type,
Jarvis may initially receive only basic details such as its name and type. A
single drop can contain up to 25 MB in total.

> [!note] A dropped item is context, not a stored attachment library. It does
> not appear as a file inside the saved transcript. Attach it again after
> reopening or switching conversations when you still need its contents.

## Reopen the Chat

1. Select **New chat** to leave the current conversation.
2. Find the newest **Text** entry under **History**. Entries are grouped as
   **Today**, **Yesterday**, or **Earlier**.
3. Select the text conversation. Its saved assistant or system replies return,
   and you can send another message.

**Current saving limitation:** the live chat shows the prompt you sent, but
incoming user text may not currently be added to the saved text thread even
though assistant and system replies are. After reopening, your prompt can be
missing and the title can remain **New Chat**. Repeat any essential details
before continuing, and keep important source instructions in your own notes
until this is fixed.

You can also select a voice entry to read its transcript. **Speak in this
conversation** continues it by voice when the desktop voice feature is ready.
If you type after opening a voice entry, Jarvis carries recent context into a
new text conversation rather than changing the original voice record.

## How It Fits Together

1. **Chats receives your request.** Typing, dictation, and a resumed
   conversation all use the active conversation context.
2. **The Brain provider prepares the answer.** It can use recent messages and
   any context you just dropped. If the preferred provider is unavailable,
   Jarvis can try another compatible provider you configured; otherwise it
   shows that the Brain is unavailable.
3. **Tools and safety checks can join the turn.** A request may call a tool or
   ask for confirmation before an action. A simple answer returns directly to
   the chat.
4. **Longer work can move to Jarvis-Agents.** You follow that work separately,
   and generated files appear in **Outputs**. Dragging a completed Outputs card
   into the Jarvis drop target brings its result back into the conversation for
   discussion.
5. **History and Sessions serve different needs.** History is the quick way to
   reopen saved text replies and voice conversations. **Sessions** and **Run
   Inspector** show a more detailed voice transcript or run trace when you need
   to understand how a result was produced.

## Check That It Works

1. Start a new chat and send `Reply with the words: chat is ready.`
2. Confirm that your message and a matching reply appear in the conversation.
3. Select **New chat**, then open the newest **Text** entry from **History**.

The live chat is working when both messages appear before you leave it. The
saved reply is available when it returns after reopening; your test prompt may
not return because of the limitation above.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| The message field says **Starting** or **Offline** | The app is still connecting, or its local connection is unavailable | Wait for startup to finish. If it stays offline, restart the app normally and check the visible status again. |
| Your message appears but no answer arrives | The Brain provider may be disconnected, unavailable, or out of usage allowance | Open **API Keys**, select **Test**, and choose another compatible provider family if one is ready. |
| The thinking status stops without a reply | The provider or a requested tool may have timed out | Send one short, text-only request. If that works, retry the original request without optional tools or context. |
| Dropping a file seems to do nothing | The item was empty, too large, or not accepted, or you have not sent the follow-up request yet | Keep the total below 25 MB, wait for **Added to conversation**, then type what Jarvis should do with it. |
| Your prompt is missing after reopening a text chat | The current version stored the assistant reply but not your user prompt | Repeat the important context and keep a separate copy of any instruction you need later. |

## Next Steps

- Read [Chats](chats) to organize, continue, and delete text conversations and
  understand conversation context in more detail.
- Follow [Start Your First Voice Conversation](start-your-first-voice-conversation)
  to move from typing to microphone input safely.
- Use [Sessions and Run Inspector](sessions-and-run-inspector) when you need a
  voice transcript or a closer look at how a run behaved.
- Read [Outputs and Files](outputs-and-files) to find, preview, and reopen files
  created by longer Jarvis-Agent work.
