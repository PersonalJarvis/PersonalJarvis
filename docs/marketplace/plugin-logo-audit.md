# Plugin logo and desktop delivery audit — 2026-09-10

## Confirmed cause

The inspected desktop still displayed the old four-column plugin table. Its served entry was `index-C2qPawXj.js`, identical to the committed main bundle. That main source revision did not contain `PluginsDialog`; the new UI and new brand files remained outside main. Later builds from main therefore restored the old interface even though source-only tests and builds of another checkout had passed.

The live compiled SVG map omitted all 22 added connectors as well as Stripe and Cloudflare. AgentMail could still display its separate local ICO. Twelve entries fell back to letter tiles. Eleven depended on external Simple Icons glyphs; monochrome variants of multicolour brands such as Google Cloud, Figma and GitLab were not the intended original app presentation. Monochrome brands such as X are not inherently incorrect, but their network dependency was unnecessary.

## All 46 plugins

The table records the state observed before correction and the local artwork now selected. Full source/license provenance is maintained in [the brand ledger](../../jarvis/ui/web/frontend/src/assets/brands/LOGOS.md).

| Plugin | Before correction in desktop | Original asset supplied locally |
|---|---|---|
| GitHub | Local original already available | `github.svg` |
| Vercel | Local original already available | `vercel.svg` |
| Supabase | Local original already available | `supabase.svg` |
| Notion | Local original already available | `notion.svg` |
| Slack | Local original already available | `slack.svg` |
| Linear | Local original already available | `linear.svg` |
| Stripe | External glyph fallback | `stripe.svg` |
| Cloudflare | External glyph fallback | `cloudflare.png` |
| Discord | Local original already available | `discord.svg` |
| Telegram | Local original already available | `telegram.svg` |
| Asana | Local original already available | `asana.svg` |
| Google Drive | Local original already available | `google_drive.svg` |
| Gmail | Local original already available | `gmail.svg` |
| Google Calendar | Local original already available | `google_calendar.svg` |
| Todoist | Local original already available | `todoist.svg` |
| ClickUp | Local original already available | `clickup.svg` |
| Dropbox | Local original already available | `dropbox.svg` |
| Canva | Local original already available | `canva.svg` |
| Airtable | Local original already available | `airtable.svg` |
| Cal.com | Local original already available | `cal_com.svg` |
| Home Assistant | Local original already available | `home_assistant.svg` |
| Spotify | Local original already available | `spotify.svg` |
| YouTube Music | Local original already available | `youtube_music.svg` |
| Higgsfield | Local original already available | `higgsfield.svg` |
| Outlook Mail & Calendar | Letter placeholder | `outlook.svg` |
| OneDrive | Letter placeholder | `onedrive.svg` |
| Microsoft Teams | Letter placeholder | `teams.svg` |
| SharePoint | Letter placeholder | `sharepoint.svg` |
| OneNote | Letter placeholder | `onenote.svg` |
| Microsoft To Do | Letter placeholder | `microsoft_todo.svg` |
| AWS | Letter placeholder | `aws.svg` |
| Microsoft Azure | Letter placeholder | `azure.svg` |
| Google Cloud | External glyph fallback | `google_cloud.png` |
| GitLab | External glyph fallback | `gitlab.svg` |
| AgentMail | Local original already available | `agentmail.png` |
| X (Twitter) | External glyph fallback | `x.svg` |
| LinkedIn | Letter placeholder | `linkedin.svg` |
| Meta (Facebook & Instagram) | External glyph fallback | `meta.svg` |
| YouTube Studio | External glyph fallback | `youtube_studio.svg` |
| HubSpot | External glyph fallback | `hubspot.svg` |
| Apollo.io | Letter placeholder | `apollo.svg` |
| Salesforce | Letter placeholder | `salesforce.svg` |
| Granola | Letter placeholder | `granola.svg` |
| Zoom | External glyph fallback | `zoom.svg` |
| Figma | External glyph fallback | `figma.svg` |
| AMD GPU Status | External glyph fallback | `amd_gpu.svg` |

## Delivery acceptance

Source presence alone is insufficient. Acceptance requires the committed main source, the served bundle, and the actual desktop window to agree: modal presentation, category filters, all 46 local logo mappings, and visible original artwork for the named Microsoft and Google services. The regression test `test_plugin_frontend_delivery.py` follows the reachable chunk graph from index.html, so an orphaned new chunk beside an old entry does not pass.

## Verified delivery

At verification, the live server and committed main bundle both referenced `index-Dn9pjjla.js`.
All 419 transitively reachable build files pass the Git delivery consistency
check. All 46 catalog logos occur in the reachable production logo map. The
actual artwork was also compared: canonical SVG content or PNG bytes for every
logo match the original source assets, including assets embedded as data URLs.
The seven separately emitted brand images were fetched from the live server
and matched byte-for-byte; the served entry assets also match the local build.

The actual Personal Jarvis desktop window's accessibility tree now reports a
Plugins dialog, its search field and recommendation filters, rather than the
old four-column table. This was checked without mouse or keyboard input.
Occluded-window screenshots that showed a different foreground application
were not used as evidence. The modal's layout and light/dark presentation were
verified during the preceding UI implementation; this correction changes the
artwork and its delivery, not that layout.

The complete logo resolver test passes for all 46 catalog entries plus the
reported regression cases. Provider-account authentication is outside this
visual change. No credentials, accounts or provider permissions were changed
by the logo correction.
