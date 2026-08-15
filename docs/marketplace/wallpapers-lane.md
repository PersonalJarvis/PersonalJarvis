# The wallpapers lane — community images, notice-and-action

**Status:** built 2026-08-14, converted from pre-moderation to
notice-and-action 2026-08-15, given an in-app publish surface 2026-08-15.
Live in the registry and the feed; the storefront's app-facing half awaits
the push listed at the end. **Related:**
[community-registry.md](community-registry.md),
[install-standard.md](install-standard.md),
[validator-parity.md](validator-parity.md).

## What changed, and why

The lane originally inverted the marketplace's trust model: every wallpaper
waited in a private queue until the maintainer approved it by hand. The
reasoning was sound — no pattern list recognizes a hateful or illegal
picture — but the cost was not payable. Pre-moderation puts one person in
front of every upload forever, and it makes that person look at exactly the
images the queue exists to stop.

So the lane now publishes automatically, like plugins and skills, and moves
its safeguard behind publication. This is the model the law actually asks of
a host: a platform is not liable for what users upload, but becomes liable
for what it leaves up after being told (DSA Art. 6 and 16). Prior inspection
is not required; prompt removal is.

**The trade accepted here is real and should be stated plainly:** a hateful
or illegal image can be publicly reachable under the project's name until
somebody reports it. What buys that back is the speed and completeness of
removal, so anything that slows a takedown is a defect in this lane, not a
nice-to-have.

## The pipeline, end to end

```
uploader — two surfaces, one endpoint, one identity:
   storefront form   GitHub session cookie + optional Turnstile
   desktop picker    GitHub device-flow token (Bearer), no Turnstile
   │  re-encode before upload: 4K cap, EXIF stripped, WebP
   │    (browser: canvas · app: Pillow, jarvis/marketplace/publish.py)
   │  machine checks: magic bytes, 8 MB cap, license allowlist,
   │                  daily per-account quota, rights statement
   ▼
PUBLIC registry  PersonalJarvis/marketplace
   submissions/<name>.json + wallpapers/<name>/wallpaper.<ext>
   │  validate (CI) → expand (ledger) → build: Pillow RE-ENCODE + thumbnail
   ▼
GitHub Pages feed   …/marketplace/index.json  wallpapers[] + image bytes
   │
   ├── storefront gallery  /marketplace/wallpapers  (live fold-in)
   ├── app wallpaper picker  "Community" chip, browse + one-click add
   ├── app import route    POST /api/marketplace/community/wallpapers/{name}/install
   └── CLI                 jarvis marketplace install <name>

   report link on every detail page  →  registry issue
   maintainer  /marketplace/wallpapers/moderate  →  one-click delist
   │  removes submissions/<name>.json, then the image
   ▼  next Pages build drops it from the feed and every surface
```

## What actually protects the lane

1. **Identity on every upload.** A GitHub sign-in is required and the
   publisher is derived from the verified identity, never from the form. An
   abusive uploader is a named account, not an anonymous POST. Two ways to
   prove that identity, one standard of proof: a session cookie our own
   callback signed, or a Bearer token verified to belong to OUR GitHub App
   before it counts as anything (`checkTokenBelongsToApp` — a token minted by
   a foreign app is not evidence, however valid it is for GitHub). Turnstile
   is skipped on the Bearer path only: the app cannot render the widget, and
   an interactive device-flow approval is the stronger gate. The quota
   applies to both.
2. **A per-account daily quota** (3/day, `MAX_SUBMISSIONS_PER_DAY`), counted
   from the registry's own commit history. Without a human gate this is the
   main brake on a flood, so it is load-bearing rather than hygiene.
3. **An explicit rights and legality statement**, recorded in the submission
   the uploader publishes under their own name. The upload form says plainly
   that nobody checks it first — a form implying later review would make the
   statement feel optional.
4. **Bytes are always re-produced.** The uploader side re-encodes before
   upload — the browser with a canvas, the app with Pillow — and the registry
   build re-encodes a third time before anything reaches Pages. No byte an
   uploader crafted is served as-is. This never judged the depiction and
   still does not; it stops payloads, not pictures. The app's route takes an
   upload ID rather than image bytes, so what is published is the picture the
   owner is looking at, already sanitized when it entered the picker.
5. **A fast, complete takedown.** Every detail page carries a report link;
   `/marketplace/wallpapers/moderate` lists everything published with a
   two-click Delist. Deletion order mirrors publication in reverse — the
   listing first, then the image — so a half-failure leaves an unreachable
   orphan rather than a listing pointing at nothing.

Because the upload form commits the image and the listing together, a
wallpaper pull request can only ever carry one of the two. `automerge_gate.py`
therefore still refuses `kind=wallpaper`, pinned by `scripts/test_wallpapers.py`
— not as a moderation gate any more, but so a listing whose picture is
missing never merges.

