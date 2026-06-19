# WELLE-0 — Foundation (BLOCKING)

> Canonical decisions: `_FROZEN-DECISIONS.md` (AD-4, AD-5, AD-11 ·note·, PC-1, PC-6).
> This wave is a **hard prerequisite**. Nothing from Waves 1-4 may merge until the
> CI matrix is green and the Orb-framework conflict is resolved on paper.

---

## Goal

Stand up the two pieces of scaffolding every later wave depends on. First, a
single shared capability module `jarvis/platform/` that answers "what works on
this box" exactly once (cached, frozen `Capabilities` dataclass + cheap probes),
so the six ports read capabilities from one place instead of each re-detecting
`sys.platform`. Second — and the highest-leverage action in the whole plan,
because there is **no functional CI today** (PC-1: `.github/workflows/` holds 5
supply-chain workflows, none run pytest/ruff/mypy) — a GitHub Actions matrix on
`ubuntu-latest` + `macos-latest` + `windows-latest` that runs ruff, mypy,
`pytest -m "not skip_ci"`, an import-cleanliness gate proving the `jarvis`
package imports clean on Linux/macOS (no module-scope `pywin32`/`winreg`/
`global_hotkeys`), and a minimum-passed-count floor so a mass-skip regression
can't sail through as green. Wave 0 also resolves the Orb-framework conflict
(PC-6: the **live** orb is Tk `ui/orb/overlay.py`; PySide6 `OS-Level/src/overlay/`
is the abandoned approach the Tk docstring explicitly rejected) and cleans the
stale `OS-Level/src` / `ui.orb` conftest references so the test collection on a
Linux runner is honest.

---

## Sub-tasks

### 0.1 — Shared platform detector + `Capabilities` dataclass

