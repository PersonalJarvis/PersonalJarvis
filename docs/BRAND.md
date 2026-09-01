# Brand Guidelines

The visual language of Personal Jarvis: ink on paper, paper on ink, and a distressed
wordmark. The interface carries no brand hue; the wordmark below is the one place a
colour treatment survives. The goal is *confident and engineered*, never noisy or
playful.

<p align="center">
  <img src="../assets/brand/banner.png" alt="Personal Jarvis wordmark" width="720" />
</p>

## Color

Two values and the distance between them. The primary is whichever end of the value scale
the ground is not: ink on paper in light mode, white on charcoal in dark mode. State is not a
colour either, so warning and success read on the same ink token. Dark does not inherit the
paper's warmth — that was the muddy look.

| Role | Hex | Chip |
|---|---|---|
| Ink (light primary, light text) | `#26251E` | ![](https://img.shields.io/badge/_-26251E?style=flat-square&labelColor=26251E) |
| Canvas / paper (light ground, dark text) | `#F7F7F4` | ![](https://img.shields.io/badge/_-F7F7F4?style=flat-square&labelColor=F7F7F4) |
| Ground (dark canvas) | `#121212` | ![](https://img.shields.io/badge/_-121212?style=flat-square&labelColor=121212) |
| White (dark primary, actions only) | `#FFFFFF` | ![](https://img.shields.io/badge/_-FFFFFF?style=flat-square&labelColor=FFFFFF) |
| Foreground, dark | `#F0F0F0` | ![](https://img.shields.io/badge/_-F0F0F0?style=flat-square&labelColor=F0F0F0) |
| Card, light | `#FFFFFF` | ![](https://img.shields.io/badge/_-FFFFFF?style=flat-square&labelColor=FFFFFF) |
| Card, dark | `#1F1F1F` | ![](https://img.shields.io/badge/_-1F1F1F?style=flat-square&labelColor=1F1F1F) |
| Sidebar, dark | `#171717` | ![](https://img.shields.io/badge/_-171717?style=flat-square&labelColor=171717) |
| Hover / selected, dark | `#292929` | ![](https://img.shields.io/badge/_-292929?style=flat-square&labelColor=292929) |
| User bubble, dark | `#2E2E2E` | ![](https://img.shields.io/badge/_-2E2E2E?style=flat-square&labelColor=2E2E2E) |
| Hairline, light | `#DFDDD5` | ![](https://img.shields.io/badge/_-DFDDD5?style=flat-square&labelColor=DFDDD5) |
| Hairline, dark | `#2B2B2B` | ![](https://img.shields.io/badge/_-2B2B2B?style=flat-square&labelColor=2B2B2B) |
| Muted text, light | `#66635A` | ![](https://img.shields.io/badge/_-66635A?style=flat-square&labelColor=66635A) |
| Muted text, dark | `#A3A3A3` | ![](https://img.shields.io/badge/_-A3A3A3?style=flat-square&labelColor=A3A3A3) |
| Destructive, light | `#BE1744` | ![](https://img.shields.io/badge/_-BE1744?style=flat-square&labelColor=BE1744) |

Surfaces, rims and ink are neutral. Colour is spent only where it carries information:
provider logos in their real colours, the sixteen ANSI slots, and the five colour jobs
below (signal, life, fault, degraded, info) plus coloured diffs. Everything else is ink
and paper.

These are the exact tokens from the desktop app
(`jarvis/ui/web/frontend/src/index.css`). The README, the product, and any brand asset
must stay on the same values so nothing drifts.

### Rules

- **Full white is for actions.** On the dark ground, white is the primary. Spend it on
  something the reader can press, not on an indicator that is merely reporting a fact.
- **Dark is graphite, not a void and not inverted cream.** Light mode is the Design.md
  paper (`#F7F7F4` canvas, warm ink). Dark mode does not flip that page: greys are
  `r = g = b`, the room is `#121212` (never `#0A0A0A` — that read as a skeleton), body
  text is `#F0F0F0` not cream, muted is `#A3A3A3` not taupe, every card has a visible
  hairline, and chat bubbles are quiet fills (`#1F1F1F` in, `#2E2E2E` out).
- **Both modes, always.** A colour comes from a theme token or from the per-appearance
  tables in `terminalThemes.ts`. Never hardcode one mode's value.
- **One signal hue, five colour jobs, no brand hue.** Surfaces and rims stay neutral. Colour
  appears only where it means something: `signal` blue (`#81A1C1` dark / `#2778C1` light)
  for links, focus and the active row's bar; `life` green, `fault` rose, `degraded` amber and
  `info` cyan for state; green/rose grounds under diffs. All of it on Cursor's desaturated
  values so the set reads as one family. The cyan and magenta in the wordmark are a *glitch
  artifact*, never UI colours. **Gigi** is black-and-white: a black body, white eyes and
  outline, no disc behind it.
- **Rasters convert on max(r, g, b), the orb on luma.** That is what keeps a mark legible
  after it loses its colour.

## Typography

| Use | Typeface | Notes |
|---|---|---|
| Display / wordmark | **Space Grotesk** (700) | Uppercase, tight tracking (`-4 to -5px` at hero size) |
| Body / UI | **Inter Variable** | The product UI font, −0.01 em tracking at interface sizes |
| Code / mono / tagline | **JetBrains Mono** (500) | Letter-spaced caps for taglines and labels |

All three ship inside the app bundle (`@fontsource` packages, SIL OFL 1.1); nothing is
fetched from a remote host.

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
