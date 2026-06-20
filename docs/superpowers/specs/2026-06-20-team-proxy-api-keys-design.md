# Team / Hosted Proxy Mode for the API-Keys Section — Design

- **Date:** 2026-06-20
- **Status:** Approved (approach selected, full scope)
- **Author:** brainstorming session
- **Approach:** Dedicated, vendor-aware standalone proxy (`keyproxy/`) + client-side `base_url` wiring (Approach 1 of 3)

---

## 1. Context & Goal

Today every Jarvis install holds its own provider credentials per machine
(`get_secret`: Windows Credential Manager → ENV → `.env`). That is the
"solo / local key" model.

This feature adds a **second, team-shareable mode** modelled on the
"Serverlink" pattern: one set of real vendor keys lives **server-side** on a
small hosted proxy; each Jarvis client stores only a **proxy URL + a per-user
token** and never sees a real vendor key. The proxy swaps the per-user token
for the real key, forwards to the vendor, and records usage.

The four product decisions that fix the scope (from the brainstorm):

1. **Scope:** build **both** the client side *and* a shipped proxy server, for
   **all API-key provider classes** (Brain API providers, STT, TTS, Vision).
2. **Access:** **per-user tokens** — issue, list, revoke; usage attributed per
   token. (Subsumes the shared case: issue one token for the whole team.)
3. **Proxy role:** **lean pass-through + usage metering** per token (who, which
   provider, which model, how many tokens, estimated cost). **No** hard budget
   or rate-limit enforcement in v1.
4. **Client mode:** **one global "Team mode" switch** (one proxy URL + one
   token routes *all* providers), with **per-provider exceptions** so a
   provider can stay local/direct (e.g. local Whisper that must not leave the
   machine).

### Key code finding that shapes the design

`BrainProviderConfig.base_url` (`jarvis/core/config.py:300`) **already exists and
is parsed from TOML, but no provider consumes it.** OpenRouter and Grok use the
OpenAI SDK yet hardcode their endpoint as a module constant
(`base_url=BASE_URL`, `openrouter.py:38`); `claude-api`, `openai`, `gemini`
hardcode the vendor endpoint inside `_ensure_client()`. Groq-STT accepts an
`endpoint` constructor param the factory never passes. So the bulk of the
client work is **"actually thread `base_url`/endpoint through every provider"** —
useful on its own, independent of the proxy.

---

## 2. Non-Goals (YAGNI)

- **No hard budgets / rate-limits / model allow-lists** in v1 (metering only;
  these are a clean later addition since per-user tokens already exist).
- **No per-provider proxy URLs** — global + exceptions only.
- **No web admin UI for the proxy** in v1 — admin happens via authenticated
  HTTP endpoints + a small CLI.
- **No retroactive rework** of the OAuth-based workers' default auth. Job-agents
  (claude-cli, Codex) authenticate via **OAuth**, not an API key, so there is no
  key to centralise by default. Routing a worker through the proxy is an
  **optional extension** (§9) that deliberately flips that worker from OAuth to
  an API-key path and requires the proxy to hold a real vendor key.

---

## 3. Architecture Overview

Two independently-deployable units that share one wire contract (the
`provider_id` vocabulary and the `/p/{provider_id}/...` path shape).

```
┌─────────────────────────────┐         ┌──────────────────────────────────┐
│  Jarvis client (any host)   │  HTTPS  │  jarvis-keyproxy (a small VPS)    │
│                             │ ──────► │                                  │
│  provider._ensure_client()  │  proxy  │  /p/{provider_id}/...   passthru │
│    base_url = proxy/p/<id>  │  URL +  │   1. validate per-user token     │
│    api_key  = team token    │  token  │   2. look up real (base, key)    │
│                             │         │   3. rewrite auth → real key     │
│  [team_proxy] config:       │         │   4. forward + stream back       │
│    enabled, url,            │         │   5. meter usage → SQLite        │
│    token (Credential Mgr),  │         │                                  │
│    local_providers = [...]  │         │  /admin/tokens  issue/list/revoke│
└─────────────────────────────┘         │  /admin/usage   per-token report │
                                         │  real keys: ENV only, never logged│
                                         └──────────────────────────────────┘
```

