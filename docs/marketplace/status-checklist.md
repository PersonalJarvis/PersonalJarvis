# Marketplace — where we stand

**Measured:** 2026-08-14 · **Scope:** the whole chain, publisher to
installed plugin · **Reads:** [community-registry.md](community-registry.md)
· [publishing-plan.md](publishing-plan.md) ·
[package-layout.md](package-layout.md) ·
[github-signin-implementation.md](github-signin-implementation.md)

> **The marketplace UI is BETA.** The Plugins → Community view in the
> desktop app (`jarvis/ui/web/frontend`, package version 0.1.0 while the app
> is 1.3.2) is a working beta surface, not a finished one. Its layout, its
> wire types, and its copy will keep moving while the publishing half is
> built. Nothing outside this repo should treat its shapes as a contract —
> the stable contract is `index.json` (§ feed) and the REST routes, not the
> view.

---

## 1. Status at a glance

| Piece | State |
|---|---|
| **Consume** — index fetch, TTL cache, offline degradation | ✅ shipped |
| **Consume** — browse, filter, consent dialog, one-click install | ✅ shipped (beta UI) |
| **Consume** — installed plugin joins connect flow, relevance gate, worker bridge | ✅ shipped |
| **Format** — Agent Plugins v1.0.0 manifests → `PluginSpec` | ✅ shipped |
| **Format** — a plugin may bundle skills; ownership markers; `$schema`; component rule | ✅ shipped 2026-08-14 (client side only) |
| **Registry** — CI validation, auto-merge, ownership ledger, Pages index | ✅ live, but see §2 |
| **Registry** — knows about bundled skills | ❌ not started |
| **Publish** — any interface a human can use | ❌ not started |
| **Publish** — GitHub sign-in gate + storefront submit | 🔨 in build (parallel effort, spec written) |
| **Storefront** — the site itself | ❌ no repo exists yet |
| **Frontend ↔ backend** — bundled skills visible in the UI | ❌ backend sends, UI ignores |

---

## 2. What is broken right now (measured, not assumed)

The registry repo was set **private** on 2026-08-14 to close submissions
until the login gate exists. GitHub Pages keeps serving the compiled index,
but the index **points at URLs inside the now-private repo**:

| Request | Result |
|---|---|
| `GET personaljarvis.github.io/marketplace/index.json` | **200** — the feed still serves |
| `GET raw.githubusercontent.com/…/skills/three-bullet-brief/SKILL.md` | **404** — the skill download in that feed |
| `GET github.com/PersonalJarvis/marketplace/tree/main/plugins/sentry` | **404** — the "view source" link on every card |

So today a user browsing the Community tab sees one plugin and one skill;
installing the **plugin** works (its manifests are embedded in the index),
installing the **skill** fails, and every provenance link is dead. The store
looks open and is half closed.

Three ways out, cheapest first:

1. **Embed skills the way plugins are embedded.** A skill is one Markdown
   file; putting `skill_md` in the index removes the second host entirely
   and makes the feed self-contained. Same move already made for bundled
   skills. Fixes the download permanently, private repo or not.
2. Reopen the repo (spec §8 step 5) — but that reopens submissions before
   the gate exists, which is what the private switch was for.
3. Ship the index with a `source_url` that degrades honestly ("source not
   public") instead of a 404 link.

Recommended: **(1) plus (3)** — self-contained feed, honest links. It is the
same code path the bundled-skill work just introduced, and it removes a
whole class of "the store points somewhere it cannot reach".

---

## 3. Frontend ↔ backend: what is wired and what is not

**Wired and working:** `PluginsCommunity.tsx` calls
`GET /api/marketplace/community`, `POST …/refresh`,
`POST …/plugins/{name}/install`, `DELETE …/plugins/{name}`,
`POST /api/skills/catalog/install`, `DELETE /api/skills/{name}`. Cards,
filters, category counts, update badges and the consent dialog all read
fields the backend actually sends.

**Not wired — four gaps, in the order they hurt:**

| # | Gap | Consequence |
|---|---|---|
| 1 | The consent dialog shows the MCP URL / stdio argv, but **not that a plugin will write skill files** into the user's skills folder | A plugin now installs files the user was never shown. This is the one gap that is a trust regression, not a missing feature. |
| 2 | `bundled_skills` on a card and `installed_skills` in the install response are **ignored by the view** | A bundle installs correctly and looks exactly like a bare connector; the user cannot tell what they got. |
| 3 | The install route can now answer **409 on a skill-name conflict**; the view renders it as a generic error | "Install failed" instead of "you already have a skill called X". |
| 4 | Uninstall returns `removed_skills`; nothing reports it | Files disappear silently — correct behaviour, invisible. |

All four are frontend-only changes against a backend that already sends the
data. Gap 1 must land before any bundle is published, since it is the
promise the store makes before writing to disk.

---

## 4. What is left to build

### Now — close what the last change opened

- [ ] **Consent dialog lists the bundled skills** (name + first line of the
      description) under a "this will also add" heading. Trust gate.
- [ ] Card badge "includes N skills"; installed state shows which.
- [ ] Surface the 409 conflict message verbatim; surface `removed_skills`
      in the uninstall toast.
- [ ] Make the feed self-contained: embed `skill_md` for standalone skills
      (§2), keep `raw_url` for older clients.

### Next — the registry side of the same format

- [ ] `submission.schema.json`: `skills: [{name, skill_md}]` for
      `kind: "plugin"`.
- [ ] `validate.py`: the four package rules (no `scripts/`, no
      `risk_policy`, caps, at least one component) — mirroring
      `agent_plugins_loader.py` until §5 merges them.
- [ ] `expand.py`: write `plugins/<name>/skills/<skill>/SKILL.md`.
- [ ] `build_index.py`: embed them in the plugin entry.
- [ ] Ownership fix from the login spec: `registry.json` keys publishers by
      **login string**; add the immutable numeric `publisher_id` before
      reopening. A renamed login is a hijack hole today.

### Then — one validator, then a door

- [ ] Extract `submission_rules.py`; registry CI installs the package and
      calls it, so "CI green" and "the app accepts it" cannot diverge.
- [ ] `jarvis plugin init | validate | publish | status` (CLI).
- [ ] Storefront: the site repo, the sign-in endpoints, the submit form
      (parallel effort — spec §8 steps 2–4).
- [ ] Reopen the registry repo (spec §8 step 5) — only after the gate is
      green.
- [ ] In-app Publish view, device flow on the same GitHub App (spec §7);
      `jarvis/marketplace/auth/oauth_device.py` already exists.

### Definition of done for the whole chain

An arbitrary downloader with a GitHub account can, without touching a
terminal: sign in, upload a plugin directory with one skill in it, watch it
auto-merge, see it in the app's store within minutes, install it, be told
exactly what was written and where, use it, and remove it leaving nothing
behind.
