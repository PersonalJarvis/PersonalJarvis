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
| **Command** | One defined action with known inputs, such as creating or listing something | You need a predictable operation callable from the app, command line, or conversation |
| **Plugin** | A packaged capability or integration | Jarvis does not yet have the action or service you need |
| **MCP connection** | Tools from an external service via Model Context Protocol (MCP) | A skill needs to read from or act in a connected service |
| **Jarvis-Agent** | An isolated background worker for longer, reviewed work | The task needs several substantial steps, or should outlive the conversation |

A skill can tell Jarvis to use a command, plugin, or connected tool, and one
marked for mission execution hands its instructions to a Jarvis-Agent. The
skill stays the method; the other building block supplies the action.

## Before You Start

- You can create and manage a skill without an artificial intelligence (AI)
  provider. AI-assisted drafting and catalog ranking use the active provider
  when one is reachable, and fall back to a basic editable draft or local
  search when it is not.
- Catalog installation needs an internet connection; a manually created skill
  does not.
- Connect any required service before testing the skill. Installing a skill
  installs no plugin and creates no connection.
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

2. **Open Skill Finder.** Describe the outcome you want, and narrow the catalog
   by trust label, popularity, category, language, and stated risk. Search
   works without an AI provider, ranking by catalog text instead.

3. **Review a promising result.** Read its description, stated risk, and
   categories, then open **Source**. Check what the instructions ask Jarvis to
   do, which services they mention, and whether you trust the source.

4. **Choose Install.** A directly downloadable skill appears in the Skills list
   afterwards. If the result says **Manual**, the app installed nothing; use
   **Source** to inspect the project and proceed only if you understand its
   separate installation steps.

5. **Check the installed state before using it.** Installation preserves the
   downloaded file. A structurally valid file with no state appears as
   **Validated** with its switch on; one that declares a state keeps it;
   invalid YAML or unsupported settings appear as **Draft** with an error.
   Switch an unfamiliar skill off while you review it.

6. **Select the skill and inspect its detail panel.** The main editor shows the
   complete `SKILL.md`: its settings followed by the instructions Jarvis will
   receive. The **Bundle** panel lists references, scripts, assets, and agent
   notes already in the skill folder, and previews UTF-8 text files but not
   binary ones.

The catalog refuses a second skill with the same name. Review the installed
copy before deleting it to make room; deletion is permanent.

> [!note] A direct catalog install downloads only `SKILL.md`. It does not clone
> the source repository or download sibling bundle folders. A catalog result
> marked **Manual** only opens its source page; Jarvis does not install it.

## Import a Skill from a Folder

Jarvis reads skills only from its own skills directory. One written anywhere
else — say a coding agent's project folder such as `.claude/skills/<name>/`
(Claude Code) or a Codex equivalent — stays invisible until imported. Those
folders are a **separate skill system** with a similar file format: a file
placed there installs nothing in Jarvis, and vice versa.

To bridge a skill across, run:

```bash
jarvis skills import <path-to-skill-folder>
```

The command copies the folder's `SKILL.md` and its bundle folders
(`references/`, `scripts/`, `assets/`, `agents/`) into the Jarvis skills
directory and reloads the registry. It also accepts an `http(s)` link to a raw
`SKILL.md`, and then behaves like a catalog install.

Import rules:

- A folder import behaves like the manual install: a structurally valid file
  with no declared state arrives as **Validated** (on); one that declares a
  state keeps it.
- Instructions containing code with disallowed calls arrive as **Draft**, with
  the findings reported. Review before promotion, as with an AI-written draft.
- A name that collides with an installed or built-in skill is refused.
- Not every field of another agent's format is supported. An unsupported
  setting fails the import with the exact validation error; remove that field
  from the source and import again.

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

6. **Review and activate an AI-created draft separately.** Check the saved
   definition's name, description, triggers, instructions, and requested
   capabilities. The desktop can edit a deliberate draft but not promote it: a
   trusted operator runs `python -m jarvis.skills.cli --promote <skill-slug>`,
   which re-checks the content before setting **Active**. Refresh Skills to
   confirm the new state.

The form refuses a skill with no real instructions, or a name that collides
with an installed or built-in one. Do not paste AI-written text into the manual
path to skip the draft boundary. If an AI-created skill appears enabled
immediately, switch it off and report it as a bug before running it.

## Review States and Changes

The switch has two internal "on" states. Their labels differ; for everyday use
they behave identically.

| State | Can it run? | What it means in the app |
|---|---|---|
| **Validated** | Yes | The definition could be read and is ready by default |
| **Active** | Yes | You explicitly switched it on |
| **Disabled** | No | You switched it off; that choice survives a restart |
| **Draft** | No | The definition needs a correction or is deliberately waiting for approval; no switch is shown |

