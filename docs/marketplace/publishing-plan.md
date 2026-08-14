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
   *exactly one* file. Anything multi-file — a skill with `references/`, a
   bundle carrying both skills and an MCP server — cannot auto-merge. The
   format is capped by the gate, not by the spec.
3. **Plugin and skill are split.** Our submission schema has
   `kind: "plugin" | "skill"` as mutually exclusive. Agent Plugins v1.0.0
   says the opposite: one plugin *contains* skills and MCP servers. We
   adopted the standard for connectors only and never took the `skills/`
   half of it.

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

## 3. The standard: one package format, three shapes, one locked tier

**Decision: drop `kind`. Every submission is an Agent Plugins v1.0.0
directory.**

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

What the author puts in decides the shape — the store labels it, the author
never picks it:

| Shape | Contains | Carries code? | Community |
|---|---|---|---|
| **Connector** | `plugin.json` + `mcp.json` + auth extension | No (endpoint or pinned launcher) | Allowed — this is what ships today |
| **Skill pack** | `plugin.json` + `skills/` | No (Markdown only) | Allowed |
| **Bundle** | both of the above | No | Allowed — the shape the spec exists for |
| **Native** | `extensions[…].native_tool`, Python entry point | **Yes** | **Blocked** — repo-contributed only, already rejected by `agent_plugins_loader.py` |

Three rules that keep the "no foreign code execution" line intact:

1. **No `scripts/` in community skills.** The spec allows a skill to ship
   executable scripts; we reject that directory on submission. A community
   skill is instructions and reference text. Repo-contributed skills keep
   `scripts/` because they pass human review.
2. **stdio stays allowlisted and pinned** — `npx` / `uvx` / `docker`, exact
   versions, no `@latest`. Unchanged from today, enforced in both places.
3. **`references/` is text only** — an extension allowlist (`.md`, `.txt`,
   `.json`, `.csv`), a per-file cap, and a total-package cap.

**Why this is worth the migration.** A bundle written for us runs unchanged
in ChatGPT, Cursor, Copilot, Kiro, and VS Code, and theirs run here — they
read `skills/` and `mcp.json` and ignore our extension namespace, exactly as
the spec intends. ClawHub's hard Skill/Plugin split gives up that
portability. This is the one place where following the standard more
literally than the competition is a straight product advantage.

**Back-compat is free.** The compiled `index.json` keeps emitting both
`plugins[]` and `skills[]`; the index builder projects each package into the
lists an older client expects (a skill-pack with one skill also appears as a
`skills[]` entry with a `raw_url`). `CommunityIndex` is already
`extra="allow"`, so new fields never break an old app.

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
one the README already promises. Needs a repo, hosting, a domain, and a
GitHub OAuth app that opens the PR on the author's behalf. Highest cost,
lowest urgency — but until it exists, **fix the README** so it points at the
CLI instead of a dead link.

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

**W1 — Unblock the format.** Auto-merge gate accepts
`submissions/<name>/**` (added/modified only, path-traversal-safe, capped
file count and total size). Submission schema gains the directory form; the
single-file JSON keeps working. Expansion and index build handle `skills/`.
*Done when:* a two-file skill-pack PR auto-merges and shows up in
`index.json`.

**W2 — One validator.** Extract `submission_rules.py`, point the registry CI
at the installed package, delete the duplicated rules. *Done when:* one rule
change lands in one file and both sides move together, proven by a test that
feeds the same fixtures to both entry points.

**W3 — The CLI.** `init` / `validate` / `publish` / `status`, plus a `--dry-run`
that prints the PR body it would open. *Done when:* a fresh clone on Windows,
macOS, and Linux publishes a skill pack end to end with no hand-written JSON.

**W4 — Client-side skill packs.** The loader learns `skills/`: install writes
them under the user's skills root (name validation is already a security
boundary in `community_source.py`), lifecycle stays Draft until the user
promotes them. *Done when:* installing a bundle yields both a store card and
a draft skill, and uninstall removes both.

**W5 — In-app Publish view.** The form, drop target, live validation,
card preview, device-flow auth.

**W6 — Storefront.** Separate repo, submit form, OAuth app. Until then, W3
lands and the README points at the CLI.

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
