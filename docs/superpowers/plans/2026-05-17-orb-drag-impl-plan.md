# Orb Drag-and-Pin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Tk Jarvis orb draggable with LMB-hold-drag-release, persist the pinned position to `jarvis.toml`, and reset to taskbar anchor on double-click.

**Architecture:** Add Tk mouse bindings to the existing `OrbOverlay` canvas. Branch the 1500 ms auto-anchor-recheck on a new `_manual_pinned` flag. Vendor the proven math/persistence helpers from `OS-Level/src/overlay/mascot_position.py` into a new `ui/orb/drag_persistence.py` so production runtime has no cross-package import.

**Tech Stack:** Tkinter (Tk main loop, `<ButtonPress-1>` / `<B1-Motion>` / `<ButtonRelease-1>` / `<Double-Button-1>`), `tomllib` (read), text-based atomic TOML write (preserves comments), `ctypes` for `EnumDisplayMonitors` (Tk-native screen discovery, no Qt).

**Spec:** [`docs/superpowers/specs/2026-05-17-orb-drag-design.md`](../specs/2026-05-17-orb-drag-design.md)

---

## File Structure

### New files
- `ui/orb/drag_persistence.py` — vendored math + TOML helpers + Tk-native screen discovery + `clear_position_in_toml`
- `tests/unit/ui/test_orb_drag_persistence.py` — unit tests for the vendored helpers (threshold-free, no Tk required)
- `tests/unit/ui/test_orb_drag_handlers.py` — unit tests for the drag state machine inside `OrbOverlay` (Tk mocked)

### Modified files
- `ui/orb/overlay.py` — constants, `_DragState`, new bindings, branched recheck, branched boot

### Untouched (reused conceptually only — not imported)
- `OS-Level/src/overlay/mascot_position.py` — stays as-is, dormant Qt path

---

## Task 1: Create `ui/orb/drag_persistence.py` skeleton + `MascotPosition` dataclass + module docstring

**Files:**
- Create: `ui/orb/drag_persistence.py`
- Test: `tests/unit/ui/test_orb_drag_persistence.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/ui/test_orb_drag_persistence.py`:

```python
"""Unit tests for ui.orb.drag_persistence — vendored helpers."""
from __future__ import annotations

import pytest

from ui.orb.drag_persistence import MascotPosition


def test_mascot_position_is_frozen_dataclass():
    pos = MascotPosition(monitor="\\\\.\\DISPLAY1", x_relative=100, y_relative=50)
    assert pos.monitor == "\\\\.\\DISPLAY1"
    assert pos.x_relative == 100
    assert pos.y_relative == 50
    with pytest.raises(AttributeError):
        pos.x_relative = 200  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/ui/test_orb_drag_persistence.py::test_mascot_position_is_frozen_dataclass -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'ui.orb.drag_persistence'`.

- [ ] **Step 3: Write minimal implementation**

Create `ui/orb/drag_persistence.py`:

```python
"""Drag-and-pin persistence helpers for the Tk Jarvis orb.

These are vendored from OS-Level/src/overlay/mascot_position.py to avoid
cross-package sys.path manipulation at runtime. The dormant PySide6
overlay keeps its own copy. If both ever need to diverge, that's fine;
if they ever need to converge, factor into a shared utility package.

Public surface:
  - MascotPosition (frozen dataclass)
  - clamp_to_work_area(x, y, monitor_geo, mascot_size_px) -> (x, y)
  - resolve_placement(persisted, screens, mascot_size_px) -> ResolvedPlacement
  - load_position_from_toml(path) -> MascotPosition | None
  - save_position_to_toml(path, pos) -> None
  - clear_position_in_toml(path) -> None
  - screens_from_tk(root) -> list[_ScreenSnapshot]   (Win32 EnumDisplayMonitors)
  - DEFAULT_MARGIN_PX, DEFAULT_X_RELATIVE, DEFAULT_Y_RELATIVE
"""
from __future__ import annotations

from dataclasses import dataclass


DEFAULT_X_RELATIVE: int = 200
DEFAULT_Y_RELATIVE: int = 80
DEFAULT_MARGIN_PX: int = 16


@dataclass(frozen=True)
class MascotPosition:
    """Persisted orb position. ``monitor`` is the Win32 device name."""

    monitor: str
    x_relative: int
    y_relative: int
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/unit/ui/test_orb_drag_persistence.py::test_mascot_position_is_frozen_dataclass -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/orb/drag_persistence.py tests/unit/ui/test_orb_drag_persistence.py
git commit -m "feat(orb-drag): scaffold drag_persistence module + MascotPosition dataclass"
```

---

## Task 2: Add `_ScreenSnapshot` + `ResolvedPlacement` + `clamp_to_work_area`

**Files:**
- Modify: `ui/orb/drag_persistence.py`
- Test: `tests/unit/ui/test_orb_drag_persistence.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/ui/test_orb_drag_persistence.py`:

```python
from ui.orb.drag_persistence import (
    DEFAULT_MARGIN_PX,
    ResolvedPlacement,
    _ScreenSnapshot,
    clamp_to_work_area,
)


def test_clamp_inside_work_area_is_noop():
    # 1920x1080 monitor at (0,0), orb is 108px. (500,500) is well inside.
    x, y = clamp_to_work_area(500, 500, (0, 0, 1920, 1080), mascot_size_px=108)
    assert (x, y) == (500, 500)


def test_clamp_pulls_back_off_screen_right():
    # Orb top-left at (1900, 500) on a 1920-wide monitor would put its
    # right edge at 2008 — past the work area.
    x, y = clamp_to_work_area(1900, 500, (0, 0, 1920, 1080), mascot_size_px=108)
    # Max x = 0 + 1920 - 108 - 16 (margin) = 1796.
    assert x == 1796
    assert y == 500


def test_clamp_pulls_back_off_screen_top_left():
    x, y = clamp_to_work_area(-50, -10, (0, 0, 1920, 1080), mascot_size_px=108)
    assert (x, y) == (DEFAULT_MARGIN_PX, DEFAULT_MARGIN_PX)


def test_screen_snapshot_is_frozen():
    snap = _ScreenSnapshot(name="\\\\.\\DISPLAY1", geometry=(0, 0, 1920, 1080), is_primary=True)
    assert snap.name == "\\\\.\\DISPLAY1"
    assert snap.geometry == (0, 0, 1920, 1080)
    assert snap.is_primary is True
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/ui/test_orb_drag_persistence.py -v
```

