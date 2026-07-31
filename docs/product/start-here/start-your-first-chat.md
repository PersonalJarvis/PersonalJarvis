---
title: "Start Your First Chat"
slug: start-your-first-chat
summary: "Send a first message, dictate a draft, add bounded context, and reopen what the app saved."
section: "Start here"
section_order: 1
order: 5
diataxis: tutorial
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [chat, conversations, attachments, dictation, history, context]
related: [chats, dictation, outputs-and-files, sessions-and-run-inspector]
---

Use **Chats** for quick answers and follow-ups. This tutorial sends text, turns
speech into a draft, adds harmless context, and reopens what was saved.

The live conversation and saved thread are not identical. The current text-
thread path restores assistant and system messages, but not the prompts you
typed. Dropped context and thinking details are also temporary.

## Before You Start

- Complete first-run setup and open the main app.
- Under **API Keys & Providers > Brain**, select **Test** on a local or
  connected provider. Continue when it says **Works**.
- For the chat microphone, choose a ready **Voice input** provider and mic.
- Use a harmless example, such as planning a quiet weekend.

> [!warning] Never put a password, provider credential, access token, recovery
> code, or private key in chat or an attached file. Use only the protected
> credential fields for the service concerned.

## Send the First Message

1. Select **Chats**. **Getting ready** means voice is warming while text may
   already work. **Starting...** or **Offline** means the live connection is not
   ready.

2. Select **New chat**. An untouched empty chat is not saved. The first sent
   message creates its text thread.

3. Enter `Create a simple three-step plan for a quiet weekend.` Press **Enter**
   or select **Send**. Use **Shift+Enter** for a new line without sending.

4. Confirm your message appears. The status shows **Thinking** or the current
   step. The reply appears under your configured assistant name. A collapsible
   **Thought for...** may summarize observed steps; it is not saved history.

5. Send `Make the second step suitable for rainy weather.` The running Brain
   uses its recent context, so this follow-up can refer to the earlier plan.

**New chat** clears the screen, not the Brain's recent in-memory context. Use a
self-contained new prompt, or restart first when strict separation matters.

## Use the Chat Microphone

The microphone beside **Send** transcribes into this composer. It neither begins
a voice conversation nor sends automatically.

1. Put the caret in the message box, then select **Dictate**.
2. Speak during **Listening... speak now**. Interim words append to existing
   text.
3. Select **Stop dictation**. Review and edit the final transcript.
4. Select **Send** only when the draft says what you intend.

Do not confuse this button with system-wide **Dictation**:

| Chat microphone | System-wide Dictation |
|---|---|
| Started from the Chats composer | Started from **Voice > Dictation** or a configured shortcut |
| Produces a draft here | Inserts text at the focused field in this or another app |
| Waits for you to select **Send** before the Brain sees it | Types or copies text; it never asks the Brain to answer |
| Stops from the composer | Has history, recovery, statistics, language, polish, and translation settings |

System-wide Dictation can also land in the focused chat field. It remains a
draft until sent. See [Dictate Into Any App](dictation).

## Add a File or Other Context

Chats has no attachment picker. Drag a file, image, PDF, selected text, or link
over the floating bar, mascot, or lower-right target. After **Drop to brief**
shows your assistant's name, release it and wait for **Added to conversation**.
Then send an instruction such as `Summarize the attached notes in five bullets.`

The intake is deliberately bounded:

- One drop can contain at most 25 MB in total.
- Text is decoded as UTF-8 and shortened to about 8,000 characters per item and
  12,000 overall.
- PDFs use best-effort text extraction. If that fails, only file details are
  available.
- Images wait for the next request. A vision-capable Brain can inspect them;
  text-only providers receive the note.
- Other formats contribute name, media type, and size. A dragged link is added
  as text; the drop does not fetch its page.

Dropped material stays in the running Brain, not a saved attachment library.
Images are used once with the next request. Resuming a saved conversation or
restarting clears this temporary context, so attach it again when needed.