The proxy is a separate root package, mirroring the project's existing
standalone services (`conductor/`, `board-backend/`): its own FastAPI app, its
own SQLite, its own Dockerfile, boots on a fresh `python:3.11-slim` with only
`fastapi` + `httpx` + `uvicorn` (no desktop/GPU extras — cloud-first compliant).

---

## 4. Component A — Client-side team mode

### A1. Endpoint resolver (single source of truth)

New helper in `jarvis/core/config.py`:

```
resolve_provider_endpoint(provider_id, *, vendor_base_url, vendor_secret)
    -> ResolvedEndpoint(base_url: str, credential: str, via_proxy: bool)
```

Logic, in order:
1. If team mode is **off**, or `provider_id` is in `local_providers` → return the
   vendor's own base URL + the real local key (`vendor_secret`). Unchanged
   behaviour.
2. Else (team mode on, provider not excepted) → return
   `f"{team_proxy.url}/p/{provider_id}"` as `base_url` and the **per-user token**
   as `credential`, `via_proxy=True`.

Every provider's `_ensure_client()` calls this exactly once and passes the
result to its SDK. The provider no longer hardcodes its endpoint.

### A2. Config model

New `TeamProxyConfig` (`jarvis/core/config.py`), section `[team_proxy]`:

| Field | Type | Notes |
|---|---|---|
| `enabled` | bool | master switch (default `false`) |
| `url` | str \| None | proxy root, e.g. `https://keys.acme.dev` |
| `local_providers` | list[str] | provider ids kept direct/local |

The **per-user token is a secret**, not stored in TOML: new Credential-Manager
slot `team_proxy_token` (added to `ALLOWED_SECRET_KEYS` + `PROVIDER_SECRET_*`).
Read via `get_secret("team_proxy_token", "TEAM_PROXY_TOKEN")`.

### A3. Per-provider wiring (the unconsumed-`base_url` fix)

Each provider `_ensure_client()` is changed to consume the resolver:

| Provider | SDK hook | Change |
|---|---|---|
| `claude-api` | `AsyncAnthropic(base_url=…)` | SDK supports `base_url`; pass resolved |
| `openai` | `AsyncOpenAI(base_url=…)` | pass resolved (was hardcoded default) |
| `openrouter` | `AsyncOpenAI(base_url=…)` | replace `BASE_URL` constant with resolved |
| `grok` | `AsyncOpenAI(base_url=…)` | replace `BASE_URL` constant with resolved |
| `gemini` | `genai.Client(http_options=HttpOptions(base_url=…))` | google-genai supports `http_options.base_url` |
| `groq-api` (STT) | constructor `endpoint=` | factory `build_stt_from_config` now passes resolved |
| `grok-voice` (TTS) | OpenAI-compatible client | pass resolved base_url |
| `cartesia` (TTS) | endpoint param | pass resolved |
| `gemini-flash` (TTS) | google-genai | `http_options.base_url` if metered route exists, else honest "not proxyable" |
| `elevenlabs` (TTS) | hardcoded host | add base_url override; document if SDK blocks it |

**Vision** providers reuse the same SDKs as the brain tier (OpenAI / Gemini for
screenshot analysis), so they consume the identical resolver via
`resolve_provider_endpoint("vision-<vendor>", …)` and need no separate hook —
they are covered by the same change, not a distinct one.

Where an SDK genuinely cannot override its endpoint, the provider stays
direct-only and the UI shows it as "not available via team proxy" (honest, not
silently ignored).

### A4. UI

- **Settings → API Keys:** a new "Team Mode" panel above the per-provider key
  list — toggle, proxy URL field, per-user token field (stored via existing
  `POST /api/secrets/team_proxy_token`), and a checkbox list of providers to
  keep local. A **Test** button reuses the existing `provider_test` path against
  the proxy.
