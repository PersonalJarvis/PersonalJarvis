# Brand Guidelines

The visual language of Personal Jarvis: matte black, a single signal-yellow, and a
distressed wordmark. The goal is *confident and engineered*, never noisy or playful.

<p align="center">
  <img src="../assets/brand/banner.png" alt="Personal Jarvis wordmark" width="720" />
</p>

## Color

One accent, one surface family. The yellow does the talking; everything else is near-black.

| Role | Hex | Chip |
|---|---|---|
| Signal Yellow (primary) | `#FFD60A` | ![](https://img.shields.io/badge/_-FFD60A?style=flat-square&labelColor=FFD60A) |
| Gold highlight | `#FFE552` | ![](https://img.shields.io/badge/_-FFE552?style=flat-square&labelColor=FFE552) |
| Deep gold (gradient end) | `#B8960A` | ![](https://img.shields.io/badge/_-B8960A?style=flat-square&labelColor=B8960A) |
| Matte Black (background) | `#0A0A0A` | ![](https://img.shields.io/badge/_-0A0A0A?style=flat-square&labelColor=0A0A0A) |
| Card | `#0F0F0F` | ![](https://img.shields.io/badge/_-0F0F0F?style=flat-square&labelColor=0F0F0F) |
| Border | `#242424` | ![](https://img.shields.io/badge/_-242424?style=flat-square&labelColor=242424) |
| Foreground (text) | `#F4F4F5` | ![](https://img.shields.io/badge/_-F4F4F5?style=flat-square&labelColor=F4F4F5) |
| Muted text | `#8F8F8F` | ![](https://img.shields.io/badge/_-8F8F8F?style=flat-square&labelColor=8F8F8F) |

**Signal-yellow gradient** (used on the wordmark and primary accents):
`linear-gradient(177deg, #FFE552 0%, #FFD60A 52%, #B8960A 100%)`.

These are the exact tokens from the desktop app
(`jarvis/ui/web/frontend/src/index.css`) — the README, the product, and any brand asset
must stay on the same values so nothing drifts.

### Rules

- **Yellow is for emphasis, not fill.** Use it for the wordmark, key accents, links, and a
  single call-to-action — not for large flat panels.
- **Default to dark.** Black is the canvas. Avoid light backgrounds; if one is unavoidable,
  use `#0A0A0A` text on it and skip the glow.
- **One accent only.** No second brand color. The cyan/magenta in the wordmark are a
  *glitch artifact*, not part of the palette — never use them as UI colors.

## Typography

| Use | Typeface | Notes |
|---|---|---|
| Display / wordmark | **Space Grotesk** (700) | Uppercase, tight tracking (`-4 to -5px` at hero size) |
| Body / UI | **Inter** | The product UI font |
| Code / mono / tagline | **JetBrains Mono** (500) | Letter-spaced caps for taglines and labels |

## The wordmark

The hero is the word **PERSONAL JARVIS** as an embossed, beveled **metallic-gold** wordmark
on matte black — chunky geometric capitals with 3D bevels, specular highlights, a warm
golden bloom, and faint embers. It should read like forged gold, not flat text.

- **Live banner:** [`../assets/brand/banner.png`](../assets/brand/banner.png) — a
  high-resolution generated raster (2172×724, 3:1). This is the file the README embeds.
- **CSS fallback:** [`../assets/brand/banner.html`](../assets/brand/banner.html) is a
  fully reproducible pure-CSS/SVG treatment of the same wordmark;
  `pwsh assets/brand/render.ps1` rasterizes it to `banner-css.png`. Use it only where a
  generated raster isn't available.

### Do

- Keep clear space around the wordmark of at least the cap-height on every side.
- Keep it on a dark, low-detail background so the glow reads.
- Keep the distress subtle — legibility first.

### Don't

- Don't recolor it (no blue, no white-only, no rainbow).
- Don't crank the glitch until letters are hard to read — it's seasoning, not the dish.
- Don't place it on a busy photo without a dark scrim behind it.
- Don't stretch, condense, or rotate it.

## Voice & tone

Write like a senior engineer who respects the reader's time.

- **Honest over hype.** State what's live, what's pending, and what's unverified — the way
  the product's own verification badges do. No "blazingly fast", no exclamation storms.
- **Concrete over abstract.** Show the mechanism, not the marketing.
- **Sparing with emoji.** A single functional marker is fine; a wall of them is not.
- **English for artifacts** (code, docs, commits); the assistant *speaks* de/en/es at
  runtime, but everything written into the repo is English.

## Asset index

| Asset | Path |
|---|---|
| Hero banner (live) | `assets/brand/banner.png` |
| Hero banner (CSS fallback source) | `assets/brand/banner.html` → `banner-css.png` |
| Banner render script | `assets/brand/render.ps1` |
| Product Orb | `jarvis/ui/web/frontend/public/hero-orb.png` |
| Mascot (Gigi) | `assets/icons/jarvis-gigi-256.png` |
| App screenshots | `assets/screenshots/` |
