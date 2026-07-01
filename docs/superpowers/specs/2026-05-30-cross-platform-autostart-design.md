# Cross-Platform Autostart-at-Login — Design

- **Date:** 2026-05-30
- **Status:** Approved (brainstorming) → ready for implementation plan
- **Author:** Jarvis-Agent (brainstorming session)
- **Scope:** Make Jarvis start automatically at login on Windows, macOS, and
  Linux; default ON; user-toggleable in Settings; self-healing against stale
  paths. Headless/VPS = graceful no-op.

---

## 1. Problem

The user reports: *"Sometimes after a reboot Jarvis does not start automatically,
and then 'Hey Jarvis' obviously cannot work either."*

Today's reality in the repo:

1. **Two divergent, Windows-only autostart paths**, both default OFF:
   - `scripts/install_shortcuts.py` writes a full `shell:startup\Personal
     Jarvis.lnk` (via `WScript.Shell`).
   - `jarvis/setup/wizard.py::_install_autostart()` writes a crude
     `Startup\Jarvis.bat` fallback.
   These two disagree on file name and mechanism, and neither runs on
   macOS/Linux.
2. **The wizard asks with default "No"** (`wizard.py:489`,
   `_ask_yesno(..., default=False)`), so most installs never set autostart up at
   all → the most likely cause of "does not start after reboot".
3. **Hardcoded path drift.** A `.lnk`/`.bat` encodes a fixed project path. If the
   project folder is moved or re-cloned (the recurring BUG-006 four-layer restore
   trap), the autostart entry points at a dead path → "sometimes does not start".
4. **No Settings toggle** for autostart anywhere.

The fix is a single cross-platform autostart capability, default-on, with a
working Settings toggle, that self-heals path drift on every boot.

## 2. Goals / Non-Goals

**Goals**

- One autostart mechanism per OS (Windows / macOS / Linux), behind a shared seam.
- Default **ON**; applied to the *current* install on the next boot (no manual
  step), per the user's "on unless explicitly disabled" mandate.
- A Settings toggle (`GET/PUT /api/settings/autostart`) that installs/removes the
  OS entry **live** (no restart) and persists `[autostart].enabled`.
- **Self-healing**: on every boot, if `enabled=true` and the entry is
  missing/stale, recreate it; if `enabled=false` and present, remove it.
- The autostart entry launches the **full voice app** (`-m
  jarvis.ui.web.launcher`), so "Hey Jarvis" works after boot.
- Zero new hard dependencies. Headless/VPS base install still boots and the
  autostart manager degrades to a logged no-op (AD-6).
- Wizard installs autostart cross-platform (default Yes).

**Non-Goals**

