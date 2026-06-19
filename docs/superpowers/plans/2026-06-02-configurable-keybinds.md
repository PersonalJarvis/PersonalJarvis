# Configurable Voice Keybinds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all three voice keybinds (Call, Hangup, Talk/PTT) user-editable from one Settings section, persisted to `jarvis.toml`, applied on the next restart.

**Architecture:** Thin symmetric extension of the existing single-key (`ptt-hotkey`) pattern across config → persistence → API → frontend. Call (`f3+f4`) and Hangup (`f1+f2`) move from hardcoded values to `[trigger]` config fields. No change to the `HotkeyTrigger` / `GlobalHotkeysBackend` checker lifecycle, and no live re-arm (restart-required, exactly like today's PTT key).

**Tech Stack:** Python 3.11 / Pydantic v2 / FastAPI / tomlkit (`config_writer`) / React + TypeScript + vitest + @testing-library/react.

**Spec:** `docs/superpowers/specs/2026-06-02-configurable-keybinds-design.md`

**Conventions for this repo (do not skip):**
- All artifacts in **English** (code, comments, commits). German only in the `de.json` locale *values*.
- Run tests with the project interpreter pattern; `pytest tests/...` from the repo root (`asyncio_mode=auto`).
- `git add` only the exact files listed per task — **never** `git add -A` (the working tree carries unrelated parallel-session changes).

---

### Task 1: Config fields + `resolve_hotkeys()`

**Files:**
- Modify: `jarvis/core/config.py` (`TriggerConfig`, ~lines 138–172)
- Test: `tests/unit/core/test_trigger_keybinds.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/core/test_trigger_keybinds.py`:

```python
"""TriggerConfig keybind fields + resolve_hotkeys (configurable Call/Hangup/PTT)."""
from __future__ import annotations

from jarvis.core.config import TriggerConfig


def test_defaults_match_legacy_hardcoded_values() -> None:
    t = TriggerConfig()
    assert t.hotkey == "ctrl+right_alt+j"
    assert t.hotkey_call == "f3+f4"
    assert t.hotkey_hangup == "f1+f2"


def test_resolve_hotkeys_ptt_on_uses_call_field() -> None:
    t = TriggerConfig(push_to_talk=True, hotkey="ctrl+right_alt+j", hotkey_call="f7+f8")
    call, ptt = t.resolve_hotkeys()
    assert call == ("f7+f8",)
    assert ptt == ("ctrl+right_alt+j",)


def test_resolve_hotkeys_ptt_off_has_two_call_combos_no_ptt() -> None:
    t = TriggerConfig(push_to_talk=False, hotkey="ctrl+shift+space", hotkey_call="f7+f8")
    call, ptt = t.resolve_hotkeys()
    assert call == ("ctrl+shift+space", "f7+f8")
    assert ptt == ()


def test_old_toml_without_new_keys_keeps_legacy_behaviour() -> None:
    # A config built only from the legacy keys must behave exactly as before.
    t = TriggerConfig(hotkey="ctrl+right_alt+j", push_to_talk=True)
    call, ptt = t.resolve_hotkeys()
    assert call == ("f3+f4",)
    assert ptt == ("ctrl+right_alt+j",)
    assert t.hotkey_hangup == "f1+f2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/core/test_trigger_keybinds.py -v`
Expected: FAIL — `TypeError`/`AttributeError` on `hotkey_call` (field does not exist yet).

- [ ] **Step 3: Add the two fields**

In `jarvis/core/config.py`, in `class TriggerConfig`, immediately after the `hotkey: str = "ctrl+right_alt+j"` line, add:

```python
    # Call/answer toggle key. Was hardcoded "f3+f4" in resolve_hotkeys() and at
    # the SpeechPipeline call sites; now user-editable via /api/settings/keybinds.
    hotkey_call: str = "f3+f4"
    # Hangup key. Was hardcoded ("f1+f2",) at the SpeechPipeline call sites; now
    # user-editable via /api/settings/keybinds. Read directly at bootstrap.
    hotkey_hangup: str = "f1+f2"
```

- [ ] **Step 4: Update `resolve_hotkeys()` to use `hotkey_call`**

Replace the body of `TriggerConfig.resolve_hotkeys` with:

```python
    def resolve_hotkeys(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Split the configured hotkeys into ``(call_hotkeys, ptt_hotkeys)``
        for ``SpeechPipeline``.

        With ``push_to_talk`` on (default), the configured ``hotkey`` becomes a
        true push-to-talk key (hold = record, release = submit) and ``hotkey_call``
        stays a quick wake-style toggle. With it off, ``hotkey`` is a toggle
        alongside ``hotkey_call`` and there is no PTT. Hangup is a separate
        value read from ``hotkey_hangup`` at the SpeechPipeline call sites.
        """
        if self.push_to_talk:
            return (self.hotkey_call,), (self.hotkey,)
        return (self.hotkey, self.hotkey_call), ()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/core/test_trigger_keybinds.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add jarvis/core/config.py tests/unit/core/test_trigger_keybinds.py
git commit -m "feat(config): configurable hotkey_call + hotkey_hangup in TriggerConfig"
```

---

### Task 2: Persistence — `set_keybind` + shared action vocabulary

**Files:**
- Modify: `jarvis/core/config_writer.py` (near `set_ptt_hotkey`, ~line 172)
- Test: `tests/unit/core/test_config_writer_keybinds.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/core/test_config_writer_keybinds.py`:

```python
"""config_writer.set_keybind — persist Call/Hangup/PTT keybinds to [trigger]."""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.core import config_writer


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_set_keybind_call_writes_hotkey_call(tmp_path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text('[trigger]\nhotkey = "ctrl+right_alt+j"\n', encoding="utf-8")
    config_writer.set_keybind("call", "f7+f8", path=toml)
    assert 'hotkey_call = "f7+f8"' in _read(toml)


def test_set_keybind_hangup_writes_hotkey_hangup(tmp_path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text("[trigger]\n", encoding="utf-8")
    config_writer.set_keybind("hangup", "ctrl+shift+h", path=toml)
    assert 'hotkey_hangup = "ctrl+shift+h"' in _read(toml)


def test_set_keybind_ptt_writes_hotkey(tmp_path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text("[trigger]\n", encoding="utf-8")
    config_writer.set_keybind("ptt", "ctrl+alt+m", path=toml)
    assert 'hotkey = "ctrl+alt+m"' in _read(toml)


def test_set_ptt_hotkey_alias_still_writes_hotkey(tmp_path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text("[trigger]\n", encoding="utf-8")
    config_writer.set_ptt_hotkey("ctrl+alt+n", path=toml)
    assert 'hotkey = "ctrl+alt+n"' in _read(toml)


def test_set_keybind_unknown_action_raises(tmp_path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text("[trigger]\n", encoding="utf-8")
    with pytest.raises(ValueError):
        config_writer.set_keybind("bogus", "f1+f2", path=toml)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/core/test_config_writer_keybinds.py -v`
Expected: FAIL — `AttributeError: module 'jarvis.core.config_writer' has no attribute 'set_keybind'`.

- [ ] **Step 3: Add the action vocabulary + `set_keybind`, make `set_ptt_hotkey` an alias**

In `jarvis/core/config_writer.py`, replace the existing `set_ptt_hotkey` function (currently `_patch_table(path, "trigger", "hotkey", hotkey)`) with:

```python
# Voice-keybind action vocabulary. Shared with the keybinds API
# (jarvis/ui/web/settings_routes.py) and the TS type KeybindAction in the
# frontend (jarvis/ui/web/frontend/src/hooks/useHotkey.ts). Keep the three in
# sync. The mapped value is BOTH the jarvis.toml key under [trigger] AND the
# TriggerConfig field name (they are intentionally identical).
KEYBIND_ACTIONS = ("call", "hangup", "ptt")
KEYBIND_TOML_KEY = {
    "call": "hotkey_call",
    "hangup": "hotkey_hangup",
    "ptt": "hotkey",
}


def set_keybind(action: str, hotkey: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist a voice keybind (call / hangup / ptt) to ``[trigger]`` in jarvis.toml.

    Toml-only by design (same rationale as the other [trigger] writers — these
    keys are NOT tracked in config-soll.json, so the drift-guard never reverts
    them; a plain atomic write suffices). Takes effect on the next SpeechPipeline
    bootstrap (a Jarvis restart): bindings are armed once at pipeline start via
    ``TriggerConfig.resolve_hotkeys`` + the ``hotkey_hangup`` read at the call
    sites.
    """
    try:
        key = KEYBIND_TOML_KEY[action]
    except KeyError:
        raise ValueError(f"unknown keybind action: {action!r}") from None
    _patch_table(path, "trigger", key, hotkey)


def set_ptt_hotkey(hotkey: str, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Backward-compatible alias for ``set_keybind("ptt", ...)``."""
    set_keybind("ptt", hotkey, path=path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/core/test_config_writer_keybinds.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Verify the legacy persist test still passes**

Run: `pytest tests/unit/ui/test_ptt_hotkey_route.py -v`
Expected: PASS — `test_put_persist_calls_config_writer` monkeypatches `set_ptt_hotkey` (still the function the legacy route calls), so it is unaffected.

- [ ] **Step 6: Commit**

```bash
git add jarvis/core/config_writer.py tests/unit/core/test_config_writer_keybinds.py
git commit -m "feat(config_writer): set_keybind + shared keybind action vocabulary"
```

---

### Task 3: API — add `GET/PUT /api/settings/keybinds`

**Files:**
- Modify: `jarvis/ui/web/settings_routes.py` (add after the existing ptt-hotkey block, ~line 324)
- Test: `tests/unit/ui/test_keybinds_route.py` (create)

The existing `/api/settings/ptt-hotkey` route is **left untouched** for backward compatibility; we only ADD the new route.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/ui/test_keybinds_route.py`:

```python
"""GET/PUT /api/settings/keybinds — editable Call/Hangup/Talk keybinds."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web.settings_routes import router


def _client(**trig) -> TestClient:
    defaults = dict(
        hotkey="ctrl+right_alt+j",
        hotkey_call="f3+f4",
        hotkey_hangup="f1+f2",
        push_to_talk=True,
    )
    defaults.update(trig)
    app = FastAPI()
    app.include_router(router)
    app.state.config = SimpleNamespace(trigger=SimpleNamespace(**defaults))
    return TestClient(app)


def test_get_returns_all_three_plus_defaults() -> None:
    body = _client().get("/api/settings/keybinds").json()
    assert body["keybinds"] == {
        "call": "f3+f4",
        "hangup": "f1+f2",
        "ptt": "ctrl+right_alt+j",
    }
    assert body["defaults"]["call"] == "f3+f4"
    assert body["restart_required"] is True
    assert len(body["suggestions"]) >= 3


def test_put_call_accepts_and_normalizes_case() -> None:
    body = _client().put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "F7+F8", "persist": False},
    ).json()
    assert body["ok"] is True
    assert body["action"] == "call"
    assert body["hotkey"] == "f7+f8"
    assert body["restart_required"] is True


def test_put_rejects_unsafe_combo() -> None:
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "j", "persist": False},
    )
    assert resp.status_code == 400


def test_put_rejects_unknown_action() -> None:
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "mute", "hotkey": "f7+f8", "persist": False},
    )
    assert resp.status_code == 400


def test_put_rejects_collision_with_other_action() -> None:
    # call defaults to f3+f4; binding hangup to the same combo must be rejected.
    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "f3+f4", "persist": False},
    )
    assert resp.status_code == 400
    assert "call" in resp.json()["detail"]


def test_put_in_memory_update_reflects_in_get() -> None:
    client = _client()
    client.put(
        "/api/settings/keybinds",
        json={"action": "call", "hotkey": "f7+f8", "persist": False},
    )
    body = client.get("/api/settings/keybinds").json()
    assert body["keybinds"]["call"] == "f7+f8"


def test_put_persist_calls_config_writer(monkeypatch) -> None:
    from jarvis.core import config_writer

    captured: dict = {}

    def _fake_set_keybind(action, hotkey, *, path=None):  # noqa: ANN001
        captured["action"] = action
        captured["hotkey"] = hotkey

    monkeypatch.setattr(config_writer, "set_keybind", _fake_set_keybind)

    resp = _client().put(
        "/api/settings/keybinds",
        json={"action": "hangup", "hotkey": "ctrl+shift+h", "persist": True},
    )
    assert resp.status_code == 200
    assert resp.json()["persisted"] is True
    assert captured == {"action": "hangup", "hotkey": "ctrl+shift+h"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/ui/test_keybinds_route.py -v`
Expected: FAIL — 404 (route not registered yet).

- [ ] **Step 3: Add the keybinds route**

In `jarvis/ui/web/settings_routes.py`, add the following AFTER the existing `put_ptt_hotkey` handler (after line ~323, before the assistant-name section). It reuses the existing `_HOTKEY_SUGGESTIONS`, `_config`, `router`, `log`, `HTTPException`, `BaseModel`, `Field`, `Request`:

```python
# ---------------------------------------------------------------------------
# Voice keybinds (editable): Call / Hangup / Talk-PTT. GET all three + defaults;
# PUT one action at a time. Persisted to jarvis.toml [trigger]; applies on the
# next voice bootstrap (a Jarvis restart) — bindings are armed once at pipeline
# start. The legacy /ptt-hotkey route above stays for backward compatibility.
# ---------------------------------------------------------------------------

from jarvis.core.config_writer import KEYBIND_ACTIONS, KEYBIND_TOML_KEY


def _keybind_values(trig: object) -> dict[str, str]:
    """Current combo per action, falling back to TriggerConfig defaults."""
    from jarvis.core.config import TriggerConfig

    d = TriggerConfig()
    out: dict[str, str] = {}
    for action, field in KEYBIND_TOML_KEY.items():
        default = getattr(d, field)
        out[action] = str(getattr(trig, field, default)) if trig is not None else default
    return out


class KeybindBody(BaseModel):
    action: str = Field(..., description="call | hangup | ptt")
    hotkey: str = Field(..., min_length=1, max_length=64)
    persist: bool = Field(default=True, description="Persist to jarvis.toml")


@router.get("/keybinds")
async def get_keybinds(request: Request) -> dict[str, object]:
    from jarvis.core.config import TriggerConfig

    cfg = _config(request)
    trig = getattr(cfg, "trigger", None) if cfg is not None else None
    d = TriggerConfig()
    return {
        "keybinds": _keybind_values(trig),
        "defaults": {"call": d.hotkey_call, "hangup": d.hotkey_hangup, "ptt": d.hotkey},
        "push_to_talk": bool(getattr(trig, "push_to_talk", True)) if trig else True,
        "suggestions": list(_HOTKEY_SUGGESTIONS),
        "restart_required": True,
    }


@router.put("/keybinds")
async def put_keybind(body: KeybindBody, request: Request) -> dict[str, object]:
    from jarvis.trigger.hotkey import validate_hotkey

    action = body.action.strip().lower()
    if action not in KEYBIND_ACTIONS:
        raise HTTPException(status_code=400, detail=f"unknown action: {action}")
    hotkey = body.hotkey.strip().lower()

    # The backend is the authority — a browser key-capture cannot be trusted to
    # filter OS-critical / unusable combos (AltGr detection is unreliable there).
    ok, reason = validate_hotkey(hotkey)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)

    cfg = _config(request)
    trig = getattr(cfg, "trigger", None) if cfg is not None else None

    # Collision check: one chord can't both answer and hang up.
    for other_action, other_combo in _keybind_values(trig).items():
        if other_action != action and other_combo.strip().lower() == hotkey:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{hotkey}' is already bound to '{other_action}' — "
                    "pick a different combo."
                ),
            )

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

    return {
        "ok": True,
        "action": action,
        "hotkey": hotkey,
        "persisted": persisted,
        # Bindings are armed once at SpeechPipeline construction, so a keybind
        # change needs a voice restart to take effect.
        "restart_required": True,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/ui/test_keybinds_route.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Verify the legacy route still passes**

Run: `pytest tests/unit/ui/test_ptt_hotkey_route.py -v`
Expected: PASS (unchanged route).

- [ ] **Step 6: Commit**

```bash
git add jarvis/ui/web/settings_routes.py tests/unit/ui/test_keybinds_route.py
git commit -m "feat(api): /api/settings/keybinds for Call/Hangup/PTT with collision check"
```

---

### Task 4: Wiring — read Hangup from config at the two pipeline call sites

**Files:**
- Modify: `jarvis/ui/desktop_app.py` (the `SpeechPipeline(...)` call, the `hangup_hotkeys=("f1+f2",)` line ~1439)
- Modify: `jarvis/speech/watchdog.py` (the `SpeechPipeline(...)` call, the `hangup_hotkeys=("f1+f2",)` line ~128)

This change is byte-equivalent in default behaviour (`hotkey_hangup` defaults to `"f1+f2"`); it just makes the value configurable. Verified by inspection + the Task-1 config test (which covers the field) + ruff + the existing suite.

- [ ] **Step 1: Edit `desktop_app.py`**

In `jarvis/ui/desktop_app.py`, in the `SpeechPipeline(` construction, change:

```python
                hangup_hotkeys=("f1+f2",),
```

to:

```python
                hangup_hotkeys=(self.cfg.trigger.hotkey_hangup,),
```

- [ ] **Step 2: Edit `watchdog.py`**

In `jarvis/speech/watchdog.py`, in the `SpeechPipeline(` construction, change:

```python
        hangup_hotkeys=("f1+f2",),
```

to:

```python
        hangup_hotkeys=(config.trigger.hotkey_hangup,),
```

- [ ] **Step 3: Verify both call sites changed + nothing else hardcodes hangup**

Run: `grep -rn "hangup_hotkeys=" jarvis/`
Expected: both lines now read from `*.trigger.hotkey_hangup`; the only remaining `("f1+f2",)` literal is the field default in `jarvis/core/config.py` and the `SpeechPipeline.__init__` default.

- [ ] **Step 4: Lint + import smoke**

Run: `ruff check jarvis/ui/desktop_app.py jarvis/speech/watchdog.py`
Run: `python -c "import jarvis.speech.watchdog, jarvis.ui.desktop_app; print('import ok')"`
Expected: ruff clean; `import ok`.

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/desktop_app.py jarvis/speech/watchdog.py
git commit -m "feat(voice): wire hangup hotkey from trigger.hotkey_hangup config"
```

---

### Task 5: Frontend hook — `useKeybinds`

**Files:**
- Modify: `jarvis/ui/web/frontend/src/hooks/useHotkey.ts` (add `useKeybinds` + types; keep `useHotkey` + `eventToCombo` for now)
- Test: `jarvis/ui/web/frontend/src/hooks/useKeybinds.test.ts` (create)

All commands in this task run from `jarvis/ui/web/frontend/`.

- [ ] **Step 1: Write the failing test**

Create `jarvis/ui/web/frontend/src/hooks/useKeybinds.test.ts`:

```typescript
import { renderHook, waitFor, act } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useKeybinds } from "./useHotkey";

const FULL = {
  keybinds: { call: "f3+f4", hangup: "f1+f2", ptt: "ctrl+right_alt+j" },
  defaults: { call: "f3+f4", hangup: "f1+f2", ptt: "ctrl+right_alt+j" },
  push_to_talk: true,
  suggestions: [],
  restart_required: true,
};

afterEach(() => vi.restoreAllMocks());

describe("useKeybinds", () => {
  it("loads keybinds from the API", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => FULL }),
    );
    const { result } = renderHook(() => useKeybinds());
    await waitFor(() => expect(result.current.config).not.toBeNull());
    expect(result.current.config?.keybinds.call).toBe("f3+f4");
  });

  it("PUTs the chosen action + combo on save", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => FULL })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          ok: true,
          action: "hangup",
          hotkey: "ctrl+shift+h",
          persisted: true,
          restart_required: true,
        }),
      })
      .mockResolvedValue({ ok: true, json: async () => FULL });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useKeybinds());
    await waitFor(() => expect(result.current.config).not.toBeNull());
    await act(async () => {
      await result.current.saveKeybind("hangup", "ctrl+shift+h");
    });

    const putCall = fetchMock.mock.calls.find((c) => c[1]?.method === "PUT");
    expect(putCall?.[0]).toBe("/api/settings/keybinds");
    expect(JSON.parse(putCall?.[1].body)).toMatchObject({
      action: "hangup",
      hotkey: "ctrl+shift+h",
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/hooks/useKeybinds.test.ts`
Expected: FAIL — `useKeybinds` is not exported from `./useHotkey`.

- [ ] **Step 3: Add `useKeybinds` + types to `useHotkey.ts`**

Append to `jarvis/ui/web/frontend/src/hooks/useHotkey.ts` (after the existing `useHotkey` function, keeping `eventToCombo` at the bottom):

```typescript
export type KeybindAction = "call" | "hangup" | "ptt";

/** Response of GET /api/settings/keybinds. */
export interface KeybindsConfig {
  keybinds: Record<KeybindAction, string>;
  defaults: Record<KeybindAction, string>;
  push_to_talk: boolean;
  suggestions: string[];
  restart_required: boolean;
}

/** Result of a successful PUT /api/settings/keybinds. */
export interface KeybindSaveResult {
  ok: boolean;
  action: KeybindAction;
  hotkey: string;
  persisted: boolean;
  restart_required: boolean;
}

/**
 * Loads /api/settings/keybinds and exposes saveKeybind(action, combo). Mirrors
 * useHotkey's fetch/error/loading shape but covers all three voice keybinds
 * (Call / Hangup / Talk-PTT). A rejected save (unsafe combo or a collision with
 * another action) throws with the backend's reason. After a successful save it
 * dispatches 'jarvis:keybinds-changed'.
 */
export function useKeybinds() {
  const [config, setConfig] = useState<KeybindsConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/keybinds");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: KeybindsConfig = await res.json();
      setConfig(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
    const onChanged = () => void refetch();
    window.addEventListener("jarvis:keybinds-changed", onChanged);
    return () => {
      window.removeEventListener("jarvis:keybinds-changed", onChanged);
    };
  }, [refetch]);

  const saveKeybind = useCallback(
    async (action: KeybindAction, hotkey: string): Promise<KeybindSaveResult> => {
      const res = await fetch("/api/settings/keybinds", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, hotkey, persist: true }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      window.dispatchEvent(new CustomEvent("jarvis:keybinds-changed"));
      return body as KeybindSaveResult;
    },
    [],
  );

  return { config, loading, error, refetch, saveKeybind };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/hooks/useKeybinds.test.ts`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/hooks/useHotkey.ts jarvis/ui/web/frontend/src/hooks/useKeybinds.test.ts
git commit -m "feat(ui): useKeybinds hook for Call/Hangup/PTT keybind editing"
```

---

### Task 6: Frontend UI — Keybinds section in Settings

**Files:**
- Modify: `jarvis/ui/web/frontend/src/views/SettingsView.tsx` (replace `HotkeyPanel` with `KeybindsPanel` + `KeybindRow`; swap the `<HotkeyPanel />` usage at ~line 93; update the import; remove the now-dead `useHotkey` import)
- Modify: `jarvis/ui/web/frontend/src/hooks/useHotkey.ts` (remove the now-unused `useHotkey` function; keep `eventToCombo`, `useKeybinds`, and the types)
- Test: `jarvis/ui/web/frontend/src/views/KeybindsPanel.test.tsx` (create)

All commands in this task run from `jarvis/ui/web/frontend/`.

- [ ] **Step 1: Write the failing test**

Create `jarvis/ui/web/frontend/src/views/KeybindsPanel.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KeybindsPanel } from "./SettingsView";

const FULL = {
  keybinds: { call: "f3+f4", hangup: "f1+f2", ptt: "ctrl+right_alt+j" },
  defaults: { call: "f3+f4", hangup: "f1+f2", ptt: "ctrl+right_alt+j" },
  push_to_talk: true,
  suggestions: [],
  restart_required: true,
};

afterEach(() => vi.restoreAllMocks());

describe("KeybindsPanel", () => {
  it("renders one row per voice action with its current combo", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => FULL }),
    );
    render(<KeybindsPanel />);
    // Three capture fields render the three current combos (formatted).
    await waitFor(() => expect(screen.getByText("F3 + F4")).toBeTruthy());
    expect(screen.getByText("F1 + F2")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/views/KeybindsPanel.test.tsx`
Expected: FAIL — `KeybindsPanel` is not exported from `./SettingsView`.

- [ ] **Step 3: Replace `HotkeyPanel` with `KeybindsPanel` + `KeybindRow`**

In `jarvis/ui/web/frontend/src/views/SettingsView.tsx`:

(a) Change the import on line ~19 from:

```tsx
import { useHotkey, eventToCombo } from "@/hooks/useHotkey";
```

to:

```tsx
import {
  useKeybinds,
  eventToCombo,
  type KeybindAction,
  type KeybindsConfig,
  type KeybindSaveResult,
} from "@/hooks/useHotkey";
```

(b) Change the usage at line ~93 from `<HotkeyPanel />` to `<KeybindsPanel />`.

(c) Delete the entire `function HotkeyPanel() { ... }` block (~lines 651–798) and replace it with:

```tsx
const _KEYBIND_ROWS: { action: KeybindAction; labelKey: string }[] = [
  { action: "call", labelKey: "settings_view.keybinds.call_label" },
  { action: "hangup", labelKey: "settings_view.keybinds.hangup_label" },
  { action: "ptt", labelKey: "settings_view.keybinds.talk_label" },
];

/**
 * Editable voice keybinds: Call / Hangup / Talk-PTT, one row each. The user
 * clicks Record and presses a combination (captured via eventToCombo), or
 * resets to default, then saves. The backend validator is the authority — an
 * unsafe combo or a collision with another action is rejected with a reason
 * shown as a toast. A successful save surfaces a restart-required hint.
 */
export function KeybindsPanel() {
  const t = useT();
  const { config, loading, error, saveKeybind } = useKeybinds();

  return (
    <div className="mt-2 rounded-lg border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <Keyboard className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <h4 className="font-display text-sm font-semibold">
            {t("settings_view.keybinds.title")}
          </h4>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("settings_view.keybinds.description")}
          </p>
          {error && <p className="mt-3 text-xs text-destructive">{error}</p>}
          <div className="mt-4 space-y-3">
            {_KEYBIND_ROWS.map((row) => (
              <KeybindRow
                key={row.action}
                action={row.action}
                label={t(row.labelKey)}
                config={config}
                loading={loading}
                onSave={saveKeybind}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function KeybindRow({
  action,
  label,
  config,
  loading,
  onSave,
}: {
  action: KeybindAction;
  label: string;
  config: KeybindsConfig | null;
  loading: boolean;
  onSave: (a: KeybindAction, h: string) => Promise<KeybindSaveResult>;
}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const current = config?.keybinds[action] ?? "";
  const def = config?.defaults[action];

  const [combo, setCombo] = useState("");
  const [capturing, setCapturing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (config) setCombo(config.keybinds[action]);
  }, [config, action]);

  function onCaptureKeyDown(e: React.KeyboardEvent) {
    if (!capturing) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.key === "Escape") {
      setCapturing(false);
      return;
    }
    const next = eventToCombo(e);
    if (next) {
      setCombo(next);
      setCapturing(false);
      setSaved(false);
    }
  }

  async function onSaveClick() {
    const trimmed = combo.trim().toLowerCase();
    if (!trimmed) return;
    setSaving(true);
    setSaved(false);
    try {
      const res = await onSave(action, trimmed);
      setSaved(res.restart_required);
      pushToast("success", t("settings_view.keybinds.saved"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const dirty = !!config && combo.trim().toLowerCase() !== current;
  const showReset = !!def && combo.trim().toLowerCase() !== def;

  return (
    <div className="rounded-md border border-border/60 bg-background/40 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-foreground">{label}</span>
        {showReset && (
          <button
            type="button"
            className="text-[11px] text-muted-foreground underline hover:text-foreground"
            onClick={() => {
              if (def) {
                setCombo(def);
                setSaved(false);
              }
            }}
          >
            {t("settings_view.keybinds.reset")}
          </button>
        )}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={() => setCapturing(true)}
          onKeyDown={onCaptureKeyDown}
          onBlur={() => setCapturing(false)}
          disabled={loading}
          className={`flex-1 rounded-md border px-3 py-2 text-left font-mono text-sm transition-colors focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50 ${
            capturing
              ? "border-primary bg-primary/10 text-primary"
              : "border-input bg-background"
          }`}
        >
          {capturing
            ? t("settings_view.keybinds.recording")
            : combo
              ? formatCombo(combo)
              : "—"}
        </button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setCapturing(true)}
          disabled={loading}
        >
          {t("settings_view.keybinds.record")}
        </Button>
        <Button size="sm" onClick={onSaveClick} disabled={saving || loading || !dirty}>
          {saving ? t("settings_view.saving") : t("settings_view.keybinds.save")}
        </Button>
      </div>
      {saved && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          {t("settings_view.keybinds.restart_required")}
        </p>
      )}
    </div>
  );
}
```

(d) In `jarvis/ui/web/frontend/src/hooks/useHotkey.ts`, delete the now-unused `useHotkey` function (the `HotkeyConfig` / `HotkeySaveResult` interfaces it used can go too). Keep `eventToCombo`, `useKeybinds`, and the keybind types.

- [ ] **Step 4: Run the new component test + typecheck**

Run: `npm run test -- src/views/KeybindsPanel.test.tsx`
Expected: PASS (1 passed).
Run: `npx tsc --noEmit`
Expected: no errors (the removed `useHotkey` import has no remaining references).

- [ ] **Step 5: Run the full frontend test + build**

Run: `npm run test`
Run: `npm run build`
Expected: all vitest green; build succeeds into `jarvis/ui/web/dist`.

- [ ] **Step 6: Commit**

```bash
git add jarvis/ui/web/frontend/src/views/SettingsView.tsx jarvis/ui/web/frontend/src/views/KeybindsPanel.test.tsx jarvis/ui/web/frontend/src/hooks/useHotkey.ts
git commit -m "feat(ui): Keybinds settings section (Call/Hangup/Talk) replacing HotkeyPanel"
```

---

### Task 7: i18n strings

**Files:**
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/en.json`
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/de.json`
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/es.json`

In each file, inside the `"settings_view"` object, find the existing `"hotkey": { ... }` block and add a sibling `"keybinds"` block right after it (same nesting level). English is the source; `de`/`es` are translations.

- [ ] **Step 1: Add the `keybinds` block to `en.json`**

```json
    "keybinds": {
      "title": "Voice Keybinds",
      "description": "Set the keys for Call, Hangup and Talk (push-to-talk). Click Record and press your combination, or reset to default. Changes apply after a restart.",
      "call_label": "Call (answer / start talking)",
      "hangup_label": "Hangup",
      "talk_label": "Talk / Push-to-talk",
      "record": "Record",
      "recording": "Press your keys now… (Esc to cancel)",
      "save": "Save",
      "saved": "Keybind saved",
      "reset": "Reset to default",
      "restart_required": "Restart required to take effect."
    },
```

- [ ] **Step 2: Add the `keybinds` block to `de.json`**

```json
    "keybinds": {
      "title": "Sprach-Tastenkürzel",
      "description": "Lege die Tasten für Anruf, Auflegen und Sprechen (Push-to-Talk) fest. Auf „Aufnehmen“ klicken und Kombination drücken, oder auf Standard zurücksetzen. Änderungen greifen nach einem Neustart.",
      "call_label": "Anruf (annehmen / Sprechen starten)",
      "hangup_label": "Auflegen",
      "talk_label": "Sprechen / Push-to-Talk",
      "record": "Aufnehmen",
      "recording": "Jetzt Tasten drücken… (Esc zum Abbrechen)",
      "save": "Speichern",
      "saved": "Tastenkürzel gespeichert",
      "reset": "Auf Standard zurücksetzen",
      "restart_required": "Neustart erforderlich, damit es wirkt."
    },
```

- [ ] **Step 3: Add the `keybinds` block to `es.json`**

```json
    "keybinds": {
      "title": "Atajos de voz",
      "description": "Configura las teclas para Llamar, Colgar y Hablar (pulsar para hablar). Haz clic en Grabar y pulsa tu combinación, o restablece el valor predeterminado. Los cambios se aplican tras reiniciar.",
      "call_label": "Llamar (responder / empezar a hablar)",
      "hangup_label": "Colgar",
      "talk_label": "Hablar / Pulsar para hablar",
      "record": "Grabar",
      "recording": "Pulsa tus teclas ahora… (Esc para cancelar)",
      "save": "Guardar",
      "saved": "Atajo guardado",
      "reset": "Restablecer predeterminado",
      "restart_required": "Requiere reinicio para aplicarse."
    },
```

- [ ] **Step 4: Validate JSON + rebuild**

Run (from repo root): `python -c "import json,io;[json.load(io.open(f'jarvis/ui/web/frontend/src/i18n/locales/{l}.json',encoding='utf-8')) for l in ('en','de','es')];print('json ok')"`
Expected: `json ok` (no trailing-comma / syntax errors).
Run (from `jarvis/ui/web/frontend/`): `npm run build`
Expected: build succeeds; the Keybinds section now renders localized labels.

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/i18n/locales/en.json jarvis/ui/web/frontend/src/i18n/locales/de.json jarvis/ui/web/frontend/src/i18n/locales/es.json
git commit -m "i18n(keybinds): Voice Keybinds section strings (en/de/es)"
```

---

### Task 8: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Backend tests (touched areas)**

Run:
```bash
pytest tests/unit/core/test_trigger_keybinds.py tests/unit/core/test_config_writer_keybinds.py tests/unit/ui/test_keybinds_route.py tests/unit/ui/test_ptt_hotkey_route.py tests/unit/trigger/ -v
```
Expected: all PASS.

- [ ] **Step 2: Ruff on touched Python**

Run: `ruff check jarvis/core/config.py jarvis/core/config_writer.py jarvis/ui/web/settings_routes.py jarvis/ui/desktop_app.py jarvis/speech/watchdog.py`
Expected: no new findings.

- [ ] **Step 3: Frontend test + typecheck + build**

Run (from `jarvis/ui/web/frontend/`): `npm run test && npx tsc --noEmit && npm run build`
Expected: all green; build into `jarvis/ui/web/dist`.

- [ ] **Step 4: Manual smoke (operator)**

1. Restart Jarvis (the new `dist` + config wiring need a fresh boot).
2. Open Settings → Keybinds. Confirm three rows (Call / Hangup / Talk) show `F3 + F4`, `F1 + F2`, `Ctrl + Right Alt + J`.
3. Record a new Call combo (e.g. `F7 + F8`), Save → toast + "restart required".
4. Try to set Hangup to the same combo → rejected with a collision message.
5. Restart, confirm in the log: `Hotkey-Trigger armed (...): call=[f7+f8], hangup=[f1+f2], ptt=[ctrl+right_alt+j]`, and that pressing the new Call combo logs `📞 CALL via Hotkey`.

- [ ] **Step 5: Final commit (if any verification fixups were needed)**

```bash
git add -- <only the files you changed during fixups>
git commit -m "test(keybinds): verification fixups"
```

---

## Self-Review (completed during authoring)

- **Spec coverage:** Config fields (T1), `resolve_hotkeys` (T1), hangup wiring (T4), persistence + shared vocabulary (T2), API + collision check + ptt alias kept (T3), hook (T5), UI section + KeybindRow (T6), i18n en/de/es (T7), tests throughout, full verification (T8). All spec sections mapped.
- **Placeholder scan:** none — every code/step block is concrete.
- **Type consistency:** `KEYBIND_ACTIONS`/`KEYBIND_TOML_KEY` (Python) ↔ `KeybindAction` (TS); `set_keybind(action, hotkey)` signature consistent across Task 2 (def), Task 3 (call + monkeypatch), Task 5 (PUT body); `useKeybinds`/`saveKeybind`/`KeybindsConfig`/`KeybindSaveResult` names consistent across Tasks 5 and 6; `hotkey_call`/`hotkey_hangup` field names == toml keys, used identically in config (T1), writer (T2), route getattr/setattr (T3), wiring (T4).
- **Out of scope kept out:** no live re-arm, no checker-lifecycle change, no generic action table, no mute key.
