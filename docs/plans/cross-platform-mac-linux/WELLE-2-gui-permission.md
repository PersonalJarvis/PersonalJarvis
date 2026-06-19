# WELLE-2 — Permission / GUI-heavy ports (parallel)

> Canonical decisions: `_FROZEN-DECISIONS.md` (AD-10 UI-element-click AX+AT-SPI,
> AD-11 Orb `OverlaySurface` ladder, AD-13 permission detect-and-degrade, AD-14
> dependency grouping). Orb-framework verdict is fixed in Wave 0's
> `ADR-orb-framework.md`: the **live** orb is Tk `ui/orb/overlay.py`; the PySide6
> `OS-Level/src/overlay/` tree is abandoned and must not be re-imported.

---

## Goal

Port the two features that touch the GUI and OS permission surfaces, where CI
can prove the logic (role normalization, factory selection, headless surface
construction) but the *visible* behavior (an AX/AT-SPI tree captured from a real
app, a transparent orb, a permission prompt) needs a one-time live sign-off
(AD-3). **UI-element-click** gains a macOS AX-tree source (`pyobjc`) and a Linux
AT-SPI source (`pyatspi`), both satisfying the existing `VisionSource` Protocol
(`protocols.py:419`) and both normalizing their native accessibility roles into
the canonical UIA role vocabulary so the model prompt and tests stay
platform-agnostic (AD-10). The **Orb** gains an `OverlaySurface` abstraction with
a 3-tier visual ladder: the Tk color-key surface (Windows + macOS, where
`-transparentcolor` works), a best-effort transparent surface on a Linux
compositor, and a `TrayOnlySurface` fallback driving the already-cross-platform
pystray tray (`jarvis/ui/tray.py`). Both ports degrade per AD-13: a missing
permission or empty native tree logs an English onboarding message and falls back
(to the pixel-click path / to the tray) — never silently empty, never hard-block.

---

## Sub-tasks

### 2.1 — macOS AX-tree `VisionSource` (`pyobjc` AXUIElement)

- **Create:** `jarvis/vision/ax_tree.py` (`AXTreeSource`), `tests/fakes/fake_ax_api.py`,
  `tests/unit/vision/test_ax_tree.py`.
