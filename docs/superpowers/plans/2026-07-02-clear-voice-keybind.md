# Clear a Voice Keybind Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user leave any of the three Voice Keybinds (Call / Hangup / Talk-PTT) unbound via a per-row "Clear" button in Settings, without weakening the ability to record a real combo for any of them.

**Architecture:** The empty string is reused as the existing "unbound" sentinel at every layer (mirroring how `ptt_hotkeys=()` already means "off" in `SpeechPipeline`). The API's `PUT /api/settings/keybinds` gets an explicit branch for `hotkey == ""` that skips validation/collision-checking and unbinds live; `TriggerConfig.resolve_hotkeys()` and the three `SpeechPipeline` construction call sites are made blank-safe so an unbound action never reaches the hotkey backend as a bogus one-element tuple containing `""`. The frontend adds a Clear button per row plus a distinct "No key assigned" placeholder.

**Tech Stack:** FastAPI + Pydantic (backend), React + TypeScript + Vitest/Testing Library (frontend), pytest (backend tests).

**Spec:** `docs/superpowers/specs/2026-07-02-clear-voice-keybind-design.md`

---

### Task 1: Config layer — `resolve_hotkeys()` drops blank entries

**Files:**
- Modify: `jarvis/core/config.py:202-215`
- Test: `tests/unit/core/test_trigger_keybinds.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/core/test_trigger_keybinds.py`:

```python
def test_resolve_hotkeys_drops_blank_call_when_ptt_on() -> None:
    t = TriggerConfig(push_to_talk=True, hotkey="ctrl+right_alt+j", hotkey_call="")
    call, ptt = t.resolve_hotkeys()
    assert call == ()
    assert ptt == ("ctrl+right_alt+j",)


def test_resolve_hotkeys_drops_blank_ptt_when_ptt_on() -> None:
    t = TriggerConfig(push_to_talk=True, hotkey="", hotkey_call="f3+f4")
    call, ptt = t.resolve_hotkeys()
    assert call == ("f3+f4",)
    assert ptt == ()


def test_resolve_hotkeys_drops_blank_entries_when_ptt_off() -> None:
    t = TriggerConfig(push_to_talk=False, hotkey="", hotkey_call="f3+f4")
    call, ptt = t.resolve_hotkeys()
    assert call == ("f3+f4",)
    assert ptt == ()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/core/test_trigger_keybinds.py -v`
Expected: the three new tests FAIL (current code returns `("",)` instead of `()`).

- [ ] **Step 3: Implement the blank-filtering**

In `jarvis/core/config.py`, replace the `resolve_hotkeys` method (lines 202-215):

```python
    def resolve_hotkeys(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Split the configured hotkeys into ``(call_hotkeys, ptt_hotkeys)``
        for ``SpeechPipeline``.

        With ``push_to_talk`` on (default), the configured ``hotkey`` becomes a
        true push-to-talk key (hold = record, release = submit) and ``hotkey_call``
        stays a quick wake-style toggle. With it off, ``hotkey`` is a toggle
        alongside ``hotkey_call`` and there is no PTT (the pre-2026-05-29 wiring).
        Hangup is a separate value read from ``hotkey_hangup`` at the
        SpeechPipeline call sites.

        A blank string means the user explicitly cleared that action (Settings
        Clear button) — filtered out here so an unbound key never reaches
        ``HotkeyTrigger`` as a bogus single-element tuple containing ``""``.
        """
        if self.push_to_talk:
            call, ptt = (self.hotkey_call,), (self.hotkey,)
        else:
            call, ptt = (self.hotkey, self.hotkey_call), ()
        return (
            tuple(h for h in call if h.strip()),
            tuple(h for h in ptt if h.strip()),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/core/test_trigger_keybinds.py -v`
