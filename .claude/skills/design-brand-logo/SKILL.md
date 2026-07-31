---
name: design-brand-logo
description: Obtain or, as a last resort, design the square brand mark a Plugins-store card needs. Use when adding a marketplace plugin, when a card falls back to a coloured tile or a letter monogram, or when someone reports that a logo "doesn't look original". Trigger phrases - "the logo is wrong", "get the real logo", "add a logo for <service>", "make a logo", "das Logo ist nicht original".  # i18n-allow: quoted German maintainer trigger phrase
---

# Getting a plugin's brand mark right

A card's mark is the first thing anyone judges the store by. A wrong one does
not read as a small cosmetic gap — it reads as fake.

**The order is: find the original, then find the original somewhere else, and
only then draw something.** Drawing is the last resort, and what you draw must
be honestly ours rather than an imitation of a mark you could not obtain.

## Where the store looks

`PluginsView.tsx` resolves three tiers per card:

1. `src/assets/brands/<plugin-id>.svg` — the original mark, inset on a neutral
   tile. Every card should reach this tier.
2. The Simple Icons glyph in white on the plugin's `logo_color` brand tile.
3. A letter monogram on that same tile.

Tiers 2 and 3 exist so a card is never blank. They are **not** a substitute for
the real mark: they draw a white glyph on a colour, which is genuinely how a
few brands look (Stripe, Cloudflare) and plainly wrong for most (Gmail is not a
white envelope on red). A card sitting on tier 2 that does not really look like
that is a bug.

## Step 1 — Look for the original

Try, in order:

- **gilbarbara/logos** (CC0, ~1861 full-colour SVGs):
  `https://cdn.jsdelivr.net/gh/gilbarbara/logos@main/logos/<name>.svg`
  Try `<brand>-icon` **before** `<brand>` — the plain name is usually the
  horizontal logotype.
- **svgl** (MIT):
  `https://cdn.jsdelivr.net/gh/pheralb/svgl@main/static/library/<name>.svg`
- **The vendor's own site.** Many serve the square icon from a live endpoint
  even when the public collections only carry their wordmark — Cal.com serves
  its at `https://cal.com/api/logo?type=icon`. Try `/logo.svg`, `/icon.svg`,
  `/api/logo?type=icon`, the `<link rel="icon">` in their homepage HTML, and
  their brand or press page before giving up.

Take the **icon**, not the wordmark. A logotype shrunk into a 40 px square is
unreadable; check the `viewBox` and reject anything wider than about 1.45:1.

## Step 2 — Decide whether the fallback is already correct

Before concluding a mark is missing, ask what the service's **real app icon**
looks like. If it genuinely is a white glyph on a solid brand colour, tier 2 is
the faithful rendering and bundling a wordmark would make it *less* accurate.
Set the right `logo_color` in the catalog and record the decision in the
"Deliberately not bundled" table of `LOGOS.md`.

## Step 3 — Only now, design one

Applies to a self-hosted or niche service with no published mark. Be sceptical
that you are really here: Cal.com looked like this case because the public
collections only carry its wordmark, and it turned out to serve a perfectly good
square icon from its own site. A hand-drawn placeholder that replaces an
obtainable mark is worse than no work at all.

Do **not** approximate a logo you have seen. Guessing at someone's trademark is
both legally worse and visually obvious. Design something that is clearly a
Jarvis-made placeholder:

- one geometric glyph that says what the service *does* (a document, a house, a
  calendar), not a stylised initial pretending to be a logotype;
- flat white on the service's own brand colour, so it sits in the same visual
  family as tier 2;
- a square `viewBox`, a single path where possible, no gradients, no text;
- clear at 20 px, since that is the only size it will ever be seen at.

Record it in the ledger with `own work` as the legal basis, so nobody later
mistakes it for an official asset.

## Step 4 — Land it

1. Save as `src/assets/brands/<plugin-id>.svg` — the catalog id, so no wiring
   is needed.
2. Strip anything active: `<script>`, `<foreignObject>`, `on*` handlers,
   external `href`s, embedded raster `<image>`. These files come from outside
   the project and an SVG runs on render.
3. Add a row to `LOGOS.md`: plugin id, source, legal basis, date.
4. Run `python scripts/ci/check_brand_logos.py` — it enforces steps 2 and 3
   plus the aspect ratio, and it is wired into pre-push.
5. `npm run build`, then look at the card. The bundle is committed, so an
   unbuilt change reaches nobody.

## The licence line to keep straight

CC0 and MIT settle the **copyright in the drawing**. Neither grants
**trademark** rights. Bundling is nominative use — identifying the service a
plugin connects to — and the ledger says so. If a vendor's brand guidelines
forbid third-party use of their icon, do not bundle it: leave it on tier 2 and
note why.
