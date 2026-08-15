# Marketplace — where we stand

**Measured:** 2026-08-15 · **Scope:** the whole chain, publisher to
installed plugin · **Reads:** [community-registry.md](community-registry.md)
· [publishing-plan.md](publishing-plan.md) ·
[package-layout.md](package-layout.md) ·
[github-signin-implementation.md](github-signin-implementation.md) ·
[validator-parity.md](validator-parity.md) ·
[wallpapers-lane.md](wallpapers-lane.md)

> **The marketplace UI is BETA.** The Plugins → Community view in the
> desktop app (`jarvis/ui/web/frontend`, package version 0.1.0 while the app
> is 1.3.2) is a working beta surface, not a finished one. Its layout, its
> wire types, and its copy will keep moving while the publishing half is
> built. Nothing outside this repo should treat its shapes as a contract —
> the stable contract is `index.json` (§ feed) and the REST routes, not the
> view.

> **Every line below was re-measured on 2026-08-15**, against the live feed,
> the three remotes (`gh`), the running desktop process, and the working
> tree. The 2026-08-14 revision of this file described an outage and a
> private registry; both statements are now stale and are corrected in §2.

---

## 1. Status at a glance

| Piece | State | Evidence |
|---|---|---|
| **Consume** — index fetch, TTL cache, offline degradation | ✅ shipped | `jarvis/marketplace/community_source.py` |
| **Consume** — browse, filter, consent dialog, one-click install | ✅ shipped (beta UI) | `jarvis/ui/web/frontend/src/views/PluginsCommunity.tsx` |
| **Consume** — installed plugin joins connect flow, relevance gate, worker bridge | ✅ shipped | `jarvis/marketplace/plugin_registry.py` |
| **Format** — Agent Plugins v1.0.0 manifests → `PluginSpec` | ✅ shipped | `jarvis/marketplace/agent_plugins_loader.py` |
| **Format** — a plugin may bundle skills; ownership markers; `$schema`; component rule | ✅ shipped 2026-08-14 | `jarvis/marketplace/bundled_skills.py` |
| **Registry** — CI validation, auto-merge, ownership ledger, Pages index | ✅ live, public | registry tree lists `.github/workflows/{validate,automerge,publish}.yml` |
| **Registry** — ownership keyed on the numeric account id | ✅ live 2026-08-14 | registry `scripts/validate.py:16-20, 404-431`; `scripts/test_ownership.py` |
| **Registry** — knows about bundled skills, feed is self-contained | ✅ live (index revision 12) | live `index.json`: `skills[0].skill_md` = 1126 bytes |
| **Feed** — served, and every URL in it resolves | ✅ measured 2026-08-15 | see §2 |
| **Publish** — GitHub sign-in + validate + submit + watch, in-app | 🔨 **in flight (this session)** | routes exist: `jarvis/ui/web/marketplace_publish_routes.py:62-210` |
| **Publish** — upload endpoint at the storefront | ✅ deployed, live | `POST personaljarvis.ai/api/marketplace/submit` → **401** without a session |
| **Storefront** — repo, sign-in, submit form, gallery | ✅ built and deployed, ❌ **curtained off** | see §2 |
| **Wallpapers lane** — app half | ✅ committed 2026-08-14 | `jarvis/ui/web/marketplace_routes.py:1310-1420` |
| **Wallpapers lane** — registry + storefront halves | ❌ **written but never pushed** | see §2 |
| **W3** — one validator, three callers | 🟡 half — data published, nobody reads it | see §4 |
| **W4** — `jarvis plugin …` CLI | ❌ untouched (generic CLI reachability exists) | see §4 |
| **Frontend ↔ backend** — bundled skills visible, consent names them | ✅ shipped 2026-08-14 | `PluginsCommunity.tsx`, `marketplace_routes.py` |

---

## 2. What is broken right now (measured, not assumed)

### The 2026-08-14 outage is over, and the registry is public again

The registry repo is **public**, not private. `gh repo view` reports
`"visibility":"PUBLIC"`, and the storefront recorded the same fact on
2026-08-14 08:28 UTC (`fix(marketplace): the registry is public again…`).
Every URL the feed publishes resolves:

| Request (unauthenticated, 2026-08-15) | Result |
|---|---|
| `GET personaljarvis.github.io/marketplace/index.json` | **200**, 3675 bytes |
| `GET …/marketplace/rules.json` | **200** |
| `github.com/…/marketplace/tree/main/plugins/sentry` (the card's "view source") | **200** |
| `github.com/…/marketplace/tree/main/skills/three-bullet-brief` | **200** |
| `raw.githubusercontent.com/…/skills/three-bullet-brief/SKILL.md` | **200** |

Feed contents: **revision 12**, generated `2026-08-14T08:20:57Z`, **1 plugin
+ 1 skill**, `skill_md` embedded (1126 bytes) so an install writes straight
from the feed. **No `wallpapers` lane** — see below.

**This retires the "omit `source_url`" item.** The app renders that link as
an external-link button on four surfaces
(`PluginsCommunity.tsx:1069`, `:1192`, `:1441`, `:1724`), and today every
one of them opens a page that exists. What is left is a rule, not a task:
*the feed may only publish a `source_url` a stranger can open* — if the
registry is ever made private again, the index generator must drop the field
in the same change.

### The real blocker: the storefront is finished and curtained off

The storefront is deployed (Cloudflare Pages, production branch `remake`,
serving `personaljarvis.ai`). Its API is live: `POST /api/marketplace/submit`
answers **401** without a session, `/api/marketplace/stats` and
`/api/marketplace/config` answer **200**. But every human-facing marketplace
page is redirected away by `public/_redirects` on the deployed branch:

```
/marketplace  /  302
/marketplace/ /  302
/marketplace/* /  302
```

Its own comment says it plainly: *"MARKETPLACE OFFLINE — 2026-08-14, on the
maintainer's request … This is a curtain, not a demolition. TO BRING IT
BACK: delete the three /marketplace lines."* Measured: `GET
personaljarvis.ai/marketplace` → **302** to `/`.

So a stranger with a browser has **no door**: no listing page, no detail
page, no sign-in page, no upload form. The only remaining publisher path is
the in-app Publish tab, which is in flight in this session.

### The wallpapers lane exists only in this repo

`wallpapers-lane.md` ends with a four-step activation checklist. Steps 1 and
2 are **not done**, and the work is sitting unpushed in two local clones:

| Repo | Measured state |
|---|---|
| App (this repo) | ✅ committed on `main` (`3bef83e22`, `a97b509e3`) |
| Registry | ❌ remote has **no** `wallpapers/`, no `scripts/test_wallpapers.py`; the local clone is **2 commits ahead** of `origin/main` |
| Storefront | ❌ deployed branch has **no** `submit-wallpaper.ts` (`POST …/api/marketplace/submit-wallpaper` → **405**, the static-asset answer, against **401** for the deployed `submit.ts`); the local clone is **4 commits ahead** of `origin/remake` |
| Inbox (private review queue) | ❌ contains `README.md` only; step 3 (grant the GitHub App access to it) unverified |

Consequence: the app ships an import route and a storefront door for a lane
that produces nothing. Nothing crashes — the feed simply carries no
`wallpapers[]` — but the feature is not live, and
[qa/upload-and-cloudflare-audit.md](../../qa/upload-and-cloudflare-audit.md)
(2026-08-15) describes those endpoints as live in production because it read
the local clones rather than the deployed branch. **Treat that audit's
wallpaper findings as a description of the code, not of production.**

---

## 3. Frontend ↔ backend: what is wired and what is not

`PluginsCommunity.tsx` calls `GET /api/marketplace/community`,
`POST …/refresh`, `POST …/plugins/{name}/install`,
`DELETE …/plugins/{name}`, `POST …/skills/{name}/install`, and
`DELETE /api/skills/{name}`. Cards, filters, category counts, update badges,
the consent dialogs and the bundled-skill list all read fields the backend
actually sends.

All of these exist in the **running** process, not only in the tree — the
live `GET /api/openapi.json` lists
`/api/marketplace/community/skills/{skill_name}/install`,
`/api/marketplace/community/wallpapers/{wallpaper_name}/install` and the six
`/api/marketplace/publish/*` routes.

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

### Done, and re-verified 2026-08-15

- [x] Consent dialog names every skill a plugin will write, before it writes.
- [x] Detail sheet lists the bundled skills; the store reports what was
      added or removed after the fact.
- [x] Install/remove surface the server's message verbatim (409 conflicts
      included).
- [x] Feed made self-contained: `skill_md` embedded for standalone skills
      and for bundled ones, `raw_url` kept for older clients.
- [x] Standalone skill install at
      `POST /api/marketplace/community/skills/{name}/install` — the name is
      the whole request, so the server reads the same index entry the card
      came from instead of trusting a caller-supplied URL.
- [x] Registry: `skills[]` in the schema, the shared SKILL.md rules in
      `validate.py`, `expand.py` writing the standard directory layout,
      `build_index.py` embedding everything, and
      `scripts/test_bundled_skills.py` pinning the four rejection cases.
- [x] **Ownership keyed on the immutable numeric account id.** The registry
      validator makes the id the only ownership key once an entry records
      one, and refuses an update that drops the field
      (`validate.py:404-431`); the auto-merge gate binds it to the PR author
      on the fork path and requires it on the trusted-bot path
      (`automerge_gate.py:164-201`); `scripts/test_ownership.py` pins both.
      Every entry in the published ledger carries an id, so no login-keyed
      entry is left. The app sends no `publisher_id` **by design** — the
      upload endpoint copies `publisher`/`publisher_id` from the verified
      GitHub session and ignores the body (`submit.ts:88-93`), so a client
      cannot claim someone else's name.
- [x] **The maintainer restart is done.** The desktop process running today
      serves the skill-install route (live `/api/openapi.json`), so the item
      this file was waiting on is closed. Nothing further is required.
- [x] Auto-merge proven end to end: four submission PRs opened by the
      endpoint's bot were validated and merged without human action
      (registry PRs #1–#4, 2026-08-14), including the Turnstile bot check.

### In flight (this session) — outcome not knowable from here

- [ ] The GitHub device-flow sign-in path for the in-app publisher.
- [ ] Bundled-skill validation in the app's upload pre-check
      (`jarvis/marketplace/publish.py`).
- [ ] The in-app **Publish** tab frontend.

Re-measure these before trusting any line about them.

### Next — the door, in order

- [ ] **Take the curtain down** (delete the three `/marketplace` lines from
      the storefront's `public/_redirects`) — but only after the two items
      below, because today the curtain is the only thing standing between a
      stranger and an unfinished lane.
- [ ] **Push the two clones** (registry: 2 commits; storefront: 4 commits)
      or explicitly decide the wallpapers lane stays parked. Unpushed work
      that other repos already reference is the most expensive state to
      leave a system in — the app advertises a door that answers 405.
- [ ] **Grant the GitHub App access to the private inbox repo**, or wallpaper
      uploads answer 502 the moment the lane goes live
      (`wallpapers-lane.md` step 3).
- [ ] **Prove the publisher path from a non-maintainer account.** Every
      green measurement so far was made by the account that owns all three
      repos. The fork path in the auto-merge gate has never been exercised
      by a stranger.

### Then — one validator, and the CLI

- [ ] **W3 is half done.** `rules.json` is generated from the registry
      validator, published next to the feed (**200** today), and CI fails if
      it drifts. But **nothing reads it**: the app still carries a
      hand-copied mirror (`jarvis/marketplace/publish.py:56-75`, comment:
      *"keep the two lists identical"*) and the storefront validator only
      names the file in a comment (`functions/_lib/validate.ts:45`). The
      17 divergences in [validator-parity.md](validator-parity.md) stand
      except the secret-pattern gap, which was closed by hand on 2026-08-14
      — which is exactly the recurrence W3 was meant to prevent. `submission_
      rules.py` does not exist in any repo.
- [ ] **W4 — `jarvis plugin init | validate | publish | status`: untouched.**
      No `plugin` group exists in `jarvis/cli_ctl/commands/`, and `plugin` is
      not in `jarvis/cli_ctl/reserved.py`. The CLI-first contract is
      nevertheless satisfied generically: the OpenAPI-derived surface exposes
      every publish route today —
      `jarvis api marketplace submit | validate | status | signin-start |
      signin-poll | publish-identity | sign-out`, plus
      `community-skill-install` and `community-wallpaper-install` (verified
      by running `jarvis api marketplace --help` against the live process).
      What W4 adds beyond that is `init` — scaffolding a package directory —
      which has no route and therefore no generic equivalent, plus
      human-shaped arguments and `--dry-run`.

### Definition of done — walked step by step, 2026-08-15

*An arbitrary downloader with a GitHub account can, without touching a
terminal: sign in, upload a plugin directory with one skill in it, watch it
auto-merge, see it in the app's store within minutes, install it, be told
exactly what was written and where, use it, and remove it leaving nothing
behind.*

| Step | State | Why |
|---|---|---|
| Sign in | 🟡 **partly** | On the web: **blocked** — the sign-in page is behind the curtain. In the app: the routes exist; the flow is in flight this session. |
| Upload a plugin directory with one skill | 🟡 **partly** | The endpoint is live (401 without a session) and accepts bundled skills. The web form is behind the curtain; the in-app form is in flight. |
| Watch it auto-merge | ✅ **reachable** | Proven four times on 2026-08-14 via the trusted-bot path. Never proven from a stranger's fork. |
| See it in the app's store within minutes | ✅ **reachable** | The publish workflow rebuilds the index on merge; the live feed is at revision 12 and the app reads it. |
| Install it | ✅ **reachable** | Plugin and standalone skill both install from the embedded feed; routes confirmed in the running process. |
| Be told exactly what was written and where | ✅ **reachable** | Consent dialog names each skill before the write; the reply reports `installed_skills`. |
| Use it | ✅ **reachable** | Installed plugin joins the connect flow and the worker bridge. |
| Remove it leaving nothing behind | ✅ **shipped** (not re-measured today) | Ownership markers mean uninstall takes only what the plugin owns. Worth one manual pass before the door opens. |

**Nothing in this walk is blocked by the registry.** The chain's one closed
gate is the storefront curtain, and behind it a lane that was never pushed.

---

## 5. Questions only the maintainer can answer

1. **Should the curtain come down at all yet** — and is the wallpapers lane
   meant to ship with it, or stay parked while plugins and skills go public?
2. **Are the two unpushed clones intentional** (waiting on a decision) or
   forgotten? They contain finished, reviewed work.
3. **Has the GitHub App been granted access to the private inbox repo?**
   That grant is a browser action nobody else can verify from here.
4. **Who tests the stranger path?** A second GitHub account is needed to
   exercise the fork path of the auto-merge gate, and it cannot be the
   account that owns the repos.