Expected: all tests PASS (7 total — 4 existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add jarvis/core/config.py tests/unit/core/test_trigger_keybinds.py
git commit -m "feat(voice): resolve_hotkeys drops blank (unbound) keybind entries"
```

---

### Task 2: Wiring — blank-safe `hangup_hotkeys` at the three SpeechPipeline sites

**Files:**
- Modify: `jarvis/ui/desktop_app.py:2278`
- Modify: `jarvis/speech/watchdog.py:128`
- Modify: `jarvis/speech/pipeline.py:7879`

No dedicated unit test: these are inline kwargs inside three async app-bootstrap
functions with no existing unit coverage of this line (the original 2026-06-02
feature left the same line untested at this granularity — `resolve_hotkeys()`
already gets full unit coverage from Task 1, and the CLEAR flow itself is
covered end-to-end by Task 6's manual Chrome pass). Skipped per YAGNI rather
than mocking a full app bootstrap for a one-line ternary.

- [ ] **Step 1: Fix `jarvis/ui/desktop_app.py:2278`**

Change:

```python
                hangup_hotkeys=(self.cfg.trigger.hotkey_hangup,),
```

to:

```python
                hangup_hotkeys=(
                    (self.cfg.trigger.hotkey_hangup,)
                    if self.cfg.trigger.hotkey_hangup.strip()
                    else ()
                ),
```

- [ ] **Step 2: Fix `jarvis/speech/watchdog.py:128`**

Change:

```python
        hangup_hotkeys=(config.trigger.hotkey_hangup,),
```

to:

```python
        hangup_hotkeys=(
            (config.trigger.hotkey_hangup,)
            if config.trigger.hotkey_hangup.strip()
            else ()
        ),
```

- [ ] **Step 3: Fix `jarvis/speech/pipeline.py:7879`**

Change:

```python
        hangup_hotkeys=(config.trigger.hotkey_hangup,),
```

to:

```python
        hangup_hotkeys=(
            (config.trigger.hotkey_hangup,)
            if config.trigger.hotkey_hangup.strip()
            else ()
        ),
```

- [ ] **Step 4: Verify all three sites are consistent**

Run: `grep -n "hangup_hotkeys=" jarvis/ui/desktop_app.py jarvis/speech/watchdog.py jarvis/speech/pipeline.py`
Expected: all three show the new ternary form, none show the bare
`(X.trigger.hotkey_hangup,)` 1-tuple form anymore.

- [ ] **Step 5: Sanity-import check**

Run: `python -c "import jarvis.ui.desktop_app, jarvis.speech.watchdog, jarvis.speech.pipeline"`
Expected: no `SyntaxError` / `ImportError` (catches a stray paren/indent typo
before it reaches app boot).

- [ ] **Step 6: Commit**

```bash
git add jarvis/ui/desktop_app.py jarvis/speech/watchdog.py jarvis/speech/pipeline.py
git commit -m "fix(voice): never register a bogus empty-string hangup hotkey"
```

---

### Task 3: API — `PUT /api/settings/keybinds` unbind branch + collision-check fix

**Files:**
- Modify: `jarvis/ui/web/settings_routes.py:577-686`
- Test: `tests/unit/ui/test_keybinds_route.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/ui/test_keybinds_route.py`:

```python
def test_put_empty_hotkey_unbinds_without_validation_error() -> None:
    """An explicit empty hotkey clears the action instead of being rejected
    as an incomplete recording (validate_hotkey normally rejects '')."""
    body = _client().put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "", "persist": False},
    ).json()
    assert body["ok"] is True
    assert body["hotkey"] == ""


def test_put_empty_hotkey_skips_collision_check() -> None:
    """Clearing hangup must never be rejected as 'overlapping' with call —
    there is nothing left to collide with."""
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "", "persist": False},
    )
    assert resp.status_code == 200


def test_put_after_clearing_other_action_still_allows_a_new_combo() -> None:
    """Regression for the false-positive collision bug: an unbound OTHER
    action's empty key-set must not be treated as a subset of every new
    combo (an empty set is a mathematical subset of everything), which would
    otherwise reject every future save once any one action is cleared."""
    client = _client()
    client.put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "", "persist": False},
    )
    resp = client.put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "f7+f8", "persist": False},
    )
    assert resp.status_code == 200


def test_put_empty_hotkey_live_applies_empty_list() -> None:
    """The running pipeline is re-armed with an empty list (not [\"\"])."""
    calls: list[dict] = []

    class _FakePipeline:
        def set_keybinds(self, **kw):  # noqa: ANN003
            calls.append(kw)

    client = _client()
    client.app.state.speech_pipeline = _FakePipeline()
    resp = client.put(
        "/api/settings/keybinds",
        json={"action": "ptt", "hotkey": "", "persist": False},
    )
    assert resp.json()["applied_live"] is True
    assert calls == [{"ptt": []}]