- Process-crash robustness on boot (stale `jarvis.lock`, the "silent pipeline
  init crash" class). Out of scope; flagged as a separate follow-up. This design
  guarantees the *entry exists and points at the right install*, not that the
  launched process never crashes.
- Boot-without-login on Linux (systemd `--user` + linger). Linux uses the XDG
  `.desktop` login-autostart path (decided in brainstorming). A systemd unit may
  be documented later but is not built here.
- Running Jarvis as a Windows Service (forbidden — AP-17, SYSTEM has no mic).

## 3. Architecture — the 7th cross-platform port

Autostart-at-login follows the established "six ports" pattern (CLAUDE.md →
*Cross-platform desktop features*): one `Protocol` + one per-OS implementation +
a `detect_platform()`/capability factory + a graceful logged null-fallback
(AD-5/AD-6). The Windows behaviour is preserved (reuses the proven
`WScript.Shell` path from `install_shortcuts.py`, AD-7 spirit).

New feature package `jarvis/autostart/` (parallel to `jarvis/admin/`,
`jarvis/terminal/`, `jarvis/overlay/`):

```
jarvis/autostart/
  __init__.py     # public API: AutostartManager, make_autostart_manager,
                  # reconcile_autostart, resolve_launch_spec
  protocol.py     # AutostartManager Protocol + AutostartStatus dataclass
  command.py      # resolve_launch_spec() -> LaunchSpec (single source of truth)
  windows.py      # WindowsAutostart  (shell:startup .lnk via PowerShell)
  macos.py        # MacOSAutostart    (LaunchAgent plist)
  linux.py        # LinuxAutostart    (XDG ~/.config/autostart/*.desktop)
  null.py         # NullAutostart     (headless / no display / unknown OS)
  factory.py      # make_autostart_manager(capabilities) -> AutostartManager
```

**Import-cleanliness (HN-7):** nothing in this package imports a platform-only
module (`winreg`, `win32*`, `pyobjc`, …) at module scope. The Windows
implementation shells out to PowerShell (subprocess) exactly like
`install_shortcuts.py` does today, so there is no `pywin32` import at all. macOS
and Linux are pure `pathlib` text writes. **No new dependency in any extras
group.**

### 3.1 `AutostartManager` protocol

```python
@dataclass(frozen=True, slots=True)
class LaunchSpec:
    program: str        # absolute interpreter path (pythonw.exe / python3)
    args: tuple[str, ...]   # ("-m", "jarvis.ui.web.launcher")
    working_dir: str    # PROJECT_ROOT, resolved from jarvis.__file__
    minimized: bool     # window hint (Windows WindowStyle, others ignore)

@dataclass(frozen=True, slots=True)
class AutostartStatus:
    supported: bool         # does this OS/seat support login autostart?
    installed: bool         # is an entry present right now?
    matches_spec: bool      # does the present entry point at the current install?
    entry_path: str | None  # where the entry lives (for diagnostics)
    detail: str             # human-readable English status line

class AutostartManager(Protocol):
    def status(self, spec: LaunchSpec) -> AutostartStatus: ...
    def install(self, spec: LaunchSpec) -> AutostartStatus: ...   # idempotent
    def uninstall(self) -> AutostartStatus: ...                   # idempotent
```

`install` and `uninstall` are idempotent and never raise for an
already-applied/already-absent state. They raise only on a genuine I/O failure;
callers (reconcile + route) wrap in try/except.

### 3.2 `LaunchSpec` resolution — `command.py` (the bug fix)

`resolve_launch_spec(cfg) -> LaunchSpec` derives everything **at runtime** from
the running package:

- `working_dir` = `PROJECT_ROOT` derived from `Path(jarvis.__file__)` (NOT a
  stored absolute string). This is what kills the stale-path class for
  autostart — the entry always targets the clone that is actually running.
- `program`:
  - **Windows:** `pythonw.exe` resolved via the existing `_detect_pythonw`
    strategy (project `.venv` → `sys.executable` sibling → `where pythonw.exe`).
    GUI subsystem → no console window (BUG-012 hygiene).
  - **macOS/Linux:** `sys.executable`.
- `args` = `("-m", "jarvis.ui.web.launcher")` — the full voice + Orb desktop app
  (voice enabled by default; this is what makes "Hey Jarvis" available after
  boot). NOT `--headless`.
- `minimized` from `cfg.autostart.start_minimized` (default True; Windows maps it
  to `WindowStyle=7`, others ignore it).

`LaunchSpec` is frozen + comparable so the OS implementations can detect "the
present entry no longer matches the current install" (path drift) by reading the
entry back and comparing the encoded command.

### 3.3 Per-OS implementations

- **Windows (`windows.py`)** — entry: `%APPDATA%\Microsoft\Windows\Start
  Menu\Programs\Startup\Personal Jarvis.lnk`. Created via the same
  PowerShell + `WScript.Shell` snippet as `install_shortcuts.py`
  (`create_shortcut`). `status()` reads the `.lnk` target+args back via
  `WScript.Shell.CreateShortcut(path)` and compares against the spec.
  `uninstall()` deletes the `.lnk` (and the legacy `Jarvis.bat`/`Jarvis.lnk`
  names, to clean up the old wizard hack). `supported=True` when
  `display_present`.
- **macOS (`macos.py`)** — entry:
  `~/Library/LaunchAgents/com.personal-jarvis.autostart.plist`,
  `RunAtLoad=true`, `ProgramArguments=[program, *args]`, `WorkingDirectory`,
  `RunAtLoad`. **LaunchAgent (per-user), not a LaunchDaemon** — the agent runs in
  the user's GUI session so it keeps microphone access. `install()` writes the
  plist; best-effort `launchctl load -w` so it also arms in the current session
  (failure is non-fatal — RunAtLoad covers the next login). `status()` parses the
  plist `ProgramArguments`/`WorkingDirectory`. `uninstall()` best-effort
  `launchctl unload` + delete the plist.
- **Linux (`linux.py`)** — entry:
  `~/.config/autostart/personal-jarvis.desktop` (respects
  `$XDG_CONFIG_HOME`). Standard freedesktop `[Desktop Entry]`:
  `Type=Application`, `Exec=<program> -m jarvis.ui.web.launcher`,
  `X-GNOME-Autostart-enabled=true`, `Hidden=false`, `Name=Personal Jarvis`.
  `status()` parses `Exec=`. `uninstall()` deletes the file. `supported` requires
  `display_present` (a headless Linux box has no login session to autostart a GUI
  into).
- **Null (`null.py`)** — every method returns
  `AutostartStatus(supported=False, installed=False, matches_spec=False,
  entry_path=None, detail="Autostart-at-login is not available on this host …")`
  and logs at debug. Used for headless (no display) and unknown platforms.

### 3.4 Factory

```python
def make_autostart_manager(caps: Capabilities) -> AutostartManager:
    if not caps.display_present:
        return NullAutostart(reason="no display (headless)")
    if caps.platform == "win32":  return WindowsAutostart()
    if caps.platform == "darwin": return MacOSAutostart()
    if caps.platform == "linux":  return LinuxAutostart()
    return NullAutostart(reason=f"unsupported platform {caps.platform!r}")
```

The display gate makes the VPS/headless case a no-op without any platform check
duplication — autostart-at-login is meaningless without a GUI login session.

## 4. Self-healing reconcile

`reconcile_autostart(cfg, caps) -> AutostartStatus` runs **once at boot**, off
the voice critical path, wrapped in try/except (must never block or crash boot —
AD-6 spirit):

| `cfg.autostart.enabled` | present? | matches? | action |
|---|---|---|---|
| True  | no  | —   | `install(spec)` (self-heal: first run / deleted) |
| True  | yes | no  | `install(spec)` (self-heal: path drift) |
| True  | yes | yes | no-op (idempotent) |
| False | yes | —   | `uninstall()` |
| False | no  | —   | no-op |
| (null manager / headless) | — | — | no-op + debug log |

Because `enabled` defaults to `True`, the first boot after this change finds no
entry and installs it — this is the "apply to the current install now" behaviour
the user asked for. A user who manually deletes the entry while `enabled=true`
gets it recreated next boot; the Settings toggle is the intended off-switch (a
decision made in brainstorming).

**Call site:** in the launcher boot sequence (`jarvis/ui/web/launcher.py`), after
config load, before/around server start. It is a handful of file ops; never on
the hot path; the result is logged (one line) and otherwise ignored.

## 5. Config

New model in `jarvis/core/config.py`:

```python
class AutostartConfig(BaseModel):
    # extra="allow" so a future [autostart.*] sub-key or a self-mod/drift write
    # never trips pre-validate (AP-16).
    model_config = ConfigDict(extra="allow")
    enabled: bool = True            # default ON (user mandate); headless = no-op
    start_minimized: bool = True    # Windows WindowStyle=7; others ignore
```

Added to `JarvisConfig`:
`autostart: AutostartConfig = Field(default_factory=AutostartConfig)`.

`jarvis/core/config_writer.py`: new `set_autostart(enabled: bool) -> None`,
atomic write of `[autostart].enabled`, mirroring `set_reply_language` /
`set_ptt_hotkey` (lock + tempfile + BOM-safe, AP-7).

## 6. Settings API (folded into `settings_routes.py`)

Reuses the established route pattern (live-apply + best-effort persist).

`GET /api/settings/autostart` →
```json
{ "enabled": true, "supported": true, "installed": true,
  "matches_spec": true, "platform": "win32",
  "resolved_command": "…pythonw.exe -m jarvis.ui.web.launcher",
  "entry_path": "…\\Startup\\Personal Jarvis.lnk", "detail": "…" }
```

`PUT /api/settings/autostart` body `{ "enabled": bool, "persist": true }`:
1. persist `[autostart].enabled` via `config_writer.set_autostart` (best-effort).
2. update in-memory `cfg.autostart.enabled`.
3. **live-apply:** `install(spec)` when enabling, `uninstall()` when disabling.
4. return `{ ok, enabled, supported, installed, applied_live, detail }`.

On an unsupported/headless host: `enabled` still persists, but `supported=false`
and `installed=false`, and `detail` says so honestly — never claim it worked when
the OS cannot honour it (mirrors the wake-word `degraded` honesty contract).

## 7. UI

`jarvis/ui/web/frontend/src/views/SettingsView.tsx`: a new panel "Start at login"
with a toggle, mirroring the existing Wake-Word / Hotkey panels:

- Reads `GET /api/settings/autostart` on mount.
- Toggle → `PUT`; reflects `applied_live`.
- When `supported === false`: render the switch disabled + a caption
  ("Not available on this host — e.g. a headless server.").
- i18n keys added to `de.json`, `en.json`, `es.json` (English source string per
  Output Language Policy; German/Spanish translations follow existing keys).

## 8. Wizard + legacy cleanup

- `jarvis/setup/wizard.py::step_finalize`: prompt default flips to **Yes**;
  installation goes through the new port (cross-platform) via
  `reconcile_autostart` or a direct `manager.install(spec)`; on "no",
  `config_writer.set_autostart(False)`. The crude Windows-only
  `_install_autostart()` `.bat` hack is removed. Touched copy is English (Output
  Language Policy; step 5 is already English).
- `scripts/install_shortcuts.py`: the **autostart branch delegates** to
  `jarvis.autostart` (one Windows autostart implementation, not two). The desktop
  double-click icon shortcut + icon generation stay in this script (separate
  concern). `--no-autostart` maps to `set_autostart(False)`; `--uninstall` also
  calls `manager.uninstall()`.

## 9. Testing

CI-provable on **any** OS (pure logic / text writes into a temp `HOME`):

- `factory` routing per platform (monkeypatch `detect_capabilities`); null on
  `display_present=False`; null on unknown platform.
- `resolve_launch_spec`: `pythonw` selection on win, `sys.executable` on posix;
  `working_dir` derived from `jarvis.__file__`; args correct.
- `reconcile_autostart` decision table (all six rows).
- Linux `.desktop` writer: file contents (`Exec=`, `X-GNOME-Autostart-enabled`),
  round-trip `status()` parse, `matches_spec` flips on a drifted `Exec=`.
- macOS plist writer: `ProgramArguments`/`WorkingDirectory`, round-trip parse
  (`plistlib`, stdlib).
- config round-trip: `set_autostart(True/False)` → reload → value present.
- Route tests for `GET`/`PUT` (mirror the settings_routes test style).

Live sign-off only (honestly labelled `unverified-on-real-desktop` until run on
a real device, per CLAUDE.md verification-honesty rule):

- Windows `.lnk` creation/read-back (needs Windows → `@pytest.mark.skip_ci`).
- "Does the OS actually launch the entry at the next login?" on each platform.

## 10. Risks & mitigations

- **Self-heal fights a user who manually removes the entry.** Accepted by design;
  the toggle is the off-switch. Documented in the UI caption + spec.
- **Launched process crashes on boot** (stale lock / init crash). Out of scope;
  flagged. Autostart guarantees the entry, not process health.
- **Drift-guard / config-soll.** `[autostart].enabled` is a new key; if the <!-- i18n-allow: literal filename identifier -->
  drift-guard's `config-soll.json` is authoritative it must learn the key, else a <!-- i18n-allow: literal filename identifier -->
  toggle could be rolled back (the BUG-010 / provider-switch class). The plan
  must add `[autostart]` to `config-soll.json` (or confirm the guard ignores <!-- i18n-allow: literal filename identifier -->
  unknown sections).
- **macOS `launchctl` quirks across versions.** `install` is best-effort on the
  `launchctl load`; correctness rests on the plist + `RunAtLoad` at next login,
  not on the live load.

## 11. Files

**New**
- `jarvis/autostart/{__init__,protocol,command,windows,macos,linux,null,factory}.py`
- `tests/unit/autostart/test_{factory,command,reconcile,linux,macos,config_route}.py`

**Modified**
- `jarvis/core/config.py` (+`AutostartConfig`, +`JarvisConfig.autostart`)
- `jarvis/core/config_writer.py` (+`set_autostart`)
- `jarvis/ui/web/settings_routes.py` (+autostart GET/PUT)
- `jarvis/ui/web/launcher.py` (call `reconcile_autostart` at boot)
- `jarvis/setup/wizard.py` (cross-platform install, default Yes, drop `.bat` hack)
- `scripts/install_shortcuts.py` (delegate autostart branch to the port)
- `jarvis/ui/web/frontend/src/views/SettingsView.tsx` (+panel)
- `jarvis/ui/web/frontend/src/i18n/locales/{de,en,es}.json` (+keys)
- `scripts/drift-guard*` / `config-soll.json` (learn `[autostart]`) — if applicable <!-- i18n-allow: literal filename identifier -->
- `CLAUDE.md` cross-platform table (+row "Autostart" — optional polish)

## 12. Open questions for the plan

- Exact insertion point of `reconcile_autostart` in `launcher.py` (after config
  load, around `_build_app`); confirm headless launcher path also reaches it
  safely (null manager makes it harmless regardless).
- Whether `config-soll.json` is authoritative on this machine and must learn <!-- i18n-allow: literal filename identifier -->
  `[autostart]` (drift-guard interaction).

## 13. Amendment 2026-06-09 — Windows promptness via a logon scheduled task

**Problem this amendment fixes.** §2/§10 listed "the launched process comes up
*promptly*" as out of scope: the original design guaranteed only that *an entry
exists and points at the right install*. In practice the `shell:startup` `.lnk` is
processed by Explorer's **serialized, throttled** Windows 11 startup queue (~one
item every ~30 s). On a machine with many startup programs the Jarvis shortcut
fired **4-8 minutes after login** (measured 2026-06-09: login 14:43:28, Jarvis up
14:50:47; the sibling Ollama `.lnk` in the same Startup folder fired 14:52). The
user reasonably read this as "autostart is broken". Forensics in
`MEMORY.md → project_bug_autostart_*`.

