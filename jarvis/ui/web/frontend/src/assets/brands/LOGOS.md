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

The plugin window presents original marks on consistent **white icon tiles**
in both themes, following the supplied marketplace reference. The larger rows
use 48-pixel tiles with 36-pixel artwork. The bundled white monochrome GitHub,
Vercel, Notion and Cal.com glyphs use CSS inversion on these tiles; multicolour
artwork keeps its original colours. X, AWS and AMD render directly in black.

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
| higgsfield | vendor app icon `https://higgsfield.ai/icon.png` (vectorized outline of the official lime-and-snake mark; Higgsfield publishes no SVG) | vendor asset | 2026-08-19 |
| home_assistant | svgl `home-assistant.svg` | MIT | 2026-07-25 |
| linear | svgl `linear.svg` (brand purple, legible on dark) | MIT | 2026-07-25 |
| notion | svgl `notion.svg` (the light variant, for dark backgrounds) | MIT | 2026-07-25 |
| slack | gilbarbara/logos `slack-icon.svg` | CC0 | 2026-07-25 |
| spotify | gilbarbara/logos `spotify-icon.svg` | CC0 | 2026-08-17 |
| supabase | gilbarbara/logos `supabase-icon.svg` | CC0 | 2026-07-25 |
| telegram | gilbarbara/logos `telegram.svg` | CC0 | 2026-07-25 |
| todoist | gilbarbara/logos `todoist-icon.svg` | CC0 | 2026-07-25 |
| vercel | svgl `vercel_dark.svg` (the light variant, for dark backgrounds) | MIT | 2026-07-25 |
| youtube_music | svgl `youtube_music.svg` | MIT | 2026-08-18 |

### Local delivery

Every built-in plugin now has local original artwork. Community plugins may
still supply their own logo URL. No built-in depends on the Simple Icons CDN.

## Service connector expansion (2026-09-10)

| Plugin | Source | Drawing license | Date |
|---|---|---|---|
| outlook | [Vendor original](https://cdn-dynmedia-1.microsoft.com/is/content/microsoftcorp/Outlook-Icon-FY26) | Vendor artwork; nominative identification only | 2026-09-10 |
| teams | [Vendor original](https://cdn-dynmedia-1.microsoft.com/is/content/microsoftcorp/Teams-Icon-FY26) | Vendor artwork; nominative identification only | 2026-09-10 |
| sharepoint | [Original mark](https://cdn.jsdelivr.net/gh/pheralb/svgl@main/static/library/microsoft-sharepoint.svg) | MIT; nominative use | 2026-09-10 |
| onenote | [Vendor original](https://www.microsoft.com/content/dam/microsoft/bade/images/icons/en-us/m365-app-icons-fy26/OneNote-Icon-FY26.svg) | Vendor artwork; nominative identification only | 2026-09-10 |
| microsoft_todo | [Original mark](https://cdn.jsdelivr.net/gh/pheralb/svgl@main/static/library/microsoft-todo.svg) | MIT; nominative use | 2026-09-10 |
| azure | [Vendor original](https://learn.microsoft.com/en-us/azure/media/index/azure.svg) | Vendor artwork; nominative identification only | 2026-09-10 |
| google_cloud | [Vendor original](https://www.gstatic.com/cgc/supercloud_favicon.ico) | Vendor artwork; nominative identification only | 2026-09-10 |
| gitlab | [Original mark](https://cdn.jsdelivr.net/gh/gilbarbara/logos@main/logos/gitlab-icon.svg) | CC0-1.0; nominative use | 2026-09-10 |
| x | [Original X mark](https://cdn.jsdelivr.net/npm/simple-icons@latest/icons/x.svg) | CC0-1.0; monochrome, white icon tile | 2026-09-10 |
| linkedin | [Original mark](https://cdn.jsdelivr.net/gh/gilbarbara/logos@main/logos/linkedin-icon.svg) | CC0-1.0; nominative use | 2026-09-10 |
| youtube_studio | [Original mark](https://cdn.jsdelivr.net/gh/gilbarbara/logos@main/logos/youtube-icon.svg) | CC0-1.0; nominative use | 2026-09-10 |
| hubspot | [Original mark](https://cdn.jsdelivr.net/npm/simple-icons@latest/icons/hubspot.svg) | CC0-1.0; nominative use | 2026-09-10 |
| apollo | [Original mark](https://cdn.jsdelivr.net/gh/pheralb/svgl@main/static/library/apollo-io.svg) | MIT; nominative use | 2026-09-10 |
| salesforce | [Original mark](https://cdn.jsdelivr.net/gh/gilbarbara/logos@main/logos/salesforce.svg) | CC0-1.0; nominative use | 2026-09-10 |
| granola | [Original mark](https://cdn.jsdelivr.net/gh/pheralb/svgl@main/static/library/granola-light.svg) | MIT; nominative use | 2026-09-10 |
| zoom | [Original mark](https://cdn.jsdelivr.net/gh/gilbarbara/logos@main/logos/zoom-icon.svg) | CC0-1.0; nominative use | 2026-09-10 |
| amd_gpu | [Original mark](https://cdn.jsdelivr.net/npm/simple-icons@latest/icons/amd.svg) | CC0-1.0; nominative use | 2026-09-10 |
| onedrive | [Vendor original](https://www.microsoft.com/content/dam/microsoft/bade/images/icons/en-us/m365-app-icons-fy26/OneDrive-Icon-FY26.svg) | Vendor artwork; nominative identification only | 2026-09-10 |
| aws | Simple Icons amazonwebservices.svg; shared with [CLI marks](../clis/LOGOS.md) | CC0-1.0; monochrome, white icon tile | 2026-09-10 |
| meta | [Original mark](https://cdn.jsdelivr.net/gh/gilbarbara/logos@main/logos/meta-icon.svg) | CC0-1.0; unchanged artwork inside square viewport | 2026-09-10 |
| figma | [Original mark](https://cdn.jsdelivr.net/gh/pheralb/svgl@main/static/library/figma.svg) | MIT; unchanged artwork inside square viewport | 2026-09-10 |
| agentmail | [Vendor original](https://www.agentmail.to/favicon.ico) | Vendor artwork; nominative identification only | 2026-09-10 |

| stripe | [Vendor original](https://images.stripeassets.com/fzn2n1nzq965/1hgcBNd12BfT9VLgbId7By/01d91920114b124fb4cf6d448f9f06eb/favicon.svg) | Vendor artwork; nominative identification only | 2026-09-10 |
| cloudflare | [Vendor original](https://www.cloudflare.com/favicon.ico) | Vendor artwork; nominative identification only | 2026-09-10 |

The `x` and `aws` marks use the monochrome [Simple Icons](https://github.com/simple-icons/simple-icons) variants (CC0-1.0) on white icon tiles. HubSpot uses its published orange. The X mark is the current X symbol, not the retired Twitter bird.

## Trace integration mark

`chrome.svg`: Google Chrome, from https://cdn.simpleicons.org/googlechrome (Simple Icons, CC0 1.0). Used to identify Chrome tool activity; the trademark belongs to Google.



Google Cloud, Cloudflare and AgentMail PNG files are lossless extractions of the largest image in the vendor ICO containers. Their artwork was not redrawn, recoloured or resized. Microsoft FY26 and Azure SVG files are byte-for-byte vendor originals.

| Plugin | Source | Drawing license | Date |
|---|---|---|---|
| chrome | https://cdn.simpleicons.org/googlechrome | CC0-1.0; Chrome trace identification | 2026-09-10 |