- **Onboarding `ApiKeysStep`:** a top-level branch — "I have my own keys"
  (existing flow) vs "Connect to a team proxy" (URL + token, skips per-provider
  key entry).

When team mode is on, the per-provider key forms are hidden/disabled for
proxied providers (the client must not hold real vendor keys in team mode).

---

## 5. Component B — `keyproxy/` service

### B1. Layout (mirrors `conductor/`)

```
keyproxy/
  app.py            # FastAPI factory
  passthrough.py    # generic streaming reverse-proxy core
  vendors.py        # per-vendor auth placement + usage parser table
  tokens.py         # per-user token store (issue/list/revoke, hashed)
  usage.py          # usage recording + report
  store.py          # SQLite open/migrate
  schema.sql
  config.py         # provider→(real_base, real_key, vendor) mapping from ENV/file
  cli.py            # admin CLI (issue-token, list-tokens, revoke, usage)
  Dockerfile
  README.md
```

### B2. Passthrough core

One generic handler for `('/p/{provider_id}/{path:path}')`, all methods:

1. Extract the bearer/token from the inbound request (Authorization header,
   `x-api-key`, or `x-goog-api-key`/`?key=` for Gemini — per `vendors.py`).
2. `tokens.verify(token)` → 401 fail-closed if missing/unknown/revoked
   (constant-time hash compare).
3. `config.lookup(provider_id)` → `(real_base, real_key, vendor)`; 404 if
   unknown provider.
4. Build the upstream request: target `real_base + "/" + path`, copy method,
   query, body, and **safe** headers; **replace** the auth credential with
   `real_key` placed per the vendor's rule.
5. Stream the upstream response back unchanged (status, headers, body) via
   `httpx.AsyncClient.stream` → `StreamingResponse` (SSE-safe).
6. After completion, `usage.record(...)` best-effort from the parsed
   `usage`/`usageMetadata` (see B4). Metering never blocks or alters the
   response.

### B3. `vendors.py` — the only per-vendor knowledge

A small table keyed by `vendor`:

| vendor | inbound creds | outbound credential placement | usage parse |
|---|---|---|---|
| `openai_compatible` | `Authorization: Bearer` | `Authorization: Bearer <real>` | JSON/SSE `usage` |
| `anthropic` | `x-api-key` | `x-api-key: <real>` | `message_delta.usage` |
| `gemini` | `x-goog-api-key`/`?key=` | same → `<real>` | `usageMetadata` |

`provider_id → vendor` is part of the wire contract and gets the five-layer
anti-drift parity treatment (§7).

### B4. Usage metering (best-effort, lean)

Parse token counts from the upstream response: OpenAI/compatible emit `usage`
(streaming requires `stream_options.include_usage`; record what arrives,
otherwise `tokens=null`); Anthropic emits usage in `message_start` +
`message_delta`; Gemini emits `usageMetadata`. Estimated cost via a static
per-model price table (best-effort; unknown model → cost `null`). A parse miss
records the call with null counts — it never fails the request.

### B5. Stores

- **tokens**: `id, label, token_sha256, created_at, revoked_at`. Plaintext token
  shown once at issue; only the SHA-256 is persisted.
- **usage**: `id, token_id, provider_id, model, prompt_tokens, completion_tokens,
  total_tokens, est_cost, ts`.
- **real keys + base URLs**: from **ENV only** (`KEYPROXY_<PROVIDER>_KEY`,
  `KEYPROXY_<PROVIDER>_BASE`) plus an optional non-secret `keyproxy.toml` for the
  provider→vendor/base map. **Real keys are never written to disk or logs**
  (AP-12 discipline).

### B6. Admin surface

`/admin/tokens` (POST issue, GET list, DELETE revoke) and `/admin/usage`
(GET per-token/period report), guarded by an admin bearer
(`KEYPROXY_ADMIN_KEY`, ENV). Same operations via `cli.py`.