def test_get_reflects_cleared_keybind() -> None:
    client = _client()
    client.put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "", "persist": False},
    )
    body = client.get("/api/settings/keybinds").json()
    assert body["keybinds"]["call"] == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/ui/test_keybinds_route.py -v`
Expected: the 5 new tests FAIL — the first three with HTTP 422 (Pydantic
`min_length=1`) or 400 ("Hotkey is empty." / false collision), the live-apply
one with `calls == [{"ptt": [""]}]` instead of `[{"ptt": []}]`.

- [ ] **Step 3: Allow an empty `hotkey` through the request body**

In `jarvis/ui/web/settings_routes.py`, change (line 579):

```python
    hotkey: str = Field(..., min_length=1, max_length=64)
```

to:

```python
    hotkey: str = Field(..., max_length=64)
```

- [ ] **Step 4: Add the unbind branch + collision-check guard in `put_keybind`**

Replace the whole `put_keybind` function body (`jarvis/ui/web/settings_routes.py:603-686`):

```python
@router.put("/keybinds")
async def put_keybind(body: KeybindBody, request: Request) -> dict[str, object]:
    from jarvis.core.config_writer import KEYBIND_ACTIONS, KEYBIND_TOML_KEY
    from jarvis.trigger.hotkey import validate_hotkey

    action = body.action.strip().lower()
    if action not in KEYBIND_ACTIONS:
        raise HTTPException(status_code=400, detail=f"unknown action: {action}")
    hotkey = body.hotkey.strip().lower()

    cfg = _config(request)
    trig = getattr(cfg, "trigger", None) if cfg is not None else None

    if hotkey:
        # The backend is the authority — a browser key-capture cannot be
        # trusted to filter OS-critical / unusable combos (AltGr detection is
        # unreliable there).
        ok, reason = validate_hotkey(hotkey)
        if not ok:
            raise HTTPException(status_code=400, detail=reason)

        # Collision check: one chord can't both answer and hang up. Exact
        # equality is not enough — the polling hotkey backend matches a combo
        # as soon as its keys are down, so a key-set SUBSET of another
        # action's combo fires both (call=f1 + hangup=f1+f2 → F1+F2 triggers
        # call AND hangup). Reject any subset/superset relation between the
        # key sets, in both directions.
        new_keys = {p.strip() for p in hotkey.split("+") if p.strip()}
        for other_action, other_combo in _keybind_values(trig).items():
            if other_action == action:
                continue
            other_keys = {
                p.strip() for p in other_combo.strip().lower().split("+") if p.strip()
            }
            if not other_keys:
                # The other action is itself unbound (Clear button) — an
                # empty key-set is a subset of every combo, so without this
                # guard EVERY save would be rejected as "overlapping" the
                # moment any one action is cleared.
                continue
            if new_keys <= other_keys or other_keys <= new_keys:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"'{hotkey}' overlaps with '{other_action}' "
                        f"('{other_combo.strip().lower()}') — pressing one would "
                        "trigger both. Pick keys that don't contain each other."
                    ),
                )
    # else: hotkey == "" is an explicit "unbind this action" request (Settings
    # Clear button) — skip validate_hotkey (that rule exists for "still
    # recording", not "cleared on purpose") and skip the collision check
    # (an unbound action cannot collide with anything).

    field = KEYBIND_TOML_KEY[action]
    if trig is not None:
        try:
            setattr(trig, field, hotkey)
        except Exception as exc:  # noqa: BLE001 — frozen model is not an error
            log.debug("in-memory trigger.%s update skipped: %s", field, exc)

    persisted = False
    if body.persist:
        try:
            from jarvis.core import config_writer

            config_writer.set_keybind(action, hotkey)
            persisted = True
        except Exception as exc:  # noqa: BLE001
            log.warning("keybind persist failed: %s", exc)

    # Live-apply to the running voice pipeline so the new combo (or the
    # cleared state) takes effect immediately — no app restart. Best-effort —
    # a headless/down pipeline just means it applies on next start.
    applied_live = False
    pipeline = getattr(request.app.state, "speech_pipeline", None)
    if pipeline is not None and hasattr(pipeline, "set_keybinds"):
        try:
            # An empty hotkey re-arms with an EMPTY list, not a list
            # containing "" — mirrors how the PTT action already represents
            # "off" internally.
            pipeline.set_keybinds(**{action: [hotkey] if hotkey else []})
            applied_live = True
        except Exception as exc:  # noqa: BLE001 — never fail the save on a live-apply hiccup
            log.warning("keybind live-apply failed (persisted; applies on restart): %s", exc)

    return {
        "ok": True,
        "action": action,
        "hotkey": hotkey,
        "persisted": persisted,
        # When live-applied the running trigger already re-armed; no restart
        # needed. Otherwise it takes effect on the next voice start.
        "applied_live": applied_live,
        "restart_required": not applied_live,
    }
