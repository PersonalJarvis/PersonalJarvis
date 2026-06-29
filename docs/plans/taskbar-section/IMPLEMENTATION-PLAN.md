# Taskbar Section + Dictation Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A dedicated "Taskbar" sidebar section owning the overlay-style selector plus two toggles — "Show bar at all times" (live `bar_persistent`) and "Mute music while dictating" (new pycaw per-app audio ducking).

**Architecture:** A new `jarvis/audio/ducking/` subsystem (protocol + Windows-pycaw + null + factory + a bus-subscribing controller) mutes other apps' audio on `VoiceSessionStarted` and restores on `VoiceSessionEnded`, excluding our own PID (protects Jarvis TTS). Two boolean settings routes persist via `config_writer` and live-apply via `DesktopApp`. A new React `TaskbarView` hosts the moved style selector + two `Switch` toggles.

**Tech Stack:** Python 3.11, pycaw + comtypes (Windows-only, `[desktop]` extra), FastAPI, pydantic v2, React + Radix Switch, pytest, vitest.

**Verified facts (file:line):** session events `VoiceSessionStarted` / `VoiceSessionEnded` (`jarvis/core/events.py`); EventBus `subscribe(type, async_handler)` (`jarvis/core/bus.py`); bridge bootstrap + `OrbBusBridge(...).attach()` in `jarvis/ui/desktop_app.py` (~1290); `config_writer._patch_table(path, table, key, value)`; `_config(request)` + autostart route pattern in `settings_routes.py`; `Sidebar.tsx` `NAV_ITEMS`, `MainView.tsx` switch, `store/events.ts` SectionId/SECTION_IDS/SECTION_LABELS; `OverlayStylePanel` in `SettingsView.tsx`; Radix `Switch` (checked/onCheckedChange/disabled); `useAutostart.ts` hook pattern. pycaw exists as `scripts/diag_mic_mute.py` but is NOT a declared dependency.

**Touch-files carrying parallel-session edits (edit additively, do NOT commit):** `config.py`, `config_writer.py`, `settings_routes.py`, `desktop_app.py`, `pyproject.toml`, the three i18n json, `SettingsView.tsx`. Commit only new isolated files.

---

## File Structure

| File | Responsibility |
|---|---|
| `jarvis/audio/ducking/__init__.py` | Package: re-export `make_audio_duck_controller`, `AudioDuckController` |
| `jarvis/audio/ducking/protocol.py` | `AudioDucker` Protocol (`mute_others`/`restore`) |
| `jarvis/audio/ducking/null.py` | `NullDucker` — logged no-op |
| `jarvis/audio/ducking/windows.py` | `WindowsPycawDucker` — pycaw mute-others/restore |
| `jarvis/audio/ducking/factory.py` | `make_audio_ducker()` (platform/capability select) |
| `jarvis/audio/ducking/controller.py` | `AudioDuckController` — bus subscriber + lifecycle |
| `jarvis/core/config.py` *(mod)* | `DuckingConfig` + `JarvisConfig.ducking` |
| `jarvis/core/config_writer.py` *(mod)* | `set_bar_persistent`, `set_mute_music` |
| `jarvis/ui/web/settings_routes.py` *(mod)* | `bar-persistent` + `mute-music` GET/PUT |
| `jarvis/ui/desktop_app.py` *(mod)* | `set_bar_persistent` (live) + ducker wiring + shutdown restore |
| `pyproject.toml` *(mod)* | `[desktop]` extra: pycaw, comtypes (win32) |
| `views/taskbar/TaskbarView.tsx` | New section view (moved selector + 2 toggles) |
| `hooks/useBarPersistent.ts`, `hooks/useMuteMusic.ts` | Boolean GET/PUT hooks |
| `Sidebar.tsx` / `MainView.tsx` / `store/events.ts` *(mod)* | Register the new section |
| `SettingsView.tsx` *(mod)* | Remove `OverlayStylePanel` |
| i18n en/de/es.json *(mod)* | `nav.taskbar` + `taskbar_view.*` |

---

## Task 1: `[ducking]` config + config_writer setters

