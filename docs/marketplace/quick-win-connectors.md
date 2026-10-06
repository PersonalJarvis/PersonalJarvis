# Quick-win connectors (drafts)

Twenty-six hosted MCP services support the reference connect path of the
[browser-auth standard](browser-auth-standard.md): hosted MCP plus Dynamic
Client Registration (DCR) plus PKCE. A user clicks Connect, signs in at the
provider, approves, and is done. Nobody registers an app, copies a key or
edits a file.

Each one ships as a **draft**, not as a catalog entry. A new built-in plugin
needs a completed browser journey before it may enter
`jarvis/marketplace/seed_catalog.json`
(`scripts/ci/check_plugin_auth_contract.py`), and that journey needs a real
account at the provider. Until then the files live here:

| What | Where |
|---|---|
| Catalog entry (Agent Plugins manifest) | `jarvis/marketplace/drafts/<id>/plugin.json` |
| MCP server template | `jarvis/marketplace/drafts/<id>/mcp.template.json` |
| Usage card | `jarvis/marketplace/drafts/<id>/usage_card.md` |
| Paired skill | `jarvis/skills/drafts/plugin-<id>/SKILL.md` |
| Pending journey record | [`quick-win-pending-e2e.json`](quick-win-pending-e2e.json) |

`tests/unit/marketplace/test_quick_win_drafts.py` keeps every draft valid,
consistent with its template and skill, bound to its pending record, and out
of the shipped catalog.

## Drafts

Atlassian (Jira & Confluence) was promoted on 2026-10-05 after a complete
browser journey; see [`plugin-e2e-audit.json`](plugin-e2e-audit.json). The
remaining drafts:

| id | Service | MCP endpoint | Every tool asks first |
|---|---|---|---|
| `sentry` | Sentry | `https://mcp.sentry.dev/mcp` | no |
| `intercom` | Intercom | `https://mcp.intercom.com/mcp` | yes |
| `paypal` | PayPal | `https://mcp.paypal.com/mcp` | yes |
| `square` | Square | `https://mcp.squareup.com/mcp` | yes |
| `webflow` | Webflow | `https://mcp.webflow.com/mcp` | yes |
| `wix` | Wix | `https://mcp.wix.com/mcp` | yes |
| `neon` | Neon | `https://mcp.neon.tech/mcp` | yes |
| `netlify` | Netlify | `https://mcp.netlify.com/mcp` | yes |
| `miro` | Miro | `https://mcp.miro.com/` | no |
| `attio` | Attio | `https://mcp.attio.com/mcp` | no |
| `fireflies` | Fireflies | `https://api.fireflies.ai/mcp` | no |
| `zapier` | Zapier | `https://mcp.zapier.com/api/mcp/mcp` | yes |
| `pipedream` | Pipedream | `https://mcp.pipedream.net/v2` | yes |
| `jam` | Jam | `https://mcp.jam.dev/mcp` | no |
| `ramp` | Ramp | `https://mcp.ramp.com/mcp` | yes |
| `mercury` | Mercury | `https://mcp.mercury.com/mcp` | yes |
| `vimeo` | Vimeo | `https://mcp.vimeo.com/mcp` | yes |
| `amplitude` | Amplitude | `https://mcp.amplitude.com/mcp` | no |
| `mixpanel` | Mixpanel | `https://mcp.mixpanel.com/mcp` | no |
| `klaviyo` | Klaviyo | `https://mcp.klaviyo.com/mcp` | yes |
| `railway` | Railway | `https://mcp.railway.com/` | yes |
| `prisma` | Prisma Postgres | `https://mcp.prisma.io/mcp` | yes |
| `datadog` | Datadog (US1) | `https://mcp.datadoghq.com/api/unstable/mcp-server/mcp` | no |
| `grafana` | Grafana Cloud | `https://mcp.grafana.com/mcp` | no |
| `buildkite` | Buildkite | `https://mcp.buildkite.com/mcp` | yes |

"Every tool asks first" (`mcp_server.risk_tier: "ask"`) is set where a tool can
move money, publish, message other people or change running infrastructure.
Skills for brand names that are also everyday words (Square, Jam, Ramp,
Mercury, Neon, Railway, Prisma) carry no voice trigger, so ordinary speech
never routes to them.

Atlassian publishes no protected-resource document (RFC 9728). Its
`discovery_url` points straight at the RFC 8414 authorization-server metadata,
which `HostedMcpDcrHandler._discover` accepts as a discovery document.

## Probe evidence

Observed on 2026-10-05 with the shipped `HostedMcpDcrHandler`, without any
account and without user data, for every draft:

1. The MCP endpoint answered `initialize` with `401` and a bearer challenge.
2. Discovery returned authorization-server metadata with a
   `registration_endpoint`.
3. Dynamic Client Registration accepted a `http://127.0.0.1:<port>/callback`
   loopback redirect and issued a client.
4. The authorize URL opened the provider's own sign-in or consent page, with
   no redirect or client error.

This proves the connect flow can start. It does not prove consent, the
callback, a connection, or a real action. Those stages remain BLOCKED in the
pending record until an account holder completes them.

Services checked and left out:

| Service | Reason |
|---|---|
| Asana (move to DCR) | The current V2 server's authorization server (`app.asana.com`) has no registration endpoint; only the deprecated V1 SSE server offers DCR. The catalog entry stays on publisher PKCE. |
| tl;dv | Registration is refused (HTTP 403). |
| Replit | `mcp.replit.com` serves a web page, not an MCP endpoint. |
| Slite | The DCR metadata exists, but no MCP endpoint answered at the probed paths. |
| HubSpot, Zoom, PagerDuty, Pitch | OAuth without DCR: a registered app is required. |
| monday.com, Box, Calendly, Mailchimp, Zendesk, Typeform, Coda, Gamma, PostHog, Shopify | No hosted OAuth discovery at the probed host. |

## Promoting a draft

Do this per service, once someone with an account there can approve consent:

1. Copy the manifest's extension block (plus `id` and `description`) into
   `seed_catalog.json`; move `plugin.json` and `mcp.template.json` (renamed to
   `mcp.json`) to `jarvis/marketplace/plugins/<id>/`, the usage card to
   `jarvis/marketplace/usage_cards/<id>.md`, and the skill to
   `jarvis/skills/builtin/`. Register the skill in `_PLUGIN_PAIRED_SKILLS`.
2. Add the brand mark (`<id>.svg`) with its `LOGOS.md` row, a routing entry in
   `tests/fixtures/skill_routing/golden.yaml`, the reviewed endpoint in
   `scripts/ci/privacy_pre_push.py`, and a row in
   [`plugin-auth-audit.md`](plugin-auth-audit.md).
3. Run the full clean journey in the app (UI, browser, callback, connected,
   one real read-only action, reconnect, restart) and move the row from the
   pending record into [`plugin-e2e-audit.json`](plugin-e2e-audit.json) as
   PASS with the observed evidence.
