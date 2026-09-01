---
version: 3.0
name: Personal Jarvis
description: >-
  The design system for the Personal Jarvis DESKTOP APP — not a marketing site.
  Graphite, not void: a near-black room whose surfaces have edges you can see,
  whose ink is bright enough to read at a glance, and whose type ships with the
  app. Black-and-white by conviction — colour has exactly three jobs (life,
  identity, fault) and appears nowhere else — but never grey-on-black. Version
  2.0 (the "black room", 2026-09-01 afternoon) was rejected by the maintainer
  the same evening as "dead, like a skeleton", "a funeral", "you cannot
  recognise the type". This version keeps its structure and replaces its
  values, re-measured against the live references that evening. Light mode is
  the same system with the value scale inverted, never an afterthought.
sources:
  maintainer-verdict-2026-09-01: >-
    On the 2.0 build: black-and-white is wanted, but this one looks dead — a
    skeleton, a funeral. Other black-and-white products (Cursor, Grok, Bolt,
    the frontier labs) look like the top of the field. The type is not
    recognisable. Not sloppy — professional.
  measured-2026-09-01-evening:
    grok-web: >-
      Stage #181716 (WARM neutral, r>g>b by one step), rail #131211, composer
      #201F1D, hairlines white at 6–10 % alpha, radius 12 px and pill, composer
      24 px. Ink: #FCFCFC primary, #9E9E9E meta, #858585 faint. universalSans
      14/21, weights 400 / 500 / 550 — nav labels at 500. Smallest type 13 px.
    chatgpt-web: >-
      Page #000000, rail #131313, selected row and composer #212121, hairlines
      white at 15–20 % alpha, radius 8–10 px, composer 28 px. Ink: #FFFFFF
      primary, #AFAFAF meta. 14/20 throughout, weight 400; 12 px only on one
      plan label.
    cursor-app: >-
      Stage #141414 (70.5 % of pixels), rail #181818, composer #212121, active
      row #252525, borders #313131–#333333. Ink ceiling #F0F0F0. 90.4 % of the
      window in two values, 0.19 % bright pixels, 0.06 % saturated.
    what-we-took: >-
      From all three: hairlines you can actually see, meta ink at #9E–#AF
      rather than #94, body ink at #F0–#FC rather than #E6, and a room that is
      dark without being a hole. From Grok: nav labels at 500 and a composer
      with both a fill AND a rim. From Cursor: the ink ceiling below pure
      white, emptiness as composition. From ChatGPT: one type size for almost
      everything.
  why-2-0-failed: >-
    Every 2.0 value was individually defensible and the result was dead
    anyway, because three of them compounded: a 4 % room, a 14 % hairline
    (invisible on it) and 58 % meta ink (dim on it). Grey text on invisible
    boxes on black IS a skeleton. Add a remote font that a WebView often fails
    to load, and the type is "not recognisable" in the literal sense: a
    different face, width and rhythm on every cold start.

colors:
  # --- Dark: the product default. Graphite, edged, brightly inked. -----------
  dark-room: "#121212"
  dark-rail: "#171717"
  dark-object: "#1F1F1F"
  dark-lift: "#292929"
  dark-speaker: "#2E2E2E"
  dark-float: "#303030"
  dark-rim: "#2B2B2B"
  dark-rim-strong: "#404040"
  dark-ink-strong: "#FAFAFA"
  dark-ink: "#F0F0F0"
  dark-ink-meta: "#A3A3A3"
  dark-ink-faint: "#7A7A7A"
  dark-fill: "#FFFFFF"
  dark-on-fill: "#121212"

  # --- Light: the same roles, scale inverted. Warm paper, warm ink. ---------
  light-room: "#F7F7F4"
  light-rail: "#F1F0EA"
  light-object: "#FFFFFF"
  light-lift: "#E6E5E0"
  light-speaker: "#E6E5E0"
  light-float: "#FFFFFF"
  light-rim: "#DFDDD5"
  light-rim-strong: "#BDBAB0"
  light-ink-strong: "#171610"
  light-ink: "#26251E"
  light-ink-meta: "#66635A"
  light-ink-faint: "#8B877C"
  light-fill: "#26251E"
  light-on-fill: "#F7F7F4"

  # --- Status. The ONLY hues in the product, identical in both themes. ------
  life: "#2DBE7E"
  fault: "#E8574C"
  degraded: "#D6A94A"

