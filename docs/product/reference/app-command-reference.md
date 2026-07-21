---
title: "App Command Reference"
slug: app-command-reference
summary: "Learn how to browse the canonical app-command catalog shared by voice, chat, desktop, CLI, and REST surfaces."
section: "Reference"
section_order: 7
order: 4
diataxis: reference
status: active
owner: maintainers
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [app-commands, voice, chat, cli, api, automation]
related: [workflows-and-commands, cli-reference, control-api-reference]
---

The App Command Registry is Personal Jarvis's curated catalog of high-value
actions. Each entry assigns one action a stable ID, an input schema, safety
metadata, a sidebar section ID, example phrases, and one existing REST
operation.

The registry creates the conversational tools and the catalog exposed through
the command-line interface (CLI) and REST. Desktop controls and feature-specific
CLI commands keep their own interfaces, but use the same feature routes named
by the registry. The catalog is intentionally smaller than the full Control API
and is not a list of every button in the app.

## Before You Start

- Start Personal Jarvis before browsing the live catalog through the CLI or
  REST.
- Conversational execution needs at least one available Brain path that can
  call tools. Jarvis can hand a tool request to another configured,
  tool-capable provider when the selected provider cannot call tools. The
  catalog remains browsable when no such provider is reachable.
- Connect any provider, device, permission, or local feature required by the
  underlying action. A catalog entry describes an action; it does not make its
  dependencies ready.

App Commands do not accept API keys or other credentials through conversation.
Enter credentials only in the protected settings screen for the relevant
provider or connection.

## Browse the Catalog

Use the [generated App Command catalog](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/commands-reference.md)
for a readable summary of every command on the current public `main` branch.
It is generated from the registry source and includes each command's endpoint,
arguments, confirmation marker, sidebar section ID, and first English alias.

For the exact catalog shipped with your running installation, use the CLI:

```powershell
jarvis commands list
jarvis commands show providers-list
```

`list` returns the complete catalog served by the running instance. `show`
returns one definition, including its full registry input schema and all
localized aliases. Both are read-only; they do not execute the selected
command.

Advanced clients can read the same machine catalog from `GET /api/commands`
or one exact entry from `GET /api/commands/{command_id}`. An unknown ID returns
`404`. Use the [Control API Reference](control-api-reference) for target
discovery and authentication.

> [!note]
> The public generated catalog can be newer or older than an installed release.
> The live CLI or REST response is authoritative for the catalog definition in
> the instance you are using. The backing route still decides whether the
> action can run and what it returns.

## Read a Command Entry

| Field | What it tells you |
|---|---|
| `id` | Stable kebab-case name used for exact lookup and as the conversational tool name |
| `title`, `description` | Plain-English purpose and expected outcome |
| `method`, `path` | The one backing REST operation |
| `params` | Conversational input schema, including required fields, types, choices, lengths, and numeric limits |
| `path_params` | Inputs inserted into the endpoint path rather than its body or query |
| `dangerous` | Whether the conversational tool starts at the `ask` risk tier instead of `monitor` |
| `worker_allowed` | Whether the command is eligible for an explicit, mission-scoped worker grant |
| `ui_section` | Stable internal ID of the sidebar section associated with the action |
| `voice_aliases` | Example input phrases for German, English, and Spanish; not guaranteed triggers or valid argument values |

The generated catalog is a compact summary. Use `jarvis commands show` or the
live REST entry when exact defaults, limits, every alias, or Jarvis-Agent
eligibility matters.

Aliases make the intended natural-language meaning easier to discover, but
they are not executable samples. Voice and chat selection still depends on an
available conversational tool path, the request's context, and valid inputs.
CLI and REST callers should use the exact command ID, endpoint, and schema
values rather than guessing from an alias. In the catalog reviewed on
2026-07-21, the `stt-switch` English alias names Deepgram even though that
entry's provider enum contains no Deepgram ID.

`worker_allowed` and IDs such as `jarvis-agent-switch` use stable internal
terminology. In user-facing app and spoken text, the agent name follows the
assistant name derived from the wake word, such as `Nova-Agent`, with
`Assistant-Agent` as the fallback when no name is available.

## Understand Availability and Safety

