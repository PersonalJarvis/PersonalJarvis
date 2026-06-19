# WELLE-1 — Easy / CI-provable ports (3 parallel worktrees)

> Canonical decisions: `_FROZEN-DECISIONS.md` (AD-6 seam pattern, AD-8 hotkey
> dual-backend, AD-9 terminal `ptyprocess`, AD-14 dependency grouping, AD-15
> platform-conditional `KNOWN_APPS`). This wave depends on **Wave 0** being
> merged and its CI matrix green (AD-4) — every seam factory reads
> `jarvis.platform.capabilities.detect_capabilities()`.

---

## Goal

Port the three features whose logic is provable on a headless CI runner with no
GUI, no permission grant, and no real desktop: the built-in **Terminal**
(Unix-PTY via `ptyprocess`, the standout win because a real PTY runs end-to-end
on the `ubuntu-latest`/`macos-latest` runners), **App-launch** (`open`/`xdg-open`
+ a platform-conditional `KNOWN_APPS` whitelist), and the global **Hotkey**
(dual backend: Windows keeps `global-hotkeys`, macOS/Linux gain `pynput`, Wayland
degrades to a logged no-op). All three follow the uniform AD-6 seam: a `Protocol`,
a per-OS implementation, a `sys.platform` factory, and a graceful null-fallback
that logs an English message and never raises. The Windows implementations are
**untouched** (AD-7) — the new OS code is added as a sibling behind a new seam,
and the existing call sites are rewired to go through that seam. By the end of
this wave the Terminal, App-launch resolution, and Hotkey *logic* are CI-verified
on all three OS legs (EK-4), the terminal via a real PTY.

---

## Sub-tasks

### 1.1 — Terminal: `PtyBackend` protocol + `UnixPtyBackend` (ptyprocess)

- **Create:** `jarvis/terminal/backend.py` (the `PtyBackend` `Protocol` + a
  `WinptyBackend` thin wrapper + `UnixPtyBackend` + a `make_pty_backend()`
  factory), `tests/fakes/fake_pty_backend.py`, `tests/unit/terminal/test_unix_pty.py`.
- **Modify:** `jarvis/terminal/pty_manager.py` — replace the inline
  `from winpty import PtyProcess` at `pty_manager.py:71` and the five direct
  `proc.*` calls (`proc.write` `:127`, `proc.setwinsize` `:138`,
  `proc.terminate(force=True)` `:173`, `proc.read(4096)` `:193`,
  `proc.isalive()`/`proc.exitstatus` `:206`/`:225`) with calls through the
  `PtyBackend` seam. The daemon-thread read-loop (`_reader_loop` `:179-239`)
  stays structurally identical — AD-9 demands *no async rewrite*.
- **Approach:**
  - Define `PtyBackend` as a `Protocol` with exactly the five methods the
    read-loop already uses, normalized to a single signature:
    `spawn(argv, cwd, cols, rows) -> PtyHandle`, and on the handle
    `write(str) -> None`, `setwinsize(rows, cols) -> None`,
    `read(size) -> str` (decode bytes→str at the seam, mirroring the existing
    `isinstance(data, bytes)` branch at `pty_manager.py:211`), `isalive() -> bool`,
    `terminate(force: bool) -> None`, `exitstatus -> int | None`, `pid -> int`.
  - `WinptyBackend` lazy-imports `winpty.PtyProcess` (preserve the exact
    `RuntimeError("pywinpty nicht installiert …")` degrade at `pty_manager.py:72-75`).
    `UnixPtyBackend` lazy-imports `ptyprocess.PtyProcess`. `ptyprocess` mirrors
    the methods 1:1 (`PtyProcess.spawn(argv, dimensions=(rows, cols), cwd=…)`,
    `.write(bytes)`, `.read(size) -> bytes`, `.setwinsize(rows, cols)`,
    `.terminate(force=…)`, `.isalive()`, `.exitstatus`) — the only seam work is
    `str`↔`bytes` normalization: `ptyprocess.read` returns `bytes`, so decode
    `utf-8`/`errors="replace"`; `.write` takes `bytes`, so encode.
  - `make_pty_backend()` selects on `detect_platform()`: `win32`→`WinptyBackend`,
    else `UnixPtyBackend`. If `not capabilities.has_pty`, return a null backend
    whose `spawn` raises a clear English `RuntimeError` (the manager already
    surfaces that as a typed error, not a crash — AD-6).
  - `PtyManager.spawn` (`pty_manager.py:55`) calls `make_pty_backend().spawn(...)`
    instead of importing `winpty` directly; `PtySession.proc` (`:39`) now holds a
    `PtyHandle`.
