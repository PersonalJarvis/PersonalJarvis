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
last_reviewed: 2026-07-15
phase: "-"
audience: operator
tags: [control-api, rest, authentication, openapi, cli, automation]
related: [cli-reference, app-command-reference, credentials-and-secrets, architecture]
---

The Control API lets a trusted client inspect and operate a running Personal
Jarvis instance over HTTP. Use it when a terminal, local agent, headless
management tool, or custom integration needs a validated app operation instead
of screen automation.

Jarvis serves one web application with a broad REST surface under `/api/*`.
The focused `/api/control/*` routes are the part designed for configuration,
provider, language, credential-status, and control-key management. The same web
server also carries feature routes used by the desktop app and the Jarvis CLI.

## Before You Start

- Start the Personal Jarvis instance you intend to control.
- Use a trusted app session or that instance's Control API key. A provider key
  cannot authenticate the Control API.
- Keep the default loopback connection for same-computer use. For another
  computer, use an encrypted private connection and an explicitly configured
  host. Do not publish the Jarvis listener directly to the public internet.
- Decide whether you need the full OpenAPI-described REST surface, the smaller
  App Command catalog, or a curated CLI command before building a custom client.

> [!warning] Treat the Control API key as an administrator credential. A client
> that possesses it can reach the protected operations exposed by that Jarvis
> instance. Never place it in chat, voice input, source code, URLs, logs,
> screenshots, documentation, or shell history.

## Understand the API Surface

The following surfaces share one server but have different purposes:

- **`/api/*`** is the broad product API. It includes settings, tasks, missions,
  providers, tools, docs, and other mounted feature routes.
- **`/api/control/*`** is a focused control facade. It adds key-only
  authentication to most of its routes and exposes a machine-readable
  configuration allowlist.
- **`/api/commands`** describes a smaller, curated set of App Commands. Each
  command points to one existing REST operation shared by app surfaces.
- **WebSocket routes** carry live chat, progress, audio, and event streams.
  OpenAPI does not describe them, and the dynamic CLI does not turn them into
  commands.

The product documentation browser uses `/api/docs`. The interactive API schema
viewer is deliberately at `/api/_swagger`, while the OpenAPI document itself is
at `/api/openapi.json`.

## Discover Operations Safely

Start with read-only discovery rather than copying paths from an old script.

| Resource | Authentication | What it tells you |
|---|---|---|
| `GET /api/health` | No Control credential | Whether the server answers, plus its version; Host and supplied-Origin checks still apply |
| `GET /api/control/auth/probe` | Control key | Whether that key is accepted; success returns a small `ok` result |
| `GET /api/openapi.json` | Control key or app session | Mounted HTTP paths, methods, tags, parameters, bodies, and response schemas |
| `GET /api/_swagger` | Authenticated browser session | A browsable view of the current OpenAPI document |
| `GET /api/control/allowlist` | Control key | Configuration paths that the focused facade may change, with risk and restart metadata |
| `GET /api/commands` | Control key or app session | The curated App Command catalog and each command's backing endpoint |

The OpenAPI document currently does not declare its Bearer security scheme, even
though the running server enforces authentication. The Swagger page is useful
for discovery from an authenticated browser session, but it does not provide a
ready-made **Authorize** control for key-only `/api/control/*` calls. The Jarvis
CLI attaches the key independently and is the safer interactive client.

## Authenticate

Jarvis uses two credential lifecycles. A persistent Control API key is for
programmatic clients. A short-lived app session is for the desktop or browser
interface.

| Caller | Credential flow | Important boundary |
|---|---|---|
| Desktop app | A one-use bootstrap token becomes an opaque, HTTP-only session cookie | The bootstrap token cannot be replayed as a general API credential |
| Trusted browser | The locked screen accepts the Control key once and exchanges it for a session cookie | Exchange requires HTTPS or a direct loopback connection |
| Jarvis CLI | Local discovery or a saved profile supplies the target and Control key | Use the hidden login prompt; avoid an inline key argument |
| Custom non-browser client | A secret manager supplies the Control key as the HTTP Bearer credential | Never persist the value in code, request traces, or diagnostic output |

The outer security boundary accepts a valid Control key or app session for most
protected `/api/*` requests. Most `/api/control/*` routes then apply a narrower
route guard and require the Control key specifically. Key reveal, rotation, and
replacement are the exception: an authenticated app session may use them so the
in-app key panel can work.

Open **API Keys & Providers**, then choose the dedicated key tab named for
your assistant (for example **Nico Key**). There you can view the masked key,
copy it into a trusted secret manager, replace it with a key you choose (at
least 12 characters; letters, digits, and `. _ ~ -`), or regenerate a random
one behind a confirmation dialog. Replacement and regeneration immediately
invalidate the previous Control key, so every CLI profile and integration
using the old value must be updated. Over HTTP the same actions are
`GET /api/control/api-key`, `PUT /api/control/api-key`
(`{value, confirm: true}`), and `POST /api/control/api-key/rotate`
(`{confirm: true}`).

## Follow the Request Lifecycle

