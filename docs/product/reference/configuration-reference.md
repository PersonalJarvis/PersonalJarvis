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
last_reviewed: 2026-07-21
phase: "-"
audience: end-user
tags: [configuration, settings, defaults, environment, restart, reference]
related: [settings-and-appearance, providers-and-api-keys, credentials-and-secrets, platform-support]
---

Personal Jarvis loads non-secret settings from typed defaults, TOML, an optional
YAML profile, and environment variables. App controls can then update a running
component. A live update survives restart only when persistence succeeds.

Use **Settings**, **API Keys & Providers**, or the feature's setup screen for
normal changes. Reserve files and environment variables for managed deployments
or values without a supported control. Those higher-priority sources can mask
an app-saved value on the next start.

## Choose the Supported Surface

| Surface | Use it for | Important behavior |
|---|---|---|
| **Settings** and feature setup screens | Languages, voice, audio, startup, shortcuts, permissions, prompts, integrations, and appearance | Uses feature-owned routes with field-specific validation and live-apply behavior |
| **API Keys & Providers** | Brain, Tool Model, speech, Realtime, Wiki, and Jarvis-Agent provider choices | Keeps provider selection separate from private credentials and shows only registered provider options |
| Jarvis CLI and dynamic API commands | Headless use and automation | Calls mounted server routes; run `jarvis refresh` if an upgraded route is missing |
| `jarvis config` and `/api/control/config` | Allowlisted scalar settings without a dedicated control | Requires authentication and uses the validating, backed-up config mutation path |
| `jarvis.toml`, profile YAML, and `JARVIS__...` | Managed deployments and advanced settings without a supported control | Bypasses feature-specific option discovery and live-apply logic |

A setting is supported only when the current schema defines it and an installed
feature uses it. A similar source-code name or retained compatibility key does
not prove that it affects behavior.

## Configuration Precedence

At process start, Jarvis resolves ordinary, non-secret settings from lowest to
highest priority:

| Priority | Source | Scope |
|---|---|---|
| 1 | Built-in schema default | Used when no higher source supplies the value |
| 2 | Active `jarvis.toml` | Persistent base for the installation |
| 3 | Selected profile in `<project-folder>/profiles/` | Recursively replaces matching file values; lists are replaced rather than combined |
| 4 | `JARVIS__SECTION__KEY` environment value | Overrides the matching value for that process |

`JARVIS_PROFILE` selects a profile before environment settings are applied.
`JARVIS_CONFIG` selects another configuration file, which is useful on a
read-only installation. They control loading and are not nested settings.
Changing `JARVIS_CONFIG` does not relocate profiles; a missing profile file
produces no overlay.

| Location | Resolution |
|---|---|
| Active configuration | Non-blank `JARVIS_CONFIG`; otherwise `<project-folder>/jarvis.toml` |
| Named profiles | `<project-folder>/profiles/<profile>.yaml` |
| Local credential fallback | `JARVIS_DATA_DIR/credentials.json` when set; otherwise the writable project data directory, with a per-user fallback such as `%LOCALAPPDATA%\Jarvis` on Windows or `~/.jarvis` elsewhere |

Nested environment names use two underscores between path segments. For
example, `JARVIS__UI__LANGUAGE=en` targets the interface language. Common
boolean and numeric forms are converted automatically. Environment overrides
support scalars only; use a supported request, or an advanced file when no
control exists, for lists and objects. Environment changes require a restart.

> [!note] A file edit can appear to have no effect when a profile or environment
> value has higher priority. Some provider choices also have compatibility
> state maintained by the supported app writer. Change those choices in **API
> Keys & Providers** instead of editing their file entries by hand.

## Trace the Value That Wins

When a setting is surprising, compare each layer instead of treating one file
or response as the effective value. First note the value shown by the
feature's current `GET` operation. Then inspect the active TOML path, the
selected profile, and matching `JARVIS__...` variables in that order. Check
whether the route reports an active component, a pending change, or a required
restart. Finally, verify that the required device, permission, credential, or
optional package is available.

