---
version: 2.0
name: Personal Jarvis
description: >-
  The design system for the Personal Jarvis DESKTOP APP — not a marketing site.
  A black room where surfaces are objects, not walls: the page stays near-black
  and untouched across most of its area, and lift is spent only on things sized
  to their content — a bubble, a row, a tile, a composer. Ink stops before pure
  white; white is a fill, never running text. Colour has exactly three jobs —
  life, identity, fault — and appears nowhere else. Derived by measuring two
  reference applications pixel by pixel (Grok Bot and Cursor, 2026-09-01) and
  reconciling them against the app's own two prior failures: opaque grey cards
  ("grey slabs") and near-invisible whisper cards ("dead"). Light mode is the
  same system with the value scale inverted, never an afterthought.
sources:
  measured-2026-09-01:
    grok-bot: >-
      Ground #070707 (40.3% of all pixels), rail #111111, assistant bubble
      #262626, composer #2F2F2F, selected row / search field #313131, user
      bubble #5A5A5A. Ink: placeholder #6E6E6E, meta #8A8A8A, body #E8E8E8,
      pure white ONLY on the identity mark. Rows ~72px, two-line. Chat text
      16px/1.55. Zero dividers in the conversation list. Colour: four saturated
      avatars, four green live rings, one violet app mark. Nothing else.
    cursor: >-
      Stage #141414 (70.5% of pixels), rail #181818 (19.9%), composer #212121,
      active row #252525, borders #313131–#333333. Ink ceiling #F0F0F0 — never
      white. 90.4% of the window sits in two values. 0.19% bright pixels, 0.06%
      saturated. Nav rows 32px, uniform. Hierarchy by indentation, not boxes.
    what-we-took: >-
      Grok's object language (generous rows, fill-based separation, coloured
      identity, green liveness) inside Cursor's restraint (emptiness as
      composition, rims lighter than the fills they enclose, ink capped below
      white, one uniform row height).

colors:
  # --- Dark: the product default. A black room with lit objects in it. -------
  dark-room: "#0A0A0A"
  dark-rail: "#121212"
  dark-object: "#212121"
  dark-lift: "#333333"
  dark-float: "#3D3D3D"
  dark-speaker: "#4D4D4D"
  dark-rim: "#242424"
  dark-rim-strong: "#424242"
  dark-ink-strong: "#F5F5F5"
  dark-ink: "#E6E6E6"
  dark-ink-meta: "#949494"
  dark-ink-faint: "#707070"
  dark-fill: "#FFFFFF"
  dark-on-fill: "#0A0A0A"

  # --- Light: the same roles, scale inverted. Warm paper, warm ink. ---------
  light-room: "#F7F7F4"
  light-rail: "#EFEEE8"
  light-object: "#FFFFFF"
  light-lift: "#E6E5E0"
  light-float: "#FFFFFF"
  light-speaker: "#26251E"
  light-rim: "#E2E1DA"
  light-rim-strong: "#C4C2B8"
  light-ink-strong: "#1A1914"
  light-ink: "#26251E"
  light-ink-meta: "#6E6B61"
  light-ink-faint: "#918D82"
  light-fill: "#26251E"
  light-on-fill: "#F7F7F4"

  # --- Status. The ONLY hues in the product, identical in both themes. ------
  life: "#2DBE7E"
  fault: "#E8574C"
  degraded: "#D6A94A"

