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
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [app-commands, voice, chat, cli, api, automation]
related: [workflows-and-commands, cli-reference, control-api-reference]
---

The App Command Registry is Personal Jarvis's curated catalog of high-value
actions. Each entry gives one action a stable ID, accepted inputs, safety
metadata, a desktop location, conversational examples, and exactly one
existing REST endpoint.

The registry helps voice, chat, desktop, command-line interface (CLI), and REST
clients refer to the same action. It is intentionally smaller than the full
Control API and is not a list of every button in the app.

## Before You Start

- Start Personal Jarvis before browsing the live catalog through the CLI or
  REST.
- Use a Brain provider with tool support before trying a command in voice or
  chat. The catalog remains browsable when conversational tool use is
  unavailable.
- Connect any provider, device, permission, or local feature required by the
  underlying action. A catalog entry describes an action; it does not make its
  dependencies ready.

App Commands do not accept API keys or other credentials through conversation.
Enter credentials only in the protected settings screen for the relevant
provider or connection.

## Browse the Catalog

Use the [generated App Command catalog](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/commands-reference.md)
for a readable summary of every command on the current public `main` branch.
It includes each command's endpoint, arguments, confirmation marker, desktop
section, and one English voice example.

For the exact catalog shipped with your running installation, use the CLI:

```powershell
jarvis commands list
jarvis commands show providers-list
```

`list` returns the complete live catalog. `show` returns one exact definition,
including its full input schema and all localized aliases. Both are read-only;
they do not execute the selected command.

Advanced clients can read the same machine catalog from `GET /api/commands`
or one exact entry from `GET /api/commands/{command_id}`. An unknown ID returns
`404`. Use the [Control API Reference](control-api-reference) for target
discovery and authentication.

> [!note]
> The public generated catalog can be newer or older than an installed release.
> The live CLI or REST response is authoritative for the instance you are using.

## Read a Command Entry

| Field | What it tells you |
|---|---|
| `id` | Stable kebab-case name used for exact lookup and conversational tool identity |
| `title`, `description` | Plain-English purpose and expected outcome |
| `method`, `path` | The one backing REST operation |
| `params` | Input schema, including required fields, types, choices, lengths, and numeric limits |
| `path_params` | Inputs inserted into the endpoint path rather than its body or query |
| `dangerous` | Whether the conversational action needs explicit confirmation |
| `worker_allowed` | Whether a non-dangerous command may be granted to a Jarvis-Agent |
| `ui_section` | Sidebar section that contains the equivalent desktop control |
| `voice_aliases` | Localized example phrases associated with the command |

The generated catalog is a compact summary. Use `jarvis commands show` or the
live REST entry when exact defaults, limits, every alias, or Jarvis-Agent
eligibility matters.

Aliases make the intended natural-language meaning easier to discover, but
they are not guaranteed trigger phrases. Voice and chat selection still
depends on the active conversational tool path, the request's context, and a
valid set of inputs. CLI and REST callers should use the exact command ID or
endpoint rather than guessing from an alias.

## Understand Availability and Safety

A listed command is **defined**, not necessarily **ready**. The action can
still fail when its provider is disconnected, an input is invalid, a requested
item no longer exists, a device or permission is missing, the host does not
support the feature, or the app server is unavailable.

For voice and chat, Jarvis exposes each registry entry as its own small tool.
It rejects missing, unknown, out-of-range, or unsupported inputs before calling
the app. The request then goes through the command's normal REST route, which
performs the same feature validation used by the matching desktop action.
Jarvis reports the route's actual response rather than assuming success from
the request wording.

| Surface | How you find or run a command | Confirmation behavior |
|---|---|---|
| Voice or chat | Ask naturally; Jarvis selects an available per-command tool | A dangerous command is deferred for a separate yes-or-no turn; policy can still block it |
| Desktop | Use the normal control in the entry's `ui_section` | The feature's own dialog and safety behavior apply; there is no separate catalog screen |
| CLI | Browse with `commands list/show`; execute through a curated feature command or `jarvis api` | Dangerous operations require the executing command's `--yes`; browsing never does |
| REST | Browse the registry routes, then call the listed endpoint | Metadata does not create an interactive prompt; the client must honor it and the server still enforces authentication and route validation |
| Jarvis-Agent | Receive only an explicitly granted `worker_allowed` subset | Dangerous and configuration-changing commands are not granted through this catalog path |

Confirmation authorizes only the proposed action. It does not add a missing
credential, bypass input validation, grant an operating-system permission, or
turn an unavailable feature into a working one.

## How It Fits Together

1. A natural-language request, desktop control, CLI command, or REST call names
   an action.
2. The registry supplies a stable identity, accepted inputs, safety marker, and
   the one existing endpoint for that action.
3. The starting surface adds its own interaction layer: conversational tool
   selection, a desktop form, CLI flags, or an HTTP request.
4. The request reaches the same feature route. That route validates current
   state and performs the real action on the Jarvis host.
5. The result returns to the starting surface. A failure stays a failure; the
   catalog does not fabricate a successful outcome.

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
the unified CLI route and live registry without testing a provider or changing
settings.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| `Unknown command id` or `404` | The ID is misspelled or not present in this installed version | Run `jarvis commands list`, then copy the exact `id` |
| The public catalog and live catalog differ | GitHub `main` and the installed release contain different registry versions | Use the live entry for current behavior and update Jarvis only through the supported release path |
| Jarvis answers in prose but no action runs | The Brain could not use the conversational tool path or did not select a command | Check the exact command with the CLI, then retry a concrete request or use the matching desktop control |
| Jarvis asks for confirmation | The command or current safety policy treats the action as consequential | Review the exact action and inputs, then approve or deny the pending request once |
| A listed command returns an error | Its inputs or feature dependency are not valid on the current host | Read the returned detail, fix the provider, permission, device, item, or host requirement, then retry a read-only check first |

## Next Steps

- Read [Workflows and App Commands](workflows-and-commands) to choose between
  one immediate action and a reusable sequence.
- Use the [CLI Reference](cli-reference) for command discovery, authentication,
  common flags, and safe terminal execution.
- Open the [Control API Reference](control-api-reference) when integrating the
  registry or its backing endpoints over HTTP.
