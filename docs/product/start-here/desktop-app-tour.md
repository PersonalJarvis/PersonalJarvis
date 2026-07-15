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
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [desktop-app, navigation, chats, jarvis-agents, tools, settings, help]
related: [start-your-first-chat, chats, find-help-in-the-app]
---

The desktop app keeps conversations, longer jobs, connected tools, saved
results, and settings in one place. Use the sidebar as your map: choose what
you want to do, and the main area changes to match that goal.

You do not need to learn every area before you begin. Start in **Chats**, then
open another area when you want to follow longer work, find an earlier result,
or change how Jarvis behaves.

## Know What Jarvis Is Doing

The area beside your assistant's name shows the current voice state, such as
**Ready**, **Listening**, **Thinking**, **Speaking**, **Voice starting…**, or
**Offline**. Below it, you may see the words Jarvis currently hears. These
signals stay visible while you move between areas.

The **Brain** card at the bottom of the sidebar shows the provider currently
used for answers. A provider is the artificial intelligence service that
handles a request. Select the card to open **API Keys & Providers** and review
the active choice.

The top bar contains **Restart** on supported desktop installs. **Update
available** appears there only when a managed installation has a newer
release. Jarvis asks again before restarting and warns when active
Jarvis-Agents would be interrupted.

## Choose an Area by Goal

**MCP** means Model Context Protocol, a way for an external service to offer
tools to Jarvis. A **CLI**, or command-line interface, is a program Jarvis can
run through text commands.

| What you want to do | Open | What you find |
|---|---|---|
| Ask, dictate, or continue a conversation | **Chats** | The current conversation, **New chat**, and **History** |
| Follow a longer delegated job | **Jarvis-Agents** | Active and completed Jarvis-Agent goals and their status |
| Add abilities or connections | **Skills, Plugins & MCPs** or **CLIs & CLI Test Hub** | Built-in skills, optional connections, and command-line tools |
| Plan work that runs now or later | **Tasks** | Scheduled, running, completed, and interrupted tasks |
| Find earlier activity | **Chats**, **Transcription**, **Run Inspector**, or **Board** | Conversation history, spoken turns, run details, or activity summaries |
| Give Jarvis useful context | **Wiki**, **Contacts**, **Profile**, or the `.md` page named after your assistant | Notes, people, preferences, and assistant instructions |
| Connect services or change behavior | **API Keys**, **Settings**, or **Dictionary** | Providers, voice and app preferences, safety choices, and speech-recognition corrections |
| Retrieve created files | **Outputs** | Reports and other files written by Jarvis or Jarvis-Agents |
| Learn more or contact the project | **Docs**, **Socials**, or **Feedback** | Guides, community links, and a way to report a problem or suggest an idea |

### Find the Right Kind of History

Jarvis separates history by purpose so one long list does not mix unrelated
information:

- Open **Chats > History** to continue an earlier conversation.
- Open **Transcription** to read recent voice sessions turn by turn.
- Open **Run Inspector** to review one attempt to complete a request, including
  its timeline, tools, timing, decisions, and errors.
- Open **Outputs** when the result is a file rather than a chat reply.
- Open **Board** for an overview of your activity over time.

## How It Fits Together

1. **You start in Chats.** Type a message, use **Speak**, or continue a saved
   conversation from **History**.
2. **The active Brain provider handles the request.** A short request can
   return directly to the conversation.
3. **Longer work can move to a Jarvis-Agent.** Follow its goal and status in
   **Jarvis-Agents** while you continue using the rest of the app.
4. **Available tools support the work.** Skills, plugins, MCP connections, and
   CLIs can add actions or information. Jarvis asks for confirmation when an
   action reaches a safety boundary.
5. **The result returns to the useful place.** A reply stays in **Chats**, a
   spoken exchange appears in **Transcription**, and a created file appears in
   **Outputs**. **Run Inspector** provides more detail when you need to
   understand a particular run.
6. **Settings and connections shape the same flow.** **API Keys** chooses the
   services that are available; **Settings** controls everyday behavior. If a
   preferred service is unavailable, Jarvis can use another compatible choice
   you configured. Otherwise, the affected area shows what needs attention.

Open **Docs** whenever you need a guide for one part of this flow. It includes
search, related-page links, and a contents list for the page you are reading.

## Check That It Works

1. Select **Docs** in the sidebar. Confirm that the documentation overview or
   document list appears in the main area.
2. Select **Chats**. Confirm that the main area changes to the chat view and
   shows **New chat** and **History**.
3. Check that the assistant name and voice state remain visible in the
   sidebar.

This confirms that the app navigation and shared status area are working. It
does not require voice, an online provider, or a connected tool.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Voice starting…** beside the assistant name | Voice is still warming up | Wait for **Ready**. You can use text chat while voice starts. |
| **Offline** beside the assistant name | The app has not connected to its local service | Wait briefly. If every area remains unavailable, use **Restart** and confirm once. |
| A red dot beside **API Keys** | A configured provider is not working | Open **API Keys**, review the affected category, and test or choose another available provider. |
| An amber dot beside **Skills, Plugins & MCPs** | A plugin connection needs attention | Open that area and use the reconnect notice in **Plugins** to identify and restore the connection. |
| A documentation page is hard to find | Its title or section may be different from the feature label | Open **Docs** and press **Ctrl+K** on Windows or Linux, or **Command+K** on macOS, then search for the feature or visible error. |

## Next Steps

- Follow [Start Your First Chat](start-your-first-chat) to send a simple
  request and see where the conversation is saved.
- Read [Chats](chats) to organize conversations, add files, and continue
  earlier work.
- Use [Find Help in the App](find-help-in-the-app) to search the documentation
  and move between guides, troubleshooting, and reference pages.
