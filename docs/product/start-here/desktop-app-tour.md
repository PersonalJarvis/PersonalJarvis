---
title: "Tour the Desktop App"
slug: desktop-app-tour
summary: "Learn where conversations, agents, tools, history, settings, and help live in the desktop app."
section: "Start here"
section_order: 1
order: 4
diataxis: explanation
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [desktop-app, navigation, chats, agents, tools, settings, help]
related: [start-your-first-chat, chats, find-help-in-the-app]
---

The desktop app puts conversations, jobs, tools, knowledge, results, and
settings in one window. Sidebar rows change the main area while shared status
stays visible.

Start in **Chats**. Several other rows are hubs, with related features behind
tabs instead of separate sidebar entries.

## Read the Shared App Frame

The sidebar shows your assistant name and voice state: **Starting**, **Voice
starting**, **Ready**, **Listening**, **Thinking**, **Speaking**, **Error**, or
**Offline**. The box below follows the latest transcript. Text can work while
voice warms.

The Agents area, personal instructions file, and Voice hub adapt to your chosen
name. This guide calls them **Agents**, **Instructions**, and **Voice**.

The bottom card shows the Pipeline **Brain** or **Realtime** provider and model.
Select it to open **API Keys**; during a Realtime call it follows the provider
actually serving the session.

Drag the sidebar divider to resize it. When it becomes an icon rail, hover for
labels; double-click the divider to restore its width.

The top bar provides **Restart** and, when available, an update action. Restart
needs confirmation and warns about active missions. Unlike navigation, it can
interrupt voice or terminal panes while they reconnect or resume.

## Understand Health and Attention Cues

| Cue | Meaning | Where it leads |
|---|---|---|
| Spinner beside the assistant name | App or voice is warming | Use text, or wait for **Ready** before speaking |
| Red dot on **API Keys** | A configured provider is failing | Open its tab, then test or replace it |
| Amber dot on **Skills, Plugins & MCPs** | A plugin needs reconnection | Open its **Plugins** tab and notice |
| Amber dot inside an API Keys tab | The selected capability still needs setup | Connect one suitable provider for that category |
| Red dot inside an API Keys tab | The capability is configured but not working | Read the card error and run its test |
| **Coding mode ON/OFF** | A workspace may or may not shape all replies | Select it to return to **Agentic IDE** |

Permission, input-isolation, and voice-warming notices describe host or device
conditions. Follow the action shown in the notice.

## Choose an Area by Goal

| Area | Use it for |
|---|---|
| **Chats** | Start, speak into, or reopen a conversation and attach context. |
| **Agents** | Follow delegated goals, questions, tool activity, and results. |
| **Skills, Plugins & MCPs** | Manage instructions, service connections, and Model Context Protocol servers. |
| **CLIs & CLI Test Hub** | Manage command-line tools and run capability checks. |
| **Tasks** | Create and review scheduled or persistent work and its status. |
| **Transcription** | Read recent voice sessions turn by turn. |
| **Run Inspector** | Inspect a recorded run's timing, decisions, tools, and errors. |
| **Board** | See a local activity, task, and tool-use summary. |
| **Wiki** | Browse either the Normal Wiki or UltraWiki knowledge experience. |
| **Contacts**, **Profile**, and **Instructions** | Store people, facts, memory, and standing preferences. |
| **Docs** | Search guides, related pages, and the current contents list. |
| **API Keys** | Choose providers, Pipeline or Realtime, the Control Key, and advanced connections. |
| **Settings** | Change language, audio, wake behavior, appearance, permissions, and app behavior. |
| **Voice** | Open Dictation, Dictionary, Shortcuts, Language, and speech-input keys. |
| **Outputs** | Find reports and files produced by chats, tools, or Agents. |
| **Socials** and **Feedback** | Open project links or send a problem report or suggestion. |
| **Agentic IDE** | Open coding workspaces with live agent terminals. |

## Use the Consolidated Hubs

Large hubs use a top tab bar:

- **Skills, Plugins & MCPs** has matching **Skills**, **Plugins**, and **MCPs**
  tabs. Read [Plugins](plugins), or [Connect Home
  Assistant](connect-home-assistant) for a self-hosted example.