These observations answer different questions. `jarvis config get <path>`
reports the persistent base value, while a feature-specific endpoint may
report the value currently used by a running component. An environment value
can win at startup without changing TOML. Conversely, a live route can update
the running component before the persisted value is used again. Record both
the save result and the apply result when diagnosing a mismatch. Do not paste
credentials or complete environment dumps into bug reports; report only the
source layer, non-secret value, and status fields involved.

## Defaults and Current Values

The installed typed schema defines defaults. Omitting a field uses its default
only when no profile or environment source supplies it. An empty provider or
model field can mean **Automatic** or **Follow the main Brain**, but other
fields reject empty values.

Use the installed app or server for exact values rather than copying a model
name, default, or option from an older document:

| What you need | Current source of truth |
|---|---|
| Current value and selectable options for a dedicated control | The matching app control or its `GET /api/settings/...` operation |
| Provider, model, voice, authentication, and billing choices | The current provider cards under **API Keys & Providers** |
| Mounted server operations | `jarvis api --help`; use `jarvis refresh` after an upgrade |
| Allowlisted scalar config paths and restart metadata | `jarvis config list` or `GET /api/control/allowlist` |
| Persistent base value for one allowlisted path | `jarvis config get <path>`; this reads the active TOML file, not a profile or environment overlay |
| Curated CLI and stable cross-surface actions | [CLI Reference](cli-reference) and [App Command Reference](app-command-reference) |

The schema is a validation boundary, not a catalog of desktop controls. The
generated mutable list contains scalar schema leaves except protected sections;
it excludes lists and maps. OpenAPI lists mounted routes, while App Commands are
a smaller stable catalog. None proves that an optional package, device,
permission, credential, or model capability is available. Provider cards load
models separately because catalogs can change without changing a connection.

## User-Facing Settings Groups

The current product-owned groups are smaller than the full schema.

| Group | Current controls |
|---|---|
| **Settings > Languages** | Interface language, voice-recognition language, and reply language |
| Voice panels in **Settings** | Realtime or Pipeline mode, wake word, thinking pause, volume, audio devices, and Call or Hangup keybinds |
| App and personalization controls | Launch at login, operating-system permissions, Bar and Overlay behavior, System Prompt, and Agent Instructions |
| **API Keys & Providers** | Brain, Voice Output, Voice Input, Realtime, Tool Model, Jarvis-Agent, and advanced Wiki or team-provider choices |
| Feature-owned setup | Wiki location, Marketplace plugins, channels, telephony, and other integration-specific values |

Safety, security, MCP server, harness, and review sections are protected from
the generic Control API. Many operational, performance, routing, retention, and
path fields are advanced only. Configuration cannot install a plugin or create
a missing host capability.

## Apply and Restart Behavior

The response from the exact route you called is more reliable than a general
restart rule. Dedicated settings and provider routes may return these fields:

| Result field | Meaning |
|---|---|
| `persisted` | `true` confirms the write; `false` can also mean persistence was not requested |
| `applied_live` or `live_switched` | The named running component accepted the change |
| `restart_required` or `requires_restart` | The route expects a restart before full activation |
| `session_restarted` | The route reconnected an active voice session for the new selection |

A missing field makes no promise. The generic config route instead returns
`applied`, `needs_confirmation`, `requires_restart`, and `backup_path`. Safe
changes can apply immediately; ask-tier changes wait for confirmation.

Typical timing is:

| Timing | Common examples |
|---|---|
| **Immediately when running** | Interface language, volume, thinking pause, sound effects, supported audio-device swaps, and several Bar controls |
| **Next message** | Reply-language policy, System Prompt, and Agent Instructions |
| **Next voice start or reconnect** | STT changes need a new Pipeline voice start; Realtime can reconnect; TTS can switch live when Pipeline voice is running |
| **Next action or mission** | Tool Model and Jarvis-Agent provider choices are resolved for new work |
| **After restart** | Environment overrides, route-reported restart changes, and values saved while their component was unavailable |

A live change without successful persistence can revert at the next start. A
headless host can also persist a desktop choice without applying it there.

## Validation, Writes, and Recovery

Do not hand-edit a value when the app or CLI controls it. The two supported TOML
write paths have different guarantees.

Dedicated routes validate requests and use the runtime writer for targeted
patches. It preserves TOML comments, formatting, and an existing UTF-8
byte-order mark; serializes in-process writes; flushes a unique sibling
temporary file; and replaces the target atomically. It retries short-lived
sharing errors and can create a missing active config file. Provider changes
may also reconcile related model, voice, or compatibility values.

