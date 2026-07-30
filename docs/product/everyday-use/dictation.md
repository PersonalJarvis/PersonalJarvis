---
title: "Dictate Into Any App"
slug: dictation
summary: "Turn speech into text in the focused field, recover missed words, and choose what stays local or uses a provider."
section: "Everyday use"
section_order: 2
order: 8
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [dictation, speech-recognition, microphone, shortcuts, privacy, accessibility]
related: [voice-conversations, speech-dictionary, providers-and-api-keys, privacy-and-local-data]
---

Dictation turns speech into text at the cursor in desktop apps and Personal
Jarvis. It does not ask the assistant to answer. If insertion is blocked, your
transcript stays on the clipboard and in **Recent dictations**.

## Before You Start

- Keep the Personal Jarvis desktop app running and allow microphone access.
- In your assistant-named **Voice** section, choose **API Keys** and select a
  ready **Voice input** provider.
- Place the cursor in an editable field. Password and protected fields may
  block automatic typing.

## Use Dictation

1. Open the app and field where you want the words to appear, then place the
   cursor at the insertion point.
2. Hold **Push to talk** and speak. Release it when finished. The floating bar
   shows the recording and transcription state.
3. Wait while Jarvis applies dictionary corrections, safe cleanup, and any
   wording or translation setting you enabled.
4. Check the result at the cursor. If a notice says the text was copied to the
   clipboard, paste it manually into the same field.
5. Open **Voice > Dictation** to review both text versions and the outcome.

You can also select **Start dictating**. Keep a target field focused, or return
to it before stopping.

## Choose a Shortcut

Open your assistant-named **Voice** section and select **Shortcuts**. New
combinations save immediately.

| Shortcut | Behavior | Best for |
|---|---|---|
| **Push to talk** | Hold to record; release to finish and insert | Short and precise passages |
| **Hands-free** | Press once to start; press again to stop | Longer passages or accessibility |
| **Paste again** | Re-inserts the latest saved dictation without recording | Recovering from a wrong target |

If Push to talk behaves like a toggle, select **Switch to hold**. Known
shortcut collisions produce a warning.

**Paste again** needs local history. If insertion is blocked, it copies the text
for manual pasting.

## Understand Where the Text Goes

Inside Personal Jarvis, text goes to the focused or last-used editable field,
including a coding terminal. With no target, it remains in history.

For another app, Jarvis places the transcript on the clipboard, sends the
normal paste shortcut, then restores the previous plain-text value. Non-text
clipboard content may not be restorable.

If the system refuses automatic typing, Jarvis leaves the transcript on the
clipboard. History reports **Copied to clipboard** or **Could not insert**.

## Review and Recover Dictations

Each history row shows delivered text. When it changed, **Heard:** shows the
original recognition. Search checks both versions, and badges distinguish
insertion, clipboard fallback, and incomplete transcription.

- **Discard** crosses out an entry but keeps it recoverable.
- **Restore** reactivates a discarded entry. For a failed or partial entry with
  **Audio kept**, it transcribes the recording with current settings.
- **Delete permanently** removes the entry and saved audio.
- **Copy** copies the delivered version.

Restore may produce different words after a provider or language change. It
updates history but does not reinsert text into another app.

## Clean Up or Translate the Text

Open **Voice > Language** to choose the recognition language. Keep **Detect
automatically** unless recognition repeatedly guesses wrong. Wake-word and
assistant reply languages are separate.

**Clean up my wording** uses a text model for punctuation, capitalization,
sentence breaks, and self-corrections. The original stays in history. **Test**
uses a fixed sample and saves nothing. If the model fails or changes too much,
Jarvis uses the version from before that pass.

**Always write in one language** translates and cleans up in one request.
Translation starts off. If the model times out, text stays in the spoken
language.

> [!warning] A hosted recognition provider receives audio; a hosted wording
> provider receives the transcript. The Language tab names the chosen model.

Local Whisper or Nemotron keeps recognition audio on this computer; automatic
fallback does not upload it. Automatic wording also stays local and uses the
unpolished text if no local model answers. Choosing a cloud wording model is an
explicit exception.

## Retention and Deletion

History is local. Defaults keep up to 200 entries for up to 30 days. Successful
dictations do not keep microphone audio.

For partial, failed, empty, or cancelled results, recovery audio is kept only
when enabled. Defaults retain at most 20 recordings for 7 days. **Delete all**
irreversibly removes history, recovery audio, lifetime statistics, and streak.

## Operating-System Limits

| System | What can prevent automatic use | What happens instead |
|---|---|---|
| Windows | The target app runs as administrator while Jarvis does not | Text remains on the clipboard |
| macOS | Secure Input, a password field, or missing Accessibility/Input Monitoring access | Text remains on the clipboard; shortcuts may be unavailable |
| Linux X11 | The optional desktop hotkey support is not installed | Use the Dictation button; insertion can still work |
| Linux Wayland | Global shortcuts and cross-app synthetic typing are blocked by design | Use the button and paste from the clipboard |
| Headless host | No desktop microphone or insertion target exists | Dictation reports that it is unavailable |

## How It Fits Together

1. A shortcut or the Dictation button starts microphone capture.
2. The selected Voice input provider turns the audio into a raw transcript.
3. The Speech Dictionary and safe cleanup repair known terms and formatting.
4. Optional wording or translation produces the delivered version.
5. Jarvis inserts that version or preserves it on the clipboard.
6. Local history records both versions, the outcome, and recovery audio only
   after an incomplete result.

In a [Voice Conversation](voice-conversations), speech becomes a request and
the assistant replies or acts instead.

## Check That It Works

In a plain-text editor, place the cursor after a marker, hold **Push to talk**,
say one sentence, and release. The sentence should appear there and history
should show **Inserted**. An explained clipboard fallback also confirms that
recognition worked.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Not available on this computer** | No microphone or ready recognition provider | Check microphone permission, then **Voice > API Keys** |
| The global shortcut does nothing | The binding conflicts, lacks OS permission, or is unsupported on Wayland | Record another shortcut; use **Start dictating** meanwhile |
| **Copied to clipboard** | The active field rejected synthetic input | Paste manually and check the operating-system table above |
| **Partly transcribed** | Some audio exhausted recognition retries | If **Audio kept** appears, choose **Restore** after checking the provider |
| Wording or translation is skipped | No selected model answered safely in time | Run **Voice > Language > Test** or choose another ready provider |

For broader microphone, provider, and desktop checks, read
[Troubleshooting](troubleshooting).

## Next Steps

- Read [Speech Dictionary](speech-dictionary) to correct names and specialist
  words in future dictations.
- Use [Providers and API Keys](providers-and-api-keys) to compare local and
  hosted speech recognition without putting credentials into chat or voice.
- Review [Privacy and Local Data](privacy-and-local-data) for the wider storage
  and deletion model.
- Check [Platform Support](platform-support) before relying on global shortcuts
  or cross-app insertion on a new operating system.
