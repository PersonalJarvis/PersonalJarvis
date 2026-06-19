# Move the "Taskbar" sidebar section into "Settings"

**Date:** 2026-06-07
**Status:** Approved (design)
**Scope:** Frontend-only UI restructuring (React). No backend logic change.

## Problem

The desktop app's sidebar has two separate top-level entries, "Taskbar" and
"Settings". The Taskbar entry hosts only three controls — the on-screen overlay
style selector and two dictation toggles — which conceptually belong with the
rest of the app configuration. The user wants the Taskbar section folded into
the Settings section so there is a single configuration destination.

## Goal

Remove the standalone "Taskbar" sidebar entry. Surface its three controls inside
the Settings view as a clearly-labelled group ("Leiste & Overlay"). Keep the
existing voice command ("geh zur Taskleiste") working by routing it to Settings.

## Non-goals

- No change to the backend config logic (`set_bar_persistent`,
  `set_mute_music`, the overlay-style endpoint) or the React hooks that drive
  these controls (`useBarPersistent`, `useMuteMusic`, `useOverlayStyle`).
- No change to the five-layer section vocabulary (`SectionId` / `SECTION_IDS` in
  `store/events.ts`, `KNOWN` in `navigate.py`). See "Chosen approach" for why.
- No retranslation of existing committed i18n; only one new key is added.

## Current state (verified)

- `jarvis/ui/web/frontend/src/components/layout/Sidebar.tsx:67` — standalone
  `{ id: "taskbar", labelKey: "nav.taskbar", icon: PanelBottom }` NAV item.
  `Settings` NAV item is at line 63.
- `jarvis/ui/web/frontend/src/components/layout/MainView.tsx:19,85-86` — imports
  `TaskbarView` and routes `case "taskbar": return <TaskbarView />`.
- `jarvis/ui/web/frontend/src/views/taskbar/TaskbarView.tsx` — holds the three
  panels to move: `OverlayStylePanel`, `BarPersistentRow`, `MuteMusicRow`, plus
  a local `ToggleRow` helper and `StylePreview`/`BarPreview`/`NonePreview` SVG
  helpers used only by `OverlayStylePanel`.
- `jarvis/ui/web/frontend/src/views/SettingsView.tsx` — target. Already a flat
  scroll page of panels (AssistantName, Autostart, WakeWord, Keybinds, generic
  rows, BackendConnection, Codex, Safety).
- `jarvis/plugins/tool/navigate.py:107-108` — aliases `"task bar"` and
  `"taskleiste"` already map to the canonical id `"taskbar"`.
- `tests/unit/plugins/tool/test_navigate.py:106` — parity guard asserts
  `NavigateTool.known_sections() == SECTION_IDS` (exact set equality). This test
  is **currently red** due to an unrelated in-flight parallel session removing
  `terminal`/`review`; our change must not add to that drift.
- No test (frontend or backend) asserts the presence of the Taskbar sidebar
  entry or imports `TaskbarView`. Only Sidebar/MainView/TaskbarView and the
  i18n locales reference `taskbar`.

## Chosen approach (Weg A): keep `taskbar` as a valid id, route it to Settings

The voice alias `"taskleiste" → "taskbar"` (navigate.py) stays. We re-route the
`taskbar` id to render the Settings view and drop the standalone sidebar row.

This deliberately does **not** touch `navigate.py` or `store/events.ts` — both
are being edited by a parallel session (git status shows them as Modified while
`terminal`/`review` are being removed). Avoiding them removes entanglement risk
and keeps the navigate parity test status unchanged. It also reuses the existing
`matchIds` pattern already used by the merged "Extensions" entry
(`Sidebar.tsx:46-51`).

Trade-off accepted: the `taskbar` id remains a "silent" identifier (it renders
Settings, is never shown as its own nav row, and keeps `SECTION_LABELS.taskbar`).
This is cosmetic and consistent with how the Extensions ids already behave.

Rejected alternative (Weg B): remove the `taskbar` id entirely and turn
"taskleiste" into a true alias of "settings". Cleaner semantics, but it edits
the two parallel-hot files and removes an enum value from the five-layer
vocabulary — higher risk for no user-visible benefit.

## Changes

### 1. `views/SettingsView.tsx` — receive the three panels

- Add a new group inside the existing scroll container, titled "Leiste &
  Overlay", placed **after the generic `rows` list and before
  `BackendConnectionSection`** — i.e. the upper-middle position shown in the
  approved mockup (below Privacy/Scope/Toasts, above Backend Connection).
