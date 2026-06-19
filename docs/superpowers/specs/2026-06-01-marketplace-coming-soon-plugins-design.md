# Design — Wire the 7 "Coming Soon" Marketplace Plugins

**Date:** 2026-06-01
**Status:** Approved (design); pending implementation plan
**Area:** `jarvis/marketplace/`, `jarvis/ui/web/marketplace_routes.py`, `jarvis/ui/web/frontend/src/views/PluginsView.tsx`, `jarvis/channels/telegram.py` (reuse)

---

## 1. Goal & context

The Plugins view shows a "Coming Soon" strip with seven teasers — **Stripe, Cloudflare,
Discord, Google Drive, Gmail, Telegram, Asana**. This work turns all seven into real,
connectable plugins.

The "Coming Soon" strip is a hardcoded array (`COMING_SOON` in `PluginsView.tsx`); the
frontend already drops any teaser that gains a real catalog entry (`ComingSoonStrip`
filters names present in the live catalog). So the primary deliverable for most providers
is a **catalog entry** in `jarvis/marketplace/seed_catalog.json` — no new frontend
components.

### Hard requirements (from the maintainer)

1. **Auth preference order:** browser-login OAuth ("click a link") first; access-token
   paste only as a fallback where OAuth is not feasible.
2. **Connections must persist** across app close and PC restart, until the user
   *explicitly* disconnects. A plugin must **never silently disappear**. This is the
   single most important invariant — it is a recurrence of the
   `project_bug_oauth_plugin_disconnect_after_restart` bug class.
3. **Real wiring** ("richtige Anbindung"): a connected token must become a tool the
   Heavy-Duty Worker can actually call (via `assemble_claude_mcp_servers`), or a genuine
   capability (Telegram channel) — not a cosmetic "connected" badge.

### Cloud-first constraint

Per `CLOUD.md`: the base `python:3.11-slim` install must still boot. DCR (Stripe,
Cloudflare) and HTTP-MCP providers add **no** new hard dependency. `stdio` community MCP
servers (Discord) require Node/`npx` and are therefore a **desktop/best-effort** extra,
documented as such — never on the base critical path.

---

## 2. How the existing system works (verified)

- **Catalog schema** (`catalog.py`): a `PluginCatalog` of `PluginSpec`, each carrying
  exactly one `auth: AuthConfig` — a discriminated union over five modes:
  `pat_paste`, `oauth_device_flow`, `hosted_mcp_oauth_dcr`, `oauth_pkce_loopback`,
  `hosted_mcp_allowlist`. `_BaseAuth` uses `extra="forbid"` (typos fail loudly).
- **Catalog data**: `data/plugin_catalog.json` override wins if present; otherwise the
  in-package `seed_catalog.json` seed (cloud-first: a fresh clone has no `data/`).
- **Token persistence** (`token_store.py`): one Credential-Manager entry per plugin,
  key `plugin_{id}_tokens`, holding a JSON `Tokens(access, refresh, expires_at, extra)`.
  Survives restarts. PAT tokens have no `refresh` and are never touched by the scheduler.
- **Connect flows** (`marketplace_routes.py`): `POST /connect/pat` (validate + save),
  `POST /connect/start` + `GET /connect/poll/{flow}` (OAuth redirect/device).
- **Refresh scheduler** (`refresh_scheduler.py`): periodic; refreshes near-expiry tokens.
  Its `REVOKED` branch is the **only** non-user code path that deletes a token.
- **MCP bridge** (`mcp_bridge.py`): `assemble_claude_mcp_servers` turns every *connected*
  plugin's `mcp_server` spec + token into a claude-cli `mcpServers` entry
  (`stdio` → command/args/env, `http` → url + bearer header; `rest_wrapper`/unknown
  skipped). This is the "real wiring".
- **The persistence bug is already fixed** in `auth/oauth_dcr.py` (commit `091f41ca`):
  the issuing `client_id` + `token_endpoint` are persisted in `Tokens.extra` at
  `_exchange` and reused verbatim in `refresh()` (a refresh_token is bound to its issuing
  client_id per OAuth 2.0 §6). New DCR plugins inherit this for free.

---

## 3. Per-provider decisions (research-verified 2026-06-01)

Each row was verified by fetching the provider's live discovery documents / official docs.
Citations live in the research appendix (§9).

