---
title: "Control API Reference"
slug: control-api-reference
summary: Understand API authentication, discovery, error behavior, and how REST operations map to CLI and in-app actions.
section: "Reference"
section_order: 7
order: 2
diataxis: reference
status: active
owner: maintainers
last_reviewed: 2026-07-21
phase: "-"
audience: operator
tags: [control-api, rest, authentication, openapi, cli, automation]
related: [cli-reference, app-command-reference, credentials-and-secrets, architecture]
---

The Control API lets a trusted terminal, agent, or integration operate a running
Personal Jarvis instance over HTTP instead of using screen automation. Jarvis
serves a broad REST surface under `/api/*`; `/api/control/*` is the focused
facade for configuration, providers, languages, secrets, and the Control key.

## Before You Start

- Start the Jarvis instance you intend to control.
- Use that instance's Control key for programmatic access. Provider keys do not
  authenticate Jarvis itself.
- Keep loopback access for same-computer use. For remote access, use HTTPS on a
  private network or an encrypted tunnel. Jarvis does not add TLS to plain HTTP.
- Choose REST, the smaller App Command catalog, or a curated CLI command before
  building a client.

> [!warning] Treat the Control key as an administrator credential. Never place
> it in chat, voice input, source code, URLs, logs, screenshots, documentation,
> or shell history.

## Understand the API Surface

- **`/api/*`** is the broad product API. It includes settings, tasks, missions,
  providers, tools, and other mounted feature routes.
- **`/api/control/*`** adds key-only authentication to most routes and exposes a
  machine-readable configuration allowlist.
- **`/api/commands`** describes a smaller, curated set of App Commands. Each
  command points to an existing REST operation.
- **WebSocket routes** carry live chat, progress, audio, terminal data, and
  events. OpenAPI does not describe them, and the dynamic CLI does not turn
  them into commands. Some sockets also require their own ticket or handshake
  credential.

The docs browser uses `/api/docs`. Swagger is at `/api/_swagger`; the OpenAPI
document is at `/api/openapi.json`.

## Discover Operations Safely

Start with read-only discovery rather than copying paths from an old script.

| Resource | Authentication | What it tells you |
|---|---|---|
| `GET /api/health` | No Control credential | Whether the server answers; the full app includes its version, while desktop warm-up reports `warming: true` |
| `GET /api/control/auth/probe` | Control key | Whether that key is accepted; success returns a small `ok` result |
| `GET /api/openapi.json` | Global API access boundary | Mounted HTTP paths, methods, tags, parameters, bodies, and declared response schemas |
| `GET /api/_swagger` | Global API access boundary | A browsable view of the current OpenAPI document |
| `GET /api/control/allowlist` | Control key | Configuration paths that the focused facade may change, with risk and restart metadata |
| `GET /api/commands` | Global API access boundary | The curated App Command catalog and each command's backing endpoint |

The global boundary accepts a Control key, an app session, or direct loopback
access while the optional browser lock is off, which is the default. Key-only
control routes still require the Control key, except for key management used by
the app.

OpenAPI does not currently declare the enforced Bearer scheme, so Swagger has no
working **Authorize** control for key-only routes. Use it for discovery, not as
proof that an operation is public or fully describes authentication.

## Authenticate

The persistent Control key is for programs. The process-local app session is
for the desktop and browser interface.

| Caller | Credential flow | Important boundary |
|---|---|---|
| Desktop or browser | A one-use bootstrap token or accepted Control key becomes an HTTP-only session cookie | Exchange requires HTTPS or direct loopback access |
| Jarvis CLI | Local discovery or a saved profile supplies the target and Control key | Use the hidden login prompt, not an inline key |
| Custom client | A secret manager supplies the Control key as the Bearer credential | Keep it out of code, traces, and diagnostics |

The outer security boundary accepts a valid Control key or app session for most
protected `/api/*` requests. With the browser lock off, it also trusts direct
loopback access with a loopback Host and no forwarding headers. Unsafe session
or open-access requests need a trusted Origin. Non-browser clients should use
the Control key instead of imitating a browser.

Most `/api/control/*` routes apply a narrower guard and require the Control key
even on loopback. Key management also accepts an app session or trusted local
open access so the in-app panel works before the key is copied.

Open **API Keys & Providers**, then choose the dedicated key tab named for
your assistant. You can reveal, copy, replace, or regenerate the key. A custom
key must contain 12 to 128 letters, digits, or `. _ ~ -`. Replacement and
regeneration require confirmation and immediately invalidate the old key, so
update every CLI profile and integration. The corresponding routes are
`GET` and `PUT /api/control/api-key` and
`POST /api/control/api-key/rotate`.

## Follow the Request Lifecycle

