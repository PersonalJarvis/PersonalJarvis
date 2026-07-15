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
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [overview, getting-started, privacy, safety]
related: [install-personal-jarvis, desktop-app-tour, start-your-first-chat]
---

Personal Jarvis is an open-source assistant, which means its source code is public. You can use it through text, voice, or the browser. It brings conversations, longer tasks, tools, saved results, and optional connections into one app.

You decide which artificial intelligence provider - the service that answers your requests - to use, which optional features to connect, and which actions Jarvis may take. Some features need an online service, a permission, or a capability that is available only on certain computers.

## Choose Where to Begin

Start with the part that matches what you want to accomplish.

Jarvis-Agents are separate workers that handle longer jobs.

| Your goal | Where to begin | What you get |
|---|---|---|
| Ask a question or develop an idea | **Chats** | A conversation you can return to later |
| Speak instead of type | Voice controls in the app or browser | A spoken request and, when configured, a spoken reply |
| Delegate longer work | **Jarvis-Agents** | Progress you can follow and a reviewed result |
| Find a file created by Jarvis | **Outputs** | A list of generated reports and other deliverables |
| Add an artificial intelligence provider | **API Keys** | The provider and its current status |

The sidebar is the main map of the desktop app and also gives you direct access to tasks, sessions, the Wiki knowledge area, contacts, settings, documentation, and support.

## What Stays Under Your Control

- **Provider choice:** Jarvis does not include access to an artificial intelligence service. You connect a supported provider account or subscription and can change it later.
- **Permissions:** Microphone, screen, file, and accessibility access are required only by features that need them.
- **Approvals:** Actions with meaningful risk can require your confirmation. A blocked action stays blocked.
- **Connections:** Optional tools and services add capabilities. If you do not connect one, the rest of the app can still use the capabilities that are available.
- **Information sharing:** A connected provider or service may receive the content needed to complete a request. Review its privacy terms before sending sensitive information.

> [!warning] Never send a password, access token, or other credential through chat or voice. Add credentials only through the app's **API Keys** view or the connection screen for that service.

## What Jarvis Does Not Do

Jarvis does not guarantee that every answer or completed task is correct. Review important facts, generated files, and actions that affect your accounts or computer.

It also does not bypass operating-system permissions, safety checks, or missing hardware. Voice, computer control, phone calls, and third-party connections work only when their requirements are available and configured. Text chat and the browser interface remain useful even when a feature is unavailable on your device.

## How It Fits Together

One request can move through several parts of Jarvis:

1. **You start the request** in a chat, through voice, from a task, or from another app feature.
2. **Your selected provider interprets it.** It creates the response or helps decide what work is needed.
3. **Jarvis chooses a path.** A simple question can return directly to the conversation. Longer work can go to a Jarvis-Agent, while an action may use a connected tool or service.
4. **Safety checks apply before action.** Jarvis runs permitted low-risk steps, asks you to review actions that need approval, and blocks disallowed actions.
5. **The result returns to the relevant place.** You see a reply in the conversation, progress in Jarvis-Agents, or a file in Outputs.
6. **Jarvis shows unavailable parts.** It can use a suitable alternative when one is configured; otherwise it explains which provider, permission, connection, or device capability is unavailable.

The [desktop app tour](desktop-app-tour) explains where each of these areas lives. The [first chat guide](start-your-first-chat) walks through the shortest complete request.

## Check That It Works

After you install the app and finish its setup:

1. Open **Chats** from the sidebar.
2. Send: `What can you help me do?`
3. Confirm that your message and Jarvis's reply appear in the same conversation.

That visible reply confirms that the app, chat view, and selected provider can complete a basic request. It does not test optional voice, tool, or computer-control features.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Offline** or **Starting** near the assistant name | The app or voice service is still connecting | Wait for startup to finish. Try text chat while voice continues to warm up. |
| Your message sends but no answer appears | The selected provider may be unavailable or not connected | Open **API Keys**, review the provider status, and connect or choose another available provider. |
| A feature is missing or unavailable | Its service, permission, software, or hardware is not ready on this device | Open the matching view or **Settings**, read the shown status, and complete only the listed requirement. |
| Jarvis asks for confirmation | The action reached a safety boundary | Review the exact action and approve it only if you understand and want the effect. |

If a problem continues, open **Docs** in the sidebar and search for the feature name or the visible error message.

## Next Steps

- [Install Personal Jarvis](install-personal-jarvis) to set up the app on a supported computer.
- [Tour the Desktop App](desktop-app-tour) to learn what each sidebar area is for.
- [Start Your First Chat](start-your-first-chat) to send a request and understand where the conversation is saved.