---

## 6. Security model

- **Real vendor keys never leave the proxy** and are never logged; clients in
  team mode hold only a per-user token.
- **Tokens hashed at rest** (SHA-256), constant-time compare, instant revoke.
- **Fail-closed everywhere**: missing/unknown/revoked token → 401; unknown
  provider → 404; the proxy never falls back to "no auth".
- **HTTPS required in production**: the proxy refuses to start token auth over
  plain HTTP unless `KEYPROXY_ALLOW_INSECURE=1` (dev only); TLS is terminated by
  the platform/reverse proxy (documented in README).
- **Header hygiene**: only an allowlist of headers is forwarded upstream; hop-by-
  hop and inbound auth headers are stripped before the real credential is set.
- **No secret on the voice/chat path** (AP-2 unaffected — tokens are entered in
  the UI/onboarding, never spoken).

---

## 7. Anti-drift (mandatory)

`provider_id` and the `provider_id → vendor` mapping are a wire-format
vocabulary spanning client config ↔ proxy ↔ usage rows ↔ UI. Apply the
five-layer pattern (`docs/anti-drift-three-layer.md`): one source-of-truth tuple,
a parity test asserting client and proxy agree on the provider set + vendor map,
mirroring `tests/unit/sessions/test_hangup_reason_parity.py`.

---

## 8. Error handling & honesty

Upstream errors pass through with their original status and are classified by the
**existing** `classify_provider_error` (`jarvis/brain/provider_test.py`) so the
client's Test button reports `bad_key` / `no_credits` / `rate_limited` /
`unreachable` honestly — including the team-mode-specific case "proxy reachable
but upstream key invalid" vs "proxy unreachable". The two must not be flattened
into one message.

---

## 9. Optional extension — workers via the proxy

Out of the v1 critical path, documented for completeness. `claude-cli` honours
`ANTHROPIC_BASE_URL` + `ANTHROPIC_API_KEY`; a team that *has* a real Anthropic
API account can point workers at the proxy by setting those in
`build_worker_env` when team mode is on, with the per-user token as the key.
This **flips that worker from OAuth to an API-key path** and requires the proxy
to hold a real Anthropic key — a conscious trade-off, off by default.

---

## 10. Testing strategy

- **Unit (client):** `resolve_provider_endpoint` (team on/off, exception list,
  missing token), `TeamProxyConfig` parse, per-provider `_ensure_client` passes
  the resolved base_url (with a fake SDK).
- **Unit (proxy):** token issue/verify/revoke + hashing; `vendors.py` credential
  placement per vendor; usage parser per vendor (fixtures for OpenAI SSE,
  Anthropic stream, Gemini); fail-closed auth/provider cases.
- **Integration:** a client brain provider pointed at a locally-spawned proxy
  instance backed by a fake upstream (httpx transport), round-tripping a
  streamed completion and asserting a usage row landed.
- **Parity:** provider↔vendor anti-drift test (§7).
- **Cloud-first:** proxy import + boot on slim deps only (no desktop extras).

---

## 11. Implementation waves

- **W1 — Client base_url wiring.** Resolver + thread `base_url`/`endpoint`
  through every provider (the unconsumed-field fix). Independently useful; no
  proxy yet. Tests per provider.
- **W2 — Team-mode client config + UI.** `TeamProxyConfig`, `team_proxy_token`
  slot, resolver flips to proxy, Settings panel + onboarding branch, Test reuse.
- **W3 — `keyproxy/` skeleton.** FastAPI + SQLite, generic streaming
  passthrough, `vendors.py`, provider mapping from ENV, fail-closed auth.
- **W4 — Tokens + metering.** Issue/list/revoke + admin auth + CLI; usage parse +
  recording + report endpoint.
- **W5 — Hardening.** HTTPS enforcement, full test matrix, anti-drift parity,
  Dockerfile + README, optional worker path doc (§9).

Each wave is committed hunk-isolated (shared working tree) and verified before
the next.