Expected: FAIL — `ImportError` for `ResolvedPlacement`, `_ScreenSnapshot`, `clamp_to_work_area`.

- [ ] **Step 3: Write minimal implementation**

Append to `ui/orb/drag_persistence.py`:

```python
@dataclass(frozen=True)
class _ScreenSnapshot:
    """Platform-free monitor description for resolve_placement()."""

    name: str  # Win32 device name on Windows; symbolic id elsewhere
    geometry: tuple[int, int, int, int]  # (x, y, w, h) work-area in logical px
    is_primary: bool


@dataclass(frozen=True)
class ResolvedPlacement:
    """Result of resolve_placement() — where the orb actually goes."""

    abs_x: int
    abs_y: int
    monitor: str
    recovered: bool  # True if primary-fallback was used


def clamp_to_work_area(
    abs_x: int,
    abs_y: int,
    monitor_geometry: tuple[int, int, int, int],
    *,
    mascot_size_px: int = 108,
    margin_px: int = DEFAULT_MARGIN_PX,
) -> tuple[int, int]:
    """Keep the orb fully inside the work area minus a safety margin."""
    sx, sy, sw, sh = monitor_geometry
    min_x = sx + margin_px
    min_y = sy + margin_px
    max_x = sx + sw - mascot_size_px - margin_px
    max_y = sy + sh - mascot_size_px - margin_px
    return (max(min_x, min(abs_x, max_x)), max(min_y, min(abs_y, max_y)))
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/ui/test_orb_drag_persistence.py -v
```

Expected: PASS (4 tests now).

- [ ] **Step 5: Commit**

```bash
git add ui/orb/drag_persistence.py tests/unit/ui/test_orb_drag_persistence.py
git commit -m "feat(orb-drag): add ResolvedPlacement + clamp_to_work_area + _ScreenSnapshot"
```

---

## Task 3: Add `resolve_placement` (monitor-recovery)

**Files:**
- Modify: `ui/orb/drag_persistence.py`
- Test: `tests/unit/ui/test_orb_drag_persistence.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/ui/test_orb_drag_persistence.py`:

```python
from ui.orb.drag_persistence import (
    DEFAULT_X_RELATIVE,
    DEFAULT_Y_RELATIVE,
    resolve_placement,
)


def _screen(name, *, x=0, y=0, w=1920, h=1080, primary=False):
    return _ScreenSnapshot(name=name, geometry=(x, y, w, h), is_primary=primary)


def test_resolve_with_persisted_monitor_present_uses_it():
    screens = [_screen("\\\\.\\DISPLAY1", primary=True)]
    persisted = MascotPosition(monitor="\\\\.\\DISPLAY1", x_relative=300, y_relative=100)
    placement = resolve_placement(persisted, screens, mascot_size_px=108)
    assert placement.abs_x == 300
    assert placement.abs_y == 100
    assert placement.monitor == "\\\\.\\DISPLAY1"
    assert placement.recovered is False


def test_resolve_falls_back_to_primary_when_monitor_missing():
    screens = [_screen("\\\\.\\DISPLAY2", primary=True, x=100, y=200)]
    persisted = MascotPosition(monitor="\\\\.\\DISPLAY99", x_relative=500, y_relative=400)
    placement = resolve_placement(persisted, screens, mascot_size_px=108)
    assert placement.abs_x == 100 + DEFAULT_X_RELATIVE
    assert placement.abs_y == 200 + DEFAULT_Y_RELATIVE
    assert placement.recovered is True
    assert placement.monitor == "\\\\.\\DISPLAY2"


def test_resolve_with_no_persisted_uses_primary_default():
    screens = [_screen("\\\\.\\DISPLAY1", primary=True)]
    placement = resolve_placement(None, screens, mascot_size_px=108)
    assert placement.abs_x == DEFAULT_X_RELATIVE
    assert placement.abs_y == DEFAULT_Y_RELATIVE
    assert placement.recovered is True


def test_resolve_with_no_screens_returns_default():
    persisted = MascotPosition(monitor="X", x_relative=10, y_relative=20)
    placement = resolve_placement(persisted, [], mascot_size_px=108)
    assert placement.recovered is True
    assert placement.monitor == ""
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/ui/test_orb_drag_persistence.py -v
```

Expected: FAIL — `resolve_placement` not defined.

- [ ] **Step 3: Write minimal implementation**

Append to `ui/orb/drag_persistence.py`:

```python
from typing import Optional, Sequence


def resolve_placement(
    persisted: Optional[MascotPosition],
    screens: Sequence[_ScreenSnapshot],
    *,
    mascot_size_px: int = 108,
) -> ResolvedPlacement:
    """5-step monitor recovery (mirrored from OS-Level/.../mascot_position.py)."""
    if not screens:
        return ResolvedPlacement(
            abs_x=DEFAULT_X_RELATIVE,
            abs_y=DEFAULT_Y_RELATIVE,
            monitor="",
            recovered=True,
        )

    by_name = {s.name: s for s in screens}

    if persisted is not None and persisted.monitor in by_name:
        screen = by_name[persisted.monitor]
        sx, sy, sw, sh = screen.geometry
        max_rel_x = max(0, sw - mascot_size_px - DEFAULT_MARGIN_PX)
        max_rel_y = max(0, sh - mascot_size_px - DEFAULT_MARGIN_PX)
        rel_x = max(DEFAULT_MARGIN_PX, min(persisted.x_relative, max_rel_x))
        rel_y = max(DEFAULT_MARGIN_PX, min(persisted.y_relative, max_rel_y))
        return ResolvedPlacement(
            abs_x=sx + rel_x,
            abs_y=sy + rel_y,
            monitor=screen.name,
            recovered=False,
        )

    primary = next((s for s in screens if s.is_primary), screens[0])
    sx, sy, _sw, _sh = primary.geometry
    return ResolvedPlacement(
        abs_x=sx + DEFAULT_X_RELATIVE,
        abs_y=sy + DEFAULT_Y_RELATIVE,
        monitor=primary.name,
        recovered=True,
    )
```