```

- [ ] **Step 5: Run the full route test file to verify everything passes**

Run: `pytest tests/unit/ui/test_keybinds_route.py -v`
Expected: all tests PASS (14 total — 9 existing + 5 new).

- [ ] **Step 6: Run the full backend keybind test suite (no regressions)**

Run: `pytest tests/unit/ui/test_keybinds_route.py tests/unit/core/test_trigger_keybinds.py tests/unit/core/test_config_writer_keybinds.py tests/unit/speech/test_pipeline_keybind_reload.py tests/unit/trigger/test_hotkey.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add jarvis/ui/web/settings_routes.py tests/unit/ui/test_keybinds_route.py
git commit -m "feat(voice): allow clearing a keybind via PUT /api/settings/keybinds"
```

---

### Task 4: i18n strings — `clear` / `cleared` / `unbound`

**Files:**
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/en.json`
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/de.json`
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/es.json`

No test step — these are static locale-table additions consumed by Task 5's
component test (which asserts on the real English strings, same pattern as
`WakeWordPanel.test.tsx`).

- [ ] **Step 1: Add the three keys to `en.json`**

In `jarvis/ui/web/frontend/src/i18n/locales/en.json`, inside the `keybinds`
block, change:

```json
      "recording_hint": "Hold the keys you want (then let go), or click them on the keyboard below. Esc cancels.",
      "save": "Save",
      "saved": "Keybind saved",
      "reset": "Reset to default",
```

to:

```json
      "recording_hint": "Hold the keys you want (then let go), or click them on the keyboard below. Esc cancels.",
      "unbound": "No key assigned",
      "save": "Save",
      "clear": "Clear",
      "saved": "Keybind saved",
      "cleared": "Keybind cleared",
      "reset": "Reset to default",
```

- [ ] **Step 2: Add the three keys to `de.json`**

In `jarvis/ui/web/frontend/src/i18n/locales/de.json`, inside the `keybinds`
block, change:

```json
      "recording_hint": "Halte die gewünschten Tasten (dann loslassen) — oder klick sie unten auf der Tastatur an. Esc bricht ab.",
      "save": "Speichern",
      "saved": "Tastenkürzel gespeichert",
      "reset": "Auf Standard zurücksetzen",
```

to:

```json
      "recording_hint": "Halte die gewünschten Tasten (dann loslassen) — oder klick sie unten auf der Tastatur an. Esc bricht ab.",
      "unbound": "Keine Taste zugewiesen",
      "save": "Speichern",
      "clear": "Entfernen",
      "saved": "Tastenkürzel gespeichert",
      "cleared": "Tastenkürzel entfernt",
      "reset": "Auf Standard zurücksetzen",
```

- [ ] **Step 3: Add the three keys to `es.json`**

In `jarvis/ui/web/frontend/src/i18n/locales/es.json`, inside the `keybinds`
block, change:

```json
      "recording_hint": "Mantén las teclas que quieras (y suéltalas) — o haz clic en ellas en el teclado de abajo. Esc cancela.",
      "save": "Guardar",
      "saved": "Atajo guardado",
      "reset": "Restablecer predeterminado",
```

to:

```json
      "recording_hint": "Mantén las teclas que quieras (y suéltalas) — o haz clic en ellas en el teclado de abajo. Esc cancela.",
      "unbound": "Sin tecla asignada",
      "save": "Guardar",
      "clear": "Quitar",
      "saved": "Atajo guardado",
      "cleared": "Atajo eliminado",
      "reset": "Restablecer predeterminado",
```

