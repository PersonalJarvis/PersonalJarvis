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
| **Format** — a plugin may bundle skills; ownership markers; `$schema`; component rule | ✅ shipped 2026-08-14 |
| **Registry** — CI validation, auto-merge, ownership ledger, Pages index | ✅ live, but see §2 |
| **Registry** — knows about bundled skills, feed is self-contained | ✅ written 2026-08-14, **on branch `feat/bundled-skills`, not pushed** |
| **Publish** — any interface a human can use | ❌ not started |
| **Publish** — GitHub sign-in gate + storefront submit | 🔨 in build (parallel effort, spec written) |
| **Storefront** — the site itself | ❌ no repo exists yet |
| **Frontend ↔ backend** — bundled skills visible, consent names them | ✅ shipped 2026-08-14 |

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

**Fix written, not yet live.** The feed now embeds every skill's `SKILL.md`
(app side: shipped; registry side: branch `feat/bundled-skills`, awaiting a
push). A linked file inherits the availability of whatever host serves it;
an embedded one cannot go missing while the feed advertising it is
reachable. `raw_url` stays for older clients.

**Until that branch is pushed and Pages redeploys, the outage stands** — the
live feed still carries links only, so the app has nothing embedded to fall
back to.

Still open: the dead `source_url` on every card. The index should omit it
while the repo is private rather than publish a link that 404s.

---

## 3. Frontend ↔ backend: what is wired and what is not

`PluginsCommunity.tsx` calls `GET /api/marketplace/community`,
`POST …/refresh`, `POST …/plugins/{name}/install`,
`DELETE …/plugins/{name}`, `POST …/skills/{name}/install`, and
`DELETE /api/skills/{name}`. Cards, filters, category counts, update badges,
the consent dialogs and the bundled-skill list all read fields the backend
actually sends.

The four gaps this section listed on the morning of 2026-08-14 are closed.
The one worth remembering was a **trust regression, not a missing feature**:
the backend had started writing skill files to disk on install while the
consent dialog still described only the server. A store that writes more
than it announced is worse than one that does less — when the backend gains
the power to touch the user's disk, the dialog is part of that change, not a
follow-up.

Rule of thumb this leaves behind: **any new install side effect must appear
in `capabilityLines` and in the dialog body before the code that performs it
ships.**

---

## 4. What is left to build

### Done 2026-08-14

- [x] Consent dialog names every skill a plugin will write, before it writes.
- [x] Detail sheet lists the bundled skills; the store reports what was
      added or removed after the fact.
- [x] Install/remove surface the server's message verbatim (409 conflicts
      included).
- [x] Feed made self-contained: `skill_md` embedded for standalone skills
      and for bundled ones, `raw_url` kept for older clients.
- [x] Standalone skill install moved to
      `POST /api/marketplace/community/skills/{name}/install` — the name is
      the whole request, so the server reads the same index entry the card
      came from instead of trusting a caller-supplied URL.
- [x] Registry: `skills[]` in the schema, the shared SKILL.md rules in
      `validate.py`, `expand.py` writing the standard directory layout,
      `build_index.py` embedding everything, and
      `scripts/test_bundled_skills.py` pinning the four rejection cases in
      CI. Verified end to end against the app's own loader.

### Blocked on a push

- [ ] Push `feat/bundled-skills` in `PersonalJarvis/marketplace` and let
      Pages redeploy. **The skill-install outage (§2) stays until this
      happens** — the live feed carries links only.

### Next — the registry's remaining hole

- [ ] Ownership: `registry.json` keys publishers by **login string**; the
      numeric `publisher_id` must be the key before the repo reopens. A
      renamed login is a hijack hole today. (Partly in flight — the
      submission schema already accepts `publisher_id`.)
- [ ] Omit `source_url` while the repo is private instead of publishing a
      link that 404s.

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
