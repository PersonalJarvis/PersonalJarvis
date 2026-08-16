# Portable skills — a marketplace for more than Jarvis

**Status:** app side live since 2026-08-16 · registry side open ·
**Registry:** [community-registry.md](community-registry.md) ·
**Packaging (plugins):** [agent-plugins-standard.md](agent-plugins-standard.md)

The registry publishes skills. A skill is a `SKILL.md` — instructions an
assistant follows — and that file format is not ours. The same file runs in
Claude Code, Cursor, Codex, Copilot and everything else that reads Agent
Skills, and `npx skills add` ([skills.sh](https://skills.sh), Vercel Labs)
installs it into whichever of those is configured locally.

So the store carries two kinds of skill, and says which is which:

| Flavor | Frontmatter | Where it runs |
|---|---|---|
| `jarvis` (default) | this app's schema: triggers, risk policy, execution mode, plugin coupling | Personal Jarvis |
| `portable` | the open format: `name` + `description`, plus whatever the publishing agent uses | every SKILL.md-reading agent, Jarvis included |

A publisher does not have to write for Jarvis to publish here. That is the
whole point: an open registry that only accepts its own dialect is a private
registry with extra steps.

## Two install commands per listing

Every skill card shows the commands that actually work for it, as tabs:

```
jarvis marketplace install <name>              # this app
npx skills add <owner>/<repo> --skill <name>   # every other agent
```

The second line is **derived, never stored** — `installStandard.ts`
(`skillsShTarget`) reads it out of the URLs the entry already carries:

1. `raw_url` on `raw.githubusercontent.com` is exact: the folder holding
   `SKILL.md` is the `--skill` argument, and a `SKILL.md` at the repository
   root has none (`npx skills add owner/repo` is then the whole command). A
   `refs/heads/<branch>` path prefix is consumed as the ref it is, so a branch
   name never poses as the skill folder.
2. Otherwise `source_url` on `github.com` gives `owner/repo`, and the entry
   name is used as the folder — which is what the registry publishes as
   `skills/<name>/SKILL.md`.
3. Anything else (no GitHub, no https) gets **no** skills.sh line. That
   installer resolves against github.com and nothing else, so inventing one
   would advertise a broken command.

Plugins never get the second line: a plugin carries an MCP server and a
sign-in flow, which that installer knows nothing about.

The storefront on personaljarvis.ai mirrors this file as
`src/lib/install-standard.ts`. Both surfaces must print the same string —
change one, change both, and keep both pinned by their tests.

## What the app does with a portable skill

`jarvis/skills/portable.py` is the second reading of a `SKILL.md`, tried only
after the strict schema rejected it. Strict when writing, tolerant when
reading: the authoring and creator services still validate against the full
schema, so a typo in a hand-written skill is caught where it is made.

* **Whitelist, not blacklist.** Only descriptive fields are adopted: `name`,
  `description`, `when_to_use`, `version`, `author`, `license`, `category`,
  `tags`, the three URLs, `token_budget_estimate`. Dashed spellings
  (`when-to-use`) fold onto the underscored ones.
* **Nothing that grants behaviour crosses over** — not even when a foreign
  file spells it exactly the way Jarvis does: `triggers` (fires by itself),
  `risk_policy` (lowers the confirmation tier), `auto_fire` (promotes into the
  matcher), `execution` (dispatches a background worker), `requires_tools`,
  `config`, and the plugin-coupling fields. A portable skill is instructions
  the assistant may follow, never a permission grant.
* **Tolerant, not silent.** Every dropped key is listed on the skill
  (`ignored_fields`), travels on `GET /api/skills`, and is shown in the Skills
  view under the portable notice. A file whose `name` is missing or malformed
  still lands as DRAFT with the old error — falling back to the filename would
  silently rename someone's skill.

Consent is unchanged: the install dialog shows the instructions verbatim
before anything is downloaded, because the text IS the skill.

## Feed contract (registry side — still open)

`CommunitySkillEntry` in `jarvis/marketplace/community_source.py` already
reads two new fields, both optional, both tolerated when absent or unknown:

```jsonc
{
  "name": "three-point-check",
  "description": "Summarize any topic in three bullets",
  "raw_url": "https://raw.githubusercontent.com/…/skills/three-point-check/SKILL.md",
  "source_url": "https://github.com/…",
  "flavor": "portable",                                  // or "jarvis"; absent = "jarvis"
  "compatible_agents": ["Claude Code", "Cursor", "Codex"] // display only, bounded
}
```

* An unknown `flavor` costs the word, not the entry — it falls back to the
  default rather than failing the index (BUG-016 class).
* `compatible_agents` is publisher-written free text that lands in the store
  UI, so the client bounds it: 8 entries, 32 characters each, deduplicated,
  non-strings dropped.

What the registry repo (`PersonalJarvis/marketplace`) still has to do:

- [ ] Accept `flavor` and `compatible_agents` in `submissions/<name>.json`
      and carry them into the compiled `index.json`.
- [ ] Validate a `portable` submission against the OPEN format — `name` and
      `description` present, name matching the shared slug rule, size limits,
      https-only URLs, secret scan — and **not** against the Jarvis schema.
- [ ] Keep publishing skills as `skills/<name>/SKILL.md` so
      `npx skills add <owner>/marketplace --skill <name>` resolves. Verify
      against the live CLI before the storefront announces it; the derivation
      above is built from the published layout, not from a test run of `npx`.
- [ ] Storefront: mirror the tabs and the "portable" mark, and let the submit
      form pick the flavor.

## Trust — unchanged, and why it holds for a foreign file

A portable skill widens the *format*, not the blast radius. Everything that
carried the weight before still carries it: registry CI, name ownership,
client-side re-validation, the consent dialog that shows the text, the
`ToolExecutor` as the single execution path, and the skill lifecycle. The
adapter's whitelist is the addition: a file from an agent Jarvis has never
heard of cannot arrive carrying a lowered risk tier or a trigger that fires it.