Move the `from typing import Optional, Sequence` to the top of the file with the other imports.

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/ui/test_orb_drag_persistence.py -v
```

Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add ui/orb/drag_persistence.py tests/unit/ui/test_orb_drag_persistence.py
git commit -m "feat(orb-drag): add resolve_placement with primary-monitor fallback"
```

---

## Task 4: Add `load_position_from_toml` + `save_position_to_toml` + `clear_position_in_toml`

**Files:**
- Modify: `ui/orb/drag_persistence.py`
- Test: `tests/unit/ui/test_orb_drag_persistence.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/ui/test_orb_drag_persistence.py`:

```python
from pathlib import Path

from ui.orb.drag_persistence import (
    clear_position_in_toml,
    load_position_from_toml,
    save_position_to_toml,
)


def test_load_returns_none_when_file_missing(tmp_path: Path):
    assert load_position_from_toml(tmp_path / "nope.toml") is None


def test_load_returns_none_when_section_missing(tmp_path: Path):
    p = tmp_path / "j.toml"
    p.write_text("[profile]\nname = 'x'\n", encoding="utf-8")
    pos = load_position_from_toml(p)
    # Section missing → defaults are returned with empty monitor name.
    assert pos is not None
    assert pos.monitor == ""


def test_save_then_load_roundtrip(tmp_path: Path):
    p = tmp_path / "j.toml"
    pos = MascotPosition(monitor="\\\\.\\DISPLAY1", x_relative=1340, y_relative=720)
    save_position_to_toml(p, pos)
    loaded = load_position_from_toml(p)
    assert loaded == pos


def test_save_preserves_existing_other_sections_and_comments(tmp_path: Path):
    p = tmp_path / "j.toml"
    p.write_text(
        "# Top comment\n"
        "[profile]\n"
        "name = \"default\"  # inline comment\n"
        "\n"
        "[overlay]\n"
        "enabled = true\n",
        encoding="utf-8",
    )
    pos = MascotPosition(monitor="\\\\.\\DISPLAY1", x_relative=100, y_relative=50)
    save_position_to_toml(p, pos)
    text = p.read_text(encoding="utf-8")
    assert "# Top comment" in text
    assert "# inline comment" in text
    assert "[overlay]" in text
    assert "[overlay.mascot]" in text
    assert "position_monitor" in text


def test_clear_removes_three_keys_keeps_rest(tmp_path: Path):
    p = tmp_path / "j.toml"
    p.write_text(
        "[overlay]\n"
        "enabled = true\n"
        "\n"
        "[overlay.mascot]\n"
        'position_monitor = "\\\\\\\\.\\\\DISPLAY1"\n'
        "position_x_relative = 100\n"
        "position_y_relative = 50\n",
        encoding="utf-8",
    )
    clear_position_in_toml(p)
    text = p.read_text(encoding="utf-8")
    assert "position_monitor" not in text
    assert "position_x_relative" not in text
    assert "position_y_relative" not in text
    # The section header itself is allowed to remain (or be removed); both
    # acceptable. The other section MUST still be there.
    assert "[overlay]" in text
    assert "enabled = true" in text


def test_clear_is_noop_when_file_missing(tmp_path: Path):
    # Must not raise.
    clear_position_in_toml(tmp_path / "absent.toml")
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/ui/test_orb_drag_persistence.py -v
```

Expected: FAIL — symbols undefined.

- [ ] **Step 3: Write minimal implementation**

Append to `ui/orb/drag_persistence.py`:

```python
import logging
import os
import re
import tempfile
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)


def load_position_from_toml(path: Path) -> Optional[MascotPosition]:
    """Read [overlay.mascot] from jarvis.toml. None if file is missing."""
    if not path.is_file():
        return None
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        logger.warning("load_position_from_toml: %s", exc)
        return None
    overlay = data.get("overlay") or {}
    section = overlay.get("mascot") or {}
    monitor = section.get("position_monitor", "")
    if not isinstance(monitor, str):
        monitor = ""
    x_rel = int(section.get("position_x_relative", DEFAULT_X_RELATIVE))
    y_rel = int(section.get("position_y_relative", DEFAULT_Y_RELATIVE))
    return MascotPosition(monitor=monitor, x_relative=x_rel, y_relative=y_rel)


def save_position_to_toml(path: Path, position: MascotPosition) -> None:
    """Atomic write of the three position fields. Comment-preserving."""
    if not path.is_file():
        new_text = (
            "[overlay.mascot]\n"
            f'position_monitor = "{_escape_toml_str(position.monitor)}"\n'
            f"position_x_relative = {position.x_relative}\n"
            f"position_y_relative = {position.y_relative}\n"
        )
        _atomic_write_text(path, new_text)
        return

    text = path.read_text(encoding="utf-8")
    section_re = re.compile(r"^\[overlay\.mascot\]\s*$", re.MULTILINE)
    if not section_re.search(text):
        if not text.endswith("\n"):
            text += "\n"
        text += (
            "\n[overlay.mascot]\n"
            f'position_monitor = "{_escape_toml_str(position.monitor)}"\n'
            f"position_x_relative = {position.x_relative}\n"
            f"position_y_relative = {position.y_relative}\n"
        )
        _atomic_write_text(path, text)
        return

    text = _replace_or_append_field(
        text, section_header="[overlay.mascot]",
        field="position_monitor",
        value=f'"{_escape_toml_str(position.monitor)}"',
    )
    text = _replace_or_append_field(
        text, section_header="[overlay.mascot]",
        field="position_x_relative", value=str(position.x_relative),
    )
    text = _replace_or_append_field(
        text, section_header="[overlay.mascot]",
        field="position_y_relative", value=str(position.y_relative),
    )
    _atomic_write_text(path, text)


def clear_position_in_toml(path: Path) -> None:
    """Remove the three position_* keys from [overlay.mascot]. No-op if file missing."""
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    section_re = re.compile(r"^\[overlay\.mascot\]\s*$", re.MULTILINE)
    if not section_re.search(text):
        return  # nothing to clear

    # Remove each key line within the section region (header up to next [ or EOF).
    region_re = re.compile(
        r"(^\[overlay\.mascot\]\s*\n)(.*?)(?=^\[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = region_re.search(text)
    if m is None:
        return
    region_body = m.group(2)
    for field in ("position_monitor", "position_x_relative", "position_y_relative"):
        region_body = re.sub(
            rf"^\s*{re.escape(field)}\s*=.*?\n",
            "",
            region_body,
            flags=re.MULTILINE,
        )
    new_text = text[: m.start(2)] + region_body + text[m.end(2) :]
    _atomic_write_text(path, new_text)


def _atomic_write_text(path: Path, text: str) -> None:
    """tempfile + os.replace() — atomic on Win32."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _escape_toml_str(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _replace_or_append_field(
    text: str, *, section_header: str, field: str, value: str
) -> str:
    section_pattern = re.escape(section_header)
    region_re = re.compile(
        rf"(^{section_pattern}\s*\n)(.*?)(?=^\[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    region_m = region_re.search(text)
    if region_m is None:
        return text

    region_text = region_m.group(2)
    field_re = re.compile(
        rf"^(?P<lead>\s*){re.escape(field)}\s*=.*?$",
        re.MULTILINE,
    )
    if field_re.search(region_text):
        new_region = field_re.sub(
            lambda m: f"{m.group('lead')}{field} = {value}",
            region_text,
            count=1,
        )
    else:
        new_region = f"{field} = {value}\n" + region_text

    return text[: region_m.start(2)] + new_region + text[region_m.end(2) :]
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/ui/test_orb_drag_persistence.py -v
```