| Provider | Auth mode | One-click? | `mcp_server` wiring | Maintainer setup |
|---|---|---|---|---|
| **Stripe** | `hosted_mcp_oauth_dcr` | yes | `http` → `https://mcp.stripe.com` (Bearer) | none |
| **Cloudflare** | `hosted_mcp_oauth_dcr` | yes | `http` → `https://observability.mcp.cloudflare.com/mcp` | none |
| **Telegram** | `pat_paste` (`telegram_path`) | no (paste) | **none** → mirror token into `telegram_bot_token` secret + enable `TelegramChannel` | bot token via @BotFather |
| **Discord** | `pat_paste` (`bot`) | no (paste) | `stdio` → community `mcp-discord` (Node) | create bot + invite to a server |
| **Asana** | `oauth_pkce_loopback` | no | `http` → `https://mcp.asana.com/v2/mcp` (Bearer, `resource=…/v2`) | register Asana app → client_id |
| **Google Drive** | `oauth_pkce_loopback` (`drive.file`) | no | `http` → official Google Drive MCP (Bearer) | 1 Google Cloud OAuth client (shared) |
| **Gmail** | `oauth_pkce_loopback` (`gmail.readonly` + `gmail.send`) | no | in-repo Gmail REST tool (no Node) | same Google client + full verification |

### Verified facts that shape the design

- **Stripe / Cloudflare = true DCR**, confirmed by a live `registration_endpoint` in each
  `.well-known/oauth-authorization-server`. They drop in exactly like Notion/Linear: a
  catalog entry only, zero maintainer setup, and they inherit the committed persistence
  fix.
- **Asana DCR is dead**: the legacy `/sse` DCR endpoint was retired 2026-05-11; the
  supported V2 server explicitly does **not** support DCR and requires a pre-registered
  client. → `oauth_pkce_loopback` with a maintainer client_id. Two risks to resolve in
  the plan: (a) Asana documents only `https`/`oob` redirects — confirm a `127.0.0.1`
  loopback is accepted, else fall back to `pat_paste`; (b) the V2 MCP server needs a
  `resource=https://mcp.asana.com/v2` parameter at authorize/token time → small handler
  extension.
- **Google (Gmail + Drive)** has no official hosted MCP with DCR. Device flow excludes all
  Gmail/Drive scopes. → `oauth_pkce_loopback` against a maintainer-registered **Desktop**
  OAuth client. **One Google client covers both** (different scope subsets).
  - **Drive** uses `drive.file` → non-sensitive, no verification, **no 7-day expiry** →
    permanently connected.
  - **Gmail** read needs restricted scopes → in Google "Testing" status the refresh token
    **expires after 7 days**. The maintainer chose **full verification** (publish to
    Production + CASA security assessment) → permanent after that; until then it works in
    testing mode with a documented 7-day-reconnect caveat.
- **Discord**: no official MCP; OAuth *user* tokens cannot read/send channel messages
  (per docs, `messages.read` is RPC-only). Only a **bot token** has full REST access →
  `pat_paste` with `Authorization: Bot <token>` validation; wired to the community
  `mcp-discord` stdio server. Requires creating an Application+Bot and inviting it.
- **Telegram**: no OAuth at all. The repo already has a first-class bidirectional
  `TelegramChannel` (`jarvis/channels/telegram.py`, secret `telegram_bot_token`, long-poll,
  allowlist, `scrub_for_voice`, EventBus). "Connect Telegram" = validate the bot token and
  enable that channel — richer and more cloud-first than a duplicate Node MCP server.
  Validation quirk: Telegram puts the token in the **URL path** (`/bot<token>/getMe`),
  not an `Authorization` header.

---

## 4. Architecture & changes

### 4.1 Frontend — automatic, one cleanup

- Adding catalog entries makes the seven appear as connectable rows and removes them from
  "Coming Soon" automatically. No new components.
- Cleanup: remove the stale `"Linear"` entry from `COMING_SOON` (Linear already shipped).
- The existing `PatConnectDialog` already tolerates an empty `token_prefix`
  (Telegram/Discord) — no change needed. The OAuth dialogs (`OAuthRedirectDialog`,
  `DeviceCodeDialog`) already serve DCR + PKCE-loopback.

