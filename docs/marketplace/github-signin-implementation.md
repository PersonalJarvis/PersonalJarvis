# Sign in with GitHub — implementation spec

**Status:** BUILT — storefront half live on personaljarvis.ai, desktop half (§7)
shipped in the app on 2026-08-22 (`components/marketplace/PublishIdentity.tsx`) ·
**Written:** 2026-08-14 ·
**Decision:** [github-login-analysis.md](github-login-analysis.md) (L2 chosen) ·
**Registry:** [community-registry.md](community-registry.md)

This is the build sheet for the storefront's GitHub-only sign-in and the
gated upload behind it. It covers what to register at GitHub, how the
session survives a page reload without a consent banner, the endpoint
contracts, and the verification chain that makes a submission provably
belong to a GitHub account.

Scope note: while the registry repo is private, nobody outside the
organisation can open a submission pull request at all. The login is what
lets it be reopened safely.

---

## 1. What to register at GitHub

One **GitHub App**, owned by the `PersonalJarvis` organisation. Cost: €0.
Settings → Developer settings → GitHub Apps → New GitHub App.

| Field | Value | Note |
|---|---|---|
| GitHub App name | `Personal Jarvis Marketplace` | Appears on the consent screen — this is the "verify against our organisation" surface. |
| Homepage URL | `https://personaljarvis.ai` | |
| Callback URL #1 | `https://personaljarvis.ai/api/auth/github/callback` | Exact string match, no trailing slash. |
| Callback URL #2 | `http://localhost:4321/api/auth/github/callback` | Astro's dev port; GitHub Apps allow up to 10 callbacks, so no second app is needed for development. |
| Request user authorization (OAuth) during installation | **unchecked** | Users log in; they never install anything. |
| Enable Device Flow | **checked** | Required for the desktop-app publish path (§7). |
| Webhook → Active | **unchecked** | Nothing listens. |
| Where can this GitHub App be installed | **Only on this account** | |
| Repository permissions → Contents | **Read and write** | Push the submission branch. |
| Repository permissions → Pull requests | **Read and write** | Open the PR. |
| Account permissions | **all "No access"** | Public profile needs no permission — this is what keeps the consent screen empty. |

After creation:

1. **Generate a client secret** → `GITHUB_APP_CLIENT_SECRET`.
2. **Generate a private key** (PEM download) → `GITHUB_APP_PRIVATE_KEY`.
3. Note the **App ID** and **Client ID**.
4. **Install the App** on `PersonalJarvis/marketplace` only. Note the
   resulting **installation id** (visible in the installation URL) —
   caching it avoids one API call per submission.

Secrets live **only** in the Cloudflare project's environment (encrypted).
Never in a repo, never in `jarvis.toml`, never in the client bundle. The
private key is multi-line: store it base64-encoded to survive env-var
handling.

Environment variables the backend expects:

```
GITHUB_APP_ID
GITHUB_APP_CLIENT_ID
GITHUB_APP_CLIENT_SECRET
GITHUB_APP_PRIVATE_KEY_B64
GITHUB_APP_INSTALLATION_ID
REGISTRY_REPO=PersonalJarvis/marketplace
SESSION_SIGNING_KEY          # 32 random bytes, base64
TURNSTILE_SECRET_KEY         # anti-spam, §6
```

---

## 2. Where the code runs

The storefront is a static Astro build served through Cloudflare. Add
**Cloudflare Pages Functions** beside it — `functions/api/**` — so the
endpoints answer on the *same origin* as the site.

Same origin matters for three reasons: the session cookie is first-party,
there is no CORS preflight, and no third-party-cookie policy in any browser
can touch it. Do not put the endpoints on a separate `api.` subdomain.

**Cloudflare trap:** every response that sets or reads the session cookie
must carry `Cache-Control: private, no-store`. A cached `Set-Cookie` at the
edge would hand one visitor's session to the next.

---

## 3. The session: no banner, survives reload

### 3.1 The decision

Use a **signed, stateless, `httpOnly` session cookie**. No database, no
server-side session table, no consent banner.

Two facts settle this, and they run against the common assumption that
"cookie = banner, therefore use localStorage":

* **A login session is consent-free.** § 25 (2) TDDDG (and ePrivacy Art.
  5(3)) exempts storage that is *strictly necessary for a service the user
  explicitly requested*. A session that exists only because the user clicked
  "Sign in" is the textbook case, next to shopping carts and language
  choice. Analytics and marketing are what need the banner — we ship
  neither on this page. *(Engineering summary, not legal advice.)*
