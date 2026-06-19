# Fold the "Languages" section into Settings

**Date:** 2026-06-07
**Status:** Approved (design), implementing
**Scope:** Frontend only (desktop app). No backend change.

## Goal

Remove the standalone "Languages" sidebar entry and fold its controls into the
Settings view as the top-most panel. The `languages` `SectionId` stays valid and
is re-routed to Settings so the existing voice command ("zeig die Sprachen",
aliases `sprache`/`sprachen`/`language`) keeps working and now lands in Settings.

This mirrors the earlier "Taskbar → Settings" fold (`OverlayTaskbarGroup`): the
nav row disappears, but the id survives and `MainView` routes it to
`<SettingsView />`.

## Why this shape

`SectionId` is a five-layer wire-format enum (TS type ↔ `SECTION_IDS` ↔
`SECTION_LABELS` ↔ `navigate.py` `KNOWN` ↔ `navigation_intent.py`). Removing an id
would touch several layers and break the parity guard
(`tests/unit/plugins/tool/test_navigate.py`). Keeping the id and only re-routing
it is the precedent-following, drift-safe path.

## Changes

1. **New `views/settings/LanguagesGroup.tsx`** — the body of the former
   `LanguagesView` (UI-language section, reply-language section, recognition note,
   per-session override note), without the page-level `ViewHeader`, presented as a
   titled group like `OverlayTaskbarGroup`. Reuses every existing
   `languages_view.*` i18n key. One new group-title key.

2. **`SettingsView.tsx`** — render `<LanguagesGroup />` as the first panel (above
   the assistant-name panel).

3. **`Sidebar.tsx`** — remove the `{ id: "languages", … }` nav item; add
   `"languages"` to the Settings row `matchIds`
   (`["settings", "taskbar", "languages"]`) so the row highlights when voice
   navigation lands there.

4. **`MainView.tsx`** — route `case "languages":` to `<SettingsView />` alongside
   `settings`/`taskbar`; drop the now-unused `LanguagesView` import.

5. **Delete `views/LanguagesView.tsx`** — content moved into `LanguagesGroup`
   (mirror of the deleted `TaskbarView`). Only importer was `MainView`.

6. **Unchanged (load-bearing):** `SectionId` `languages` stays in `events.ts`,
   `SECTION_LABELS.languages` stays, `navigate.py` needs no change. The
   `nav.languages` i18n key is left in place (unused, harmless).

7. **i18n:** add `settings_view.languages_group_title` to `en`/`de`/`es`.

## Tests (TDD)

- Extend `__tests__/i18n.test.ts`: assert `settings_view.languages_group_title`
  exists in all three locales (RED: key missing → GREEN: add key).
- New `views/settings/LanguagesGroup.test.tsx`: the group renders the group title,
  both sections, and the language option rows (RED: component missing → GREEN:
  create component).
- Existing `test_navigate.py` parity test stays green (no id removed).

## Risks

Minimal. The only real hazard is the anti-drift parity between `SECTION_IDS` and
`navigate.py` — deliberately untouched. Verify with `npm run build`
(`tsc -b`) + `npm run test` + the navigate parity test.
