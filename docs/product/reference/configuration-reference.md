---
title: "Configuration Reference"
slug: configuration-reference
summary: "Look up supported settings, defaults, environment overrides, and safe configuration-write behavior."
section: "Reference"
section_order: 7
order: 3
diataxis: reference
status: active
owner: maintainers
last_reviewed: 2026-07-15
phase: "-"
audience: end-user
tags: [configuration, settings, defaults, environment, restart, reference]
related: [settings-and-appearance, providers-and-api-keys, credentials-and-secrets, platform-support]
---

Personal Jarvis combines built-in defaults, a persistent configuration file,
optional profiles, environment overrides, and live runtime changes. For normal
use, change settings in the app: it validates the value, writes the correct
configuration location, and tells the running feature whether the change was
applied immediately.

Use the file and environment layers for managed, headless, or advanced setups.
They are powerful because they can override the app, so treat their precedence
as part of the configuration rather than as a recovery shortcut.

## Choose the Supported Surface

| Surface | Best for | Important behavior |
|---|---|---|
| **Settings** | Everyday language, voice, audio, startup, shortcut, and appearance choices | Shows valid choices and attempts to apply them live |
| **API Keys & Providers** | Brain, Tool Model, speech, Realtime, and Jarvis-Agent connections | Keeps provider selection separate from the private credential |
| **Jarvis CLI or Control API** | Headless hosts, automation, and remote administration | Uses the same validated server routes as the app; supported setting operations can choose whether to persist |
| `jarvis.toml` | Advanced values that have no current app control | Persistent base configuration; direct editing bypasses the app's safe writer and live-apply logic |
| Profile YAML | Reusable operator profiles | Overlays the base file for one selected profile |
| `JARVIS__...` environment variables | Deployment-time scalar overrides | Wins over the file and profile when Jarvis starts |

Do not invent a setting because a similar name appears in source code. A
setting is supported when it exists in the current configuration schema and is
used by an installed feature. Unknown keys may be ignored, rejected, or kept
only for compatibility; their presence does not prove they affect behavior.

## Configuration Precedence

For ordinary, non-secret settings, Jarvis resolves values from lowest to
highest priority:

| Priority | Source | Scope |
|---|---|---|
| 1 | Built-in schema default | Used when no higher source supplies the value |
| 2 | `jarvis.toml` | Persistent base for the installation |
| 3 | Selected profile in `profiles/` | Replaces matching file values; lists are replaced rather than combined |
| 4 | `JARVIS__SECTION__KEY` environment value | Overrides the matching setting at process start |
| 5 | Live app, CLI, or API change | Changes the current process when the feature supports live apply |

`JARVIS_PROFILE` selects a profile before the environment setting overlay is
applied. `JARVIS_CONFIG` selects a different configuration file and is useful
when the installation directory is read-only. These two variables control
loading; they are not nested settings.

Nested environment names use two underscores between path segments. For
example, `JARVIS__UI__LANGUAGE=en` targets the interface-language setting.
Boolean forms such as `true`, `false`, `yes`, `no`, `1`, and `0`, plus integer
and decimal values, are converted automatically. Use environment overrides for
scalars only; lists and structured objects have no stable environment encoding
and belong in a supported API request or configuration file.

> [!note] A file edit can appear to have no effect when a profile or environment
> value has higher priority. Some provider choices also have compatibility
> state maintained by the supported app writer. Change those choices in **API
> Keys & Providers** instead of editing their file entries by hand.

## Defaults and Current Values

Defaults are defined by the installed version's typed configuration schema.
Omitting a field restores that schema default only when no profile or
environment source supplies it. An empty value can have a different meaning:
for some provider and model fields it means **Automatic** or **Follow the main
Brain**, while for other fields an empty value is invalid.

Use the current app or server for exact values rather than copying a model name
or option from an older document:

| What you need | Current source of truth |
|---|---|
| Effective value and selectable options | The matching Settings control or its `GET /api/settings/...` operation |
| Provider, model, voice, authentication, and billing choices | The current provider cards under **API Keys & Providers** |
| Mounted server operations | `jarvis api --help`; run `jarvis refresh` first after an upgrade |
| Curated CLI commands and flags | The [generated Jarvis CLI command catalog](https://github.com/PersonalJarvis/PersonalJarvis/blob/main/docs/jarvis-cli-reference.md) |
| Stable actions shared across app surfaces | [App Command Reference](app-command-reference) |

Provider cards deliberately discover models separately from the provider list,
because available models can change without changing the connection itself. A
configured provider name also does not guarantee that the installed model has
the capability a feature needs.

## Settings Families

The schema is broad, but its public settings fall into a small number of
families:

| Family | Includes | Related surface |
|---|---|---|
| **Language and profile** | Interface, speech recognition, reply language, and active profile | **Settings > Languages** |
| **Providers and models** | Main Brain, Tool Model, Pipeline speech, Realtime voice, and Jarvis-Agent worker choices | **API Keys & Providers** |
| **Voice and audio** | Voice mode, wake phrase and activation, microphone and speaker, volume, silence window, and keybinds | **Settings > Voice** and **Audio** |
| **Desktop behavior** | Jarvis Bar, overlay, sound effects, audio ducking, preferred opener, and login startup | **Settings > App settings** and **Bar & Overlay** |
| **Personalization and memory** | Persona sidecars, standing instructions, memory retention, Wiki location, and curator choices | **Settings**, **Wiki**, and memory features |
| **Safety and permissions** | Risk defaults, allow and block lists, and approval behavior | Safety controls; operating-system permissions remain separate |
| **Tools and integrations** | Computer Use, pointer context, MCP server, Marketplace callback, channels, telephony, team proxy, and board connections | The feature's own setup screen |
| **Operations** | Logging, telemetry retention, latency, performance, review, ports, and host-specific paths | Advanced file or operator-managed deployment |

Not every member of a family has a desktop control. The absence of a control
means the value is advanced, not that it is safe to guess. Provider plugins and
optional desktop or voice components must also be installed; configuration
cannot create a missing capability.

## Apply and Restart Behavior

The response from a settings API is more reliable than a general restart rule.
Where exposed, read these fields:

| Result field | Meaning |
|---|---|
| `persisted` | The boot value was written successfully |
| `applied_live` | The running component accepted the change |
| `restart_required` | The saved value could not be applied to the current component |
| `session_restarted` | An active voice session was reconnected so the new policy could take effect |

Typical timing is:

| Timing | Common examples |
|---|---|
| **Immediately** | Interface language, volume, silence window, sound effects, audio devices, and Bar visibility when their runtime is available |
| **Next message** | System prompt and standing instructions |
| **Next voice session** | Voice engine or Realtime selection; a current session may reconnect automatically |
| **Next mission** | Jarvis-Agent worker or Computer Use choices resolved when new work starts |
| **After restart** | Startup-only components, environment overrides, or a desktop/voice feature that was not running for live apply |

A live success is not the same as a persistent success. If `applied_live` is
true but `persisted` is false, the current run can use the value while the next
start returns to the previous effective configuration. Conversely, a headless
host can persist a desktop choice while reporting that it could not apply it.

## Safe Persistent Writes

Supported app and API writers update only the requested value. They parse the
existing TOML, preserve comments and a UTF-8 byte-order mark when present,
write a unique temporary file beside the target, flush it, and replace the old
file atomically. Short-lived file locks are retried. A missing writable config
file can be created at the location selected by `JARVIS_CONFIG`.

For a direct edit:

1. Prefer to stop Jarvis so the app, another CLI process, and your editor do
   not race to replace the same file.
2. Keep the file as UTF-8 TOML and change the smallest possible value.
3. Keep a backup outside the active path.
4. Restart Jarvis and verify the effective value, not just the text in the
   file.

Manual edits do not receive route validation, atomic replacement, live apply,
or managed compatibility updates. If the app provides a control for a value,
use that control.

## Credentials Are Separate

API keys, tokens, passwords, and private connection material are not ordinary
configuration values and must never be placed in `jarvis.toml`, a profile,
documentation, chat, or voice input. Save them only through the protected field
or sign-in flow owned by the feature.

Credential lookup has its own portable chain: an operating-system credential
store when usable, an operator-provided environment value or `.env` compatibility
source, then a restricted local-file fallback for hosts without a working
credential store. That fallback keeps headless setup usable, but it is not
encrypted by Jarvis. See [Credentials and Secrets](credentials-and-secrets) for
replacement, deletion, and recovery behavior.

The nested `JARVIS__...` variables described above are for non-secret settings.
Provider credentials use their documented credential names and protected
storage flow; do not derive or guess one from a configuration field.

## Cross-Platform and Headless Behavior

The same configuration model loads on Windows, macOS, Linux, and a headless
server. Application of a value still depends on installed extras and host
capabilities:

- A graphical overlay or login-startup preference can be saved on a headless
  host but cannot create a desktop session.
- Audio and wake controls need the matching audio devices, permissions, and
  optional local speech components.
- Operating-system permissions can block a configured feature. A preference
  cannot override a microphone, screen-capture, or accessibility denial.
- Platform-specific optional packages are selected during installation. A
  valid setting remains a graceful no-op when its native component is absent.
- On a read-only deployment, point `JARVIS_CONFIG` at a writable persistent
  location. If credential fallback is needed, protect the location selected by
  `JARVIS_DATA_DIR` as private application data.

Use [Platform Support](platform-support) for the current capability matrix. Use
the `available`, `supported`, and apply-status fields returned by the relevant
operation for the truth about the current host.

## How It Fits Together

1. The schema supplies a safe default for each omitted setting.
2. Jarvis loads `jarvis.toml`, overlays the selected profile, then applies
   nested environment overrides.
3. Installed provider and plugin registries determine which configured choices
   are actually available.
4. The app, CLI, or Control API validates a requested change and asks the safe
   writer to persist it when requested.
5. The running feature applies the value immediately, on its next unit of work,
   or after a restart, and reports which outcome occurred.
6. Credentials resolve through the separate secret-storage path only when a
   selected capability needs them.

Read [CLI Reference](cli-reference) for command discovery and persistence
flags, or [Control API Reference](control-api-reference) for authentication,
OpenAPI discovery, and response behavior. Both surfaces reach the same server
that backs the desktop controls.

## Check That It Works

On a graphical desktop, open **Settings > Languages**, note the current
interface language, and choose another supported language. The labels should
change immediately. Close and reopen Settings, then confirm the same value is
still selected. After active Jarvis-Agent missions finish, restart Jarvis and
confirm that the language remains selected; then restore your original choice.

This checks the live update, persistent write, and next-start load path. If the
live change works but the restarted app returns to the old value, inspect the
save result and the higher-priority profile or environment sources.

For a headless instance, use the authenticated Control API or dynamic CLI to
read the matching settings operation before and after a harmless persistent
change. Confirm `persisted: true`; do not use a desktop-only setting as the
headless test.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| A change works now but returns after restart | The live update succeeded but persistence failed, or a profile/environment value won at boot | Check `persisted`, then remove or update the higher-priority source |
| The file contains the new value, but Jarvis shows another value | A profile or `JARVIS__...` override masks it | Read the precedence table and inspect the process's deployment configuration |
| Jarvis rejects the configuration on start | TOML syntax or a typed value is invalid | Restore the backup, start Jarvis, and repeat the change through a supported control |
| `restart_required` is true | The component was unavailable or reads that value only during initialization | Finish active work, restart safely, and verify the effective value |
| A desktop or audio setting persists but does nothing | The host lacks the required session, device, permission, or optional component | Check the operation's `available` or `supported` result and read [Platform Support](platform-support) |
| A removed credential still appears configured | It comes from an operator environment, `.env`, or another external login | Remove it from the original source, restart, and follow [Credentials and Secrets](credentials-and-secrets) |
| A current API setting is missing from CLI help | The dynamic OpenAPI cache describes an older server | Run `jarvis refresh`, reconnect to the intended server, then open `jarvis api --help` again |

## Next Steps

- Use [Settings and Appearance](settings-and-appearance) for step-by-step help
  with everyday desktop controls.
- Read [Providers and API Keys](providers-and-api-keys) before changing a
  provider, model, voice, or connection.
- Review [Credentials and Secrets](credentials-and-secrets) before adding,
  rotating, or removing private access.
- Open [Platform Support](platform-support) to check which settings can apply
  on Windows, macOS, Linux, and headless hosts.
