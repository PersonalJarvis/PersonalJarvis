# Public plugin marketplace — design analysis

**Status:** IMPLEMENTED 2026-08-12 — see [community-registry.md](community-registry.md)
for the shipped architecture ·
**Written:** 2026-08-12 ·
**Depends on:** [Agent Plugins standard adoption](agent-plugins-standard.md)

> **Decision record.** The maintainer resolved the open decisions the same
> day: **D1** = Model A1 storage with **CI-only auto-merge** (open registry,
> no human review queue — the D3 auto-merge variant), **D2** = plugins AND
> skills (metadata only), **D5** = one-click in-app install + live refresh,
> **D9** = storefront generated on personaljarvis.ai with a **web submit
> form** as the upload path. Registry repo:
> `github.com/PersonalJarvis/marketplace`. The rest of this document is the
> preserved analysis that led there.

The idea under analysis: a community-facing plugin marketplace (in the spirit
of a hub like LobeHub) where **anyone** can publish a plugin for the Jarvis
agent and the delegated agentic runs, **without that plugin's files ever
living in this repository**. This document assesses how the idea fits the
existing `jarvis/marketplace/` substrate, lays out the candidate distribution
models with their trade-offs, and lists the decisions that must come from the
maintainer before any code is written.

---

## 1. What already exists — the substrate is further along than it looks

The in-process half of a marketplace is **done**. Five building blocks:

| Building block | Where | What it already does |
|---|---|---|
| Catalog schema | `jarvis/marketplace/catalog.py` | Typed `PluginSpec` (branding, category, longevity badge, five auth modes as a discriminated union, `mcp_server` launch/transport block, `native_tool` binding) |
| Catalog loading | `jarvis/marketplace/catalog_data.py` | Package seed (21 plugins) **merged** with a user-owned override at `data/plugin_catalog.json`; purely local entries pass through verbatim; in-memory migrations heal stale overrides |
| Live runtime | `jarvis/marketplace/plugin_registry.py` | Per-plugin MCP connect with timeout isolation, re-auth flagging, `BrainToolsChanged` bus events, live connect/disconnect without restart (`refresh_plugin()`) |
| Worker export | `jarvis/marketplace/mcp_bridge.py` | The same connected plugins assembled into a claude-cli `mcpServers` config for delegated heavy-duty runs — agent and runs share one catalog and one token store |
| Per-turn gate | `jarvis/marketplace/plugin_relevance.py` | Keyword-only relevance filter (name / usage card / auto-derived nouns) so a connected plugin is only offered on turns that signal it |

Two further pieces matter for a public marketplace:

* **Packaging standard already adopted.** Since 2026-08-08 every new
  marketplace plugin must be an Agent Plugins v1.0.0 directory
  (`plugin.json`, optional `mcp.json`, Jarvis specifics under the
  `io.github.personaljarvis` extension namespace). The
  [standard doc](agent-plugins-standard.md) explicitly defers a
  "loader/aggregation wave" — a public marketplace **is** that wave.
* **"Install" is already a file write.** Because the catalog loader keeps
  purely local override entries, installing an external plugin reduces to
  writing its entry into `data/plugin_catalog.json` (via `config_writer`-style
  atomic IO) and calling `refresh_plugin()`. No new runtime concept is needed.

### Known gaps in the substrate (independent of any distribution model)

1. **Usage cards are package-only.** `usage_cards/loader.py` resolves cards
   from the package directory alone, so an externally installed plugin cannot
   ship the keyword card the relevance gate and prompt injector use. A second
   lookup root (e.g. `data/usage_cards/`) is required, with the same
   path-traversal guard.
2. **No manifest→catalog converter.** The Agent Plugins v1.0.0 directory
   format is policy, but nothing parses `plugin.json`/`mcp.json`/extension
   files into a `PluginSpec` yet (the deferred loader wave).
3. **No remote source.** `load_catalog()` knows two sources (seed, local
   override); a marketplace adds a third: a fetched, cached index.
4. **Trust metadata is absent.** `PluginSpec` has no notion of publisher,
   review status, version, or provenance — irrelevant while every entry was
   maintainer-authored, load-bearing the moment third parties publish.

---

## 2. What a plugin actually *is* here — and why that shrinks the problem

Most marketplace plugins are **metadata, not code**: a hosted
`streamable-http` MCP endpoint plus auth choreography, or a stdio launch
command (`npx …` / `docker run …`) whose executable already lives on npm,
PyPI, or a container registry. The marketplace therefore does **not** need to
host or execute contributed code — it needs to distribute **reviewed
manifests**. That has three consequences:

* Hosting is cheap: an index of small JSON/Markdown directories, no binary
  storage, no build farm.
