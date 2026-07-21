---
title: "CLI Reference"
slug: cli-reference
summary: "Find the supported command-line surfaces, authentication options, common flags, and the generated command catalog."
section: "Reference"
section_order: 7
order: 1
diataxis: reference
status: active
owner: maintainers
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [cli, terminal, api, automation, reference]
related: [cli-connections, control-api-reference, app-command-reference, configuration-reference]
---

The Jarvis command-line interface (CLI) lets you inspect and control a running
Personal Jarvis instance from a terminal. It is a thin client: the command
sends a request, while Jarvis performs the action and applies its normal
validation and safety rules.

Start with a curated command such as `jarvis system status`. Use the dynamic
`jarvis api` layer when an operation has no shorter curated command.

## Before You Start

- Install Personal Jarvis so the `jarvis`, `jarvisctl`, and `jctl` commands are
  available.
- Start the desktop app or headless server for any command that reads or
  changes Jarvis. Curated help, version, and schema-cache maintenance work
  without a running server.
- For a remote instance, use a trusted private connection and authenticate
  with its control key. Do not expose the control address directly to the
  public internet.

Commands affect the computer that runs the Jarvis server, which may be
different from the computer where you type the command.

## Choose the Right Surface

| Surface | Use it when | What it controls |
|---|---|---|
| **Jarvis CLI** | You want to inspect, automate, or manage Jarvis from a terminal | A running Personal Jarvis instance |
| **CLI Connections** | You want Jarvis to use another terminal program | External programs installed on the Jarvis host |
| **Control API** | You are building an HTTP integration | The REST operations behind the CLI and app |
| **App Commands** | You need a stable Jarvis action shared by voice, chat, desktop, CLI, and REST | The canonical cross-surface command catalog |

The similar names describe opposite directions: the Jarvis CLI lets **you
control Jarvis**; a [CLI Connection](cli-connections) lets **Jarvis control an
external CLI**.

## Choose a Command Layer

| Layer | Shape | Current behavior |
|---|---|---|
| Curated commands | `jarvis <group> <command>` | Short, human-oriented names for common operations |
| Dynamic API commands | `jarvis api <tag> <operation>` | Built from the target server's OpenAPI description for mounted `GET`, `POST`, `PUT`, `PATCH`, and `DELETE` operations |
| Launcher commands | bare `jarvis`, `jarvis serve`, and launcher flags | Starts or diagnoses the app rather than calling the control CLI |

`jarvisctl` and `jctl` are control-only aliases. They are especially useful for
top-level help because `jarvis --help` intentionally shows launcher help, while
`jarvisctl --help` lists the curated control groups.

The dynamic layer uses the target server's OpenAPI schema. It reuses a local
schema cache for up to 24 hours. After that, an `api` invocation tries to fetch
a new schema and falls back to the older cache if the server is unreachable.
Run `jarvis refresh` to clear the cache before the next `jarvis api` call.

Use the [generated Jarvis CLI command catalog](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/jarvis-cli-reference.md)
for the maintained list of curated groups, arguments, and options. It
intentionally does not copy the server-specific dynamic API operations.

## Authentication and Target Selection

The CLI resolves the server address and control key separately. Higher rows
take priority over lower rows.

| Priority | Source | Best use |
|---|---|---|
| 1 | Global `--url` or `--key` | A one-call override |
| 2 | `JARVISCTL_BASE_URL` or `JARVISCTL_CONTROL_KEY` | Managed automation with a secret store |
| 3 | Profile saved by `jarvis auth login` | Repeated access to one target |
| 4 | Live local-instance discovery and the local control key | Normal same-computer use |
| 5 | `http://127.0.0.1:47821` and no key if none can be resolved | Local fallback |

For remote access, run `jarvis auth login --url <server-url>` and enter the key
in the hidden prompt. The CLI verifies it before saving it. For managed
automation, `jarvis auth login --url <server-url> --key -` reads the login key
from standard input. The root-level `--key` option does not read from standard
input. Avoid an inline key because shell history and process inspection can
expose it.

The saved profile is a per-user CLI configuration file, not the provider-key
vault. POSIX systems restrict its file permissions; Windows relies on the user
profile's access controls. Use `jarvis auth logout` on a shared or retired
computer.

## Common Options

Only the root options `--json`, `--url`, and `--key` go before the command
group. Command options go after the command and are available only when that
command's help lists them.