typography:
  display:
    fontFamily: "'Space Grotesk', 'Inter Variable', system-ui, sans-serif"
    fontSize: 24px
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: -0.02em
  page:
    fontFamily: "'Inter Variable', 'Inter', system-ui, sans-serif"
    fontSize: 20px
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: -0.015em
  title:
    fontFamily: "'Inter Variable', 'Inter', system-ui, sans-serif"
    fontSize: 15px
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: -0.01em
  reading:
    fontFamily: "'Inter Variable', 'Inter', system-ui, sans-serif"
    fontSize: 15px
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: 0
  body:
    fontFamily: "'Inter Variable', 'Inter', system-ui, sans-serif"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: -0.01em
  label:
    fontFamily: "'Inter Variable', 'Inter', system-ui, sans-serif"
    fontSize: 14px
    fontWeight: 500
    lineHeight: 1.5
    letterSpacing: -0.01em
  meta:
    fontFamily: "'Inter Variable', 'Inter', system-ui, sans-serif"
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: -0.01em
  micro:
    fontFamily: "'Inter Variable', 'Inter', system-ui, sans-serif"
    fontSize: 11px
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: 0.01em
  numeral:
    fontFamily: "'Space Grotesk', 'Inter Variable', sans-serif"
    fontSize: 24px
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: -0.03em
    fontVariantNumeric: tabular-nums
  code:
    fontFamily: "'JetBrains Mono', ui-monospace, Consolas, monospace"
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: 0

rounded:
  none: 0px
  control: 8px
  surface: 12px
  feature: 16px
  pill: 9999px

spacing:
  row: 8px
  stack: 12px
  block: 20px
  group: 32px
  page: 28px

measure:
  reading: 720px
  form: 640px
  page: 1080px

elevation:
  flat: none
  rim: "inset 0 1px 0 rgb(var(--sheen-rgb) / 0.07)"
  float: "0 12px 32px -8px rgb(var(--scrim-rgb) / 0.55), 0 0 0 1px hsl(var(--border-strong))"