* The trust question concentrates on two fields: the `mcp.json` **URL**
  (where the user's data flows) and the stdio **launch argv** (arbitrary
  command execution on the user's machine). Review effort goes there.
* Code-carrying extension types (native tools, channel plugins, Python
  entry-point plugins per contract §5) are a **separate, harder tier** — they
  ship executable Python and would need sandboxing or source review. They can
  be explicitly out of scope for v1 without weakening the connector story.

---

## 3. Candidate distribution models

### Model A — Git-based marketplace repository (index + PR review)

A separate public repo (e.g. `PersonalJarvis/plugin-marketplace`). Each
plugin is one Agent Plugins v1.0.0 directory; authors submit by pull request;
CI validates schemas, name constraints, URL provenance, and the language
rule; merge = published. CI compiles all directories into one static
`index.json` served from GitHub Pages (or raw/jsDelivr). The app fetches the
index, caches it under `data/`, and shows entries in the Plugins view;
install writes the entry into the local override.

* **Pros:** zero infrastructure and zero hosting cost; review is ordinary PR
  review with full provenance and history; versioning is git; CI is the
  automated half of the trust model; `gh`-CLI-only tooling matches the
  project's GitHub doctrine; the main repo stays completely clean; works for
  a fork or an air-gapped mirror (open-source universality §3).
* **Cons:** maintainer review is a throughput bottleneck; contributors need a
  GitHub account; publishing latency = review latency; ratings/stats need
  extra machinery (GitHub stars/reactions at best).

**A1 (monorepo):** plugin directories live in the marketplace repo itself —
Homebrew-core style. Simplest review and strongest guarantees (the reviewed
bytes are the shipped bytes).
**A2 (index-of-repos):** each plugin lives in its author's repo; the
marketplace repo only pins `name + repo + tag/commit`. More author autonomy,
but review must chase moving targets, and a pinned commit can differ from
what the author later shows users. A1 is the safer default; A2 can be added
per-plugin later for authors who insist.

### Model B — Decentralized sources ("taps")

Any git repo or URL can be a plugin source; users add sources in the UI;
an official curated source ships as default. Homebrew taps / Obsidian
community-plus-sideloading style.

* **Pros:** no gatekeeping at all; authors publish instantly in their own
  repo; the official index stays small and high-trust.
* **Cons:** trust fragments — the UI must carry a real "unreviewed source"
  warning; discovery is weak (nothing aggregates third-party sources);
  support burden ("plugin X from some repo broke") lands on the project.
* **Fit:** excellent as a **later additive layer** on top of Model A —
  the source abstraction ("a catalog fragment fetched from a URL") is the
  same code either way. Shipping B first would make "unreviewed" the default
  experience.

### Model C — Package-registry feed (PyPI / npm as the marketplace)

Plugins published as `jarvis-plugin-*` packages; the app searches the feed.

* **Pros:** existing infra, real versioning, dependency resolution.
* **Cons:** wrong shape for metadata-first plugins (a manifest does not need
  `pip install`); installing a Python package executes arbitrary code at
  install time — worse than reviewing a manifest; name squatting; no
  curation surface; discovery via PyPI search is poor. Note the stdio
  executables *already* come from npm/PyPI — Model A distributes the
  reviewed launch command, which is the right division of labor.
* **Fit:** poor as the marketplace itself; remains the natural channel for
  the *code* behind stdio servers, and the plausible future channel for the
  code-carrying tier (§2) if that tier ever opens.

### Model D — Hosted registry service (what LobeHub actually runs)

A web service with accounts, a publish API, search, ratings, stats, and a
public storefront site.

* **Pros:** best possible UX; instant publishing; social proof (installs,
  ratings); a web storefront markets the project itself; monetization
  becomes possible.
* **Cons:** real operations — a server, a database, auth, abuse handling,
  uptime, cost — owned by a project whose doctrine is "no maintainer
  infrastructure in the critical path". A registry outage would break
  install/browse for every user.
* **Fit:** premature now. Crucially, **Model A compiles to a static
  `index.json` — a static storefront website can be generated from the very
  same repo** (GitHub Pages), giving most of D's discovery value with none
  of its operations. A dynamic service can replace the static host later
  without changing the client, because the client only ever consumes the
  index contract.

### Trade-off summary

| | A: git index repo | B: taps | C: package feed | D: hosted service |
|---|---|---|---|---|
| Infra cost | none | none | none | server + ops |
| Review/trust | PR review + CI | per-source, mostly none | none | custom pipeline |
| Publish latency | review time | instant | instant | instant |
| Discovery | in-app + static site | weak | poor | best |
| Repo cleanliness | ✔ separate repo | ✔ | ✔ | ✔ |
| Offline/headless | cached index + seed | cached | needs feed access | needs service |
| Migration path | → B and → D later | — | — | — |

---

## 4. Trust and security analysis

What a malicious or sloppy community plugin could do, and where the existing
substrate already limits it:

| Threat | Vector | Mitigation |
|---|---|---|
| Data exfiltration | `mcp.json` URL points at an attacker host; user connects a real token to it | Review rule: MCP URL domain must provably belong to the named service (the catalog's existing entries all satisfy this); CI can enforce a domain/service match list |
| Arbitrary code execution | stdio `install` argv (`npx -y evil@latest`) | Highest-risk field. Options: restrict launchers to `npx`/`uvx`/`docker`; require version pinning; require human review of every argv change; risk-tier default stays `monitor` so calls surface in the review flow |
| Token theft via env | `env_template` leaks a token to an unexpected process | Placeholders resolve only `$plugin_<id>_access_token`; review any template that maps a token into a non-obvious variable |
| Prompt injection at scale | Tool descriptions carry injected instructions | Already partially mitigated: the relevance gate only exposes a plugin's tools on turns that signal it; tool output stays inside the existing risk-tier / review machinery |
| Index tampering | MITM or compromised host serves a poisoned index | HTTPS + caching; optionally sign the compiled index in CI (precedent exists — contract §7 already mandates signing keys live only in GH Actions secrets) and verify in the client |
| Rug pull / hijack | Author changes a published plugin's endpoint post-review | A1 monorepo makes every change a reviewed PR; pinned index versions mean updates are explicit |

Runtime blast-radius limits that already exist and apply unchanged to
community plugins: per-plugin connect isolation (one broken plugin degrades
to zero tools), the keyword relevance gate, risk tiers via
`MCPToolAdapter`, and `ToolExecutor` as the single execution path.

---

## 5. Cross-cutting concerns

* **Versioning.** The spec's `plugin.json` requires `version`; the compiled
  index should carry it plus an index-level revision. Client behavior to
  decide: auto-refresh the browse list (cheap, metadata only) vs. explicit
  per-plugin update (safer for `mcp_server` changes, since those alter what
  runs/where data flows).
* **Update cadence.** A fetched index is a cache: refresh on Plugins-view
  open with a TTL, plus a manual refresh. Never fetch on the boot critical
  path (contract §7).
* **Headless/offline.** Cached index under `data/` + the shipped seed keep
  every surface working with no network — same doctrine as the seed catalog.
* **Naming.** Spec name constraints (lowercase, `a-z 0-9 - .`, no
  underscores) already bind; community ids should carry no namespace prefix
  requirement initially, but the index must enforce uniqueness and reserve
  the existing 21 seed ids.
* **Usage cards + logos.** Submission requirements to decide: a usage card
  (keywords) strongly improves the relevance gate; a `logo_slug`/`logo_url`
  keeps the store visually consistent. Both fit in the
  `io.github.personaljarvis/` extension directory.
* **Runs parity is free.** Because `mcp_bridge.py` assembles the worker MCP
  config from the same catalog + token store, every installed community
  plugin reaches delegated agentic runs with no extra work. The only open
  question is whether workers should get *all* connected plugins or a
  per-run selection.
* **Language.** Index metadata is English (repo policy); usage-card keyword
  lists may carry the runtime matching vocabulary as today (`i18n-allow`).

---

## 6. Provisional recommendation (pending maintainer answers)

**Model A1 now, designed so B and D can be added without client changes:**

1. New public repo `PersonalJarvis/plugin-marketplace`; one Agent Plugins
   v1.0.0 directory per plugin; PR submission; CI validation (schemas, name
   rules, URL provenance, no credentials, English); CI compiles and publishes
   a static `index.json` (+ optionally a generated storefront page) via
   GitHub Pages.
2. In the app: a "remote source" catalog fragment (fetched, cached,
   TTL-refreshed) shown in the existing Plugins view under a Community
   section; install = atomic write into `data/plugin_catalog.json` +
   `data/usage_cards/` + `refresh_plugin()` — live, no restart.
3. Trust surface: "Reviewed" badge for the official index; the source
   abstraction keeps the door open for user-added sources (Model B) with an
   explicit warning banner; a signed index is a cheap hardening step.
4. Scope v1: metadata plugins only (hosted MCP / reviewed stdio launch
   commands / auth choreography). Code-carrying plugins (native tools,
   channels, entry-point plugins) stay repo-contributed until a sandbox
   story exists.

Why A1 over the runner-up (B): with no infrastructure and the same amount of
client code, A1 gives a reviewed, curated default experience — B without A
would make "unreviewed" the default and put the project's name on unvetted
manifests.

---

## 7. Open decisions (maintainer input required)

| # | Decision | Options (provisional lean in bold) |
|---|---|---|
| D1 | Distribution model | **A1 git marketplace repo**, B taps, C package feed, D hosted service |
| D2 | Publishable scope v1 | **Metadata connectors only**, + skills/CLI catalogs, + code-carrying plugins |
| D3 | Review model | **Maintainer PR review + CI, "Reviewed" badge**, CI-only auto-merge, open sources with warnings |
| D4 | Plugin content hosting | **Monorepo (A1)**, index-of-author-repos (A2), mixed |
| D5 | Install UX | **One-click in-app + live refresh**, CLI-first, manual manifest copy |
| D6 | Update behavior | **Auto-refresh browse + explicit per-plugin update**, full auto-update, manual only |
| D7 | Monetization | **None now, structure stays open**, paid tier planned, donation links only |
| D8 | Surfaces | **Live agent + delegated runs (shared catalog)**, agent only, per-run plugin selection |
| D9 | Storefront | **Generated static site from the index repo**, in-app only, dedicated web app |
| D10 | Submission bar | Usage card required? Logo required? Version pinning for stdio argv? Signed index at launch? |

Answers to D1–D8 determine the implementation plan; D9–D10 can be settled
during the build.
