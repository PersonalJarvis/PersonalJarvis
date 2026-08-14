# The wallpapers lane — community images with a human gate

**Status:** built 2026-08-14, in three repos, awaiting the pushes listed at
the end. **Related:** [community-registry.md](community-registry.md),
[install-standard.md](install-standard.md),
[validator-parity.md](validator-parity.md).

## Why this lane is different

Plugins and skills publish through automated checks with no human review.
That model is fine for manifests — a validator can prove there is no
credential, no plaintext endpoint, no unpinned code. It is structurally
wrong for images: **no pattern list recognizes a hateful or illegal
picture**, and a single Nazi wallpaper published under the project's name
is a legal and reputational disaster in a way a broken manifest never is.

So wallpapers invert the trust model: **pre-moderation**. Nothing becomes
public without a maintainer's explicit approval, and an unapproved upload
is never visible anywhere — not even as an open pull request.

## The pipeline, end to end

```
uploader (storefront, GitHub sign-in + optional Turnstile)
   │  in-browser re-encode: 4K cap, EXIF stripped, WebP/JPEG
   ▼
PRIVATE inbox repo  PersonalJarvis/marketplace-inbox   pending/<name>/…
   │  seen by exactly one person
   ▼
maintainer review   personaljarvis.ai/marketplace/wallpapers/review
   │  Approve                                  │  Reject
   ▼                                           ▼
PUBLIC registry  PersonalJarvis/marketplace    deleted (was never public)
   submissions/<name>.json + wallpapers/<name>/wallpaper.<ext>
   │  validate (CI) → expand (ledger) → build: Pillow RE-ENCODE + thumbnail
   ▼
GitHub Pages feed   …/marketplace/index.json  wallpapers[] + image bytes
   │
   ├── storefront gallery  /marketplace/wallpapers  (live fold-in)
   ├── app import route    POST /api/marketplace/community/wallpapers/{name}/install
   └── CLI                 jarvis marketplace install <name>
```

Three defense layers live in that picture:

1. **The queue is private.** A troll upload reaches one maintainer and
   nobody else; rejection deletes it. There is never a public artifact to
   report, screenshot, or index.
2. **The gate is structural, not procedural.** The registry's auto-merge
   gate refuses `kind=wallpaper` outright (`automerge_gate.py`), pinned by
   `scripts/test_wallpapers.py` — even a valid wallpaper on the trusted bot
   path stays open. Approval-by-click on the review page is the only door.
3. **Bytes are always re-produced.** The browser re-encodes before upload
   (EXIF/GPS gone), the registry build re-encodes again with Pillow before
   anything reaches Pages, and the app re-encodes a third time on import.
   No byte an uploader crafted is ever served or stored as-is.

## The "Add Wallpaper to Personal Jarvis" button

The storefront button POSTs to the running app at
`127.0.0.1:47821/api/marketplace/community/wallpapers/{name}/install`.
`SurfaceSecurity` admits **exactly this path from exactly the configured
storefront origin** (`marketplace.storefront_origin`, default
`https://personaljarvis.ai`, empty string closes the door) — including the
private-network preflight Chrome requires. It deliberately does NOT add the
storefront to the general trusted origins: a compromised storefront must
never be worth more than "a moderated wallpaper appeared in the picker".
The app resolves the name against the moderated feed itself; the website
can not hand it a URL. Fallback when no app answers: the three
install-standard surfaces (CLI / uvx / assistant prompt).

Imports land in the existing uploads store with an `origin:
community:<name>` marker (dedupe + future "installed" badges) and show up
in the picker under "Yours".

## Who owns what

| Piece | Repo | Key files |
| --- | --- | --- |
| Feed model, import route, storefront door, CLI | PersonalJarvis (app) | `jarvis/marketplace/community_source.py`, `jarvis/ui/web/marketplace_routes.py`, `jarvis/ui/web/surface_security.py`, `jarvis/cli_ctl/commands/marketplace.py` |
| Validation, never-auto-merge, ledger, site build | marketplace (registry) | `scripts/validate.py`, `scripts/automerge_gate.py`, `scripts/build_index.py`, `scripts/test_wallpapers.py` |
| Gallery, upload, review UI, inbox functions | personal-jarvis-webui (storefront) | `src/pages/marketplace/wallpapers/*`, `functions/api/marketplace/submit-wallpaper.ts`, `functions/api/marketplace/wallpapers/*`, `functions/_lib/wallpapers.ts` |
| Review queue | marketplace-inbox (PRIVATE) | `pending/<name>/submission.json` + image |

Rules that must move together across repos: the `kind` enum (schema,
`validate.py`, `expand.py`, app), the name regex, the license allowlist
(`WALLPAPER_LICENSES` → `rules.json` → storefront), and the install-command
strings (`install_standard.py` ↔ `src/lib/wallpapers-client.ts`).

## Activation checklist (maintainer)

1. **Push the registry** (the local `PersonalJarvis/marketplace` clone, two
   commits) — deploys the wallpapers lane + three CC0 seed wallpapers to
   Pages.
2. **Push the storefront** (the webui repo, branch `remake`) — Cloudflare
   Pages deploys the gallery/upload/review pages and functions.
3. **Add `marketplace-inbox` to the GitHub App installation** (org owner,
   in the browser): github.com → PersonalJarvis org settings → GitHub Apps
   → personal-jarvis-marketplace → Repository access → add
   `marketplace-inbox`. Without this, uploads answer 502.
4. **App repo** is committed on main; ships with the next release. Optional
   Cloudflare env overrides: `INBOX_REPO`, `MAINTAINER_GITHUB_IDS` (both
   have working defaults in code).

## Residual risks, stated honestly

- The maintainer sees troll images during review; that is inherent to
  pre-moderation. The queue endpoint caps at 50 listed entries and the
  submitter quota (3/day/account, GitHub sign-in required) keeps volume
  human-scale.
- A published wallpaper can still be reported (report link on every detail
  page → registry issue); delisting is one commit that removes the
  submission + image, and the next Pages deploy drops it from the feed.
- The skills `raw_url` download in `jarvis/skills/finder.py` still follows
  redirects (pre-existing); the wallpaper download does not. Worth
  unifying some day.
