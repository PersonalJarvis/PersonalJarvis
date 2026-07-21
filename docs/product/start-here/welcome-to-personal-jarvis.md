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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [overview, getting-started, privacy, safety]
related: [install-personal-jarvis, desktop-app-tour, start-your-first-chat]
---

Personal Jarvis is an open-source assistant, which means its source code is public. You can use the same app in a desktop window or a web browser. Type a request, or speak when voice is set up. Chats, longer agent tasks, tools, saved files, and optional service connections all live in the app.

Jarvis works with several artificial intelligence providers. A provider is a service or local program that handles part of a request. You choose which supported options to connect and what they power. Features that use a cloud service, microphone, screen, or desktop control need the relevant connection, hardware, and permission.

## Choose Where to Begin

Start with the part that matches what you want to accomplish.

The labels below match the English interface. The **Agents** label follows your assistant name. For example, an assistant named Nova appears as **Nova-Agents**.

| Your goal | Where to begin | What you get |
|---|---|---|
| Ask a question or develop an idea | **Chats** | A conversation saved in **History** |
| Speak instead of type | Voice controls in **Chats** or the sidebar | A transcript and, when voice output is available, a spoken reply |
| Delegate longer work | The assistant-named **Agents** item | Live progress; saved results appear in **Outputs** |
| Find a file created by Jarvis | **Outputs** | Generated reports and other saved deliverables |
| Connect or change a provider | **API Keys** | Connection controls, provider choices, and current status |

The sidebar is the main map of both the desktop and browser app. It also opens areas such as **Skills, Plugins & MCPs**, **Tasks**, **Transcription**, **Wiki**, **Contacts**, **Settings**, **Docs**, and **Feedback**.

## What Stays Under Your Control

- **Provider choice:** Jarvis does not bundle paid provider access. For a cloud provider, connect your own key or supported subscription login. Local options may require separate software instead.
- **Permissions:** Microphone, screen, file, and accessibility access are required only by features that need them.
- **Approvals:** Jarvis checks actions before they run. Some actions run directly, some require your confirmation, and disallowed actions stay blocked.
- **Connections:** Optional tools and services add capabilities. If you do not connect one, the rest of the app can still use the capabilities that are available.
- **Information sharing:** A connected provider or service may receive the content needed to complete a request. Review its privacy terms before sending sensitive information.

> [!warning] Never send a password, access token, or other credential through chat or voice. Add credentials only through the app's **API Keys** view or the connection screen for that service.

## What Jarvis Does Not Do

Jarvis does not guarantee that every answer or completed task is correct. Review important facts, generated files, and actions that affect your accounts or computer.

It also does not bypass operating-system permissions, safety checks, or missing hardware. Voice, computer control, phone calls, and third-party connections work only when their requirements are available and configured.

The core app supports Windows, macOS, and Linux, including a browser interface for headless servers. A feature that needs local audio, a display, or desktop integration may be unavailable on a headless device. Text chat still requires a running Jarvis backend and a reachable brain provider.

## How It Fits Together

One request can move through several parts of Jarvis:

1. **You start the request** in a chat, through voice, from a task, or from another app feature.
2. **Jarvis checks what the request needs.** It uses a configured provider with the required capability. If the preferred provider is unavailable, Jarvis can try a suitable provider from another configured family.
3. **Jarvis chooses a path.** A simple question can return directly to the conversation. Longer work can go to an agent, while an action may use a connected tool or service.
4. **Safety checks apply before action.** Jarvis runs permitted low-risk steps, asks you to review actions that need approval, and blocks disallowed actions.
5. **The result returns to the relevant place.** You see a reply in the conversation, progress in the assistant-named **Agents** view, or a file in **Outputs**.
6. **Jarvis reports what it cannot use.** It can use a suitable alternative when one is configured. If it cannot continue, it identifies the unavailable provider, permission, connection, or device capability.

The [desktop app tour](desktop-app-tour) explains where each of these areas lives. The [first chat guide](start-your-first-chat) walks through the shortest complete request.

## Check That It Works

After you install the app and finish its setup:

1. Open **Chats** from the sidebar.
2. Type `What can you help me do?` and press Enter.
3. Confirm that your message and Jarvis's reply appear in the same conversation.

That visible reply confirms that the app, chat view, and a reachable brain provider can complete a basic request. It does not test optional voice, tool, or computer-control features.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Starting…** or **Voice starting…** appears below the assistant name | The backend or voice service is still warming up | If the chat input is disabled, wait for it to become available. If it is enabled, you can type while voice warms up. Speak only after the status changes to **Ready**. |
| **Offline** appears below the assistant name | The app cannot reach the Jarvis backend | Check that Jarvis is still running. If the status does not recover, close and reopen the app using your normal launcher. |
| Your message sends but no answer appears | No suitable configured brain provider completed the request | Open **API Keys**, review the **Brain** status, and connect or choose a reachable provider. |
| A feature is missing or unavailable | Its service, permission, software, or hardware is not ready on this device | Open the matching view or **Settings**, read the shown status, and complete the listed requirement. |
| Jarvis asks for confirmation | The action reached a safety boundary | Review the exact action and approve it only if you understand and want the effect. |

If a problem continues, open **Docs** in the sidebar and search for the feature name or the visible error message.

## Next Steps

- [Install Personal Jarvis](install-personal-jarvis) to set up the app on a supported computer.
- [Tour the Desktop App](desktop-app-tour) to learn what each sidebar area is for.
- [Start Your First Chat](start-your-first-chat) to send a request and understand where the conversation is saved.
