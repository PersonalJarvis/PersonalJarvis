# Clear a Voice Keybind — Design

**Date:** 2026-07-02
**Status:** Approved (direction, confirmed in chat)
**Author:** Jarvis dev session

## Problem

The Voice Keybinds section (Settings → Call / Hangup / Talk-PTT, built in
`docs/superpowers/specs/2026-06-02-configurable-keybinds-design.md`) lets a
user record a new combo per action, but there is no way to **unbind** one —
leave an action with no key at all. The recorder can only ever produce a
non-empty combo, `onSaveClick` explicitly refuses to save an empty one, and
the backend (`validate_hotkey`) rejects an empty string as invalid input. A
user who does not want a keyboard shortcut for, say, Hangup has no path to
that state today.

The three keybinds must stay independently and fully configurable as before —
this only adds "none" as a legal value for any one of them.

## Existing capability this builds on

The plumbing already treats an empty combo list as a first-class "off" state
for one action (`ptt`): `SpeechPipeline` only registers `ptt` bindings
`if self._ptt_hotkeys:`, and the live re-arm log line already prints `"off"`
for an empty PTT list. `HotkeyTrigger._build_bindings` iterates each action's
combo *list* and simply contributes zero bindings for an empty one — this is
already safe for `call` and `hangup` too, it is just never exercised because
nothing upstream ever produces an empty list for them. This design extends
the same "off" state to `call` and `hangup`, and exposes it in the UI for all
three actions.

## A pre-existing bug this design must fix