typography:
  display:
    fontFamily: "'Space Grotesk', 'Archivo', system-ui, sans-serif"
    fontSize: 24px
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: -0.02em
  page:
    fontFamily: "'Inter', system-ui, 'Segoe UI', sans-serif"
    fontSize: 20px
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: -0.015em
  title:
    fontFamily: "'Inter', system-ui, sans-serif"
    fontSize: 15px
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: -0.005em
  reading:
    fontFamily: "'Inter', system-ui, sans-serif"
    fontSize: 15px
    fontWeight: 400
    lineHeight: 1.6
    letterSpacing: 0
  body:
    fontFamily: "'Inter', system-ui, sans-serif"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: 0
  meta:
    fontFamily: "'Inter', system-ui, sans-serif"
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: 0
  micro:
    fontFamily: "'Inter', system-ui, sans-serif"
    fontSize: 11px
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: 0.01em
  numeral:
    fontFamily: "'Space Grotesk', 'Inter', sans-serif"
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
  rim: "inset 0 1px 0 rgb(var(--sheen-rgb) / 0.05)"
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
    typography: "{typography.body}"
    padding: "{spacing.row}"
  nav-row:
    backgroundColor: transparent
    textColor: "{colors.dark-ink}"
    typography: "{typography.body}"
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
    rounded: "{rounded.surface}"
    padding: "14px 16px"
    border: "1px solid {colors.dark-rim-strong}"
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
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "8px 16px"
    height: 36px
  button-secondary:
    backgroundColor: "{colors.dark-lift}"
    textColor: "{colors.dark-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "8px 16px"
    height: 36px
  button-ghost:
    backgroundColor: transparent
    textColor: "{colors.dark-ink-meta}"
    typography: "{typography.body}"
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
  skeleton:
    backgroundColor: "rgb(var(--sheen-rgb) / 0.06)"
    rounded: "{rounded.control}"
---

## Overview

This document describes the **Personal Jarvis desktop application**. It replaces
a previous version that described Cursor's marketing website — warm cream canvas,
orange CTA, 80px editorial rhythm. None of that survived contact with a
near-black desktop tool, and following it is what produced the current defects.

The app is a **black room with lit objects in it**. Most of the window is
untouched near-black and stays that way. Lift — the act of making a surface
lighter than the room — is spent exclusively on things sized to their content: a
bubble, a row, a tile, a composer, a menu. A full-width region never rises.

This single rule is the resolution of two failed attempts on record. Lifting
everything produced opaque grey furniture on a black page ("grey slabs",
reverted). Refusing to lift anything produced surfaces three byte-values above
their ground, below the threshold at which an eye perceives an edge ("dead").
Both were attempts to find one correct value for a variable that should never
have been global.

**Key characteristics**

- Separation is **fill first**. A row exists because it is a lighter rectangle,
  not because a line was drawn around it. Grok Bot's conversation list contains
  zero dividers.
- A rim is **lighter** than the fill it encloses. On near-black, a dark border
  around a dark fill reads as a wireframe with nothing inside it.
- **Ink stops before white.** Body runs at 90%, headings at 96%. Cursor's
  brightest text measures #F0F0F0 and never exceeds it. Pure white is a *fill* —
  buttons, marks, focus rings — never running text.
- **Colour has three jobs**: life, fault, identity. Nothing else in the product
  carries hue. Both references are >99% neutral and still read as alive, because
  the fraction of a percent that is coloured is spent on exactly the right
  things.
- **Emptiness is composition.** Cursor holds 90.4% of its window in two values.
  A screen is finished when there is nothing left to remove, not when the space
  is filled.
- Light mode is the same system with the scale inverted. Every token has a role,
  not a brightness, and the role holds in both themes.

## Colors

### The surface ladder

Six roles. Their brightness inverts between themes; their **role never changes**.
Nothing in the product invents a seventh surface, and no surface is produced by
multiplying a token by an opacity.

| Role | Job | Dark | Light |
|---|---|---|---|
| `room` | The page and every full-bleed region. Most of the window. | `#0A0A0A` | `#F7F7F4` |
| `rail` | The navigation column and other standing chrome. | `#121212` | `#EFEEE8` |
| `object` | A card, bubble, panel or hovered row. **Content-sized only.** | `#212121` | `#FFFFFF` |
| `lift` | The answer to a pointer: hover on an object, a selected row, a field, a tile. | `#333333` | `#E6E5E0` |
| `float` | Menus, dialogs, tooltips — surfaces that leave the plane. | `#3D3D3D` | `#FFFFFF` + float shadow |
| `speaker` | The user's own turn in a conversation. The one deliberately loud surface. | `#4D4D4D` | `#26251E` |

