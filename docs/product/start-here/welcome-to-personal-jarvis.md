---
title: "Welcome to Personal Jarvis"
slug: welcome-to-personal-jarvis
summary: "Understand what Personal Jarvis can do, what stays under your control, and where to begin."
section: "Start here"
section_order: 1
order: 1
diataxis: explanation
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [overview, getting-started, privacy, safety]
related: [install-personal-jarvis, desktop-app-tour, start-your-first-chat]
---

Personal Jarvis is an open-source assistant you can use in its desktop window
or through its browser interface. You choose how it reasons, which services it
can reach, and which device permissions it receives.

You can begin with one working chat provider or a local model. Voice, connected
services, long-running Agents, knowledge, and computer control are optional
capabilities you can add when you need them.

Several visible labels follow the assistant name you chose, including the
Agents area and Voice hub. This guide uses the neutral words **assistant**,
**Agents**, and **Voice** so it applies to every name.

## What You Can Do Now

| Goal | What Personal Jarvis provides |
|---|---|
| Talk or type | Saved chats, Pipeline or Realtime voice conversations, and push-to-talk from the current conversation. |
| Dictate into another app | Local or hosted speech recognition, a personal dictionary, optional wording cleanup, and recent dictation history. |
| Build useful knowledge | A Normal Wiki for editable notes and an optional UltraWiki for approved sources, semantic search, people, exploration, and cited answers. |
| Use tools safely | App commands, plugins, MCP connections, command-line tools, and supported computer control, all subject to capability and safety checks. |
| Delegate longer work | Assistant-named Agents run larger missions separately, show progress, and return reviewed results or files. |
| Work with coding agents | Agentic IDE opens one or more live terminals in a chosen workspace and can focus assistant replies on that workspace. |
| Plan and retrieve work | Tasks hold scheduled or persistent work; Outputs collects reports and other created files. |

These surfaces are connected, but they are not one permission. Connecting a
Brain does not connect a smart home. Granting microphone access does not grant
screen control. Opening a coding workspace does not turn focused coding mode on
unless you choose it.

## Choose Local, Self-Hosted, or Cloud

Personal Jarvis does not make one provider load-bearing. You choose providers
for different jobs such as Brain reasoning, speech input, speech output,
Realtime voice, tool use, Agents, and knowledge processing.

- **Local:** Ollama can run the Brain; Whisper or Nemotron can recognize
  speech; Piper can speak Pipeline replies. These options need no provider API
  key, but use your computer's storage, memory, and processing time.
- **Self-hosted:** The Brain can connect to a trusted OpenAI-compatible server,
  and service plugins can reach servers you operate, such as Home Assistant.
  Data still crosses the network between the app host and that server.
- **Cloud:** Supported providers can supply stronger models or avoid local
  downloads. You bring your own key or supported subscription login and remain
  responsible for that provider's account, limits, cost, and privacy terms.

Read [Use Local AI Providers](local-ai-providers) before planning an offline
setup. “Local” applies to the selected call, not automatically to tools,
fallbacks, wording, or knowledge slots. If a local Brain fails, the app can use
another configured provider family; a hosted fallback may receive that
request. Keep only the paths you trust ready for sensitive work.

Local control means you own the app's records and can choose local inference;
it is not a claim that every enabled feature is offline.

## What Stays Under Your Control

- **Credentials:** Enter keys and tokens only in **API Keys** or the protected
  connection dialog that owns them. Never send a credential through chat,
  voice, a task, a skill, documentation, or a screenshot.
- **Permissions:** Microphone, screen, accessibility, files, and desktop input
  are requested only by features that need them. A missing permission should
  produce an honest limitation rather than a pretend success.
- **Approvals:** Actions use four safety levels: safe, monitor, ask, and block.
  Some low-risk work can run directly, some changes wait for confirmation, and
  blocked actions do not run. Review the exact target and effect before
  approving.
- **Connections:** Plugins and MCP servers add access to other services. Their
  service-side permissions remain an outer boundary even after connection.
- **Knowledge:** Normal Wiki files remain editable. UltraWiki reads only the
  sources you add and approve, and switching Wiki modes does not delete either
  store.

Much of the app's state—chats, Wiki data, task records, settings, and generated
files—lives on the app host. Content needed by a configured provider or
connected service can leave that host. Local data ownership and local inference
are related, but not identical.

