---
title: "Complete First-Run Setup"
slug: first-run-setup
summary: "Your pet explains the app, then you add one key, connect a subscription, pick a wake word and try ten first steps."
section: "Start here"
section_order: 1
order: 3
diataxis: tutorial
status: active
owner: maintainers
last_reviewed: 2026-10-01
phase: "-"
audience: end-user
tags: [setup, onboarding, tour, language, permissions, microphone, wake-word, providers]
related: [providers-and-api-keys, audio-and-wake-word, permissions, start-your-first-chat]
---

First-run setup happens inside the real app. There is no separate setup
screen: the window dims and your desktop pet (chosen under **Settings > My
Pets**) walks you to the places where each thing is really set, explains them
in a speech bubble, and waits there. A short tour and ten small first steps
follow and let you try the assistant for real.

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
previous step, never to the welcome. If the window reloads, setup reopens on
the step you were on.

### 1. Welcome

Pick **English**, **Deutsch** or **Español** on the card if you want another
interface language (you can change it later under **Settings > Languages**),
then select **Let's start**. There is nothing to agree to: the app is open
source. **Skip setup, I know my way around** ends the whole first-run guide
at once; it is recorded as done and the app restarts once.

### 2. How it works

Before anything is set up, the pet walks the real interface and explains how
everything runs through one assistant: you talk to it in the composer, it
thinks with an AI model, uses tools (your computer, the browser and connected
apps from **Plugins**), hands bigger jobs to **Agents**, works with coding
agents in the **Agentic IDE**, keeps results in **Artifacts**, remembers what
matters, and asks before anything with real consequences. **Next** and
**Back** move through it, the dots show where you are, **Skip** goes straight
to the keys. Nothing is changed while the pet explains.

### 3. Add one API key

Setup opens the **API Keys** page and highlights it. Paste one key into a
provider card and save it:

- An **OpenAI** or **Gemini** key is enough on its own and also brings live
  voice: setup points live voice and its thinking model at it and confirms
  **Connected**.
- Any other provider's key becomes the Brain when none is active yet. Chat
  works, and voice runs the classic way (speech to text, answer read aloud),
  a little slower. The card says which of the two you have.
- For a keyless start, turn on **Local Mode** on the same page and use Ollama.

Once a key is saved, the card points on to the subscriptions.

**Continue** unlocks once a key is saved. **I'll add a key later** moves on;
chat and voice then stay off until a key exists.

### 4. Connect a subscription for your agents

Setup switches the API Keys page to its **Agents** tab and scrolls to the
**Coding CLIs** rows. Agents do the bigger
background jobs, and they run best on a subscription you already pay for:
**Claude** (Pro or Max) or **ChatGPT** (Codex) are recommended. Select
**Connect** on that row; your browser opens once to sign in. A subscription is
a flat monthly price; without one, agents use your API key and each job is
billed per use.

The card lists every subscription that is signed in, and **Continue** unlocks
once there is one. **I'll connect one later** moves on.

### 5. Allow access on this Mac (macOS only)

Setup opens **Settings > Privacy permissions**. Use **Allow** or **Open
Settings** on each row you want, return, and wait for the row to update. The
restart at the end applies the grants. Windows and Linux skip this step.

### 6. Choose your wake word

Setup opens **Settings** at the **Wake Word** group. **Hey** is fixed; type
your own word after it and save. The word also becomes the assistant's name,
for example **Hey Nova** makes an assistant called Nova. The card confirms when
the wake word is on, and **Continue** unlocks once one is saved. Without a
wake word, choose **Skip, I'll use the Call shortcut**; the Call keyboard
shortcut then starts a conversation.

### 7. All set

The last card reads back the active Brain, the connected agent subscriptions
and how voice starts, and offers **Start at login** if your system supports
it. **Show me around** starts the tour.

## Take the Tour

The tour follows setup straight away. It dims the window, lights up one part
of the real interface at a time, and the pet explains it: the voice bar, a new
chat, the agents' world, Voice, and Settings. What the setup walk already
covered is not repeated.

- **Next** moves on; clicking the highlighted part yourself does the same.
- The tour navigates by itself where needed (into the agents' world and back)
  and ends on the home screen. It never starts a call or any work for you.
- **Skip tour** or **Escape** ends it at any point.
- When the tour ends (or is skipped), setup is saved and the app restarts once
  so every choice takes effect together.
- Do it all again anytime under **Settings > App > Do the onboarding again**.
  The replay starts at the pet's explanation and never restarts anything.

## Try the First Steps

After the first tour, the pet stays in the bottom-right corner with ten small
things to try for real: wake the assistant, ask a first question, let it use a
tool, let it look at your screen, teach it something, have it make an
artifact, hand a big job to an agent, meet your agents, connect an app, and
open the Agentic IDE.

- Each step shows one example. **Send it for me** sends exactly that message;
  it is a real request and bills your connected model like any other.
- The pet waits until the app reports that the step really happened, then
  explains what went on and where to see the result.
- **Skip this one** moves on; the arrow tucks the pet into a small pill; the
  cross closes the guide. It never blocks the app.
- Start it again under **Settings > App > First steps**.
- Stuck at any point? Ask the assistant; it can open any part of the app for
  you.

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

1. Nothing asks for consent: neither the installer nor setup.
2. Setup never has its own screens: each step uses the page you will use
   later, so what you learn on day one is where things live.
3. One key is enough to start. A starter plan points live voice and its
   thinking model at the same key; any other single Brain key works too.
4. The wake phrase supplies both local activation and the assistant's name.
   The Call shortcut starts voice without an always-listening wake engine.
5. Permissions allow an operating-system capability; they do not approve a
   later Computer Use action or bypass its safety check.
6. Setup and tour run as one flow; one restart after the tour applies every
   choice.

## Check That It Works

1. After the tour, confirm the app restarts and reopens on the home screen.
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
| The tour does not appear | The tour was already seen | Replay it under **Settings > App** |
| A first step never completes | The app did not report that action (for example no tool was needed for the answer) | Choose **Skip this one**, or try the example again |
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
