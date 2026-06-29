# Onboarding "System Style" Step — Design Spec

- **Date:** 2026-06-20
- **Status:** Approved for planning (brainstorming complete)
- **Topic:** A new first-time-onboarding step that lets the user choose the on-screen
  "system style" (the Jarvis Bar vs. the Orb vs. no overlay), with the Jarvis Bar as the
  default + "Recommended" choice.

---

## 1. Context & Goal

The first-time onboarding guide (`docs/superpowers/specs/2026-06-19-first-time-onboarding-guide-design.md`)
walks a new user through a multi-step setup: `welcome → terms → language → wake-word →
api-keys → mic-test → persona-theme → finish`. It currently has **no step that lets the
user choose how Jarvis appears on screen**.

The goal is to add one **"System Style" selection step** where the user picks the primary
on-screen surface. The **Jarvis Bar** (the slim floating voice bar) is the default and is
labelled **"Recommended"**, so a fresh install is "Bar-equipped" out of the box.

### Key reuse insight

The on-screen surface is already a fully-built, tested axis in the codebase — it does **not**
need a new vocabulary:

- **Backend:** `GET/PUT /api/settings/overlay-style` (`jarvis/ui/web/settings_routes.py`),
  persisted via `config_writer.set_overlay_style` to `ui.orb_style` in `jarvis.toml`. The
  config default is already `ui.orb_style = "jarvis_bar"` (`jarvis/core/config.py:1009`).
- **Frontend:** the `useOverlayStyle` hook (`hooks/useOverlayStyle.ts`) and the 3-card
  picker with preview graphics in `views/settings/OverlayTaskbarGroup.tsx`
  (`BarPreview`, `MascotGigi`, `NonePreview`).
- **Values:** `jarvis_bar` (the Jarvis Bar), `mascot` (the Orb / Gigi ghost), `none`
  (no overlay — just the app window / browser tab).

This step **surfaces that existing axis inside onboarding**; it adds no new enum and no
launcher changes.

### Success criteria

1. On the onboarding flow, a new `system-style` step appears after `persona-theme` and
   before `finish`.
2. The step shows all three options as preview cards; **`jarvis_bar` is pre-selected and
   carries a visible "Recommended" badge**.
3. Picking an option persists it (`PUT /api/settings/overlay-style`, `persist: true`) and
   live-applies it when possible; when a restart is required it offers an explicit
   **"Restart now"** action (a pick alone never auto-restarts) (see §4).
4. The step is skippable; "Next" / "Skip" both leave the persisted default (`jarvis_bar`)
   in place.
5. The backend `ONBOARDING_STEPS` and the frontend `REGISTRY` stay in sync (the existing
   cross-layer parity test covers the new key).
6. The developer can verify via `?onboarding=force` without resetting any completed-install
   state — the maintainer's configured localhost install is never re-onboarded.

### Non-goals

- No new `system_style` enum or config key — we reuse `ui.orb_style` / `overlay-style`.
- No launcher / headless mode switching at runtime ("browser-only" as a *deployment* mode is
  out of scope; `none` is the honest in-app representative of "no floating overlay").
- No change to the existing Settings overlay picker behaviour (`OverlayTaskbarGroup`).
- No *automatic* app-restart: the restart is always an explicit, opt-in click (never fired by
  selecting a card).

---

## 2. Architecture

**Chosen approach: a new onboarding step that reuses the existing `overlay-style` system.**

A new step component (`SystemStyleStep.tsx`) renders the same three-option picker as the
Settings panel, reusing `useOverlayStyle` and the preview graphics, pre-selected on
`jarvis_bar` with a "Recommended" badge. It registers as a new step key `system-style` in
both the backend step list and the frontend registry.

### Rejected alternatives

- **New `system_style` enum (Bar / Window / Browser) + launcher integration.** A second
  3-way vocabulary parallel to the existing `overlay-style`, plus a full five-layer enum
  (Python ↔ Pydantic ↔ TS ↔ UI) and launcher work to make "browser-only" actually switch
  at runtime (a headless restart with different flags). Rejected: YAGNI + multi-layer enum
  drift risk (AP-4); duplicates an axis that already exists.
