# Bundled CLI marks

The **original** mark of each vendor whose command-line tool the CLIs section
lists, bundled so a row renders offline, on a locked-down network, and on a
headless host without calling any third party at render time. Same rules and
same gate as `../brands/LOGOS.md` and `../providers/LOGOS.md`
(`scripts/ci/check_brand_logos.py` covers all three folders).

Every mark belongs to its owner. They are used **solely to identify the vendor
whose CLI a row drives** — nominative use — and never to imply that the owner
endorses, sponsors or is affiliated with this project. If you own a mark listed
here and want it removed, open an issue and it will be taken out.

> A CC0 or MIT licence on an SVG settles the **copyright in the drawing**. It
> does not grant **trademark** rights, and no entry below claims otherwise.

## How a row picks a mark

`CliLogo.tsx` maps a catalog CLI name to a **vendor** — the company the tool
talks to, not the binary — and the vendor to a file here. `gcloud` and `gam`
are both Google, but a different Google: `gcloud` draws the Cloud hexagon and
`gam` the Google G, because that is what each tool actually administers. A CLI
with no vendor row (`jarvisctl`, any custom CLI someone registers) draws the
category glyph on the same tile, so adding a CLI never produces a broken image.

Two render paths, chosen per file and recorded in the table — the same two the
provider cards use:

* **colour** — the vendor's full-colour icon (the Azure kite, the Kubernetes
  helm, the Firebase flame). Drawn as an `<img>` on a neutral tile; the colours
  are the brand and are never recoloured.
* **mono** — a single-colour glyph whose own brand is black-on-white or
  white-on-black (GitHub, Vercel, Stripe, Cloudflare, AWS, Railway, Render,
  PlanetScale). Drawn as a CSS mask over the current text ink, so it follows
  the theme by itself: near-white on the dark app, near-black on paper, from
  one asset.

## Adding one

1. Take the mark from the vendor's own brand/press page, or from a
   permissively-licensed collection that redistributes vendor originals.
2. Prefer the **icon** variant over the wordmark — `gitlab-icon`, not `gitlab`.
   The gate rejects anything wider than 1.45:1.
3. Strip scripts, external references and embedded raster images; keep a
   roughly square `viewBox`. Root-level `width`/`height`/`style` go too — the
   tile sizes the mark.
4. Save it as `<vendor>.svg` and add the vendor to `CLI_VENDOR_LOGOS` in
   `CliLogo.tsx`, plus the CLI name to `CLI_VENDORS`.
5. Add a row below. An entry without a row is a licence gap, not a shortcut.

## Ledger

| vendor | CLIs | Source | Legal basis | Render | Added |
| --- | --- | --- | --- | --- | --- |
| aws | `aws` | simple-icons `amazonwebservices.svg` | CC0-1.0 | mono | 2026-08-23 |
| azure | `az` | gilbarbara/logos `microsoft-azure.svg` | CC0 | colour | 2026-08-23 |
| cloudflare | `wrangler` | simple-icons `cloudflare.svg` | CC0-1.0 | mono | 2026-08-23 |
| docker | `docker` | gilbarbara/logos `docker-icon.svg` | CC0 | colour | 2026-08-23 |
| firebase | `firebase` | gilbarbara/logos `firebase-icon.svg` | CC0 | colour | 2026-08-23 |
| fly | `flyctl` | gilbarbara/logos `fly-icon.svg` | CC0 | colour | 2026-08-23 |
| github | `gh` | simple-icons `github.svg` | CC0-1.0 | mono | 2026-08-23 |
| gitlab | `glab` | gilbarbara/logos `gitlab-icon.svg` | CC0 | colour | 2026-08-23 |
| google | `gam` (Google Workspace) | gilbarbara/logos `google-icon.svg` | CC0 | colour | 2026-08-23 |
| google-cloud | `gcloud` | gilbarbara/logos `google-cloud.svg` | CC0 | colour | 2026-08-23 |
| heroku | `heroku` | gilbarbara/logos `heroku-icon.svg` | CC0 | colour | 2026-08-23 |
| kubernetes | `kubectl` | gilbarbara/logos `kubernetes.svg` | CC0 | colour | 2026-08-23 |
| neon | `neonctl` | gilbarbara/logos `neon-icon.svg` | CC0 | colour | 2026-08-23 |
| netlify | `netlify` | gilbarbara/logos `netlify-icon.svg` | CC0 | colour | 2026-08-23 |
| planetscale | `pscale` | simple-icons `planetscale.svg` | CC0-1.0 | mono | 2026-08-23 |
| railway | `railway` | simple-icons `railway.svg` | CC0-1.0 | mono | 2026-08-23 |
| render | `render` | simple-icons `render.svg` | CC0-1.0 | mono | 2026-08-23 |
| stripe | `stripe` | simple-icons `stripe.svg` | CC0-1.0 | mono | 2026-08-23 |
| supabase | `supabase` | gilbarbara/logos `supabase-icon.svg` | CC0 | colour | 2026-08-23 |
| twilio | `twilio` | gilbarbara/logos `twilio-icon.svg` | CC0 | colour | 2026-08-23 |
| vercel | `vercel` | simple-icons `vercel.svg` | CC0-1.0 | mono | 2026-08-23 |

### Deliberately not bundled

| CLI | Why |
|---|---|
| `jarvisctl` | Ours, not a third party's. It draws the terminal glyph on the same tile rather than pretending to be a vendor brand. |
