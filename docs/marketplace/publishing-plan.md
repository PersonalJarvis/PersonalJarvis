# Publishing — how someone gets a plugin INTO the marketplace

**Status:** design, not implemented · **Written:** 2026-08-14 ·
**Consumes:** [community-registry.md](community-registry.md) (the shipped
consumer half) · [agent-plugins-standard.md](agent-plugins-standard.md)
(the packaging policy) · [public-marketplace-analysis.md](public-marketplace-analysis.md)
(the decisions that led there)

The registry is live and the app installs from it. What is missing is the
door: **there is no way to publish that a normal person can walk through.**
This document decides the package standard, the upload paths, and the order
we build them.

---

## 1. Where we actually stand

| Half | State |
|---|---|
| Consume — fetch index, browse, consent, install, connect | **Shipped** 2026-08-12 |
| Registry — CI validation, auto-merge, ownership ledger, Pages index | **Shipped** 2026-08-12 |
| Publish — any interface a human uses to submit | **Missing entirely** |

Three concrete gaps, in order of how much they hurt:

1. **A broken public promise.** `PersonalJarvis/marketplace/README.md` tells
   every visitor to "use the form at <https://personaljarvis.ai/marketplace/submit>".
   That storefront repo does not exist. Today the only real path is: hand-write
   `submissions/<name>.json`, fork, open a PR. Nobody outside this project
   will do that.
2. **One file per submission.** `scripts/automerge_gate.py` matches
   `^submissions/[a-z0-9][a-z0-9.-]*\.json$` and requires the PR to change
   *exactly one* file. Bundled skills fit inside that file, so this is no
   longer a blocker for the standard — but a skill with `references/`, or
   any package that genuinely needs several files, still cannot auto-merge.
3. **Plugin and skill were split.** Our submission schema has
   `kind: "plugin" | "skill"` as mutually exclusive, and Agent Plugins
   v1.0.0 says a plugin *contains* skills and MCP servers. **Closed
   2026-08-14** on the client side: the two upload doors stay, but a plugin
   package may now bundle skills (§3).

---

## 2. What ClawHub does, and what we take from it

ClawHub's "Add a skill or plugin" screen makes four choices visible:

| ClawHub | Take it? |
|---|---|
| Publisher context first (personal account or org), changeable later | **Yes** — but personal-only in v1 (see §7, D3) |
| A `Skill` / `Plugin` toggle before anything else | **No** — see §3, this is the split we are removing |
| Source type: **Code plugin** (`package.json` + manifest, executable) | **No** — executable community code stays out of scope |
| Source type: **Bundle plugin** (`.zip` / `.tgz` archive upload) | **Partly** — accept a folder or zip as *input*, but the bytes land in git, not in blob storage |

The important divergence: ClawHub asks the author to classify their work
**up front**, then validates. We invert it — **the folder is the
classification**. The author submits an Agent Plugins directory; what is
inside it decides what kind of card it becomes. Fewer wrong turns, and it
matches the spec, where components are optional and additive.

The second divergence: ClawHub hosts archives. We keep Model A1 — *the
reviewed bytes are the shipped bytes*. A submission's files live in the
registry repo, so there is no archive to fetch at install time, no hash to
verify, no second host to trust, and delisting is a `git revert`.

---

## 3. The standard: two upload shapes, one package format

**Decision (maintainer, 2026-08-14): the two upload shapes stay.** You
publish *either* a skill *or* a plugin, because a skill genuinely is just a
Markdown file and asking its author to wrap it in a manifest is friction for
nothing. What changes is the plugin side: a plugin package may now **bundle
skills**, which is the combination Agent Plugins v1.0.0 exists for. The
author-facing reference is [package-layout.md](package-layout.md).

The submission file keeps `kind: "plugin" | "skill"` and stays ONE file per
PR — a bundled skill travels inside the plugin submission as
`skills: [{name, skill_md}]`, so the auto-merge gate needs no change for
this. Multi-file packages (a skill with `references/`) remain blocked by
the gate and are deliberately out of scope until someone needs them.

**Shipped 2026-08-14** (`convert_package` + `bundled_skills.py`): the loader
validates and installs bundled skills, ownership markers keep a plugin from
overwriting or deleting a skill it does not own, `$schema` is enforced, and
a package with no working component is rejected.