Select a skill to review or edit its complete definition. **Save** replaces the
whole file and reads it again. Invalid YAML, unsupported fields, or a trigger
without its required value move the skill to **Draft** and show an error. Fix
the named problem and save again. The save path does not check whether a voice
regular expression will compile, whether a cron expression will run, or whether
a named tool is installed, so test those parts before relying on them.

Built-in skills are protected from deletion, and editing one requires admin
access, though you can switch one off without changing its file. Personal
skills can be deleted individually or in a confirmed batch. Dragging rows
changes only their display order, not which skill Jarvis prefers.

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

Jarvis first sees only the name and description of enabled skills, within a
bounded list and text budget, then loads the selected skill's instruction body
for the turn; bundle files are never included automatically. An **inline**
skill guides the current turn. A **mission** skill hands the instructions to a
Jarvis-Agent, falling back to the current turn if worker dispatch is
unavailable or fails.

A voice trigger written by the creator carries no language list, which
currently means German and English. Add `es` to the trigger's `language` list
to match spoken Spanish. Chat matching applies no spoken-language filter.

Scheduled triggers can exist in an installed skill, but the scheduler starts
with the voice pipeline, so they never run in a headless API-only session or
while Jarvis is stopped, and the creator does not expose schedule setup.
Hotkey definitions can be stored and displayed, but nothing is listening for
them yet — do not rely on a skill hotkey to start work.

### Skills paired with plugins

Most marketplace plugins ship with a built-in paired skill. The plugin
connection supplies tools and credentials. Its paired skill supplies the
matching words and instructions that help Jarvis route a request to those
tools. Enabling a paired skill does not connect the plugin, and disabling it
does not disconnect the service.

Only **Validated** and **Active** paired skills contribute their matching
capability after a registry load. After changing a paired skill's switch, use
**Refresh** in Skills so the registry and capability map are rebuilt together.

## Safety During a Run

A skill supplies instructions; it does not bypass the action policy. Connected
actions still pass the same safe, monitored, confirmation, and blocked
decisions used everywhere else, and a blocked action does not run. An
unattended scheduled action needing a decision may simply stop, because nobody
is there to approve it.

If a skill names a tool that is not installed or a service that is not
connected, the editor may not warn you beforehand. At run time Jarvis skips the
step or reports that it could not finish; the skill gains no missing access.
Read [Plugins](plugins) or [MCP Connections](mcp-connections) before enabling
instructions that depend on an external capability.

## How It Fits Together

1. **A request or trigger starts the match.** Chat, voice, or a schedule points
   Jarvis to an enabled skill.
2. **The skill state is checked.** Validated and Active continue; Draft and
   Disabled are rejected before their instructions load.
3. **Jarvis loads the playbook.** The instruction body is rendered with the
   current request; bundle files stay separate unless requested.
4. **Commands and connections supply actions.** A skill can guide Jarvis to an
   app command, a plugin tool, or an MCP tool, but creates no capability itself.
5. **Safety checks every action**, exactly as in a normal conversation — see
   [Safety and Approvals](safety-and-approvals) before automating changes.
6. **Inline work returns to the conversation.** Mission skills delegate to
   [Jarvis-Agents](jarvis-agents), which own their worker, review, and outputs.

## Check That It Works

1. Create **Three Point Check** manually. Describe it as turning a topic into
   three bullets, and instruct it to finish with `Check complete.`
2. Confirm it appears as **Validated** with its switch on, then ask Chats to
   use it for a harmless topic. Success is three bullets followed by the
   requested words.
3. Switch it off, refresh Skills, and confirm Chats no longer use it. The row
   should still read **Off** after restarting Jarvis.
4. Ask AI to draft a second harmless skill. It should appear as **Draft**,
   without a switch, until you promote it separately.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| A row says **Draft** and has no switch | Its settings, trigger, or structure was not accepted, or it was saved for review | Read the validation error and fix only the named issue. For an intentional draft, use the promotion command; the switch cannot promote. |
| The switch is on but Jarvis ignores the skill | Vague description, outside the bounded discovery list, or too strict a trigger | Name the skill in a clear request, make its description specific, test a distinctive trigger |
| A catalog installation fails | Download unavailable, network failed, or that name is already installed | Open **Source**, check the connection, and review the existing skill before deleting it |
| The skill starts but cannot finish an action | A required plugin, MCP connection, permission, tool, or approval is missing | Connect the capability, review its permissions, retry; never put a credential in the skill |
| An automatic trigger does not run | Spanish missing from the trigger's language list, the schedule is outside the live voice runtime, or it is a skill hotkey | Add the language, run the schedule with the desktop voice runtime, or use chat or voice instead |

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