* **localStorage is governed by the same rule.** § 25 covers *any* storing
  of or access to information on the user's device — the statute never says
  "cookie". Moving the token to localStorage changes nothing legally while
  making it readable by any injected script. An `httpOnly` cookie is
  invisible to JavaScript, so an XSS bug cannot steal the session.

So the cookie is both the safer and the cheaper option; nothing is gained by
avoiding it.

### 3.2 Cookie shape

```
Name:      pj_session
Value:     base64url(payload) "." base64url(HMAC-SHA256(payload, SESSION_SIGNING_KEY))
Payload:   {"uid":<numeric GitHub id>,"login":"<login>","av":"<avatar url>",
            "iat":<unix>,"exp":<unix>}
Attributes: HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=2592000   (30 days)
```

* **`uid` is the numeric GitHub account id and is the only identity key.**
  Logins can be renamed and the freed name re-registered by someone else;
  the numeric id never changes. `login` rides along for display only.
* The cookie carries no token and no email — nothing worth stealing, and
  nothing that needs a privacy notice beyond the existing one.
* `SameSite=Lax` is required, not cosmetic: `Strict` would withhold the
  cookie on the top-level redirect *back from github.com*, and the login
  would appear to fail on the last step.

### 3.3 What happens on reload (the actual answer to "stay signed in")

1. Browser re-requests the page and automatically attaches `pj_session`.
2. The submit page calls `GET /api/auth/session`.
3. The function verifies the HMAC and `exp`, and answers
   `{"login": "...", "id": 123, "avatar": "..."}` or `401`.
4. On `401` the page renders the "Sign in with GitHub" button; otherwise it
   renders the upload form with the user's handle.

That is the whole persistence mechanism: no refresh-token dance, because
after the identity check the GitHub token is discarded — there is nothing to
refresh. When the 30 days elapse, the user signs in again, which is one
click and no re-consent (GitHub remembers the authorisation).

**Renewal:** if a valid cookie is older than 7 days, re-issue it with a
fresh `exp` on the same response. Active users are never logged out;
abandoned sessions still die on schedule.

---

## 4. Endpoint contracts

### `GET /api/auth/github/login`
Generates a 32-byte random `state`, stores it in a short-lived cookie
(`pj_oauth_state`, `HttpOnly; Secure; SameSite=Lax; Max-Age=600`), and
redirects (302) to:

```
https://github.com/login/oauth/authorize
  ?client_id=<GITHUB_APP_CLIENT_ID>
  &redirect_uri=<exact callback URL>
  &state=<nonce>
```

Accepts an optional `?next=/marketplace/submit` (validated as a same-site
relative path) and carries it inside the state cookie.

### `GET /api/auth/github/callback`
1. Compare `?state` with the state cookie; mismatch or missing → `400`,
   clear the cookie. (This is the CSRF gate — never skip it.)
2. `POST https://github.com/login/oauth/access_token` with `client_id`,
   `client_secret`, `code`, `redirect_uri`, `Accept: application/json`.
   **This is the step a browser cannot do** (GitHub sends no CORS headers),
   and the reason this function exists at all.
3. `GET https://api.github.com/user` with the token → `id`, `login`,
   `avatar_url`, `created_at`.
4. Reject accounts younger than the configured minimum age (§6).
5. **Discard the GitHub token.** Do not store it, do not send it to the
   browser.
6. Set `pj_session`, clear `pj_oauth_state`, redirect to `next`.

### `GET /api/auth/session`
Returns the identity from the cookie, or `401`. Never touches GitHub.

### `POST /api/auth/logout`
Clears the cookie (`Max-Age=0`), returns `204`.

### `POST /api/marketplace/submit`
The gated upload. Requires a valid `pj_session` — no session, no upload,
which is the whole point of the feature.

```
Request:  { kind: "skill" | "plugin", name, version, title?, description?,
            categories?, skill_md? | plugin_json?, mcp_json?, usage_card?,
            turnstileToken }
Response: 201 { prUrl, submissionPath } | 4xx { error, field? }
```

Server-side sequence:

1. Verify the session cookie; `401` if absent or expired.
2. Verify the Turnstile token; `403` on failure.
3. Enforce the per-account quota (§6); `429` when exceeded.
4. **Re-validate everything** — the same rules `scripts/validate.py`
   enforces in CI: name pattern, reserved names, https-only, launcher
   allowlist with pinned versions, secret scan, 64 KB `skill_md`,
   128 KB file, 500-char description. Client-side checks are UX only; this
   is the boundary that counts.