Expected: PASS (14 tests).

- [ ] **Step 5: Commit**

```bash
git add ui/orb/drag_persistence.py tests/unit/ui/test_orb_drag_persistence.py
git commit -m "feat(orb-drag): TOML load/save/clear with comment preservation"
```

---

## Task 5: Add `screens_from_tk` (Win32 `EnumDisplayMonitors`)

**Files:**
- Modify: `ui/orb/drag_persistence.py`
- Test: `tests/unit/ui/test_orb_drag_persistence.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/ui/test_orb_drag_persistence.py`:

```python
import sys


def test_screens_from_tk_returns_at_least_one_screen_on_windows():
    if sys.platform != "win32":
        pytest.skip("EnumDisplayMonitors is Win32-only")
    from ui.orb.drag_persistence import screens_from_tk
    screens = screens_from_tk(root=None)  # we don't need a real Tk root
    assert len(screens) >= 1
    primary = [s for s in screens if s.is_primary]
    assert len(primary) == 1  # exactly one primary monitor


def test_screens_from_tk_returns_empty_on_non_windows(monkeypatch):
    import ui.orb.drag_persistence as mod
    monkeypatch.setattr(mod, "sys", type("M", (), {"platform": "linux"}))
    assert mod.screens_from_tk(root=None) == []
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/ui/test_orb_drag_persistence.py -v
```

Expected: FAIL on import of `screens_from_tk`.

- [ ] **Step 3: Write minimal implementation**

Append to `ui/orb/drag_persistence.py`:

```python
import sys  # already imported; idempotent if duplicate, just consolidate at top of file


def screens_from_tk(root) -> list[_ScreenSnapshot]:
    """Win32 EnumDisplayMonitors → list of _ScreenSnapshot.

    The ``root`` parameter is accepted for API symmetry with the Qt
    sibling but is unused — we read directly from Win32. On non-Windows
    platforms we return an empty list and the caller falls back.
    """
    _ = root
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes
    except (ImportError, OSError):
        return []

    user32 = ctypes.windll.user32

    # MONITORINFOEXW is MONITORINFO + szDevice[32 wide chars].
    class _MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    MONITORINFOF_PRIMARY = 0x00000001
    out: list[_ScreenSnapshot] = []

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    def _enum_proc(hmonitor, _hdc, _rect, _lparam):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if not user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            return 1
        rc = info.rcWork
        out.append(
            _ScreenSnapshot(
                name=info.szDevice,
                geometry=(rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top),
                is_primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
            )
        )
        return 1

    user32.EnumDisplayMonitors(0, 0, MONITORENUMPROC(_enum_proc), 0)
    return out
```

Make sure `import sys` is at the top of the file.

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/ui/test_orb_drag_persistence.py -v
```

Expected: PASS (16 tests, the Windows-only one runs on this machine).

- [ ] **Step 5: Commit**

```bash
git add ui/orb/drag_persistence.py tests/unit/ui/test_orb_drag_persistence.py
git commit -m "feat(orb-drag): screens_from_tk via Win32 EnumDisplayMonitors"
```

---

## Task 6: Add `__all__` and finalize public surface of `drag_persistence.py`

**Files:**
- Modify: `ui/orb/drag_persistence.py`

- [ ] **Step 1: Append `__all__` at end of file**

```python
__all__ = [
    "DEFAULT_MARGIN_PX",
    "DEFAULT_X_RELATIVE",
    "DEFAULT_Y_RELATIVE",
    "MascotPosition",
    "ResolvedPlacement",
    "_ScreenSnapshot",
    "clamp_to_work_area",
    "clear_position_in_toml",
    "load_position_from_toml",
    "resolve_placement",
    "save_position_to_toml",
    "screens_from_tk",
]
```

- [ ] **Step 2: Run all drag_persistence tests + lint**

```
pytest tests/unit/ui/test_orb_drag_persistence.py -v
ruff check ui/orb/drag_persistence.py
```

Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add ui/orb/drag_persistence.py
git commit -m "chore(orb-drag): expose drag_persistence public surface via __all__"
```