The dark ladder is calibrated against both references: Grok Bot's assistant
bubble sits at `#262626` and its selected row at `#313131`; Cursor's composer at
`#212121` and its active row at `#252525`. Our `object` at `#212121` and `lift`
at `#333333` sit inside that measured band with steps large enough to survive an
uncalibrated monitor.

In light mode the direction reverses and that is correct: an object rises toward
white, and interaction answers by going *down* into warm grey. The role is
"the surface that responds", not "the lighter surface".

### Rims

Two values, because one border cannot do two jobs on near-black.

| Token | Job | Dark | Light |
|---|---|---|---|
| `rim` | Structural hairlines, dividers, table rules. Barely noticeable. | `#242424` | `#E2E1DA` |
| `rim-strong` | Composer outline, floating layers, focus rings. | `#424242` | `#C4C2B8` |

**A rim is never the only thing describing an object.** If an element has a real
fill it usually needs no border at all. Cursor's `#313131` rims cover 0.2% of its
window — a composer outline and a few dividers, not every card.

### Ink

Four steps, each with one job. The split is what fixes "the white looks
artificial": one 97% slab across an entire app has no hierarchy to justify its
brightness, so the eye reads it as harsh rather than as important.

| Token | Job | Dark | Light | On `object` |
|---|---|---|---|---|
| `ink-strong` | Headings, headline numbers, the selected row's label. | `#F5F5F5` | `#1A1914` | 15.1 : 1 |
| `ink` | Body, labels, running text, list titles. | `#E6E6E6` | `#26251E` | 13.2 : 1 |
| `ink-meta` | Timestamps, secondary lines, table heads, captions. | `#949494` | `#6E6B61` | 5.2 : 1 |
| `ink-faint` | Placeholders and disabled text only. Never information. | `#707070` | `#918D82` | 3.0 : 1 |

`fill` (`#FFFFFF` dark, `#26251E` light) is the accent. It paints buttons, marks,
active indicators and focus rings. **It never paints text, a byline, or a
decorative icon.** An icon that is brighter than the heading it labels is a bug.

### Status — the only hue in the product

Three colours, identical in both themes because a status must not change meaning
with the theme. Everything else in the interface is neutral.

| Token | Meaning | Value |
|---|---|---|
| `life` | Running, live, connected, on, passed. | `#2DBE7E` |
| `fault` | Failed, blocked, disconnected, error. | `#E8574C` |
| `degraded` | Stale, partial, needs attention. | `#D6A94A` |

Three binding rules. A status may **never** be encoded as `ink` or `fill` — a
white "cancelled" chip becomes the loudest mark on a screen and inverts the
ramp. A status ramp may **never** render "ok" dimmer than "unknown". And a
success state must actually use `life`: it is the mechanism that makes Grok Bot
feel alive, and four green rings carry that window's entire "this is running"
message.

### Identity

The second thing that makes a black app feel alive, and the one this product
currently lacks entirely.

Every conversation, agent, provider and person carries a **coloured identity
mark**: a real vendor logo where one exists (kept in full colour — this is the
documented exception to the neutral rule), otherwise a 36px round avatar whose
hue is derived deterministically from the name. Never a grey placeholder, never a
monogram on a neutral disc.

Identity hues sit outside the status palette and are never read as state.

## Typography

### Families

| Role | Family | Notes |
|---|---|---|
| Interface | **Inter** | Everything. Loaded locally, never from a remote host. |
| Display | **Space Grotesk** | Section titles and headline numbers only. Used with restraint. |
| Code | **JetBrains Mono** | Every code, terminal, path and transcript surface. |

All three ship with the application. A remote font import means an offline
launch renders the entire product in a system fallback.

### The scale

Seven steps. There is nothing between them and nothing below them.

| Token | Size / Weight | Line height | Use |
|---|---|---|---|
| `display` | 24 / 600 | 1.2 | Section title, headline number |
| `page` | 20 / 600 | 1.3 | Page heading |
| `title` | 15 / 600 | 1.4 | Card title, list-row title, group label |
| `reading` | 15 / 400 | 1.6 | Chat, transcripts, documents, prose |
| `body` | 14 / 400 | 1.5 | Default interface text, nav rows, controls |
| `meta` | 13 / 400 | 1.45 | Timestamps, captions, secondary lines |
| `micro` | 11 / 500 | 1.4 | Badges and dense table cells. **Hard floor.** |

