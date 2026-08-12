# Community registry — how the marketplace works

**Status:** live since 2026-08-12 ·
**Decisions:** [public-marketplace-analysis.md](public-marketplace-analysis.md) ·
**Packaging:** [agent-plugins-standard.md](agent-plugins-standard.md)

Anyone can publish plugins and skills for Personal Jarvis; every submission
that passes automated checks is listed automatically — an open registry in
the ClawHub spirit, with **zero maintainer infrastructure**: GitHub pull
requests are the upload API, Actions is the validation pipeline, and Pages
is the CDN.

## The three repositories

| Repo | Role |
|---|---|
| `PersonalJarvis/PersonalJarvis` (this repo) | In-app half only: fetch the index, browse, one-click install, connect. Never hosts marketplace website code. |
| `PersonalJarvis/marketplace` | The registry: `submissions/`, CI validation, auto-merge gate, ownership ledger (`registry.json`), compiled `index.json` on GitHub Pages. |
| personaljarvis.ai storefront repo (maintainer-owned, separate) | Storefront: `/marketplace` (browse, client-side index fetch) and `/marketplace/submit` (the upload form → pre-filled GitHub PR). |

```
submit form ──► PR (one submissions/<name>.json) ──► CI checks ──► auto-merge
                                                                      │
              plugins/<name>/…  skills/<name>/SKILL.md  ◄── bot expansion
                                                                      │
              Pages: index.json ◄── compile          app + storefront fetch
```

## App-side pieces (this repo)

| Piece | Where |
|---|---|
| Manifest → `PluginSpec` converter (the "loader wave") | `jarvis/marketplace/agent_plugins_loader.py` |
| Index fetch + TTL cache (`data/marketplace_index.json`) | `jarvis/marketplace/community_source.py` |
| Install/uninstall persistence (`data/plugin_catalog.json`) | `jarvis/marketplace/community_install.py` |
| Usage cards second root (`data/usage_cards/`) | `jarvis/marketplace/usage_cards/loader.py` |
| REST: `GET/POST /api/marketplace/community…` | `jarvis/ui/web/marketplace_routes.py` |
| Community section UI + consent dialog | `frontend/src/views/PluginsCommunity.tsx` |
| Community skills in the Skill Finder pool | `jarvis/skills/finder.py` |
| Source URL override / kill switch | `jarvis.toml` → `[marketplace].community_index_url` (empty = off) |

An installed community plugin is a normal catalog entry with
`source: "community"` plus publisher/version provenance — it rides the
existing connect flows, relevance gate, and worker MCP bridge unchanged.
Never fetch the index on the boot critical path; the Plugins view triggers
the TTL-gated refresh.

## Trust model (no human review — these carry the weight)

1. **Registry CI** (`scripts/validate.py` there): naming rules + reserved
   built-in names, https-only URLs, stdio launcher allowlist
   (`npx`/`uvx`/`docker`) with pinned versions, `$plugin_…` placeholders
   only (no literal credentials anywhere), size limits, secret-pattern scan.
2. **Ownership**: first merged submission claims the name in
   `registry.json`; updates auto-merge only from the same GitHub account
   and must increase the version. The auto-merge gate runs trusted
   base-branch code and never executes PR content.
3. **Client re-enforcement**: `agent_plugins_loader.py` re-applies every CI
   rule at install time, so a poisoned index cannot smuggle what CI would
   have rejected. Keep the two rule sets in sync.
4. **Consent dialog** (app + storefront detail view): shows verbatim the
   MCP URL (where requests and the token go) or the stdio argv (what runs
   locally) BEFORE install. Community entries are badged "not reviewed".
5. Existing blast-radius limits apply unchanged: per-plugin connect
   isolation, per-turn relevance gate, `ToolExecutor` as the single
   execution path, skill Draft/Validated lifecycle.

Delisting a malicious entry = revert its submission in the registry repo;
the index redeploys in minutes. Installed copies are removed by the user
(Plugins → Community → Remove).

## Feed contract

`https://personaljarvis.github.io/marketplace/index.json` — shape mirrored
by `CommunityIndex` in `community_source.py` (tolerant models: unknown
fields never break older apps). Plugins embed their Agent Plugins v1.0.0
manifests verbatim; skills carry a `raw_url` the existing skill-catalog
install downloads.