| Stage | What Jarvis checks or does | Typical failure |
|---|---|---|
| 1. Resolve target | The client chooses the host and port; local CLI discovery can find a changed desktop port | Connection failure or stale target |
| 2. Guard access | Jarvis checks Host, Origin where applicable, and the credential | `400`, `401`, or `403` |
| 3. Validate and run | FastAPI validates input, then the route applies its allowlist, safety, state, and capability checks | `400`, `403`, `404`, `409`, `422`, or `503` |
| 4. Return | The route reports its result or error | JSON, empty `204`, or another declared content type |

> [!warning] `--dry-run` and `--yes` are Jarvis CLI safeguards, not HTTP
> protocol features. Direct REST mutations run the checks on that route but do
> not open the app's conversational approval flow.

## Change Settings and Handle Confirmation

Use the focused control facade for a configuration change:

1. **Read the allowlist and current value.** Check the dotted path, type, risk,
   restart requirement, and whether `GET /api/control/config` marks it allowed.
2. **Submit one change.** `PUT /api/control/config` validates the path and value.
3. **Review the response.** Safe changes return `applied: true`. Review-tier
   changes return `applied: false`, `needs_confirmation: true`, a single-use
   `pending_id`, and the old and proposed values.
4. **Confirm or reject, then read again.** Compare the values and restart flag
   before confirming. Verify stored state after the request.

Pending configuration changes live in memory for up to five minutes and
disappear on server restart. Confirmation consumes an entry before applying it;
a repeated, expired, or unknown confirmation returns `410 Gone`. Rejection is
idempotent and returns success even when the entry no longer exists.

Language, provider, secret, and key routes have their own confirmation behavior.
For credential entry, prefer **API Keys & Providers** as described in
[Credentials and Secrets](credentials-and-secrets). Generic configuration
routes refuse protected secret paths.

## Map REST to the App and CLI

| Surface | How it maps to REST | Safety and discovery behavior |
|---|---|---|
| Desktop app | A labeled control calls its mounted feature route with an app session | Shows feature-specific validation and visible state |
| Curated CLI | A maintained `jarvis <group> <command>` wraps a common route | Uses readable arguments and an explicit danger policy |
| Dynamic CLI | `jarvis api <tag> <operation>` is generated from live or cached OpenAPI | Creates commands for described GET, POST, PUT, PATCH, and DELETE operations |
| App Command | One stable catalog entry maps to exactly one route | Shared by supported voice, chat, desktop, CLI, and REST paths |
| Raw REST client | Calls the mounted route directly | Must implement credential handling, review, retries, and response parsing |

The dynamic CLI groups operations by their first OpenAPI tag and derives names
from operation IDs; untagged operations appear under `default`. Repository
checks require feature route modules to be mounted and tagged, but the running
OpenAPI document is the source of truth for that instance.

The CLI caches the schema for 24 hours and may use an older cache while offline.
Run `jarvis refresh` before the next `jarvis api` call when a route is missing.
Generated commands send JSON and expect finite responses. Multipart uploads,
binary downloads, HTML, XML, and WebSockets need a dedicated client.

Generated commands require `--yes` for every `DELETE`, known high-impact paths,
and operations marked `x-jarvis-dangerous`. Other mutations can run without it.
The flag passes only the CLI gate, never server validation or safety policy.

The App Command catalog is intentionally smaller than the API. Do not copy its
generated entries into this page. Read [App Commands](app-command-reference) for
cross-surface actions and the [CLI Reference](cli-reference) for target,
output, safety-flag, and cache details.

## Run on Desktop and Headless Hosts

The normal desktop server binds to loopback, with `47821` as the default port.
The port is configurable, so integrations should use CLI discovery or an
operator-supplied base URL rather than assume that value.

Headless mode serves the same REST application. It does not create missing
desktop capabilities: audio-device, overlay, accessibility, file-opening, and
other graphical operations can return an unavailable response even though they
appear in OpenAPI. Every action happens on the Jarvis host, not necessarily on
the computer running the client.

During warm-up, the listener can accept a connection before the full application
is ready. Most HTTP API requests wait for the full app and eventually receive
`503` with `Retry-After: 1` if startup does not finish. A WebSocket handshake is
accepted and closed with code `1013` so the client can reconnect instead of
waiting on a stalled handshake. The health response during desktop warm-up can
report `warming: true`, so health alone does not prove that feature routes are
ready.

A non-loopback bind is refused unless a Control key already exists. The global
guard also checks Host and supplied Origin values, and a remote browser cannot
exchange a key for a session over plain HTTP. Direct Bearer requests do not get
that transport check, so the operator must provide HTTPS or an encrypted tunnel.
These checks do not make a public listener low risk. Keep remote access on a
private network and restrict who can reach it.

## Read Responses and Errors

There is no single response envelope for the full API. Many successful routes
return JSON, session creation can return an empty `204`, and other routes can
return text, files, HTML, or XML. Most framework and route errors use a JSON
`detail` field, which may contain text or structured validation details. Some
feature routes return `ok: false` with an `error` field instead. Check the HTTP
status and content type before interpreting the body.

