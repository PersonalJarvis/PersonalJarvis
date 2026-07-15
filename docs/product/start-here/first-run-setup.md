---
title: "Complete First-Run Setup"
slug: first-run-setup
summary: "Walk through language, permissions, microphone, wake word, and provider setup without exposing credentials."
section: "Start here"
section_order: 1
order: 3
diataxis: tutorial
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [setup, onboarding, language, permissions, microphone, wake-word, providers]
related: [providers-and-api-keys, audio-and-wake-word, permissions, start-your-first-chat]
---

First-run setup prepares Jarvis for the way you want to use it. You choose
the app and reply languages, review system permissions, choose how to start a
voice request, check your microphone, and learn where to connect a provider.

You can finish without enabling voice or connecting an online service. Text
and local features that do not need the skipped capability remain available,
and you can complete the other choices later in Settings.

## Before You Start

- Install and open the desktop app.
- Have a microphone available only if you want to use voice.
- If you plan to use an online provider, get its credential from the
  provider's official account page. A provider is the service that answers a
  request or handles part of a voice conversation.

> [!warning] Enter a provider credential only in **API Keys & Providers**.
> Never paste it into chat, speak it to Jarvis, include it in a screenshot, or
> add it to a settings file.

## Setup at a Glance

| Stage | What you choose | What becomes ready |
|---|---|---|
| Language | Interface and reply languages | Menus and answers use your preference |
| Permissions | Only the access your features need | Voice, shortcuts, and screen control can work |
| Activation | Wake word or keyboard shortcut | A way to begin a voice request |
| Provider | One or more services for the features you use | Chat and provider-backed voice features |
| Finish | Optional start-at-login setting | The main Jarvis app |

## Complete the Setup

### 1. Choose your languages

1. On **Welcome to Personal Jarvis**, select **Get started**.
2. Under **Interface language**, choose the language used by menus, buttons,
   and settings.
3. Under **Reply language**, choose a specific language or **Auto**. Auto lets
   Jarvis follow the language of your conversation.
4. Select **Next**. The interface updates to the selected language.

These are separate choices. Changing the interface does not force every
answer into that language.

### 2. Review system permissions

Jarvis asks only for operating-system access that supports features you may
use. On macOS, the setup lists permissions such as **Microphone**, **Screen
Recording**, **Accessibility**, **Input Monitoring**, and **Input control**.
Select **Allow** or **Open Settings** for the capabilities you want, then
return to Jarvis and wait for their status to change to **Allowed**.

If the app shows **Restart now**, use it before relying on newly granted
access. On Windows and Linux, setup may instead report that no extra desktop
privacy permissions are required.

Select **Continue** when the page is ready. If you do not want to grant access
now, select **Continue with text only**. Voice input, global shortcuts, or
Computer Use may remain unavailable until you grant the related permission.

### 3. Choose how to activate voice

You have two first-class choices:

- **Wake word** keeps the local activation listener ready for a phrase you
  choose.
- **Keyboard shortcut** uses push-to-talk instead. You can choose the exact
  shortcut later under **Settings > Voice Keybinds**.

If you choose **Keyboard shortcut**, review the note and select **Continue**.
This avoids an always-ready wake listener and does not require a wake-word
model.

If you choose **Wake word**:

1. Enter the part that follows the fixed word **Hey**.
2. Confirm that you are responsible for the word you choose.
3. Select **Test your microphone** and speak during the short listening
   window. You can also select **Say your wake word once** for the same level
   check with a more specific prompt.
4. Look for **Sounds good**. If the result says the microphone is too quiet,
   no device was found, or permission is required, fix that issue or continue
   and return later.
5. Select **Save wake word**.

Some custom words need the optional local speech pack. If Jarvis cannot yet
support the word, it offers to install that pack. You may also select
**Continue anyway**; setup finishes, but the wake word will not be usable
until a suitable local engine is available. Use the keyboard shortcut or text
in the meantime.

### 4. Finish onboarding

