# Sign in with GitHub — design analysis for marketplace uploads

**Status:** ANALYSIS — no code written yet ·
**Written:** 2026-08-14 ·
**Depends on:** [community-registry.md](community-registry.md) (shipped registry),
[public-marketplace-analysis.md](public-marketplace-analysis.md) (D1–D9 decisions)

The goal under analysis: **a "Sign in with GitHub" gate on the Jarvis
marketplace — GitHub is the only identity provider, and only a signed-in
account can upload a skill or a plugin.** No email/password, no second
account system.

Everything in §1 was measured against the live repos and the live site on
2026-08-14, not inferred from the docs.

---

## 1. Measured starting position

| Piece | State on 2026-08-14 |
|---|---|
| Registry repo `PersonalJarvis/marketplace` | Live. `submissions/`, `plugins/`, `skills/`, `registry.json`, `schemas/submission.schema.json`, four CI scripts (`validate.py`, `automerge_gate.py`, `expand.py`, `build_index.py`), three workflows (`validate`, `automerge`, `publish`). |
| Compiled feed | Live — `https://personaljarvis.github.io/marketplace/index.json` answers 200; Pages serves `main` at `/`. |
| Ownership ledger | `registry.json` holds two entries (`sentry`, `three-bullet-brief`), each with `publisher` = a GitHub **login string** and a SemVer version. |
| Auto-merge gate | Merges only when the PR touches exactly one `submissions/<name>.json`, validation passes, **`publisher` equals the PR author's login**, and (on updates) the owner is unchanged and the version strictly increases. |
| Submission format | One JSON file. A skill embeds its complete `SKILL.md` as the `skill_md` string (≤ 64 KB); the whole file is capped at 128 KB; descriptions at 500 chars. |
| Publish path used so far | **Zero pull requests have ever been opened** against the registry — both live entries were committed directly. The contributor path is therefore untested in practice. |
| Storefront | Private Astro 6 repo, **static build, no adapter, no server routes**. `src/pages/` has the landing page, docs, `de/`, `es/`, legal pages — **no `/marketplace` and no `/marketplace/submit`**. |
| Live site | `https://personaljarvis.ai` answers 200 **through Cloudflare**. `/marketplace` and `/marketplace/submit` both answer **404** — the paths the registry README already advertises do not exist yet. |
| App-side | Browse + install + consent dialog are shipped (`marketplace_routes.py`, `PluginsCommunity.tsx`). There is **no publish path in the app at all**. |
| GitHub auth substrate | `jarvis/marketplace/auth/oauth_device.py` is a complete, working **OAuth Device Flow** handler (start, poll, refresh, all four GitHub error codes). It is currently unused for GitHub: the shipped `github` plugin uses `pat_paste`. **No OAuth App or GitHub App is registered for the project.** |

Two consequences fall straight out of this table:

* **The identity gate already exists in the registry** — it is the
  `publisher == PR author` rule. A login button does not introduce
  GitHub identity; it replaces *"prove it by opening a PR yourself"* with
  *"prove it by clicking a button"*.
* **The upload surface is what is missing**, not the trust model. Both the
  storefront submit page and any in-app publish button are unwritten.

---

## 2. What the login must buy, and what it must not pretend to buy

**Buys:**

1. **Upload without git.** Today publishing requires forking a repo, writing
   a JSON file by hand, and opening a PR. That excludes most skill authors.
2. **Un-forgeable publisher identity.** The `publisher` field stops being
   self-declared text and becomes a value the server derived from a GitHub
   token.
3. **A rate-limiting subject.** "Five submissions per account per day" is
   only expressible once submissions carry a verified account.
4. **A "my submissions" view** — without any database, because
   `registry.json` already *is* the ownership table.

**Does not buy:**

* **Trust in the content.** A GitHub account costs nothing and takes
  seconds. The login proves *who* published, never *whether it is safe*.
  The "Community · not reviewed" badge, the consent dialog, and
  `validate.py` stay exactly as load-bearing as they are today.
* **Spam immunity.** Throwaway accounts exist. Account age checks, per-account
  quotas, and a Cloudflare Turnstile challenge on the submit endpoint are the
  actual anti-spam layer (§7).

