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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [desktop-app, navigation, chats, agents, tools, settings, help]
related: [start-your-first-chat, chats, find-help-in-the-app]
---

The desktop app keeps conversations, longer jobs, connected tools, saved
results, and settings in one place. Choose an area in the sidebar, and the main
area changes to match it.

You do not need to learn every area before you begin. Start in **Chats**, then
open another area when you want to follow longer work, find an earlier result,
or change how your assistant behaves.

Two sidebar labels use your assistant's name. If the assistant is named Nova,
the agent view appears as **Nova-Agents** and the personal instructions view as
**Nova.md**. This guide calls them **Agents** and **Instructions**.

## Know What Jarvis Is Doing

The header beside your assistant's name shows **Starting…**, **Voice
starting…**, **Ready**, **Listening**, **Thinking**, **Speaking**, **Error**, or
**Offline**. The box below shows a live transcript while your assistant hears
speech. The header and transcript box stay visible when you change areas.

The card at the bottom of the sidebar is labeled **Brain** in Pipeline mode and
**Realtime** in Realtime mode. It shows the provider and model selected for
that mode. During a live Realtime call, it follows the provider and model that
are actually serving the call. A provider is a service or local program that
handles part of a request. Select the card to open **API Keys & Providers**.

The top bar always shows **Restart**. Select it once to reveal **Confirm
restart?**, then select it again to continue. If an agent mission is running,
the app blocks the first attempt and offers **Restart anyway?**. On a headless
host that cannot relaunch a desktop app, the action reports **Restart failed**.

A managed installation shows **Update available** when a newer published
release is ready. If an update was downloaded but the restart did not finish,
the button changes to **Finish update**. Manual clones and development
checkouts do not show either update action.

## Choose an Area by Goal

**MCP** means Model Context Protocol, a standard that lets a server offer tools
or data to your assistant. A **CLI**, or command-line interface, is a program
your assistant can run through text commands.

| What you want to do | Open | What you find |
|---|---|---|
| Ask, dictate, or continue a conversation | **Chats** | The current conversation, **New chat**, and **History** |
| Follow a longer delegated job | **Agents**, named for your assistant | Active and recent goals, status, and tool activity |
| Add abilities or connections | **Skills, Plugins & MCPs** or **CLIs & CLI Test Hub** | Skills, optional connections, command-line tools, and their test hub |
| Plan work that runs now or later | **Tasks** | Scheduled, running, completed, and interrupted tasks |
| Find earlier activity | **Chats**, **Transcription**, **Run Inspector**, or **Board** | Conversation history, spoken turns, run details, or activity summaries |
| Give your assistant useful context | **Wiki**, **Contacts**, **Profile**, or **Instructions** | Notes, contacts, profile facts, and personal instructions |
| Connect services or change behavior | **API Keys**, **Settings**, or **Dictionary** | Providers, voice and app preferences, safety choices, and speech-recognition corrections |
| Retrieve created files | **Outputs** | Reports and other files written by your assistant or its agents |
| Learn more or contact the project | **Docs**, **Socials**, or **Feedback** | Guides, community links, and a way to report a problem or suggest an idea |

### Find the Right Kind of History

The app separates history by purpose so one long list does not mix unrelated
information:

- Open **Chats > History** to continue an earlier conversation.
- Open **Transcription** to read recent voice sessions turn by turn.
- Open **Run Inspector** to review one attempt to complete a request, including
  its timeline, tools, timing, decisions, and errors.
- Open **Outputs** when the result is a file rather than a chat reply.
- Open **Board** for an overview of your activity over time.

**Transcription** and **Run Inspector** only contain data captured while their
recorders are enabled. Each view says when its recorder is unavailable.

## How It Fits Together

1. **You start in Chats.** Type a message, use **Speak**, or continue a saved
   conversation from **History**.
2. **The request follows its input path.** Typed requests and Pipeline voice
   use the **Brain** provider. Realtime voice uses the provider shown on the
   **Realtime** card. A short request can return directly to the conversation.
3. **Longer work can move to an agent.** Follow its goal and status in the
   assistant-named **Agents** view while you continue using the rest of the
   app.
4. **Available tools support the work.** Skills, plugins, MCP connections, and
   CLIs can add actions or information. Jarvis asks for confirmation when an
   action reaches a safety boundary.
5. **The result returns to the useful place.** A reply stays in **Chats**, a
   recorded voice session appears in **Transcription**, and a created file
   appears in **Outputs**. When run recording is enabled, **Run Inspector**
   provides more detail about a particular run.
6. **Settings and connections shape the same flow.** **API Keys** chooses the
   services that are available; **Settings** controls everyday behavior. If a
   preferred service is unavailable, your assistant can try another compatible
   provider that is ready. If none is available, the relevant view or message
   explains what needs attention.

Open **Docs** whenever you need a guide for one part of this flow. It includes
search, related-page links, and a contents list for the page you are reading.

## Check That It Works

1. Select **Docs** in the sidebar. Confirm that the documentation sidebar and
   either the overview or a selected guide appear.
2. Select **Chats**. Confirm that the main area changes to the chat view and
   shows **New chat** and **History**.
3. Check that the assistant name and voice state remain visible in the
   sidebar.

These steps check the navigation and shared status area without sending a
request. They do not require voice, a cloud provider, or a connected tool.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Voice starting…** beside the assistant name | Voice is still warming up | Wait for **Ready**. You can use text chat while voice starts. |
| **Offline** beside the assistant name | The app is not connected to its local service | Wait briefly. If it does not recover, select **Restart**, then **Confirm restart?**. If that reports **Restart failed**, reopen the desktop app or start the server in the usual way. |
| A red dot beside **API Keys** | A configured provider is not working | Open **API Keys**, review the affected category, and test or choose another available provider. |
| An amber dot beside **Skills, Plugins & MCPs** | A connected plugin needs to sign in again | Select the row to open **Plugins**, then follow the reconnect notice. |
| A documentation page is hard to find | Its title or section may be different from the feature label | Open **Docs** and press **Ctrl+K** on Windows or Linux, or **Command+K** on macOS, then search for the feature or visible error. |

## Next Steps

- Follow [Start Your First Chat](start-your-first-chat) to send a simple
  request and see where the conversation is saved.
- Read [Chats](chats) to organize conversations, add files, and continue
  earlier work.
- Use [Find Help in the App](find-help-in-the-app) to search the documentation
  and move between guides, troubleshooting, and reference pages.