The generic `/api/control/config` path accepts only mutable scalar fields. It
rejects protected or unknown paths, validates the full config, backs it up,
writes atomically, reload-tests, rolls back on failure, emits a config event,
and audits the result. It needs an existing readable config file. Use `jarvis
config list` for its allowlist and restart metadata.

Direct TOML or profile edits are only for advanced values without a control.
They lack route checks, live apply, compatibility updates, and Control API
rollback. Avoid concurrent writers, keep a known-good copy, use UTF-8, and
verify after restarting. Restore the copy if an edit prevents startup.

## Credentials Are Separate

API keys, tokens, passwords, and private connection material are not ordinary
configuration values and must never be placed in `jarvis.toml`, a profile,
documentation, chat, or voice input. Save them only through the protected field
or sign-in flow owned by the feature.

Credential lookup uses the operating-system store when usable, then an operator
environment value, `.env` compatibility, and a permission-restricted file
fallback. A newer fallback can supersede a stale platform copy during recovery.
This keeps headless in-app setup usable, but Jarvis does not encrypt the file.
See [Credentials and Secrets](credentials-and-secrets) for recovery details.

The nested `JARVIS__...` variables described above are for non-secret settings.
Provider credentials use their documented credential names and protected
storage flow; do not derive or guess one from a configuration field.

## Cross-Platform and Headless Behavior

The same loader and REST application run on Windows, macOS, Linux, and headless
servers. Applying a value still depends on host capabilities:

- A graphical overlay or login-startup preference can be saved on a headless
  host but cannot create a desktop session.
- Audio and wake controls need devices, permissions, and optional speech
  components. A preference cannot override an operating-system denial.
- A saved value cannot install an optional component. Check feature status
  before assuming it can run.
- On a read-only deployment, point `JARVIS_CONFIG` at a writable persistent
  location. Its parent directory must also be writable so same-directory
  temporary files and Control API backups can be created.
- Put `JARVIS_DATA_DIR` on private, persistent storage when credential fallback
  is needed.

See [Platform Support](platform-support) for the capability matrix. Read the
exact route's `available`, `supported`, or apply status; fields vary by route.

## How It Fits Together

1. Jarvis loads schema defaults, active TOML, the selected profile, and nested
   environment overrides.
2. Registries, credentials, permissions, and host probes determine what is
   usable.
3. Product controls use dedicated routes; advanced scalars can use the
   authenticated mutable-config route.
4. The route validates, persists when requested, and applies at supported
   timing, then reports live, pending, or restart state.
5. Credentials remain on the separate secret-storage path.

Read [CLI Reference](cli-reference) for command discovery and persistence
flags, or [Control API Reference](control-api-reference) for authentication,
OpenAPI discovery, and response behavior. Both surfaces reach the same server
that backs the desktop controls.

## Check That It Works

On a desktop, open **Settings > Languages**, note the interface language, and
choose another supported language. Labels should change immediately. Reopen
Settings, use the app's Restart action, and confirm the selection again. Wait
if active missions block restart, then restore the original language.

This checks live update, persistence, and next-start loading. A reverted value
points to the save result or a higher-priority source.

Headlessly, record `jarvis config language get`, set another supported code with
`jarvis config language set` and persistence enabled, then read it before and
after restart. Restore the original value. Do not test a desktop-only setting.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| A change works now but returns after restart | The live update succeeded but persistence failed, or a profile/environment value won at boot | Check `persisted`, then remove or update the higher-priority source |
| The file contains the new value, but Jarvis shows another value | A profile or `JARVIS__...` override masks it | Read the precedence table and inspect the process's deployment configuration |
| A named profile appears to do nothing | The profile is `default`, the YAML file is missing, or it was placed beside `JARVIS_CONFIG` instead of in the project `profiles/` directory | Check `JARVIS_PROFILE`, the fixed profile path, and the YAML name |
| Jarvis rejects the configuration on start | An advanced TOML or YAML edit has invalid syntax or a typed value is invalid | Restore your known-good copy; generic Control API writes roll back automatically when their reload test fails |
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