Below is the package layout underneath both shapes.

```
submissions/<name>/
├── plugin.json                     # REQUIRED — $schema, name, description, version, license
├── mcp.json                        # optional — exactly one server
├── skills/<skill-name>/SKILL.md    # optional, repeatable — instructions
│   └── references/*.md             # optional — text the skill may read
└── io.github.personaljarvis/
    ├── usage-card.md               # optional — relevance keywords + guidance
    └── logo.svg                    # optional — square mark
```

Within the plugin shape, what the author puts in decides the rest — the
store labels it, the author never sub-classifies it (this is where ClawHub
asks up front and we do not):

| Shape | Contains | Carries code? | Community |
|---|---|---|---|
| **Connector** | `plugin.json` + `mcp.json` + auth extension | No (endpoint or pinned launcher) | Allowed — this is what ships today |
| **Bundle** | connector + `skills/` | No | Allowed — the shape the spec exists for |
| **Skill** | one `SKILL.md`, uploaded as a skill | No (Markdown only) | Allowed — needs no manifest, no auth, no connect flow |
| **Native** | `extensions[…].native_tool`, Python entry point | **Yes** | **Blocked** — repo-contributed only, already rejected by `agent_plugins_loader.py` |

Four rules that keep the "no foreign code, no self-granted privilege" line
intact — all four enforced in `agent_plugins_loader.py` as of 2026-08-14:

1. **No `scripts/` in community skills.** The spec allows a skill to ship
   executable scripts; the index embeds only `SKILL.md`, and the registry
   rejects the directory on submission. Repo-contributed skills keep
   `scripts/` because they pass human review.
2. **No self-declared `risk_policy`.** `skills/runner.py` evaluates a
   skill's tools against the SKILL'S declared tier rather than the tool's
   own — an auto-merged author could otherwise mark a tool `safe` and skip
   its confirmation.
3. **stdio stays allowlisted and pinned** — `npx` / `uvx` / `docker`, exact
   versions, no `@latest`. Unchanged from today, enforced in both places.
4. **Every package carries a working component.** No MCP server, no hosted
   auth URL and no skills means a card that collects a token and does
   nothing.

**Why the bundle shape is worth having.** A bundle written for us runs
unchanged in ChatGPT, Cursor, Copilot, Kiro, and VS Code, and theirs run
here — they read `skills/` and `mcp.json` and ignore our extension
namespace, exactly as the spec intends. ClawHub's hard split gives that up
on the plugin side; we keep the friendly two-door upload AND the portable
package underneath.

**Back-compat is free.** `CommunityIndex` is already `extra="allow"`, so the
new `skills` block on a plugin entry is simply ignored by an older app,
which keeps installing the connector alone. The compiled `index.json` goes
on emitting both `plugins[]` and `skills[]`.

---

## 4. The upload paths, in build order

### Path 1 — `jarvis plugin …` (the CLI) · **Recommended first**

The contract already demands it (CLAUDE.md §5: a feature is a REST route
plus an auto-CLI), the audience already has Jarvis installed, and it costs
zero infrastructure.

```
jarvis plugin init <name>        # scaffold the directory, ask the 6 questions
jarvis plugin validate [path]    # the exact rules CI and the client apply
jarvis plugin publish [path]     # fork + branch + commit + PR, via gh
jarvis plugin status <name>      # PR checks, merge state, live in the index?
```

`publish` shells out to `gh` — already a hard dependency of our GitHub
doctrine, already authenticated on a contributor's machine, and it makes the
PR author identical to the publisher, which is what the auto-merge gate
checks. No OAuth app, no server, no secret to hold.

Why first: it is the smallest thing that turns "hand-write JSON" into "one
command", and every later surface calls the same validator underneath.

### Path 2 — Plugins → **Publish** (in-app)

The ClawHub screen, rebuilt inside the desktop app: publisher line, a form
for the six fields, drag a folder or zip onto it, live validation with the
real rule set, a diff-style preview of what the store card will look like,
then one button that runs the same `gh` flow. For contributors who do not
live in a terminal.