- **Purely informational step (display only, no effect).** Stores nothing actionable, so
  "Recommended" would be a dead label. Rejected: dead-feature risk.

### Component tree (frontend)

```
OnboardingFlow                       // stepper shell (unchanged mechanics)
└─ steps/SystemStyleStep   (NEW)     // 3-card overlay-style picker, Bar pre-selected + Recommended
   ├─ reuses hooks/useOverlayStyle.ts
   └─ reuses preview graphics (BarPreview / MascotGigi / NonePreview)
```

Preview-graphic reuse is small and targeted, not a refactor: `MascotGigi` is already a
shared component (`@/components/MascotGigi`) and `NonePreview` is already exported from
`OverlayTaskbarGroup.tsx`. Only `BarPreview` is currently a private function in that file.
The clean move is to lift the three previews into one shared module
(e.g. `components/overlay/OverlayStylePreviews.tsx`) so the onboarding step does not import a
Settings view, with both call sites importing from there. The `StylePreview` dispatcher
(maps a style value → its graphic) moves with them. No picker logic changes.

### Data flow

The step calls `useOverlayStyle()` → `GET /api/settings/overlay-style` to read the current
style and options, pre-selects `jarvis_bar`, and on a card click calls `saveStyle(opt)` →
`PUT /api/settings/overlay-style` with `persist: true`. The onboarding stepper's existing
`goNext` / `skip` advance the flow. The onboarding step pointer persists through the normal
`/api/onboarding/step` path (unchanged).

---

## 3. The Step

| Field | Value |
|---|---|
| Step key | `system-style` |
| Position | after `persona-theme`, before `finish` |
| Mandatory? | No — skippable; default (`jarvis_bar`) stands if skipped |
| Reuses | `useOverlayStyle`, `/api/settings/overlay-style`, overlay preview graphics |

### Options (all three, matching the existing axis)

| Card | `overlay-style` value | Onboarding framing |
|---|---|---|
| **Jarvis Bar** | `jarvis_bar` | Pre-selected + **"Recommended"** badge. "The slim floating voice bar — always within reach." |
| **Orb** | `mascot` | "A floating companion orb (Gigi)." |
| **No overlay** | `none` | "Just the app window / browser tab — no floating surface." |

### Layout

A title + short description, then the three preview cards in a row (reusing the Settings
visuals), the **Recommended** badge on the Jarvis Bar card, a primary "Next" button, and an
unobtrusive "Skip" link — matching the structure of `PersonaThemeStep`.

---

## 4. Behaviour: live-apply, else one-click restart

The pick is persisted (`persist: true`) and live-applied when `swap_overlay` can change the
surface in place. When it can't — a first-time `bar ↔ mascot` swap needs a brand-new Tk root,
which would cross-thread-abort the process (BUG-031) — the backend returns
`restart_required: true`. The choice is real (persisted) but invisible until the app
restarts.

The original design left that to a passive "applies next start" hint and **no** restart
action. Maintainer feedback (2026-06-20): a selection must actually take effect, not force a
manual relaunch. So the step now mirrors the Settings `OverlayStylePanel`:

- **On a pick:** persist + attempt live-apply. If `applied_live` → done, no prompt (the
  common case — keeping `jarvis_bar`, the maintainer's current state, is a no-op).
- **If `restart_required`:** show a **"Restart now to apply"** button that POSTs
  `/api/settings/restart-app` → the existing `request_restart` relauncher cleanly self-
  restarts the app so the chosen style is live. The same **409 → force-arm** path as the
  Settings panel guards running missions (first click warns, second click forces).
- **Never on the pick itself:** the app only restarts when the user explicitly clicks
  "Restart now" — picking a card never auto-restarts.

This is a deliberate change from the first draft's no-restart stance; honoring the user's
choice outweighs avoiding a mid-flow restart, and the restart is opt-in (one explicit click),
so the flow is never torn down without intent.

---

## 5. Backend

Single change: add `"system-style"` to `ONBOARDING_STEPS` in
`jarvis/setup/onboarding_meta.py`, positioned between `"persona-theme"` and `"finish"`:

```python
ONBOARDING_STEPS = [
    "welcome", "terms", "language", "wake-word",
    "api-keys", "mic-test", "persona-theme",
    "system-style",   # NEW
    "finish",
]
```

No new route, no new config key, no new enum. The overlay-style endpoint, config writer,
and `ui.orb_style` default (`"jarvis_bar"`) already exist and are unchanged.

---

## 6. Frontend Details

- **Registry:** add `"system-style": SystemStyleStep` to `REGISTRY` in `OnboardingFlow.tsx`
  (between `persona-theme` and `finish`). `STEP_KEYS` then matches the backend list.
- **New component:** `steps/SystemStyleStep.tsx` consuming `StepProps`, using
  `useOverlayStyle`, pre-selecting `jarvis_bar`, rendering the Recommended badge, persisting
  on pick, advancing via `goNext` / `skip`.
- **Shared previews:** lift `BarPreview` + `NonePreview` + the `StylePreview` dispatcher into
  `components/overlay/OverlayStylePreviews.tsx` (`MascotGigi` already lives in
  `@/components/MascotGigi`); update `OverlayTaskbarGroup.tsx` to import from there (no visual
  change).
- **i18n:** new keys under `onboarding.system_style.*` — title, description, the three option
  labels + sub-captions, the "Recommended" badge, the "applies next start" hint, and the
  skip label. English source in `en.json`, translations in `de.json` / `es.json`
  (CI language-policy gate compliant). Reuse existing `onboarding.nav.next` / `…back`.
- **Accessibility:** cards are `<button>` with `aria-pressed`; the Recommended option is the
  default focus target; keyboard navigable; respects the overlay's existing focus trap.

---

## 7. Testing Strategy

### Automated (TDD)

- **Frontend (vitest)** — `SystemStyleStep.test.tsx`:
  - Renders three options; `jarvis_bar` is pre-selected and shows the "Recommended" badge.
  - Clicking a card calls `saveStyle` with the right value (mock `useOverlayStyle`).
  - "Next" advances; "Skip" advances and leaves the default.
  - A **pick alone** never calls `/api/settings/restart-app` (restart is an explicit,
    separate click).
  - When a pick reports `restart_required`, a "Restart now" button appears and clicking it
    POSTs `/api/settings/restart-app` (with the 409 → force-arm path).
- **Parity** — the existing frontend↔backend step parity test now includes `system-style`
  (add the key on both sides so it passes).
- **Backend (pytest)** — assert `"system-style"` is present in `ONBOARDING_STEPS` in the
  correct position; the onboarding-state endpoint returns it in `steps`.

### Developer verification (never destroys the configured env)

- **`?onboarding=force`** in the running localhost app (`http://localhost:47821`) to render
  the flow and walk to the new step without clearing any completed-install state.
- Final self-check with the **Chrome checkup-loop skill** against the live localhost app, as
  requested by the maintainer.

---

## 8. Acceptance Criteria

1. A `system-style` step renders after `persona-theme` and before `finish`; the parity test
   passes (backend + frontend lists agree).
2. The step shows all three overlay styles as cards; `jarvis_bar` is pre-selected and
   visibly "Recommended".
3. Selecting an option persists it via `PUT /api/settings/overlay-style` (`persist: true`);
   no app restart is triggered from onboarding.
4. The step is skippable; the default (`jarvis_bar`) stands when skipped.
5. The maintainer's configured localhost install is never re-onboarded by this work
   (verification uses `?onboarding=force` only).
6. `ruff` clean; vitest + the CI language-policy gate pass (English source strings).

---

## 9. Open Items / Future

- Optionally surface a richer preview (animated bar) later; the static SVG previews ship now.
- If a real "browser-only / headless" deployment toggle is ever wanted, that is a separate
  launcher-level feature (out of scope here; `none` covers the in-app "no overlay" case).
