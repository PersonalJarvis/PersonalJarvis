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
last_reviewed: 2026-08-12
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
  when reachable and fall back to a basic editable draft or local search.
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

5. **Check the installed state before using it.** A valid file with no state
   appears as **Validated** with its switch on; a declared state is kept;
   invalid YAML or unsupported settings appear as **Draft** with an error.
   Switch an unfamiliar skill off while you review it.

6. **Select the skill and inspect its detail panel.** The editor shows the
   complete `SKILL.md`; the **Bundle** panel lists the references, scripts,
   assets, and agent notes in the skill folder (UTF-8 text previews only).

The catalog refuses a second skill with the same name. Review the installed
copy before deleting it to make room; deletion is permanent.

> [!note] A direct catalog install downloads only `SKILL.md`. It does not clone
> the source repository or download sibling bundle folders. A catalog result
> marked **Manual** only opens its source page; Jarvis does not install it.

## Import a Skill from a Folder

Jarvis only reads skills from its own skills directory. A skill written
elsewhere — for example in a coding agent's project folder such as
`.claude/skills/<name>/` — is a **separate skill system** with a similar file
format, and stays invisible to Jarvis until it is imported:

```bash
jarvis skills import <path-to-skill-folder>
```

The command copies the folder's `SKILL.md` and its bundle folders
(`references/`, `scripts/`, `assets/`, `agents/`) into the Jarvis skills
directory and reloads the registry. It also accepts an `http(s)` link to a raw
`SKILL.md` and then behaves like a catalog install.

Import rules:

- A folder import is treated like a manual install: a valid file with no
  declared state arrives as **Validated** (on); a declared state is kept.
- Instructions containing code with disallowed calls arrive as **Draft**, with
  the findings reported. Review before promotion, as with an AI-written draft.
- A name that collides with an installed or built-in skill is refused.
- An unsupported field from another agent's format fails the import with the
  exact validation error; remove it from the source file and import again.

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
   help, so avoid broad claims such as "handles everything."

4. **Add a voice trigger only when you need a direct shortcut.** Use a short,
   distinctive phrase and test it against ordinary conversation. Jarvis treats
   the field as a regular expression, so punctuation and special symbols can
   change what it matches. The current form does not offer schedule, language,
   or hotkey setup.

5. **Choose Create skill.** A skill you wrote entirely yourself can become
   **Validated** and ready to use after this explicit submission. Content
   supplied by AI or by the automatic starter must instead be saved as
   **Draft**. A draft cannot run and has no on/off switch.

6. **Review and activate an AI-created draft separately.** Open the saved
   definition and check its name, description, triggers, instructions, and any
   requested capabilities. The desktop can display and edit a draft but cannot
   promote it; after review, a trusted operator activates it with
   `python -m jarvis.skills.cli --promote <skill-slug>`, which checks the
   content before changing it to **Active**. Refresh Skills before testing it.

The form refuses a skill with no real instructions and a name that collides
with an installed or built-in skill. Do not copy AI-written text into the manual
path merely to skip the draft boundary. If an AI-created skill appears enabled
immediately, switch it off and report the behavior as a bug before running it.

## Review States and Changes

The switch uses two internal "on" states. They behave the same for everyday
use, even though their labels differ.

| State | Can it run? | What it means in the app |
|---|---|---|
| **Validated** | Yes | The definition could be read and is ready by default |
| **Active** | Yes | You explicitly switched it on |
| **Disabled** | No | You switched it off; that choice survives a restart |
| **Draft** | No | The definition needs a correction or is deliberately waiting for approval; no switch is shown |

Select a skill to review or edit its complete definition. **Save** replaces the
whole file and reads it again. Invalid YAML, unsupported fields, or a trigger
without its required value move the skill to **Draft** with an error; fix the
named problem and save again. Saving does not check whether a voice regular
expression compiles, a cron expression runs, or a named tool is installed, so
test those parts before relying on them. A deliberate AI-created draft stays
unable to run until the separate promotion command completes.

Built-in skills are protected from deletion and editing one requires admin
access, but you can still switch one off. Personal skills can be deleted
individually or in a confirmed batch. Dragging rows changes only their display
order, not which skill Jarvis prefers for a request.

### Use the command line

The `jarvis skills` commands can list and inspect skills, draft and commit an
AI-assisted skill, search and install from the catalog, switch a non-draft
skill on or off, and reload the registry. They require a running Jarvis API.
The curated command group does not edit or delete skills.

`jarvis skills enable <name>` cannot promote a **Draft**. After reviewing a
draft, use the separate local promotion command:

```bash
python -m jarvis.skills.cli --promote <skill-slug>
```