### 4.2 Catalog schema — one additive field (backend-only)

Extend `PatPasteAuth` with:

```python
auth_scheme: Literal["bearer", "bot", "telegram_path"] = "bearer"
```

Backward-compatible (defaulted). The frontend never reads it. `validation_endpoint` gains
an optional `{token}` placeholder semantics for `telegram_path`.

### 4.3 PAT validation — branch on `auth_scheme` (`connect_pat`)

- `bearer` (default, unchanged): `Authorization: Bearer <token>` → `GET validation_endpoint`,
  expect 200.
- `bot` (Discord): `Authorization: Bot <token>` → `GET validation_endpoint`, expect 200.
- `telegram_path` (Telegram): substitute the token into `validation_endpoint`'s `{token}`
  path segment, send **no** auth header, expect 200 **and** JSON body `ok == true`.

### 4.4 Telegram post-connect hook

On successful Telegram validation, in addition to saving `plugin_telegram_tokens`:
mirror the token into the canonical `telegram_bot_token` secret (Credential Manager) and
set `[integrations.telegram].enabled = true` via `config_writer` (lock + tempfile +
BOM-safe). Disconnecting Telegram clears the mirrored secret and flips `enabled` back.
`mcp_server` is `null` (the capability is the channel, not a worker tool).

### 4.5 `oauth_pkce_loopback` — `resource` parameter (Asana)

Add optional pass-through of an OAuth `resource` parameter on authorize + token requests,
driven by a new optional catalog field on `OAuthPkceLoopbackAuth`. Used by Asana
(`resource=https://mcp.asana.com/v2`); inert for Slack/Google.

### 4.6 OAuth-app-required entries (Asana, Gmail, Drive)

Ship the catalog entries with a placeholder `client_id`
(`REPLACE_WITH_JARVIS_<PROVIDER>_CLIENT_ID`, the established Slack precedent) plus a
per-provider `instruction_md` click-path. The maintainer registers the app, supplies the
client_id, and it is written into the `data/plugin_catalog.json` override (not committed).
Gmail + Drive share one Google client_id.

### 4.7 Gmail real wiring — small in-repo REST tool

To keep Gmail under the marketplace's own token model (persistence + the fixed refresh
logic) and avoid a Node dependency, wire Gmail via a small in-repo tool that calls the
Gmail REST API with the keyring access token (read inbox / send), rather than a stdio
community server that runs its own separate OAuth. Drive uses Google's official HTTP MCP
(accepts the bearer); Stripe/Cloudflare/Asana use their hosted HTTP MCP.

---

## 5. Persistence hardening (the bug — must not regress)

1. **DCR client_id persistence** — already committed (`091f41ca`). New DCR plugins
   (Stripe, Cloudflare) inherit it; **no static client_id** in their catalog entries.
2. **Revoke no longer deletes.** Change the scheduler's `REVOKED` branch (and
   `_plugin_status`) so a revoked/un-refreshable token is **marked `needs_reauth`**
   (a flag persisted in `Tokens.extra`, surfaced as the existing frontend `needs_reauth`
   status) instead of `store.delete()`. The plugin row stays visible with a "Reconnect"
   affordance; it disappears **only** on user-initiated `DELETE`. This directly satisfies
   the "never silently disappear" requirement.
3. **Regression test.** Simulate a restart (fresh `TokenStore.load`) followed by a refresh
   cycle and assert that **no** plugin — DCR, PKCE-loopback, or PAT — is auto-deleted; a
   simulated `invalid_grant` yields `needs_reauth`, not removal. PAT plugins are skipped by
   the scheduler (no refresh token) and survive untouched.

---

## 6. Build waves (staggered, as approved)

- **Wave 1 — zero setup, ship first:** Stripe + Cloudflare (DCR catalog entries) +
  persistence hardening (§5.2, §5.3) + tests. End-to-end after an app restart.
- **Wave 2 — token paste:** `auth_scheme` schema + validator branches (§4.2–4.3),
  Telegram (channel reuse, §4.4) + Discord (bot token + community MCP). Maintainer creates
  two bot tokens.
- **Wave 3 — OAuth link:** `resource` param (§4.5), Asana + Drive + Gmail catalog entries
  with placeholder client_ids + click-paths (§4.6), Gmail in-repo REST tool (§4.7).
  Maintainer registers the Google Cloud client + Asana app and supplies client_ids; Gmail
  works in testing mode until Google verification completes, then permanently.