**Files:** Modify `jarvis/core/config.py`, `jarvis/core/config_writer.py`; Test `tests/unit/core/test_config_writer_taskbar.py`

- [ ] **Step 1 — failing test**
```python
# tests/unit/core/test_config_writer_taskbar.py
from __future__ import annotations
import tomllib
from jarvis.core import config_writer
from jarvis.core.config import DuckingConfig, JarvisConfig

def test_ducking_config_defaults():
    d = DuckingConfig()
    assert d.enabled is False
    assert d.restore_delay_ms == 400
    assert JarvisConfig().ducking.enabled is False

def test_set_mute_music_round_trip(tmp_path):
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text('[ui]\norb_style = "jarvis_bar"\n', encoding="utf-8")
    config_writer.set_mute_music(True, path=cfg)
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["ducking"]["enabled"] is True

def test_set_bar_persistent_round_trip(tmp_path):
    cfg = tmp_path / "jarvis.toml"
    cfg.write_text('[ui]\norb_style = "jarvis_bar"\n', encoding="utf-8")
    config_writer.set_bar_persistent(False, path=cfg)
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert data["ui"]["bar_persistent"] is False
```
- [ ] **Step 2 — run, expect fail** `py -3.11 -m pytest tests/unit/core/test_config_writer_taskbar.py -q`
- [ ] **Step 3 — implement config model.** In `config.py`, add a model (near `UIConfig`) and a field on `JarvisConfig`:
```python
class DuckingConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    # "Mute music while dictating" — mute other apps' audio during a voice session.
    enabled: bool = False
    # Grace before restoring other apps' volume (lets the TTS tail finish).
    restore_delay_ms: int = 400
    # App process names never to mute (e.g. "Discord.exe"). Empty = mute all others.
    never_mute: list[str] = Field(default_factory=list)
```
Add to `JarvisConfig`: `ducking: DuckingConfig = Field(default_factory=DuckingConfig)`.
- [ ] **Step 4 — implement setters.** In `config_writer.py`, after `set_overlay_style`:
```python
def set_bar_persistent(enabled: bool, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist [ui] bar_persistent (show the bar at all times). TOML-only."""
    _patch_table(path, "ui", "bar_persistent", bool(enabled))


def set_mute_music(enabled: bool, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist [ducking] enabled (mute music while dictating). TOML-only."""
    _patch_table(path, "ducking", "enabled", bool(enabled))
```
- [ ] **Step 5 — run, expect PASS**
- [ ] **Step 6 — NO commit** (config.py/config_writer.py carry parallel edits; leave uncommitted).

## Task 2: ducking protocol + null + factory

**Files:** Create `jarvis/audio/ducking/__init__.py`, `protocol.py`, `null.py`, `factory.py`; Test `tests/unit/audio/test_ducking_factory.py`

