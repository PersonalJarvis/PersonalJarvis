# Marketplace Upload & Cloudflare Bot/DDoS Audit

**Date:** 2026-08-15 · **Scope:** upload/submission handling across the three marketplace
repos + Cloudflare protection status for `personaljarvis.ai` · **Type:** investigation
only — no fixes applied.

---

## 1. Executive summary

The premise "users can currently upload unlimited content" is **not accurate** —
per-account daily quotas, size caps, type checks, a bot challenge, and an
account-age gate all exist and are live in production. What IS true:

1. **The quota counters have real bypass holes** — both are non-atomic
   (parallel requests race past the limit) and both read only the first page
   of GitHub history, so under load they undercount and stop enforcing.
2. **There is no global cap, no deduplication, and no stale-file cleanup** —
   the private wallpaper inbox and the registry's submission branches grow
   forever, and git history keeps every rejected 8 MB image even after deletion.
3. **Cloudflare protection is baseline-only and partly unverifiable** — the
   free-plan always-on DDoS shield and Turnstile are active, but no rate-limiting
   rule protects the upload endpoints, `always_use_https` is **off**, and the
   read-only API token cannot see Bot Fight Mode or WAF rules (403), so their
   status is unknown.

Current storage reality: the registry repo is **1.1 MB** total (working tree)
with a 0.7 MB `.git`. Nothing is burning money today — this audit is about the
growth curve, not the present bill.

---

## 2. Where upload handling lives