components:
  app-room:
    backgroundColor: "{colors.dark-room}"
    textColor: "{colors.dark-ink}"
    typography: "{typography.body}"
    padding: 0
  nav-rail:
    backgroundColor: "{colors.dark-rail}"
    textColor: "{colors.dark-ink}"
    typography: "{typography.label}"
    padding: "{spacing.row}"
    border-right: "1px solid {colors.dark-rim}"
  nav-row:
    backgroundColor: transparent
    textColor: "{colors.dark-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
    height: 40px
  nav-row-hover:
    backgroundColor: "{colors.dark-object}"
    textColor: "{colors.dark-ink}"
    rounded: "{rounded.control}"
  nav-row-selected:
    backgroundColor: "{colors.dark-lift}"
    textColor: "{colors.dark-ink-strong}"
    rounded: "{rounded.control}"
  card:
    backgroundColor: "{colors.dark-object}"
    textColor: "{colors.dark-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.surface}"
    padding: "{spacing.block}"
    border: "1px solid {colors.dark-rim}"
    elevation: "{elevation.rim}"
  list-row:
    backgroundColor: transparent
    textColor: "{colors.dark-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "12px 14px"
  list-row-hover:
    backgroundColor: "{colors.dark-object}"
    rounded: "{rounded.control}"
  list-row-selected:
    backgroundColor: "{colors.dark-lift}"
    textColor: "{colors.dark-ink-strong}"
    rounded: "{rounded.control}"
  tile:
    backgroundColor: "{colors.dark-lift}"
    textColor: "{colors.dark-ink}"
    typography: "{typography.numeral}"
    rounded: "{rounded.control}"
    padding: "16px"
  bubble-assistant:
    backgroundColor: "{colors.dark-object}"
    textColor: "{colors.dark-ink}"
    typography: "{typography.reading}"
    rounded: "{rounded.surface}"
    padding: "14px 18px"
  bubble-user:
    backgroundColor: "{colors.dark-speaker}"
    textColor: "{colors.dark-ink-strong}"
    typography: "{typography.reading}"
    rounded: "{rounded.surface}"
    padding: "14px 18px"
  composer:
    backgroundColor: "{colors.dark-object}"
    textColor: "{colors.dark-ink}"
    typography: "{typography.reading}"
    rounded: "{rounded.feature}"
    padding: "14px 16px"
    border: "1px solid {colors.dark-rim-strong}"
    elevation: "{elevation.rim}"
  input:
    backgroundColor: "{colors.dark-lift}"
    textColor: "{colors.dark-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "9px 12px"
    height: 36px
  button-primary:
    backgroundColor: "{colors.dark-fill}"
    textColor: "{colors.dark-on-fill}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "8px 16px"
    height: 36px
  button-secondary:
    backgroundColor: "{colors.dark-lift}"
    textColor: "{colors.dark-ink}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "8px 16px"
    height: 36px
  button-ghost:
    backgroundColor: transparent
    textColor: "{colors.dark-ink-meta}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "8px 12px"
    height: 36px
  chip:
    backgroundColor: "{colors.dark-lift}"
    textColor: "{colors.dark-ink-meta}"
    typography: "{typography.meta}"
    rounded: "{rounded.pill}"
    padding: "3px 10px"
  identity-avatar:
    backgroundColor: derived-from-name
    textColor: "{colors.dark-ink-strong}"
    typography: "{typography.title}"
    rounded: "{rounded.pill}"
    size: 36px
  status-dot-live:
    backgroundColor: "{colors.life}"
    rounded: "{rounded.pill}"
    size: 8px
  status-dot-fault:
    backgroundColor: "{colors.fault}"
    rounded: "{rounded.pill}"
    size: 8px
  status-dot-idle:
    backgroundColor: "{colors.dark-ink-faint}"
    rounded: "{rounded.pill}"
    size: 8px
  popover:
    backgroundColor: "{colors.dark-float}"
    textColor: "{colors.dark-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.surface}"
    padding: "{spacing.row}"
    elevation: "{elevation.float}"
  table-head:
    backgroundColor: "{colors.dark-lift}"
    textColor: "{colors.dark-ink-meta}"
    typography: "{typography.meta}"
    padding: "10px 14px"
  section-header:
    backgroundColor: transparent
    textColor: "{colors.dark-ink-strong}"
    typography: "{typography.page}"
    padding: "{spacing.page}"
  empty-state:
    backgroundColor: "{colors.dark-object}"
    textColor: "{colors.dark-ink-meta}"
    typography: "{typography.body}"
    rounded: "{rounded.surface}"
    padding: "40px {spacing.block}"
    border: "1px solid {colors.dark-rim}"
  skeleton:
    backgroundColor: "rgb(var(--sheen-rgb) / 0.07)"
    rounded: "{rounded.control}"
  selection:
    backgroundColor: "rgb(var(--sheen-rgb) / 0.22)"
    textColor: "{colors.dark-ink-strong}"
---

## Overview

This document describes the **Personal Jarvis desktop application**. It is the
third version in one day, and the reason is worth keeping on record.