- **Acceptance criteria:**
  - `pytest tests/unit/terminal/test_unix_pty.py -v` green (spawns a real
    `bash -c "echo hi"` via `ptyprocess`, asserts the bytes round-trip and
    `exitstatus == 0`).
  - `python -c "from jarvis.terminal.backend import make_pty_backend; print(type(make_pty_backend()).__name__)"` prints `UnixPtyBackend` on Linux/macOS, `WinptyBackend` on Windows.
  - `pytest tests/unit/terminal/ -v` green on all three OS legs (no `skip_ci` marker — terminal is fully CI-provable per AD-9).
  - `ruff check jarvis/terminal/ && mypy jarvis/terminal/` clean.

### 1.2 — Terminal: Unix shell discovery in `shells.py`

- **Modify:** `jarvis/terminal/shells.py` (add a Unix branch to `discover_shells()`
  at `:71-78`; keep the four Windows factories `_powershell_7` `:23`,
  `_windows_powershell` `:35`, `_cmd` `:48`, `_git_bash` `:55` untouched per AD-7).
- **Create:** `tests/unit/terminal/test_unix_shells.py`.
- **Approach:**
  - Add `_unix_shells() -> list[ShellInfo]` returning the discovered POSIX shells
    in preference order: (1) `$SHELL` if set and on disk (label it from the
    basename, e.g. "zsh"); (2) parse `/etc/shells`, keeping each existing path;
    (3) fall back to `shutil.which("bash"/"zsh"/"fish")`. Dedupe by resolved
    path. Each `ShellInfo.argv` is `(path, "-i")` (interactive) — never `-l`
    unconditionally (a login shell on every spawn re-sources profiles and is slow).
  - Gate the dispatch in `discover_shells()` on `detect_platform()`: on `win32`
    iterate the four Windows factories (unchanged); else return `_unix_shells()`.
  - `get_shell(shell_id)` (`shells.py:81`) already iterates `discover_shells()`,
    so it works unchanged once the Unix branch is in place.
- **Acceptance criteria:**
  - `pytest tests/unit/terminal/test_unix_shells.py -v` green (monkeypatches
    `$SHELL`, a fake `/etc/shells`, and `shutil.which`; asserts dedup + order +
    `argv == (path, "-i")`).
  - `python -c "from jarvis.terminal.shells import discover_shells; print([s.id for s in discover_shells()])"` lists at least `bash` on a CI Linux runner.
  - `python -c "import ast; m=ast.parse(open('jarvis/terminal/shells.py').read()); assert all(not (getattr(n,'module',None)=='winreg') for n in ast.walk(m))"` exits 0 (no Windows-only module-scope import introduced).

### 1.3 — App-launch: cross-platform `resolve_app_launch_target` + platform `KNOWN_APPS`

- **Modify:** `jarvis/plugins/tool/app_resolver.py` (add `sys.platform` branches to
  `resolve_app_launch_target` `:88`; `winreg` is already lazy-guarded at `:24-27`,
  keep it), `jarvis/plugins/tool/open_app.py` (make `KNOWN_APPS` `:23` and the
  launch call `:137-156` platform-conditional).
- **Create:** `tests/unit/plugins/tool/test_app_resolver_unix.py`.
- **Approach:**
  - In `app_resolver.py`, keep the URL/path escape hatch (`:96-99`) and the
    Spotify protocol case (`:102`) — they are OS-agnostic. Branch the GUI-app
    resolution on `detect_platform()`:
    - **macOS:** prefer `LaunchTarget("open_a", canonical)` — launched via
      `open -a <AppName>` (the system app-launcher; resolves `.app` bundles by
      display name). Fall back to `shutil.which()` for CLI tools.
    - **Linux:** `shutil.which(canonical)` for direct executables; otherwise
      `LaunchTarget("xdg_open", canonical)` for `.desktop`/MIME handlers. A
      `.desktop` lookup walks `$XDG_DATA_DIRS/applications` + `~/.local/share/applications`.
  - Extend the `LaunchKind` `Literal` (`app_resolver.py:30`) with `"open_a"` and
    `"xdg_open"`. The `_EXE_ALIASES` map (`:54`) becomes platform-conditional
    (e.g. macOS maps `"vscode"→"Visual Studio Code"`, Linux maps `"vscode"→"code"`).
  - In `open_app.py`, select `KNOWN_APPS` by `detect_platform()`: keep the
    existing Windows set as `_KNOWN_APPS_WIN`; add `_KNOWN_APPS_DARWIN`
    (`safari`, `terminal`, `finder`, `calculator`, `firefox`, `chrome`, `vscode`,
    `slack`, …) and `_KNOWN_APPS_LINUX` (`firefox`, `nautilus`, `gnome-terminal`,
    `gnome-calculator`, `code`, `chromium`, …) per AD-15. The
    `_is_plausible_app_name` gate (`open_app.py:68`), the `_APP_NAME_RE` and
    `_HALLUCINATION_RE` (`:52`/`:58`), and the PATH/URL/path escape hatches
    (`:81-89`) are reused verbatim — only the whitelist set swaps.
  - In `OpenAppTool.execute` (`open_app.py:116`), branch the launch on the new
    `LaunchKind`: `open_a` → `subprocess.Popen(["open", "-a", value, *args])`,
    `xdg_open` → `subprocess.Popen(["xdg-open", value])`, `executable` →
    `subprocess.Popen([value, *args])`. `startfile`/`os.startfile` stays the
    Windows-only branch (`:153`). Every `Popen` keeps `shell=False`.
