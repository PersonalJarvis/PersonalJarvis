# Open Knowledge Matrix — ProfileView de-classification

**Date:** 2026-05-29
**Status:** Approved (design), implementing
**Scope:** Frontend only (`jarvis/ui/web/frontend/`). No backend, data, config, or enum changes.

## Problem

The Profile view's "Knowledge matrix" section (what Jarvis knows about the user)
renders as a *classified dossier*: unknown/empty fields are covered by a
diagonal-hatch **redaction bar** (`.dossier-redact`), and the hero carries a
`Lock` icon plus "Confidential" / "Dossier" framing. To the user this reads as
information being **withheld** ("verdeckt") — even though the bars merely mark
empty fields. The user wants the opposite: everything plainly readable, openly
collected under the "Knowledge Matrices" heading. *"Just see what's there. No
effect."*

## Decision

Approach **"Open knowledge matrix"**: remove the concealment/secrecy signals,
keep the tasteful structure (ghost numerals, segmented meters, corner frames —
these conceal nothing).

### Changes

1. **`FieldRow` (`ProfileView.tsx`)** — empty fields render quiet, readable
   muted-italic text (`profile_view.field_unknown`, e.g. "noch nicht bekannt")
   instead of the `.dossier-redact` hatch bar. Drop the now-unused `seed` prop
   and `REDACT_WIDTHS` table.
2. **`HeroBand` (`ProfileView.tsx`)** — swap the `Lock` icon in the
   classification strip for an open icon (`BrainCircuit`); remove the
   `.dossier-hatch` corner texture div.
3. **CSS (`index.css`)** — delete `.dossier-redact`; delete `.dossier-hatch`
   (only consumer was the hero corner).
4. **i18n (`en/de/es.json`)** — reword the secrecy strings to an open framing
   (English is the source):
   - `hero_eyebrow`: "Dossier" → "Knowledge profile"
   - `hero_classification`: "Confidential" → "Your knowledge profile"
   - `hero_tagline`: "The file Jarvis keeps on you …" → "What Jarvis has learned
     about you — from your conversations."
   (`hero_maintained_by` and `section_knowledge` = "Knowledge matrix" stay.)
5. **`ProfileView.test.tsx`** — add a regression test: with empty cluster
   fields, no `.dossier-redact` node renders and the `field_unknown` label is
   shown; the hero no longer renders "Confidential"/"Vertraulich".

### Kept (not concealment, just structure)
Cluster cards + plain-text values, `NumberedSection` ghost numerals,
`SegmentedMeter`, `CornerFrame` around avatar/icons.

## Verification
`npm run test` (vitest) green → `npm run build` → app restart (pywebview holds
the old bundle in RAM) → before/after screenshot for visual sign-off.