5. Build the submission object and **set `publisher` and `publisher_id`
   from the session, never from the request body.** A client-supplied
   publisher is ignored outright.
6. Check ownership against `registry.json` on `main`: if the name exists
   and its `publisher_id` differs → `409` with a plain-language message. If
   it matches, require a strictly higher version.
7. Mint an installation token (JWT signed with the App private key →
   `POST /app/installations/{id}/access_tokens`), then, in the registry
   repo: create branch `submit/<name>-<version>` off `main`, PUT
   `submissions/<name>.json`, open the PR with the publisher recorded in
   the body.
8. Return the PR URL. The existing CI takes over from there.

---

## 5. The verification chain

What makes a submission provably belong to a GitHub account, end to end:

1. GitHub authenticated the human — we never see a password.
2. The token was exchanged with **our** client secret, so the identity came
   back from GitHub to us and to nobody else.
3. `publisher_id` is copied from the session on the server; the browser has
   no way to influence it.
4. The branch was pushed with **our App's installation token** — the only
   credential on earth with write access to that private repo besides the
   maintainer.
5. The merge gate therefore trusts the file's publisher **only** when the PR
   comes from a branch inside the registry repo and was opened by our App's
   bot. Fork PRs keep the existing rule (`publisher` == PR author).

Two extra defences worth building in from the start:

* **Token provenance check** (needed for the desktop path in §7): when a
  token arrives from a client rather than from our own callback, verify it
  with `POST /applications/{client_id}/token` under the App's basic auth.
  That endpoint answers 200 only for tokens minted for *our* client id — it
  is what stops someone presenting a token from an unrelated OAuth app.
* **Gate-fires check:** pull requests opened with an App installation token
  *do* trigger Actions workflows, unlike ones opened with Actions' own
  `GITHUB_TOKEN`. Verify on the first live run — a silent non-trigger here
  would stall every submission at "open, never merged".

### Optional later: organisation-verified publishers

Publishing under an *organisation's* name (rather than a personal login)
needs proof of membership, which the GitHub App cannot see: `GET /user/orgs`
with a GitHub App user token lists only organisations where the App is
installed. If org namespaces are ever wanted, the choice is a separate OAuth
App with `read:org`, or a manually curated allowlist of verified publishers.
Not needed for v1, where every publisher is a personal account.

---

## 6. Anti-abuse

A GitHub account is free, so the login is an identity, not a filter. Three
cheap layers do the actual work:

| Layer | Setting | Rationale |
|---|---|---|
| Account age | Reject accounts younger than **7 days** (`created_at` from `GET /user`) | Kills same-day throwaway accounts at near-zero false-positive cost. |
| Quota | Max **5 submissions per account per 24 h** | Counted from open PRs by that publisher; no storage needed. |
| Challenge | **Cloudflare Turnstile** on the submit endpoint | Free, same vendor, no puzzle for real users. |

Everything above is *in addition to* `validate.py`, the unreviewed badge,
and the install-time consent dialog — none of which the login replaces.

---

## 7. Desktop-app publishing (later wave)

The app must not reuse the website's cookie. It uses the **device flow** on
the same GitHub App — the handler already exists at
`jarvis/marketplace/auth/oauth_device.py` (start, poll, refresh, all four
GitHub error codes), which is why enabling device flow in §1 matters.

Flow: app shows the user code → user approves in a browser → token lands in
the existing keyring `TokenStore` → the app POSTs the submission plus that
token to `/api/marketplace/submit` (as `Authorization: Bearer`, no cookie)
→ the endpoint runs the token-provenance check from §5 and proceeds
identically.

One publishing implementation, one identity, and no client secret inside a
binary anyone can download.

---

## 8. Build order and proof

1. **Registry:** add `publisher_id` to the schema, `validate.py`,
   `expand.py`, and the ownership comparison; add the trusted-branch rule to
   `automerge_gate.py`. *Proof: a test submission with a mismatched id is
   rejected.*
2. **App registration** per §1. *Proof: the consent screen lists no
   permissions.*
3. **Auth endpoints** (login, callback, session, logout). *Proof: sign in,
   hard-reload the page, still signed in; delete the cookie, signed out.*
4. **Submit endpoint** + form. *Proof: a skill uploaded from the browser
   appears as a bot PR, auto-merges, and shows up in `index.json`.*
5. **Reopen the registry repo** to the public once 1–4 are green.
6. **Browse page**, then the in-app publish path (§7).