- [ ] **Step 1 — failing test**
```python
# tests/unit/audio/test_ducking_factory.py
from __future__ import annotations
from jarvis.audio.ducking.factory import make_audio_ducker
from jarvis.audio.ducking.null import NullDucker

def test_factory_returns_null_when_pycaw_absent(monkeypatch):
    monkeypatch.setattr("jarvis.audio.ducking.factory._pycaw_available", lambda: False)
    assert isinstance(make_audio_ducker(), NullDucker)

def test_null_ducker_is_noop():
    d = NullDucker()
    assert d.mute_others(own_pid=123, never=frozenset()) == []
    d.restore([1, 2, 3])  # must not raise
```
- [ ] **Step 2 — run, expect fail**
- [ ] **Step 3 — implement**
```python
# jarvis/audio/ducking/protocol.py
from __future__ import annotations
from typing import Protocol

class AudioDucker(Protocol):
    def mute_others(self, *, own_pid: int, never: frozenset[str]) -> list[int]:
        """Mute every other app's audio session; return the PIDs muted."""
        ...
    def restore(self, pids: list[int]) -> None:
        """Unmute exactly the given PIDs."""
        ...
```
```python
# jarvis/audio/ducking/null.py
from __future__ import annotations
import logging
log = logging.getLogger("jarvis.audio.ducking")

class NullDucker:
    """No-op ducker (non-Windows / pycaw absent). Audio ducking unavailable."""
    def mute_others(self, *, own_pid: int, never: frozenset[str]) -> list[int]:
        return []
    def restore(self, pids: list[int]) -> None:
        return None
```
```python
# jarvis/audio/ducking/factory.py
from __future__ import annotations
import logging
import sys
from jarvis.audio.ducking.null import NullDucker
from jarvis.audio.ducking.protocol import AudioDucker
log = logging.getLogger("jarvis.audio.ducking")

def _pycaw_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("pycaw") is not None

def make_audio_ducker() -> AudioDucker:
    """Windows + pycaw -> WindowsPycawDucker; else a logged NullDucker."""
    if sys.platform == "win32" and _pycaw_available():
        from jarvis.audio.ducking.windows import WindowsPycawDucker
        return WindowsPycawDucker()
    log.info("Audio ducking unavailable (platform=%s) -> no-op.", sys.platform)
    return NullDucker()
```
```python
# jarvis/audio/ducking/__init__.py
from __future__ import annotations
from jarvis.audio.ducking.controller import AudioDuckController, make_audio_duck_controller
__all__ = ["AudioDuckController", "make_audio_duck_controller"]
```
*(NB: `__init__` imports controller — write Task 4 before importing the package, or guard the import. For TDD order, create `__init__.py` with the exports only after Task 4. In Task 2 leave `__init__.py` empty.)*
- [ ] **Step 4 — run, expect PASS**
- [ ] **Step 5 — commit** `feat(audio): ducking protocol + null backend + factory`

## Task 3: WindowsPycawDucker (pycaw backend)

**Files:** Create `jarvis/audio/ducking/windows.py`. No unit test (real COM) — live sign-off + a smoke import.

- [ ] **Step 1 — implement** (lazy pycaw import; each session guarded)
```python
# jarvis/audio/ducking/windows.py
from __future__ import annotations
import logging
log = logging.getLogger("jarvis.audio.ducking")

class WindowsPycawDucker:
    """Mute/restore other apps' audio via the Windows Core Audio session API.

    Must be called from a thread that has done CoInitialize() (the controller
    runs these inside asyncio.to_thread + comtypes.CoInitialize).
    """
    def mute_others(self, *, own_pid: int, never: frozenset[str]) -> list[int]:
        from pycaw.pycaw import AudioUtilities
        muted: list[int] = []
        for session in AudioUtilities.GetAllSessions():
            try:
                pid = session.ProcessId
                if not pid or pid == own_pid:  # 0 = system sounds; own = our TTS
                    continue
                proc = session.Process
                if proc is not None and proc.name() in never:
                    continue
                vol = session.SimpleAudioVolume
                if not vol.GetMute():           # only mute currently-audible ones
                    vol.SetMute(1, None)
                    muted.append(pid)
            except Exception:  # noqa: BLE001 — COMError on protected sessions; skip
                log.debug("mute skip", exc_info=True)
        return muted

    def restore(self, pids: list[int]) -> None:
        from pycaw.pycaw import AudioUtilities
        want = set(pids)
        for session in AudioUtilities.GetAllSessions():
            try:
                if session.ProcessId in want:
                    session.SimpleAudioVolume.SetMute(0, None)
            except Exception:  # noqa: BLE001
                log.debug("restore skip", exc_info=True)
```
- [ ] **Step 2 — smoke (Windows)** `py -3.11 -c "import jarvis.audio.ducking.factory as f; print(type(f.make_audio_ducker()).__name__)"` → `WindowsPycawDucker` if pycaw installed, else `NullDucker`.
- [ ] **Step 3 — commit** `feat(audio): WindowsPycawDucker (per-app mute via Core Audio)`

## Task 4: AudioDuckController (bus lifecycle, TDD with fake ducker)

**Files:** Create `jarvis/audio/ducking/controller.py`; Test `tests/unit/audio/test_duck_controller.py`