Uploads flow through four layers in three repos. There is no blob storage
anywhere — **git repos are the database and the CDN** (deliberate: "the
reviewed bytes are the shipped bytes", `docs/marketplace/publishing-plan.md` §2).

| Layer | Repo | Key files |
|---|---|---|
| **Upload endpoints** (Cloudflare Pages Functions) | `Jarvis Web UI` (storefront, branch `remake`, deployed 2026-08-14) | `functions/api/marketplace/submit.ts` (plugins/skills → PR), `submit-wallpaper.ts` (images → private inbox), `_lib/validate.ts`, `_lib/wallpapers.ts` |
| **CI validation + auto-merge gate** | `jarvis-marketplace` (registry) | `scripts/validate.py`, `scripts/automerge_gate.py` |
| **Client-side pre-validation** (mirror, for instant form feedback) | `Personal Jarvis` (app) | `jarvis/marketplace/publish.py` |
| **Install-time re-validation** (defence against a poisoned index) | `Personal Jarvis` (app) | `jarvis/marketplace/agent_plugins_loader.py` |

Two distinct upload lanes:

- **Plugins/skills** — `POST /api/marketplace/submit` opens a PR on the
  registry; `automerge_gate.py` merges it automatically when validation passes
  and the publisher identity is proven (fork path or trusted-bot path).
- **Wallpapers** — `POST /api/marketplace/submit-wallpaper` commits image +
  metadata into the **private** `PersonalJarvis/marketplace-inbox` repo; a
  maintainer approves/rejects on the review page (`decide.ts`). The gate
  structurally refuses to auto-merge `kind=wallpaper`
  (`automerge_gate.py:166-181`, pinned by `test_wallpapers.py`).

---

## 3. What validation exists today (with evidence)

### 3.1 Size and type limits — present and layered

| Limit | Value | Enforced at |
|---|---|---|
| Whole submission JSON | 128 KB | `validate.py:36` (`MAX_FILE_BYTES`), mirrored `publish.py:55` |
| One SKILL.md | 64 KB | `validate.py:37`, `agent_plugins_loader.py:89` (`MAX_SKILL_MD_BYTES`) |
| Bundled skills per plugin | 10 | `validate.py:38`, `agent_plugins_loader.py:88` (`MAX_BUNDLED_SKILLS`) |
| Description | 500 chars | `validate.py:39` |
| Wallpaper image | 8 MB | `validate.py:44` (`MAX_WALLPAPER_BYTES`), re-checked in `submit-wallpaper.ts:88` before any byte is stored |
| Wallpaper title | 80 chars | `validate.py:40` |
| Image type | WebP/JPEG/PNG by **magic bytes**, declared MIME ignored | `_lib/wallpapers.ts:72-90` (`imageKind`), `validate.py:319-323` (`WALLPAPER_MAGIC`) |

Content rules on top: secret-pattern scan of the raw submission text
(`validate.py:91-101`), https-only URLs, stdio launcher allowlist
(`npx`/`uvx`/`docker`) with pinned versions, no `risk_policy` in community
skills, no `native_tool`, reserved-name lists, license allowlist for
wallpapers (redistribution licenses only). The client re-enforces all of it at
install time (`agent_plugins_loader.py`), so a poisoned index cannot bypass it.

### 3.2 Per-user quotas — present, but leaky

- **Plugins/skills: 5/day/account** (`submit.ts:25`, `DEFAULT_MAX_PER_DAY`;
  `MAX_SUBMISSIONS_PER_DAY` env override — not set in prod, so the default
  applies). Counted by scanning the registry's **50 newest PRs** for a
  `publisher-id: <uid>` marker in the PR body (`submit.ts:117-131`).
- **Wallpapers: 3/day/account** (`submit-wallpaper.ts:34`). Counted by scanning
  the inbox repo's **100 newest commits** of the last 24 h for a `(uid:…)`
  marker (`submit-wallpaper.ts:108-123`).

Weaknesses (all verified in code, none exploited so far):

1. **TOCTOU race.** Both counters read history *before* writing. N parallel
   requests all see the same count and all pass; there is no atomic
   reserve-then-write. The D1 database already bound to the project
   (`MARKETPLACE_SIGNALS`) is unused for quotas.
2. **First-page-only counting.** `per_page=50` PRs / `per_page=100` commits
   with no pagination. Once total daily volume (all users combined) exceeds
   one page, older same-day submissions fall off the page and the quota
   silently under-counts — i.e. the limit stops working exactly when the
   marketplace is under submission pressure.
3. **Per-account only.** The 7-day GitHub account-age gate
   (`callback.ts:21,50-56`, `DEFAULT_MIN_ACCOUNT_AGE_DAYS = 7`, env not
   overridden) raises the cost of sock-puppet accounts, but there is **no
   global daily ceiling**: 100 aged accounts can legally push
   100 × 3 × 8 MB = 2.4 GB/day into the inbox repo.

### 3.3 Deduplication — absent

No content hashing anywhere. The same 8 MB image can be submitted repeatedly
under different titles (slug collision handling even auto-suffixes `-2`…`-9`,
`submit-wallpaper.ts:144-148`); identical `skill_md` can be published under
any free name. Name uniqueness is the only dedup that exists.

### 3.4 Stale-file cleanup — absent

- **Registry submission branches.** Every `/submit` creates
  `submit/<name>-<version>-<hex>` (`submit.ts:175-181`). Nothing deletes the
  branch after merge, and PRs the gate leaves open ("not eligible") stay open
  with their branches forever.
- **Inbox entries.** A pending wallpaper nobody decides on sits in
  `pending/<name>/` indefinitely. The review list caps at 50 *displayed*
  entries — the repo itself has no cap and no TTL.
- **Git history.** `decide.ts` reject deletes the files, but git history keeps
  the blobs: **every rejected 8 MB troll image permanently grows the private
  inbox repo.** GitHub's soft guidance is <5 GB/repo; at the quota's legal
  maximum a handful of hostile accounts reaches that within weeks. This is
  the single real "unbounded storage" sink in the system.
- **D1 signals DB** (votes/downloads) is bounded by design — votes are keyed
  `(entry, voter)`. The unauthenticated download counter can be inflated by a
  loop, but that is a vanity-metric problem, not storage.

---

## 4. Cloudflare bot/DDoS status (checked via `wrangler` 4.110.0 + API, token from `CLOUDFLARE_API_TOKEN`)

Account `5dd0e4ab…` · zone `personaljarvis.ai` (`9b667823…`) · **Free plan** ·
Pages project `personal-jarvis-webui` serving `personaljarvis.ai` +
`www.personaljarvis.ai`, production branch `remake`, last deploy success
2026-08-14.

### Active protections (verified)

| Protection | Status |
|---|---|
| L3/L4 + HTTP DDoS mitigation | Always-on for every Cloudflare zone, including Free — cannot be disabled; this is the baseline DDoS story |
| Security level | `medium` |
| Browser Integrity Check | `on` |
| Challenge TTL | 1800 s |
| **Turnstile** on both upload endpoints | **Active in production**: widget `personaljarvis-marketplace-submit` (managed mode, domain `personaljarvis.ai`) exists, and `TURNSTILE_SECRET_KEY` + `TURNSTILE_SITE_KEY` are set on the Pages project — so the `if (env.TURNSTILE_SECRET_KEY)` gates in `submit.ts:74` / `submit-wallpaper.ts:53` are live, not dormant |
| GitHub sign-in + 7-day account age | Required for submit, vote, wallpaper upload |

### Gaps and unverifiable items

| Item | Finding |
|---|---|
| `always_use_https` | **OFF** — plain-http requests to the zone are not force-redirected. Cheap to fix, no downside. |
| Rate-limiting rules | **None visible / not verifiable** (API 403). Nothing rate-limits `/api/marketplace/submit*` at the edge — Turnstile stops naive bots but a token-solving farm or a scripted session hits the origin functions (and the GitHub API quota behind them) unthrottled. The Free plan includes rate-limiting rules; none appears to be used. |
| Bot Fight Mode | **Unknown** — `GET /zones/…/bot_management` returns 403 with this token. Needs a dashboard check; it is free and one toggle. |
| WAF custom rules / managed rulesets | **Unknown** — `GET /zones/…/rulesets` returns 403. Free plan includes the Cloudflare Free Managed Ruleset + custom rules; cannot confirm any are configured. |
| API token scope | The stored token is the read-only observability token (matches `jarvis/marketplace/usage_cards/cloudflare.md`). It cannot audit Bot Management, WAF, or rate limits — future security audits need a token with `Zone → Firewall Services:Read` and `Zone → Bot Management:Read`. |

---

## 5. Recommended measures (proposal only — nothing applied)

Ordered by impact per effort:

1. **Edge rate limit on the upload endpoints** (smallest fix, biggest DDoS
   win). One Cloudflare rate-limiting rule: e.g. ≥6 requests/min per IP to
   `personaljarvis.ai/api/marketplace/submit*` → block 10 min. Free plan
   supports this; it protects the Pages Functions *and* the GitHub App's API
   quota, which is the real scarce resource behind every upload.
2. **Make quotas atomic and page-proof** by moving the counters into the D1
   database that is already bound (`MARKETPLACE_SIGNALS`): one
   `submissions(uid, day, count)` table, incremented in the same transaction
   pattern `vote.ts` already uses. Closes both the TOCTOU race and the
   first-page undercount in one change, no new infrastructure.
3. **Global daily ceiling** as a circuit breaker: e.g. 25 plugin/skill
   submissions and 15 wallpaper uploads per day across *all* accounts, env-
   configurable. A legitimate community never hits it; a sock-puppet farm
   hits a wall instead of a private repo.
4. **Content-hash dedup**: store `sha256` of the image bytes in
   `submission.json`; refuse an upload whose hash already exists in the inbox
   or the published registry (and tell the uploader which entry has it).
   Same check for `skill_md`. One field, one Set-lookup.
5. **Stale-file cleanup**, one scheduled registry workflow (weekly):
   delete branches of merged/closed PRs; close "not eligible" PRs older than
   30 days; auto-reject inbox entries older than 30 days (mail-back via PR
   comment is not possible in the inbox — a note in the review UI suffices).
6. **Inbox history hygiene**: because rejected images live on in git history,
   either (a) periodically re-create the inbox repo once it exceeds a
   threshold (it holds only transient state, so this is safe), or (b) accept
   the growth and monitor repo size in the same workflow as (5). Option (a)
   is honest and free; blob storage (R2) would be the structural fix but
   contradicts the deliberate "repo is the state" doctrine — not recommended
   until numbers force it.
7. **Cloudflare toggles**: turn `always_use_https` **on**; verify/enable
   **Bot Fight Mode** in the dashboard; confirm the Free Managed WAF ruleset
   is deployed. All three are free and take minutes.
8. **Token for future audits**: mint a second, audit-scoped API token
   (Firewall Services:Read, Bot Management:Read) so this check can be
   automated instead of ending at 403s.

Not recommended: lowering the 8 MB wallpaper cap (the build re-encodes
anyway and 4K WebP fits comfortably), or automated takedown flows
(`publishing-plan.md` D4 already argues why manual revert is safer).