- **Acceptance criteria:**
  - `pytest tests/unit/plugins/tool/test_app_resolver_unix.py -v` green
    (monkeypatches `detect_platform`→`darwin`/`linux`, asserts `safari`→`open_a`,
    a PATH tool→`executable`, an unknown name→`xdg_open`/raw, and that a URL still
    short-circuits to the shell verb).
  - `python -c "from jarvis.plugins.tool.open_app import KNOWN_APPS; assert 'safari' in KNOWN_APPS or 'firefox' in KNOWN_APPS"` exits 0 on a non-Windows box.
  - `pytest tests/unit/plugins/tool/ -k app -v` green on all three OS legs (resolution logic only; an actual launch is a live check, not asserted in CI).
  - `ruff check jarvis/plugins/tool/ && mypy jarvis/plugins/tool/app_resolver.py` clean.

### 1.4 — Hotkey: `HotkeyBackend` protocol + per-OS backends (pynput / global-hotkeys / noop)

- **Create:** `jarvis/trigger/backends/__init__.py` (the `HotkeyBackend` `Protocol`
  + `make_hotkey_backend()` factory), `jarvis/trigger/backends/global_hotkeys.py`
  (wraps the existing Windows `_KEY_MAP`/refcount logic), `jarvis/trigger/backends/pynput.py`,
  `jarvis/trigger/backends/noop.py`, `tests/fakes/fake_hotkey_backend.py`,
  `tests/unit/trigger/test_hotkey_backends.py`.
- **Modify:** `jarvis/trigger/hotkey.py` — extract the `global_hotkeys` calls
  (`import global_hotkeys` `:241`, `gh.remove_hotkeys`/`gh.register_hotkeys`
  `:279`/`:288`, `_start_checker_once`/`_stop_checker_once` `:301`/`:313`,
  `_normalize_combo` `:104` + `_KEY_MAP` `:50`, the refcount guard `:69-101`)
  behind the `GlobalHotkeysBackend`. The `HotkeyTrigger` class, its `__aenter__`
  degrade contract (`:241-296`), `validate_hotkey` (`:121`), and the PTT
  press/release edge logic (`:256-267`) stay intact — only the backend handle
  swaps.