- [ ] **Step 1 — failing test**
```python
# tests/unit/audio/test_duck_controller.py
from __future__ import annotations
from types import SimpleNamespace
from jarvis.audio.ducking.controller import AudioDuckController

class FakeDucker:
    def __init__(self): self.muted_calls = 0; self.restored = []
    def mute_others(self, *, own_pid, never): self.muted_calls += 1; return [111, 222]
    def restore(self, pids): self.restored.append(list(pids))

class FakeBus:
    def __init__(self): self.subs = {}
    def subscribe(self, ev, h): self.subs[ev.__name__] = h

def _cfg(enabled=True, delay=0):
    return SimpleNamespace(ducking=SimpleNamespace(enabled=enabled, restore_delay_ms=delay, never_mute=[]))

async def test_mutes_on_start_restores_on_end_when_enabled():
    d, bus = FakeDucker(), FakeBus()
    c = AudioDuckController(bus=bus, cfg=_cfg(enabled=True), ducker=d); c.attach()
    await bus.subs["VoiceSessionStarted"](object())
    assert d.muted_calls == 1 and c._muted == [111, 222]
    await bus.subs["VoiceSessionEnded"](object())
    assert d.restored == [[111, 222]] and c._muted == []

async def test_disabled_does_nothing():
    d, bus = FakeDucker(), FakeBus()
    c = AudioDuckController(bus=bus, cfg=_cfg(enabled=False), ducker=d); c.attach()
    await bus.subs["VoiceSessionStarted"](object())
    assert d.muted_calls == 0 and c._muted == []

async def test_set_enabled_false_midsession_restores():
    d, bus = FakeDucker(), FakeBus()
    c = AudioDuckController(bus=bus, cfg=_cfg(enabled=True), ducker=d); c.attach()
    await bus.subs["VoiceSessionStarted"](object())
    await c.set_enabled(False)
    assert d.restored == [[111, 222]] and c._muted == []

async def test_restore_is_idempotent():
    d, bus = FakeDucker(), FakeBus()
    c = AudioDuckController(bus=bus, cfg=_cfg(enabled=True), ducker=d); c.attach()
    await c.restore(); assert d.restored == []  # nothing muted -> no restore call
```
- [ ] **Step 2 — run, expect fail**
- [ ] **Step 3 — implement**
```python
# jarvis/audio/ducking/controller.py
from __future__ import annotations
import asyncio
import logging
import os
from typing import Any
from jarvis.core.events import VoiceSessionEnded, VoiceSessionStarted
from jarvis.audio.ducking.factory import make_audio_ducker
log = logging.getLogger("jarvis.audio.ducking")

class AudioDuckController:
    """Mutes other apps' audio for the duration of a voice session.

    Subscribes VoiceSessionStarted (mute) / VoiceSessionEnded (restore). The COM
    work runs in asyncio.to_thread with CoInitialize so it never blocks the loop.
    Own PID is excluded from the mute sweep -> Jarvis's own TTS is never muted.
    """
    def __init__(self, bus: Any, cfg: Any, ducker: Any) -> None:
        self._bus = bus
        self._cfg = cfg
        self._ducker = ducker
        self._muted: list[int] = []
        self._own_pid = os.getpid()
        self._lock = asyncio.Lock()

    def attach(self) -> None:
        try:
            self._bus.subscribe(VoiceSessionStarted, self._on_start)
            self._bus.subscribe(VoiceSessionEnded, self._on_end)
        except Exception:  # noqa: BLE001
            log.exception("AudioDuckController.attach failed")

    def _never(self) -> frozenset[str]:
        return frozenset(getattr(self._cfg.ducking, "never_mute", []) or [])

    async def _on_start(self, _ev: Any) -> None:
        if not getattr(self._cfg.ducking, "enabled", False):
            return
        async with self._lock:
            if self._muted:
                return
            self._muted = await self._run(self._ducker.mute_others,
                                          own_pid=self._own_pid, never=self._never())
            log.info("ducking: muted %d other session(s)", len(self._muted))

    async def _on_end(self, _ev: Any) -> None:
        delay = getattr(self._cfg.ducking, "restore_delay_ms", 400) / 1000.0
        if delay > 0:
            await asyncio.sleep(delay)
        await self._restore_locked()

    async def set_enabled(self, enabled: bool) -> None:
        # Live-apply the toggle; turning OFF mid-session restores now.
        try:
            self._cfg.ducking.enabled = enabled
        except Exception:  # noqa: BLE001
            pass
        if not enabled:
            await self._restore_locked()

    async def restore(self) -> None:
        await self._restore_locked()

    async def _restore_locked(self) -> None:
        async with self._lock:
            if not self._muted:
                return
            pids, self._muted = self._muted, []
            await self._run(self._ducker.restore, pids)
            log.info("ducking: restored %d session(s)", len(pids))

    async def _run(self, fn, *a, **k):
        # pycaw/COM is blocking -> off the loop, with CoInitialize on the worker.
        def _call():
            try:
                import comtypes
                comtypes.CoInitialize()
            except Exception:  # noqa: BLE001
                comtypes = None  # type: ignore
            try:
                return fn(*a, **k)
            finally:
                if 'comtypes' in dir() and comtypes is not None:
                    try: comtypes.CoUninitialize()
                    except Exception: pass
        try:
            return await asyncio.to_thread(_call)
        except Exception:  # noqa: BLE001
            log.exception("ducking COM call failed")
            return []

def make_audio_duck_controller(bus: Any, cfg: Any) -> AudioDuckController:
    return AudioDuckController(bus=bus, cfg=cfg, ducker=make_audio_ducker())
```
Then write `__init__.py` exports (Task 2 note).
- [ ] **Step 4 — run, expect PASS** (asyncio_mode=auto handles the async tests)
- [ ] **Step 5 — commit** `feat(audio): AudioDuckController (session-boundary ducking)`