`PUT /api/settings/keybinds`'s collision check compares the new key-set
against every other action's key-set with a subset/superset test. If another
action is *already* unbound (empty key-set), the empty set is a subset of
literally any key-set, so the check as written would reject **every** save
with a false "overlaps with `<unbound action>`" error the moment one action
is cleared. The frontend's mirror of this check
(`useHotkey.ts::validateCombo`) already skips comparisons against an empty
other-combo — the backend needs the same guard. This is a latent bug (unbinding
doesn't exist yet, so it has never fired), fixed as part of this change since
it would otherwise break saving immediately after the first clear.

## Decision (confirmed in chat)

One additional **Clear** button per keybind row, next to Record/Save, visible
at all times. Clicking it immediately unbinds and persists that action (no
staging step, no confirmation dialog) — mirroring the existing "Reset to
default" link's immediacy. Disabled when the action is already unbound.
Recording/saving a real combo continues to work exactly as before; this only
adds "none" as a reachable state.

Rejected alternative: making the recorder field itself clearable (e.g. via
Escape). The recorder has no natural gesture for "produce nothing" — Escape
already restores the last saved value — so this would need a new affordance
anyway, while blending "record a new key" and "erase" into one control.

## Architecture

No new layer. The empty string is the sentinel for "unbound" at every layer,
consistent with how `hotkey_hangup`/`hotkey_call` already default to a
non-empty string and how `ptt_hotkeys=()` already means "off" — no new field,
no new enum value.

```
Frontend: Clear button → saveKeybind(action, "")
              ↓
API PUT /api/settings/keybinds  — hotkey == "" is a distinct, valid branch:
  skip validate_hotkey, skip collision-check-against-others (nothing to
  collide with), persist "" to jarvis.toml, live-apply set_keybinds(**{action: []})
              ↓
Config (TriggerConfig.resolve_hotkeys) — filters "" out of the returned
  tuples so an unbound action never reaches HotkeyTrigger as a bogus
  single-element ("",) combo
              ↓
HotkeyTrigger — an action with zero combos contributes zero OS registrations
  (already-safe existing behaviour)
```

## Components

### 1. Backend — `jarvis/ui/web/settings_routes.py`

- `KeybindBody.hotkey`: drop `min_length=1` (keep `max_length=64`) so an
  empty string reaches the handler instead of a blanket 422.
- `put_keybind`: branch on `hotkey == ""` right after normalizing:
  - Skip `validate_hotkey` (it always rejects empty — that rule exists for
    "user is still recording", not for "user explicitly cleared it").
  - Skip the collision loop entirely (an unbound action cannot collide).
  - Persist `""` via the existing `config_writer.set_keybind` call — no
    special-casing needed there, it just writes an empty TOML string.
  - Live-apply via `pipeline.set_keybinds(**{action: []})` instead of
    `[hotkey]` (empty list, not a list containing an empty string).
- Existing collision loop (non-empty `hotkey` path): add
  `if not other_keys: continue` before the subset/superset test, so a save
  is never rejected against an already-unbound other action. This is the bug
  fix from above — needed regardless of which action is being saved.

### 2. Config — `jarvis/core/config.py` (`TriggerConfig.resolve_hotkeys`)

Filter blank entries out of both returned tuples so an empty `hotkey_call`
(or `hotkey` when `push_to_talk` is off) never produces a `("",)` tuple:

```python
def resolve_hotkeys(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if self.push_to_talk:
        call, ptt = (self.hotkey_call,), (self.hotkey,)
    else:
        call, ptt = (self.hotkey, self.hotkey_call), ()
    return (
        tuple(h for h in call if h.strip()),
        tuple(h for h in ptt if h.strip()),
    )
```

### 3. Wiring — the three `SpeechPipeline(hangup_hotkeys=...)` call sites

`jarvis/ui/desktop_app.py:2278`, `jarvis/speech/watchdog.py:128`, and the
`jarvis/speech/pipeline.py` CLI smoke-test entry point (`:7879`) all build
`hangup_hotkeys=(config.trigger.hotkey_hangup,)` — a 1-tuple regardless of
content. Change each to omit the entry when blank:

```python
hangup_hotkeys=(
    (config.trigger.hotkey_hangup,) if config.trigger.hotkey_hangup.strip() else ()
),
```

### 4. Frontend — `jarvis/ui/web/frontend/src/views/SettingsView.tsx`

- `KeybindRow`: add an `onClearClick` handler that calls
  `onSave(action, "")` directly (bypassing the local recorder `combo`
  state), then on success sets `combo` to `""`, closes the recorder if open,
  and shows the same saved/restart-required feedback as a normal save.
- Render a **Clear** button next to Record/Save (`variant="outline"`,
  `size="sm"`, small icon e.g. `X`/`Eraser`), `disabled={loading || saving ||
  !current}` — nothing to clear when the action is already unbound.
- Idle empty-state text: replace the bare `"—"` placeholder (shown when not
  capturing and `combo` is empty) with a dedicated
  `settings_view.keybinds.unbound` string ("No key assigned") so a
  deliberately-cleared row reads distinctly from a mid-recording blank.
- `showReset`/`dirty` logic is untouched — clearing already makes `showReset`
  true (current combo differs from default), giving an existing path back to
  the default combo.

### 5. i18n — `de.json` / `en.json` / `es.json`

New keys under `settings_view.keybinds.*` (English source, per the Output
Language Policy):
- `clear`: "Clear"
- `cleared`: "Keybind cleared"
- `unbound`: "No key assigned"

## Data flow

1. User clicks **Clear** on a row → `saveKeybind(action, "")`.
2. `PUT /api/settings/keybinds` takes the empty branch: no validation, no
   collision check, persists `""` to `jarvis.toml [trigger]`, live-applies
   `set_keybinds(**{action: []})` if a pipeline is running.
3. `HotkeyTrigger.rearm` (or the next boot's `resolve_hotkeys()`) registers
   zero OS bindings for that action — the other two actions are untouched.
4. UI shows "Keybind cleared" + the existing restart-required note when no
   live pipeline is present.

## Error handling

- Nothing new on the invalid-combo path — that validation is unchanged for
  non-empty input.
- Persist failure on clear: same existing pattern (`persisted: false`,
  logged warning, in-memory value still updated).
- All three keybinds can be cleared simultaneously — voice stays reachable
  via wake-word and the mascot-click UI regardless; this is intentional, not
  guarded against, matching the explicit ask.

## Testing

- **Unit (config):** `resolve_hotkeys()` drops a blank `hotkey_call`/`hotkey`
  from its output tuples in both `push_to_talk` states.
- **Unit (API):** `PUT` with `hotkey=""` skips validation and the collision
  check, persists `""`, calls `set_keybinds` with an empty list; a *second*
  `PUT` for a different action with a real combo succeeds even though the
  first action is now unbound (regression test for the collision-check bug).
- **Unit (hotkey backend):** existing `_build_bindings` coverage already
  implies an empty combo list is safe; add one explicit case for `call`.
- **Frontend (vitest):** Clear button calls `onSave(action, "")`; disabled
  when `current` is already empty; row shows the "No key assigned" text
  after a successful clear.
- **Manual/Chrome verification:** after implementation, click through the
  real Settings page (chrome-checkup-loop) — clear each of the three rows,
  confirm the button disables once cleared, confirm Record still works to
  rebind afterward, confirm no console/network errors.

## Out of scope (YAGNI)

- A confirmation dialog before clearing (matches "Reset to default", which
  has none).
- A dedicated `clear: bool` request field — the empty-string sentinel is
  reused, consistent with how `ptt_hotkeys=()` already means "off".
- Enforcing at least one bound action — not requested; wake-word/mascot
  click remain as fallbacks.

## Files touched

- `jarvis/ui/web/settings_routes.py` — `KeybindBody`, `put_keybind` empty
  branch + collision-check guard.
- `jarvis/core/config.py` — `resolve_hotkeys()` blank-filtering.
- `jarvis/ui/desktop_app.py`, `jarvis/speech/watchdog.py`,
  `jarvis/speech/pipeline.py` — blank-safe `hangup_hotkeys` wiring.
- `jarvis/ui/web/frontend/src/views/SettingsView.tsx` — Clear button +
  unbound placeholder text.
- `jarvis/ui/web/frontend/src/i18n/locales/{de,en,es}.json` — new strings.
- Tests under `tests/unit/...` + frontend vitest.