- **Approach:**
  - `AXTreeSource` satisfies `VisionSource` (`protocols.py:419`): `name = "ax-tree"`,
    `kind = "ui_tree"`, `async def observe(...) -> Observation`, `async def close()`.
    It returns the same `Observation` dataclass (`protocols.py:402`) carrying a
    tuple of `UIANode` (`protocols.py:394`) — same field layout
    (`role`, `name`, `automation_id`, `bounds`, `enabled`, `parent_index`) the
    Windows `UIATreeSource` produces (`uia_tree.py:45`), so downstream pruning,
    serialization, and the model prompt are identical.
  - Lazy-import the pyobjc frameworks **inside** `observe` (never at module scope —
    import-cleanliness gate from Wave 0): `Quartz`/`ApplicationServices` /
    `HIServices` `AXUIElementCreateApplication`, `AXUIElementCopyAttributeValue`
    (`kAXRoleAttribute`, `kAXTitleAttribute`/`kAXValueAttribute`,
    `kAXPositionAttribute`/`kAXSizeAttribute`, `kAXEnabledAttribute`,
    `kAXChildrenAttribute`). Get the frontmost app via
    `NSWorkspace.frontmostApplication().processIdentifier()`.
  - Walk the AX tree depth-first, flattening into `UIANode` with `parent_index`,
    and reuse the existing pruning ladder (`uia_tree.py:42` `_DEPTH_RETRY_LADDER`
    `(6, 5, 4)` and `prune_tree` from `pruning.py`). Convert AX positions
    (`{x,y}` + `{w,h}`) into the `(x, y, w, h)` bounds tuple.
  - **Permission gate (AD-13):** before walking, check `AXIsProcessTrusted()`.
    If `False`, log the English onboarding message ("macOS Accessibility
    permission not granted — grant it in System Settings › Privacy & Security ›
    Accessibility to enable named UI clicks; falling back to pixel clicks") and
    return an `Observation` with empty `nodes` + `source="screenshot_only"`. The
    empty-tree path self-gates the consumers back to the pixel-click loop
    (same contract as `_foreground_clickable_labels` returning `[]` at
    `screenshot_only_loop.py:1078-1095`). Never raise.
- **Acceptance criteria:**
  - `pytest tests/unit/vision/test_ax_tree.py -v` green (drives `AXTreeSource`
    against `fake_ax_api.py` returning a canned AX tree; asserts the flattened
    `UIANode` list, the role normalization, and the empty-`nodes` degrade when the
    fake reports `AXIsProcessTrusted()==False`).
  - `python -c "from jarvis.vision.ax_tree import AXTreeSource; from jarvis.core.protocols import VisionSource; assert isinstance(AXTreeSource(), VisionSource)"` exits 0 (runtime-checkable Protocol conformance) on any OS.
  - `python -c "import ast; m=ast.parse(open('jarvis/vision/ax_tree.py').read()); assert not any(getattr(n,'names',None) and any(a.name in ('Quartz','HIServices','ApplicationServices') for a in n.names) for n in ast.walk(m) if isinstance(n,ast.Import))"` exits 0 (no module-scope pyobjc import).
  - **Marked `skip_ci`** for any test needing a real AX grant; logic tests run on every leg.

### 2.2 — Linux AT-SPI `VisionSource` (`pyatspi`)

- **Create:** `jarvis/vision/atspi_tree.py` (`AtspiTreeSource`),
  `tests/fakes/fake_atspi.py`, `tests/unit/vision/test_atspi_tree.py`.
- **Approach:**
  - `AtspiTreeSource` satisfies `VisionSource` exactly like 2.1 (`name = "atspi-tree"`,
    `kind = "ui_tree"`). Lazy-import `pyatspi` inside `observe` — AD-14:
    `pyatspi` is **not on PyPI** (GObject-Introspection, distro-packaged via
    `apt install python3-pyatspi gir1.2-atspi-2.0`), so the import is guarded and
    its absence is a logged degrade, not a crash.
  - Use `pyatspi.Registry.getDesktop(0)`, find the active/focused application,
    walk `Accessible` children, reading `getRole()` (→ `pyatspi.ROLE_*`),
    `name`, `getState()` (for `enabled`), and the `Component` interface
    `getExtents(pyatspi.DESKTOP_COORDS)` for bounds. Flatten into `UIANode` with
    `parent_index`, reusing `prune_tree`/`_DEPTH_RETRY_LADDER`.
  - **AT-SPI bus gate (AD-13):** the bus may be unreachable (no `at-spi-bus-launcher`,
    headless, or `pyatspi` not installed). Probe via `capabilities.has_ax_tree`
    (Wave 0's `find_spec("pyatspi")`) plus a cheap `getDesktop(0)` reachability
    check; on failure log "Linux AT-SPI accessibility bus unavailable — install
    python3-pyatspi + gir1.2-atspi-2.0 and ensure the AT-SPI bus is running;
    falling back to pixel clicks" and return empty `nodes`. Never raise.
- **Acceptance criteria:**
  - `pytest tests/unit/vision/test_atspi_tree.py -v` green (drives against
    `fake_atspi.py`; asserts the flattened tree, role normalization, and the
    empty-`nodes` degrade when the bus is reported unreachable).
  - `python -c "from jarvis.vision.atspi_tree import AtspiTreeSource; from jarvis.core.protocols import VisionSource; assert isinstance(AtspiTreeSource(), VisionSource)"` exits 0.
  - `python -c "import ast; m=ast.parse(open('jarvis/vision/atspi_tree.py').read()); assert not any(getattr(n,'names',None) and any(a.name=='pyatspi' for a in n.names) for n in ast.walk(m) if isinstance(n,ast.Import))"` exits 0 (no module-scope `pyatspi`).

### 2.3 — Native-role → canonical-UIA-role normalization

- **Create:** `jarvis/vision/role_map.py` (the AX and AT-SPI → UIA role tables +
  `normalize_role(native_role, platform) -> str`), `tests/unit/vision/test_role_map.py`.
- **Approach:**
  - The canonical vocabulary is the union already used by the pipeline:
    `DEFAULT_INTERESTING_ROLES` (`pruning.py:51`: `Button, Edit, ComboBox, List,
    ListItem, Tab, MenuItem, CheckBox, RadioButton, Hyperlink, Text`) and
    `_CLICKABLE_UIA_ROLES` (`screenshot_only_loop.py:1072`: `Button, MenuItem,
    ListItem, TabItem, CheckBox, RadioButton, Hyperlink, Edit, ComboBox, TreeItem,
    SplitButton, Text`). `role_map.py` maps native roles onto this set so the
    model never sees an `AXButton` or `push button` — it sees `Button`.
  - **macOS AX table:** `AXButton`→`Button`, `AXTextField`/`AXTextArea`→`Edit`,
    `AXPopUpButton`/`AXComboBox`→`ComboBox`, `AXMenuItem`→`MenuItem`,
    `AXCheckBox`→`CheckBox`, `AXRadioButton`→`RadioButton`, `AXLink`→`Hyperlink`,
    `AXStaticText`→`Text`, `AXTabGroup` child→`TabItem`, `AXRow`/`AXCell`→`ListItem`,
    `AXOutlineRow`→`TreeItem`, etc. Unknown roles map to `Text` (visible but not
    pixel-guessed) or are dropped by `filter_by_role`.
  - **Linux AT-SPI table:** `ROLE_PUSH_BUTTON`→`Button`, `ROLE_TEXT`/`ROLE_ENTRY`→`Edit`,
    `ROLE_COMBO_BOX`→`ComboBox`, `ROLE_MENU_ITEM`→`MenuItem`,
    `ROLE_CHECK_BOX`→`CheckBox`, `ROLE_RADIO_BUTTON`→`RadioButton`,
    `ROLE_LINK`→`Hyperlink`, `ROLE_LABEL`→`Text`, `ROLE_PAGE_TAB`→`TabItem`,
    `ROLE_LIST_ITEM`/`ROLE_TABLE_CELL`→`ListItem`, `ROLE_TREE_ITEM`→`TreeItem`.
  - 2.1 and 2.2 call `normalize_role(...)` while flattening, so the `UIANode.role`
    written into the `Observation` is always canonical UIA.
- **Acceptance criteria:**
  - `pytest tests/unit/vision/test_role_map.py -v` green (asserts representative
    AX + AT-SPI roles map to the canonical set, and that every output role is a
    member of `_CLICKABLE_UIA_ROLES | set(DEFAULT_INTERESTING_ROLES)`).
  - `python -c "from jarvis.vision.role_map import normalize_role; assert normalize_role('AXButton','darwin')=='Button' and normalize_role('ROLE_PUSH_BUTTON','linux')=='Button'"` exits 0.

### 2.4 — `tree_factory` + rewire the 6 hardcoded `UIATreeSource()` literals

- **Create:** `jarvis/vision/tree_factory.py` (`make_ui_tree_source() -> VisionSource`),
  `tests/unit/vision/test_tree_factory.py`.
- **Modify (replace the literal `UIATreeSource()` with `make_ui_tree_source()`):**
  - `jarvis/plugins/tool/click_element.py:125`
  - `jarvis/plugins/tool/read_visible_ui_state.py:60`
  - `jarvis/plugins/tool/wait_for_element.py:97`
  - `jarvis/plugins/tool/wait_for_ui_state.py:78`
  - `jarvis/vision/engine.py:71` (the `uia_source or UIATreeSource()` default)
  - `jarvis/harness/screenshot_only_loop.py:1092` (inside `_foreground_clickable_labels`)
- **Approach:**
  - `make_ui_tree_source()` selects on `detect_platform()` + `capabilities`:
    `win32`→`UIATreeSource()` (unchanged, AD-7); `darwin`→`AXTreeSource()`;
    `linux`→`AtspiTreeSource()` if `capabilities.has_ax_tree` else a
    **null source** whose `observe` returns an empty `Observation`
    (`source="screenshot_only"`, no nodes) and logs once. The null source is the
    AD-6 graceful fallback and is also what every consumer already treats as
    "no labels → pixel path".
  - The DI seams already exist: every consumer takes `self._vision_source or
    <literal>` (`click_element.py:125`, `read_visible_ui_state.py:60`,
    `wait_for_element.py:97`, `wait_for_ui_state.py:78`) or
    `uia_source=` (`engine.py:62-71`). The change is mechanical: swap the literal
    default for the factory call. The lazy `from jarvis.vision.uia_tree import
    UIATreeSource` import guard at `click_element.py:117-123` becomes a
    `from jarvis.vision.tree_factory import make_ui_tree_source` import.
  - Keep `screenshot_only_loop.py`'s `_foreground_clickable_labels` "returns `[]`
    on any failure" contract (`:1093`) intact — the factory's null source produces
    empty nodes, which already yields `[]`.
- **Acceptance criteria:**
  - `grep -rn "UIATreeSource()" jarvis/` shows only `tree_factory.py` (the Windows branch) and `uia_tree.py` itself — none of the 6 former call sites.
  - `pytest tests/unit/vision/test_tree_factory.py -v` green (asserts per-platform selection + the Linux null-source degrade).
  - `pytest tests/unit/vision/test_engine.py tests/contract/test_vision_source_protocol.py -v` green (existing engine + contract suites unbroken; the contract suite at `test_vision_source_protocol.py:26` already parametrizes `VisionSource` impls — add `AXTreeSource`/`AtspiTreeSource` to it).
  - `python -c "from jarvis.vision.tree_factory import make_ui_tree_source; from jarvis.core.protocols import VisionSource; assert isinstance(make_ui_tree_source(), VisionSource)"` exits 0 on every OS.

### 2.5 — Orb: `OverlaySurface` protocol + `TkColorKeyOverlay` (Win + Mac)

- **Create:** `jarvis/overlay/surface.py` (`OverlaySurface` `Protocol` +
  `make_overlay_surface()` factory + `TkColorKeyOverlay`), `tests/fakes/fake_overlay_surface.py`,
  `tests/overlay/test_overlay_surface.py`.
- **Modify:** none of `ui/orb/overlay.py`'s rendering — the live Tk orb
  (`OrbOverlay` at `ui/orb/overlay.py:1236`, `wm_attributes("-transparentcolor",
  COLOR_KEY_HEX)` at `:966`/`:1331`) is wrapped, not rewritten (AD-7 + the Wave 0
  verdict).
- **Approach:**
  - `OverlaySurface` `Protocol`: `start() -> None`, `stop() -> None`,
    `set_state(state) -> None`, `is_visible() -> bool` — the minimal surface the
    desktop bridge already drives.
  - `TkColorKeyOverlay` wraps `OrbOverlay`. Per the Wave 0 ADR, Tk
    `-transparentcolor` (Win32 `LWA_COLORKEY` on Windows, the Carbon/Cocoa
    equivalent on macOS) works on **both** Windows and macOS — so this surface is
    the default on `win32` and `darwin`. No code change to the color-key path; the
    wrapper just adapts the lifecycle to the `OverlaySurface` Protocol.
  - The Windows-only `SetSystemCursor` swap (`jarvis/overlay/system_cursor.py`)
    stays Windows-only — it is already a no-op off Windows (it lazy-loads
    `ctypes.windll`/Win32). Do **not** wire it into the cross-platform surface;
    leave its existing call site (`jarvis/ui/desktop_app.py`) gated on Windows.
  - `make_overlay_surface()` selects on `detect_platform()` + `capabilities`:
    `win32`/`darwin` → `TkColorKeyOverlay` (when `capabilities.has_overlay`);
    `linux` → `LinuxBestEffortOverlay` (2.6) or `TrayOnlySurface` (2.6);
    `not has_overlay` → `TrayOnlySurface`.
- **Acceptance criteria:**
  - `pytest tests/overlay/test_overlay_surface.py -v` green under
    `QT_QPA_PLATFORM=offscreen` (the headless guard at `tests/overlay/conftest.py:21`);
    constructs `TkColorKeyOverlay` against `fake_overlay_surface.py` and asserts the
    lifecycle + factory selection (no real window).
  - `python -c "from jarvis.overlay.surface import make_overlay_surface, OverlaySurface; assert isinstance(make_overlay_surface(), OverlaySurface)"` exits 0 on every OS (never raises).
  - `python -c "import ui.orb.overlay"` still resolves on Windows (live orb import unbroken — Wave 0's 0.7 keeps the `sys.path` insert).

### 2.6 — Orb: Linux best-effort transparent surface + `TrayOnlySurface` fallback

- **Create:** `jarvis/overlay/linux_surface.py` (`LinuxBestEffortOverlay`),
  `jarvis/overlay/tray_surface.py` (`TrayOnlySurface`), `tests/overlay/test_tray_surface.py`.
- **Approach:**
  - `LinuxBestEffortOverlay`: attempt the Tk `-transparentcolor` path on a Linux
    compositor (it works under some compositing window managers, fails under
    others). On a non-compositing/Wayland session it cannot key out the color —
    detect that (`capabilities.is_wayland` or a failed `wm_attributes` probe) and
    **fall through to `TrayOnlySurface`** with a logged English message. Never
    show an opaque magenta box (the failure mode the color-key avoids).
  - `TrayOnlySurface` is the universal floor: it drives the existing
    cross-platform pystray tray (`jarvis/ui/tray.py` — no platform marker, already
    renders `JarvisState` icons via PIL at `tray.py:39`). `set_state(state)` maps
    the orb's state onto `JarvisState` (`tray.py:20-26`) so the user still gets
    IDLE/LISTENING/THINKING/SPEAKING feedback through the tray icon color
    (`_STATE_COLORS` at `tray.py:29`). This satisfies AD-11's "guarantee *some*
    presence everywhere" floor.
  - Wire `make_overlay_surface()` (2.5) to return `LinuxBestEffortOverlay` when
    `display_present and not is_wayland`, else `TrayOnlySurface`.
- **Acceptance criteria:**
  - `pytest tests/overlay/test_tray_surface.py -v` green (asserts `TrayOnlySurface`
    maps orb states onto `JarvisState` and drives the pystray tray via a fake; no
    real tray thread).
  - `python -c "import jarvis.platform; ...; print(type(make_overlay_surface()).__name__)"` prints `LinuxBestEffortOverlay` or `TrayOnlySurface` on a Linux runner (never `TkColorKeyOverlay`, never a raise).
  - On a headless Linux CI leg (`no DISPLAY`), `make_overlay_surface()` returns `TrayOnlySurface` and `start()`/`stop()` are no-op-safe.

### 2.7 — Dependency grouping: new `[desktop-macos]` extra

- **Modify:** `pyproject.toml` `[project.optional-dependencies]` — add a new
  `desktop-macos` group next to `desktop` (`pyproject.toml:99-110`).
- **Approach:** per AD-14, mirror the `sys_platform` marker pattern:
  ```toml
  desktop-macos = [
      "pyobjc-framework-Quartz>=10; sys_platform == 'darwin'",
      "pyobjc-framework-ApplicationServices>=10; sys_platform == 'darwin'",
      "pyobjc-framework-Accessibility>=10; sys_platform == 'darwin'",
  ]
  ```
  Do **not** add `pyatspi` anywhere — it is distro-packaged (AD-14) and surfaced
  via the `capabilities.has_ax_tree` runtime probe + a documented `apt install
  python3-pyatspi gir1.2-atspi-2.0` prerequisite. Document both in the README
  capability matrix (a Wave-4 doc task references this).
- **Acceptance criteria:**
  - `python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); g=d['project']['optional-dependencies']['desktop-macos']; assert all('darwin' in x for x in g) and any('Accessibility' in x for x in g)"` exits 0.
  - The macOS/Linux CI legs install **only** `.[dev]` (not `.[desktop-macos]`, not distro `pyatspi`) and stay green — proving the base install imports clean (Wave 0's import gate covers `ax_tree`/`atspi_tree`).

---

## Parallelism

Two largely independent worktrees:

- **Worktree D — UI-element-click:** 2.1 + 2.2 + 2.3 + 2.4 + the `pyatspi` doc
  note in 2.7. These are coupled (the factory in 2.4 imports the two sources and
  the role map) and best done together; 2.3 (`role_map.py`) can be a quick first
  PR that 2.1/2.2 build on.
- **Worktree E — Orb:** 2.5 + 2.6 + the `desktop-macos` extra in 2.7
  (`jarvis/overlay/`, `pyproject.toml`).

The only shared file is `pyproject.toml` (2.7) — split it: Worktree E owns the
`desktop-macos` block; Worktree D adds the one-line `pyatspi` comment/doc note in
the README, not pyproject. Each worktree runs `pwsh scripts/preflight.ps1` first.
The 6 consumer-site edits in 2.4 touch `jarvis/plugins/tool/*` and
`jarvis/vision/engine.py` + `jarvis/harness/screenshot_only_loop.py` — all owned
by Worktree D, no overlap with Worktree E.

## EK acceptance gate

This wave advances **EK-2** (UI-element-click and Orb now have per-OS
implementations behind their seams, degrading to a logged fallback — pixel-click
path / tray — when the capability/permission is absent) and **EK-3** (new fakes
`fake_ax_api.py`, `fake_atspi.py`, `fake_overlay_surface.py` + unit tests, no
`unittest.mock`). The GUI/permission verification (AX/AT-SPI tree capture, Orb
transparency) is **deferred to Wave 4's one-time live sign-off** per AD-3 — this
wave proves only the logic in CI and marks the permission-dependent tests
`skip_ci`. It thereby sets up **EK-5** (the live sign-off notes Wave 4 writes).

## Dependencies on prior waves

**Wave 0** (the `jarvis/platform/` factory + `capabilities.has_ax_tree`/
`has_overlay`/`is_wayland` probes, the green CI matrix, and the
`ADR-orb-framework.md` verdict that fixes the Tk orb as the wrap target).
Independent of Wave 1 (terminal/app-launch/hotkey) and Wave 3 (admin) — can run
in parallel with neither blocking the other. Must merge before Wave 4 so the
live sign-off has the AX/AT-SPI/Orb code to verify.
