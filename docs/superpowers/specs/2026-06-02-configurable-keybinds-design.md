# Configurable Voice Keybinds — Design

**Date:** 2026-06-02
**Status:** Approved (direction) — pending written-spec review
**Author:** Jarvis dev session

## Problem

The three voice keybinds are inconsistent in how configurable they are:

- **Talk / Push-to-talk** key (`ctrl+right_alt+j`) is already user-configurable
  via `[trigger].hotkey` + the `GET/PUT /api/settings/ptt-hotkey` route + the
  `HotkeyPanel` Settings UI.
- **Call** (`f3+f4`) and **Hangup** (`f1+f2`) are **hardcoded** at the two
  `SpeechPipeline` construction sites (`jarvis/ui/desktop_app.py:1439`,
  `jarvis/speech/watchdog.py:128`) and inside `TriggerConfig.resolve_hotkeys()`.
  The user cannot change them.

The user wants all three voice keybinds editable from one place in Settings.

### Non-problem (explicitly out of scope)

A prior investigation established that the hotkeys themselves **fire reliably**
(`📞 CALL via Hotkey` / `📵 HANGUP via Hotkey` are logged on every press). The
"hotkeys don't work" symptom that prompted this work was a downstream effect of a
total brain-provider outage wedging the voice session — **not** a keybind bug.
This feature does not attempt to fix that wedge or the brain outage. It only adds
configurability.

## Decisions (from brainstorming)

1. **Scope:** unify all three voice keybinds — Call, Hangup, and Talk/PTT — in
   one Settings section. (Not a generic "any action" table; not a mic-mute key.)
2. **Apply timing:** **restart-required**, identical to the existing PTT key.
   Changes persist to `jarvis.toml` and take effect at the next voice bootstrap.
   This deliberately avoids live re-arming the `HotkeyTrigger` in-process, which
   would risk the fragile process-global single-checker refcount in
   `jarvis/trigger/backends/global_hotkeys.py`.

## Architecture

A thin, symmetric extension of the existing single-key pattern across four
layers. No new subsystem; no change to the hotkey backend or the checker
lifecycle.

```
Config (TriggerConfig)  →  resolve_hotkeys() + hangup field
        ↓ persist (config_writer)            ↑ read at bootstrap
API (/api/settings/keybinds)  ←→  Frontend (useKeybinds + Keybinds Settings section)
```

### Shared action vocabulary (anti-drift)

The action identifiers `call`, `hangup`, `ptt` cross Python → API → TS → UI. To
avoid the BUG-008 multi-layer-drift class, they live as **one** small source of
truth:

- Python: a `KEYBIND_ACTIONS` tuple/`Literal` + an `action → toml-key` map
  (`{"call": "hotkey_call", "hangup": "hotkey_hangup", "ptt": "hotkey"}`) defined
  once in `config_writer.py` (it owns the toml mapping) and imported by the API
  route for request validation (unknown action → HTTP 422/400).
- TS: `export type KeybindAction = "call" | "hangup" | "ptt";`

These are UI-routing identifiers (not persisted wire/DB enums), so a full
five-layer parity **test** is overkill; a Python-side `Literal`/validation that
rejects unknown actions is sufficient. A short comment in each file points at the
other layer.

## Components

### 1. Config — `jarvis/core/config.py` (`TriggerConfig`)

Add two fields; keep `hotkey` as the talk/PTT key (backward-compatible):

```python
hotkey: str = "ctrl+right_alt+j"   # talk / push-to-talk key (unchanged)
hotkey_call: str = "f3+f4"         # NEW — call / answer toggle
hotkey_hangup: str = "f1+f2"       # NEW — hangup
```

Update `resolve_hotkeys()` to use `hotkey_call` instead of the literal `"f3+f4"`:

```python
def resolve_hotkeys(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if self.push_to_talk:
        return (self.hotkey_call,), (self.hotkey,)
    return (self.hotkey, self.hotkey_call), ()
```

Hangup stays a separate value read directly from `hotkey_hangup` (see wiring).
Defaults are byte-identical to today's hardcoded behaviour, so an existing
`jarvis.toml` with no new keys behaves exactly as before.

### 2. Wiring — `desktop_app.py` + `watchdog.py`

Replace the hardcoded `hangup_hotkeys=("f1+f2",)` with
`hangup_hotkeys=(cfg.trigger.hotkey_hangup,)` at both `SpeechPipeline`
construction sites. `call_hotkeys`/`ptt_hotkeys` already come from
`resolve_hotkeys()`, so no other change there.

### 3. Persistence — `jarvis/core/config_writer.py`

Add a generic setter that writes the correct `[trigger]` key, BOM-safe + under
`_WRITE_LOCK` like the existing writers:

```python
def set_keybind(action: str, hotkey: str, *, path=DEFAULT_CONFIG_FILE) -> None:
    key = _KEYBIND_TOML_KEY[action]   # {"call": "hotkey_call", ...}
    _patch_table(path, "trigger", key, hotkey)
```

Keep `set_ptt_hotkey(hotkey)` as a thin alias → `set_keybind("ptt", hotkey)` for
backward compatibility. Toml-only by design (same rationale as `set_ptt_hotkey`:
keybinds are not tracked in `config-soll.json`, so the drift-guard won't revert
them).

### 4. API — `jarvis/ui/web/settings_routes.py`

Generalise the single-key route into a keybinds route:

- `GET /api/settings/keybinds` → `{ keybinds: {call, hangup, ptt}, defaults:
  {call, hangup, ptt}, push_to_talk: bool, suggestions: [...],
  restart_required: true }`. Reads the live `cfg.trigger`, falling back to
  `TriggerConfig()` defaults.
- `PUT /api/settings/keybinds` → body `{ action: KeybindAction, hotkey: str,
  persist: bool = true }`. Steps:
  1. Normalise (`strip().lower()`), validate the action against `KEYBIND_ACTIONS`.
  2. `validate_hotkey(combo)` (existing safety validator — modifier-or-2-keys,
     no Win combos, no Alt+F4 / Ctrl+C).
  3. **Collision check (new):** reject if the combo equals either of the other
     two actions' current combos, with a clear English reason.
  4. Update in-memory `cfg.trigger.<key>` and persist via
     `config_writer.set_keybind`.
  5. Return `{ ok, action, hotkey, persisted, restart_required: true }`.

Keep `GET/PUT /api/settings/ptt-hotkey` as a thin backward-compat alias mapping
to `action="ptt"` (in case any other caller exists).

### 5. Frontend — `jarvis/ui/web/frontend/src/`

- Replace `hooks/useHotkey.ts`'s `useHotkey` with `useKeybinds()` that fetches
  `/api/settings/keybinds` and exposes `saveKeybind(action, combo)`. `eventToCombo`
  is already generic and is reused unchanged. `HotkeyPanel` is `useHotkey`'s only
  consumer and is itself replaced (below), so no other call site breaks.
- `views/SettingsView.tsx`: replace `HotkeyPanel` with a **Keybinds** section
  containing three rows (Call / Hangup / Talk-PTT). Each row reuses the existing
  capture UX: a "record" button that captures the next key combo via
  `eventToCombo`, the current value, a reset-to-default action, inline validation
  /collision error display, and the "restart required" note. Extract a reusable
  `<KeybindRow action label .../>` to keep the three rows DRY.
- i18n: add the new strings under `settings_view.keybinds.*` to `de.json`,
  `en.json`, `es.json` (English source per the Output Language Policy). Reuse the
  existing `settings_view.hotkey.*` strings where applicable.

## Data flow

1. Boot: `desktop_app`/`watchdog` read `cfg.trigger` → `resolve_hotkeys()` +
   `hotkey_hangup` → `SpeechPipeline(call_hotkeys, ptt_hotkeys, hangup_hotkeys)`
   → `HotkeyTrigger` arms the combos (unchanged path).
2. User edits a row in Settings → `PUT /api/settings/keybinds` → validate +
   collision-check → persist to `jarvis.toml [trigger]` → response flags
   `restart_required`.
3. UI shows "restart required". On next launch the new combo is armed.

## Error handling

- Invalid combo → `validate_hotkey` reason surfaced inline (existing behaviour).
- Collision with another action → explicit English error, no persist.
- Persist failure → logged warning, `persisted: false` in the response (existing
  pattern); the in-memory value still updated so the current process reflects it.
- Unknown action → 400/422 (Pydantic `Literal` / explicit check).
- Headless / no `[desktop]` extra: unaffected — these are harmless config
  strings; the hotkey backend already degrades to a logged no-op when
  `global_hotkeys`/`pynput` is absent (AD-6/AD-8). The €5-VPS base install is not
  touched.

## Testing

- **Unit (config):** `resolve_hotkeys()` honours a custom `hotkey_call` in both
  `push_to_talk` states; default `hotkey_hangup` == `"f1+f2"`; an old toml with
  no new keys yields the legacy tuples.
- **Unit (config_writer):** `set_keybind("call"/"hangup"/"ptt", ...)` patches the
  right `[trigger]` key; BOM-safe + lock (reuse the existing writer test
  pattern); `set_ptt_hotkey` alias still writes `hotkey`.
- **Unit (API):** `GET /keybinds` returns all three + defaults; `PUT` validates,
  collision-rejects (call == hangup), persists via a fake writer, returns
  `restart_required: true`; unknown action rejected; `/ptt-hotkey` alias still
  works.
- **Frontend (vitest):** `useKeybinds` fetch/save/error shape; `<KeybindRow>`
  capture → combo string; collision/validation error rendering.

## Out of scope (YAGNI)

- Live re-arm without restart (explicitly deferred — restart chosen).
- A generic `[trigger.keybinds]` table for arbitrary future actions.
- Mic-mute or other new action keybinds.
- Multiple combos per action in the UI (internally `push_to_talk=false` still
  yields two call combos; the UI edits the single `hotkey_call`).
- Any change to the `HotkeyTrigger` / `GlobalHotkeysBackend` checker lifecycle.

## Files touched

- `jarvis/core/config.py` — `TriggerConfig` fields + `resolve_hotkeys()`.
- `jarvis/core/config_writer.py` — `set_keybind` + action→key map; `set_ptt_hotkey` alias.
- `jarvis/ui/desktop_app.py`, `jarvis/speech/watchdog.py` — hangup from config.
- `jarvis/ui/web/settings_routes.py` — `/api/settings/keybinds` (+ ptt-hotkey alias).
- `jarvis/ui/web/frontend/src/hooks/useHotkey.ts` — `useKeybinds`.
- `jarvis/ui/web/frontend/src/views/SettingsView.tsx` — Keybinds section + `KeybindRow`.
- `jarvis/ui/web/frontend/src/i18n/locales/{de,en,es}.json` — strings.
- Tests under `tests/unit/...` + frontend vitest.