- **Approach:**
  - `HotkeyBackend` `Protocol`: `register(bindings, on_event) -> None`,
    `unregister() -> None`, `start() -> None`, `stop() -> None`, plus a
    `received_any_event() -> bool` introspection hook (feeds AD-8's macOS
    "registered but zero events → guide the user to grant Input-Monitoring/
    Accessibility" detection). `bindings` is the existing
    `[combo_str, on_press, on_release]` shape.
  - `GlobalHotkeysBackend` (Windows): move `_KEY_MAP`, `_normalize_combo`, the
    module-level `_CHECKER_LOCK`/`_CHECKER_REFCOUNT` refcount guard, and the
    idempotent pre-remove → register sequence (`hotkey.py:278-301`) into this
    module **verbatim** — these carry the BUG fixes (remove-by-string,
    pre-remove-on-reentry, single-checker refcount, register failure → degrade).
    Do not refactor the logic; just relocate it (AD-7).
  - `PynputBackend` (macOS/Linux X11): use `pynput.keyboard.GlobalHotKeys` (or a
    `Listener` for the PTT both-edges case). Map jarvis combo syntax
    (`ctrl+right_alt+j`) to pynput's `<ctrl>+<alt>+j`. Maintain a tiny refcount
    so two `HotkeyTrigger`s don't spawn two listeners. On macOS, after `start()`,
    if no event fires within a short window the introspection hook flags it so
    the wizard can surface the Input-Monitoring grant message (AD-8/AD-13).
  - `NoopBackend`: returned when `not capabilities.has_hotkey` — which is
    true on Wayland (`is_wayland()` is folded into `has_hotkey` in Wave 0's
    probe). Logs the English "global hotkey unavailable on Wayland by OS design;
    lean on the wake word" message once, then no-ops (AD-8 / AD-OE6).
  - `make_hotkey_backend()` selects: `win32`→`GlobalHotkeysBackend`; else if
    `capabilities.has_hotkey`→`PynputBackend`; else `NoopBackend`.
  - `HotkeyTrigger.__aenter__` (`hotkey.py:238`) calls `make_hotkey_backend()`
    and keeps its existing try/except degrade-to-`self._gh=None` semantics
    mapped onto `backend = None`. The "voice still works via wake word / mascot
    click" degrade message (`:243-248`) is preserved.
- **Acceptance criteria:**
  - `pytest tests/unit/trigger/test_hotkey_backends.py -v` green (asserts factory
    selection per platform, the Windows refcount 0↔1 boundary, and that the noop
    backend logs-once-then-no-ops without raising).
  - `pytest tests/unit/trigger/test_hotkey.py -v` (existing 48-case suite) stays green — the degrade contract and PTT edges are unchanged.
  - `python -c "from jarvis.trigger.backends import make_hotkey_backend; print(type(make_hotkey_backend()).__name__)"` prints `PynputBackend` or `NoopBackend` on Linux (never raises, never `GlobalHotkeysBackend`).
  - `python -c "import ast; m=ast.parse(open('jarvis/trigger/hotkey.py').read()); assert not any(getattr(n,'names',None) and any(a.name=='global_hotkeys' for a in n.names) for n in ast.walk(m) if isinstance(n,ast.Import))"` exits 0 (the module-scope `global_hotkeys` import moved into the backend; lazy import inside a function is still allowed there).

### 1.5 — Dependency grouping for `[desktop]` (ptyprocess + pynput)

- **Modify:** `pyproject.toml` `[project.optional-dependencies]` `desktop` group
  (`pyproject.toml:99-110`).
- **Approach:** Mirror the existing `sys_platform` marker pattern (`:100-107`):
  - Add `"pynput>=1.7"` (no platform marker — all-platform, AD-14).
  - Add `"ptyprocess>=0.7; sys_platform != 'win32'"` (POSIX-only; Windows uses
    `pywinpty`, already pinned at `:103`).
  - Do **not** add `pyobjc-*` here (that is Wave 2's new `[desktop-macos]` extra)
    and do **not** add `pyatspi` (distro-packaged, not on PyPI — AD-14).
  - After editing entry-points/extras the project must be reinstalled:
    `pip install -e ".[desktop]" --no-deps` (BUG-006/014 contract).
- **Acceptance criteria:**
  - `python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); deps=d['project']['optional-dependencies']['desktop']; assert any(x.startswith('pynput') for x in deps) and any('ptyprocess' in x for x in deps)"` exits 0.
  - `python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); deps=d['project']['optional-dependencies']['desktop']; assert all('pyobjc' not in x and 'pyatspi' not in x for x in deps)"` exits 0.
  - On a Linux CI runner, `pip install -e ".[desktop]" --no-deps` followed by `python -c "import ptyprocess, pynput"` exits 0.

---

## Parallelism

Three independent worktrees, one feature each — no shared files:

- **Worktree A — Terminal:** 1.1 + 1.2 (`jarvis/terminal/`).
- **Worktree B — App-launch:** 1.3 (`jarvis/plugins/tool/{app_resolver,open_app}.py`).
- **Worktree C — Hotkey:** 1.4 (`jarvis/trigger/`).

Sub-task **1.5** (pyproject extras) is a one-line additive edit touched by both
Worktree A (`ptyprocess`) and Worktree C (`pynput`); to avoid a merge conflict on
`pyproject.toml`, land 1.5 as a tiny standalone PR **first** (or assign it to
Worktree A and have C rebase). Each worktree runs `pwsh scripts/preflight.ps1`
before coding (AD-UF23 / BUG-006/014) and confirms `python -c "import jarvis;
print(jarvis.__file__)"` points at the worktree clone.

## EK acceptance gate

This wave satisfies **EK-4** (Terminal + App-launch resolution + Hotkey logic
CI-verified on the ubuntu+macos runners, terminal via a real PTY) and advances
**EK-2** (three of the six features now have a per-OS implementation behind their
seam, selected by the `jarvis/platform/` factory, degrading to a logged no-op
when the capability is absent) and **EK-3** (each new seam ships a
`tests/fakes/` fake — `fake_pty_backend.py`, `fake_hotkey_backend.py` — and
unit tests, no `unittest.mock`).

## Dependencies on prior waves

**Wave 0 only.** All three seam factories call
`jarvis.platform.capabilities.detect_capabilities()` (`has_pty`, `has_hotkey`,
`is_wayland`) and `jarvis.platform.detect_platform()`, both built in Wave 0
(sub-tasks 0.1/0.2). No code from this wave may merge until Wave 0's CI matrix
(0.3) is green (AD-4). No dependency on Wave 2/3/4.