The active Brain or fallback may receive the prompt, extracted text, and image
bytes. A local drop is not necessarily local processing. See [Privacy and Local
Data](privacy-and-local-data).

## Save and Resume the Conversation

The first message creates a local thread. **History** groups entries under
**Today**, **Yesterday**, and **Earlier**. Open the newest **Text** entry to
resume; its saved messages replace recent Brain context.

> [!note] Typed prompts are not currently written into saved text threads.
> Dropped context, pre-acknowledgments, thinking progress, and thought summaries
> are also absent. After reopening, repeat the goal and any essential limits.
> The title may remain **New Chat** because the missing first prompt normally
> supplies the automatic title.

Assistant replies and system diagnostics are restored. Local text threads are
pruned at startup after 365 inactive days. **Delete** removes a text thread;
Chats does not offer it for voice sessions.

History also lists **Voice** sessions. Opening one restores its transcript;
typing creates a new text thread with that context. **Speak in this
conversation** starts voice from saved turns when available.

## Providers, Outputs, and Sessions

The sidebar shows the active Brain and model. If it fails, another compatible
configured family may be tried. If none answers, thinking ends with a system
diagnostic. Test provider health under **API Keys & Providers** before retrying.

A short answer stays in Chats. Delegated files appear under **Outputs**.
Dragging an output card starts a chat turn with its task status and summary,
not the generated files. **Sessions and Run Inspector** holds detailed run or
voice evidence.

## How It Fits Together

1. The composer turns typed or chat-mic text into one user request only after
   **Send**.
2. The Brain receives recent in-memory context plus any current dropped
   material. Provider fallback can change who processes that request.
3. Tools may add progress or request approval. Delegated files stay outside the
   transcript.
4. The live UI shows the complete current exchange. The local text thread saves
   only the currently supported message roles.
5. Resuming seeds the Brain from saved messages; starting voice seeds the voice
   path from the same normalized transcript.

## Check That It Works

1. Select **New chat** and send `Reply only with: chat is ready.`
2. Confirm both your prompt and **chat is ready** appear, with no thinking
   indicator left running.
3. Select **New chat**, open the newest **Text** entry, and confirm the assistant
   reply returns. The typed test prompt is expected to be absent.
4. Select **Dictate**, say a harmless short sentence, stop, and confirm it is an
   editable draft that is not sent until you choose **Send**.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| Composer says **Starting...** or **Offline** | The live connection is warming or unavailable | Wait for startup; if it remains offline, restart the app and check again |
| Your message appears but no reply arrives | A Brain, tool, or provider request failed or timed out | Test the Brain cards, then retry one short text-only request |
| Chat microphone records nothing | The microphone, permission, speech pipeline, or Voice input provider is unavailable | Check **Settings > Audio devices**, permissions, and the **Voice input** provider |
| Dictated words appear but no answer starts | Dictation creates text; it does not send a Brain request | Edit the draft and select **Send** |
| A drop produces no answer | Context was added silently, as designed | Wait for **Added to conversation**, then type what to do with it |
| Prompt or attachment is missing after resume | That material is outside the saved text-thread roles | Reattach the source and restate essential instructions |
| New chat mentions the old topic | Visible reset did not clear in-memory Brain history | Use a self-contained prompt or restart for a strict boundary |

## Next Steps

- Read [Chats](chats) for daily history, deletion, voice continuation, and
  context boundaries.
- Read [Dictate Into Any App](dictation) for shortcuts, insertion recovery,
  transcript history, and privacy choices.
- Follow [Start Your First Voice Conversation](start-your-first-voice-conversation)
  when you want a spoken exchange rather than an editable text draft.
- Use [Outputs and Files](outputs-and-files) and [Sessions and Run Inspector](sessions-and-run-inspector)
  to inspect delegated files and detailed run evidence.