**11px is the floor.** No `text-[10px]`, `text-[9px]`, `text-[8px]`. Near-black
amplifies small type: a 10px grey glyph on `#0A0A0A` reads as a scratch, not as
a word.

`reading` exists as its own step because a reading surface and a navigation
surface are different registers. Grok Bot sets chat at 16px/1.55 against 14–15px
list text, and that difference is a large part of why its conversation feels
like a document rather than a table.

### Principles

- **Two weights in static content.** 400 for body, meta and labels; 600 for
  titles. Weight 500 is reserved for interactive affordances. When almost
  everything is 500, nothing recedes.
- **No tiny all-caps labels.** `uppercase` is banned in body and label
  typography. It is the specific construction that makes an interface read as an
  admin panel, and neither reference screenshot contains a single one. It
  survives only inside a badge.
- **Line height comes from the scale**, never from a `leading-*` class at the
  call site.
- **Tabular numerals wherever digits align** — tables, meters, stat tiles,
  timestamps.

## Layout

### Spacing

Four named steps and nothing else.

| Token | Value | Use |
|---|---|---|
| `row` | 8px | Inside a row: icon to label, chip to chip |
| `stack` | 12px | Between siblings in a list or form |
| `block` | 20px | Card padding, between related blocks |
| `group` | 32px | **Between groups in a section** |
| `page` | 28px | Section outer padding |

The `group` step is the one currently missing from the product, which is why
sections read as a single undifferentiated mesh. **Every section separates its
groups by 32px.**

### Measure

Content is bounded. A card holding two words does not span 1170 pixels, and an
explanatory sentence does not run the full width of the window.

| Token | Value | Use |
|---|---|---|
| `reading` | 720px | Chat, transcripts, prose, documentation |
| `form` | 640px | Settings groups, option lists, single-column forms |
| `page` | 1080px | Dashboards, tables, card grids |

The measure is applied by the shell, not by each view.

### Density

The measured difference between this app and both references is not colour.
Cursor holds 90.4% of its window in two values with 0.19% bright pixels. This
product runs 74% of its type at 12px or smaller across 357 uppercase labels.

**A section is designed by subtraction.** Before styling a screen, remove: every
wrapper that only holds one other thing, every label that repeats what the value
already says, every count nobody acts on, every chrome element around a list
that is already legible. The target is ≥60% of a section's pixels at `room` or
`rail`.

## Elevation

Three levels. Depth is carried by fill, not by shadow.

| Level | Treatment | Use |
|---|---|---|
| Flat | No shadow. Its own surface token. | Rows, tiles, chips — everything in the plane |
| Rim | `inset 0 1px 0` sheen at 5% | Cards and bubbles. A top edge catching light, not a drop shadow |
| Float | Real shadow + `rim-strong` ring | Menus, dialogs, tooltips only |

A black shadow on a near-black ground is mathematically invisible, which is why
the product currently has no working depth device at all. On this ground the
working device is **a lighter fill and a lighter top edge**.

No decorative glows. A bloom is additive light standing in for a fill that
should have been there.

## Shapes

| Token | Value | Use |
|---|---|---|
| `control` | 8px | Rows, buttons, inputs, tiles, menu items |
| `surface` | 12px | Cards, bubbles, panes, composer, popovers |
| `feature` | 16px | Large feature cards. Rare. |
| `pill` | 9999px | Chips, badges, avatars, status dots |

Two families: soft rectangles and pills. Grok Bot uses 12px and pill; Cursor uses
6–10px and pill. Nothing in this product uses 4px.

**The theme never changes an element's shape** — only its value. A card that
loses its radius in dark mode is a bug, not a style.

## Interaction

One ladder, one recipe, app-wide. It only ever goes up.