## The "Add Wallpaper to Personal Jarvis" button

Unchanged by this conversion. The storefront button POSTs to the running app
at `127.0.0.1:47821/api/marketplace/community/wallpapers/{name}/install`.
`SurfaceSecurity` admits **exactly this path from exactly the configured
storefront origin** (`marketplace.storefront_origin`, default
`https://personaljarvis.ai`, empty string closes the door) — including the
private-network preflight Chrome requires. It deliberately does NOT add the
storefront to the general trusted origins: a compromised storefront must
never be worth more than "a community wallpaper appeared in the picker".
The app resolves the name against the published feed itself; the website can
not hand it a URL. Fallback when no app answers: the three install-standard
surfaces (CLI / uvx / assistant prompt).

Imports land in the existing uploads store with an `origin:
community:<name>` marker (dedupe + future "installed" badges) and show up in
the picker under "Yours". Note the consequence of automatic publication: a
delisted wallpaper disappears from the feed but stays on machines that
already imported it. Removal is not retroactive.

## Who owns what

| Piece | Repo | Key files |
| --- | --- | --- |
| Feed model, import route, storefront door, CLI | PersonalJarvis (app) | `jarvis/marketplace/community_source.py`, `jarvis/ui/web/marketplace_routes.py`, `jarvis/ui/web/surface_security.py`, `jarvis/cli_ctl/commands/marketplace.py` |
| In-app publish + community browsing | PersonalJarvis (app) | `jarvis/marketplace/publish.py`, `jarvis/ui/web/marketplace_publish_routes.py`, `jarvis/ui/web/frontend/src/views/WallpaperView.tsx`, `.../components/wallpaper/PublishWallpaperDialog.tsx`, `.../hooks/useCommunityWallpapers.ts` |
| Validation, never-auto-merge, ledger, site build | marketplace (registry) | `scripts/validate.py`, `scripts/automerge_gate.py`, `scripts/build_index.py`, `scripts/test_wallpapers.py` |
| Gallery, upload, moderation UI, publish + delist functions | personal-jarvis-webui (storefront) | `src/pages/marketplace/wallpapers/*`, `functions/api/marketplace/submit-wallpaper.ts`, `functions/api/marketplace/wallpapers/*`, `functions/_lib/wallpapers.ts` |

Rules that must move together across repos: the `kind` enum (schema,
`validate.py`, `expand.py`, app), the name regex, the license allowlist
(`WALLPAPER_LICENSES` → `rules.json` → storefront), and the install-command
strings (`install_standard.py` ↔ `src/lib/wallpapers-client.ts`).

## Activation checklist (maintainer)

Measured against the live services on 2026-08-15, not against the clones:

1. **Registry — done.** `index.json` is at revision 13 and carries
   `wallpapers[]` with three CC0 seeds; every image URL resolves.
2. **Storefront — deployed, one commit behind.**
   `POST /api/marketplace/submit-wallpaper` answers **401** (it exists and
   demands identity), but the deployed revision is the PRE-conversion one: it
   still writes to the private inbox repo and accepts a browser session only.
   The local `remake` clone holds two commits it has not seen — the
   notice-and-action rewrite, and the Bearer path the desktop app needs.
   **Until that push, in-app wallpaper publishing answers 401 and the
   picker's Share button is the only part that works.**
3. **App — done and committed on main.** The picker browses the community
   lane and offers Share; the button hides itself when
   `publish_wallpaper_endpoint` is empty, so a fork without the lane shows
   nothing broken.

The private `marketplace-inbox` repo is obsolete once step 2 ships. Nothing
in the current code reads or writes it.

## Residual risks, stated honestly

- **An illegal image is public until reported.** This is the accepted trade,
  not an oversight. The report link, the moderation page and the delist
  endpoint exist to make the exposure window short; keep all three working.
- **Child sexual abuse material is the one case where removal is not
  enough.** Delist it, do not forward or archive it, and report the incident
  to the police or a hotline (in Germany: jugendschutz.net / BKA). Every
  other category is handled by taking the image down.
- **A delisted wallpaper survives on machines that already imported it.** The
  feed is the only thing a takedown reaches.
- **The quota is per account, and accounts are free.** A determined attacker
  can make more accounts. If that ever happens in practice, the cheap next
  step is an automated image-moderation check on upload (an OpenAI moderation
  call or Cloudflare Images' classifier), which was the alternative to this
  design and remains the natural escalation.
- The skills `raw_url` download in `jarvis/skills/finder.py` still follows
  redirects (pre-existing); the wallpaper download does not. Worth unifying
  some day.