| Status | Common meaning | Safe first response |
|---|---|---|
| `200`, `201`, `204` | The operation completed; `204` has no body | Parse only the documented response shape, then verify visible state |
| `400` | Malformed request, untrusted Host, unknown config path, or missing explicit confirmation | Correct the request; do not retry unchanged |
| `401` | Missing, malformed, rotated, or rejected credential | Re-authenticate; never repeat the same failed key in a loop |
| `403` | Untrusted Origin, protected path, or denied operation | Fix the trust or permission boundary instead of forcing the call |
| `404` | Unknown route, resource, provider, secret slot, or command ID | Refresh discovery and verify the identifier |
| `409` | The request conflicts with current runtime state | Read current state, resolve the conflict, then decide whether to retry |
| `410` | A pending config change expired, was consumed, or is unknown | Create a new pending change and review it again |
| `422` | Query or body fields failed schema validation | Read the validation details and correct types, required fields, or allowed values |
| `500` | Storage or an internal operation failed | Preserve non-secret diagnostics and check local app health before retrying |
| `503` | Jarvis is warming up or the requested subsystem or host capability is unavailable | Wait briefly for startup, then inspect the feature's readiness |

Retry read-only requests with bounded backoff when transport or warm-up fails.
Do not automatically retry a mutation after a timeout: the server may have
completed it after the client stopped waiting. Read the resource or audit state
first, then decide whether another write is safe. There is no API-wide
idempotency-key or replay guarantee.

`--dry-run` is also client-side only. It prints a request preview and sends
nothing, so it cannot validate the body against the server, check current
runtime state, predict a response, or make the eventual write idempotent. A
route-specific field also named `dry_run` is a separate server feature and must
be interpreted from that route's schema.

## How It Fits Together

1. A desktop control, CLI command, App Command, or custom client starts an
   operation and identifies the running Jarvis target.
2. [Credentials and Secrets](credentials-and-secrets) supplies either the
   Control key to a trusted program or an app session to the browser. Provider
   credentials are separate and cannot authenticate this step.
3. The global web guard checks the Host, Origin when required or supplied, and
   the applicable credential before the selected route receives the request.
4. The route validates the input and calls the same underlying feature used by
   the app. The [CLI](cli-reference) adds terminal-oriented rendering and
   danger checks; [App Commands](app-command-reference) add a smaller stable
   cross-surface catalog.
5. Permissions, safety policy, connected-service access, and host capabilities
   apply where that route implements them. A valid Control key never creates a
   missing provider, desktop permission, audio device, or approval.
6. Jarvis returns the real route result. If a preferred provider or local
   capability is unavailable, the feature either follows its configured
   fallback path or reports that limitation; the API does not invent success.

The [Architecture Reference](architecture) explains how the web layer reaches
the lower feature layers without making HTTP transport itself the source of
business logic.

## Check That It Works

With Jarvis running, use the CLI's read-only authentication check:

```powershell
jarvis --json auth status
```

Success returns the selected base URL with `"reachable": true`. This verifies
target resolution, server reachability, and Control-key acceptance. It does not
prove that every provider, permission, or desktop-only capability is ready.

## Troubleshooting

| What you see | What it usually means | What to do |
|---|---|---|
| Authentication status reports `reachable: false` | The server is stopped, the target is wrong, or the key was rejected | Confirm the intended instance is running, then use the hidden `jarvis auth login` flow for that target |
| `Invalid or missing Jarvis credential` or `Invalid or missing Jarvis Control API key` | The outer guard or a key-only control route rejected the credential | Re-authenticate without printing or logging the key |
| `Untrusted Host header` or `Untrusted Origin header` | The request names an unconfigured host or comes from a foreign browser origin | Use the configured private address and trusted browser origin; do not weaken the guard |
| The `jarvis api` group or a new operation is missing | No schema is cached, the server is unreachable, or the 24-hour cache is older than the target | Run `jarvis refresh`, then invoke `jarvis api` while the intended server is reachable |
| Confirmation returns `410` | The pending change expired, was already used, or belonged to a previous process | Submit the original change again and review the new values and ID |
| A listed operation returns `503` on a headless host | The route exists, but its subsystem is still warming or needs a desktop capability | Check feature readiness and use a supported headless alternative where available |

## Next Steps

- Read the [CLI Reference](cli-reference) to use curated and OpenAPI-generated
  operations without building an HTTP client.
- Open the [App Command Reference](app-command-reference) when one action must
  remain consistent across voice, chat, desktop, CLI, and REST.
- Review [Credentials and Secrets](credentials-and-secrets) before storing,
  rotating, or removing the Control key or a provider credential.
- Use the [Architecture Reference](architecture) to understand the boundaries
  behind the web server and its feature routes.