| State | Treatment |
|---|---|
| Rest | The element's own surface token |
| Hover | One step up the ladder (`object` → `lift`) |
| Selected | `lift` plus `ink-strong` on the label |
| Focus | 2px `rim-strong` ring. Never a third fill. |
| Pressed | `scale(0.98)`, 120ms |

**A hover that darkens is a bug.** So is a selection drawn on a 24×24 icon box
while the 200px row it belongs to stays at ground — selection is a full-width
rounded fill, inset from the column edge, on the whole row. Both references do
exactly this, and it is the loudest missing "you are here" cue in the product.

Transitions are 120ms on fills and 160ms on transforms. Everything respects
`prefers-reduced-motion`.

## Nesting

A child surface **steps up, never down**. It is a hard error for an element to
render darker than its parent in dark mode.

| Parent | A nested well, tile, field or table head takes |
|---|---|
| `room` | `object` or `rail` |
| `rail` | `object` |
| `object` | `lift` |
| `lift` | `float`, or no surface at all |

When the ladder runs out, stop nesting. Grok Bot's search field sits on the rail
at +32 values *inside nothing* — the correct answer to a deep hierarchy is
usually a flatter one.

## States

Every section owns four states, and all four are designed.

- **Loading** renders the real container at its real height with skeleton bars.
  Never a centred grey word in a void, and never invented zeros — a section
  showing `0` while data is in flight reads as broken data, not as loading.
- **Empty** is a designed surface with one sentence saying what will appear here
  and one action that makes it appear. Not a dimmed label.
- **Error** says what failed and what to do. It uses `fault` on a normal
  surface, never a red-washed panel.
- **Populated** is the state everything else in this document describes.

## Do's and Don'ts

### Do

- Lift only what is sized to its content. A surface wider than 720px stays at
  `room` or `rail`.
- Separate with fill first, rim second, shadow only when floating.
- Give every conversation, agent, provider and person a coloured identity mark.
- Spend `life` green on anything that is actually running.
- Cap ink below white and reserve `fill` for fills.
- Bound the measure. Every screen, every time.
- Remove before you restyle.

### Don't

- Don't express depth with opacity. `bg-card/40` as a resting ground is a
  defect; depth is a named token.
- Don't write a literal colour anywhere — not in a component, not in a `.ts`
  module, not in the stylesheet itself.
- Don't put a border on something that already has a fill.
- Don't set a status in `ink` or `fill`, and never let "ok" be quieter than
  "unknown".
- Don't use tiny uppercase labels.
- Don't go below 11px.
- Don't add a shadow to anything that is not floating.
- Don't let a view hand-roll a surface, a section header, or a loading state.

## Light mode

Light is not a regression check. It is the same system read the other way.

Warm paper (`#F7F7F4`) and warm near-black ink (`#26251E`) — the previous
document's one durable contribution, kept. Every rule above holds with the
values swapped: objects rise toward white, interaction answers downward into
warm grey, rims darken instead of lightening, and `fill` becomes ink on paper.

The three status hues do **not** change between themes. `life` green must be
legible on both grounds, and it is.

## Enforcement

These rules are checked, not remembered. Each has a gate in `scripts/ci/`
alongside the existing German and private-key gates.

| Gate | Fails on |
|---|---|
| No literal colour | A hex, `rgb()`, or Tailwind palette class in `.tsx`, `.ts` or `.css` |
| No opacity hierarchy | `bg-(card\|background\|muted)/[0-9]` or `bg-sheen/` used as a resting fill |
| No negative hover | `hover:bg-background/` |
| Type floor | `text-[10px]` or smaller |
| No admin caps | `uppercase` in `views/` outside a badge |

## Known gaps

- Motion is specified only as durations. A typing indicator, a streaming cursor
  and a run-in-progress pulse are named as needed but not yet designed — "dead"
  for an interactive app is partly that nothing moves.
- Icon size and stroke weight are not yet audited across the view tree.
  Mismatched icon weights read as amateur faster than any grey value.
- The wallpaper feature's relationship to this system is undecided. Every value
  here was derived on a flat ground.
- The Agentic IDE's pane family (terminal shells, splits, chat rail, toolbar)
  needs its own section; it is one of the largest surfaces in the product.