## Task 5: pyproject.toml [desktop] extra

**Files:** Modify `pyproject.toml` `[project.optional-dependencies].desktop`

- [ ] **Step 1 — add** alongside the existing win32 entries:
```toml
  "pycaw>=20240210; sys_platform == 'win32'",
  "comtypes>=1.2; sys_platform == 'win32'",
```
- [ ] **Step 2 — verify** `py -3.11 -c "import jarvis.audio.ducking; print('ok')"` (base import, no pycaw needed).
- [ ] **Step 3 — NO commit** (pyproject.toml parallel-edited).

## Task 6: settings routes (bar-persistent + mute-music)

**Files:** Modify `jarvis/ui/web/settings_routes.py`; Test `tests/unit/ui/test_taskbar_routes.py`

- [ ] **Step 1 — failing test**
```python
# tests/unit/ui/test_taskbar_routes.py
from __future__ import annotations
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jarvis.ui.web.settings_routes import router

def _client(*, bar_persistent=True, ducking_enabled=False, desktop=None):
    app = FastAPI(); app.include_router(router)
    app.state.config = SimpleNamespace(
        ui=SimpleNamespace(bar_persistent=bar_persistent),
        ducking=SimpleNamespace(enabled=ducking_enabled),
    )
    if desktop is not None:
        app.state.desktop_app = desktop
    return TestClient(app)

def test_get_bar_persistent():
    r = _client(bar_persistent=True).get("/api/settings/bar-persistent")
    assert r.status_code == 200 and r.json()["enabled"] is True

def test_put_bar_persistent_live(monkeypatch):
    import jarvis.core.config_writer as cw
    monkeypatch.setattr(cw, "set_bar_persistent", lambda v, **k: None)
    applied = {}
    desktop = SimpleNamespace(set_bar_persistent=lambda v: applied.setdefault("v", v) or {"applied_live": True})
    r = _client(desktop=desktop).put("/api/settings/bar-persistent", json={"enabled": False})
    assert r.status_code == 200 and applied["v"] is False
    assert r.json()["applied_live"] is True

def test_get_mute_music():
    r = _client(ducking_enabled=False).get("/api/settings/mute-music")
    assert r.status_code == 200 and r.json()["enabled"] is False

def test_put_mute_music(monkeypatch):
    import jarvis.core.config_writer as cw
    monkeypatch.setattr(cw, "set_mute_music", lambda v, **k: None)
    seen = {}
    async def _set_enabled(v): seen["v"] = v
    desktop = SimpleNamespace(_ducker=SimpleNamespace(set_enabled=_set_enabled))
    r = _client(desktop=desktop).put("/api/settings/mute-music", json={"enabled": True})
    assert r.status_code == 200 and seen["v"] is True
```
- [ ] **Step 2 — run, expect fail**
- [ ] **Step 3 — implement** (append after the existing routes)
```python
class BoolToggleBody(BaseModel):
    enabled: bool = Field(...)


@router.get("/bar-persistent")
async def get_bar_persistent(request: Request) -> dict[str, object]:
    cfg = _config(request)
    ui = getattr(cfg, "ui", None)
    return {"enabled": bool(getattr(ui, "bar_persistent", True))}


@router.put("/bar-persistent")
async def put_bar_persistent(body: BoolToggleBody, request: Request) -> dict[str, object]:
    enabled = bool(body.enabled)
    cfg = _config(request)
    ui = getattr(cfg, "ui", None)
    if ui is not None:
        try: ui.bar_persistent = enabled
        except Exception as exc: log.debug("in-memory bar_persistent skip: %s", exc)
    persisted = False
    try:
        from jarvis.core import config_writer
        config_writer.set_bar_persistent(enabled); persisted = True
    except Exception as exc:  # noqa: BLE001
        log.warning("bar_persistent persist failed: %s", exc)
    applied_live = False
    desktop = getattr(request.app.state, "desktop_app", None)
    fn = getattr(desktop, "set_bar_persistent", None)
    if callable(fn):
        try:
            res = await asyncio.to_thread(fn, enabled)
            applied_live = bool(res.get("applied_live")) if isinstance(res, dict) else bool(res)
        except Exception as exc:  # noqa: BLE001
            log.warning("bar_persistent live-apply failed: %s", exc)
    return {"ok": True, "enabled": enabled, "persisted": persisted,
            "applied_live": applied_live, "restart_required": not applied_live}


@router.get("/mute-music")
async def get_mute_music(request: Request) -> dict[str, object]:
    cfg = _config(request)
    duck = getattr(cfg, "ducking", None)
    return {"enabled": bool(getattr(duck, "enabled", False))}


@router.put("/mute-music")
async def put_mute_music(body: BoolToggleBody, request: Request) -> dict[str, object]:
    enabled = bool(body.enabled)
    cfg = _config(request)
    duck = getattr(cfg, "ducking", None)
    if duck is not None:
        try: duck.enabled = enabled
        except Exception as exc: log.debug("in-memory ducking.enabled skip: %s", exc)
    persisted = False
    try:
        from jarvis.core import config_writer
        config_writer.set_mute_music(enabled); persisted = True
    except Exception as exc:  # noqa: BLE001
        log.warning("mute_music persist failed: %s", exc)
    applied_live = False
    desktop = getattr(request.app.state, "desktop_app", None)
    ducker = getattr(desktop, "_ducker", None)
    setter = getattr(ducker, "set_enabled", None)
    if callable(setter):
        try:
            await setter(enabled); applied_live = True
        except Exception as exc:  # noqa: BLE001
            log.warning("mute_music live-apply failed: %s", exc)
    return {"ok": True, "enabled": enabled, "persisted": persisted, "applied_live": applied_live}
```
- [ ] **Step 4 — run, expect PASS**
- [ ] **Step 5 — NO commit** (settings_routes.py parallel-edited).