---

## Task 7: Wire the drag state machine into `OrbOverlay` — state slots + constants

**Files:**
- Modify: `ui/orb/overlay.py`

- [ ] **Step 1: Add module-level constant near other DEFAULTs (around line 91)**

In `ui/orb/overlay.py`, after `POSITION_RECHECK_MS = 1500`:

```python
# Drag-and-pin threshold (manhattan distance, px). Below this we treat
# the gesture as a click (no movement, no persistence).
DRAG_THRESHOLD_PX = 5
```

- [ ] **Step 2: Add import + dataclass at module level (near other imports, after the existing `from ui.orb.taskbar import ...` block)**

```python
from dataclasses import dataclass

from jarvis.core.config import DEFAULT_CONFIG_FILE as JARVIS_TOML_PATH
from ui.orb.drag_persistence import (
    MascotPosition,
    clamp_to_work_area,
    clear_position_in_toml,
    load_position_from_toml,
    resolve_placement,
    save_position_to_toml,
    screens_from_tk,
)


@dataclass
class _DragState:
    start_root_x: int
    start_root_y: int
    offset_x: int  # event.x_root - mascot_x at press time
    offset_y: int
    moved: bool = False
```

- [ ] **Step 3: Add state slots in `OrbOverlay.__init__` (around line 1513, right after `_mascot_y` init)**

```python
        # Drag-and-pin state. _manual_pinned switches the 1500 ms recheck
        # loop into clamp-only mode and tells boot to skip the taskbar
        # anchor.
        self._manual_pinned: bool = False
        self._drag_state: "_DragState | None" = None
```

- [ ] **Step 4: Run a quick import-smoke**

```
python -c "import ui.orb.overlay; print('OK')"
```

Expected: `OK` (no ImportError).

- [ ] **Step 5: Commit**

```bash
git add ui/orb/overlay.py
git commit -m "feat(orb-drag): add drag state slots + DRAG_THRESHOLD_PX in OrbOverlay"
```

---

## Task 8: Hook the canvas mouse bindings inside `OrbOverlay.start()`

**Files:**
- Modify: `ui/orb/overlay.py`

- [ ] **Step 1: After `self._canvas.pack(fill="both", expand=True)` (currently line 1577), add the bindings**

```python
        # Drag-and-pin bindings. Tk dispatch:
        #   <Button-1> → drag-start (always fires, even on a double-click)
        #   <B1-Motion> → drag-update (only fires while LMB held)
        #   <ButtonRelease-1> → drag-finish (or no-op if it was a click)
        #   <Double-Button-1> → reset (fires *in addition* to two Button-1)
        # The drag-start handler does not commit any geometry change until
        # the threshold is crossed, so a fast double-click stays harmless.
        self._canvas.bind("<ButtonPress-1>", self._on_drag_press)
        self._canvas.bind("<B1-Motion>", self._on_drag_motion)
        self._canvas.bind("<ButtonRelease-1>", self._on_drag_release)
        self._canvas.bind("<Double-Button-1>", self._on_reset_double_click)
```

- [ ] **Step 2: Add the four handler methods to `OrbOverlay` (place them near `_resolve_anchor`, after `_schedule_position_recheck`)**

```python
    # ------------------------------------------------------------------
    # Drag-and-pin handlers (Spec: docs/superpowers/specs/2026-05-17-orb-drag-design.md)
    # ------------------------------------------------------------------

    def _on_drag_press(self, event: tk.Event) -> None:
        if self._root is None:
            return
        self._drag_state = _DragState(
            start_root_x=event.x_root,
            start_root_y=event.y_root,
            offset_x=event.x_root - self._mascot_x,
            offset_y=event.y_root - self._mascot_y,
            moved=False,
        )
        try:
            self._root.configure(cursor="fleur")
        except tk.TclError:
            pass

    def _on_drag_motion(self, event: tk.Event) -> None:
        if self._drag_state is None or self._root is None:
            return
        dx = event.x_root - self._drag_state.start_root_x
        dy = event.y_root - self._drag_state.start_root_y
        if not self._drag_state.moved and (abs(dx) + abs(dy)) < DRAG_THRESHOLD_PX:
            return
        self._drag_state.moved = True
        new_x = event.x_root - self._drag_state.offset_x
        new_y = event.y_root - self._drag_state.offset_y
        self._mascot_x = new_x
        self._mascot_y = new_y
        try:
            self._root.geometry(f"{WIN_W}x{WIN_H}+{new_x}+{new_y}")
        except tk.TclError:
            return
        if self._comment_bubble is not None:
            screen_w = self._root.winfo_screenwidth()
            self._comment_bubble.update_anchor(new_x, new_y, screen_w)

    def _on_drag_release(self, _event: tk.Event) -> None:
        if self._root is not None:
            try:
                self._root.configure(cursor="")
            except tk.TclError:
                pass
        state = self._drag_state
        self._drag_state = None
        if state is None or not state.moved:
            return  # click, not drag

        screens = screens_from_tk(self._root)
        monitor_geo, monitor_name = self._monitor_at_orb_center(screens)
        clamped_x, clamped_y = clamp_to_work_area(
            self._mascot_x, self._mascot_y, monitor_geo, mascot_size_px=WIN_W
        )
        if (clamped_x, clamped_y) != (self._mascot_x, self._mascot_y):
            self._mascot_x = clamped_x
            self._mascot_y = clamped_y
            try:
                self._root.geometry(f"{WIN_W}x{WIN_H}+{clamped_x}+{clamped_y}")
            except tk.TclError:
                pass

        self._manual_pinned = True
        try:
            save_position_to_toml(
                JARVIS_TOML_PATH,
                MascotPosition(
                    monitor=monitor_name,
                    x_relative=self._mascot_x - monitor_geo[0],
                    y_relative=self._mascot_y - monitor_geo[1],
                ),
            )
        except OSError as exc:
            # Persistence failure is non-fatal — orb stays at new position
            # for this session; next restart falls back to default.
            print(f"[orb] save_position_to_toml failed: {exc}")

    def _on_reset_double_click(self, _event: tk.Event) -> None:
        if self._root is None:
            return
        self._manual_pinned = False
        self._drag_state = None
        try:
            clear_position_in_toml(JARVIS_TOML_PATH)
        except OSError as exc:
            print(f"[orb] clear_position_in_toml failed: {exc}")
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        anchor = self._resolve_anchor(screen_w, screen_h)
        self._mascot_x = anchor.x
        self._mascot_y = anchor.y
        try:
            self._root.geometry(f"{WIN_W}x{WIN_H}+{anchor.x}+{anchor.y}")
        except tk.TclError:
            return
        if self._comment_bubble is not None:
            self._comment_bubble.update_anchor(anchor.x, anchor.y, screen_w)

    def _monitor_at_orb_center(
        self,
        screens: list,
    ) -> tuple[tuple[int, int, int, int], str]:
        """Return (geometry, device_name) of the monitor containing the orb center."""
        cx = self._mascot_x + WIN_W // 2
        cy = self._mascot_y + WIN_H // 2
        for s in screens:
            sx, sy, sw, sh = s.geometry
            if sx <= cx < sx + sw and sy <= cy < sy + sh:
                return s.geometry, s.name
        # Fallback: primary monitor.
        primary = next((s for s in screens if s.is_primary), None)
        if primary is not None:
            return primary.geometry, primary.name
        if screens:
            return screens[0].geometry, screens[0].name
        # Last resort: single-screen guess.
        if self._root is not None:
            sw = self._root.winfo_screenwidth()
            sh = self._root.winfo_screenheight()
            return (0, 0, sw, sh), ""
        return (0, 0, 1920, 1080), ""
```

