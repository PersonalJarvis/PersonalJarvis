---
title: "Skills"
slug: skills
summary: "Understand how a skill gives Jarvis repeatable instructions, when it runs, and how drafts differ from enabled skills."
section: "Extend and automate"
section_order: 5
order: 2
diataxis: explanation
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [skills, automation, triggers, safety, extensions]
related: [plugins, mcp-connections, jarvis-agents, safety-and-approvals]
---

A skill is a saved playbook for Jarvis. It describes how you want a recurring
kind of work handled, such as how to prepare a briefing, organize notes, or
review a file. When a request matches, Jarvis loads the playbook and follows it
with the capabilities already available in the app.

Skills are useful when the method matters as much as the result. They do not
create access to an app, add a missing action, or store a service credential.
Connect those capabilities separately, then let the skill explain when and how
to use them.

## Choose the Right Building Block

| Building block | What it gives Jarvis | Use it when |
|---|---|---|
| **Skill** | Repeatable instructions, optional triggers, and supporting files | You want Jarvis to follow your preferred method each time |
| **Command** | One defined action with known inputs, such as creating or listing something | You need a predictable operation that can be called from the app, command line, or conversation |
| **Plugin** | A packaged capability or integration | Jarvis does not yet have the action or service you need |
| **MCP connection** | Tools supplied by an external service through Model Context Protocol (MCP) | A skill needs to read from or act in a connected service |
| **Jarvis-Agent** | An isolated background worker for longer, reviewed work | The task needs several substantial steps or should continue outside the conversation |

A skill can tell Jarvis to use a command, plugin, or connected tool. A skill
marked for mission execution can hand its instructions to a Jarvis-Agent. The
skill remains the method; the other building block supplies the action or does
the work.

## Before You Start

- You can create and manage a skill without an artificial intelligence (AI)
  provider. AI-assisted drafting and catalog ranking use the active provider
  when one is reachable, then fall back to a basic editable draft or local
  search when it is not.
- Catalog installation needs an internet connection. A manually created skill
  does not.
- Connect any required service before testing the skill. Installing a skill
  does not install its plugin or create its connection.
- Keep credentials, private contact details, and other sensitive values out of
  a skill's name, description, trigger, and instructions. Enter credentials
  only in **API Keys** or the relevant connection screen.

> [!warning] A catalog label, source label, or star count is useful context,
> not a security guarantee. Read the source and the installed instructions
> before allowing a third-party skill to use connected tools.

## Find and Install a Skill

1. **Open Skills from the sidebar.** The left side lists built-in and personal
   skills. A switch shows whether a healthy skill is on; a draft shows an error
   instead of a switch.

2. **Open Skill Finder.** Describe the outcome you want. You can narrow the
   catalog by trust label, popularity, category, language, and stated risk.
   Search still works without an AI provider, although its ranking is then
   based on the catalog text rather than an AI comparison.

3. **Review a promising result.** Read its description, stated risk, and
   categories, then open **Source**. Check what the instructions ask Jarvis to
   do, which services they mention, and whether the source is one you trust.

4. **Choose Install.** A directly downloadable skill appears in the Skills
   list after installation. If the result says **Manual**, the app has not
   installed it; use **Source** to inspect the project and proceed only if you
   understand its separate installation steps.

5. **Check the installed state before using it.** A structurally valid skill
   normally appears as **Validated** with its switch on. A skill that explicitly
   declares another state keeps that state, while a file that cannot be read
   appears as **Draft** with a validation error. Switch an unfamiliar skill off
   while you review its complete definition.

6. **Select the skill and inspect its detail panel.** The main editor shows the
   complete `SKILL.md`: its settings followed by the instructions Jarvis will
   receive. A bundled skill can also include references, scripts, assets, or
   agent notes in the **Bundle** panel. Open only the files needed to understand
   what the skill will do.

The catalog refuses a second skill with the same name. Review the installed
copy before deleting it to make room for a replacement; deletion is permanent.

## Create Your Own Skill

1. **Open the new-skill form from the Skills header.** Describe the outcome in
   **What should this skill do?** You can ask AI to write a first version or
   fill in the form yourself.