---

## 3. The hard constraint: a browser cannot finish a GitHub login

GitHub's token endpoint (`https://github.com/login/oauth/access_token`)
serves **no CORS headers and no OPTIONS**, deliberately and unchanged for
years. A purely static page therefore cannot exchange the `code` for a
token — every browser-based GitHub login on earth runs the exchange on a
server. Two documented facts shape every option below:

* **Web flow requires the client secret** at the token exchange. A secret
  cannot live in a static bundle, so the exchange must be server-side.
* **Device flow requires no client secret** (only `client_id`, `device_code`,
  `grant_type`) — which is precisely why it is the right flow for the
  desktop app, and why `oauth_device.py` was written that way. It must be
  explicitly enabled in the app's settings.

So: the storefront login needs exactly one server endpoint. The desktop app
needs none. That asymmetry drives the recommendation.

---

## 4. Candidate designs

### L0 — No login: pre-filled PR link (today's documented plan)

The submit form builds the JSON client-side and hands the user a GitHub
"create new file" URL with the content pre-filled. GitHub itself demands the
login, auto-forks, and opens the PR.

* **Pros:** zero infrastructure, zero secrets, zero new attack surface; the
  existing gate works untouched; degrades to the current manual path.
* **Cons:** the user still lands in GitHub's PR UI (three unfamiliar screens);
  the site never knows who is signed in, so no quota, no "my submissions",
  no per-account abuse handling; URL length limits make a 64 KB `skill_md`
  fragile; **fails the stated requirement** ("only signed-in users can
  upload") in spirit, since the site itself has no notion of signed-in.

### L1 — OAuth App + user's own token opens the PR

Classic web flow with scope `public_repo`. A server endpoint exchanges the
code; then the *user's* token forks the registry, commits the file, and opens
the PR as the user.

* **Pros:** the existing auto-merge gate needs **no change at all** (the PR
  author really is the publisher); provenance is visible in git history.
* **Cons:** `public_repo` grants **write access to every public repo the user
  owns** — the coarsest scope GitHub offers, and a hard sell to exactly the
  developer audience this targets; leaves a fork in every publisher's account;
  the server must hold a live user token for the duration of the write.

### L2 — GitHub App + bot opens the PR *(recommended)*

A GitHub App registered under the `PersonalJarvis` org, installed **only on
the registry repo**. Login uses the App's web flow purely to answer *"who are
you"* — the consent screen asks for **no repository access on the user's
side**. The server then writes `submissions/<name>.json` to a branch **in the
registry repo** with its own installation token and opens the PR as the App.

* **Pros:** the user grants nothing but identity; no fork litter; the server
  never needs to retain a user token past the identity check; the App's
  installation token is scoped to one repo with two permissions; the same App
  serves the desktop app via device flow, so there is one identity for both
  surfaces.
* **Cons:** the auto-merge gate needs a second, equally strict trust rule
  (§6); every PR shows the same bot as author (the publisher lives in the
  file and in the PR body).

### L3 — Hosted registry with its own accounts and database

Rejected in the earlier analysis (Model D) and still rejected: it trades the
project's "no maintainer infrastructure in the critical path" doctrine for
UX that L2 already delivers.

### Trade-off summary

| | L0 pre-filled PR | L1 OAuth App + fork | L2 GitHub App + bot | L3 hosted service |
|---|---|---|---|---|
| Meets "only signed-in can upload" | ✗ (GitHub gates, site doesn't) | ✓ | ✓ | ✓ |
| What the user grants | nothing | write to **all** public repos | identity only | account + password |
| New infrastructure | none | 1 endpoint + secret | 1 endpoint + secret + key | server + DB + ops |
| Change to the merge gate | none | none | one added rule | replaced |
| Per-account quotas | impossible | possible | possible | possible |
| Failure blast radius | — | publishing only | publishing only | browse + install too |

---

## 5. Recommendation

**L2 — one GitHub App, login for identity only, the App commits the
submission and opens the pull request.**

Why over the runner-up (L1): both give a real login and identical
publishing UX, but L1 makes every skill author hand a marketing website
write access to *all of their public repositories* just to upload a
Markdown file — for an audience of developers that is the difference
between "sure" and "no thanks". L2's consent screen asks for nothing on the
user's account, and the cost is a single extra branch in the gate.

The endpoint belongs on **Cloudflare** (Pages Functions or a Worker on
`personaljarvis.ai/api/*`): the live site already serves through Cloudflare,
so this adds no new vendor, no new bill, and no new domain — and the Astro
site can stay a static build with the function beside it.

Doctrine check (contract §3, "no maintainer infrastructure in the critical
path"): if that endpoint is down, **browsing, installing, the feed, and the
manual PR route all keep working** — only the convenience upload pauses. That
is an acceptable, honestly-degrading dependency, and it must be documented as
such on the submit page.

---

## 6. What we need from GitHub — the concrete checklist

One GitHub App, created in the `PersonalJarvis` organisation. Cost: **€0**.

| Setting | Value | Why |
|---|---|---|
| App name | `Personal Jarvis Marketplace` | Shown on the consent screen. |
| Homepage URL | `https://personaljarvis.ai` | — |
| Callback URL | `https://personaljarvis.ai/api/auth/github/callback` | Exact match required; add a `localhost` second callback for development. |
| Request user authorization (OAuth) during installation | **off** | Users never install the App; they only log in. |
| Enable Device Flow | **on** | Needed for the desktop-app publish path (§9). |
| Webhook | **off** | Nothing listens. |
| Where can this App be installed | **Only on this account** | It is installed on exactly one repo. |
| Repository permissions | `Contents: Read & write`, `Pull requests: Read & write` | Enough to push a branch and open the PR. Nothing else. |
| Account permissions | **none** | The public profile (`GET /user`) needs no permission — this is what keeps the consent screen empty. |
| Installed on | `PersonalJarvis/marketplace` only | Blast radius = one repo. |

Credentials it produces, all of which live **only** in the Cloudflare
project's secret store — never in a repo, never in `jarvis.toml`:

* `GITHUB_APP_ID`, `GITHUB_APP_CLIENT_ID` (not secret, but keep them together)
* `GITHUB_APP_CLIENT_SECRET` — the web-flow exchange
* `GITHUB_APP_PRIVATE_KEY` (PEM) — mints installation tokens
* `SESSION_SIGNING_KEY` — signs the login cookie

Rate limits are a non-issue at this scale: 5.000 requests/hour both for
user-to-server and installation tokens.

---

## 7. Security analysis

| Risk | Why it matters here | Mitigation |
|---|---|---|
| **Login renaming / publisher hijack** | `registry.json` stores the publisher as a **login string**. GitHub logins can be renamed, and the freed name can be claimed by anyone — who would then inherit ownership of every skill under it. This is the classic repo-jacking shape, and it is live today. | Store the **numeric account id** alongside the login (`publisher_id`) and make the ownership check compare ids, with the login kept for display only. Worth doing regardless of the login work. |
| Confused deputy (token from a foreign app) | The desktop app sends a token to our endpoint; a token minted by *someone else's* OAuth app would otherwise be accepted as proof of identity. | Verify every incoming token with `POST /applications/{client_id}/token` under the App's own basic auth — it answers 200 only for tokens belonging to *our* client id. |
| Forged `publisher` in a bot PR | Bot PRs are trusted by design, so the branch must be unforgeable. | Only the App installation can push to the registry repo; the gate accepts the trusted path only when `head.repo.full_name == PersonalJarvis/marketplace` **and** the PR author is the App's bot login. Fork PRs keep the old `publisher == author` rule. |
| CSRF / code interception on login | Standard web-flow exposure. | `state` nonce bound to a short-lived signed cookie; strict `redirect_uri` matching; PKCE where GitHub supports it. |
| Token leakage into the browser | A leaked user token is a live GitHub credential. | The user token never leaves the server and is discarded right after the identity check — nothing to leak. The browser gets only an httpOnly, `SameSite=Lax`, signed session cookie carrying login + id. |
| Spam submissions | A login is free; a bot farm can hold many. | Cloudflare Turnstile on the submit endpoint, a per-account daily quota, and a minimum account age; every submission still runs the full `validate.py`. |
| Malicious content | Unchanged by the login. | `validate.py` in CI, the same rules re-enforced client-side at install (`agent_plugins_loader.py`), the unreviewed badge, and the consent dialog. |
| GDPR / data held | An identity provider tempts you into a user table. | Store **nothing**. The session cookie is stateless; ownership lives in `registry.json`, which is public data (login + numeric id) already. |

**One CI trap worth naming:** pull requests opened with a GitHub App
installation token *do* trigger workflows (unlike ones opened with Actions'
own `GITHUB_TOKEN`, which deliberately does not). The auto-merge gate will
therefore fire normally on bot PRs — but this must be verified on the first
real run, because it is the single point where the whole flow silently
stalls if it is wrong.

---

## 8. What actually changes, per repo

**`PersonalJarvis/marketplace`** (small, surgical)

1. `automerge_gate.py`: add the trusted-branch rule described above, keeping
   the fork rule untouched.
2. `validate.py` + `expand.py` + `schemas/submission.schema.json`: add the
   optional `publisher_id`; make ownership compare ids when present.
3. `README.md`: describe the two publishing routes honestly.

**Storefront repo** (the bulk of the work — all of it new)

4. `/marketplace` — browse, client-side fetch of the compiled `index.json`.
5. `/marketplace/submit` — the gated upload form: drag a `SKILL.md` in (or
   paste a `plugin.json`), validate the same rules the CI enforces *before*
   submitting, show a preview of the exact file that will be committed.
6. `/api/auth/github/{login,callback,logout}` and `/api/marketplace/submit`
   as Cloudflare functions.
7. A deliberate switch from a purely static build to static + functions.

**`PersonalJarvis/PersonalJarvis`** (optional, later — see §9)

8. A "Publish to marketplace" action in the Skills view.

---

## 9. The in-app publish path (why it is nearly free)

The desktop app should not use the website's login. It has a better one
already written: `oauth_device.py`. With device flow enabled on the same
GitHub App, publishing from inside Jarvis is: show the user code → user
approves in a browser → the app holds a user token in the existing keyring
`TokenStore` → the app POSTs the submission plus that token to the same
`/api/marketplace/submit` endpoint, which verifies the token belongs to our
App and takes it from there.

That keeps **one** publishing implementation and one identity, works
headless (device flow is designed for exactly that), and needs no client
secret in a binary that anyone can download — the reason the handler was
written this way in the first place.

---

## 10. Open decisions

| # | Decision | Options (lean in bold) |
|---|---|---|
| D11 | Identity model | **L2 GitHub App, identity-only consent**, L1 OAuth App + fork, L0 no login |
| D12 | Endpoint host | **Cloudflare (same vendor as the live site)**, Vercel, own VPS |
| D13 | Ownership key | **Numeric account id + login for display**, login only (status quo) |
| D14 | Anti-spam bar | **Turnstile + daily quota + minimum account age**, quota only, none |
| D15 | Storefront browse page | Ship with submit, or ship browse first and submit after |
| D16 | In-app publish | **Wave 2, after the web path proves out**, same wave, never |
| D17 | Domain of record | `personaljarvis.ai` is live; the Astro config still says `personaljarvis.dev` — fix before any callback URL is registered |

## 11. Suggested build order

1. **W1 — Registry hardening (no login involved).** `publisher_id`, the
   trusted-branch gate rule, README honesty. Independently valuable, and it
   closes the rename-hijack hole that exists today.
2. **W2 — Login.** GitHub App registered, Cloudflare functions for
   login/callback/logout, session cookie, `/marketplace/submit` behind the
   gate showing only "Sign in with GitHub" when signed out.
3. **W3 — Upload.** Client-side validation mirroring `validate.py`, the file
   preview, the submit endpoint, the bot PR, and the first end-to-end run
   that proves auto-merge fires on a bot PR.
4. **W4 — Browse page** on the storefront (pure static, reads the feed).
5. **W5 — In-app publish** via device flow against the same endpoint.