| Option | Scope | Meaning |
|---|---|---|
| `--json` | Root | Force machine-readable JSON output |
| `--url` | Root | Override the target server for this call |
| `--key` | Root | Override control authentication for this call; avoid inline values |
| `--dry-run` | Command | Preview the request without sending it when the command exposes this option |
| `--yes`, `-y` | Command | Authorize an operation classified as dangerous |
| `--json-body -` | Dynamic API command | Read a JSON request body from standard input |
| `--request-timeout` | Dynamic API command | Override that operation's HTTP read timeout |
| `--persist` / `--no-persist` | Supported curated setting command | Choose whether a change survives restart |

Interactive terminals receive readable tables where possible. Piped output
defaults to JSON even without `--json`, but scripts should pass `--json`
explicitly so their intent stays clear.

Read-only requests and reversible changes can run immediately. Dangerous
operations fail unless you add `--yes` or explicitly set
`JARVIS_CLI_ASSUME_YES=1`; the CLI does not open an interactive confirmation
prompt for them. Review `--dry-run` output first when the command supports it.
The preview can include the request body, so do not share it when the body
contains private information.

## Discover Commands and Help

- Run `jarvisctl --help` for every curated top-level group.
- Run `jarvis <group> --help` and `jarvis <group> <command> --help` for exact
  arguments and options.
- Run `jarvis api --help` to fetch or reuse the dynamic API tree, then continue
  with a tag and operation.
- Run `jarvisctl --install-completion` to install completion for the current
  shell. Completion uses only the cached API schema and does not make a network
  request.
- Browse App Commands with `jarvis commands list` and inspect one definition
  with `jarvis commands show <command-id>`. These commands describe the shared
  app catalog; they do not execute every entry themselves.

## Platform and Host Limits

The control client runs on Windows, macOS, and Linux, but each operation still
depends on capabilities of the Jarvis host. A command can exist even when its
feature is unavailable there. For example, desktop privacy prompts require the
matching desktop operating system, audio commands require host audio devices,
and some app-opening actions do not work on a headless server.

The curated command tree never needs the server merely to display help. The
dynamic `api` tree needs either a reachable server or an existing schema cache.
An offline cache can explain why help lists an operation that an older or
different target does not accept.

## How It Fits Together

1. You choose a curated command or a dynamic `api` operation.
2. The CLI resolves the target address and attaches a control key when one is
   available.
3. The request enters the same [Control API](control-api-reference) used by
   other supported control clients.
4. Jarvis validates the input, checks authentication, and applies server-side
   safety and permission rules. `--yes` satisfies only the CLI confirmation
   gate; it does not bypass those checks.
5. Jarvis performs the action on its host and returns a result. The CLI renders
   it as a table or JSON.
6. If the server, authentication, or host capability is unavailable, the CLI
   exits with an error instead of inventing a result.

[App Commands](app-command-reference) are the smaller stable catalog shared
across voice, chat, desktop, CLI, and REST. The Control API is broader, and the
dynamic CLI mirrors that broader surface.

## Check That It Works

With Personal Jarvis running, use one read-only command:

```powershell
jarvis --json system status
```

The command succeeds and returns JSON with `"reachable": true`. This confirms
that unified CLI routing, local target discovery, authentication, and the
server request path are working. It does not test every feature on the host.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| `jarvis --help` shows app-launch options | Top-level `jarvis` help belongs to the launcher | Use `jarvisctl --help`, or ask for help below a control group |
| Status reports that Jarvis is unreachable | The server is stopped, the target is wrong, or authentication failed | Start Jarvis, check `jarvis auth status`, then verify the saved target |
| The `api` group is unavailable | No schema is cached and the server cannot be reached | Start or reconnect to the target, run `jarvis refresh`, then retry `jarvis api --help` |
| A new API operation is missing | The dynamic schema cache can remain fresh for 24 hours | Run `jarvis refresh`, then invoke the `api` group while the intended server is reachable |
| A dangerous action is refused | The CLI requires explicit authorization and never prompts | Run the supported `--dry-run`, review it, then repeat with `--yes` only if the action is correct |

## Next Steps

- Read [CLI Connections](cli-connections) when Jarvis should use an external
  terminal program rather than receive a control command.
- Use the [Control API Reference](control-api-reference) for HTTP
  authentication, discovery, responses, and integration behavior.
- Open the [App Command Reference](app-command-reference) to understand the
  stable actions shared across Jarvis surfaces.
- Review the [Configuration Reference](configuration-reference) before
  changing settings from the CLI or API.