| Stage | What Jarvis checks or does | Typical failure |
|---|---|---|
| 1. Resolve target | The client chooses the running host and port; the local CLI can discover a non-default desktop port | Connection failure or stale target |
| 2. Guard the surface | Jarvis validates the Host, any supplied Origin, and the Control key or app session | `400`, `401`, or `403` before route code runs |
| 3. Apply route authentication | Key-only control routes reject an app session where a Control key is required | `401 Unauthorized` |
| 4. Validate input | FastAPI checks path, query, and request-body fields against the operation schema | `422 Unprocessable Entity` |
| 5. Run the operation | The route applies its own allowlist, safety, state, credential, and host-capability rules | Route-specific `400`, `403`, `404`, `409`, or `503` |
| 6. Return the result | The route reports its actual state change, output, or error | Route-specific JSON, an empty `204`, or a file response |

> [!warning] `--dry-run` and `--yes` are Jarvis CLI safeguards, not HTTP
> protocol features. A direct REST mutation is sent immediately. It receives
> only the checks implemented by the outer security layer and that particular
> route; it does not automatically open the app's conversational approval flow.

## Change Settings and Handle Confirmation

Use the focused control facade for a configuration change:

1. **Read the allowlist.** Find the exact dotted path, its description, risk
   tier, accepted type, and restart requirement.
2. **Read the current value.** `GET /api/control/config` refuses protected
   sections and reports whether the requested path is in the allowlist.
3. **Submit one change.** `PUT /api/control/config` runs allowlist and value
   validation before anything is written.
4. **Inspect the response.** A safe change returns `applied: true`. A change
   requiring review returns `needs_confirmation: true`, `applied: false`, and a
   single-use `pending_id`, together with the old and proposed values.
5. **Confirm or reject deliberately.** Use the matching confirm or reject
   operation only after comparing the values and restart flag.
6. **Read the value again.** Verify the stored result instead of assuming that a
   successful request changed the running capability immediately.

Pending configuration changes live in memory for up to five minutes. They are
consumed once, disappear on server restart, and return `410 Gone` when expired,
unknown, or already used.

Convenience routes for language, provider switching, secrets, and Control-key
rotation have their own behavior. Do not assume that every mutation creates a
pending confirmation. For human credential entry, prefer the protected
**API Keys & Providers** view described in [Credentials and Secrets](credentials-and-secrets);
the generic configuration API deliberately refuses protected secret paths.

## Map REST to the App and CLI

| Surface | How it maps to REST | Safety and discovery behavior |
|---|---|---|
| Desktop app | A labeled control calls its mounted feature route with an app session | Shows feature-specific validation and visible state |
| Curated CLI | A maintained `jarvis <group> <command>` wraps a common route | Uses readable arguments and an explicit danger policy |
| Dynamic CLI | `jarvis api <tag> <operation>` is generated from live or cached OpenAPI | Covers mounted GET, POST, PUT, PATCH, and DELETE operations |
| App Command | One stable catalog entry maps to exactly one route | Shared by supported voice, chat, desktop, CLI, and REST paths |
| Raw REST client | Calls the mounted route directly | Must implement credential handling, review, retries, and response parsing |

The dynamic CLI groups operations by their first OpenAPI tag and derives names
from operation IDs. It caches the schema for up to 24 hours and can fall back to
an older cache while the server is offline. Run `jarvis refresh` before the next
`jarvis api` call when a newly added route is missing.

The App Command catalog is intentionally smaller than the full API. It contains
curated actions with stable IDs, input schemas, danger metadata, and a named app
section. Read [App Commands](app-command-reference) when an action must also be
available through natural language. Use the [CLI Reference](cli-reference) for
target resolution, output modes, `--dry-run`, `--yes`, and schema-cache rules.

## Run on Desktop and Headless Hosts

The normal desktop server binds to loopback, with `47821` as the default port.
The port is configurable, so integrations should use CLI discovery or an
operator-supplied base URL rather than assume that value.

Headless mode serves the same REST application. It does not create missing
desktop capabilities: audio-device, overlay, accessibility, file-opening, and
other graphical operations can return an unavailable response even though they
appear in OpenAPI. Every action happens on the Jarvis host, not necessarily on
the computer running the client.

During headless warm-up, the listener can accept a connection before the full
application is ready. Requests wait for readiness; if startup does not complete
within the bounded warm-up window, the holding server returns `503` with a short
retry hint.

A non-loopback bind is refused unless a Control key already exists. The global
guard also checks trusted Host and Origin values, and a remote browser cannot
exchange a key for a session over plain HTTP. These checks are defense in depth,
not a reason to expose the listener publicly. Keep remote access behind an
encrypted private tunnel or private network and restrict who can reach it.

## Read Responses and Errors

There is no single response envelope for the full API. Many successful routes
return JSON, session creation can return an empty `204`, and download or preview
routes can return files or HTML. Most framework and route errors use a JSON
`detail` field, while a few older feature routes return `ok: false` with an
`error` field. Check the HTTP status before interpreting the body.

| Status | Common meaning | Safe first response |
|---|---|---|
| `200`, `201`, `204` | The operation completed or was accepted; `204` has no body | Parse only the documented response shape, then verify visible state |
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
first, then decide whether another write is safe.

## How It Fits Together

1. A desktop control, CLI command, App Command, or custom client starts an
   operation and identifies the running Jarvis target.
2. [Credentials and Secrets](credentials-and-secrets) supplies either the
   Control key to a trusted program or an app session to the browser. Provider
   credentials are separate and cannot authenticate this step.
3. The global web guard checks network identity, Origin when present, and the
   credential before the selected route receives the request.
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
| `Invalid or missing Jarvis credential` | No accepted Control key or app session reached the outer guard | Re-authenticate without printing or logging the key |
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
