---
title: "Complete First-Run Setup"
slug: first-run-setup
summary: "Setup runs inside the app. Agree to the terms, add one key on the API Keys page, pick a wake word in Settings, then take the tour."
section: "Start here"
section_order: 1
order: 3
diataxis: tutorial
status: active
owner: maintainers
last_reviewed: 2026-09-30
phase: "-"
audience: end-user
tags: [setup, onboarding, tour, language, permissions, microphone, wake-word, providers]
related: [providers-and-api-keys, audio-and-wake-word, permissions, start-your-first-chat]
---

First-run setup happens inside the real app. There is no separate setup
screen: the window dims and a small card with the mascot walks you to the
places where each thing is really set, and waits there. Everything else is
explained afterwards by a short tour of the app.

## Before You Start

- Open the installed desktop app. The first launch may take several seconds.
- Have one API key ready (OpenAI or Google Gemini is the shortest path), or run
  Ollama with one installed model for a keyless Brain.
- On macOS, launch the signed app from its application bundle before granting
  access; permissions belong to that exact app identity.

> [!warning] Paste a provider credential only into the masked key field on the
> **API Keys** page. Never paste one into chat, speak it, put it in a wake word,
> add it to configuration, or include it in a screenshot.

## Complete the Setup

While setup runs, only the highlighted part of the app and the card can be
used. The dots on the card show where you are. **Back** returns to the
previous step, never behind the agreement. If the window reloads, setup
reopens on the step you were on.

### 1. Agree to the terms

The first card lists what the assistant does on this computer: it runs
commands and changes files, can see your screen when asked, sends what you say
to the provider you choose, is billed by that provider, and can make mistakes.

1. Pick **English**, **Deutsch** or **Español** on the card if you want
   another interface language. You can change it later under **Settings >
   Languages**.
2. Optionally open **Read the full Terms of Use**.
3. Tick the agreement, then select **Agree and continue**. **Decline and quit**
   closes the app without saving anything; setup asks again next time.

### 2. Add one API key

Setup opens the **API Keys** page and highlights it. Paste one key into a
provider card and save it:

- An **OpenAI** or **Gemini** key is enough on its own: setup points live voice
  and its thinking model at it and confirms **Connected**.
- Any other provider's key becomes the Brain when none is active yet.
- For a keyless start, turn on **Local Mode** on the same page and use Ollama.

**Continue** unlocks once a key is saved. **I'll add a key later** moves on;
chat and voice then stay off until a key exists.

### 3. Allow access on this Mac (macOS only)

Setup opens **Settings > Privacy permissions**. Use **Allow** or **Open
Settings** on each row you want, return, and wait for the row to update. The
restart at the end applies the grants. Windows and Linux skip this step.

### 4. Choose your wake word

Setup opens **Settings** at the **Wake Word** group. **Hey** is fixed; type
your own word after it and save. The word also becomes the assistant's name,
for example **Hey Nova** makes an assistant called Nova. The card confirms when
the wake word is on. Without a wake word, the Call keyboard shortcut starts a
conversation.

### 5. All set

The last card reads back the active Brain and how voice starts, and offers
**Start at login** if your system supports it. **Start** saves the setup and
restarts the app once so every choice takes effect together.

## Take the Tour

After the restart the app opens with a short guided tour. It dims the window,
lights up one part of the real interface at a time, and explains it in a small
card: the voice bar, a new chat, the agents and their world, Voice, Artifacts,
the Agentic IDE, Plugins and the Marketplace, and Settings.

- **Next** moves on; clicking the highlighted part yourself does the same.
- The tour navigates by itself where needed (into the agents' world and back)
  and ends on the home screen. It never starts a call or any work for you.
- **Skip tour** or **Escape** ends it at any point.
- Replay it anytime under **Settings > App > App tour**.

## Recover a Skipped or Deferred Choice

- Change the interface and reply languages under **Settings > Languages**.
- Connect and test models under **API Keys**; the same page connects coding
  agents by key or subscription.
- Repair macOS access under **Settings > Privacy permissions**.
- Change the phrase, spoken wake language, activation switch, or local wake
  pack under **Settings > Wake Word**, and the Call shortcut under **Settings >
  Voice Keybinds**.
- Change login startup under **Settings > App** where supported.

## How It Fits Together

1. The agreement is the only consent moment; the installer asks nothing.
2. Setup never has its own screens: each step uses the page you will use
   later, so what you learn on day one is where things live.
3. One key is enough to start. A starter plan points live voice and its
   thinking model at the same key; any other single Brain key works too.
4. The wake phrase supplies both local activation and the assistant's name.
   The Call shortcut starts voice without an always-listening wake engine.
5. Permissions allow an operating-system capability; they do not approve a
   later Computer Use action or bypass its safety check.
6. One restart at the end applies every choice; the tour runs once after it.

## Check That It Works

1. After **Start**, confirm the app reopens and the tour begins.
2. Open **API Keys**, select **Test** on the active Brain card, and look for
   **Works**.
3. Start a new chat and send a harmless message. Confirm a reply arrives.
4. For voice, press the Call shortcut or say your wake word.

On a headless system there is no restart; verify text chat or the Control API.
Desktop-only features report their limits rather than prevent startup.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Continue** stays disabled on the key step | No key was saved yet, or saving failed | Read the line under the key field, fix the key, or choose **I'll add a key later** |
| A card on the API Keys page reports a failing key | The provider refused the key or the account has no credit | Fix the account at the provider, or save a key from another provider |
| Microphone test reports quiet, missing, or blocked | The input has no usable signal | Check OS access and **Settings > Audio devices** |
| Saved wake word does not respond | Its local model, language, microphone, or activation switch is not ready | Use the Call shortcut; under **Settings > Wake Word**, install the offered model and run **Test wake word** |
| The tour does not appear after the restart | The app opened in the Agentic IDE, or the tour was already seen | Leave the IDE, or replay it under **Settings > App** |
| App does not reopen | The restart could not start a fresh process | Open the app; setup was already saved |
| First-run setup returns every launch | The completion state is not read from the same writable data location | Follow [Troubleshooting](troubleshooting) for data-directory and version checks |

## Next Steps

- Follow [Start Your First Chat](start-your-first-chat) for a safe first test.
- Read [Providers and API Keys](providers-and-api-keys) before connecting a
  cloud account or changing fallbacks.
- Read [Local AI Providers](local-ai-providers) for Ollama setup and limits.
- Use [Audio and Wake Word](audio-and-wake-word) to finish microphone, wake
  language, activation, or shortcut setup.
- Review [Permissions](permissions) before enabling Computer Use or global
  shortcuts on a new operating system.