**Decision (supersedes the Windows row of §3.3).** Windows autostart now uses a
**per-user logon Scheduled Task** (Task Scheduler is a separate subsystem, not
subject to the Explorer startup throttle → Jarvis starts within seconds of login).
macOS (`RunAtLoad` LaunchAgent) and Linux (XDG `.desktop`) already fire promptly
at login and are **unchanged** — the throttle is Windows-only.

**Elevation reality (verified non-elevated on Windows 11).** *Registering* a task
is denied to a non-elevated process (even an Administrator account's filtered
token) — `schtasks /create`, a subfolder task, and `Register-ScheduledTask` all
return "Access denied". *Reading* task state (`Get-ScheduledTask`) is allowed.
Therefore:

- The task is (un)registered only on an **interactive** call (Settings toggle /
  wizard / `install_shortcuts`), where a single UAC prompt is expected. The new
  `AutostartManager.install(spec, *, interactive=False)` / `uninstall(*, interactive=False)`
  keyword carries this; macOS/Linux/null ignore it.
- Registration runs the privileged script via `Start-Process -Verb RunAs -Wait`;
  the task runs Jarvis **non-elevated** (`RunLevel=Limited` → microphone access,
  the AP-17 "no Windows Service" rule). The login user is *baked* into the task
  (so it targets the original user, not whoever approves UAC).
- The silent **boot reconcile** (`interactive=False`) never prompts. If the task
  is absent it ensures the no-elevation **`.lnk` fallback** so autostart still
  works (possibly delayed). The Settings panel surfaces an "enable instant start"
  upgrade (`GET …/autostart` gains `mechanism: scheduled_task | shortcut | native
  | none`) so a user on the fallback can register the task with one UAC prompt.

**Visibility.** `[autostart].start_minimized` now defaults to **False** — the
autostart launch opens the desktop window **visibly** (user choice 2026-06-09), so
"I turned on the PC" produces a visible Jarvis, not a silent tray icon.

**Tests.** `tests/unit/autostart/test_windows_task.py` (pure script builders +
decision logic with injected fakes — CI-provable anywhere);
`tests/unit/autostart/test_windows_scripts.py` keeps the `.lnk` fallback green.
Live sign-off (skip_ci): the task actually fires at the next Windows login.