Version 2.0 measured two reference products and derived a "black room": a 4 %
ground, surfaces lifted only when content-sized, rims kept almost invisible,
ink capped at 90 %. Every number was defensible. The maintainer's verdict on
the built result was immediate: *dead, like a skeleton; a funeral; the type is
not recognisable*. The verdict was correct. Three quiet values compounded into
one loud failure: a near-black page, hairlines the eye cannot find on it, and
meta ink too dim to read on it. Grey text on invisible boxes on black **is** a
skeleton — an outline where an object should be.

Version 3.0 keeps 2.0's structure (the surface ladder, the four inks, the three
status hues, the seven-step scale) and changes what it was wrong about:

- **Graphite, not void.** The room is `#121212`, not `#0A0A0A`. Cursor's stage
  is `#141414`, Grok's `#181716`; neither product is a hole.
- **Edges exist.** Every rim is tuned to be visible on the surface it encloses.
  A card has a fill *and* a hairline. A composer has a fill *and* a strong rim.
  The 2.0 rule "no border on something that already has a fill" is withdrawn —
  it was the single largest contributor to the skeleton.
- **Ink is bright.** Body at `#F0F0F0` (Cursor's ceiling), meta at `#A3A3A3`
  (between Grok's `#9E` and ChatGPT's `#AF`). Nothing informational sits
  below 64 %.
- **Type ships with the app.** Inter Variable, JetBrains Mono and Space
  Grotesk are bundled. A WebView that starts offline used to render the whole
  product in Segoe UI — a different face, width and rhythm on every cold
  start, which is what "not recognisable" meant in the literal sense.
- **Nothing below 11 px.** The 271 remaining `text-[7–10px]` sites were swept
  to the `micro` step in the same change.

Still black-and-white. Still neutral greys (`r = g = b` in dark, warm paper in
light). Still exactly three hues. The conviction was never the problem; the
values were.

## Colors

### The surface ladder

Six roles. Their brightness inverts between themes; their **role never
changes**. Nothing in the product invents a seventh surface, and no surface is
produced by multiplying a token by an opacity.

| Role | Job | Dark | Light |
|---|---|---|---|
| `room` | The page and every full-bleed region. Most of the window. | `#121212` | `#F7F7F4` |
| `rail` | The navigation column and other standing chrome. Edged with `rim`. | `#171717` | `#F1F0EA` |
| `object` | A card, bubble, composer, panel or hovered row. **Content-sized only.** | `#1F1F1F` | `#FFFFFF` |
| `lift` | The answer to a pointer: hover on an object, a selected row, a field, a tile. | `#292929` | `#E6E5E0` |
| `speaker` | The user's own turn in a conversation. A quiet fill with strong ink — not a slab. | `#2E2E2E` | `#E6E5E0` |
| `float` | Menus, dialogs, tooltips — surfaces that leave the plane. | `#303030` | `#FFFFFF` + float shadow |

Steps are 5–7 byte-values apart at the bottom of the ladder, which is the
smallest distance that survives an uncalibrated monitor. Lift still scales
inversely with area: a surface wider than ~720 px stays at `room` or `rail`.

The 2.0 `speaker` at `#4D4D4D` read as a tombstone — the loudest rectangle on
the screen carrying the one thing the reader already knows. ChatGPT's user
bubble sits a single step above its page. So does ours now; the ink carries
the emphasis.

### Rims

Two values, and both must be **visible on the surface they enclose**. A rim
the eye cannot find is not restraint; it is a missing edge.

| Token | Job | Dark | Light | Visible on |
|---|---|---|---|---|
| `rim` | Card edges, dividers, table rules, the rail's edge. | `#2B2B2B` | `#DFDDD5` | room, rail and object |
| `rim-strong` | Composer outline, floating layers, focus rings, scrollbar thumbs. | `#404040` | `#BDBAB0` | everything up to `float` |

The references draw hairlines as white at 8–20 % alpha over their ground.
`#2B2B2B` on `#121212` is white at 10 %; `#404040` is white at 19 %. That is
the measured band, not a taste.

**A fill and a rim together are the normal case.** Grok's composer is
`#201F1D` inside a 10 % white hairline; ChatGPT's is `#212121` inside 15 %.
An object with a fill and no edge sinks; an outline with no fill is a
wireframe. Both are what "skeleton" looks like.

### Ink

Four steps, each with one job.

| Token | Job | Dark | Light | On `object` |
|---|---|---|---|---|
| `ink-strong` | Headings, headline numbers, the selected row's label. | `#FAFAFA` | `#171610` | 15.9 : 1 |
| `ink` | Body, labels, running text, list titles. | `#F0F0F0` | `#26251E` | 14.5 : 1 |
| `ink-meta` | Timestamps, secondary lines, table heads, captions. | `#A3A3A3` | `#66635A` | 6.6 : 1 |
| `ink-faint` | Placeholders and disabled text only. Never information. | `#7A7A7A` | `#8B877C` | 3.7 : 1 |

`fill` (`#FFFFFF` dark, `#26251E` light) is the accent. It paints buttons,
marks, active indicators and focus rings. **It never paints text, a byline, or
a decorative icon.**

The ceiling stays below white: `#F0F0F0` is what Cursor's brightest text
measures. But the floor for anything a person is meant to read is now 64 %,
up from 58 %, and the placeholder step is 48 %, up from 44 %. Small numbers;
the difference between "meta" and "faded".

### Status — the only hue in the product

Three colours, identical in both themes because a status must not change
meaning with the theme. Everything else in the interface is neutral.

| Token | Meaning | Value |
|---|---|---|
| `life` | Running, live, connected, on, passed. | `#2DBE7E` |
| `fault` | Failed, blocked, disconnected, error. | `#E8574C` |
| `degraded` | Stale, partial, needs attention. | `#D6A94A` |

A status may **never** be encoded as `ink` or `fill`. A status ramp may
**never** render "ok" dimmer than "unknown". A success state must actually
use `life` — four green rings are most of what makes Grok's window read as
running.

### Identity

Every conversation, agent, provider and person carries a **coloured identity
mark**: a real vendor logo where one exists (kept in full colour — the
documented exception to the neutral rule), otherwise a round avatar whose hue
is derived deterministically from the name. Never a grey placeholder, never a
monogram on a neutral disc. Identity hues sit outside the status palette and
are never read as state.

### Selection

Dragging across text answers in the theme's own material — `sheen` at 22 % —
never the engine's default blue. It was the one saturated slab a
black-and-white product showed every time someone selected a sentence.

## Typography

### Families

| Role | Family | Ships as |
|---|---|---|
| Interface | **Inter Variable** | `@fontsource-variable/inter`, one woff2 per script subset, every weight |
| Display | **Space Grotesk** | 500 / 600 / 700 static cuts. Section titles and headline numbers only. |
| Code | **JetBrains Mono** | 400 / 500 / 600 / 700 static cuts. Every code, terminal, path and transcript surface. |

All three are bundled by Vite and fingerprinted into `dist/assets`. There is
no remote font request anywhere in the product. Licences are SIL OFL 1.1 and
are listed in `public/THIRD_PARTY_NOTICES.txt`.

Inter is set with `cv02 cv03 cv04 cv11` (open shapes, single-storey a) and
**−0.01 em tracking at interface sizes** — Inter's own recommendation, and
what both Grok (−0.2 px at 14 px) and Cursor set. `reading` and `code` reset
tracking to 0 through their own declarations.

### The scale

Seven steps plus one weight variant. There is nothing between them and
nothing below them.

| Token | Size / Weight | Line height | Use |
|---|---|---|---|
| `display` | 24 / 600 | 1.2 | Section title, headline number |
| `page` | 20 / 600 | 1.3 | Page heading |
| `title` | 15 / 600 | 1.4 | Card title, list-row title, group label |
| `reading` | 15 / 400 | 1.6 | Chat, transcripts, documents, prose |
| `body` | 14 / 400 | 1.5 | Default interface text, controls |
| `label` | 14 / 500 | 1.5 | **Navigation rows and buttons.** The weight every reference sets its nav in. |
| `meta` | 13 / 400 | 1.45 | Timestamps, captions, secondary lines |
| `micro` | 11 / 500 | 1.4 | Badges and dense table cells. **Hard floor.** |

**11 px is the floor and it is now enforced by the tree, not the document.**
No `text-[10px]`, `text-[9px]`, `text-[8px]` survives in `src/`. ChatGPT's
smallest type is 12 px; Grok's is 13 px.

### Principles

- **Weight is hierarchy.** 400 for reading and meta, 500 for anything a
  person navigates or presses, 600 for titles. A navigation column set
  entirely in 400 grey is a list of ghosts.
- **No tiny all-caps labels** outside a badge.
- **Line height comes from the scale**, never from a `leading-*` class.
- **Tabular numerals wherever digits align.**

## Layout

Four named spacing steps and three measures, unchanged from 2.0:

| Token | Value | Use |
|---|---|---|
| `row` | 8px | Inside a row: icon to label, chip to chip |
| `stack` | 12px | Between siblings in a list or form |
| `block` | 20px | Card padding, between related blocks |
| `group` | 32px | Between groups in a section |
| `page` | 28px | Section outer padding |

| Measure | Value | Use |
|---|---|---|
| `reading` | 720px | Chat, transcripts, prose |
| `form` | 640px | Settings groups, single-column forms |
| `page` | 1080px | Dashboards, tables, card grids |

**A section is designed by subtraction.** Remove every wrapper that only
holds one other thing, every label that repeats its value, every count nobody
acts on. The target is ≥ 60 % of a section's pixels at `room` or `rail` —
but what remains must have edges.

## Elevation

Three levels. Depth is carried by fill and rim; shadow only when floating.

| Level | Treatment | Use |
|---|---|---|
| Flat | No shadow. Its own surface token. | Rows, tiles, chips — everything in the plane |
| Rim | `inset 0 1px 0` sheen at 7 % **plus a `rim` hairline** | Cards, bubbles, composers. A top edge catching light and a drawn edge. |
| Float | Real shadow + `rim-strong` ring | Menus, dialogs, tooltips only |

No decorative glows. A bloom is additive light standing in for a fill that
should have been there.

## Shapes

| Token | Value | Use |
|---|---|---|
| `control` | 8px | Rows, buttons, inputs, tiles, menu items |
| `surface` | 12px | Cards, bubbles, panes, popovers |
| `feature` | 16px | Composers and large feature cards |
| `pill` | 9999px | Chips, badges, avatars, status dots |

The composer moves up to `feature`: both references round their composer
more than anything else on screen (Grok 24 px, ChatGPT 28 px), and a 16 px
radius inside a 720 px measure is the restrained version of that.

**The theme never changes an element's shape** — only its value.

## Interaction

One ladder, one recipe. It only ever goes up.

| State | Treatment |
|---|---|
| Rest | The element's own surface token |
| Hover | One step up the ladder (`object` → `lift`) |
| Selected | `lift` plus `ink-strong` on the label |
| Focus | 2px `rim-strong` ring. Never a third fill. |
| Pressed | `scale(0.98)`, 120ms |

Selection is a full-width rounded fill on the whole row, inset from the
column edge. Scrollbar thumbs are `rim-strong`; a thumb at `rim` was a thumb
nobody could find. Transitions are 120 ms on fills and 160 ms on transforms.
Everything respects `prefers-reduced-motion`.

## Nesting

A child surface **steps up, never down**. It is a hard error for an element
to render darker than its parent in dark mode.

| Parent | A nested well, tile, field or table head takes |
|---|---|
| `room` | `rail` or `object` |
| `rail` | `object` |
| `object` | `lift` |
| `lift` | `float`, or no surface at all |

When the ladder runs out, stop nesting.

## States

Every section owns four states, and all four are designed.

- **Loading** renders the real container at its real height with skeleton
  bars at `sheen` 7 %. Never a centred grey word in a void, never invented
  zeros.
- **Empty** is a designed surface — `object` with a `rim` — with one sentence
  saying what will appear here and one action that makes it appear.
- **Error** says what failed and what to do. It uses `fault` on a normal
  surface, never a red-washed panel.
- **Populated** is the state everything else in this document describes.

## Do's and Don'ts

### Do

- Give every object a fill **and** an edge. Fill first, rim second, shadow
  only when floating.
- Keep the room at `room`. Lift only what is sized to its content.
- Set navigation and buttons in `label` (500), not `body` (400).
- Spend `life` green on anything that is actually running.
- Cap ink below white and reserve `fill` for fills.
- Bound the measure. Every screen, every time.
- Remove before you restyle — and check what is left still has edges.

### Don't

- Don't express depth with opacity. `bg-card/40` as a resting ground is a
  defect; depth is a named token.
- Don't write a literal colour anywhere except `terminalThemes.ts`, the one
  sanctioned exception (xterm needs resolved strings).
- Don't tune a rim until it disappears. If it cannot be seen on the surface
  it encloses, it is not a rim.
- Don't set a status in `ink` or `fill`, and never let "ok" be quieter than
  "unknown".
- Don't use tiny uppercase labels. Don't go below 11 px.
- Don't add a shadow to anything that is not floating.
- Don't fetch a font from a remote host. Ever.

## Light mode

Light is the same system read the other way: warm paper (`#F7F7F4`) and warm
near-black ink (`#26251E`). Objects rise toward white, interaction answers
downward into warm grey, rims darken instead of lightening, and `fill`
becomes ink on paper. The 3.0 changes on paper are the same changes as on
graphite: `rim` down to `#DFDDD5` so it reads on white cards, `ink-meta`
down to `#66635A` (6.6 : 1 on paper), and the user bubble becomes a quiet
`lift` fill with strong ink rather than a black slab.

The three status hues do **not** change between themes.

## The Agentic IDE panes

The pane family (terminal shells, chat stage, rail, toolbar) reads the same
ladder re-derived per appearance in `terminalThemes.ts`, because a light pane
inside a dark app is a supported combination and xterm cannot read a CSS
token. The values there are this document's values: dark shell over `#121212`,
rims at white 12 %, float `#303030`, ink `#F0F0F0` / `#A3A3A3` / `#7A7A7A`.
The 16 ANSI slots keep their hues — a terminal's palette is one of the few
places colour legitimately survives.

## Enforcement

These rules are checked, not remembered. Each has a gate in `scripts/ci/`
alongside the existing German and private-key gates.

| Gate | Fails on |
|---|---|
| No literal colour | A hex, `rgb()`, or Tailwind palette class in `.tsx`, `.ts` or `.css` outside `terminalThemes.ts` |
| No opacity hierarchy | `bg-(card\|background\|muted)/[0-9]` or `bg-sheen/` used as a resting fill |
| No negative hover | `hover:bg-background/` |
| Type floor | `text-[10px]` or smaller |
| No admin caps | `uppercase` in `views/` outside a badge |
| No remote fonts | `fonts.googleapis.com` or any `@import url(http` in `.css` |

## Known gaps

- Motion is specified only as durations. A typing indicator, a streaming
  cursor and a run-in-progress pulse are still not designed; "dead" for an
  interactive app is partly that nothing moves.
- The navigation column lists ~20 sections at one weight and one indent.
  This version makes them legible; grouping them is a product decision the
  theme cannot make.
- Icon size and stroke weight are not yet audited across the view tree.
- The wallpaper feature's readability floors were derived on the 2.0 ladder
  and have not been re-tuned.