## What It Does Not Do Automatically

Personal Jarvis does not include paid provider access, create third-party
accounts, or turn a chat message into secure credential setup. It does not need
every provider card filled in, and a missing optional integration should not
break unrelated features.

It does not guarantee that an answer, action, report, or code change is correct.
Review important facts and outputs. Read approvals rather than treating them as
routine prompts, especially for account changes, messages, files, computer
control, locks, heating, or other physical effects.

The assistant does not bypass operating-system permissions, service policies,
missing hardware, or unavailable models. It does not make every file, account,
or folder an UltraWiki source; add and approve the sources you want. Chat
history and other memory features keep their own records.

## Understand Platform Differences

The core app supports Windows, macOS, and Linux. A browser interface can reach
a headless installation, so text chat, API access, provider connections,
knowledge work, tasks, and suitable Agents do not require a desktop window.

Device features still need devices. Local wake word, microphone capture,
speakers, global shortcuts, screen context, and desktop control require the
matching hardware, permission, and supported operating-system capability. A
headless server with no display or audio should report those features as
unavailable while keeping its server-capable paths working.

Local models must also fit the host. A smaller CPU model may be slower or less
capable than a hosted model, while a large model can exceed available memory.
The provider card's readiness and live test are more useful than assumptions
about a particular GPU or operating system.

## Follow a Simple Reader Journey

1. [Install Personal Jarvis](install-personal-jarvis) for your platform and
   complete the in-app setup.
2. Choose one Brain path in **API Keys**. Use a provider you already trust or
   start with a key-free local option.
3. Follow [Start Your First Chat](start-your-first-chat) and confirm one visible
   reply before adding more capabilities.
4. Use [Tour the Desktop App](desktop-app-tour) to find Voice, Wiki, Tasks,
   Outputs, connections, and health indicators.
5. Add one feature at a time. Try [Dictation](dictation), [Use
   UltraWiki](ultrawiki), [Connect Home Assistant](connect-home-assistant), or
   [Agentic IDE](agentic-ide) only when it matches a real goal.

This order makes failures easier to understand: first prove chat, then test the
new provider, device, permission, or connection separately.

## How It Fits Together

1. You start a request in Chats, by voice, from a task, or through an app
   control.
2. The app identifies the required capabilities and offers only providers and
   tools that are ready and relevant.
3. A short request can return directly. Longer work can move to an Agent; a
   coding request can use an Agentic IDE workspace when focused coding mode is
   enabled.
4. Safety rules inspect proposed actions before execution. Connected tools act
   only within their own permissions.
5. Results return to Chats, the Agents view, task history, Wiki, or Outputs.
   Failures name the missing provider, permission, device, or connection rather
   than claiming success.

## Check That It Works

After setup:

1. Open **Chats**.
2. Send `Reply with: setup check complete.`
3. Confirm that the reply appears in the same conversation.
4. Open **API Keys** and confirm that the active Brain has no red health dot.

This verifies the basic chat path only. Test voice, local models, tools,
connections, Agents, and the IDE separately before relying on them.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Starting** or **Voice starting** | The backend or voice path is warming | Use text when available and wait for **Ready** before speaking. |
| **Offline** | The window lost its local backend connection | Wait briefly, then use the confirmed Restart action or reopen the app. |
| Message sends but no answer arrives | No suitable Brain completed the request | Open **API Keys**, inspect the Brain health, and test or choose another ready provider. |
| A feature says setup is required | Its provider, connection, permission, model, or device is missing | Open the named area and complete only that requirement. |
| The assistant asks for confirmation | The action reached an ask-level safety boundary | Check the target and effect; reject it if either is unexpected. |
| A local choice still uses the network | Another provider slot, fallback, tool, or connected service is hosted | Review all active provider and connection choices for that exact feature. |
| A desktop feature is unavailable on a server | The host has no usable display, audio device, or desktop session | Use the server-capable path or move that feature to a desktop device. |

If a problem continues, open **Docs** and search for the feature name or the
visible error.

## Next Steps

- [Install Personal Jarvis](install-personal-jarvis).
- [Tour the Desktop App](desktop-app-tour).
- [Start Your First Chat](start-your-first-chat).