The promotion command checks the body for blocked code patterns, changes the
file to **Active**, and reloads the user-skill registry.

## When a Skill Runs

An enabled skill can start in two common ways:

- **Request matching:** Jarvis sees the names and descriptions of enabled
  skills. When your chat or voice request fits one, Jarvis loads the full
  instructions before answering. Your wording does not need to repeat an exact
  trigger phrase.
- **Direct trigger:** A matching chat or voice phrase selects one skill for the
  current turn. This is useful for a dependable shortcut, but an overly broad
  pattern can also match conversation you did not intend as a command.

Jarvis exposes the name and description of enabled skills within a bounded
list and text budget, then loads the selected skill's instruction body for the
turn; bundle files are not included automatically. An **inline** skill guides
the current turn. A **mission** skill hands the instructions to a Jarvis-Agent;
if worker dispatch is unavailable or fails, Jarvis follows them inline instead.

The creator writes a voice trigger without a language list, which currently
defaults to German and English. Add `es` to the trigger's `language` list if it
must match spoken Spanish. Chat matching uses the current text without that
spoken-language filter.

Scheduled triggers can exist in an installed skill, but the scheduler starts
with the voice pipeline, so they do not run in a headless API-only session or
while Jarvis is stopped. Hotkey definitions can be stored and displayed, but no
live skill-hotkey handler is connected to them; do not rely on one to start
work.

### Skills paired with plugins

Most marketplace plugins ship with a built-in paired skill: the connection
supplies tools and credentials, the skill supplies the matching words and
instructions that route a request to them. Enabling a paired skill does not
connect the plugin, and disabling it does not disconnect the service.

Only **Validated** and **Active** paired skills contribute their matching
capability after a registry load. After changing a paired skill's switch, use
**Refresh** in Skills so the registry and capability map are rebuilt together.

## Safety During a Run

A skill supplies instructions; it does not bypass the normal action policy.
Connected actions still pass through the same safe, monitored, confirmation,
and blocked decisions used elsewhere in Jarvis. A blocked skill or action does
not run. An unattended scheduled action that needs a decision may stop because
no person is present to approve it.

If a skill names a tool that is not installed or a service that is not
connected, the normal editor may not warn you before the run. During use,
Jarvis may skip the unavailable step or report that it could not complete it;
the skill does not gain the missing access. Read [Plugins](plugins) or [MCP
Connections](mcp-connections) before enabling instructions that depend on an
external capability.

## How It Fits Together

1. **A request or trigger starts the match** — chat, voice, or a schedule.
2. **The state is checked.** Validated and Active skills continue; Draft and
   Disabled skills are rejected before their instructions load.
3. **Jarvis loads the playbook** and renders it with the current request;
   bundle files stay separate unless requested.
4. **Commands and connections supply the actions.** The skill guides their use
   but cannot create them.
5. **Safety checks every action** — see [Safety and Approvals](safety-and-approvals).
6. **Inline work returns to the conversation;** mission skills delegate to
   [Jarvis-Agents](jarvis-agents), which have their own lifecycle.

## Check That It Works

1. Create **Three Point Check** manually. Describe it as turning a topic into
   three bullets, and instruct it to finish with `Check complete.`
2. Confirm that it appears as **Validated** with its switch on, then ask Chats
   to use it for a harmless topic. Success is three bullets followed by the
   requested words.
3. Switch it off, refresh Skills, and confirm that Chats no longer use it. The
   row should remain **Off** after restarting Jarvis.
4. Ask AI to draft a second harmless skill and save it. It should appear as
   **Draft**, without a switch, until you promote it separately.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| A row says **Draft** and has no switch | Its settings, trigger, or file structure could not be accepted, or it was intentionally saved for review | Select it, read any validation error, and fix only the named issue. After reviewing an intentional draft, use the promotion command above. |
| The switch is on but Jarvis ignores the skill | The description is vague, the skill fell outside the bounded discovery list, the request does not match it, or the direct trigger is too strict | Name the skill in a clear request, make its description specific, and test a distinctive trigger |
| A catalog installation fails | The download is unavailable, the network failed, or that name is already installed | Open **Source**, check the connection, and review the existing skill before deciding whether to delete it |
| The skill starts but cannot finish an action | A required plugin, MCP connection, permission, tool, or approval is missing | Connect the capability in the app, review its permissions, and retry; never put a credential in the skill |
| An automatic trigger does not run | Spanish is missing from the voice trigger's language list, the schedule is outside the live voice runtime, or the trigger is a skill hotkey | Add the required language, run a schedule with the desktop voice runtime, or use chat or voice instead of a skill hotkey |

For repeated app, provider, or connection failures, see the main
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