---

## 7. Testing

- Contract/schema tests for the seven new catalog entries (valid against `PluginCatalog`).
- Unit tests for the three PAT `auth_scheme` branches (`bearer` / `bot` / `telegram_path`),
  including the Telegram token-in-path + `ok==true` assertion and the Discord `Bot` header.
- The persistence regression test (§5.3).
- A `mcp_bridge` smoke: connected Stripe/Cloudflare/Asana/Drive tokens produce correct
  `http` entries; Discord produces a `stdio` entry; Telegram produces **no** MCP entry.
- Telegram post-connect hook test (token mirrored to `telegram_bot_token`, channel enabled;
  cleared on disconnect).

---

## 8. Out of scope / explicitly deferred

- An in-UI OAuth-app registration wizard (we use placeholder client_id + click-path docs).
- Replacing the Discord community `stdio` MCP with an in-repo REST tool (a later hardening;
  noted as a decision point).
- Stripe/Cloudflare PAT fallbacks (DCR is zero-friction; PAT modes documented as future).
- The Vercel `hosted_mcp_allowlist` cloud proxy (unrelated existing deferral).
- Gmail going to Google "Production": the maintainer drives the verification/CASA process;
  this work only prepares the catalog entry, scopes, and instructions.

---

## 9. Research appendix — key citations

- **Stripe DCR:** `https://mcp.stripe.com/.well-known/oauth-protected-resource` →
  `https://access.stripe.com/mcp/.well-known/oauth-authorization-server`
  (`registration_endpoint` present, `token_endpoint_auth_methods_supported: ["none"]`,
  PKCE S256, `refresh_token` grant). PAT fallback: restricted key `rk_`, validate
  `GET https://api.stripe.com/v1/balance`. Docs: `https://docs.stripe.com/mcp`.
- **Cloudflare DCR:**
  `https://observability.mcp.cloudflare.com/.well-known/oauth-authorization-server`
  (`registration_endpoint` present; identical across `bindings`/`radar`). PAT fallback:
  API token, validate `GET https://api.cloudflare.com/client/v4/user/tokens/verify`.
  Docs: `https://developers.cloudflare.com/agents/model-context-protocol/`.
- **Asana:** V2 MCP `https://mcp.asana.com/v2/mcp` (no DCR; needs client_id +
  `resource=…/v2`); OAuth `https://app.asana.com/-/oauth_authorize` (+`/oauth_token`,
  `/oauth_revoke`), PKCE S256. PAT validate `GET https://app.asana.com/api/1.0/users/me`.
  Docs: `https://developers.asana.com/docs/using-asanas-mcp-server`,
  `/docs/oauth`, `/docs/personal-access-token`.
- **Discord:** bot token, validate `GET https://discord.com/api/v10/users/@me` with
  `Authorization: Bot <token>`; community MCP `barryyip0625/mcp-discord`
  (`npx -y mcp-discord`, env `DISCORD_TOKEN`). OAuth user tokens cannot read messages.
  Docs: `https://docs.discord.com/developers/topics/oauth2`, `/reference`.
- **Telegram:** bot token via `https://t.me/BotFather`; validate
  `https://api.telegram.org/bot<token>/getMe` (token in path, no header, body `ok==true`).
  Reuse `jarvis/channels/telegram.py` (`telegram_bot_token`). Docs:
  `https://core.telegram.org/bots/api`.
- **Gmail:** `oauth_pkce_loopback`, Desktop client; scopes `gmail.readonly` (restricted) +
  `gmail.send` (sensitive); **7-day refresh-token expiry in Testing status** →
  publish + CASA to make permanent. Docs:
  `https://developers.google.com/identity/protocols/oauth2`,
  `https://developers.google.com/workspace/gmail/api/auth/scopes`.
- **Google Drive:** `oauth_pkce_loopback`, same Desktop client; scope `drive.file`
  (non-sensitive → no verification, no 7-day expiry). Official HTTP MCP
  `https://drivemcp.googleapis.com/mcp/v1` (Developer Preview, self-registered client).
  Docs: `https://developers.google.com/workspace/drive/api/guides/configure-mcp-server`.