- [ ] **Step 4: Validate all three files are still well-formed JSON**

Run: `node -e "['de','en','es'].forEach(l => JSON.parse(require('fs').readFileSync('jarvis/ui/web/frontend/src/i18n/locales/'+l+'.json','utf8')))"`
Expected: no output, exit code 0 (a syntax slip like a missing comma throws
`SyntaxError` here).

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/i18n/locales/en.json jarvis/ui/web/frontend/src/i18n/locales/de.json jarvis/ui/web/frontend/src/i18n/locales/es.json
git commit -m "feat(voice): add i18n strings for the keybind Clear button"
```

---

### Task 5: Frontend — Clear button + unbound placeholder in `KeybindRow`

**Files:**
- Modify: `jarvis/ui/web/frontend/src/views/SettingsView.tsx:1-6,613-732`
- Test: Create `jarvis/ui/web/frontend/src/views/SettingsView.keybinds.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `jarvis/ui/web/frontend/src/views/SettingsView.keybinds.test.tsx`:

```tsx
/**
 * Tests for the Clear button on the Voice Keybinds rows (KeybindsPanel,
 * rendered inside SettingsView).
 */
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/i18n", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/i18n")>();
  return {
    ...actual,
    useT: () => actual.useT(),
    useUiLanguage: () => "en",
    useReplyLanguage: () => "auto",
  };
});

const FULL_CONFIG = {
  keybinds: { call: "f3+f4", hangup: "f1+f2", ptt: "ctrl+right_alt+j" },
  defaults: { call: "f3+f4", hangup: "f1+f2", ptt: "ctrl+right_alt+j" },
  push_to_talk: true,
  suggestions: [],
  restart_required: false,
};

const saveKeybind = vi.fn().mockResolvedValue({
  ok: true,
  action: "hangup",
  hotkey: "",
  persisted: true,
  applied_live: true,
  restart_required: false,
});

vi.mock("@/hooks/useHotkey", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/useHotkey")>();
  return {
    ...actual,
    useKeybinds: () => ({
      config: FULL_CONFIG,
      loading: false,
      error: null,
      refetch: vi.fn(),
      saveKeybind,
    }),
  };
});

import { KeybindsPanel } from "@/views/SettingsView";

afterEach(() => {
  cleanup();
  saveKeybind.mockClear();
});

describe("KeybindsPanel — Clear button", () => {
  it("renders a Clear button for every bound row", () => {
    render(<KeybindsPanel />);
    expect(screen.getByTestId("clear-keybind-call")).toBeInTheDocument();
    expect(screen.getByTestId("clear-keybind-hangup")).toBeInTheDocument();
    expect(screen.getByTestId("clear-keybind-ptt")).toBeInTheDocument();
  });

  it("clicking Clear saves an empty hotkey for that action", async () => {
    render(<KeybindsPanel />);
    fireEvent.click(screen.getByTestId("clear-keybind-hangup"));
    await waitFor(() => expect(saveKeybind).toHaveBeenCalledWith("hangup", ""));
  });

  it("shows 'No key assigned' after a successful clear", async () => {
    render(<KeybindsPanel />);
    fireEvent.click(screen.getByTestId("clear-keybind-hangup"));
    await waitFor(() =>
      expect(screen.getAllByText("No key assigned").length).toBeGreaterThan(0),
    );
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd jarvis/ui/web/frontend && npx vitest run src/views/SettingsView.keybinds.test.tsx`
Expected: FAIL — `getByTestId("clear-keybind-call")` finds nothing (the
button does not exist yet).

- [ ] **Step 3: Add the `X` icon import**

In `jarvis/ui/web/frontend/src/views/SettingsView.tsx`, change (lines 2-6):

```tsx
import {
  Settings,
  Mic,
  Keyboard,
} from "lucide-react";
```

to:

```tsx
import {
  Settings,
  Mic,
  Keyboard,
  X,
} from "lucide-react";
```

- [ ] **Step 4: Add the `onClearClick` handler**

In `jarvis/ui/web/frontend/src/views/SettingsView.tsx`, right after the
closing brace of `onSaveClick` (after line 632, before the `dirty`/`showReset`
consts), insert:

```tsx
  async function onClearClick() {
    setSaving(true);
    try {
      const res = await onSave(action, "");
      setCombo("");
      setCapturing(false);
      setSaved(res.restart_required);
      pushToast("success", t("settings_view.keybinds.cleared"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }
```