- [ ] **Step 3: Run an import-smoke**

```
python -c "import ui.orb.overlay; print('OK')"
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add ui/orb/overlay.py
git commit -m "feat(orb-drag): canvas mouse bindings + drag/release/reset handlers"
```

---

## Task 9: Branch `_schedule_position_recheck` on `_manual_pinned`

**Files:**
- Modify: `ui/orb/overlay.py`

- [ ] **Step 1: Edit `_schedule_position_recheck` (currently lines 1629-1648)**

Replace the existing body with:

```python
    def _schedule_position_recheck(self) -> None:
        """Re-resolve the mascot anchor periodically.

        Two paths:
        - manual_pinned=False: existing taskbar-anchor recompute (DPI /
          monitor / taskbar resize tracking).
        - manual_pinned=True: clamp-only — keep orb on the visible work
          area if monitors changed; otherwise leave it alone.
        """
        if not self._running or not self._root:
            return
        try:
            screen_w = self._root.winfo_screenwidth()
            screen_h = self._root.winfo_screenheight()
            if self._manual_pinned:
                screens = screens_from_tk(self._root)
                monitor_geo, monitor_name = self._monitor_at_orb_center(screens)
                clamped_x, clamped_y = clamp_to_work_area(
                    self._mascot_x, self._mascot_y, monitor_geo, mascot_size_px=WIN_W
                )
                if (clamped_x, clamped_y) != (self._mascot_x, self._mascot_y):
                    self._mascot_x = clamped_x
                    self._mascot_y = clamped_y
                    self._root.geometry(f"{WIN_W}x{WIN_H}+{clamped_x}+{clamped_y}")
                    if self._comment_bubble is not None:
                        self._comment_bubble.update_anchor(clamped_x, clamped_y, screen_w)
                    try:
                        save_position_to_toml(
                            JARVIS_TOML_PATH,
                            MascotPosition(
                                monitor=monitor_name,
                                x_relative=clamped_x - monitor_geo[0],
                                y_relative=clamped_y - monitor_geo[1],
                            ),
                        )
                    except OSError:
                        pass
            else:
                anchor = self._resolve_anchor(screen_w, screen_h)
                if (anchor.x, anchor.y) != (self._mascot_x, self._mascot_y):
                    self._mascot_x = anchor.x
                    self._mascot_y = anchor.y
                    self._root.geometry(f"{WIN_W}x{WIN_H}+{anchor.x}+{anchor.y}")
                    if self._comment_bubble is not None:
                        self._comment_bubble.update_anchor(anchor.x, anchor.y, screen_w)
        except (tk.TclError, OSError):
            pass
        if self._root is not None:
            self._root.after(POSITION_RECHECK_MS, self._schedule_position_recheck)
```

- [ ] **Step 2: Run an import-smoke + the existing overlay tests**

```
python -c "import ui.orb.overlay; print('OK')"
pytest tests/unit/ui/ -v -x
```

Expected: import OK, drag_persistence tests still pass. (No tests in `tests/unit/ui/` for `overlay.py` exist yet.)

- [ ] **Step 3: Commit**

```bash
git add ui/orb/overlay.py
git commit -m "feat(orb-drag): branch position-recheck on _manual_pinned (clamp-only path)"
```

---

## Task 10: Branch boot path to restore persisted position

**Files:**
- Modify: `ui/orb/overlay.py`

- [ ] **Step 1: Edit `OrbOverlay.start()` — replace the existing anchor block (currently lines 1561-1567) with the branched version**

The current code is:

```python
        # Resolve mascot anchor from the live Windows taskbar rect.
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        anchor = self._resolve_anchor(screen_w, screen_h)
        self._mascot_x = anchor.x
        self._mascot_y = anchor.y
        self._root.geometry(f"{WIN_W}x{WIN_H}+{anchor.x}+{anchor.y}")
```

Replace it with:

```python
        # Resolve mascot anchor. If the user has manually pinned the orb
        # in a prior session, restore that position; otherwise compute
        # the live Windows-taskbar-aligned default.
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        persisted = load_position_from_toml(JARVIS_TOML_PATH)
        if persisted is not None and persisted.monitor:
            screens = screens_from_tk(self._root)
            placement = resolve_placement(persisted, screens, mascot_size_px=WIN_W)
            if not placement.recovered:
                # Persisted monitor still present — honour user's pin.
                self._manual_pinned = True
                self._mascot_x = placement.abs_x
                self._mascot_y = placement.abs_y
                self._root.geometry(
                    f"{WIN_W}x{WIN_H}+{placement.abs_x}+{placement.abs_y}"
                )
            else:
                # Monitor gone — fall back to default, clear stale entry.
                try:
                    clear_position_in_toml(JARVIS_TOML_PATH)
                except OSError:
                    pass
                anchor = self._resolve_anchor(screen_w, screen_h)
                self._mascot_x = anchor.x
                self._mascot_y = anchor.y
                self._root.geometry(f"{WIN_W}x{WIN_H}+{anchor.x}+{anchor.y}")
        else:
            anchor = self._resolve_anchor(screen_w, screen_h)
            self._mascot_x = anchor.x
            self._mascot_y = anchor.y
            self._root.geometry(f"{WIN_W}x{WIN_H}+{anchor.x}+{anchor.y}")
```

- [ ] **Step 2: Run an import-smoke + drag_persistence tests**

```
python -c "import ui.orb.overlay; print('OK')"
pytest tests/unit/ui/test_orb_drag_persistence.py -v
```

Expected: both green.

- [ ] **Step 3: Commit**

```bash
git add ui/orb/overlay.py
git commit -m "feat(orb-drag): branch boot path to restore persisted position"
```

---

## Task 11: Drag state-machine unit tests (handlers, with mocked Tk)

**Files:**
- Test: `tests/unit/ui/test_orb_drag_handlers.py`

- [ ] **Step 1: Write the tests**

Create `tests/unit/ui/test_orb_drag_handlers.py`:

```python
"""Unit tests for OrbOverlay drag/reset handlers — no real Tk window.

We instantiate OrbOverlay and inject a fake root + canvas + bubble so
the handlers can be called directly. This is the same fake-style used
in tests/unit/ui/test_orb_bus_bridge.py.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Skip the whole module on non-Windows because OrbOverlay's import
# pulls in Win32-specific helpers (DPI awareness, taskbar Win32 calls).
if sys.platform != "win32":
    pytest.skip("OrbOverlay handlers are Windows-only", allow_module_level=True)

from ui.orb.drag_persistence import MascotPosition, _ScreenSnapshot
from ui.orb.overlay import DRAG_THRESHOLD_PX, OrbOverlay, _DragState


def _make_overlay_with_fakes(tmp_path: Path, monkeypatch):
    ov = OrbOverlay()
    # Inject fakes — handlers only touch _root, _canvas, _comment_bubble.
    fake_root = MagicMock()
    fake_root.winfo_screenwidth.return_value = 1920
    fake_root.winfo_screenheight.return_value = 1080
    ov._root = fake_root
    ov._canvas = MagicMock()
    ov._comment_bubble = MagicMock()
    ov._mascot_x = 1796
    ov._mascot_y = 940
    # Patch the TOML path to a tmp file so we don't write into real jarvis.toml.
    fake_toml = tmp_path / "jarvis.toml"
    fake_toml.write_text("[overlay]\nenabled = true\n", encoding="utf-8")
    import ui.orb.overlay as overlay_mod
    monkeypatch.setattr(overlay_mod, "JARVIS_TOML_PATH", fake_toml)
    # screens_from_tk → single 1920x1080 primary monitor named "FAKE1".
    monkeypatch.setattr(
        overlay_mod,
        "screens_from_tk",
        lambda root: [_ScreenSnapshot(name="FAKE1", geometry=(0, 0, 1920, 1080), is_primary=True)],
    )
    return ov, fake_toml


def _event(x_root: int, y_root: int):
    return SimpleNamespace(x_root=x_root, y_root=y_root)


def test_press_sets_drag_state_and_cursor(tmp_path, monkeypatch):
    ov, _ = _make_overlay_with_fakes(tmp_path, monkeypatch)
    ov._on_drag_press(_event(1800, 950))
    assert isinstance(ov._drag_state, _DragState)
    assert ov._drag_state.offset_x == 1800 - 1796
    assert ov._drag_state.offset_y == 950 - 940
    assert ov._drag_state.moved is False
    ov._root.configure.assert_called_with(cursor="fleur")


def test_motion_below_threshold_does_not_move(tmp_path, monkeypatch):
    ov, _ = _make_overlay_with_fakes(tmp_path, monkeypatch)
    ov._on_drag_press(_event(1800, 950))
    # 4 px manhattan = below threshold.
    ov._on_drag_motion(_event(1802, 952))
    assert ov._drag_state.moved is False
    assert (ov._mascot_x, ov._mascot_y) == (1796, 940)
    ov._root.geometry.assert_not_called()


def test_motion_above_threshold_moves_orb(tmp_path, monkeypatch):
    ov, _ = _make_overlay_with_fakes(tmp_path, monkeypatch)
    ov._on_drag_press(_event(1800, 950))
    # 10 px manhattan = above threshold.
    ov._on_drag_motion(_event(1810, 950))
    assert ov._drag_state.moved is True
    # new_x = event.x_root - offset_x = 1810 - 4 = 1806
    assert ov._mascot_x == 1806
    assert ov._mascot_y == 940
    ov._root.geometry.assert_called_with("108x108+1806+940")


def test_release_after_real_drag_persists(tmp_path, monkeypatch):
    ov, fake_toml = _make_overlay_with_fakes(tmp_path, monkeypatch)
    ov._on_drag_press(_event(1800, 950))
    ov._on_drag_motion(_event(500, 200))
    ov._on_drag_release(_event(500, 200))
    assert ov._manual_pinned is True
    assert ov._drag_state is None
    text = fake_toml.read_text(encoding="utf-8")
    assert "[overlay.mascot]" in text
    assert "position_monitor" in text
    assert 'position_monitor = "FAKE1"' in text


def test_release_after_click_does_not_persist(tmp_path, monkeypatch):
    ov, fake_toml = _make_overlay_with_fakes(tmp_path, monkeypatch)
    ov._on_drag_press(_event(1800, 950))
    # Tiny jitter, below threshold.
    ov._on_drag_motion(_event(1801, 950))
    ov._on_drag_release(_event(1801, 950))
    assert ov._manual_pinned is False
    text = fake_toml.read_text(encoding="utf-8")
    assert "position_monitor" not in text


def test_double_click_clears_toml_and_resets_flag(tmp_path, monkeypatch):
    ov, fake_toml = _make_overlay_with_fakes(tmp_path, monkeypatch)
    # Simulate prior pin.
    ov._manual_pinned = True
    fake_toml.write_text(
        "[overlay.mascot]\n"
        'position_monitor = "FAKE1"\n'
        "position_x_relative = 500\n"
        "position_y_relative = 200\n",
        encoding="utf-8",
    )
    # Stub _resolve_anchor so we don't hit Win32 taskbar calls.
    ov._resolve_anchor = MagicMock(return_value=SimpleNamespace(x=1796, y=940, taskbar_aligned=True))
    ov._on_reset_double_click(_event(0, 0))
    assert ov._manual_pinned is False
    text = fake_toml.read_text(encoding="utf-8")
    assert "position_monitor" not in text
    assert "position_x_relative" not in text
    assert ov._mascot_x == 1796
    assert ov._mascot_y == 940


def test_drag_threshold_constant_is_five_px():
    # If this value ever changes, the click/drag tests above need new event
    # coordinates — this guard ensures the change goes through deliberation.
    assert DRAG_THRESHOLD_PX == 5
```