- **Create:** `jarvis/platform/__init__.py`, `jarvis/platform/capabilities.py`
- **Approach:**
  - `detect_platform() -> Literal["win32", "darwin", "linux"]` in `__init__.py`.
    Map `sys.platform`: `win32`→`win32`, `darwin`→`darwin`, everything starting
    `linux`→`linux`. Anything else → raise nothing; return `"linux"` as the POSIX
    default and log a one-line English warning (AD-6: no `sys.platform` branch
    ever raises).
  - In `capabilities.py` define `@dataclass(frozen=True, slots=True) Capabilities`
    with exactly the fields named in AD-5: `platform: str`, `has_hotkey: bool`,
    `has_ax_tree: bool`, `has_overlay: bool`, `has_pty: bool`,
    `has_elevation: bool`, `display_present: bool`, `is_wayland: bool`,
    `ax_permission_granted: bool` (tri-state via `bool | None` where "unknown
    until first use" matters — document it). Mirror the frozen-dataclass style of
    `jarvis/core/protocols.py:402` (`Observation`).
  - `detect_capabilities() -> Capabilities` computes every field from the probes
    in 0.2 and returns the frozen instance. Wrap in `functools.lru_cache` (or a
    module-level `_CACHED` guard) so the probes run once per process. Add
    `reset_capabilities_cache()` as a test-isolation hook (mirror
    `jarvis/trigger/hotkey.py:97` `_reset_checker_state_for_tests`).
  - Probes must be **import-clean**: never `import winreg`/`pyobjc`/`pyatspi` at
    module scope; each probe does its platform-guarded lazy import inside the
    function body (the pattern at `jarvis/plugins/tool/app_resolver.py:24`).
- **Acceptance criteria:**
  - `python -c "from jarvis.platform import detect_platform; print(detect_platform())"` prints `win32` on the maintainer box.
  - `python -c "from jarvis.platform.capabilities import detect_capabilities as d; print(d() is d())"` prints `True` (cache identity).
  - `pytest tests/unit/platform/test_capabilities.py -v` green.
  - `python -c "import ast,sys; m=ast.parse(open('jarvis/platform/capabilities.py').read()); assert not any(getattr(n,'names',None) and any(a.name in ('winreg','pyatspi') for a in n.names) for n in ast.walk(m) if isinstance(n,ast.Import))"` exits 0 (no module-scope Windows/Linux-only import).

### 0.2 — Capability probes (display / Wayland / AX-permission / PTY / elevation)

- **Create:** `jarvis/platform/probes.py` (imported by `capabilities.py`)
- **Approach:** one small function per field, each returning a plain `bool` and
  swallowing its own exceptions to a logged `False`:
  - `display_present()` — Windows: always `True`. macOS: `True`. Linux: `bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))`.
  - `is_wayland()` — `os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))` (Linux only; `False` elsewhere). Feeds AD-8 (Wayland → hotkey no-op).
  - `has_pty()` — Windows: `importlib.util.find_spec("winpty") is not None`. POSIX: `importlib.util.find_spec("ptyprocess") is not None` (the import that Wave 1.1 adds).
  - `ax_permission_granted()` — Windows: `True` (UIA needs no grant). macOS: lazy `AXIsProcessTrusted()` via `ApplicationServices` (returns `None` if pyobjc absent → "unknown"). Linux: `True` if AT-SPI bus reachable, else `False`.
  - `has_ax_tree()` — Windows: `find_spec("pywinauto")`. macOS: `find_spec("Quartz")`. Linux: `find_spec("pyatspi")` (distro-packaged per AD-14, NOT pip).
  - `has_hotkey()` — Windows: `find_spec("global_hotkeys")`. macOS/Linux: `find_spec("pynput")` AND `not is_wayland()`.
  - `has_overlay()` — `display_present()` AND a tk-import probe (`find_spec("tkinter")`); the Tk color-key path is the cross-platform default per AD-11.
  - `has_elevation()` — Windows: `find_spec("win32pipe")`. macOS: `True`. Linux: `bool(shutil.which("pkexec") or shutil.which("sudo"))`.
- **Acceptance criteria:**
  - `pytest tests/unit/platform/test_probes.py -v` green (probes patchable via monkeypatched env / `find_spec`).
  - `python -c "from jarvis.platform.probes import is_wayland; print(is_wayland())"` exits 0 on every OS.
  - `tests/fakes/fake_capabilities.py` exists and constructs a `Capabilities` for each of the three platforms (EK-3: fakes, no `unittest.mock`).

### 0.3 — GitHub Actions test matrix (the BLOCKING gate)

- **Create:** `.github/workflows/ci.yml`
- **Approach:**
  - Copy the 3-OS matrix shape verbatim from `.github/workflows/cross-runner-hash.yml:76-86`
    (`fail-fast: false`; `os: [ubuntu-latest, macos-latest, windows-latest]`;
    `runs-on: ${{ matrix.os }}`). Pin `actions/checkout` and `actions/setup-python`
    by 40-char commit SHA (same hardening rationale as `cross-runner-hash.yml:91-93`).
  - Trigger on `push` to `main` + the active feature branch and on `pull_request`.
  - Steps per matrix leg: `python -m pip install -e ".[dev]"` → `ruff check jarvis/`
    → `ruff format --check jarvis/` → `mypy jarvis/` → import-cleanliness gate
    (0.4) → pytest with the min-passed floor (0.5).
  - macOS/Linux legs must NOT install the `[desktop-macos]` extra or distro
    `pyatspi` — the matrix proves the **base** `.[dev]` install imports and tests
    clean, exactly the €5-VPS contract.
- **Acceptance criteria:**
  - `gh workflow view ci.yml` resolves (file is syntactically valid).
  - The `ci` workflow appears green for all three OS legs on the branch PR (`gh pr checks --watch`).
  - `python -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/ci.yml')); assert d['jobs']['test']['strategy']['matrix']['os']==['ubuntu-latest','macos-latest','windows-latest']"` exits 0.

### 0.4 — Import-cleanliness gate

- **Create:** `scripts/ci/check_import_clean.py` (referenced by `ci.yml`)
- **Approach:**
  - On Linux/macOS legs, run `python -c "import jarvis"` and assert exit 0 — a
    module-scope `import win32pipe`/`winreg`/`global_hotkeys` would crash here.
    PC-2 says all Windows deps are already lazy-guarded
    (`app_resolver.py:24`, `hotkey.py:241`, `pty_manager.py:71`,
    `uia_tree.py:188`); this gate **locks that in** so a future PR can't regress it.
  - Belt-and-braces: AST-walk every `.py` under `jarvis/` and fail on a
    top-level (module-scope, not inside a function/try) `import` of
    `{win32api, win32pipe, win32file, win32security, winreg, global_hotkeys,
    pywinauto, winpty, pywintypes}`. Allow them inside function bodies and
    `try:`/`except ImportError:` blocks.
- **Acceptance criteria:**
  - `python scripts/ci/check_import_clean.py` exits 0 on the current tree.
  - Temporarily adding `import winreg` at the top of `jarvis/__init__.py` makes it exit non-zero (verify, then revert).
  - On a Linux runner, the `import jarvis` step is green.

### 0.5 — Minimum-passed-count floor

- **Create:** `scripts/ci/assert_min_passed.py` + a pytest invocation in `ci.yml` writing a JUnit XML
- **Approach:**
  - Run `pytest -m "not skip_ci" --junitxml=report.xml -q`, then parse the XML
    and assert `passed >= FLOOR`. Seed `FLOOR` from the current green count on the
    branch (read it once, hardcode a conservative value, e.g. `FLOOR = 1200`, and
    note in a comment to bump it forward — never down). A mass-skip regression
    (e.g. a broken conftest that errors-out collection) drops `passed` below the
    floor and fails the build even though pytest's own exit code might be 0.
  - The floor is per-OS-independent: assert against the **Linux** leg's count
    (the most ports-relevant, since Windows-only tests carry `skip_ci`/markers).
- **Acceptance criteria:**
  - `pytest -m "not skip_ci" --junitxml=report.xml -q; python scripts/ci/assert_min_passed.py report.xml` exits 0 locally.
  - Setting `FLOOR` absurdly high (e.g. 999999) makes `assert_min_passed.py` exit non-zero (verify, then revert).

### 0.6 — Resolve the Orb-framework conflict (PC-6, on paper)

- **Create:** `docs/plans/cross-platform-mac-linux/ADR-orb-framework.md` (a short decision note; the full `OverlaySurface` build is Wave 2)
- **Modify:** none of the orb code yet — this sub-task only **records the verdict**.
- **Approach:** Document the ground truth found in Wave 0:
  - The **live** orb is Tk: `ui/orb/overlay.py` (top-level `ui.orb` package) using
    `wm_attributes("-transparentcolor", COLOR_KEY_HEX)` at lines `964-966` and
    `1329-1331`, class `OrbOverlay` at `1236`. Its docstring (lines 3-13)
    explicitly records that **PySide6 + WA_TranslucentBackground was tried and
    rejected** ("opaque black backing buffer + drop-shadow frame").
  - `OS-Level/src/overlay/` (PySide6, 19 modules) is the **abandoned** approach.
    The Tk color-key path (`-transparentcolor` → Win32 `LWA_COLORKEY`) is what
    AD-11 standardizes on because it works on Windows **and** macOS.
  - **Verdict (feeds AD-11):** the `OverlaySurface` abstraction in Wave 2 wraps
    the Tk orb (`TkColorKeyOverlay`), not the PySide6 tree. Do **not** delete
    `OS-Level/src/overlay/` in this plan (out of scope; grandfathered), but it is
    NOT the live overlay and must not be re-imported as one.
- **Acceptance criteria:**
  - `test -f docs/plans/cross-platform-mac-linux/ADR-orb-framework.md` exits 0.
  - The note names both paths with `file:line` and states the Tk verdict explicitly.

### 0.7 — Clean the stale `ui.orb` / `OS-Level/src` conftest references

- **Modify:** `tests/conftest.py` (line 7 comment), `tests/overlay/conftest.py` (lines 1-21)
- **Approach:**
  - Root `tests/conftest.py:7` keeps the `sys.path.insert(repo_root)` (the live
    Tk orb genuinely lives at top-level `ui/orb/`, so `import ui.orb` must keep
    working) — but update the stale comment so it no longer implies the orb moved.
  - `tests/overlay/conftest.py:15-18` inserts `OS-Level/src` so `import overlay`
    resolves the PySide6 tree. Per the 0.6 verdict that tree is abandoned. EITHER
    (a) gate the PySide6 overlay tests behind `pytest.importorskip("PySide6")`
    (already partly done at `:27`) AND mark them `skip_ci` so the Wave-0 matrix
    floor isn't polluted by an abandoned framework, OR (b) if those tests have no
    value, move them under a clearly-labelled `tests/overlay/legacy_pyside/`.
    Pick (a) unless a test there guards live behavior.
  - Keep `QT_QPA_PLATFORM=offscreen` (`tests/overlay/conftest.py:21`) — PC-5
    confirms it already makes overlay tests headless-safe.
- **Acceptance criteria:**
  - `pytest tests/overlay/ -v` collects without `ModuleNotFoundError` on a box without PySide6 (skips cleanly).
  - `python -c "import ui.orb.overlay"` still resolves on Windows (live orb import unbroken).
  - `grep -rn "OS-Level" tests/` shows only the intentionally-scoped overlay conftest reference.

---

## Parallelism

- **0.1 + 0.2** are one worktree (platform module — tightly coupled).
- **0.3 + 0.4 + 0.5** are one worktree (CI workflow — `ci.yml` and the two gate
  scripts ship together; 0.3 references 0.4/0.5).
- **0.6 + 0.7** are one worktree (orb-conflict resolution — doc + conftest cleanup).
- The three worktrees are independent and can run in parallel. **But the wave is
  not "done" until 0.3's matrix is green**, which requires 0.1/0.2 (so `import
  jarvis` and the new tests exist) and 0.7 (so collection is clean). Merge order:
  platform module → conftest cleanup → CI workflow last (it gates on the others).

## EK acceptance gate

This wave satisfies **EK-1** (CI matrix green on all three OSes for
ruff + mypy + `pytest -m "not skip_ci"`, with the import-cleanliness gate and
min-passed floor enforced) and lays the `jarvis/platform/` factory that **EK-2**
depends on. It also discharges PC-1 (no functional CI) and PC-6 (orb-framework
conflict). It produces the first `tests/fakes/` capability fake toward **EK-3**.

## Dependencies on prior waves

None — Wave 0 is the root. Every later wave depends on **this** wave: Waves 1-4
all read `jarvis/platform/capabilities.detect_capabilities()` for their seam
factories, and none of them may merge until 0.3's matrix is green (AD-4).