2. **Wait for an editable result.** If the active provider answers, the form is
   filled with an AI draft. If no provider is reachable, Jarvis supplies a
   simple starter version instead. In both cases, you remain responsible for
   the text that will be saved.

3. **Review every field.** Give the skill a clear name, a specific description,
   and instructions that say what to do, what not to do, and what a useful
   result looks like. Jarvis uses the description to decide when the skill may
   help, so avoid broad claims such as “handles everything.”

4. **Add a voice trigger only when you need an exact shortcut.** Use a short,
   distinctive phrase and test it against ordinary conversation. The field is
   treated as a text pattern, so punctuation and special symbols can change how
   it matches. The current form does not offer schedule or hotkey setup.

5. **Choose Create skill.** A skill you wrote entirely yourself can become
   **Validated** and ready to use after this explicit submission. Content
   supplied by AI or by the automatic starter must instead be saved as
   **Draft**. A draft cannot run and has no on/off switch.

6. **Review and activate an AI-created draft separately.** Open the saved
   definition and check its name, description, triggers, instructions, and any
   requested capabilities. The current desktop can display and edit a deliberate
   draft but cannot promote it. After review, a trusted operator can activate it
   with `python -m jarvis.skills.cli --promote <skill-slug>`. That command checks
   the content before changing it to **Active**. Refresh Skills and confirm the
   new state before testing it.

The form refuses a skill with no real instructions. It also prevents a name
collision with an installed or built-in skill. Do not copy AI-written text into
the manual path merely to skip the draft boundary. If an AI-created skill
appears enabled immediately, switch it off and report the behavior as a bug
before running it.

## Review States and Changes

The switch uses two internal “on” states. They behave the same for everyday
use, even though their labels differ.

| State | Can it run? | What it means in the app |
|---|---|---|
| **Validated** | Yes | The definition could be read and is ready by default |
| **Active** | Yes | You explicitly switched it on |
| **Disabled** | No | You switched it off; that choice survives a restart |
| **Draft** | No | The definition needs a correction or is deliberately waiting for approval; no switch is shown |

Select a skill to review or edit its complete definition. **Save** replaces the
whole file and reads it again. If the settings or trigger are invalid, the
skill moves to **Draft** and the detail panel shows the validation error. Fix
the named problem and save again. A deliberate AI-created draft stays unable to
run until the separate promotion command completes. The desktop switch cannot
perform that promotion yet.

Built-in skills are protected from deletion, and editing one requires existing
admin access. You can still switch a built-in skill off without changing its
file. Personal skills can be deleted individually or in a confirmed batch.
Dragging rows changes only their display order; it does not change which skill
Jarvis prefers for a request.

## When a Skill Runs

An enabled skill can start in two common ways:

- **Request matching:** Jarvis sees the names and descriptions of enabled
  skills. When your chat or voice request fits one, Jarvis loads the full
  instructions before answering. Your wording does not need to repeat an exact
  trigger phrase.
- **Direct trigger:** A matching chat or voice phrase marks one skill for the
  next turn. This is useful for a dependable shortcut, but an overly broad
  pattern can also match conversation you did not intend as a command.

Only the short description is considered at first. The full instructions and
bundled references are loaded when needed, which keeps unrelated skills out of
the conversation. An **inline** skill guides the current turn. A **mission**
skill hands the instructions to a Jarvis-Agent; if no worker action is
available, Jarvis can fall back to following the instructions in the current
turn.

Scheduled triggers can exist in an installed skill, but they run only while
Jarvis and its skill scheduler are running. The current creator does not expose
schedule setup. Hotkey definitions can be displayed and validated, but the
current desktop runtime does not connect them to a live global-hotkey handler,
so do not rely on a skill hotkey as your only way to start important work.

## Safety During a Run

A skill supplies instructions; it does not bypass the normal action policy.
Connected actions still pass through the same safe, monitored, confirmation,
and blocked decisions used elsewhere in Jarvis. A blocked skill or action does
not run. An unattended scheduled action that needs a decision may stop because
no person is present to approve it.

If a skill names a tool that is not installed or a service that is not
connected, validation may show only a warning. During use, Jarvis should skip
the unavailable step or report that it could not complete it; the skill does
not silently gain the missing access. Read [Plugins](plugins) or [MCP
Connections](mcp-connections) before enabling instructions that depend on an
external capability.