- [ ] **Step 5: Render the Clear button next to Save**

In the same file, change the Save `<Button>` block:

```tsx
        <Button
          size="sm"
          onClick={onSaveClick}
          disabled={saving || loading || !dirty || invalid}
        >
          {saving ? t("settings_view.saving") : t("settings_view.keybinds.save")}
        </Button>
```

to:

```tsx
        <Button
          size="sm"
          onClick={onSaveClick}
          disabled={saving || loading || !dirty || invalid}
        >
          {saving ? t("settings_view.saving") : t("settings_view.keybinds.save")}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          data-testid={`clear-keybind-${action}`}
          aria-label={t("settings_view.keybinds.clear")}
          title={t("settings_view.keybinds.clear")}
          onClick={onClearClick}
          disabled={saving || loading || !current}
        >
          <X className="h-3.5 w-3.5" />
        </Button>
```

- [ ] **Step 6: Distinguish the idle-empty placeholder from a deliberate clear**

In the same file, change:

```tsx
          {combo ? (
            <ComboChips combo={combo} />
          ) : (
            <span className="text-muted-foreground">
              {capturing ? t("settings_view.keybinds.recording") : "—"}
            </span>
          )}
```

to:

```tsx
          {combo ? (
            <ComboChips combo={combo} />
          ) : (
            <span className="text-muted-foreground">
              {capturing
                ? t("settings_view.keybinds.recording")
                : loading
                  ? "—"
                  : t("settings_view.keybinds.unbound")}
            </span>
          )}
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `cd jarvis/ui/web/frontend && npx vitest run src/views/SettingsView.keybinds.test.tsx`
Expected: all 3 tests PASS.

- [ ] **Step 8: Run the full frontend test suite (no regressions)**

Run: `cd jarvis/ui/web/frontend && npx vitest run`
Expected: all tests PASS, including `useKeybinds.test.ts` and
`WakeWordPanel.test.tsx`.

- [ ] **Step 9: Typecheck + lint**

Run: `cd jarvis/ui/web/frontend && npx tsc --noEmit && npx eslint src/views/SettingsView.tsx src/views/SettingsView.keybinds.test.tsx`
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add jarvis/ui/web/frontend/src/views/SettingsView.tsx jarvis/ui/web/frontend/src/views/SettingsView.keybinds.test.tsx
git commit -m "feat(voice): add a Clear button to each Voice Keybinds row"
```

---

### Task 6: Manual verification in the real app (chrome-checkup-loop)

**Files:** none — verification only.

- [ ] **Step 1: Rebuild the frontend and restart the app**

Run: `cd jarvis/ui/web/frontend && npm run build`
Then restart Jarvis via `POST /api/settings/restart-app` (or relaunch
`run.bat`) so the new bundle is served — per CLAUDE.md, `Stop-Process` is not
the correct restart path for the tray `pythonw.exe`.

- [ ] **Step 2: Drive the Settings page with claude-in-chrome**

Navigate to the desktop app's Settings view, scroll to Voice Keybinds, and for
each of Call / Hangup / Talk-PTT:
1. Click **Clear** — confirm the field switches to "No key assigned", the
   Clear button becomes disabled, and a success toast appears.
2. Click **Record**, press a real combo, confirm **Save** re-enables and
   persists it, and the row shows the new combo.
3. Repeat Clear once more to confirm it's reachable again after a fresh save.

- [ ] **Step 3: Check for regressions**

Read the browser console (`read_console_messages`) and network log
(`read_network_requests`) during the pass above — confirm no errors and no
failed `/api/settings/keybinds` requests.

- [ ] **Step 4: Confirm collision checking still works for real combos**

With Call cleared, try to save Hangup to Call's OLD default combo (`f3+f4`) —
should succeed (nothing to collide with). Then set both Call and Hangup to
real, distinct combos and confirm setting one of them to the other's exact
combo is still rejected with the overlap error (regression check for the
Task 3 collision-check guard).

- [ ] **Step 5: Report**

Summarize the pass/fail result. If anything fails, fix it and re-run Steps
2-4 before considering the feature done — do not report success without
having actually clicked through it (per `verification-before-completion`).
