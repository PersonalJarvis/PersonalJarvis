# Brand Guidelines

The visual language of Personal Jarvis: a neutral, cool-black interface with one
restrained accent, and a distressed wordmark. The goal is *confident and engineered*,
never noisy or playful.

<p align="center">
  <img src="../assets/brand/banner.png" alt="Personal Jarvis wordmark" width="720" />
</p>

## Color

Pure neutral in both modes — every surface, rim and ink is `r = g = b`. Elevation is a
surface colour one step lighter than the one under it (canvas → sidebar → card → popover),
never a shadow. One accent (blue) marks links, focus rings, the active nav row, selected
states, progress bars and informational status. Green, amber and red are status only.

| Role | Dark | Light |
|---|---|---|
| Canvas (`--background`) | `#0A0A0A` ![](https://img.shields.io/badge/_-0A0A0A?style=flat-square&labelColor=0A0A0A) | `#FFFFFF` ![](https://img.shields.io/badge/_-FFFFFF?style=flat-square&labelColor=FFFFFF) |
| Sidebar (`--sidebar`) | `#0F0F0F` ![](https://img.shields.io/badge/_-0F0F0F?style=flat-square&labelColor=0F0F0F) | `#FAFAFA` ![](https://img.shields.io/badge/_-FAFAFA?style=flat-square&labelColor=FAFAFA) |
| Card (`--card`) | `#171717` ![](https://img.shields.io/badge/_-171717?style=flat-square&labelColor=171717) | `#FFFFFF` ![](https://img.shields.io/badge/_-FFFFFF?style=flat-square&labelColor=FFFFFF) |
| Popover (`--popover`) | `#1C1C1C` ![](https://img.shields.io/badge/_-1C1C1C?style=flat-square&labelColor=1C1C1C) | `#FFFFFF` ![](https://img.shields.io/badge/_-FFFFFF?style=flat-square&labelColor=FFFFFF) |
| Hover / input (`--secondary`, `--muted`, `--input`) | `#1F1F1F` ![](https://img.shields.io/badge/_-1F1F1F?style=flat-square&labelColor=1F1F1F) | `#F5F5F5` ![](https://img.shields.io/badge/_-F5F5F5?style=flat-square&labelColor=F5F5F5) |
| Raised (`--surface-raised`) | `#262626` ![](https://img.shields.io/badge/_-262626?style=flat-square&labelColor=262626) | `#EDEDED` ![](https://img.shields.io/badge/_-EDEDED?style=flat-square&labelColor=EDEDED) |
| Hairline (`--border`) | `#262626` ![](https://img.shields.io/badge/_-262626?style=flat-square&labelColor=262626) | `#E5E5E5` ![](https://img.shields.io/badge/_-E5E5E5?style=flat-square&labelColor=E5E5E5) |
| Strong rim (`--border-strong`) | `#383838` ![](https://img.shields.io/badge/_-383838?style=flat-square&labelColor=383838) | `#D1D1D1` ![](https://img.shields.io/badge/_-D1D1D1?style=flat-square&labelColor=D1D1D1) |
| Foreground (`--foreground`) | `#FAFAFA` ![](https://img.shields.io/badge/_-FAFAFA?style=flat-square&labelColor=FAFAFA) | `#171717` ![](https://img.shields.io/badge/_-171717?style=flat-square&labelColor=171717) |
| Secondary ink (`--foreground-secondary`) | `#B8B8B8` ![](https://img.shields.io/badge/_-B8B8B8?style=flat-square&labelColor=B8B8B8) | `#525252` ![](https://img.shields.io/badge/_-525252?style=flat-square&labelColor=525252) |
| Muted ink (`--muted-foreground`) | `#A1A1A1` ![](https://img.shields.io/badge/_-A1A1A1?style=flat-square&labelColor=A1A1A1) | `#666666` ![](https://img.shields.io/badge/_-666666?style=flat-square&labelColor=666666) |
| Faint ink (`--foreground-faint`) | `#737373` ![](https://img.shields.io/badge/_-737373?style=flat-square&labelColor=737373) | `#999999` ![](https://img.shields.io/badge/_-999999?style=flat-square&labelColor=999999) |
| Primary fill (`--primary`) | `#FAFAFA` ![](https://img.shields.io/badge/_-FAFAFA?style=flat-square&labelColor=FAFAFA) | `#171717` ![](https://img.shields.io/badge/_-171717?style=flat-square&labelColor=171717) |
| Accent (`--accent`) | `#3D8BFF` ![](https://img.shields.io/badge/_-3D8BFF?style=flat-square&labelColor=3D8BFF) | `#0A6FF5` ![](https://img.shields.io/badge/_-0A6FF5?style=flat-square&labelColor=0A6FF5) |
| Success (`--success`) | `#22C35E` ![](https://img.shields.io/badge/_-22C35E?style=flat-square&labelColor=22C35E) | `#15803D` ![](https://img.shields.io/badge/_-15803D?style=flat-square&labelColor=15803D) |
| Warning (`--warning`) | `#F59E0B` ![](https://img.shields.io/badge/_-F59E0B?style=flat-square&labelColor=F59E0B) | `#BD5E05` ![](https://img.shields.io/badge/_-BD5E05?style=flat-square&labelColor=BD5E05) |
| Destructive (`--destructive`) | `#DC2828` ![](https://img.shields.io/badge/_-DC2828?style=flat-square&labelColor=DC2828) | `#C52020` ![](https://img.shields.io/badge/_-C52020?style=flat-square&labelColor=C52020) |

These are the exact tokens from the desktop app
(`jarvis/ui/web/frontend/src/index.css`). `jarvis/ui/theme.py` (`WINDOW_BACKGROUND`),
the boot splash tokens in `frontend/index.html` (`--jbs-*`), the README, and any brand
asset must stay on the same values so nothing drifts.

### Rules

- **Neutral, both modes.** Surfaces, rims and ink carry zero saturation. Dark is a
  cool black room (`#0A0A0A`) with objects one step lighter each; light is the same ladder
  inverted on white. No warm cast, no blue-black, no cream.
- **One accent.** Blue (`#3D8BFF` dark / `#0A6FF5` light) is the only hue that means
  "interactive or selected": links, focus rings, the active nav row's bar, selected rows
  (`--accent` at 12 % as the wash), progress bars, informational callouts. It never fills a
  primary button — that stays white on dark and black on light.
- **Status colours are for status.** Green for live dots and "running", amber for paused or
  quota, red for failed and destructive actions. Provider logos keep their real colours,
  the sixteen ANSI slots stay coloured, diffs stay green/red.
- **Elevation by surface, not shadow.** Shadows exist only on popovers, dialogs and the
  composer. No glows.
- **Both modes, always.** A colour comes from a theme token or from the per-appearance
  tables in `terminalThemes.ts`. Never hardcode one mode's value.
- **Gigi** is black-and-white: a black body, white eyes and outline, no disc behind it.
- **Rasters convert on max(r, g, b), the orb on luma.** That is what keeps a mark legible
  after it loses its colour.

## Typography

One family. Six sizes. Three weights.

| Use | Typeface | Notes |
|---|---|---|
| Interface and display | **Inter Variable** | 400 body, 500 labels / nav / buttons, 600 titles. Display text is the same family with `-0.02em` tracking. No 700 in UI text. |
| Code / mono / tagline | **JetBrains Mono** (500) | Letter-spaced caps for taglines and labels |

| Step | Size / line | Use |
|---|---|---|
| `text-xs` | 12 / 16 | badges, keyboard hints, table meta — the only 12 px |
| `text-sm` | 13 / 18 | dense table cells, chip labels, code |
| `text-base` | 14 / 20 | body, sidebar items, inputs, buttons |
| `text-lg` | 16 / 24 | card and list titles, composer text |
| `text-xl` | 20 / 28 | view titles |
| `text-2xl` | 28 / 34 | home greeting only |

Spacing sits on a 4 px grid. Radii: 4 px badges, 8 px buttons / inputs / chips / menu
items, 12 px cards / panels / dialogs, 16 px only the composer and hero cards, full only
avatars and status dots. No pill-shaped buttons or chips.

Both families ship inside the app bundle (`@fontsource` packages, SIL OFL 1.1); nothing is
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