## How It Fits Together

1. **A request or trigger starts the match.** Chat, voice, or an advanced
   schedule can point Jarvis to an enabled skill.
2. **The skill state is checked.** Validated and Active skills can continue;
   Draft and Disabled skills are rejected before their instructions load.
3. **Jarvis loads the playbook.** It renders the instructions with the current
   request and reads a bundled resource only when that resource is needed.
4. **Commands and connections supply actions.** The skill can guide Jarvis to
   use an app command, a plugin tool, or an MCP-connected tool, but it cannot
   create those capabilities by itself.
5. **Safety checks every action.** Permissions and approval rules apply after
   the skill is selected, just as they do for a normal conversation. Read
   [Safety and Approvals](safety-and-approvals) before automating changes.
6. **Inline work returns to the conversation.** Longer mission skills can
   delegate the same instructions to [Jarvis-Agents](jarvis-agents), where the
   worker, review, and durable outputs have their own lifecycle.

## Check That It Works

1. Create a personal skill manually, without AI drafting, named **Three Point
   Check** with the description
   “Turn a short topic into exactly three plain-English bullet points.”
2. In its instructions, tell Jarvis to return exactly three short bullets and
   finish with the words “Check complete.” Leave the voice trigger empty.
3. Choose **Create skill** and confirm that the new row appears with its switch
   on. Open it once and verify that the saved instructions match what you
   reviewed.
4. In Chats, ask Jarvis to use **Three Point Check** for a harmless topic.
   Success is a three-bullet answer ending with the requested words.
5. Switch the skill off. Its row should show **Off** after a refresh and remain
   off after restarting the app.
6. For the review boundary, ask AI to draft a second harmless skill and save it.
   Confirm that it appears as **Draft**, has no switch, and is unavailable to
   Chats until a separate promotion. If it appears enabled immediately, switch
   it off and report the bug instead of testing it.

The manual part proves creation, discovery, instruction loading, and the saved
on/off preference without sensitive data or an external connection. The final
step checks the separate safety boundary; drafting may use the active provider
or the automatic starter.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| **Could not load skills** | The skill service or app is not ready | Wait briefly, use Refresh, then restart the app if the error remains |
| A row says **Draft** and has no switch | Its settings, trigger, or file structure could not be accepted, or it was intentionally saved for review | Select it, read any validation error, and fix only the named issue. After reviewing an intentional draft, use the supported promotion command; the desktop switch cannot promote it yet. |
| An AI-created skill appears enabled immediately | The required draft boundary was bypassed | Switch it off, do not run it, and report the behavior with the app version and creation steps. Never include the skill's private content in a public report. |
| The switch is on but Jarvis ignores the skill | The description is too vague, the request does not match it, or the direct trigger is too strict | Make the description specific, try a clear request that names the skill, and test a simple distinctive trigger |
| A catalog installation fails | The download is unavailable, the network failed, or that name is already installed | Open **Source**, check the connection, and review the existing skill before deciding whether to delete it |
| AI drafting or ranking is unavailable | No compatible provider is reachable | Continue with the editable starter text or connect a provider in **API Keys**; manual creation and local catalog search still work |
| The skill starts but cannot finish an action | A required plugin, MCP connection, permission, or approval is missing | Connect the capability in the app, review its permissions, and retry; never put a credential in the skill |
| A scheduled skill does not run | Jarvis or the skill scheduler was not running, the schedule is invalid, or the action required interaction | Keep the app running, review the schedule, and use a supervised test before relying on it |
| A built-in skill cannot be deleted or saved | Built-ins are protected; saving also needs configured admin access | Switch it off, or create a personal skill with the behavior you want |

For repeated app, provider, or connection failures, follow the main
[Troubleshooting](troubleshooting) guide.

## Next Steps

- Read [Plugins](plugins) when the skill needs a capability Jarvis does not yet
  have.
- Read [MCP Connections](mcp-connections) to connect external tools without
  putting credentials in the skill.
- Read [Jarvis-Agents](jarvis-agents) to run a longer skill as an isolated,
  reviewed background mission.
- Review [Safety and Approvals](safety-and-approvals) before enabling a skill
  that changes files, accounts, or connected services.