The **Set up API keys after onboarding** page explains where provider setup
lives; it does not ask you to enter a credential. Select **Continue
onboarding**.

On the final page, enable **Start Jarvis automatically at login** if the
option is available and you want it. Select **Get started** to open the main
app. You can do this even when permissions, microphone setup, wake word, or
providers are still incomplete.

### 5. Connect only the providers you need

1. In the main sidebar, open **API Keys**. The page title is **API Keys &
   Providers**.
2. For text chat, open **Brain** and choose a provider. For voice, choose one
   of the two modes shown at the top of the page:

   | Voice mode | What it uses | When to choose it |
   |---|---|---|
   | Realtime | One compatible service listens and replies in a live audio stream | You want the shortest voice setup and accept that it is a research preview |
   | Pipeline | Separate Voice Input, Brain, and Voice Output choices | You want to choose each stage independently |

3. Open the provider card. Use **Get your key here** if you need the
   provider's official account page.
4. Enter the credential in that card's protected field and select **Save**.
5. Select **Test**. **Works** means the provider answered successfully. If the
   card is not marked **Active**, activate it for that category.

You do not need to fill every credential field. Connect only the categories
you intend to use. If you skip this step, features that require an online
provider stay unavailable until you return.

## How It Fits Together

1. You type a message, say a wake word, or use push-to-talk to start a
   request.
2. Language settings decide how the interface appears and which language
   Jarvis uses for the answer. Microphone and shortcut permissions allow the
   selected input method to reach Jarvis.
3. A connected provider handles the Brain response. In Pipeline voice mode,
   the Voice Input provider first turns speech into text and the Voice Output
   provider turns the answer back into speech. Realtime mode handles those
   stages in one live audio connection.
4. Computer Use and other actions may need additional system permission and
   can still ask for a safety confirmation. Setup permission does not approve
   every future action.
5. If the preferred provider is unavailable, Jarvis can use another compatible
   provider you have configured. If none is ready, the affected feature shows
   that setup or attention is required; text and local paths remain available
   when they do not depend on that provider.

This is why permissions, activation, providers, chat, and voice are separate
choices: each can fail or be changed without forcing you to redo the others.

## Check That It Works

Open **Chats**, start a new chat, and send a short greeting. Setup is working
when your message appears and Jarvis returns an answer in the reply language
you selected. If you enabled voice, say the saved wake phrase or use your
push-to-talk shortcut and confirm that the listening state appears.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Continue** is unavailable on the permissions page | A required macOS permission or restart is still pending | Use **Allow** or **Open Settings**, return to Jarvis, and restart when prompted; or choose **Continue with text only** |
| The microphone check says it is too quiet or finds no device | The wrong input is selected, its level is low, or no microphone is available | Check the operating-system input device and level, then try again; continue with text if needed |
| The microphone check says permission is required | Jarvis cannot read the microphone | Grant **Microphone** access in Permissions, then run the check again |
| The wake phrase is saved but does not activate Jarvis | The local speech pack is missing, the wake listener is off, or the microphone is unavailable | Use push-to-talk, then review **Settings > Wake Word** and install the offered local pack if needed |
| A provider says **Configured** but requests fail | A saved credential is present, but the account, model, quota, or service may not be working | Select **Test**, follow its result, and try another compatible provider family if one is available |
| Menus and answers use different languages | Interface and reply language are independent settings | Review both language choices and use **Auto** only when you want replies to follow the conversation |

## Next Steps

- Read [Providers and API Keys](providers-and-api-keys) to understand provider
  categories, tests, fallback choices, and safe credential management.
- Read [Audio and Wake Word](audio-and-wake-word) to tune your microphone,
  activation phrase, local speech pack, and push-to-talk behavior.
- Read [Permissions](permissions) before enabling voice, shortcuts, or
  Computer Use on a new operating system.
- Follow [Start Your First Chat](start-your-first-chat) for a simple first
  conversation and the signs that your Brain provider is ready.