## Task 7: DesktopApp.set_bar_persistent (live) + ducker wiring

**Files:** Modify `jarvis/ui/desktop_app.py`; Test `tests/unit/ui/test_desktop_bar_persistent.py`

- [ ] **Step 1 — failing test**
```python
# tests/unit/ui/test_desktop_bar_persistent.py
from __future__ import annotations
from types import SimpleNamespace
from jarvis.ui.desktop_app import DesktopApp

class FakeBar:
    def __init__(self): self._persistent=True; self.shown=None; self.hidden=False; self._mode="idle"
    def show(self, m): self.shown=m
    def hide(self): self.hidden=True

def _app(bar, bridge):
    a = DesktopApp.__new__(DesktopApp)
    a.cfg = SimpleNamespace(ui=SimpleNamespace(bar_persistent=True))
    a._orb = bar; a._bridge = bridge
    return a

def test_set_bar_persistent_off_hides_when_idle():
    bar = FakeBar(); bridge = SimpleNamespace(_hide_on_idle=False)
    res = _app(bar, bridge).set_bar_persistent(False)
    assert bar._persistent is False and bridge._hide_on_idle is True
    assert bar.hidden is True and res["applied_live"] is True

def test_set_bar_persistent_on_shows_idle():
    bar = FakeBar(); bar._persistent=False; bridge = SimpleNamespace(_hide_on_idle=True)
    res = _app(bar, bridge).set_bar_persistent(True)
    assert bar._persistent is True and bridge._hide_on_idle is False
    assert bar.shown == "idle" and res["applied_live"] is True

def test_set_bar_persistent_no_bridge_persisted_only():
    a = DesktopApp.__new__(DesktopApp); a.cfg=SimpleNamespace(ui=SimpleNamespace(bar_persistent=True))
    a._orb=None; a._bridge=None
    assert a.set_bar_persistent(False) == {"ok": True, "applied_live": False}
```
- [ ] **Step 2 — run, expect fail**
- [ ] **Step 3 — implement** `set_bar_persistent` (near `swap_overlay`):
```python
    def set_bar_persistent(self, enabled: bool) -> dict[str, object]:
        """Live-toggle 'show bar at all times' (bar_persistent) without a restart.

        Flips the bar's _persistent flag + the bridge's _hide_on_idle, then shows
        the idle pill (enabled) or hides it when currently idle (disabled). Only
        flag flips — no new Tk root — so it is safe and immediate.
        """
        from loguru import logger
        enabled = bool(enabled)
        bar = getattr(self, "_orb", None)
        bridge = getattr(self, "_bridge", None)
        try:
            self.cfg.ui.bar_persistent = enabled
        except Exception:  # noqa: BLE001
            pass
        if bar is None or bridge is None:
            return {"ok": True, "applied_live": False}
        try:
            if hasattr(bar, "_persistent"):
                bar._persistent = enabled
            bridge._hide_on_idle = not enabled
            mode = getattr(bar, "_mode", "idle")
            if enabled:
                bar.show("idle")
            elif mode == "idle":
                bar.hide()
            logger.info("bar_persistent set live to {}.", enabled)
            return {"ok": True, "applied_live": True}
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning("set_bar_persistent failed")
            return {"ok": True, "applied_live": False}
```
- [ ] **Step 4 — wire the ducker** in the voice-stack bootstrap, right after `OrbBusBridge(...).attach()`:
```python
            try:
                from jarvis.audio.ducking import make_audio_duck_controller
                self._ducker = make_audio_duck_controller(bus=bus, cfg=self.cfg)
                self._ducker.attach()
            except Exception as exc:  # noqa: BLE001
                from loguru import logger
                logger.opt(exception=exc).warning("Audio ducking not started")
                self._ducker = None
```
- [ ] **Step 5 — shutdown restore.** In `shutdown()`, near the overlay teardown:
```python
        ducker = getattr(self, "_ducker", None)
        if ducker is not None:
            try:
                import asyncio
                asyncio.run(ducker.restore())
            except Exception:  # noqa: BLE001
                pass
```
*(During execution: if `shutdown` already has a running loop / a loop ref, use `run_coroutine_threadsafe` instead of `asyncio.run`; pick the pattern the surrounding teardown uses.)*
- [ ] **Step 6 — run test, expect PASS** + import smoke `py -3.11 -c "import jarvis.ui.desktop_app"`.
- [ ] **Step 7 — NO commit** (desktop_app.py parallel-edited).