A listed command is **defined**, not necessarily **ready**. The action can
still fail when its provider is disconnected, an input is invalid, a requested
item no longer exists, a device or permission is missing, the host does not
support the feature, or the app server is unavailable.

For voice and chat, Jarvis exposes each registry entry as its own small tool.
It rejects missing, unknown, incorrectly typed, out-of-range, and unsupported
schema values before calling the app. The request then goes through the listed
REST route, where feature-specific state and availability checks still apply.
Jarvis reports the route's actual response rather than assuming success from
the request wording.

The `dangerous` field is a default, not the complete safety decision. A value
of `true` gives the conversational tool the `ask` tier; `false` gives it
`monitor`. The runtime blacklist can block a call, a whitelist can approve it,
and the voice plausibility check can require confirmation for a `monitor` call.
CLI danger checks are separate and come from the executing CLI command, route
path, and OpenAPI metadata.

| Surface | How you find or run a command | Confirmation behavior |
|---|---|---|
| Voice or chat | Ask naturally; a tool-capable Brain path can select the command's tool | An `ask` decision waits for a separate yes-or-no turn; runtime policy can approve, add confirmation, or block |
| Desktop | Use the normal feature control associated with `ui_section` | The feature's own dialog and safety behavior apply; there is no separate catalog screen |
| CLI | Browse with `commands list/show`; execute through a curated feature command or `jarvis api` | The executing CLI path applies its own `--yes` rules; browsing never executes the action |
| REST | Read the catalog routes, then call the listed endpoint | Catalog metadata does not open a confirmation prompt; direct mutations run with the global API boundary and that route's checks |
| Mission worker | Use only a `worker_allowed` command included in a supervisor grant | The broker must be available; dangerous and configuration-changing commands are excluded |

Confirmation authorizes only the proposed action. It does not add a missing
credential, bypass input validation, grant an operating-system permission, or
turn an unavailable feature into a working one.

## How It Fits Together

1. The registry maps a curated action to a stable ID, conversational schema,
   default risk tier, sidebar section ID, and existing REST operation.
2. `GET /api/commands` and `jarvis commands` expose that metadata for discovery.
3. Voice and chat load one tool per registry entry. The tool validates its
   schema and calls the listed route through the running app.
4. Desktop controls and feature-specific CLI commands do not dispatch through
   the catalog. They reach the corresponding feature route through their own
   interface.
5. The route validates current state and performs the action on the Jarvis
   host. Its real result returns to the starting surface, including failures.

An [App Command](workflows-and-commands) performs one curated action now. A
Workflow stores several ordered steps for reuse or scheduling. The
[Jarvis CLI](cli-reference) is a terminal client that can browse the registry
and execute supported routes. The [Control API](control-api-reference) is the
broader HTTP surface from which registry commands select their endpoints.

## Check That It Works

With Personal Jarvis running, inspect one read-only entry:

```powershell
jarvis --json commands show providers-list
```

The result has `"id": "providers-list"`, uses the `GET` method, marks
`dangerous` as `false`, and includes localized `voice_aliases`. This verifies
CLI access to the running registry without testing a provider, executing an
App Command, or changing settings.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| `Unknown command id` or `404` | The ID is misspelled or not present in this installed version | Run `jarvis commands list`, then copy the exact `id` |
| The public catalog and live catalog differ | GitHub `main` and the installed release contain different registry versions | Use the live entry for current behavior and update Jarvis only through the supported release path |
| Jarvis answers in prose but no action runs | The Brain could not use the conversational tool path or did not select a command | Check the exact command with the CLI, then retry a concrete request or use the matching desktop control |
| Jarvis asks for confirmation | The command's default tier or the current safety and plausibility checks require review | Review the exact action and inputs, then approve or deny the pending request |
| A listed command returns an error | Its inputs or feature dependency are not valid on the current host | Read the returned detail, fix the provider, permission, device, item, or host requirement, then retry a read-only check first |

## Next Steps

- Read [Workflows and App Commands](workflows-and-commands) to choose between
  one immediate action and a reusable sequence.
- Use the [CLI Reference](cli-reference) for command discovery, authentication,
  common flags, and safe terminal execution.
- Open the [Control API Reference](control-api-reference) when integrating the
  registry or its backing endpoints over HTTP.