- [ ] **Step 2: Run tests**

```
pytest tests/unit/ui/test_orb_drag_handlers.py -v
```

Expected: PASS (7 tests).

- [ ] **Step 3: Commit**

```bash
git add tests/unit/ui/test_orb_drag_handlers.py
git commit -m "test(orb-drag): drag state machine + double-click reset unit tests"
```

---

## Task 12: Full test suite + lint sweep

**Files:**
- None modified — verification only.

- [ ] **Step 1: Lint**

```
ruff check ui/orb/drag_persistence.py ui/orb/overlay.py tests/unit/ui/test_orb_drag_persistence.py tests/unit/ui/test_orb_drag_handlers.py
```

Expected: no errors. Fix any reported issues inline.

- [ ] **Step 2: Run all orb-related tests**

```
pytest tests/unit/ui/ tests/overlay/ -v
```

Expected: all green. The existing `tests/overlay/test_mascot_position.py` must still pass — we did not touch the dormant Qt path.

- [ ] **Step 3: Commit (only if lint fixes were needed)**

```bash
git add -p
git commit -m "chore(orb-drag): lint sweep"
```

If no fixes were required, skip the commit.

---

## Task 13: Live verification (CLAUDE.md autonomy rule §2)

**Files:**
- None modified — verification only.

- [ ] **Step 1: Launch Jarvis**

```
Start-Process pythonw -ArgumentList "-m","jarvis.ui.web.launcher"
```

Wait ≈10 s for the orb to appear bottom-right.

- [ ] **Step 2: Drag verification**

Use `bh` (browser-harness) or direct mouse via PowerShell/`SendInput` to:
- Move mouse to orb center (`screen_w - 24 - 54, screen_h - taskbar_h - 54`) — orb is at the tray edge.
- Press LMB, hold, move to ≈ (400, 300), release.
- Take screenshot. Orb must be at the new position.

- [ ] **Step 3: Persistence verification**

```
Get-Content "C:/Users/Administrator/Desktop/Personal Jarvis/jarvis.toml" | Select-String "overlay.mascot" -Context 0,5
```

Expected: section present with the dragged coordinates.

- [ ] **Step 4: Restart verification**

```
Get-Process pythonw | Stop-Process -Force
Start-Sleep -Seconds 2
Start-Process pythonw -ArgumentList "-m","jarvis.ui.web.launcher"
```

Wait. Orb must reappear at the **dragged** position, not the tray edge.

- [ ] **Step 5: Reset verification**

Double-click the orb. Orb must jump back to the bottom-right tray position.

```
Get-Content "C:/Users/Administrator/Desktop/Personal Jarvis/jarvis.toml" | Select-String "overlay.mascot" -Context 0,5
```

Expected: section header may remain but the three position_* keys are gone.

- [ ] **Step 6: Restart verification, take 2**

Kill + relaunch. Orb appears bottom-right (default behavior fully restored).

- [ ] **Step 7: Document in the commit history**

Append a final commit (no code changes — empty `--allow-empty`):

```
git commit --allow-empty -m "chore(orb-drag): live-verified drag, persistence, reset, restart cycle"
```

---

## Self-review (already done during plan write)

- [x] Spec §2 (gestures table) → Tasks 7, 8, 11.
- [x] Spec §3 (architecture) → Task 1-6 (drag_persistence.py module), Task 7 (state slots), Tasks 8-10 (overlay.py wiring).
- [x] Spec §4 (state machine) → Tasks 8, 9 (recheck branch), 10 (boot branch).
- [x] Spec §5.1-5.6 (event-flow pseudo-code) → Task 8 (handlers), Task 9 (recheck), Task 10 (boot).
- [x] Spec §6 (edge cases) → Task 4 (clear + load fallback), Task 9 (clamp-on-recheck), Task 10 (missing-monitor stale-clear), Task 11 (Tk double-click ordering).
- [x] Spec §7 (file structure) → exactly mirrored in "File Structure" above.
- [x] Spec §8 (testing) → Tasks 11, 12, 13.
- [x] Spec §9 (anti-patterns) → no subprocess (AP-1 ✓), no Tool.execute (AP-3 ✓), no LLM (AP-11 ✓), `JarvisConfig` is `extra=ignore` by default for unknown roots so AP-16 is not triggered (verified via `jarvis/core/config.py:922` — no `model_config` on root class).
- [x] No placeholders, no "TODO", no "similar to Task N".
- [x] Type consistency: `MascotPosition`, `_ScreenSnapshot`, `ResolvedPlacement`, `_DragState`, `DRAG_THRESHOLD_PX`, `JARVIS_TOML_PATH`, `WIN_W` — all defined exactly once and referenced consistently.