## Task 8: register the Taskbar section (store/events + Sidebar + MainView)

**Files:** Modify `store/events.ts`, `Sidebar.tsx`, `MainView.tsx`

- [ ] **Step 1** — `store/events.ts`: add `"taskbar"` to the `SectionId` union, the `SECTION_IDS` array, and `SECTION_LABELS` (`"taskbar": "Taskbar"`). **All three** (AP-4).
- [ ] **Step 2** — `Sidebar.tsx`: import an icon (e.g. `LayoutPanelTop` from lucide-react) and add to `NAV_ITEMS`: `{ id: "taskbar", labelKey: "nav.taskbar", icon: LayoutPanelTop }`.
- [ ] **Step 3** — `MainView.tsx`: `import { TaskbarView } from "@/views/taskbar/TaskbarView";` + `case "taskbar": return <TaskbarView />;`.
- [ ] **Step 4 — commit** (these are isolated additions) `feat(ui): register Taskbar sidebar section`.

## Task 9: TaskbarView + hooks + i18n + remove from Settings

**Files:** Create `views/taskbar/TaskbarView.tsx`, `hooks/useBarPersistent.ts`, `hooks/useMuteMusic.ts`; Modify `SettingsView.tsx`, en/de/es.json

- [ ] **Step 1 — hooks** (mirror `useAutostart`): each does `GET /api/settings/<x>` on mount and `PUT` on toggle, returning `{ config, loading, error, setEnabled }`. `useBarPersistent` → `/api/settings/bar-persistent`; `useMuteMusic` → `/api/settings/mute-music`.
- [ ] **Step 2 — TaskbarView.tsx**: `ViewHeader` (title `nav.taskbar`, subtitle `taskbar_view.subtitle`) + two cards:
  - Card "Appearance": the `OverlayStylePanel` (moved verbatim from SettingsView, with its `useOverlayStyle` import).
  - Card "Behavior": two `<Switch>` rows using `useBarPersistent` / `useMuteMusic`, each: icon + title (`taskbar_view.bar_persistent.title` / `.mute_music.title`) + description + `<Switch checked onCheckedChange disabled>`; toast on change. Brand classes `rounded-lg border border-border bg-card/60 p-4`, `text-primary` icons.
