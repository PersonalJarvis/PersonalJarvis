# Bundled brand marks

Full-colour brand marks for the Plugins store, bundled so a card renders
offline, on a locked-down network, and on a headless host without calling any
third party at render time.

Every mark here belongs to its owner. They are used **solely to identify the
service a plugin connects to** — nominative use — and never to imply that the
owner endorses, sponsors or is affiliated with this project. If you own a mark
listed here and want it removed, open an issue and it will be taken out.

> A CC0 or MIT licence on an SVG settles the **copyright in the drawing**. It
> does not grant **trademark** rights, and no entry below claims otherwise.
> Where a vendor's guidelines forbid third-party use of their product icon, do
> not add the file: leave it to the automatic fallback, which draws the vendor's
> glyph or initial on their brand colour instead.

## How the store picks a mark

`PluginsView.tsx` resolves in three tiers, so a missing file is never a blank
card:

1. `<plugin-id>.svg` in this folder — a full-colour mark on a neutral tile.
2. Otherwise the Simple Icons glyph on the plugin's `logo_color` brand tile.
3. Otherwise a monogram on that same tile — also what appears if tier 2 cannot
   load at all.

Tier 2 is deliberately good enough to ship with: a coloured tile reads as a
design decision, so the catalog never has to wait for a complete asset set.

## Adding one

1. Take the mark from the vendor's own brand/press page, or from a
   permissively-licensed collection.
2. Run it through `svgo`. Remove scripts, external references and embedded
   raster images; keep a square `viewBox`.
3. Save it as `<plugin-id>.svg` — the catalog id, so no wiring is needed.
4. Add a row below. An entry without a row is a licence gap, not a shortcut.

## Ledger

| plugin_id | Source | Legal basis | Added |
|---|---|---|---|

*(empty — every card currently renders through tier 2 or 3)*
