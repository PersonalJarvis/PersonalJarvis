# Agent Plugins standard — adoption policy and migration audit

**Policy effective:** 2026-08-08 ·
**Standard:** [Agent Plugins v1.0.0](https://agent-plugins.org/)
([specification](https://agent-plugins.org/specification) ·
[schemas](https://agent-plugins.org/schemas) ·
[spec repository](https://github.com/agentplugins/agent-plugins-spec))

Personal Jarvis adopts the vendor-neutral **Agent Plugins** standard as the
packaging format for marketplace plugins (the connectors shown in the app's
Plugins store: GitHub, Notion, Slack, Gmail, …).

## Policy — binding for every new plugin

1. **New marketplace plugin submissions MUST be packaged as an Agent Plugins
   v1.0.0 directory**: a folder whose root contains a `plugin.json` with the
   canonical `$schema` (`https://agent-plugins.org/schemas/1.0.0/plugin.schema.json`)
   and a spec-conformant `name`.
2. A plugin that exposes an MCP server declares it in a root **`mcp.json`**
   (`$schema` `https://agent-plugins.org/schemas/1.0.0/mcp.schema.json`,
   `mcpServers` map, `type` one of `stdio` / `streamable-http` / `sse`) —
   never inline in `plugin.json` and never with credentials in `headers`.
3. Everything Jarvis-specific — auth flows, branding, category, longevity
   badge, native-tool or channel binding, post-install hints — lives in the
   client extension namespace **`io.github.personaljarvis`** (manifest data
   under `extensions["io.github.personaljarvis"]` in `plugin.json`, files
   under an `io.github.personaljarvis/` directory). Other clients ignore that
   namespace, exactly as the spec intends.
4. Existing catalog entries are migrated per the audit below. The loader
   wave has landed for COMMUNITY plugins: `agent_plugins_loader.py` converts
   Agent Plugins v1.0.0 manifests from the
   [community registry](community-registry.md) into `PluginSpec` at install
   time. The runtime keeps consuming `jarvis/marketplace/seed_catalog.json`
   unchanged for the shipped catalog — this document tracks WHAT must change
   per seed plugin; that migration remains open.

## How the current catalog maps onto the standard

| Today (one entry in `seed_catalog.json`) | Agent Plugins v1.0.0 location |
|---|---|
| `id` | `plugin.json` → `name` (constraints: 1–64 chars, `a-z 0-9 - .` only, alphanumeric start/end, no `--` / `..` — **underscores are illegal**) |
| `description` | `plugin.json` → `description` |
| `mcp_server` (`transport: "http"`) | `mcp.json` → `mcpServers.<id>` with `type: "streamable-http"` and the same `url` |
| `mcp_server.auth_header_template` | **Must NOT move to `mcp.json`** — the spec forbids credentials in `headers`. Token injection stays a client concern in the extension namespace. |
| `auth` (all five modes), `oauth_client_family` | `extensions["io.github.personaljarvis"]` |
| `display_name`, `category`, `logo_*`, `featured`, `longevity`, `longevity_note`, `post_install_hint_md`, `future_v2_note` | `extensions["io.github.personaljarvis"]` |
| `native_tool` (Vercel, Google trio, Home Assistant) | `extensions["io.github.personaljarvis"]` — such a plugin is valid under the spec with `plugin.json` alone (components are optional), but carries nothing portable |
| Channel enablement (Discord, Telegram) | `extensions["io.github.personaljarvis"]` — same extension-only shape |

Target layout per plugin (seed side; the gitignored `data/` override keeps its
merge semantics):

```
jarvis/marketplace/plugins/<name>/
├── plugin.json                      # $schema, name, description, version, license
├── mcp.json                         # only when a hosted/stdio MCP server exists
└── io.github.personaljarvis/        # auth, branding, longevity, hints (extension files)
```

## Migration audit — status as of 2026-08-08

**No plugin conforms yet.** All 21 catalog entries live inline in
`seed_catalog.json`; none is a directory with a `plugin.json`. Every plugin
therefore needs the **baseline migration** (create the directory, write
`plugin.json`, move Jarvis-specific fields into the extension namespace).
The table below marks what EACH plugin needs on top of that baseline.

Legend: ☐ = needs update (nothing is migrated yet) · **rename** = the id
violates the spec's name constraints and changes for the standard package
(the internal catalog id may keep a compatibility alias).

| Plugin | Status | Beyond the baseline |
|---|---|---|
| GitHub | ☐ needs update | `mcp.json` (`streamable-http`, `https://api.githubcopilot.com/mcp/`); bearer-token injection moves to the extension namespace |
| Vercel | ☐ needs update | Extension-only (native tool, no MCP server); nothing portable until Vercel's hosted MCP allowlist lands |
| Supabase | ☐ needs update | `mcp.json` (`streamable-http`, `https://mcp.supabase.com/mcp?read_only=true`) |
| Notion | ☐ needs update | `mcp.json` with the primary `streamable-http` server; the `/sse` fallback maps to the spec's deprecated `sse` type — keep it as a second entry or drop it |
| Slack | ☐ needs update | `mcp.json` (`streamable-http`, `https://mcp.slack.com/mcp`); PKCE flow + own-client family to the extension namespace |
| Linear | ☐ needs update | `mcp.json` (`streamable-http`, `https://mcp.linear.app/mcp`) |
| Stripe | ☐ needs update | `mcp.json` (`streamable-http`, `https://mcp.stripe.com`) |
| Cloudflare | ☐ needs update | `mcp.json` can carry ALL four servers (observability, bindings, radar, graphql) as separate `mcpServers` entries — today the extra three hide in `auth.capabilities` |
| Discord | ☐ needs update | Extension-only (channel plugin, no MCP server) |
| Telegram | ☐ needs update | Extension-only (channel plugin, no MCP server) |
| Asana | ☐ needs update | `mcp.json` (`streamable-http`, `https://mcp.asana.com/v2/mcp`) |
| Google Drive | ☐ needs update · **rename** `google_drive` → `google-drive` | Extension-only (native tool; Google's hosted Drive MCP still 403s consumer accounts) |
| Gmail | ☐ needs update | Extension-only (native tool) |
| Google Calendar | ☐ needs update · **rename** `google_calendar` → `google-calendar` | Extension-only (native tool) |
| Todoist | ☐ needs update | `mcp.json` (`streamable-http`, `https://ai.todoist.net/mcp`) |
| ClickUp | ☐ needs update | `mcp.json` (`streamable-http`, `https://mcp.clickup.com/mcp`) |
| Dropbox | ☐ needs update | `mcp.json` (`streamable-http`, `https://mcp.dropbox.com/mcp`) |
| Canva | ☐ needs update | `mcp.json` (`streamable-http`, `https://mcp.canva.com/mcp`) |
| Airtable | ☐ needs update | `mcp.json` (`streamable-http`, `https://mcp.airtable.com`) |
| Cal.com | ☐ needs update · **rename** `cal_com` → `cal-com` | `mcp.json` (`streamable-http`, `https://mcp.cal.com/mcp`) |
| Home Assistant | ☐ needs update · **rename** `home_assistant` → `home-assistant` | Extension-only (native tool; the instance URL is user data and can never be packaged) |

Tally: 14 plugins gain a portable `mcp.json`; 7 are extension-only
(Vercel, Discord, Telegram, Google Drive, Gmail, Google Calendar,
Home Assistant); 4 need a spec-conformant rename.

## Definition of done for migrating one plugin

- [ ] Directory `jarvis/marketplace/plugins/<name>/` exists; `<name>` passes
      the spec's name constraints
- [ ] `plugin.json` with canonical `$schema`, `name`, `description`,
      `version`, `license` validates against the published schema
- [ ] Hosted MCP server (when present) declared in `mcp.json` with
      `type: "streamable-http"`; **no credentials in `headers`**
- [ ] All Jarvis-specific fields moved under
      `extensions["io.github.personaljarvis"]`; nothing Jarvis-specific
      remains at top level
- [ ] Seed aggregation still serves the identical `/api/marketplace/plugins`
      payload (existing tests stay green; connected users see no change)
- [ ] Renamed plugins keep a compatibility alias so stored tokens and the
      `data/` override still resolve

## Where this policy is enforced

- `CONTRIBUTING.md` → "Plugin, tool, or skill?" states the submission rule
  and links here.
- `jarvis/marketplace/catalog.py` (the catalog schema) points here so any
  agent editing the catalog sees the policy before adding an entry.