- Move `OverlayStylePanel`, `BarPersistentRow`, `MuteMusicRow`, the `ToggleRow`
  helper, and the `StylePreview` / `BarPreview` / `NonePreview` SVG helpers from
  `TaskbarView.tsx` into `SettingsView.tsx` (or a small co-located module —
  see "Isolation note"). They keep their current i18n keys (`taskbar_view.*`,
  `settings_view.overlay_style.*`) and their hooks unchanged.
- The group renders the two existing sub-headings inside it: appearance
  (`taskbar_view.appearance_title`) wrapping the overlay panel, and behaviour
  (`taskbar_view.behavior_title`) wrapping the two toggle rows — identical
  structure to the current TaskbarView body.

**Isolation note:** `SettingsView.tsx` is already ~744 lines. To avoid growing a
single large file, extract the moved controls into a focused sibling module
`views/settings/OverlayTaskbarGroup.tsx` (default export a `OverlayTaskbarGroup`
component containing the heading + the three panels + their private helpers).
`SettingsView` then renders `<OverlayTaskbarGroup />` in one line. This keeps the
unit small, self-contained, and independently testable.

### 2. `components/layout/MainView.tsx` — re-route

- Remove the `TaskbarView` import (line 19).
- Change `case "taskbar":` to `return <SettingsView />;` (merge it with the
  existing `case "settings":` fall-through).

### 3. `components/layout/Sidebar.tsx` — drop the row, broaden the match

- Remove the `{ id: "taskbar", ... }` NAV item (line 67) and the now-unused
  `PanelBottom` import.
- Give the Settings NAV item `matchIds: ["settings", "taskbar"]` so the
  "Einstellungen" row stays highlighted when the active section is `taskbar`
  (i.e. after a voice "Taskleiste" jump).

### 4. i18n — one new key

Add `settings_view.overlay_taskbar_group_title` to de / en / es:
- de: "Leiste & Overlay"
- en: "Bar & Overlay"
- es: "Barra y superposición"

All other strings reuse the existing `taskbar_view.*` and
`settings_view.overlay_style.*` keys (no deletion, no retranslation).

### 5. Cleanup

- Delete `views/taskbar/TaskbarView.tsx` (its content now lives in
  `views/settings/OverlayTaskbarGroup.tsx`). Remove the empty `views/taskbar/`
  directory if nothing else remains.

## Data flow (unchanged)

`OverlayStylePanel` → `useOverlayStyle().saveStyle` → `POST /api/settings/...`.
`BarPersistentRow` → `useBarPersistent().setEnabled`. `MuteMusicRow` →
`useMuteMusic().setEnabled`. Voice "Taskleiste" → `navigate.py` resolves to
`taskbar` → `NavigateSidebar(section="taskbar")` → `useWebSocket.ts` accepts it
(`isSectionId("taskbar")` is still true) → `setActiveSection("taskbar")` →
`MainView` renders `<SettingsView />`, Sidebar highlights "Einstellungen" via
`matchIds`.

## Testing

- `npm run build` (tsc) and `npm run test` (vitest) stay green.
- Add a focused vitest test for `OverlayTaskbarGroup` (or `SettingsView`)
  asserting the group heading and the three controls render.
- No backend test changes. The navigate parity test is untouched by this change
  (we modify neither `KNOWN` nor `SECTION_IDS`); its pre-existing red state from
  the parallel terminal/review removal is out of scope.

## Addendum (implementation): Settings declutter

During implementation the user asked to also declutter the Settings view in the
same pass — several rows/panels looked confusing (notably the static
"Privacy-Mode"). The following are **removed from `SettingsView.tsx`** (UI only;
no backend logic deleted):

- the **Privacy-Mode** static row (`settings_view.rows.privacy_*`),
- the **Project-Scope** static row (`settings_view.rows.scope_*`),
- the **Backend-Connection** block (`<BackendConnectionSection />`),
- the **OpenAI Codex CLI** path panel (`settings_view.codex_*`, plus its
  `useProviders` / `setCodexBinaryPath` state),
- the **Safety-Whitelist** static block (`settings_view.safety_*`).

Settings now contains: Assistant-Name, Autostart, Wake-Word, Keybinds, the
Autopilot-Toasts toggle, and the new "Leiste & Overlay" group. The now-unused
i18n keys are left in place (no retranslation / no deletion churn); the
`BackendConnectionSection` component file and the providers hook are untouched
and remain importable elsewhere.

## Risks

- **Parallel-session drift** on `events.ts` / `navigate.py`: mitigated by not
  touching them.
- **Dead i18n keys**: none removed, so no dangling references.
- **Restart required**: the desktop app bundles the frontend into the pywebview
  RAM image; the change is live only after a rebuild + restart (standard for
  this repo).