- [ ] **Step 3 — SettingsView.tsx**: remove `<OverlayStylePanel />`, the `OverlayStylePanel` function, and the `useOverlayStyle`/`Monitor` imports if now unused.
- [ ] **Step 4 — i18n** en/de/es: add `nav.taskbar` + a `taskbar_view` namespace (title/subtitle/bar_persistent.{title,description}/mute_music.{title,description}/toasts). English source; de/es translated.
- [ ] **Step 5 — build** `npm --prefix jarvis/ui/web/frontend run build` (tsc must pass).
- [ ] **Step 6 — commit isolated** (TaskbarView + hooks); SettingsView + i18n stay uncommitted (parallel-edited / dependent).

## Task 10: validation

- [ ] Backend suite: `py -3.11 -m pytest tests/unit/audio/test_ducking_factory.py tests/unit/audio/test_duck_controller.py tests/unit/core/test_config_writer_taskbar.py tests/unit/ui/test_taskbar_routes.py tests/unit/ui/test_desktop_bar_persistent.py -v`
- [ ] `ruff check jarvis/audio/ducking`
- [ ] Headless import: `py -3.11 -c "import jarvis.audio.ducking; print('ok')"` (no pycaw needed).
- [ ] Frontend: `npm --prefix jarvis/ui/web/frontend run build` + `run test -- --run`.
- [ ] `code-reviewer` over the new files + touch hunks.
- [ ] Live sign-off: restart Jarvis, open Taskbar section, toggle "Mute music while dictating" on, play Spotify, say "Hey Jarvis" → music mutes; session ends → music restores. Toggle "Show bar at all times" off → bar hides at idle.

## Execution Notes
- Parallel-session touch-files (`config.py`, `config_writer.py`, `settings_routes.py`, `desktop_app.py`, `pyproject.toml`, i18n, `SettingsView.tsx`) stay UNCOMMITTED; commit only isolated new files. Restart applies the live tree.
- pycaw must be installed for live ducking: `py -3.11 -m pip install pycaw comtypes` (the maintainer's box already has them via the diag script).