Needs a GitHub identity in the app. Use `gh auth token` when the CLI is
present; fall back to the GitHub **device flow** (no client secret, works
headless, prints a code the user types on github.com) — never ask for a PAT
in a text field.

### Path 3 — the storefront form (`personaljarvis.ai/marketplace/submit`)

The only path that reaches someone who has *not* installed Jarvis, and the
one the README already promises. **In build since 2026-08-14** in a parallel
effort — see [github-signin-implementation.md](github-signin-implementation.md)
(GitHub App, cookie session, Cloudflare Pages Functions, bot opens the PR)
and [github-login-analysis.md](github-login-analysis.md) for the decision.
The registry repo is private until that gate is green, so no submission path
is open at all right now. Nothing in this plan duplicates that work: it
supplies the *format* the form will upload and the *validator* it should
call.

---

## 5. One validator, three callers

Today the rules exist twice: `scripts/validate.py` in the registry and
`agent_plugins_loader.py` in the client, kept in sync by hand. A publish CLI
would make it three copies and guarantee drift.

**Decision: the client loader is the single implementation.** Extract the
rule set into `jarvis/marketplace/submission_rules.py`; the registry's CI
job installs the published `personal-jarvis` package and calls it. The
client keeps re-validating at install time — that is not duplication, it is
the deliberate defence against a poisoned index — but it runs *the same
code*, so "CI green" and "the app will accept this" can no longer disagree.

Pin the package version in the workflow and bump it deliberately, so a bad
release cannot silently change what the registry accepts.

---

## 6. Waves

**W1 — Client-side bundles. DONE 2026-08-14.** `convert_package` validates
a package's skills; `bundled_skills.py` writes them under the user's skills
root behind an ownership marker; install rolls back on a name conflict and
uninstall takes only what the plugin owns. `$schema` and the
working-component rule land with it. 10 new tests.

**W2 — Registry side of the same format.** Submission schema gains
`skills: [{name, skill_md}]` for `kind: "plugin"`; `validate.py` applies the
four §3 rules; `expand.py` writes a real `skills/<name>/SKILL.md` into the
package directory; `build_index.py` embeds them. Gate untouched — it is
still one file. *Done when:* a bundle submission auto-merges and the app
installs card plus skill from the live index.

**W3 — One validator.** Extract `submission_rules.py`, point the registry CI
at the installed package, delete the duplicated rules. *Done when:* one rule
change lands in one file and both sides move together, proven by a test that
feeds the same fixtures to both entry points.

**W4 — The CLI.** `init` / `validate` / `publish` / `status`, plus a
`--dry-run` that prints the PR body it would open. *Done when:* a fresh
clone on Windows, macOS, and Linux publishes a bundle end to end with no
hand-written JSON.

**W5 — In-app Publish view.** The form, drop target, live validation,
card preview, device-flow auth (the GitHub App already enables Device Flow
for exactly this — signin spec §7).

**W6 — Storefront upload.** Being built in parallel as the GitHub sign-in
gate; this plan does not duplicate it.

**Multi-file packages** (a skill with `references/`) stay out of scope until
someone asks: they need the gate widened to `submissions/<name>/**` with
path-traversal and size caps, which is a trust change, not a format one.

---

## 7. Open decisions for the maintainer

**D1 — Org publishing.** ClawHub lets you publish as an org. Our gate
compares `publisher` to the PR author, so an org login would never
auto-merge. Supporting it means an extra membership check in the gate.
*Recommended:* personal logins in v1, org support in a later wave — it is a
gate change, not a format change, so it costs nothing to defer.

**D2 — Verified publishers.** Nothing today distinguishes a first-time
account from a known one. A cheap version: badge publishers whose GitHub
account is older than N months or who own a merged package that has survived
M weeks. *Recommended:* defer until there is a listing worth impersonating.

**D3 — Package size cap.** Once `references/` is allowed, the registry repo
grows with every submission and it is the CDN. *Recommended:* 256 KB per
package and 25 files, generous for text and small enough that Pages stays
fast.

**D4 — Delisting on report.** Today: open an issue, maintainer reverts.
*Recommended:* keep it manual — an automated takedown is itself an attack
surface, and revert-plus-redeploy is already minutes.
