# Bundled brand marks

The **original** mark of each service, bundled so a card renders offline, on a
locked-down network, and on a headless host without calling any third party at
render time.

Every mark here belongs to its owner. They are used **solely to identify the
service a plugin connects to** — nominative use — and never to imply that the
owner endorses, sponsors or is affiliated with this project. If you own a mark
listed here and want it removed, open an issue and it will be taken out.

> A CC0 or MIT licence on an SVG settles the **copyright in the drawing**. It
> does not grant **trademark** rights, and no entry below claims otherwise.
> Where a vendor's guidelines forbid third-party use of their product icon, do
> not add the file: leave it to the fallback, which draws the vendor's glyph on
> their brand colour instead.

Marks sit on a **dark plate**, matching the rest of the interface. A brand
whose own logo is near-black (GitHub, Vercel, Notion) therefore uses the
**light variant its vendor publishes for exactly this purpose** — swapping to
one of those is still the original mark, while recolouring one ourselves
would not be.

## How the store picks a mark

`PluginsView.tsx` resolves in three tiers, so a missing file is never a blank
card:

1. `<plugin-id>.svg` in this folder — the original full-colour mark, rendered
   inset on a neutral tile. **This is the tier every plugin should reach.**
2. Otherwise the Simple Icons glyph on the plugin's `logo_color` brand tile.
3. Otherwise a monogram on that same tile.

Tiers 2 and 3 are a safety net, **not a substitute for the real mark**. They
draw a white glyph on the brand colour, which is genuinely correct for the
handful of brands whose actual app icon looks exactly like that (Stripe,
Cloudflare, Cal.com) and plainly wrong for everyone else — Gmail is not a white
envelope on red, Google Drive is not a white triangle on green. If a card is
sitting on tier 2 or 3 and the brand does not really look like that, the fix is
to add the original file here, not to adjust the colour.

## Adding one

1. Take the mark from the vendor's own brand/press page, or from a
   permissively-licensed collection.
2. Prefer the **icon** variant over the wordmark. A horizontal logotype shrunk
   into a 40 px square is unreadable; that is why Todoist uses `todoist-icon`
   rather than `todoist`.
3. Strip scripts, external references and embedded raster images; keep a
   roughly square `viewBox`.
4. Save it as `<plugin-id>.svg` — the catalog id, so no wiring is needed.
5. Add a row below. An entry without a row is a licence gap, not a shortcut.

`scripts/ci/check_brand_logos.py` enforces steps 3–5.

## No original available?

Look harder first — this is where a hand-drawn placeholder is usually the wrong
answer. Cal.com appeared to publish only a horizontal wordmark, so it briefly
got a drawn calendar glyph; in fact the vendor serves its own square icon at
`https://cal.com/api/logo?type=icon`. **Check the vendor's own site and icon
endpoints before concluding a mark does not exist.**

Only when that genuinely turns up nothing, run the **`design-brand-logo`**
skill. It produces a mark that is honest about being ours — a clean geometric
glyph on the service's own brand colour — rather than a bad imitation of a logo
we could not obtain. Record it below with `own work` as the legal basis.

## Ledger

| plugin_id | Source | Legal basis | Added |
|---|---|---|---|
| airtable | gilbarbara/logos `airtable.svg` | CC0 | 2026-07-25 |
| asana | gilbarbara/logos `asana-icon.svg` | CC0 | 2026-07-25 |
| cal_com | Cal.com's own icon endpoint, `https://cal.com/api/logo?type=icon` (white variant) | vendor asset | 2026-07-25 |
| canva | svgl `canva.svg` | MIT | 2026-07-25 |
| clickup | svgl `clickup.svg` | MIT | 2026-07-25 |
| discord | gilbarbara/logos `discord-icon.svg` | CC0 | 2026-07-25 |
| dropbox | gilbarbara/logos `dropbox.svg` | CC0 | 2026-07-25 |
| github | svgl `github_dark.svg` (the light variant, for dark backgrounds) | MIT | 2026-07-25 |
| gmail | gilbarbara/logos `google-gmail.svg` | CC0 | 2026-07-25 |
| google_calendar | gilbarbara/logos `google-calendar.svg` | CC0 | 2026-07-25 |
| google_drive | gilbarbara/logos `google-drive.svg` | CC0 | 2026-07-25 |
| home_assistant | svgl `home-assistant.svg` | MIT | 2026-07-25 |
| linear | svgl `linear.svg` (brand purple, legible on dark) | MIT | 2026-07-25 |
| notion | svgl `notion.svg` (the light variant, for dark backgrounds) | MIT | 2026-07-25 |
| slack | gilbarbara/logos `slack-icon.svg` | CC0 | 2026-07-25 |
| supabase | gilbarbara/logos `supabase-icon.svg` | CC0 | 2026-07-25 |
| telegram | gilbarbara/logos `telegram.svg` | CC0 | 2026-07-25 |
| todoist | gilbarbara/logos `todoist-icon.svg` | CC0 | 2026-07-25 |
| vercel | svgl `vercel_dark.svg` (the light variant, for dark backgrounds) | MIT | 2026-07-25 |

### Deliberately not bundled

| plugin_id | Why |
|---|---|
| stripe | Publishes no square full-colour icon. Its real app icon **is** a white "S" on the brand purple, so the fallback is the faithful rendering and a bundled wordmark would be less accurate. |
| cloudflare | Same: the real app icon is the white cloud on brand orange. |