- **CLIs & CLI Test Hub** manages connections under **CLIs** and checks them
  under **CLI Test Hub**.
- **Voice** has **Dictation**, **Dictionary**, **Shortcuts**, **Language**, and
  speech-input **API Keys**. Full provider setup remains in the main **API
  Keys** area. See [Dictation](dictation).
- **API Keys** changes provider tabs with Pipeline or Realtime and also holds
  Tool Model, Agents, the Control Key, and **Advanced**. See [Use Local AI
  Providers](local-ai-providers).
- **Settings** now includes language choices and on-screen overlay controls
  that previously had separate sidebar entries.

**Wiki** has one mode switch. Normal shows the vault tree, pages, graph, and
backlinks. Ultra replaces it with **Overview**, **Explore**, **People**, **Ask**,
**Sources**, **Contents**, and **Settings**. Switching does not delete either
store. Read [Wiki and Memory](wiki-and-memory) or [Use UltraWiki](ultrawiki).

## Keep an Agentic IDE Workspace Alive

The Agentic IDE asks for a folder and terminal layout. After its first visit it
is **sticky**: navigating elsewhere hides rather than closes it. Returning
restores its panes without another setup flow. Navigation is not a stop control;
stop or close work inside the IDE.

**Coding mode ON** makes replies from Chats, voice, and other areas use the
active workspace. **OFF** can coexist with running terminals. The badge only
navigates; change the mode inside the IDE. Read [Agentic IDE](agentic-ide).

## How It Fits Together

1. Start a request in Chats, by voice, or from a feature control.
2. The active provider supplies reasoning, while relevant skills, plugins,
   MCPs, CLIs, or app commands supply capabilities.
3. Short work returns to the conversation. Longer delegated work appears in
   Agents; scheduled work appears in Tasks.
4. Voice turns appear in Transcription, diagnostic runs in Run Inspector, and
   created files in Outputs.
5. Wiki, Contacts, Profile, and Instructions provide reusable context. API Keys
   and Settings shape the same request path without becoming part of a chat.

## Check That It Works

1. Select **Skills, Plugins & MCPs** and switch through its three tabs.
2. Select **Voice** and confirm that Dictation, Dictionary, Shortcuts, Language,
   and API Keys appear in one hub.
3. Open **Wiki** and confirm that its current Normal or Ultra mode is named.
4. Select **Docs**, then return to **Chats**. The assistant name and status
   should remain visible throughout.
5. If an IDE workspace is already open, switch away and back once. Its panes
   should return without another setup flow.

These checks do not send a provider request or change a setting.

## Troubleshooting

| What you see | What it means | What to do |
|---|---|---|
| Sidebar labels disappeared | The sidebar snapped to its icon rail | Hover for labels, drag the divider right, or double-click it. |
| **Voice starting** remains for a long time | The local service is connected, but voice did not finish warming | Keep using text; open Settings for device status, then restart if it never becomes ready. |
| **Offline** | The window lost its local backend connection | Wait briefly, then use the confirmed **Restart** action or reopen the app. |
| A feature is missing from the sidebar | It may now be a tab in Voice, Extensions, CLI, API Keys, or Settings | Open the matching consolidated hub and inspect its top tabs. |
| Wiki says it is checking the mode | The backend has not yet confirmed Normal or Ultra | Wait for the retry; use the shown retry action if the check stalls. |
| Replies unexpectedly focus on code | Focused coding mode is still on | Select the coding-mode badge, review the workspace, and turn the mode off there. |
| An IDE terminal is still running after leaving the page | Sticky navigation hid the workspace instead of closing it | Return to Agentic IDE and stop or close the pane deliberately. |
| Docs or another area stays empty | Its view did not finish loading or the backend is unavailable | Switch once, wait briefly, then use Restart if multiple areas remain empty. |

## Next Steps

- Follow [Start Your First Chat](start-your-first-chat).
- Read [Chats](chats) for conversation history and attachments.
- Use [Find Help in the App](find-help-in-the-app) to search the documentation.
- Open [Agentic IDE](agentic-ide) before creating a coding workspace.
