# Cross-Platform Port — PROMPTS.md (coding-agent prompt library)

> Single source of truth for spawning coding agents on the macOS/Linux cross-platform
> migration. Each fenced ` ```prompt ` block is keyed (heading) and copy-paste-ready into a
> fresh Claude-Code session. The HTML cockpit (`PHASE-TRACKER.html`) parses these keyed
> blocks into a JSON library, so **keep the keys exact and the fences clean**.
>
> Key convention:
> - `preflight` — one-time migration kickoff.
> - `<wave>.<task>` — sub-task prompts (e.g. `1.1`, `2.4`, `3.6`).
> - `merge-w<n>` — mechanical merge of a wave's sub-task branches onto the integration branch.
> - `check-w<n>` — read-only phase-check verifying that wave's EK criteria before merge.
> - `recovery-<type>` — step-prescriptive recovery prompts.
>
> Canonical sources every prompt points at:
> - [`_FROZEN-DECISIONS.md`](_FROZEN-DECISIONS.md) — AD-1..AD-15, PC-1..PC-7, EK-1..EK-6, wave structure.
> - [`HARD-NEGATIVES.md`](HARD-NEGATIVES.md) — HN-1..HN-18 (inviolable; agents inline-quote the relevant ones).
> - [`ANTI-PATTERNS.md`](ANTI-PATTERNS.md) — AP-1..AP-13 (migration traps).
> - The matching `WELLE-<n>-*.md` brief for the wave.
>
> Naming used throughout: the integration branch is `feat/crossplatform-port`; each sub-task
> branch is `crossplat/w<wave>-<short-slug>` (e.g. `crossplat/w1-terminal-pty`). Worktrees are
> created under `../sub-agents-outputs/` (≤200-char path cap, Phase-6 isolation invariant).

---

## preflight

```prompt
ultrathink
<role>
You are the migration lead bootstrapping the cross-platform (macOS + Linux) port of Personal Jarvis. Your outcome is a clean, verified starting state and the integration branch every later wave merges into — not any feature code. You set the stage; you do not act.
</role>

<outcome>
DONE means all of the following are true and demonstrated with command output:
- The repo is on a fresh integration branch `feat/crossplatform-port` cut from the latest `origin/main`, pushed to origin.
- `pwsh scripts/preflight.ps1` exits 0 in the repo root.
- `python -c "import jarvis; print(jarvis.__file__)"` resolves to the live working clone (BUG-006/014 guard).
- The whole cross-platform plan has been read and an at-a-glance wave/dependency map is reproduced in your final report.
- No production file has been modified.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` (the full AD/PC/EK/wave list — read all of it).
- `docs/plans/cross-platform-mac-linux/README.md` (the master plan + the hot-file ownership table).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-15 and HN-17 in your final report:
  - HN-15: "Never merge a wave before its CI matrix (ubuntu-latest + macos-latest + windows-latest) is green for ruff + mypy + pytest -m 'not skip_ci'."
  - HN-17: "Every artifact is English; never git push --force; never --no-verify; never bypass signing."
- `CLAUDE.md` (Output-Language Policy + Worktree activation checklist + git policy).
</required-reading>

<environment-setup>
Run exactly, in the repo root `C:\Users\Administrator\Desktop\Personal Jarvis`:
1. `git fetch origin`
2. `git switch -c feat/crossplatform-port origin/main`
3. `git push -u origin feat/crossplatform-port`
4. `pwsh scripts/preflight.ps1`   (must exit 0 — if not, STOP and fix before continuing)
5. `python -c "import jarvis; print(jarvis.__file__)"`
Do NOT create a worktree here — this prompt only stands up the integration branch. Sub-task prompts create their own worktrees.
</environment-setup>

<scope>
Create / modify: NOTHING in `jarvis/`. This is a branch + verification step only. (Optional: you may append a one-line note to your own scratch report — never to a plan doc.)
</scope>

<primary-path>
1. Read all required files.
2. Run the environment-setup commands in order, stopping on any non-zero exit.
3. Confirm the integration branch exists on origin (`git ls-remote --heads origin feat/crossplatform-port`).
4. Reproduce the wave dependency map (Wave 0 blocks 1/2/3; Wave 4 needs all of 0-3) in the report.
</primary-path>

<fallback-paths>
- If `origin/main` is unexpectedly behind a known-good commit, branch from the commit the operator names instead, and record the deviation. Failure condition: `git switch` reports the base ref is missing.
- If `preflight.ps1` fails on a stale editable-install pin, run `pip install -e . --no-deps` then re-run preflight. Failure condition: preflight still non-zero.
- You may invent a recovery path if both fail, provided you break no INVIOLABLE hard-rule (no force-push, no history rewrite on a shared branch — HN-17).
</fallback-paths>

<acceptance>
git ls-remote --heads origin feat/crossplatform-port
pwsh scripts/preflight.ps1
python -c "import jarvis; print(jarvis.__file__)"
git status --porcelain
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-17 — English-only artifacts; never `git push --force`; never `--no-verify`; never bypass signing.
SOFT:
- Prefer branching from `origin/main`; deviate only with an explicit recorded reason.
</hard-rules>

<done-signal>
Final report (5 bullets):
- Integration branch created + pushed (with the origin hash).
- Preflight + import-clean checks: PASS/FAIL with output.
- Wave dependency map reproduced.
- Hard-rules honored (HN-15/HN-17 quoted).
- Path-taken: primary or which fallback, and why.
</done-signal>
```

---

# Wave 0 — Foundation (BLOCKING)

> Worktree split (per `WELLE-0-foundation.md` §Parallelism): **0.1+0.2** one worktree (platform module), **0.3+0.4+0.5** one worktree (CI workflow), **0.6+0.7** one worktree (orb-conflict). Merge order: platform module → conftest cleanup → CI workflow last. Nothing in Waves 1-4 merges until 0.3's matrix is green (AD-4 / HN-15).

## 0.1

```prompt
ultrathink
<role>
You own the shared platform-detection authority for the whole cross-platform port. Your outcome is a single cached, frozen capability source of truth that all six later ports read instead of each re-detecting sys.platform. You are not building any feature — you are building the one module everything else depends on.
</role>

<outcome>
DONE means:
- `jarvis/platform/__init__.py` exposes `detect_platform() -> Literal["win32","darwin","linux"]` that maps sys.platform and NEVER raises (unknown → "linux" + one English warning).
- `jarvis/platform/capabilities.py` defines `@dataclass(frozen=True, slots=True) Capabilities` with exactly the AD-5 fields: `platform: str`, `has_hotkey: bool`, `has_ax_tree: bool`, `has_overlay: bool`, `has_pty: bool`, `has_elevation: bool`, `display_present: bool`, `is_wayland: bool`, `ax_permission_granted: bool | None` (tri-state; document the None="unknown until first use" case).
- `detect_capabilities() -> Capabilities` is cached (lru_cache or module `_CACHED`) so `d() is d()`; `reset_capabilities_cache()` exists as a test hook.
- No module-scope import of `winreg`/`pyobjc`/`pyatspi`/any Windows-only package — all lazy + guarded inside function bodies.
- All acceptance commands pass.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-0-foundation.md` §0.1 (this exact sub-task).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-5 (single capability module) + AD-6 (graceful null-fallback, no branch raises).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote in your report:
  - HN-3: "Never branch on sys.platform inline in a consumer. Read the capability from the shared jarvis/platform/ module."
  - HN-4: "No sys.platform factory branch may EVER raise. An unavailable capability logs one clear English message and returns a null/no-op implementation."
  - HN-7: "Never add a module-scope import of a Windows-only package. Lazy + guarded inside the function/branch only."
- Reference patterns: frozen dataclass style at `jarvis/core/protocols.py:402` (`Observation`); test-reset hook at `jarvis/trigger/hotkey.py:97` (`_reset_checker_state_for_tests`); lazy-guarded import at `jarvis/plugins/tool/app_resolver.py:24`.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`  (must exit 0)
2. Create the worktree for this sub-task:
   `git worktree add -b crossplat/w0-platform-module ../sub-agents-outputs/crossplat-w0-platform feat/crossplatform-port`
3. From the worktree root: `pip install -e ".[dev]"`
4. Confirm the live import points at the worktree: `python -c "import jarvis; print(jarvis.__file__)"`
</environment-setup>

<scope>
Create: `jarvis/platform/__init__.py`, `jarvis/platform/capabilities.py`, `tests/unit/platform/__init__.py`, `tests/unit/platform/test_capabilities.py`.
(Probes live in 0.2's `jarvis/platform/probes.py`; you import them from `capabilities.py`. If 0.2 has not landed in this worktree yet, define thin local stub probe functions in `probes.py` and let 0.2's author replace them — coordinate so you do not both author the same probe bodies.)
Do NOT modify any existing `jarvis/` file.
</scope>

<primary-path>
1. Write `detect_platform()` in `__init__.py`: `win32`→`win32`, `darwin`→`darwin`, `startswith("linux")`→`linux`, else log + return `linux`.
2. Write `Capabilities` frozen dataclass mirroring `Observation` style; document the tri-state `ax_permission_granted`.
3. Write `detect_capabilities()` computing each field from `jarvis.platform.probes`, wrapped in `functools.lru_cache(maxsize=1)`; add `reset_capabilities_cache()` clearing the cache.
4. Write `tests/unit/platform/test_capabilities.py`: cache identity, field presence, monkeypatched-probe → Capabilities mapping. Use a `tests/fakes/`-style fake or direct monkeypatch of probe functions — NOT `unittest.mock`.
</primary-path>

<fallback-paths>
- If `lru_cache` interferes with the reset hook in tests, use a module-level `_CACHED: Capabilities | None` guard + a `reset_capabilities_cache()` that sets it to None. Failure condition: `d() is d()` returns False.
- If `slots=True` conflicts with a default-factory field, drop `slots=True` (keep `frozen=True`) and note the deviation. Failure condition: dataclass construction raises.
- You may invent another caching strategy if both fail, provided HN-3/HN-4/HN-7 hold.
</fallback-paths>

<acceptance>
python -c "from jarvis.platform import detect_platform; print(detect_platform())"
python -c "from jarvis.platform.capabilities import detect_capabilities as d; print(d() is d())"
pytest tests/unit/platform/test_capabilities.py -v
python -c "import ast; m=ast.parse(open('jarvis/platform/capabilities.py').read()); assert not any(getattr(n,'names',None) and any(a.name in ('winreg','pyatspi') for a in n.names) for n in ast.walk(m) if isinstance(n,ast.Import))"
ruff check jarvis/platform/ && mypy jarvis/platform/
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-3 — one capability source of truth; consumers never re-detect sys.platform.
- HN-4 — no factory/detector branch raises; degrade + log English.
- HN-7 — no module-scope Windows/OS-specific import.
SOFT:
- Mirror the `Observation` frozen-dataclass style; mirror the existing test-reset-hook naming.
</hard-rules>

<done-signal>
Final report (5 bullets):
- Files created + the `Capabilities` field list confirmed against AD-5.
- Cache identity + reset hook proven (command output).
- Import-cleanliness AST check: PASS.
- Hard-rules honored (HN-3/HN-4/HN-7 quoted).
- Path-taken + any coordination needed with 0.2's probes.
</done-signal>
```

## 0.2

```prompt
ultrathink
<role>
You own the capability probes that answer "what works on this box" — display, Wayland, AX-permission, PTY, elevation, hotkey, overlay, ax-tree. Your outcome is a set of cheap, exception-swallowing boolean probes that feed the frozen Capabilities dataclass from 0.1. Each probe runs once per process and never crashes the import.
</role>

<outcome>
DONE means:
- `jarvis/platform/probes.py` defines one small function per Capabilities field, each returning a plain bool (or `bool | None` for `ax_permission_granted`) and swallowing its own exceptions to a logged False/None.
- Every OS-specific import (`winreg`, `Quartz`, `pyatspi`, `winpty`, `pynput`, `tkinter`, …) is lazy inside the function body via `importlib.util.find_spec` or a guarded import — never at module scope.
- `tests/fakes/fake_capabilities.py` exists and constructs a valid `Capabilities` for each of win32/darwin/linux (EK-3 fake, no `unittest.mock`).
- All acceptance commands pass.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-0-foundation.md` §0.2 (the exact per-field probe spec — follow it verbatim).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-5, AD-6, AD-8 (Wayland feeds hotkey), AD-14 (pyatspi is distro-only).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-7 (no module-scope Windows import) and HN-9 ("Never put pyatspi in a pip extra. It is GObject-Introspection, distro-packaged only").
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-4 (Wayland hotkey) + AP-13 (convenience module-scope import).
- Pattern: lazy-guarded import at `jarvis/plugins/tool/app_resolver.py:24`.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. Use the SAME worktree as 0.1 (platform module is one worktree per WELLE-0 §Parallelism): `git worktree add -b crossplat/w0-platform-module ../sub-agents-outputs/crossplat-w0-platform feat/crossplatform-port` (skip if already created for 0.1).
3. `pip install -e ".[dev]"`
4. `python -c "import jarvis; print(jarvis.__file__)"`
</environment-setup>

<scope>
Create: `jarvis/platform/probes.py`, `tests/fakes/fake_capabilities.py`, `tests/unit/platform/test_probes.py`.
Probe field map (WELLE-0 §0.2): `display_present`, `is_wayland`, `has_pty` (winpty/ptyprocess find_spec), `ax_permission_granted` (macOS lazy `AXIsProcessTrusted()` → None if pyobjc absent), `has_ax_tree` (win:pywinauto / mac:Quartz / linux:pyatspi find_spec), `has_hotkey` (win:global_hotkeys / unix:pynput AND not is_wayland), `has_overlay` (display_present AND tkinter find_spec), `has_elevation` (win:win32pipe / mac:True / linux:pkexec|sudo via shutil.which).
</scope>

<primary-path>
1. Write each probe exactly as WELLE-0 §0.2 prescribes; wrap each body in try/except → logged False/None.
2. Write `fake_capabilities.py` with three factory helpers (`win_capabilities()`, `darwin_capabilities()`, `linux_capabilities()`) returning canned `Capabilities`.
3. Write `test_probes.py` monkeypatching `os.environ` (DISPLAY/WAYLAND_DISPLAY/XDG_SESSION_TYPE) and `importlib.util.find_spec`/`shutil.which` to assert each probe's True/False boundary.
</primary-path>

<fallback-paths>
- If `find_spec` raises for a namespace package, catch it and treat as "absent". Failure condition: probe import throws on a clean Linux box.
- If `AXIsProcessTrusted()` cannot be imported lazily on a non-Mac, return None ("unknown") — that is the designed tri-state. Failure condition: probe raises off-Mac.
- You may invent an additional environment signal for `display_present`/`is_wayland` if the prescribed ones are insufficient, provided HN-7/HN-9 hold and the result is still a swallowed bool.
</fallback-paths>

<acceptance>
pytest tests/unit/platform/test_probes.py -v
python -c "from jarvis.platform.probes import is_wayland; print(is_wayland())"
python -c "from tests.fakes.fake_capabilities import linux_capabilities, darwin_capabilities, win_capabilities; [c() for c in (linux_capabilities,darwin_capabilities,win_capabilities)]; print('ok')"
python -c "import ast; m=ast.parse(open('jarvis/platform/probes.py').read()); assert not any(getattr(n,'names',None) and any(a.name in ('winreg','pyatspi','Quartz','winpty','global_hotkeys','pywinauto','win32pipe','pynput') for a in n.names) for n in ast.walk(m) if isinstance(n,ast.Import))"
ruff check jarvis/platform/ && mypy jarvis/platform/
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-7 / AP-13 — no module-scope OS-specific import; lazy + guarded only.
- HN-9 — `pyatspi` is detected via find_spec, NEVER pip-installed; absence is a clean False.
- HN-4 — a probe never raises; it logs and returns False/None.
SOFT:
- Keep each probe a few lines; one concern per function.
</hard-rules>

<done-signal>
Final report (5 bullets):
- Probes implemented + field-by-field mapping to Capabilities.
- `fake_capabilities.py` constructs all three platforms (output).
- Import-cleanliness AST check across the full Windows-only set: PASS.
- Hard-rules honored (HN-7/HN-9/HN-4 quoted).
- Path-taken + which env signals were used for display/Wayland.
</done-signal>
```

## 0.3

```prompt
ultrathink
<role>
You build the highest-leverage missing safety net in the entire project: the GitHub Actions test matrix on ubuntu+macos+windows. There is NO functional CI today (PC-1). Your outcome is a green 3-OS matrix that runs ruff, mypy, pytest, the import-cleanliness gate (0.4), and the min-passed floor (0.5). Until this is green, nothing in Waves 1-4 may merge.
</role>

<outcome>
DONE means:
- `.github/workflows/ci.yml` exists, is syntactically valid, and defines a matrix `os: [ubuntu-latest, macos-latest, windows-latest]` with `fail-fast: false`.
- `actions/checkout` and `actions/setup-python` are pinned by 40-char commit SHA.
- Each leg runs: `pip install -e ".[dev]"` → `ruff check jarvis/` → `ruff format --check jarvis/` → `mypy jarvis/` → the import-cleanliness gate (0.4) → pytest with the min-passed floor (0.5).
- macOS/Linux legs install ONLY `.[dev]` (no `[desktop-macos]`, no distro `pyatspi`) — proving the base €5-VPS install imports + tests clean.
- The `ci` workflow shows green for all three OS legs on the PR.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-0-foundation.md` §0.3 (+ §0.4, §0.5 which this references).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-4 (CI matrix is blocking Wave 0).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-15 ("Never merge a wave before its CI matrix is green…") and HN-16 ("Never let a mass-skip pass as green. The minimum-passed-count floor must hold").
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-6 (green-by-mass-skip).
- The 3-OS matrix shape to copy verbatim: `.github/workflows/cross-runner-hash.yml:76-86`; SHA-pinning rationale at `:91-93`.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. `git worktree add -b crossplat/w0-ci-matrix ../sub-agents-outputs/crossplat-w0-ci feat/crossplatform-port`
3. `pip install -e ".[dev]"`
4. NOTE: this worktree depends on 0.1/0.2 (so `import jarvis` + new platform tests exist) and 0.7 (so collection is clean). If those branches are not yet merged into `feat/crossplatform-port`, the matrix will not go green — coordinate merge order (platform module → conftest cleanup → CI last).
</environment-setup>

<scope>
Create: `.github/workflows/ci.yml`.
This sub-task references `scripts/ci/check_import_clean.py` (0.4) and `scripts/ci/assert_min_passed.py` (0.5) — they ship in the SAME worktree (WELLE-0 §Parallelism groups 0.3+0.4+0.5). Author them together or coordinate so `ci.yml` references real files.
Do NOT touch `jarvis/` production code.
</scope>

<primary-path>
1. Copy the matrix block from `cross-runner-hash.yml:76-86`; adapt the job to `test`.
2. SHA-pin `actions/checkout` + `actions/setup-python` (look up current pins; mirror `:91-93`).
3. Add the step sequence; trigger on `push` to `main` + the active feature branch + `pull_request`.
4. Validate locally with the acceptance YAML assertion, then push and watch `gh pr checks --watch`.
</primary-path>

<fallback-paths>
- If `ruff format --check` flags pre-existing formatting outside the touched ports, scope the format check to changed paths OR accept the existing format and note it — do NOT mass-reformat `jarvis/` (out of scope, churns other worktrees). Failure condition: format check fails on untouched files.
- If `mypy jarvis/` surfaces a large pre-existing error backlog, scope mypy to the new `jarvis/platform/` package for Wave 0 and record a follow-up to widen it — do not suppress errors with blanket ignores. Failure condition: mypy red on legacy modules unrelated to the port.
- You may invent the step ordering if a runner-specific quirk blocks the prescribed one, provided the matrix stays 3-OS and the floor/gate both run (HN-15/HN-16).
</fallback-paths>

<acceptance>
gh workflow view ci.yml
python -c "import yaml; d=yaml.safe_load(open('.github/workflows/ci.yml')); assert d['jobs']['test']['strategy']['matrix']['os']==['ubuntu-latest','macos-latest','windows-latest']"
gh pr checks --watch
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-15 — the matrix is the merge gate; it must be genuinely green on all three legs.
- HN-16 / AP-6 — the min-passed floor must run; a mass-skip is a regression, not a pass.
- HN-17 — no `--no-verify`, no force-push.
SOFT:
- Prefer scoping mypy/ruff-format narrowly over reformatting the whole tree this wave.
</hard-rules>

<done-signal>
Final report (5 bullets):
- `ci.yml` matrix shape + SHA-pins confirmed (YAML assertion output).
- The three OS legs' status (green/red, with the run URL).
- mypy/ruff-format scoping decision, if any deviation taken.
- Hard-rules honored (HN-15/HN-16 quoted).
- Path-taken + dependency note (needs 0.1/0.2/0.7 merged to go fully green).
</done-signal>
```

## 0.4

```prompt
ultrathink
<role>
You build the import-cleanliness gate that permanently locks in the cross-platform import discipline. Your outcome is a script that proves `import jarvis` is clean on Linux/macOS and that no module-scope Windows-only import can ever regress in.
</role>

<outcome>
DONE means:
- `scripts/ci/check_import_clean.py` exists and is referenced by `ci.yml`.
- It runs `python -c "import jarvis"` (asserting exit 0) AND AST-walks every `.py` under `jarvis/`, failing on a TOP-LEVEL (module-scope, not inside a function/try) import of any of `{win32api, win32pipe, win32file, win32security, winreg, global_hotkeys, pywinauto, winpty, pywintypes}`. Imports inside function bodies and `try/except ImportError` blocks are allowed.
- Verified: the script exits 0 on the current tree; temporarily adding `import winreg` to the top of `jarvis/__init__.py` makes it exit non-zero (then reverted).
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-0-foundation.md` §0.4.
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-4 + EK-6.
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-7 (no module-scope Windows import) and HN-10 ("Never add a new Windows-only dependency without the ; sys_platform == 'win32' marker").
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-13 (convenience module-scope import).
- PC-2 lazy-guard precedents: `app_resolver.py:24`, `hotkey.py:241`, `pty_manager.py:71`, `uia_tree.py:188`.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. Same worktree as 0.3: `git worktree add -b crossplat/w0-ci-matrix ../sub-agents-outputs/crossplat-w0-ci feat/crossplatform-port` (skip if already created).
3. `pip install -e ".[dev]"`
</environment-setup>

<scope>
Create: `scripts/ci/check_import_clean.py`, `scripts/ci/__init__.py` (if needed for collection), `tests/unit/ci/test_check_import_clean.py` (optional but recommended — a fake module tree asserting the AST walker flags a top-level offender and ignores a function-body one).
Call `sys.stdout.reconfigure(encoding='utf-8')` at the top (Windows cp1252 default — CLAUDE.md Windows specifics).
</scope>

<primary-path>
1. Implement the `import jarvis` subprocess check (assert returncode 0, print stderr on failure).
2. Implement the AST walker: for each `.py`, parse, and for each `ast.Import`/`ast.ImportFrom` whose `col_offset == 0` AND not nested under a `FunctionDef`/`Try`, check the forbidden name set.
3. Run it on the current tree; then do the add-`import winreg`-and-revert verification.
</primary-path>

<fallback-paths>
- If walking col_offset is ambiguous for conditionally-indented module code, instead track parent nodes during the walk (only flag imports whose nearest enclosing scope is Module). Failure condition: a legitimate function-body lazy import is flagged.
- You may invent the nesting-detection approach if both fail, provided allowed lazy imports stay allowed (HN-7).
</fallback-paths>

<acceptance>
python scripts/ci/check_import_clean.py
pytest tests/unit/ci/test_check_import_clean.py -v
ruff check scripts/ci/
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-7 / AP-13 — the gate must distinguish module-scope (forbidden) from lazy function-body (allowed) imports.
SOFT:
- Keep the forbidden set in one named constant for easy extension.
</hard-rules>

<done-signal>
Final report (5 bullets):
- Gate exits 0 on the current tree (output).
- The add-winreg-then-revert verification result.
- Forbidden-set + nesting-detection approach.
- Hard-rules honored (HN-7 quoted).
- Path-taken.
</done-signal>
```

## 0.5

```prompt
ultrathink
<role>
You build the minimum-passed-count floor that stops a mass-skip regression from sailing through CI as green. Your outcome is a script that parses the pytest JUnit XML and fails the build if fewer than FLOOR tests passed — even when pytest itself reports zero failures.
</role>

<outcome>
DONE means:
- `scripts/ci/assert_min_passed.py` exists, parses a JUnit `report.xml`, and asserts `passed >= FLOOR`.
- `ci.yml` invokes `pytest -m "not skip_ci" --junitxml=report.xml -q` then this script (against the Linux leg's count).
- `FLOOR` is seeded from the current green count (read it once, hardcode a conservative value e.g. `FLOOR = 1200`, with a comment: bump forward only, never down).
- Verified: passes locally on the current tree; setting FLOOR to 999999 makes it exit non-zero (then reverted).
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-0-foundation.md` §0.5.
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-4.
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-16 ("Never let a mass-skip pass as green. The minimum-passed-count floor must hold; a collapse in collected/passed tests is a regression, not a pass").
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-6 (green-by-mass-skip).
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. Same worktree as 0.3/0.4: `../sub-agents-outputs/crossplat-w0-ci` on branch `crossplat/w0-ci-matrix`.
3. `pip install -e ".[dev]"`
4. Establish the seed: `pytest -m "not skip_ci" --junitxml=report.xml -q` and read the passed count to choose a conservative FLOOR.
</environment-setup>

<scope>
Create: `scripts/ci/assert_min_passed.py`, `tests/unit/ci/test_assert_min_passed.py` (feed it a tiny synthetic JUnit XML asserting above/below-floor behavior).
Wire the pytest+script invocation into `ci.yml` (coordinate with 0.3).
`sys.stdout.reconfigure(encoding='utf-8')` at the top.
</scope>

<primary-path>
1. Parse `report.xml` with `xml.etree.ElementTree`; compute passed = total - failures - errors - skipped (or read the `<testsuite>` attributes).
2. Compare against the `FLOOR` constant; print a clear English message and `sys.exit(1)` on shortfall.
3. Seed FLOOR conservatively from the measured count; comment the bump-forward-only rule.
4. Run the FLOOR=999999 negative test, then revert.
</primary-path>

<fallback-paths>
- If JUnit attribute names differ across pytest versions, sum per-`<testcase>` outcomes instead of reading suite totals. Failure condition: parsed passed count is 0 on a known-green run.
- You may invent the parse strategy if both fail, provided the floor genuinely catches a mass-skip (HN-16).
</fallback-paths>

<acceptance>
pytest -m "not skip_ci" --junitxml=report.xml -q; python scripts/ci/assert_min_passed.py report.xml
pytest tests/unit/ci/test_assert_min_passed.py -v
ruff check scripts/ci/
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-16 / AP-6 — the floor must hold; never lower it to make a red run green.
SOFT:
- Assert against the Linux leg's count (most ports-relevant; Windows-only tests carry markers).
</hard-rules>

<done-signal>
Final report (5 bullets):
- Script passes on current tree; the seeded FLOOR value + measured passed count.
- FLOOR=999999 negative test result.
- Parse strategy used.
- Hard-rules honored (HN-16 quoted).
- Path-taken.
</done-signal>
```

## 0.6

```prompt
ultrathink
<role>
You resolve the Orb-framework conflict (PC-6) on paper, before any Wave-2 orb code is written. Your outcome is a short, evidence-grounded decision note that fixes WHICH orb is live (Tk) and WHICH is abandoned (PySide6), so Wave 2 wraps the right one. You write a verdict, not code.
</role>

<outcome>
DONE means:
- `docs/plans/cross-platform-mac-linux/ADR-orb-framework.md` exists.
- It names BOTH paths with `file:line` evidence: the LIVE Tk orb (`ui/orb/overlay.py`, `wm_attributes("-transparentcolor", COLOR_KEY_HEX)` at ~`:964-966` and `:1329-1331`, class `OrbOverlay` at ~`:1236`, docstring lines 3-13 rejecting PySide6) and the ABANDONED PySide6 tree (`OS-Level/src/overlay/`, 19 modules).
- It states the verdict explicitly: the Wave-2 `OverlaySurface` wraps the Tk orb (`TkColorKeyOverlay`); `OS-Level/src/overlay/` is NOT deleted (out of scope, grandfathered) but must NOT be re-imported as the live overlay.
- No orb code is modified.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-0-foundation.md` §0.6.
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-11 (the orb ladder + the PC-6 NOTE).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-1 ("Never rewrite, refactor, or 'clean up' a Windows implementation. Add a sibling behind the seam") and HN-6 ("Never claim a GUI/permission feature 'works' without a live sign-off").
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-5 (`-transparentcolor` does not exist on X11 Tk).
- Read the actual source: `ui/orb/overlay.py` (docstring + the cited line ranges) to confirm the evidence before writing the verdict.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. `git worktree add -b crossplat/w0-orb-verdict ../sub-agents-outputs/crossplat-w0-orb feat/crossplatform-port`
3. `pip install -e ".[dev]"` (so `python -c "import ui.orb.overlay"` can be confirmed)
</environment-setup>

<scope>
Create: `docs/plans/cross-platform-mac-linux/ADR-orb-framework.md`.
This sub-task pairs with 0.7 (conftest cleanup) in one worktree (WELLE-0 §Parallelism). Do NOT modify orb code.
</scope>

<primary-path>
1. Open `ui/orb/overlay.py`; confirm the docstring's PySide6-rejection and the three `-transparentcolor` sites; record exact current line numbers.
2. Confirm `OS-Level/src/overlay/` is PySide6 and not imported by the live desktop app path.
3. Write the ADR with both paths, the AD-11 rationale (color-key works on Win + macOS), and the explicit "wrap Tk, do not delete or re-import PySide6" verdict.
</primary-path>

<fallback-paths>
- If the cited line numbers have drifted, record the ACTUAL current lines (the WELLE numbers are approximate) and note the drift. Failure condition: the `-transparentcolor` calls cannot be found at all (then escalate — the orb may have changed framework).
- You may structure the ADR however reads clearly, provided it names both paths with evidence and states the Tk verdict (AD-11).
</fallback-paths>

<acceptance>
test -f docs/plans/cross-platform-mac-linux/ADR-orb-framework.md && echo OK
python -c "import ui.orb.overlay; print('live orb import ok')"
grep -n "transparentcolor" "ui/orb/overlay.py"
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-1 — do not rewrite the Tk orb; record the verdict only.
- HN-6 — no "works" claim without a live sign-off (Wave 4 owns that).
SOFT:
- Use exact file:line evidence over prose assertions.
</hard-rules>

<done-signal>
Final report (5 bullets):
- ADR created; both paths named with confirmed file:line.
- The Tk-vs-PySide6 verdict stated.
- Any line-number drift recorded.
- Hard-rules honored (HN-1/HN-6 quoted).
- Path-taken.
</done-signal>
```

## 0.7

```prompt
ultrathink
<role>
You clean the stale `ui.orb` / `OS-Level/src` conftest references so test collection on a Linux runner is honest and the Wave-0 min-passed floor is not polluted by the abandoned PySide6 framework. Your outcome is clean, honest collection — the live Tk orb import keeps working; the abandoned PySide6 tests skip cleanly without PySide6 installed.
</role>

<outcome>
DONE means:
- `tests/conftest.py:7` keeps the `sys.path.insert(repo_root)` (live Tk orb lives at top-level `ui/orb/`) but the stale comment no longer implies the orb moved.
- `tests/overlay/conftest.py` (lines ~1-21): the PySide6 overlay tests are gated behind `pytest.importorskip("PySide6")` (option a) AND marked `skip_ci` so the matrix floor isn't polluted — OR, only if those tests guard no live behavior, moved under `tests/overlay/legacy_pyside/` (option b). Pick (a) unless a test there guards live behavior.
- `QT_QPA_PLATFORM=offscreen` (`tests/overlay/conftest.py:21`) is KEPT.
- All acceptance commands pass.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-0-foundation.md` §0.7 (+ §0.6 verdict, which gates this).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-11 + PC-5/PC-6.
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-2 ("Never delete, lower, or 'simplify' a regression guard that pins a Windows fix") and HN-16 (min-passed floor / mass-skip).
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-6 (green-by-mass-skip).
- Read the current `tests/conftest.py` and `tests/overlay/conftest.py` to see the exact present lines before editing.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. Same worktree as 0.6: `../sub-agents-outputs/crossplat-w0-orb` on `crossplat/w0-orb-verdict`.
3. `pip install -e ".[dev]"`
</environment-setup>

<scope>
Modify: `tests/conftest.py` (the stale comment near the `sys.path.insert`), `tests/overlay/conftest.py` (gate + `skip_ci` the PySide6 path; keep `QT_QPA_PLATFORM=offscreen`).
Do NOT touch `ui/orb/` or `OS-Level/src/overlay/` source.
</scope>

<primary-path>
1. Update the `tests/conftest.py` comment to reflect: top-level `ui/orb/` is the live orb; the insert stays.
2. In `tests/overlay/conftest.py`, ensure `pytest.importorskip("PySide6")` guards the PySide6 import, and add the `skip_ci` marker to the abandoned-framework tests.
3. Confirm collection on a no-PySide6 box skips cleanly and the live Tk orb import is unbroken.
</primary-path>

<fallback-paths>
- If a PySide6 test actually exercises live behavior (not just the abandoned tree), do NOT skip_ci it — keep it and note the finding (HN-2). Failure condition: a test you were about to skip pins a Windows orb fix.
- If `importorskip` is already present at `:27`, just add the `skip_ci` marker and fix the comment. Failure condition: collection still raises `ModuleNotFoundError` without PySide6.
- You may choose option (b) (move to `legacy_pyside/`) if the tests guard nothing live; record the rationale.
</fallback-paths>

<acceptance>
pytest tests/overlay/ -v
python -c "import ui.orb.overlay; print('live orb import ok')"
grep -rn "OS-Level" tests/
ruff check tests/overlay/ 2>/dev/null || true
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-2 — do not delete/weaken a regression guard pinning a Windows fix.
- HN-16 / AP-6 — the abandoned framework must not pollute the passed-count floor (skip_ci it).
SOFT:
- Prefer option (a) (importorskip + skip_ci) over moving files.
</hard-rules>

<done-signal>
Final report (5 bullets):
- Comment + conftest edits made (option a or b, with rationale).
- `tests/overlay/` collects cleanly without PySide6 (output).
- Live Tk orb import still resolves.
- Hard-rules honored (HN-2/HN-16 quoted).
- Path-taken.
</done-signal>
```

## check-w0

```prompt
ultrathink
<role>
You are a read-only phase auditor for Wave 0. Your outcome is a PASS/FAIL verdict on whether Wave 0 satisfies its EK criteria and is safe to merge — you verify, you do NOT modify any file.
</role>

<outcome>
DONE means a verdict report stating, with command evidence, whether ALL of these hold:
- The shared `jarvis/platform/` module exists (detector + Capabilities + probes), cache identity holds, and is import-clean.
- `.github/workflows/ci.yml` defines the 3-OS matrix and the `ci` workflow is green on all three legs (EK-1).
- The import-cleanliness gate and the min-passed floor both run in CI and pass.
- The Orb-framework verdict (`ADR-orb-framework.md`) exists and the stale conftest references are cleaned (PC-6).
- `tests/fakes/fake_capabilities.py` exists (first step toward EK-3).
If any fails, the verdict is FAIL with the exact blocking item.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-0-foundation.md` (the EK acceptance gate section).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` EK-1 + AD-4.
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` HN-15 + HN-16 (the CI gate that must hold).
</required-reading>

<environment-setup>
1. `git switch feat/crossplatform-port` (or the integration branch with the Wave-0 sub-task branches merged in).
2. `pip install -e ".[dev]"`
Read-only: do not create a worktree, do not edit anything.
</environment-setup>

<scope>
Modify: NOTHING. Verification only.
</scope>

<primary-path>
1. Run each acceptance command below and capture output.
2. Check the live CI run status via `gh`.
3. Render the PASS/FAIL verdict against the five outcome items.
</primary-path>

<fallback-paths>
- If `gh` cannot reach the run, report the matrix as "unverified — CI status unreachable" rather than guessing PASS. Failure condition: no network.
</fallback-paths>

<acceptance>
python -c "from jarvis.platform import detect_platform; print(detect_platform())"
python -c "from jarvis.platform.capabilities import detect_capabilities as d; print(d() is d())"
python scripts/ci/check_import_clean.py
python -c "import yaml; d=yaml.safe_load(open('.github/workflows/ci.yml')); assert d['jobs']['test']['strategy']['matrix']['os']==['ubuntu-latest','macos-latest','windows-latest']; print('matrix ok')"
test -f docs/plans/cross-platform-mac-linux/ADR-orb-framework.md && echo "adr ok"
test -f tests/fakes/fake_capabilities.py && echo "fake ok"
gh run list --workflow=ci.yml --branch feat/crossplatform-port --limit 1
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-15 — do not declare Wave 0 mergeable unless the matrix is genuinely green on all three legs.
SOFT:
- Be explicit about any item you could not verify.
</hard-rules>

<done-signal>
Final report (5 bullets):
- Platform module + cache + import-clean: PASS/FAIL.
- CI matrix shape + live green status: PASS/FAIL (with run URL).
- Gate + floor present and passing: PASS/FAIL.
- Orb verdict + conftest cleanup + first fake: PASS/FAIL.
- Overall verdict + path-taken; if FAIL, the exact blocker.
</done-signal>
```

## merge-w0

```prompt
ultrathink
<role>
You mechanically merge Wave 0's sub-task branches onto the integration branch in the prescribed order, run the wave acceptance, and push only if green. This is step-prescriptive — follow the steps exactly; do not improvise feature code.
</role>

<outcome>
DONE means: the three Wave-0 sub-task branches are merged with `--no-ff` onto `feat/crossplatform-port` in the order platform-module → orb-verdict (conftest cleanup) → ci-matrix; the wave acceptance passes locally; the CI matrix is green; and the integration branch is pushed. If any merge conflicts or any acceptance fails, you STOP and hand off to the matching recovery prompt — you do NOT force anything.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-0-foundation.md` §Parallelism (merge order: platform module → conftest cleanup → CI workflow last).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` HN-15 (matrix green before merge), HN-16 (floor), HN-17 (no force-push / no --no-verify).
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. `git switch feat/crossplatform-port`
3. `git fetch origin`
4. `git pull --ff-only origin feat/crossplatform-port`
5. `pip install -e ".[dev]"`
</environment-setup>

<scope>
Branches to merge (in order): `crossplat/w0-platform-module`, `crossplat/w0-orb-verdict`, `crossplat/w0-ci-matrix`.
Modify: only merge commits + conflict resolutions. No new features.
</scope>

<primary-path>
1. `git merge --no-ff crossplat/w0-platform-module` → if clean, `pip install -e ".[dev]"`, run acceptance.
2. `git merge --no-ff crossplat/w0-orb-verdict` → run acceptance.
3. `git merge --no-ff crossplat/w0-ci-matrix` → run acceptance.
4. After all three: run the full wave acceptance; if green, `git push origin feat/crossplatform-port` and watch `gh pr checks --watch` / `gh run list`.
</primary-path>

<fallback-paths>
- On a merge conflict → STOP, hand off to `recovery-merge-conflict` with the conflicted paths.
- On red CI after push → STOP, hand off to `recovery-red-ci`.
- Never resolve a conflict by deleting a regression guard (HN-2) or by force-push (HN-17).
</fallback-paths>

<acceptance>
python scripts/ci/check_import_clean.py
pytest -m "not skip_ci" --junitxml=report.xml -q; python scripts/ci/assert_min_passed.py report.xml
ruff check jarvis/ && ruff format --check jarvis/ && mypy jarvis/platform/
git log --oneline --merges -3
gh run list --workflow=ci.yml --branch feat/crossplatform-port --limit 1
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-15 — push only if the matrix is green.
- HN-17 — never `git push --force`, never `--no-verify`.
SOFT:
- Keep `--no-ff` so each sub-task stays a visible merge unit.
</hard-rules>

<done-signal>
Final report (5 bullets):
- The three merges done in order (merge hashes).
- Wave acceptance result.
- CI matrix green status (run URL).
- Hard-rules honored (HN-15/HN-17 quoted).
- Path-taken / any handoff to a recovery prompt.
</done-signal>
```

---

# Wave 1 — Easy / CI-provable ports (3 parallel worktrees)

> Worktree split (per `WELLE-1-easy-ports.md` §Parallelism): **A=Terminal** (1.1+1.2), **B=App-launch** (1.3), **C=Hotkey** (1.4). Sub-task **1.5** (pyproject extras) is shared by A (ptyprocess) and C (pynput) — land it as a tiny standalone PR FIRST (or assign to A and have C rebase). Depends only on Wave 0; nothing merges until Wave 0's matrix is green (AD-4 / HN-15).

## 1.5

```prompt
ultrathink
<role>
You add the `[desktop]` extras entries (ptyprocess + pynput) that Wave-1's terminal and hotkey ports need. Your outcome is a one-line-each additive pyproject edit that keeps the base €5-VPS install clean. Land this FIRST so Worktrees A and C do not collide on pyproject.toml.
</role>

<outcome>
DONE means:
- `pyproject.toml` `[project.optional-dependencies].desktop` gains `"pynput>=1.7"` (NO platform marker — all-platform) and `"ptyprocess>=0.7; sys_platform != 'win32'"` (POSIX-only).
- It does NOT add `pyobjc-*` (that is Wave 2's `[desktop-macos]`) and does NOT add `pyatspi` (distro-only, never pip — AD-14/HN-9).
- After the edit, `pip install -e ".[desktop]" --no-deps` then `import ptyprocess, pynput` works on a POSIX box.
- All acceptance commands pass.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-1-easy-ports.md` §1.5.
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-14 (dependency grouping).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-8 ("Never add pyobjc-*, pyatspi, or pynput to the base dependencies. Extras only"), HN-9 (pyatspi distro-only), HN-10 (Windows-only deps need the sys_platform marker).
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-3 (pyatspi is not pip-installable).
- Mirror the existing `sys_platform` marker pattern at `pyproject.toml:99-110`.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. `git worktree add -b crossplat/w1-deps ../sub-agents-outputs/crossplat-w1-deps feat/crossplatform-port`
3. `pip install -e ".[dev]"`
4. After editing extras, reinstall per the BUG-006/014 contract: `pip install -e ".[desktop]" --no-deps`
</environment-setup>

<scope>
Modify: `pyproject.toml` (the `desktop` extras group only). Touch nothing else.
</scope>

<primary-path>
1. Open the `desktop` group at `pyproject.toml:99-110`; add the two lines mirroring the existing marker style.
2. Reinstall + import-verify.
</primary-path>

<fallback-paths>
- If a `desktop` group does not exist exactly at those lines (drift), find the real `[project.optional-dependencies]` `desktop` array and add there; if no `desktop` group exists, create it. Failure condition: tomllib cannot parse the file after the edit.
- You may choose slightly higher minimum versions if the pinned ones are yanked, provided the markers (none for pynput; `sys_platform != 'win32'` for ptyprocess) are correct.
</fallback-paths>

<acceptance>
python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); deps=d['project']['optional-dependencies']['desktop']; assert any(x.startswith('pynput') for x in deps) and any('ptyprocess' in x for x in deps)"
python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); deps=d['project']['optional-dependencies']['desktop']; assert all('pyobjc' not in x and 'pyatspi' not in x for x in deps)"
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-8 — extras only; no port lib in base `dependencies`.
- HN-9 / AP-3 — never add `pyatspi` to pyproject.
- HN-10 — `ptyprocess` carries `sys_platform != 'win32'`.
SOFT:
- Keep the edit minimal and additive.
</hard-rules>

<done-signal>
Final report (5 bullets):
- The two lines added + markers confirmed (tomllib assertion output).
- No pyobjc/pyatspi present (assertion output).
- `pip install -e ".[desktop]"` + import verify result.
- Hard-rules honored (HN-8/HN-9/HN-10 quoted).
- Path-taken.
</done-signal>
```

## 1.1

```prompt
ultrathink
<role>
You port the built-in terminal to Unix via a `PtyBackend` seam, with `ptyprocess` as the POSIX backend. This is the standout verification win — a real PTY runs end-to-end on the CI runners. Your outcome is a Unix PTY backend that mirrors pywinpty 1:1, with str↔bytes normalized once at the seam, and the Windows path untouched.
</role>

<outcome>
DONE means:
- `jarvis/terminal/backend.py` defines a `PtyBackend` Protocol (the 5 methods the read-loop uses, normalized: `spawn(argv,cwd,cols,rows)->PtyHandle`; handle `write(str)->None`, `setwinsize(rows,cols)->None`, `read(size)->str`, `isalive()->bool`, `terminate(force:bool)->None`, `exitstatus->int|None`, `pid->int`) + `WinptyBackend` (thin wrapper preserving the exact `RuntimeError("pywinpty nicht installiert …")` degrade at `pty_manager.py:72-75`) + `UnixPtyBackend` (ptyprocess) + `make_pty_backend()` factory.
- `jarvis/terminal/pty_manager.py` routes through the seam: the inline `from winpty import PtyProcess` (`:71`) and the five direct `proc.*` calls (`:127`,`:138`,`:173`,`:193`,`:206`,`:225`) go through `PtyBackend`. The daemon-thread read-loop (`_reader_loop` `:179-239`) stays structurally identical (AD-9: NO async rewrite).
- `UnixPtyBackend.read` decodes bytes→str (utf-8/replace); `write` encodes str→bytes; `spawn` honors `dimensions=(rows, cols)`.
- `make_pty_backend()` returns `UnixPtyBackend` on POSIX, `WinptyBackend` on Windows, and a null backend (whose `spawn` raises a clear English RuntimeError the manager surfaces as a typed error) when `not capabilities.has_pty`.
- `tests/unit/terminal/test_unix_pty.py` spawns a real `bash -c "echo hi"` via ptyprocess and asserts the round-trip + `exitstatus == 0`.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-1-easy-ports.md` §1.1 (the full seam spec + the exact line references).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-9 (ptyprocess 1:1, no async rewrite) + AD-6 (graceful null) + AD-7 (Windows untouched).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-1 (never rewrite the Windows impl; add a sibling behind the seam), HN-4 (no factory branch raises; degrade), HN-7 (no module-scope Windows import).
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-1 (str↔bytes mismatch at the PtyBackend seam — read the full counter-pattern) + AP-12 (ship a fake, no unittest.mock).
- Read `jarvis/terminal/pty_manager.py` around the cited lines to see the existing `isinstance(data, bytes)` defensive branch at `:210-214`.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. `git worktree add -b crossplat/w1-terminal-pty ../sub-agents-outputs/crossplat-w1-terminal feat/crossplatform-port`
3. Ensure 1.5's `[desktop]` extras are present (rebase if 1.5 landed separately); `pip install -e ".[dev,desktop]" --no-deps` (or `.[dev]` + ensure `ptyprocess` importable on POSIX).
4. `python -c "import jarvis; print(jarvis.__file__)"`
</environment-setup>

<scope>
Create: `jarvis/terminal/backend.py`, `tests/fakes/fake_pty_backend.py`, `tests/unit/terminal/test_unix_pty.py`.
Modify: `jarvis/terminal/pty_manager.py` (route through the seam; keep the read-loop structure). Do NOT touch the Windows winpty behavior beyond relocating its import into `WinptyBackend`.
</scope>

<primary-path>
1. Define `PtyBackend` Protocol + `PtyHandle` Protocol in `backend.py`.
2. Implement `WinptyBackend` (lazy `from winpty import PtyProcess`, preserve the exact RuntimeError degrade) and `UnixPtyBackend` (lazy `from ptyprocess import PtyProcess`, str↔bytes at the seam, `dimensions=(rows,cols)`).
3. Implement `make_pty_backend()` selecting on `detect_platform()` + `capabilities.has_pty`; null backend otherwise.
4. Rewire `PtyManager.spawn` (`:55`) + `PtySession.proc` (`:39`) to the seam; leave the read-loop intact.
5. Write `fake_pty_backend.py` (a FakePtyBackend yielding scripted str chunks) + `test_unix_pty.py` (real ptyprocess `echo hi` round-trip + the fake contract test pinning up-seam type is `str`).
</primary-path>

<fallback-paths>
- If `ptyprocess.spawn` signature differs by version (`dimensions` vs `rows`/`cols`), adapt at the seam and pin with the fake. Failure condition: spawn raises a TypeError.
- If the read-loop's existing `isinstance(data, bytes)` branch double-decodes after the seam normalizes, remove the now-redundant branch ONLY inside the loop and document it (the seam now guarantees str). Failure condition: mojibake in the round-trip test.
- You may invent the handle abstraction shape if the prescribed one fights ptyprocess, provided AD-9 (no async rewrite) and AP-1 (str up-seam) hold, pinned by the contract test.
</fallback-paths>

<acceptance>
pytest tests/unit/terminal/test_unix_pty.py -v
pytest tests/unit/terminal/ -v
python -c "from jarvis.terminal.backend import make_pty_backend; print(type(make_pty_backend()).__name__)"
python -c "import ast; m=ast.parse(open('jarvis/terminal/pty_manager.py').read()); assert not any(getattr(n,'module',None)=='winpty' for n in ast.walk(m) if isinstance(n,ast.ImportFrom) and getattr(n,'col_offset',1)==0)"
ruff check jarvis/terminal/ && mypy jarvis/terminal/
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-1 / AD-7 — the Windows winpty behavior is untouched; only relocated behind `WinptyBackend`.
- HN-4 — `make_pty_backend()` never raises; the null backend surfaces a typed RuntimeError on spawn.
- HN-7 / AP-13 — no module-scope `winpty`/`ptyprocess` import; lazy inside the backend.
- AP-1 — normalize str↔bytes ONCE at the seam; up-seam type is always `str`.
SOFT:
- `argv` is `(path, "-i")`-style from shells.py; do not hardcode shells here.
</hard-rules>

<done-signal>
Final report (5 bullets):
- `PtyBackend` + the three backends + factory; the 5 method mappings.
- Real-PTY round-trip test result (echo hi, exitstatus 0).
- str↔bytes normalization location + the AP-1 contract test.
- Hard-rules honored (HN-1/HN-4/HN-7/AP-1 quoted).
- Path-taken + whether the read-loop's isinstance branch was kept/removed.
</done-signal>
```

## 1.2

```prompt
ultrathink
<role>
You add Unix shell discovery to the terminal so the Unix PTY backend has shells to spawn. Your outcome is a `_unix_shells()` branch in `discover_shells()` that finds POSIX shells in preference order, with the four Windows shell factories left completely untouched.
</role>

<outcome>
DONE means:
- `jarvis/terminal/shells.py` gains `_unix_shells() -> list[ShellInfo]` returning POSIX shells in order: (1) `$SHELL` if set and on disk (label from basename); (2) parsed `/etc/shells` paths; (3) `shutil.which("bash"/"zsh"/"fish")` fallback. Deduped by resolved path. Each `ShellInfo.argv == (path, "-i")` (interactive, never unconditional `-l`).
- `discover_shells()` (`:71-78`) dispatches on `detect_platform()`: `win32` → the four unchanged Windows factories (`_powershell_7` `:23`, `_windows_powershell` `:35`, `_cmd` `:48`, `_git_bash` `:55`); else → `_unix_shells()`.
- `get_shell(shell_id)` (`:81`) works unchanged.
- `tests/unit/terminal/test_unix_shells.py` green; the file introduces no module-scope Windows-only import.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-1-easy-ports.md` §1.2.
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-9 + AD-7 (Windows factories untouched).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-1 (add a sibling, never rewrite the Windows impl), HN-3 (read platform from the capability/detect module, not inline drift), HN-7 (no module-scope Windows import).
- Read `jarvis/terminal/shells.py` to see `ShellInfo` and the four Windows factories.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. Same worktree as 1.1 (Worktree A — Terminal): `../sub-agents-outputs/crossplat-w1-terminal` on `crossplat/w1-terminal-pty`.
3. `pip install -e ".[dev]"`
</environment-setup>

<scope>
Modify: `jarvis/terminal/shells.py` (add `_unix_shells()` + the dispatch). Create: `tests/unit/terminal/test_unix_shells.py`. Leave the four Windows factories untouched.
</scope>

<primary-path>
1. Write `_unix_shells()` with the three-tier discovery + dedupe-by-resolved-path + `(path, "-i")` argv.
2. Gate `discover_shells()` on `detect_platform()`.
3. Write the test: monkeypatch `$SHELL`, a fake `/etc/shells`, and `shutil.which`; assert dedupe + order + argv.
</primary-path>

<fallback-paths>
- If `/etc/shells` is absent (some minimal containers), skip tier 2 gracefully and rely on `$SHELL`/`which`. Failure condition: discovery returns empty on a box that has bash.
- If `$SHELL` points at a path not on disk, skip it (don't trust the env blindly). Failure condition: a non-existent shell is offered.
- You may add `dash`/`sh` to the `which` fallback list if bash/zsh/fish are all absent, provided the Windows branch is untouched (HN-1).
</fallback-paths>

<acceptance>
pytest tests/unit/terminal/test_unix_shells.py -v
python -c "from jarvis.terminal.shells import discover_shells; print([s.id for s in discover_shells()])"
python -c "import ast; m=ast.parse(open('jarvis/terminal/shells.py').read()); assert all(getattr(n,'module',None)!='winreg' for n in ast.walk(m))"
ruff check jarvis/terminal/ && mypy jarvis/terminal/
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-1 / AD-7 — the four Windows shell factories are untouched.
- HN-3 — dispatch via `detect_platform()`, not scattered inline `sys.platform`.
- HN-7 — no module-scope Windows import.
SOFT:
- Use `(path, "-i")`, never unconditional `-l` (slow profile re-source).
</hard-rules>

<done-signal>
Final report (5 bullets):
- `_unix_shells()` + the three-tier order + dedupe approach.
- Dispatch gated on `detect_platform()`.
- Test result + the discovered shell ids on the box.
- Hard-rules honored (HN-1/HN-3/HN-7 quoted).
- Path-taken.
</done-signal>
```

## 1.3

```prompt
ultrathink
<role>
You port app-launch-by-name to macOS (`open -a`) and Linux (`xdg-open`/direct exec), with a platform-conditional `KNOWN_APPS` whitelist. Your outcome is cross-platform launch resolution that keeps the anti-STT-hallucination gate intact and reuses the OS-agnostic URL/path/Spotify escape hatches verbatim.
</role>

<outcome>
DONE means:
- `jarvis/plugins/tool/app_resolver.py`: `resolve_app_launch_target` (`:88`) branches on `detect_platform()` — macOS prefers `LaunchTarget("open_a", canonical)` (fallback `shutil.which`); Linux prefers `shutil.which(canonical)` for direct exec else `LaunchTarget("xdg_open", canonical)`. The URL/path escape hatch (`:96-99`) + Spotify protocol case (`:102`) are kept; `winreg` stays lazy-guarded (`:24-27`). The `LaunchKind` Literal (`:30`) gains `"open_a"` and `"xdg_open"`. `_EXE_ALIASES` (`:54`) becomes platform-conditional.
- `jarvis/plugins/tool/open_app.py`: `KNOWN_APPS` (`:23`) selected by `detect_platform()` — keep Windows set as `_KNOWN_APPS_WIN`; add `_KNOWN_APPS_DARWIN` (safari, terminal, finder, calculator, firefox, chrome, vscode, slack, …) and `_KNOWN_APPS_LINUX` (firefox, nautilus, gnome-terminal, gnome-calculator, code, chromium, …) per AD-15. `_is_plausible_app_name` (`:68`), `_APP_NAME_RE`/`_HALLUCINATION_RE` (`:52`/`:58`), and the PATH/URL/path escape hatches (`:81-89`) are reused verbatim. `OpenAppTool.execute` (`:116`) branches launch on `LaunchKind`: `open_a`→`Popen(["open","-a",value,*args])`, `xdg_open`→`Popen(["xdg-open",value])`, `executable`→`Popen([value,*args])`, `startfile`→Windows-only (`:153`). Every `Popen` keeps `shell=False`.
- `tests/unit/plugins/tool/test_app_resolver_unix.py` green.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-1-easy-ports.md` §1.3 (the full branch spec + line refs).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-15 (platform-conditional KNOWN_APPS) + AD-6 (graceful) + AD-7 (Windows untouched).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-3 (read platform from the shared module), HN-4 (no branch raises), HN-7 (no module-scope Windows import), HN-18 ("Every new subprocess on ANY OS passes creationflags/equivalent from jarvis/core/process_utils.py").
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-11 (don't re-detect sys.platform per call-site) + AP-12 (ship a fake).
- CLAUDE.md AP-1 — `NO_WINDOW_CREATIONFLAGS` from `jarvis/core/process_utils.py` on every subprocess.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. `git worktree add -b crossplat/w1-app-launch ../sub-agents-outputs/crossplat-w1-applaunch feat/crossplatform-port`
3. `pip install -e ".[dev]"`
4. `python -c "import jarvis; print(jarvis.__file__)"`
</environment-setup>

<scope>
Modify: `jarvis/plugins/tool/app_resolver.py`, `jarvis/plugins/tool/open_app.py`. Create: `tests/unit/plugins/tool/test_app_resolver_unix.py`.
This worktree owns no shared file with A or C. Do NOT edit `pyproject.toml` (that is 1.5).
</scope>

<primary-path>
1. Extend `LaunchKind` with `open_a`/`xdg_open`; make `_EXE_ALIASES` platform-conditional.
2. Branch `resolve_app_launch_target` on `detect_platform()`, keeping the escape hatches.
3. Split `KNOWN_APPS` into `_KNOWN_APPS_{WIN,DARWIN,LINUX}` + select by platform; reuse the plausibility gate verbatim.
4. Branch `OpenAppTool.execute` launch on `LaunchKind`; route every `Popen` through `NO_WINDOW_CREATIONFLAGS` (HN-18) and keep `shell=False`.
5. Write `test_app_resolver_unix.py` monkeypatching `detect_platform`→darwin/linux, asserting: safari→open_a, a PATH tool→executable, an unknown name→xdg_open/raw, a URL short-circuits to the shell verb.
</primary-path>

<fallback-paths>
- If `xdg-open` is absent on a minimal Linux box, fall back to direct exec via `which` and log; never raise (HN-4). Failure condition: resolution raises on a box with no xdg-open.
- If the `.desktop` walk (`$XDG_DATA_DIRS/applications` + `~/.local/share/applications`) is too heavy for CI, gate it behind a cheap existence check and unit-test the resolver logic with a fake filesystem. Failure condition: the test needs a real desktop.
- You may add more KNOWN_APPS entries per OS as long as the plausibility regex + escape hatches are reused verbatim (AD-15) and platform is read via the shared module (AP-11).
</fallback-paths>

<acceptance>
pytest tests/unit/plugins/tool/test_app_resolver_unix.py -v
python -c "from jarvis.plugins.tool.open_app import KNOWN_APPS; assert 'safari' in KNOWN_APPS or 'firefox' in KNOWN_APPS"
pytest tests/unit/plugins/tool/ -k app -v
python -c "import ast; src=open('jarvis/plugins/tool/open_app.py').read(); assert 'shell=True' not in src"
ruff check jarvis/plugins/tool/ && mypy jarvis/plugins/tool/app_resolver.py
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-3 / AP-11 — platform via the shared `detect_platform()`, not scattered inline checks.
- HN-4 — resolution degrades, never raises.
- HN-7 — `winreg` stays lazy-guarded; no new module-scope Windows import.
- HN-18 — every `Popen` uses `NO_WINDOW_CREATIONFLAGS`; `shell=False` always.
- AD-7 — the Windows launch branch (`startfile`) is untouched.
SOFT:
- Reuse `_is_plausible_app_name`/regex/escape hatches verbatim — only the whitelist set swaps.
</hard-rules>

<done-signal>
Final report (5 bullets):
- `LaunchKind` extension + the per-OS resolution branches.
- The three `KNOWN_APPS` sets + selection.
- `shell=False` + `NO_WINDOW_CREATIONFLAGS` confirmed on every Popen.
- Hard-rules honored (HN-3/HN-4/HN-7/HN-18 quoted).
- Path-taken + how the .desktop walk was tested.
</done-signal>
```

## 1.4

```prompt
ultrathink
<role>
You port the global hotkey to a dual-backend seam: Windows keeps `global-hotkeys` (with its battle-tested L/R-Alt + refcount bug fixes), macOS/Linux gain `pynput`, and Wayland degrades to a logged no-op. Your outcome is a `HotkeyBackend` seam where the Windows logic is RELOCATED verbatim (not refactored) and the degrade contract + PTT edges are unchanged.
</role>

<outcome>
DONE means:
- `jarvis/trigger/backends/__init__.py` defines `HotkeyBackend` Protocol (`register(bindings,on_event)`, `unregister()`, `start()`, `stop()`, `received_any_event()->bool`) + `make_hotkey_backend()` factory.
- `jarvis/trigger/backends/global_hotkeys.py` holds the RELOCATED-VERBATIM Windows logic: `_KEY_MAP` (`hotkey.py:50`), `_normalize_combo` (`:104`), the module-level `_CHECKER_LOCK`/`_CHECKER_REFCOUNT` guard (`:69-101`), the idempotent pre-remove→register sequence (`:278-301`). These carry the BUG fixes — relocate, do not refactor (AD-7).
- `jarvis/trigger/backends/pynput.py` maps jarvis combo syntax (`ctrl+right_alt+j`) to pynput (`<ctrl>+<alt>+j`), keeps a tiny refcount, and on macOS flags "registered but zero events" via `received_any_event()` (AD-8 Input-Monitoring guidance).
- `jarvis/trigger/backends/noop.py` is returned when `not capabilities.has_hotkey` (true on Wayland); logs ONCE the English "global hotkey unavailable on Wayland by OS design; lean on the wake word" message then no-ops.
- `make_hotkey_backend()`: win32→GlobalHotkeysBackend; else if `capabilities.has_hotkey`→PynputBackend; else→NoopBackend.
- `jarvis/trigger/hotkey.py`: `HotkeyTrigger.__aenter__` (`:238`) calls the factory; its try/except degrade-to-None semantics + the "voice still works via wake word" message (`:243-248`) + `validate_hotkey` (`:121`) + the PTT press/release edges (`:256-267`) stay intact.
- The existing 48-case `tests/unit/trigger/test_hotkey.py` stays green; `tests/unit/trigger/test_hotkey_backends.py` green.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-1-easy-ports.md` §1.4 (the full relocation spec + line refs).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-8 (dual backend + Wayland no-op + macOS zero-events detection) + AD-7 (Windows untouched) + AD-6.
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-1 (relocate, never refactor the Windows impl), HN-2 (don't weaken the 48-case regression guard), HN-4 (no branch raises; degrade), HN-7 (no module-scope `global_hotkeys` import).
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-4 (Wayland hotkey silently dead — read the full counter-pattern) + AP-12 (ship a fake, no unittest.mock).
- Read `jarvis/trigger/hotkey.py` around the cited lines (the refcount guard + the degrade contract).
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. `git worktree add -b crossplat/w1-hotkey ../sub-agents-outputs/crossplat-w1-hotkey feat/crossplatform-port`
3. Ensure 1.5's `[desktop]` (pynput) is present (rebase if landed separately); `pip install -e ".[dev,desktop]" --no-deps`.
4. `python -c "import jarvis; print(jarvis.__file__)"`
</environment-setup>

<scope>
Create: `jarvis/trigger/backends/__init__.py`, `jarvis/trigger/backends/global_hotkeys.py`, `jarvis/trigger/backends/pynput.py`, `jarvis/trigger/backends/noop.py`, `tests/fakes/fake_hotkey_backend.py`, `tests/unit/trigger/test_hotkey_backends.py`.
Modify: `jarvis/trigger/hotkey.py` (route through the factory; keep degrade contract + PTT edges + validate_hotkey).
</scope>

<primary-path>
1. Define `HotkeyBackend` Protocol + `make_hotkey_backend()` in `backends/__init__.py`.
2. RELOCATE the Windows logic verbatim into `global_hotkeys.py` (copy `_KEY_MAP`, `_normalize_combo`, the refcount guard, the pre-remove→register sequence — do NOT rewrite).
3. Implement `PynputBackend` (combo translation + tiny refcount + zero-events flag) and `NoopBackend` (log-once + no-op).
4. Rewire `HotkeyTrigger.__aenter__` to the factory, mapping the old `self._gh=None` degrade onto `backend=None`; keep the messages + PTT edges.
5. Write `fake_hotkey_backend.py` + `test_hotkey_backends.py` (factory selection per platform; the Windows refcount 0↔1 boundary; noop logs-once-then-no-ops without raising). Re-run the 48-case suite.
</primary-path>

<fallback-paths>
- If `pynput.keyboard.GlobalHotKeys` cannot express the PTT both-edges case, use a `pynput.keyboard.Listener` for press+release and synthesize the combo match. Failure condition: PTT release never fires.
- If relocating the module-level refcount globals breaks `_reset_checker_state_for_tests` (`:97`), keep that reset hook working by re-exporting/redirecting it to the backend module. Failure condition: a test that resets checker state fails.
- You may invent the combo-syntax translation table if the prescribed `<ctrl>+<alt>+j` mapping is incomplete, provided the Windows `_normalize_combo` is untouched (HN-1) and Wayland still no-ops (AP-4).
</fallback-paths>

<acceptance>
pytest tests/unit/trigger/test_hotkey_backends.py -v
pytest tests/unit/trigger/test_hotkey.py -v
python -c "from jarvis.trigger.backends import make_hotkey_backend; print(type(make_hotkey_backend()).__name__)"
python -c "import ast; m=ast.parse(open('jarvis/trigger/hotkey.py').read()); assert not any(getattr(n,'names',None) and any(a.name=='global_hotkeys' for a in n.names) for n in ast.walk(m) if isinstance(n,ast.Import))"
ruff check jarvis/trigger/ && mypy jarvis/trigger/
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-1 / AD-7 — the Windows hotkey logic is RELOCATED verbatim, never refactored (it carries the L/R-Alt + refcount + remove-by-string bug fixes).
- HN-2 — the 48-case `test_hotkey.py` regression suite stays green; do not weaken it.
- HN-4 / AP-4 — Wayland → NoopBackend logs once + no-ops; never registers a dead listener; never raises.
- HN-7 — no module-scope `global_hotkeys` import in `hotkey.py` (lazy inside the backend is fine).
SOFT:
- macOS zero-events detection feeds the wizard's Input-Monitoring grant message.
</hard-rules>

<done-signal>
Final report (5 bullets):
- The seam + three backends + factory selection.
- Confirmation the Windows logic was relocated verbatim (not refactored).
- 48-case suite + new backend tests: green.
- Hard-rules honored (HN-1/HN-2/HN-4/HN-7 quoted).
- Path-taken + the PTT both-edges approach on pynput.
</done-signal>
```

## check-w1

```prompt
ultrathink
<role>
You are a read-only phase auditor for Wave 1. Your outcome is a PASS/FAIL verdict on whether the three easy ports (terminal, app-launch, hotkey) meet EK-2/EK-3/EK-4 and are safe to merge. You verify; you do not modify.
</role>

<outcome>
DONE means a verdict report stating, with command evidence, whether:
- Terminal: `make_pty_backend()` selects per platform; a real-PTY test passes; the read-loop is unchanged (AD-9); str up-seam (AP-1) pinned by a fake (EK-4).
- App-launch: per-OS resolution + platform `KNOWN_APPS`; `shell=False` everywhere; resolution tests green.
- Hotkey: dual backend + Wayland no-op; the 48-case suite still green; module-scope `global_hotkeys` import gone.
- Each new seam ships a `tests/fakes/` fake; no `unittest.mock` (EK-3).
- The CI matrix is still green on all three legs (HN-15).
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-1-easy-ports.md` (EK acceptance gate).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` EK-2/EK-3/EK-4.
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` HN-1/HN-2/HN-4/HN-7/HN-15.
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-1/AP-4/AP-12.
</required-reading>

<environment-setup>
1. `git switch feat/crossplatform-port` (with Wave-1 branches merged in, or check each branch).
2. `pip install -e ".[dev,desktop]" --no-deps`
Read-only.
</environment-setup>

<scope>
Modify: NOTHING. Also `grep` for `unittest.mock` in the new test files and for any `shell=True` in the touched tool modules.
</scope>

<primary-path>
1. Run each acceptance command; capture output.
2. Grep for `unittest.mock` in the three new test files + `shell=True` in open_app.
3. Confirm the CI run is green.
4. Render the verdict.
</primary-path>

<fallback-paths>
- If a sub-task branch is not yet merged, audit it on its own branch and note the integration state. Failure condition: a branch is missing.
</fallback-paths>

<acceptance>
python -c "from jarvis.terminal.backend import make_pty_backend; print(type(make_pty_backend()).__name__)"
pytest tests/unit/terminal/ -v
pytest tests/unit/plugins/tool/ -k app -v
pytest tests/unit/trigger/test_hotkey.py tests/unit/trigger/test_hotkey_backends.py -v
grep -rn "unittest.mock" tests/fakes/fake_pty_backend.py tests/fakes/fake_hotkey_backend.py || echo "no unittest.mock in new fakes"
grep -n "shell=True" jarvis/plugins/tool/open_app.py || echo "no shell=True"
gh run list --workflow=ci.yml --branch feat/crossplatform-port --limit 1
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-15 — do not declare mergeable unless the matrix is green.
- HN-2 — confirm the 48-case hotkey suite is intact, not weakened.
SOFT:
- Be explicit about any unverified item.
</hard-rules>

<done-signal>
Final report (5 bullets):
- Terminal verdict (real-PTY + read-loop intact + fake).
- App-launch verdict (resolution + shell=False).
- Hotkey verdict (dual backend + 48-case green + import gone).
- EK-3 fakes / no-mock + CI green: PASS/FAIL.
- Overall verdict + path-taken; if FAIL, the exact blocker.
</done-signal>
```

## merge-w1

```prompt
ultrathink
<role>
You mechanically merge Wave 1's branches onto the integration branch, run the wave acceptance, and push only if green. Step-prescriptive.
</role>

<outcome>
DONE means: `crossplat/w1-deps` merges FIRST (the shared pyproject edit), then the three feature branches (`crossplat/w1-terminal-pty`, `crossplat/w1-app-launch`, `crossplat/w1-hotkey`) merge `--no-ff` onto `feat/crossplatform-port`; the wave acceptance passes; the CI matrix is green; the branch is pushed. Any conflict or red CI → STOP and hand off to the matching recovery prompt.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-1-easy-ports.md` §Parallelism (land 1.5 first to avoid the pyproject conflict).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` HN-15, HN-16, HN-17.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. `git switch feat/crossplatform-port`
3. `git fetch origin && git pull --ff-only origin feat/crossplatform-port`
4. `pip install -e ".[dev,desktop]" --no-deps`
</environment-setup>

<scope>
Branches in order: `crossplat/w1-deps`, then `crossplat/w1-terminal-pty`, `crossplat/w1-app-launch`, `crossplat/w1-hotkey`. Merge commits + conflict resolutions only.
</scope>

<primary-path>
1. `git merge --no-ff crossplat/w1-deps`; reinstall extras.
2. `git merge --no-ff crossplat/w1-terminal-pty` → reinstall → run acceptance.
3. `git merge --no-ff crossplat/w1-app-launch` → run acceptance.
4. `git merge --no-ff crossplat/w1-hotkey` → run acceptance.
5. Full wave acceptance; if green, `git push origin feat/crossplatform-port`; watch CI.
</primary-path>

<fallback-paths>
- Merge conflict (likely on `pyproject.toml` if 1.5 wasn't landed first) → STOP, hand off to `recovery-merge-conflict`.
- Red CI after push → STOP, hand off to `recovery-red-ci`.
- Never force-push (HN-17), never delete a regression guard to resolve a conflict (HN-2).
</fallback-paths>

<acceptance>
python scripts/ci/check_import_clean.py
pytest -m "not skip_ci" --junitxml=report.xml -q; python scripts/ci/assert_min_passed.py report.xml
pytest tests/unit/terminal/ tests/unit/trigger/test_hotkey.py tests/unit/plugins/tool/ -k "app or hotkey or pty or shell" -q
ruff check jarvis/ && mypy jarvis/terminal/ jarvis/trigger/ jarvis/plugins/tool/app_resolver.py
gh run list --workflow=ci.yml --branch feat/crossplatform-port --limit 1
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-15 — push only if green.
- HN-17 — no force-push, no `--no-verify`.
SOFT:
- Land 1.5 first; `--no-ff` for each unit.
</hard-rules>

<done-signal>
Final report (5 bullets):
- Merge order + hashes (deps first).
- Wave acceptance result.
- CI matrix green status (run URL).
- Hard-rules honored (HN-15/HN-17 quoted).
- Path-taken / any recovery handoff.
</done-signal>
```

---

# Wave 2 — Permission / GUI-heavy ports (parallel)

> Worktree split (per `WELLE-2-gui-permission.md` §Parallelism): **D=UI-element-click** (2.1+2.2+2.3+2.4 + the pyatspi doc note), **E=Orb** (2.5+2.6 + the `[desktop-macos]` extra in 2.7). The only shared file is `pyproject.toml` (2.7) — split it: E owns the `desktop-macos` block; D adds only a README pyatspi note. Depends on Wave 0 (the platform factory + `has_ax_tree`/`has_overlay`/`is_wayland` probes + the green matrix + the Orb-framework ADR). Independent of Waves 1 and 3.

## 2.3

```prompt
ultrathink
<role>
You build the native-role → canonical-UIA-role normalization table that keeps the model prompt platform-agnostic. This is the quick first PR that 2.1 and 2.2 build on. Your outcome is a `normalize_role()` that maps macOS AX roles and Linux AT-SPI roles into the SAME canonical UIA vocabulary the Windows path already uses — so the model never sees `AXButton` or `push button`, only `Button`.
</role>

<outcome>
DONE means:
- `jarvis/vision/role_map.py` defines the macOS AX table, the Linux AT-SPI table, and `normalize_role(native_role, platform) -> str`.
- Every output role is a member of the canonical set: `_CLICKABLE_UIA_ROLES` (`screenshot_only_loop.py:1072`) ∪ `DEFAULT_INTERESTING_ROLES` (`pruning.py:51`): {Button, MenuItem, ListItem, TabItem, CheckBox, RadioButton, Hyperlink, Edit, ComboBox, TreeItem, SplitButton, Text}.
- Representative mappings hold: `AXButton`→Button, `AXTextField`/`AXTextArea`→Edit, `AXPopUpButton`/`AXComboBox`→ComboBox, `AXMenuItem`→MenuItem, `AXLink`→Hyperlink, `AXStaticText`→Text, `AXRow`/`AXCell`→ListItem, `AXOutlineRow`→TreeItem; `ROLE_PUSH_BUTTON`→Button, `ROLE_TEXT`/`ROLE_ENTRY`→Edit, `ROLE_COMBO_BOX`→ComboBox, `ROLE_MENU_ITEM`→MenuItem, `ROLE_LINK`→Hyperlink, `ROLE_LABEL`→Text, `ROLE_PAGE_TAB`→TabItem, `ROLE_LIST_ITEM`/`ROLE_TABLE_CELL`→ListItem, `ROLE_TREE_ITEM`→TreeItem. Unknown roles map to `Text` (visible, not pixel-guessed) or are droppable by `filter_by_role`.
- `tests/unit/vision/test_role_map.py` green with a parity assertion that EVERY produced role is in the canonical set.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-2-gui-permission.md` §2.3.
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-10 (normalize into canonical UIA vocabulary).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-3 (single source of truth, no drift) and HN-7 (no module-scope OS import — pyobjc/pyatspi constants are NOT imported here; use plain strings).
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-8 (role-vocabulary drift = the BUG-008 multi-layer-enum-drift class — read the full counter-pattern; note the live source of the role set is `screenshot_only_loop.py:1072`).
- `docs/anti-drift-three-layer.md` (the five-layer pattern this defends).
- Read `jarvis/harness/screenshot_only_loop.py:1072` and `jarvis/vision/pruning.py:51` to copy the canonical set EXACTLY.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. `git worktree add -b crossplat/w2-ui-click ../sub-agents-outputs/crossplat-w2-uiclick feat/crossplatform-port`
3. `pip install -e ".[dev]"`
</environment-setup>

<scope>
Create: `jarvis/vision/role_map.py`, `tests/unit/vision/test_role_map.py`.
Do NOT edit `pruning.py`/`screenshot_only_loop.py` role sets — they are the canonical source (read-only here). This worktree (D) continues into 2.1/2.2/2.4.
</scope>

<primary-path>
1. Read the two canonical role sets verbatim; build the union as the allowed output set.
2. Write the AX table and the AT-SPI table as plain string→string dicts (no pyobjc/pyatspi imports — use the role-name strings).
3. Write `normalize_role()` with an unknown-role fallback to `Text` (or a documented drop).
4. Write the parity test asserting every value in both tables ∈ the canonical union + the representative mappings.
</primary-path>

<fallback-paths>
- If a native role has no clean canonical analogue, map it to `Text` (keeps it visible) rather than inventing a new canonical role — adding a role would be enum drift (AP-8). Failure condition: a produced role escapes the canonical set.
- You may extend the tables with more native roles, provided the parity test still passes (every output ∈ canonical union).
</fallback-paths>

<acceptance>
pytest tests/unit/vision/test_role_map.py -v
python -c "from jarvis.vision.role_map import normalize_role; assert normalize_role('AXButton','darwin')=='Button' and normalize_role('ROLE_PUSH_BUTTON','linux')=='Button'"
ruff check jarvis/vision/role_map.py && mypy jarvis/vision/role_map.py
</acceptance>

<hard-rules>
INVIOLABLE:
- AP-8 / AD-10 — normalize into the EXISTING canonical UIA vocabulary; never emit a raw native role; never add a new canonical role.
- HN-3 — one mapping table per OS as the single source; pin with a parity test.
- HN-7 — no pyobjc/pyatspi import here (use role-name strings).
SOFT:
- Unknown → `Text`, not a guess.
</hard-rules>

<done-signal>
Final report (5 bullets):
- The two tables + `normalize_role` + unknown-role policy.
- The canonical union copied from the live source (which file:line).
- Parity test result (every output ∈ canonical).
- Hard-rules honored (AP-8/HN-3/HN-7 quoted).
- Path-taken.
</done-signal>
```

## 2.1

```prompt
ultrathink
<role>
You add the macOS AX-tree `VisionSource` (`pyobjc` AXUIElement) so UI-element-click works on macOS. Your outcome is an `AXTreeSource` that satisfies the existing `VisionSource` Protocol, produces the SAME `Observation`/`UIANode` layout the Windows path produces (so downstream is identical), normalizes roles via 2.3, and detect-and-degrades on a missing Accessibility grant — never silently empty.
</role>

<outcome>
DONE means:
- `jarvis/vision/ax_tree.py` defines `AXTreeSource` satisfying `VisionSource` (`protocols.py:419`): `name="ax-tree"`, `kind="ui_tree"`, `async def observe(...) -> Observation`, `async def close()`. It returns `Observation` (`protocols.py:402`) carrying a tuple of `UIANode` (`protocols.py:394`) with the same fields (role, name, automation_id, bounds, enabled, parent_index) as the Windows `UIATreeSource` (`uia_tree.py:45`).
- pyobjc frameworks (`Quartz`/`ApplicationServices`/`HIServices`) are lazy-imported INSIDE `observe`, never at module scope. Frontmost app via `NSWorkspace.frontmostApplication().processIdentifier()`; walk via `AXUIElementCopyAttributeValue` (kAXRole/kAXTitle/kAXValue/kAXPosition/kAXSize/kAXEnabled/kAXChildren).
- Roles run through `normalize_role(..., "darwin")` (2.3) while flattening; reuse the existing `_DEPTH_RETRY_LADDER (6,5,4)` (`uia_tree.py:42`) + `prune_tree` (`pruning.py`). AX `{x,y}`+`{w,h}` → `(x,y,w,h)` bounds.
- Permission gate (AD-13): before walking, check `AXIsProcessTrusted()`. If False, log the English onboarding message ("macOS Accessibility permission not granted — grant it in System Settings › Privacy & Security › Accessibility … falling back to pixel clicks") and return an `Observation` with empty `nodes` + `source="screenshot_only"`. Never raise.
- `tests/unit/vision/test_ax_tree.py` drives `AXTreeSource` against `tests/fakes/fake_ax_api.py` (canned AX tree); asserts the flattened nodes, role normalization, and the empty-nodes degrade when the fake reports `AXIsProcessTrusted()==False`. Permission-dependent tests are marked `skip_ci`.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-2-gui-permission.md` §2.1 (the full spec + line refs).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-10 + AD-13 (detect-and-degrade) + AD-3 (live sign-off deferred to Wave 4).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-4 (never raise; degrade), HN-5 ("Never silently return an empty result to hide a fixable misconfiguration … probe → if missing, log an onboarding message AND fall back"), HN-6 (no "works" claim without live sign-off), HN-7 (no module-scope pyobjc).
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-2 (empty AX tree hides ungranted permission — read the full counter-pattern), AP-9 (no real display in CI), AP-12 (ship a fake), AP-13 (no convenience module-scope import).
- Read `jarvis/vision/uia_tree.py:42-45`, `protocols.py:394/402/419`, and the empty-tree contract at `jarvis/harness/screenshot_only_loop.py:1078-1095`.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. Same worktree as 2.3 (Worktree D — UI-element-click): `../sub-agents-outputs/crossplat-w2-uiclick` on `crossplat/w2-ui-click`.
3. `pip install -e ".[dev]"` (do NOT install `[desktop-macos]` — the import gate proves the base install imports clean).
</environment-setup>

<scope>
Create: `jarvis/vision/ax_tree.py`, `tests/fakes/fake_ax_api.py`, `tests/unit/vision/test_ax_tree.py`.
Depends on 2.3's `role_map.py`. Do NOT edit `uia_tree.py` (Windows, untouched — AD-7).
</scope>

<primary-path>
1. Define `AXTreeSource` against `VisionSource`; lazy-import pyobjc inside `observe`.
2. Implement the `AXIsProcessTrusted()` gate FIRST → on False, log + return empty `Observation(source="screenshot_only")`.
3. Walk the AX tree depth-first, flatten to `UIANode` with `parent_index`, normalize roles via 2.3, convert bounds, reuse the depth ladder + `prune_tree`.
4. Write `fake_ax_api.py` (a fake exposing the AX attribute calls + a togglable `AXIsProcessTrusted`) and the tests; mark permission/real-tree tests `skip_ci`.
</primary-path>

<fallback-paths>
- If a pyobjc attribute call returns a CFType that needs bridging, normalize at the seam (extract Python primitives) and pin with the fake. Failure condition: a node carries a non-serializable value.
- If the frontmost-app PID call is unavailable in the fake, parametrize the source with an injectable "app provider" for testing. Failure condition: the test needs a real NSWorkspace.
- You may invent the tree-walk strategy if the prescribed attribute set is insufficient, provided roles are normalized (AP-8), the permission degrade fires (AP-2/HN-5), and no module-scope pyobjc import exists (HN-7).
</fallback-paths>

<acceptance>
pytest tests/unit/vision/test_ax_tree.py -v
python -c "from jarvis.vision.ax_tree import AXTreeSource; from jarvis.core.protocols import VisionSource; assert isinstance(AXTreeSource(), VisionSource)"
python -c "import ast; m=ast.parse(open('jarvis/vision/ax_tree.py').read()); assert not any(getattr(n,'names',None) and any(a.name in ('Quartz','HIServices','ApplicationServices') for a in n.names) for n in ast.walk(m) if isinstance(n,ast.Import))"
ruff check jarvis/vision/ax_tree.py && mypy jarvis/vision/ax_tree.py
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-5 / AP-2 — distinguish empty from forbidden; on un-granted permission log ONE English onboarding line + fall back to pixel; never silently empty, never hard-block.
- HN-4 — `observe` never raises.
- HN-6 — no "macOS AX works" claim; that is Wave 4's live sign-off.
- HN-7 / AP-13 — no module-scope pyobjc import.
- AP-8 / AD-10 — roles normalized to canonical UIA via 2.3.
SOFT:
- Reuse the depth ladder + `prune_tree` rather than re-implementing pruning.
</hard-rules>

<done-signal>
Final report (5 bullets):
- `AXTreeSource` Protocol conformance + the UIANode field parity.
- The `AXIsProcessTrusted()` gate + the degrade Observation.
- Role normalization via 2.3 + reused pruning.
- Hard-rules honored (HN-4/HN-5/HN-6/HN-7 quoted).
- Path-taken + which tests are skip_ci.
</done-signal>
```

## 2.2

```prompt
ultrathink
<role>
You add the Linux AT-SPI `VisionSource` (`pyatspi`) so UI-element-click works on Linux. Your outcome is an `AtspiTreeSource` mirroring 2.1's contract, with `pyatspi` lazy-imported and distro-only (never pip), the AT-SPI bus probed for reachability, and a logged degrade to empty nodes when the bus is unavailable — never a crash.
</role>

<outcome>
DONE means:
- `jarvis/vision/atspi_tree.py` defines `AtspiTreeSource` satisfying `VisionSource` (`name="atspi-tree"`, `kind="ui_tree"`), producing the same `Observation`/`UIANode` layout as 2.1, with roles normalized via `normalize_role(..., "linux")` (2.3) + reused `prune_tree`/`_DEPTH_RETRY_LADDER`.
- `pyatspi` is lazy-imported inside `observe` (AD-14: not on PyPI, distro-packaged `apt install python3-pyatspi gir1.2-atspi-2.0`); its absence is a logged degrade, not a crash.
- Walk: `pyatspi.Registry.getDesktop(0)` → active/focused app → `Accessible` children → `getRole()`/`name`/`getState()` (enabled) + `Component.getExtents(pyatspi.DESKTOP_COORDS)` for bounds; flatten to `UIANode` with `parent_index`.
- AT-SPI bus gate (AD-13): probe `capabilities.has_ax_tree` + a cheap `getDesktop(0)` reachability check; on failure log "Linux AT-SPI accessibility bus unavailable — install python3-pyatspi + gir1.2-atspi-2.0 and ensure the AT-SPI bus is running; falling back to pixel clicks" and return empty `nodes`. Never raise.
- `tests/unit/vision/test_atspi_tree.py` drives against `tests/fakes/fake_atspi.py`; asserts the flattened tree, role normalization, and the empty-nodes degrade when the bus is reported unreachable.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-2-gui-permission.md` §2.2.
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-10 + AD-13 + AD-14 (pyatspi distro-only).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-4 (never raise), HN-5 (detect-and-degrade, never silently empty), HN-7 (no module-scope pyatspi), HN-9 ("Never put pyatspi in a pip extra. It is GObject-Introspection, distro-packaged only").
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-3 (pyatspi is not pip-installable — read the full counter-pattern), AP-8 (role drift), AP-9 (no real display in CI), AP-12 (ship a fake).
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. Same worktree as 2.1/2.3 (Worktree D): `../sub-agents-outputs/crossplat-w2-uiclick` on `crossplat/w2-ui-click`.
3. `pip install -e ".[dev]"` (do NOT pip-install pyatspi — it is not on PyPI).
</environment-setup>

<scope>
Create: `jarvis/vision/atspi_tree.py`, `tests/fakes/fake_atspi.py`, `tests/unit/vision/test_atspi_tree.py`.
Depends on 2.3's `role_map.py`. Do NOT add pyatspi to `pyproject.toml` (HN-9). Do NOT edit `uia_tree.py`.
</scope>

<primary-path>
1. Define `AtspiTreeSource` against `VisionSource`; lazy-import pyatspi inside `observe`.
2. Implement the bus reachability gate FIRST (has_ax_tree + getDesktop(0)) → on failure log + return empty `Observation`.
3. Walk the AT-SPI tree, flatten to `UIANode`, normalize roles via 2.3, read extents for bounds.
4. Write `fake_atspi.py` (fake Registry/Accessible/Component with a togglable bus-reachable flag) + the tests.
</primary-path>

<fallback-paths>
- If `getExtents(DESKTOP_COORDS)` is unavailable on a node, fall back to `(0,0,0,0)` bounds and keep the node (still nameable) rather than dropping it. Failure condition: a clickable node is lost.
- If the focused-app lookup differs across AT-SPI versions, walk from the desktop root and filter to the active frame; pin with the fake. Failure condition: the walk needs a live bus.
- You may invent the reachability probe shape if `getDesktop(0)` is insufficient, provided the degrade fires (HN-5) and no module-scope pyatspi import exists (HN-7/HN-9).
</fallback-paths>

<acceptance>
pytest tests/unit/vision/test_atspi_tree.py -v
python -c "from jarvis.vision.atspi_tree import AtspiTreeSource; from jarvis.core.protocols import VisionSource; assert isinstance(AtspiTreeSource(), VisionSource)"
python -c "import ast; m=ast.parse(open('jarvis/vision/atspi_tree.py').read()); assert not any(getattr(n,'names',None) and any(a.name=='pyatspi' for a in n.names) for n in ast.walk(m) if isinstance(n,ast.Import))"
python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); ext=d['project'].get('optional-dependencies',{}); assert all('pyatspi' not in x for g in ext.values() for x in g)"
ruff check jarvis/vision/atspi_tree.py && mypy jarvis/vision/atspi_tree.py
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-9 / AP-3 — `pyatspi` is NEVER in pyproject; lazy-import + capability probe only.
- HN-5 — bus-unavailable → log onboarding + empty nodes (pixel fallback); never silently empty.
- HN-4 — `observe` never raises.
- HN-7 — no module-scope pyatspi import.
- AP-8 — roles normalized to canonical UIA via 2.3.
SOFT:
- Reuse the depth ladder + `prune_tree`.
</hard-rules>

<done-signal>
Final report (5 bullets):
- `AtspiTreeSource` conformance + UIANode parity.
- The bus reachability gate + the degrade Observation.
- pyatspi NOT in pyproject (assertion output) + lazy import confirmed.
- Hard-rules honored (HN-9/HN-5/HN-4/HN-7 quoted).
- Path-taken + which tests are skip_ci.
</done-signal>
```

## 2.4

```prompt
ultrathink
<role>
You build the `tree_factory` and rewire the 6 hardcoded `UIATreeSource()` literals to it, so every UI-element-click consumer selects the right per-OS VisionSource. Your outcome is a single factory that returns the Windows/macOS/Linux source (or a null source that degrades to the pixel path), wired into all 6 call sites mechanically — the DI seams already exist.
</role>

<outcome>
DONE means:
- `jarvis/vision/tree_factory.py` defines `make_ui_tree_source() -> VisionSource` selecting on `detect_platform()` + `capabilities`: win32→`UIATreeSource()` (unchanged, AD-7); darwin→`AXTreeSource()`; linux→`AtspiTreeSource()` if `capabilities.has_ax_tree` else a NULL source whose `observe` returns an empty `Observation(source="screenshot_only", nodes=())` and logs once.
- The literal `UIATreeSource()` is replaced by `make_ui_tree_source()` at all 6 sites: `click_element.py:125`, `read_visible_ui_state.py:60`, `wait_for_element.py:97`, `wait_for_ui_state.py:78`, `engine.py:71` (the `uia_source or UIATreeSource()` default), `screenshot_only_loop.py:1092` (inside `_foreground_clickable_labels`). The lazy import guard at `click_element.py:117-123` becomes a `from jarvis.vision.tree_factory import make_ui_tree_source` import.
- `screenshot_only_loop.py`'s "returns [] on any failure" contract (`:1093`) is intact (the null source's empty nodes already yield []).
- `tests/unit/vision/test_tree_factory.py` green; `tests/unit/vision/test_engine.py` + `tests/contract/test_vision_source_protocol.py` green (add `AXTreeSource`/`AtspiTreeSource` to the parametrized contract suite at `test_vision_source_protocol.py:26`).
- `grep -rn "UIATreeSource()" jarvis/` shows ONLY `tree_factory.py` (Windows branch) and `uia_tree.py` — none of the 6 former call sites.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-2-gui-permission.md` §2.4 (the 6 exact call sites).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-5 (factory reads the shared capabilities) + AD-6 (null fallback) + AD-7 (Windows untouched).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-3 (read capability from the shared module, no inline drift), HN-4 (never raise), HN-5 (degrade, never silently empty).
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-11 (re-detecting sys.platform per call-site) + AP-12 (ship a fake).
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. Same worktree as 2.1/2.2/2.3 (Worktree D): `../sub-agents-outputs/crossplat-w2-uiclick` on `crossplat/w2-ui-click`.
3. `pip install -e ".[dev]"`
</environment-setup>

<scope>
Create: `jarvis/vision/tree_factory.py`, `tests/unit/vision/test_tree_factory.py`.
Modify (replace the literal with the factory call): `jarvis/plugins/tool/click_element.py`, `jarvis/plugins/tool/read_visible_ui_state.py`, `jarvis/plugins/tool/wait_for_element.py`, `jarvis/plugins/tool/wait_for_ui_state.py`, `jarvis/vision/engine.py`, `jarvis/harness/screenshot_only_loop.py`. Extend `tests/contract/test_vision_source_protocol.py` parametrization.
</scope>

<primary-path>
1. Write `make_ui_tree_source()` + the null source.
2. Mechanically swap the literal at the 6 sites (preserve each `self._vision_source or <call>` / `uia_source=` DI seam).
3. Replace the `click_element.py` lazy `UIATreeSource` import with the factory import.
4. Add `AXTreeSource`/`AtspiTreeSource` to the contract suite parametrization; write `test_tree_factory.py` (per-platform selection + the Linux null-source degrade).
5. Run the grep to confirm zero stray literals.
</primary-path>

<fallback-paths>
- If a call site constructs `UIATreeSource(...)` WITH arguments, thread those args through `make_ui_tree_source(**kwargs)` rather than dropping them. Failure condition: a site loses configuration.
- If adding the two new sources to the contract suite trips a real-API requirement, pass them a fake/null-constructed instance for the structural conformance check only. Failure condition: the contract suite needs a real AX/AT-SPI bus.
- You may invent the null-source shape if the prescribed one fights a consumer, provided the [] contract holds (HN-5) and platform is read from the shared module (AP-11).
</fallback-paths>

<acceptance>
pytest tests/unit/vision/test_tree_factory.py tests/unit/vision/test_engine.py tests/contract/test_vision_source_protocol.py -v
python -c "from jarvis.vision.tree_factory import make_ui_tree_source; from jarvis.core.protocols import VisionSource; assert isinstance(make_ui_tree_source(), VisionSource)"
grep -rn "UIATreeSource()" jarvis/
ruff check jarvis/vision/ jarvis/plugins/tool/ jarvis/harness/screenshot_only_loop.py && mypy jarvis/vision/tree_factory.py
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-3 / AP-11 — the factory reads the shared capabilities once; no consumer re-detects sys.platform.
- HN-4 — `make_ui_tree_source()` never raises.
- HN-5 — the Linux null source degrades to [] with a one-time log; the pixel path takes over.
- AD-7 — the Windows `UIATreeSource()` branch is unchanged.
SOFT:
- Keep each call-site edit a one-line swap; preserve the existing DI seam.
</hard-rules>

<done-signal>
Final report (5 bullets):
- The factory + null source + per-OS selection.
- The 6 call-site swaps + the grep proving zero strays.
- Contract suite extended with the two new sources; engine suite green.
- Hard-rules honored (HN-3/HN-4/HN-5 quoted).
- Path-taken.
</done-signal>
```

## 2.5

```prompt
ultrathink
<role>
You build the `OverlaySurface` seam and wrap the LIVE Tk orb as `TkColorKeyOverlay` (the Windows + macOS default, since `-transparentcolor` works on both). Your outcome is a thin lifecycle wrapper around `OrbOverlay` — the rendering is NOT rewritten — plus a factory that selects the right surface per OS. The Wave-0 ADR fixed the Tk orb as the wrap target; the PySide6 tree must not be re-imported.
</role>

<outcome>
DONE means:
- `jarvis/overlay/surface.py` defines `OverlaySurface` Protocol (`start()`, `stop()`, `set_state(state)`, `is_visible()->bool`) + `make_overlay_surface()` factory + `TkColorKeyOverlay`.
- `TkColorKeyOverlay` WRAPS `OrbOverlay` (`ui/orb/overlay.py:1236`, `-transparentcolor` at ~`:966`/`:1331`) — no change to the color-key rendering; the wrapper only adapts the lifecycle to the Protocol.
- `make_overlay_surface()` selects on `detect_platform()` + `capabilities`: win32/darwin → `TkColorKeyOverlay` (when `capabilities.has_overlay`); linux → `LinuxBestEffortOverlay`/`TrayOnlySurface` (2.6); `not has_overlay` → `TrayOnlySurface`.
- The Windows-only `SetSystemCursor` swap (`jarvis/overlay/system_cursor.py`) is left Windows-only at its existing call site (`jarvis/ui/desktop_app.py`); it is NOT wired into the cross-platform surface (AD-11 / HN-18).
- `tests/overlay/test_overlay_surface.py` green under `QT_QPA_PLATFORM=offscreen`; constructs `TkColorKeyOverlay` against `tests/fakes/fake_overlay_surface.py` and asserts lifecycle + factory selection (no real window).
- `python -c "import ui.orb.overlay"` still resolves (live orb import unbroken).
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-2-gui-permission.md` §2.5.
- `docs/plans/cross-platform-mac-linux/ADR-orb-framework.md` (Wave-0 verdict: wrap Tk, do not re-import PySide6).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-11 (the 3-tier ladder; SetSystemCursor stays Windows-only) + AD-7.
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-1 (wrap, never rewrite the live orb), HN-4 (factory never raises), HN-6 (no transparency "works" claim without live sign-off), HN-18 (SetSystemCursor stays a Windows-only no-op off Windows).
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-5 (`-transparentcolor` raises TclError on X11 — relevant to the factory NOT selecting Tk on Linux), AP-9 (no real display in CI), AP-12 (ship a fake).
- Read `ui/orb/overlay.py` around the cited lines + `tests/overlay/conftest.py:21` (the offscreen guard, kept by Wave-0 0.7).
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. `git worktree add -b crossplat/w2-orb ../sub-agents-outputs/crossplat-w2-orb feat/crossplatform-port`
3. `pip install -e ".[dev]"`
4. `python -c "import jarvis; print(jarvis.__file__)"`
</environment-setup>

<scope>
Create: `jarvis/overlay/surface.py`, `tests/fakes/fake_overlay_surface.py`, `tests/overlay/test_overlay_surface.py`.
Modify: none of `ui/orb/overlay.py`'s rendering (wrap only). This worktree (E) continues into 2.6 + the `desktop-macos` extra in 2.7.
</scope>

<primary-path>
1. Define the `OverlaySurface` Protocol + `make_overlay_surface()`.
2. Write `TkColorKeyOverlay` adapting `OrbOverlay`'s lifecycle to the Protocol (start/stop/set_state/is_visible) — no rendering changes.
3. Implement the factory selection (linux branch delegates to 2.6's surfaces).
4. Write `fake_overlay_surface.py` + the offscreen test asserting lifecycle + selection (Tk on win32/darwin, never on linux).
</primary-path>

<fallback-paths>
- If `OrbOverlay`'s constructor needs a Tk root that can't be built headless, gate construction so the FACTORY selection + Protocol conformance are testable without instantiating a real window (construct lazily in `start()`). Failure condition: importing the module needs a display.
- If `set_state` maps imperfectly to `OrbOverlay`'s state API, add a small adapter mapping table and pin it with the fake. Failure condition: a state has no orb representation.
- You may invent the lifecycle adapter shape if the prescribed Protocol fights `OrbOverlay`, provided the rendering is untouched (HN-1) and the factory never selects Tk on Linux (AP-5).
</fallback-paths>

<acceptance>
QT_QPA_PLATFORM=offscreen pytest tests/overlay/test_overlay_surface.py -v
python -c "from jarvis.overlay.surface import make_overlay_surface, OverlaySurface; assert isinstance(make_overlay_surface(), OverlaySurface)"
python -c "import ui.orb.overlay; print('live orb import ok')"
ruff check jarvis/overlay/ && mypy jarvis/overlay/surface.py
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-1 / AD-7 — wrap `OrbOverlay`; do not rewrite the color-key rendering.
- HN-4 / AP-5 — the factory degrades, never raises; Tk is selected ONLY on win32/darwin (never X11 Linux).
- HN-6 — no transparency "works" claim; Wave 4 owns the live sign-off.
- HN-18 — `SetSystemCursor` stays a Windows-only no-op; do not wire it cross-platform.
SOFT:
- Keep the wrapper thin; lazy-construct the Tk root in `start()`.
</hard-rules>

<done-signal>
Final report (5 bullets):
- The `OverlaySurface` Protocol + `TkColorKeyOverlay` wrap (no rendering change).
- Factory selection (Tk on win/mac only).
- Offscreen test + live orb import intact.
- Hard-rules honored (HN-1/HN-4/HN-6/HN-18 quoted).
- Path-taken + how headless construction was handled.
</done-signal>
```

## 2.6

```prompt
ultrathink
<role>
You build the Linux orb tiers: a best-effort transparent surface on a compositor and the universal `TrayOnlySurface` floor driving the already-cross-platform pystray tray. Your outcome is graceful orb presence on every Linux session — transparency where the compositor allows it, a state-colored tray icon everywhere else — never an opaque magenta box, never a crash.
</role>

<outcome>
DONE means:
- `jarvis/overlay/linux_surface.py` defines `LinuxBestEffortOverlay`: attempts the Tk `-transparentcolor` path on a Linux compositor; on a non-compositing/Wayland session (detect via `capabilities.is_wayland` or a failed `wm_attributes` probe / `TclError`) it FALLS THROUGH to `TrayOnlySurface` with a logged English message. Never shows an opaque magenta box.
- `jarvis/overlay/tray_surface.py` defines `TrayOnlySurface`: drives the existing cross-platform pystray tray (`jarvis/ui/tray.py`, no platform marker, renders `JarvisState` icons via PIL at `tray.py:39`); `set_state(state)` maps the orb state onto `JarvisState` (`tray.py:20-26`) so IDLE/LISTENING/THINKING/SPEAKING feedback shows in the tray color (`_STATE_COLORS` at `tray.py:29`).
- `make_overlay_surface()` (2.5) returns `LinuxBestEffortOverlay` when `display_present and not is_wayland`, else `TrayOnlySurface`.
- `tests/overlay/test_tray_surface.py` green (TrayOnlySurface maps orb states onto `JarvisState` via a fake; no real tray thread). On a headless leg (no DISPLAY) `make_overlay_surface()` returns `TrayOnlySurface` and start/stop are no-op-safe.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-2-gui-permission.md` §2.6.
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-11 (the ladder + the tray floor) + AD-6.
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-4 (never raise; degrade), HN-1 (do not rewrite the tray; drive it), HN-18 (SetSystemCursor stays Windows-only).
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-5 (`-transparentcolor` TclError on X11 → log + fall to tray, never propagate — read the full counter-pattern), AP-9 (no real display in CI).
- Read `jarvis/ui/tray.py:20-39` (the `JarvisState` enum + `_STATE_COLORS` + PIL icon render).
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. Same worktree as 2.5 (Worktree E — Orb): `../sub-agents-outputs/crossplat-w2-orb` on `crossplat/w2-orb`.
3. `pip install -e ".[dev]"`
</environment-setup>

<scope>
Create: `jarvis/overlay/linux_surface.py`, `jarvis/overlay/tray_surface.py`, `tests/overlay/test_tray_surface.py`.
Modify: `jarvis/overlay/surface.py` (wire the linux branch of `make_overlay_surface()` — coordinate with 2.5 in the same worktree). Do NOT modify `jarvis/ui/tray.py` (drive it, don't rewrite — HN-1).
</scope>

<primary-path>
1. Write `TrayOnlySurface` first (the floor): map orb state→`JarvisState`, drive pystray; start/stop no-op-safe headless.
2. Write `LinuxBestEffortOverlay`: try the Tk color-key path wrapped in try/except TclError → on failure or Wayland, log + delegate to `TrayOnlySurface`.
3. Wire the linux branch in `make_overlay_surface()`.
4. Write `test_tray_surface.py` (state mapping via a fake tray) + a headless-selection assertion.
</primary-path>

<fallback-paths>
- If `wm_attributes("-transparentcolor", …)` neither raises nor keys out on a given compositor (silent opaque), add a post-set verification probe; on ambiguity, prefer the tray floor over a possibly-opaque box (AP-5). Failure condition: an opaque magenta box ever shows.
- If pystray's icon update API differs from the assumed one, adapt at the `set_state` seam and pin with the fake. Failure condition: state changes don't reflect in the tray color.
- You may invent the compositor-detection heuristic if `is_wayland` + the wm_attributes probe are insufficient, provided it always degrades to the tray (HN-4) and never propagates a TclError (AP-5).
</fallback-paths>

<acceptance>
pytest tests/overlay/test_tray_surface.py -v
python -c "from jarvis.overlay.surface import make_overlay_surface; print(type(make_overlay_surface()).__name__)"
ruff check jarvis/overlay/ && mypy jarvis/overlay/
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-4 / AP-5 — `-transparentcolor` TclError on X11 → log + fall to the tray; never propagate; never an opaque magenta box.
- HN-1 — drive `jarvis/ui/tray.py`; do not rewrite it.
- HN-18 — no SetSystemCursor wiring cross-platform.
SOFT:
- The tray is the universal floor — guarantee SOME presence everywhere.
</hard-rules>

<done-signal>
Final report (5 bullets):
- `TrayOnlySurface` state→JarvisState mapping + `LinuxBestEffortOverlay` fall-through.
- The linux branch wired into the factory.
- Headless selection (TrayOnlySurface) + no-op-safe start/stop.
- Hard-rules honored (HN-4/HN-1/HN-18 quoted).
- Path-taken + the compositor-detection heuristic used.
</done-signal>
```

## 2.7

```prompt
ultrathink
<role>
You add the new `[desktop-macos]` pip extra for the pyobjc frameworks and record the pyatspi distro-prerequisite note. Your outcome is a marked, darwin-only extras group that keeps the base install clean, plus a one-line README note that pyatspi is apt-only (never pip).
</role>

<outcome>
DONE means:
- `pyproject.toml` gains a `desktop-macos` group next to `desktop`: `pyobjc-framework-Quartz>=10; sys_platform == 'darwin'`, `pyobjc-framework-ApplicationServices>=10; sys_platform == 'darwin'`, `pyobjc-framework-Accessibility>=10; sys_platform == 'darwin'`.
- `pyatspi` is added NOWHERE in pyproject (distro-only — AD-14/HN-9); the README capability section carries a one-line note: `apt install python3-pyatspi gir1.2-atspi-2.0` is a Linux system prerequisite gated by `capabilities.has_ax_tree`.
- The macOS/Linux CI legs install ONLY `.[dev]` (not `[desktop-macos]`, not distro pyatspi) and stay green — the base import gate covers `ax_tree`/`atspi_tree`.
- All acceptance commands pass.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-2-gui-permission.md` §2.7.
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-14.
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-8 (extras only; never base deps), HN-9 (pyatspi distro-only, never a pip extra), HN-10 (the sys_platform marker pattern).
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-3 (pyatspi not pip-installable).
- Mirror `pyproject.toml:99-110`.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. Owned by Worktree E (Orb): `../sub-agents-outputs/crossplat-w2-orb` on `crossplat/w2-orb` (the `desktop-macos` block). Worktree D adds ONLY the README pyatspi note (no pyproject edit) to avoid a conflict.
3. `pip install -e ".[dev]"`
</environment-setup>

<scope>
Modify: `pyproject.toml` (add `desktop-macos`). Add the pyatspi prerequisite note to the cross-platform plan README capability area (the actual README capability-matrix update is Wave 4's 4.3 — here just the one-line dependency note so a future agent does not pip-install pyatspi).
</scope>

<primary-path>
1. Add the `desktop-macos` group mirroring the marker style.
2. Add the one-line pyatspi-is-apt-only note.
3. Verify tomllib parses + the markers are correct.
</primary-path>

<fallback-paths>
- If a `desktop-macos` group already exists (partial prior edit), reconcile to the three frameworks with darwin markers. Failure condition: tomllib parse fails.
- You may bump the pyobjc minimums if v10 is unavailable, provided the darwin marker stays.
</fallback-paths>

<acceptance>
python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); g=d['project']['optional-dependencies']['desktop-macos']; assert all('darwin' in x for x in g) and any('Accessibility' in x for x in g)"
python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); ext=d['project']['optional-dependencies']; assert all('pyatspi' not in x for g in ext.values() for x in g)"
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-8 — extras only.
- HN-9 / AP-3 — pyatspi never in pyproject.
- HN-10 — pyobjc carries `sys_platform == 'darwin'`.
SOFT:
- Keep the README note one line; the full matrix is Wave 4.
</hard-rules>

<done-signal>
Final report (5 bullets):
- The `desktop-macos` group + darwin markers (assertion output).
- pyatspi absent from pyproject + the apt prerequisite note added.
- CI legs still install only `.[dev]`.
- Hard-rules honored (HN-8/HN-9/HN-10 quoted).
- Path-taken.
</done-signal>
```

## check-w2

```prompt
ultrathink
<role>
You are a read-only phase auditor for Wave 2. Your outcome is a PASS/FAIL verdict on whether UI-element-click (AX/AT-SPI behind the VisionSource seam) and the Orb (OverlaySurface ladder) meet EK-2/EK-3 and are safe to merge — and a reminder that the GUI/permission behavior is deferred to Wave 4's live sign-off (EK-5, not provable here). You verify; you do not modify.
</role>

<outcome>
DONE means a verdict report stating, with command evidence, whether:
- Role normalization: every produced AX/AT-SPI role ∈ the canonical UIA set (AP-8 parity).
- `AXTreeSource` + `AtspiTreeSource` satisfy `VisionSource`, lazy-import their native libs, and degrade to empty nodes on missing permission/bus (HN-5).
- `make_ui_tree_source()` selects per OS; the 6 former `UIATreeSource()` literals are gone; the contract + engine suites are green.
- `make_overlay_surface()` selects per OS (Tk only on win/mac), the Linux tray floor works headless, and the live Tk orb import is unbroken.
- pyatspi is NOT in pyproject; the macOS pyobjc extra is darwin-marked; the base `.[dev]` import gate is still clean.
- The CI matrix is green; all permission/transparency tests are `skip_ci` (no real display in CI — AP-9), deferred to Wave 4.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-2-gui-permission.md` (EK acceptance gate).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` EK-2/EK-3/EK-5 + AD-3.
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` HN-5/HN-6/HN-9/HN-15.
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-2/AP-3/AP-5/AP-8/AP-9.
</required-reading>

<environment-setup>
1. `git switch feat/crossplatform-port` (with Wave-2 branches merged, or check each branch).
2. `pip install -e ".[dev]"` (do NOT install the macOS extra — prove the base install is clean).
Read-only.
</environment-setup>

<scope>
Modify: NOTHING. Grep for stray `UIATreeSource()`, module-scope native imports, and `pyatspi` in pyproject.
</scope>

<primary-path>
1. Run each acceptance command; capture output.
2. Confirm CI green + the permission/transparency tests are skip_ci (not provable headless).
3. Render the verdict, explicitly flagging that GUI/permission proof is Wave 4's job (HN-6).
</primary-path>

<fallback-paths>
- If a sub-task branch is not yet merged, audit it on its branch and note integration state.
</fallback-paths>

<acceptance>
pytest tests/unit/vision/test_role_map.py tests/unit/vision/test_ax_tree.py tests/unit/vision/test_atspi_tree.py tests/unit/vision/test_tree_factory.py -v
pytest tests/unit/vision/test_engine.py tests/contract/test_vision_source_protocol.py -v
QT_QPA_PLATFORM=offscreen pytest tests/overlay/test_overlay_surface.py tests/overlay/test_tray_surface.py -v
grep -rn "UIATreeSource()" jarvis/
python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); ext=d['project']['optional-dependencies']; assert all('pyatspi' not in x for g in ext.values() for x in g); print('no pyatspi in pyproject')"
python scripts/ci/check_import_clean.py
gh run list --workflow=ci.yml --branch feat/crossplatform-port --limit 1
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-6 — do not declare AX/Orb GUI behavior "verified"; that is Wave 4's live sign-off.
- HN-15 — do not declare mergeable unless the matrix is green.
SOFT:
- Be explicit about Wave-4-deferred items.
</hard-rules>

<done-signal>
Final report (5 bullets):
- Role parity + the two VisionSources (conformance + degrade): PASS/FAIL.
- tree_factory + 6 swaps + contract suite: PASS/FAIL.
- Overlay factory + tray floor + live orb import: PASS/FAIL.
- pyatspi-absent + import gate + CI green: PASS/FAIL.
- Overall verdict + the Wave-4-deferred GUI/permission items + path-taken.
</done-signal>
```

## merge-w2

```prompt
ultrathink
<role>
You mechanically merge Wave 2's two branches onto the integration branch, run the wave acceptance, and push only if green. Step-prescriptive.
</role>

<outcome>
DONE means: `crossplat/w2-ui-click` and `crossplat/w2-orb` merge `--no-ff` onto `feat/crossplatform-port` (order does not matter — disjoint files except the split pyproject/README note); the wave acceptance passes; the CI matrix is green; the branch is pushed. Any conflict or red CI → STOP and hand off to the matching recovery prompt.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-2-gui-permission.md` §Parallelism (the only shared file is pyproject 2.7 — E owns the desktop-macos block; D adds only the README note).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` HN-9, HN-15, HN-16, HN-17.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. `git switch feat/crossplatform-port`
3. `git fetch origin && git pull --ff-only origin feat/crossplatform-port`
4. `pip install -e ".[dev]"`
</environment-setup>

<scope>
Branches: `crossplat/w2-ui-click`, `crossplat/w2-orb`. Merge commits + conflict resolutions only.
</scope>

<primary-path>
1. `git merge --no-ff crossplat/w2-orb` (carries the desktop-macos extra) → reinstall → run acceptance.
2. `git merge --no-ff crossplat/w2-ui-click` → run acceptance.
3. Full wave acceptance; if green, `git push origin feat/crossplatform-port`; watch CI.
</primary-path>

<fallback-paths>
- Conflict on `pyproject.toml`/README (if both touched it) → STOP, hand off to `recovery-merge-conflict`.
- Red CI after push → STOP, hand off to `recovery-red-ci`.
- Never force-push (HN-17); never resolve a conflict by re-adding pyatspi to pyproject (HN-9).
</fallback-paths>

<acceptance>
python scripts/ci/check_import_clean.py
pytest -m "not skip_ci" --junitxml=report.xml -q; python scripts/ci/assert_min_passed.py report.xml
QT_QPA_PLATFORM=offscreen pytest tests/overlay/ -q
pytest tests/unit/vision/ tests/contract/test_vision_source_protocol.py -q
python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); ext=d['project']['optional-dependencies']; assert all('pyatspi' not in x for g in ext.values() for x in g)"
ruff check jarvis/ && mypy jarvis/vision/tree_factory.py jarvis/overlay/surface.py
gh run list --workflow=ci.yml --branch feat/crossplatform-port --limit 1
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-15 — push only if green.
- HN-9 — pyatspi never re-added to pyproject during conflict resolution.
- HN-17 — no force-push, no `--no-verify`.
SOFT:
- `--no-ff` per unit.
</hard-rules>

<done-signal>
Final report (5 bullets):
- The two merges + hashes.
- Wave acceptance result.
- CI matrix green status (run URL).
- Hard-rules honored (HN-15/HN-9/HN-17 quoted).
- Path-taken / any recovery handoff.
</done-signal>
```

---

# Wave 3 — Admin / elevation (security-sensitive)

> Worktree split (per `WELLE-3-admin.md` §Parallelism): **F=Transport+elevation** (3.1→3.2→3.4→3.6, the security core; 3.1 lands first, then 3.2/3.4 in parallel sub-branches, 3.6 wires last), **G=Op-vocabulary+ADR** (3.3+3.5, independent of the transport seam). Every PR here carries a `requesting-code-review` pass focused on no-`shell=True` / pattern-validated-argv / peer-cred invariants. Depends on Wave 0 only (the platform factory + `has_elevation` probe + the green matrix + the import gate). Independent of Waves 1/2. NEVER CI-testable end-to-end (interactive auth) → relies on Wave 4 live sign-off + heavy fake-transport unit tests.

## 3.1

```prompt
ultrathink
<role>
You extract the `AdminTransport` seam from the Windows named-pipe code while leaving the HMAC/envelope/Pydantic-argv security core UNTOUCHED. Your outcome is a transport Protocol with the Windows pipe relocated behind it, the security core reused verbatim, and a factory that selects the right transport per OS. This is the foundation 3.2/3.4/3.6 build on — it must land first.
</role>

<outcome>
DONE means:
- `jarvis/admin/transport.py` defines `AdminTransport` Protocol — server: `async def serve(handler)` where `handler: Callable[[bytes], Awaitable[bytes]]`; client: `async def roundtrip(raw: bytes) -> bytes` — plus `NamedPipeTransport` (Windows) + `make_admin_transport()` factory.
- The transport-specific Windows code is RELOCATED into `NamedPipeTransport`: `AdminPipeServer._accept_one` (`ipc.py:295`), `_handle_connection`/`_read_message`/`_write_message`/`_safe_close` (`:330-424`), `AdminPipeClient._roundtrip` (`:511`), `_build_sddl` (`:123`), `current_user_sid` (`:92`), `default_pipe_name` (`:117`). No behavior change (AD-7); the SDDL-ACL `D:(A;;FA;;;<SID>)` (`:129`) + MESSAGE-mode read still apply.
- The HMAC/envelope core STAYS in `ipc.py` verbatim: `_canonical_args_json` (`:65`), `_compute_hmac` (`:76`), `_decode_request` (`:194`, its 5-step ordering), `_encode_response` (`:258`), the nonce LRU (`:181`), the `_TIMESTAMP_WINDOW_NS`/`_NONCE_LRU_SIZE` constants (`:43-50`).
- `make_admin_transport()`: win32→`NamedPipeTransport`; else→`UnixSocketTransport` (3.2).
- `tests/unit/admin/test_hmac_replay.py` STAYS green (the core is unmoved). `tests/unit/admin/test_transport_seam.py` green (a fake transport round-trips a signed envelope through `_decode_request`/`_encode_response`). `ipc.py` no longer module-scope-imports `win32pipe`/`win32file`/`win32security`/`pywintypes`.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-3-admin.md` §3.1 (the exact relocate-vs-keep split + line refs).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-12 (reuse the security core untouched; only transport + op vocabulary change) + AD-7.
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-1 (relocate, never rewrite the Windows impl), HN-11 ("Never use shell=True and never pass unvalidated argv anywhere on the elevation path"), HN-12 ("Never weaken, bypass, or make optional the HMAC signature or the Pydantic discriminated-union argv validation"), HN-7 (no module-scope Windows import).
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-10 (shelling a privileged command string) + AP-12 (ship a fake).
- Read `jarvis/admin/ipc.py` around all the cited lines to see exactly what is transport vs core.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. `git worktree add -b crossplat/w3-transport ../sub-agents-outputs/crossplat-w3-transport feat/crossplatform-port`
3. `pip install -e ".[dev]"`
4. `python -c "import jarvis; print(jarvis.__file__)"`
5. Invoke the `requesting-code-review` discipline before merge (security-sensitive wave).
</environment-setup>

<scope>
Create: `jarvis/admin/transport.py`, `tests/fakes/fake_admin_transport.py`, `tests/unit/admin/test_transport_seam.py`.
Modify: `jarvis/admin/ipc.py` (move ONLY the transport-specific Windows code; leave the HMAC core in place). This worktree (F) continues into 3.2/3.4/3.6.
</scope>

<primary-path>
1. Define `AdminTransport` Protocol (server `serve(handler)` + client `roundtrip(raw)`) at the bytes-level seam `_decode_request`/`_encode_response` already operate on.
2. Relocate the listed Windows pipe functions into `NamedPipeTransport` verbatim (lazy-import `win32*` inside the methods).
3. Keep `_build_envelope` (`:495`) in the client; only the byte-transport swaps.
4. Write `make_admin_transport()`.
5. Write `fake_admin_transport.py` + `test_transport_seam.py`; re-run `test_hmac_replay.py` to prove the core is intact.
</primary-path>

<fallback-paths>
- If relocating a pipe helper breaks an import cycle, keep a thin re-export shim in `ipc.py` (a lazy delegating function) rather than leaving the win32 import at module scope. Failure condition: the import gate goes red on `ipc.py`.
- If the handler signature can't be threaded cleanly, define a small `AdminHandler` type alias and pin the seam with the fake. Failure condition: `serve(handler)` can't route through `_decode_request`.
- You may invent the transport abstraction shape if the prescribed one fights the existing flow, provided the HMAC core is byte-for-byte unmoved (HN-12) and `test_hmac_replay.py` stays green.
</fallback-paths>

<acceptance>
pytest tests/unit/admin/test_hmac_replay.py -v
pytest tests/unit/admin/test_transport_seam.py -v
python -c "from jarvis.admin.transport import make_admin_transport, AdminTransport; assert isinstance(make_admin_transport(), AdminTransport)"
python -c "import ast; m=ast.parse(open('jarvis/admin/ipc.py').read()); assert not any(getattr(n,'names',None) and any(a.name in ('win32pipe','win32file','win32security','pywintypes') for a in n.names) for n in ast.walk(m) if isinstance(n,ast.Import))"
ruff check jarvis/admin/ && mypy jarvis/admin/transport.py
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-12 — the HMAC signature + Pydantic argv validation are byte-for-byte unmoved; `test_hmac_replay.py` proves it.
- HN-11 / AP-10 — no `shell=True`, no unvalidated argv anywhere.
- HN-1 / AD-7 — the Windows pipe behavior is relocated verbatim, not rewritten.
- HN-7 — `ipc.py` is import-clean (win32 imports now lazy inside `transport.py`).
SOFT:
- A thin re-export shim is acceptable to avoid cycles, as long as the import gate stays green.
</hard-rules>

<done-signal>
Final report (5 bullets):
- `AdminTransport` Protocol + the relocated Windows functions + the factory.
- Confirmation the HMAC core is unmoved (`test_hmac_replay.py` green).
- `ipc.py` import-clean (AST output).
- Hard-rules honored (HN-12/HN-11/HN-1/HN-7 quoted) + the code-review pass.
- Path-taken + any re-export shim used.
</done-signal>
```

## 3.2

```prompt
ultrathink
<role>
You build the `UnixSocketTransport` — a 0700 AF_UNIX socket with a peer-credential check that is the moral equivalent of the Windows SDDL-ACL pipe. Your outcome is a Unix transport that round-trips signed envelopes through the reused HMAC core, rejects any peer whose UID != the server UID, and constructs even on a headless box (refusal happens at the NullElevator layer, not here).
</role>

<outcome>
DONE means:
- `jarvis/admin/unix_socket.py` defines `UnixSocketTransport` binding an `AF_UNIX`/`SOCK_STREAM` socket at `$XDG_RUNTIME_DIR/jarvis-admin-<uid>.sock` (fallback `0700` dir under `/run/user/<uid>/` or `tempfile.mkdtemp(mode=0o700)`); the socket file is `0600` in a `0700` dir (the FS ACL replacing the SDDL-ACL).
- Peer-credential check on accept: Linux `sock.getsockopt(SOL_SOCKET, SO_PEERCRED, struct.calcsize("3i"))`→(pid,uid,gid); macOS `LOCAL_PEERCRED`/`getpeereid`. Reject any connection whose UID != the server process UID. The HMAC envelope check (`_decode_request`) still runs on top (defense in depth).
- Server side implements `AdminTransport.serve(handler)`: accept → read raw → `await handler(raw)` (the reused `_decode_request`→executor→`_encode_response` chain) → write → close, reusing the accept-loop/per-connection-task structure from `AdminPipeServer.serve_forever` (`ipc.py:268-293`). Client: connect→write→read→close (mirrors `_roundtrip`).
- `tests/unit/admin/test_unix_socket_transport.py` green (socket 0600 in a 0700 dir; mismatched-UID peer rejected via a monkeypatched SO_PEERCRED reader). `tests/integration/test_admin_unix_loopback.py` green on Linux/macOS (real AF_UNIX loopback — runs fine on a runner, NOT skip_ci).
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-3-admin.md` §3.2.
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-12.
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-13 ("Never drop the peer-credential check on the Unix transport. SO_PEERCRED/LOCAL_PEERCRED on a 0700 socket in $XDG_RUNTIME_DIR is the moral equivalent of the Windows SDDL-ACL pipe"), HN-11 (no shell=True / no unvalidated argv), HN-12 (don't weaken HMAC), HN-4 (never raise; degrade).
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-10 (shelling a privileged command string) + AP-12 (ship a fake).
- Read `ipc.py:268-293` (the accept-loop to mirror) + `:456-545` (the client roundtrip shape).
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. Same worktree as 3.1 (Worktree F), on a sub-branch off `crossplat/w3-transport` (3.2 + 3.4 are parallel sub-branches that both depend on 3.1). Suggested: `crossplat/w3-unix-socket` branched from `crossplat/w3-transport` after 3.1 is committed.
3. `pip install -e ".[dev]"`
4. `requesting-code-review` before merge.
</environment-setup>

<scope>
Create: `jarvis/admin/unix_socket.py`, `tests/unit/admin/test_unix_socket_transport.py`, `tests/integration/test_admin_unix_loopback.py`.
Depends on 3.1's `AdminTransport` Protocol. Reuse the HMAC core from `ipc.py` — do not copy it.
</scope>

<primary-path>
1. Implement socket bind with the `$XDG_RUNTIME_DIR` path + 0600/0700 perms + the fallback dir logic.
2. Implement the peer-cred check (Linux SO_PEERCRED, macOS LOCAL_PEERCRED/getpeereid); reject UID mismatch BEFORE reading the envelope.
3. Implement `serve(handler)` mirroring the accept-loop + the client connect/write/read.
4. Write the unit test (perms + UID-mismatch rejection via monkeypatched reader) + the real-loopback integration test.
</primary-path>

<fallback-paths>
- If `SO_PEERCRED` is unavailable (older kernel/platform), fall back to `LOCAL_PEERCRED`/`getpeereid`; if neither exists, REFUSE all connections with a logged message rather than skipping the check (HN-13 — never drop peer-cred). Failure condition: the transport accepts a peer without any cred check.
- If `$XDG_RUNTIME_DIR` is unset, use the `tempfile.mkdtemp(mode=0o700)` fallback and log. Failure condition: the socket lands world-readable.
- You may invent the accept-loop concurrency shape if the prescribed mirror fights asyncio, provided peer-cred + HMAC both run (HN-13/HN-12) and a slow op doesn't block accept.
</fallback-paths>

<acceptance>
pytest tests/unit/admin/test_unix_socket_transport.py -v
pytest tests/integration/test_admin_unix_loopback.py -v
python -c "import socket,struct; print('peercred' , hasattr(socket,'SO_PEERCRED'))"
ruff check jarvis/admin/unix_socket.py && mypy jarvis/admin/unix_socket.py
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-13 — the peer-cred check is mandatory; never accept a peer without it; UID-mismatch is rejected.
- HN-12 — the HMAC envelope check runs ON TOP of peer-cred (defense in depth); never weakened.
- HN-11 / AP-10 — no shell=True; the handler executes validated argv only.
- HN-4 — the transport degrades/refuses, never crashes the process.
SOFT:
- Socket file 0600 in a 0700 dir; mirror the existing accept-loop.
</hard-rules>

<done-signal>
Final report (5 bullets):
- Socket path + perms (0600/0700) + the fallback dir logic.
- The peer-cred check (Linux + macOS branches) + UID-mismatch rejection.
- Real AF_UNIX loopback round-trip through the reused HMAC core.
- Hard-rules honored (HN-13/HN-12/HN-11/HN-4 quoted) + the code-review pass.
- Path-taken.
</done-signal>
```

## 3.3

```prompt
ultrathink
<role>
You define the NEW per-OS admin op vocabulary (macOS brew/launchctl, Linux apt/systemctl/ufw, protected-path) as fresh members of the SAME `AdminOperation` discriminated union, with pattern-validated argv and no free-form shell strings. Your outcome is a Unix op schema that ports the op CONCEPTS (install a package, start a service) without porting the Windows COMMAND STRINGS — `sudo winget` is nonsense.
</role>

<outcome>
DONE means:
- `jarvis/admin/schema_unix.py` defines the macOS + Linux op models, each subclassing `_AdminOpBase` (`schema.py:25`, `frozen=True`, `extra="forbid"`) so they inherit strict validation, with pattern-validated argv (mirror `InstallWingetOp` `package_id` regex `:39`, `_SERVICE_NAME` `:55`, firewall name `:82`):
  - Linux: `AptInstallOp`/`AptRemoveOp` (`package` regex `^[a-z0-9][a-z0-9+\-.]{0,127}$`), `SystemctlOp` (`unit` regex, `action: Literal["start","stop","enable","disable","restart"]`), `UfwRuleOp` (port 1..65535, `action: allow|deny`, `proto: tcp|udp`), `WriteProtectedPathOp` (reuse `schema.py:161`, paths like `/etc/...`).
  - macOS: `BrewInstallOp`/`BrewRemoveOp` (`formula` regex), `LaunchctlOp` (`label` regex, `action: load|unload|enable|disable`), `WriteProtectedPathOp` (paths like `/Library/...`).
- `jarvis/admin/schema.py`: the `AdminOperation` union (`:175-192`) + `ADMIN_OPERATION_TYPES` (`:195-209`) + `DESTRUCTIVE_OPS` (`:212`) become platform-conditional (or a superset the executor dispatches per OS). `jarvis/admin/executor.py` gains per-OS argv builders emitting validated argv lists (`["apt-get","install","-y",op.package]`, `["systemctl",op.action,op.unit]`, `["brew","install",op.formula]`, `["launchctl",op.action,op.label]`) — argv only, `shell=False`.
- Destructive ops (`apt_remove`, `systemctl stop/disable`, `ufw_remove`, `brew_remove`, `launchctl unload`, `write_protected_path`) are in `DESTRUCTIVE_OPS` so the per-action approval gate (`client.py:135-139`) fires identically across OSes.
- `tests/unit/admin/test_schema_unix.py` green; a malicious payload (`package="foo; rm -rf /"`) fails the regex.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-3-admin.md` §3.3.
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-12 + PC-7 (the 13 ops are Windows-native; port the concept, not the command).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-11 (no shell=True / no unvalidated argv), HN-12 (every new op extends the same union; no side door).
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-7 (porting the 13 ops literally via sudo is nonsense — read the full counter-pattern), AP-10 (shelling a privileged string).
- Read `jarvis/admin/schema.py:1-9` (the §Safety mandate) + `:25` + `:175-212` and `jarvis/admin/executor.py` (the Windows argv-builder pattern).
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. `git worktree add -b crossplat/w3-op-vocab ../sub-agents-outputs/crossplat-w3-opvocab feat/crossplatform-port`  (Worktree G — independent of the transport seam)
3. `pip install -e ".[dev]"`
4. `requesting-code-review` before merge.
</environment-setup>

<scope>
Create: `jarvis/admin/schema_unix.py`, `tests/unit/admin/test_schema_unix.py`.
Modify: `jarvis/admin/schema.py` (make the union/types/destructive list platform-conditional or a superset), `jarvis/admin/executor.py` (add per-OS argv builders). Do NOT touch the HMAC core in `ipc.py`. Worktree G also owns 3.5.
</scope>

<primary-path>
1. Write the Unix op models subclassing `_AdminOpBase`, each with a strict regex/Literal field set.
2. Extend the discriminated union + `ADMIN_OPERATION_TYPES` + `DESTRUCTIVE_OPS` (platform-conditional or superset).
3. Add the per-OS argv builders in `executor.py` (argv lists only, `shell=False`).
4. Write `test_schema_unix.py`: each op validates a good payload, rejects a malicious one, and destructive ops are in `DESTRUCTIVE_OPS`.
</primary-path>

<fallback-paths>
- If making the union platform-conditional breaks the Windows tests, use a SUPERSET union (all OS ops registered) and dispatch per-OS in the executor — never narrow the Windows ops. Failure condition: a Windows op-validation test breaks.
- If `apt-get` vs `apt` matters, prefer `apt-get` (stable scripting interface) in the argv builder. Failure condition: the builder emits an interactive-only command.
- You may invent additional Unix ops if clearly within the same concept space, provided each is a pattern-validated `_AdminOpBase` subclass in the same union (HN-12) with argv-only execution (HN-11/AP-7).
</fallback-paths>

<acceptance>
pytest tests/unit/admin/test_schema_unix.py -v
python -c "from jarvis.admin.schema_unix import AptInstallOp; AptInstallOp(package='git')"
python -c "from jarvis.admin.schema_unix import AptInstallOp; AptInstallOp(package='git; whoami')" ; test $? -ne 0 && echo "rejected as expected"
pytest tests/unit/admin/ -v
ruff check jarvis/admin/ && mypy jarvis/admin/schema_unix.py
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-12 / AP-7 — every new op is a fresh member of the SAME `AdminOperation` union; no side door; the Windows ops are not narrowed.
- HN-11 / AP-10 — pattern-validated, typed argv only; `shell=False`; never a free-form shell string.
- the §Safety mandate (`schema.py:1-9`) — `extra="forbid"`, `frozen=True` inherited from `_AdminOpBase`.
SOFT:
- Prefer `apt-get` over `apt` for scripting.
</hard-rules>

<done-signal>
Final report (5 bullets):
- The Unix op models + their validation regexes/Literals.
- The union/types/destructive-list change (conditional vs superset) + the per-OS argv builders.
- Malicious-payload rejection proven (output).
- Hard-rules honored (HN-12/HN-11/AP-7 quoted) + the code-review pass.
- Path-taken.
</done-signal>
```

## 3.4

```prompt
ultrathink
<role>
You build the `Elevator` seam and per-OS elevators (Uac / Sudo / Polkit / MacAuth / Null), with `NullElevator` as the safe default that refuses elevation on a headless/no-auth box. Your outcome is an elevation abstraction where the elevator only SPAWNS the helper — the helper still runs every op through the reused validated-argv chain — and a missing mechanism refuses with a clear English message, never silently runs privileged ops.
</role>

<outcome>
DONE means:
- `jarvis/admin/elevator.py` defines `Elevator` Protocol (`async def ensure_elevated_helper(transport_addr) -> ElevationResult` + `is_available() -> bool`) + `UacElevator`, `SudoElevator`, `PolkitElevator`, `MacAuthElevator`, `NullElevator` + `make_elevator()` factory.
  - `UacElevator` (Windows): the existing `ShellExecuteW("runas", python.exe, "-m jarvis.admin.helper --pipe-name ...")` flow (`launcher.py:11`), unchanged (AD-7).
  - `PolkitElevator` (Linux, preferred): `pkexec` to spawn the helper bound to the unix socket; a polkit policy file under `jarvis/admin/data/`; `is_available()`=`shutil.which("pkexec")`.
  - `SudoElevator` (Linux, fallback): `sudo`/`sudo -A` when polkit absent; `is_available()`=`shutil.which("sudo")`.
  - `MacAuthElevator` (macOS): `osascript -e 'do shell script "…" with administrator privileges'` (or Authorization Services via pyobjc) to spawn the helper.
  - `NullElevator`: returned when `not capabilities.has_elevation`; `ensure_elevated_helper` returns a refusal `ElevationResult` and logs "no elevation mechanism available on this host — privileged operations are disabled; install pkexec or run with sudo". Never raises.
- `make_elevator()`: win32→Uac; darwin→MacAuth; linux→Polkit if pkexec else Sudo else Null; `not has_elevation`→Null.
- `jarvis/admin/launcher.py`: `ensure_admin_secret` (`:42`) stays transport-agnostic; the Windows `ShellExecuteW(runas,…)` helper-spawn (`:11`) moves behind `UacElevator`. `jarvis/admin/client.py`: `AdminClient` (`:66`) gains an injected `Elevator` alongside its existing injectable `pipe_client` (`:80`).
- The elevator ONLY spawns the helper; the helper runs every op through the reused `_decode_request` → `extra="forbid"` schema → argv builder → `shell=False` chain (HN-11/HN-12).
- `tests/unit/admin/test_elevator.py` green (factory selection per platform + `is_available`; `NullElevator.ensure_elevated_helper` returns a refusal and never raises). No end-to-end elevation test — any test that would trigger a real prompt is `skip_ci`.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-3-admin.md` §3.4.
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-12 + AD-6 (graceful null) + AD-3 (interactive auth never CI-testable).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-14 ("NullElevator is the default. A headless / no-auth box refuses elevation with a clear English message; it never silently runs privileged ops"), HN-11 (no shell=True / no unvalidated argv), HN-12 (don't weaken HMAC/validation for convenience), HN-4 (never raise; degrade), HN-6 (no "works" claim without live sign-off).
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-10 (shelling a privileged command string — the elevator passes a validated argv vector, never a shell string), AP-12 (ship a fake).
- Read `jarvis/admin/launcher.py:11/42` (the dormant elevation glue, PC-7) + `jarvis/admin/client.py:66/80/152-164`.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. Same worktree as 3.1 (Worktree F), parallel sub-branch off `crossplat/w3-transport` after 3.1: suggested `crossplat/w3-elevator` branched from `crossplat/w3-transport`.
3. `pip install -e ".[dev]"`
4. `requesting-code-review` before merge.
</environment-setup>

<scope>
Create: `jarvis/admin/elevator.py`, `jarvis/admin/data/` (polkit policy file), `tests/fakes/fake_elevator.py`, `tests/unit/admin/test_elevator.py`.
Modify: `jarvis/admin/launcher.py` (move the runas spawn behind UacElevator; keep `ensure_admin_secret` transport-agnostic), `jarvis/admin/client.py` (inject `Elevator`). Depends on 3.1's transport seam.
</scope>

<primary-path>
1. Define `Elevator` Protocol + `ElevationResult` (success/refusal shape).
2. Implement each elevator; the Linux/macOS ones spawn the helper with a VALIDATED argv vector (never a shell string) routed through `NO_WINDOW_CREATIONFLAGS`/equivalent (HN-18).
3. Implement `NullElevator` (refusal + log, never raises) and `make_elevator()`.
4. Move the Windows runas spawn behind `UacElevator`; inject `Elevator` into `AdminClient` (preserve the DI seam).
5. Write `fake_elevator.py` + `test_elevator.py` (factory selection + is_available + the Null refusal path); mark any real-prompt test `skip_ci`.
</primary-path>

<fallback-paths>
- If the polkit policy file format is uncertain, ship a minimal valid `.policy` granting only the helper action and document it; `PolkitElevator.is_available()` still gates on `which pkexec`. Failure condition: pkexec is invoked without a policy and silently fails.
- If `osascript … with administrator privileges` can't bind cleanly to the socket address, pass the address as a validated argv arg to the helper rather than interpolating into the shell-script string (HN-11/AP-10). Failure condition: the address is interpolated into a shell string.
- You may invent the `ElevationResult` shape and the spawn mechanism if the prescribed ones fight the platform, provided NullElevator is the safe default (HN-14), no shell string carries a privileged arg (AP-10), and the helper still validates every op (HN-12).
</fallback-paths>

<acceptance>
pytest tests/unit/admin/test_elevator.py -v
pytest tests/unit/admin/test_elevator.py -k null -v
python -c "from jarvis.admin.elevator import make_elevator, Elevator; assert isinstance(make_elevator(), Elevator)"
ruff check jarvis/admin/elevator.py && mypy jarvis/admin/elevator.py
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-14 — `NullElevator` is the default; a headless/no-auth box REFUSES with a clear English message; it never silently runs privileged ops.
- HN-11 / AP-10 — the elevator passes a validated argv vector; never a shell string; never `shell=True`.
- HN-12 — the helper still runs every op through the reused validation; the elevator does not bypass it.
- HN-4 — every elevator (esp. Null) degrades/refuses, never raises.
- HN-6 — no "elevation works" claim; Wave 4 owns the live sign-off.
SOFT:
- Mirror the existing DI seam when injecting into `AdminClient`.
</hard-rules>

<done-signal>
Final report (5 bullets):
- The `Elevator` Protocol + the five elevators + factory selection.
- The NullElevator refusal path (default, never raises).
- Confirmation the elevator only spawns; the helper still validates every op (argv-only).
- Hard-rules honored (HN-14/HN-11/HN-12/HN-4 quoted) + the code-review pass.
- Path-taken + the polkit/osascript spawn approach.
</done-signal>
```

## 3.5

```prompt
ultrathink
<role>
You supersede ADR-0001 with ADR-0020 documenting the cross-platform elevation architecture. Your outcome is an append-only ADR recording the AdminTransport + Elevator seams, the reused HMAC core, the UnixSocketTransport peer-cred model, the per-OS op vocabulary, and the NullElevator headless contract — with ADR-0001 marked superseded (not deleted) and numbering kept unique.
</role>

<outcome>
DONE means:
- `docs/adr/0020-cross-platform-elevation.md` exists and records the AD-12 architecture: the `AdminTransport` + `Elevator` seams, the reused HMAC/envelope/Pydantic-argv core (explicitly UNCHANGED), the `UnixSocketTransport` peer-cred model vs the SDDL-ACL pipe, the per-OS op vocabulary, and the `NullElevator` headless refusal. It references the regression guard (`tests/unit/admin/test_hmac_replay.py`) that protects the unchanged core, and states the new surface is transport + elevation + op-vocabulary only.
- `docs/adr/0001-ipc-named-pipe-hmac.md` gains a "Superseded by ADR-0020 (2026-xx-xx)" header note (append-only; do not delete content).
- `CLAUDE.md` Phase-5/admin pointers + AP-table reference the new seams where needed.
- `tests/unit/docs/test_adr_uniqueness.py` green (0020 does not collide; note 0009/0010/0014 carry legacy duplicates).
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-3-admin.md` §3.5.
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-12 + EK-6 (ADR-0001 superseded).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-12 (the security core is unchanged) and HN-17 (English-only artifacts).
- Read `docs/adr/0001-ipc-named-pipe-hmac.md` + an existing well-formed ADR for the house format, and `tests/unit/docs/test_adr_uniqueness.py` for the uniqueness constraint.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. Same worktree as 3.3 (Worktree G): `../sub-agents-outputs/crossplat-w3-opvocab` on `crossplat/w3-op-vocab`.
3. `pip install -e ".[dev]"`
</environment-setup>

<scope>
Create: `docs/adr/0020-cross-platform-elevation.md`. Modify: `docs/adr/0001-ipc-named-pipe-hmac.md` (supersession header), `CLAUDE.md` (admin pointers). Docs only.
</scope>

<primary-path>
1. Read 0001 + the house ADR format.
2. Write 0020 covering the AD-12 architecture + the unchanged-core statement + the regression-guard reference.
3. Add the supersession note to 0001 (append-only).
4. Add the CLAUDE.md pointer; run the uniqueness test.
</primary-path>

<fallback-paths>
- If `test_adr_uniqueness.py` flags a collision with a legacy duplicate, follow its convention (the test documents how duplicates are handled) — do not renumber existing ADRs. Failure condition: the test stays red.
- You may structure ADR-0020 to match whichever house template the existing ADRs use, provided it states the unchanged-core fact (HN-12).
</fallback-paths>

<acceptance>
test -f docs/adr/0020-cross-platform-elevation.md && echo OK
grep -n "Superseded by ADR-0020" docs/adr/0001-ipc-named-pipe-hmac.md
pytest tests/unit/docs/test_adr_uniqueness.py -v
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-12 — ADR-0020 states the HMAC/argv core is UNCHANGED; the new surface is transport + elevation + op vocabulary only.
- HN-17 — English-only; ADR history is append-only (0001 is not deleted).
SOFT:
- Match the house ADR template.
</hard-rules>

<done-signal>
Final report (5 bullets):
- ADR-0020 created + the architecture it records.
- 0001 supersession note + the unchanged-core statement.
- CLAUDE.md pointer added.
- Uniqueness test green (HN-12/HN-17 quoted).
- Path-taken.
</done-signal>
```

## 3.6

```prompt
ultrathink
<role>
You wire the AdminTransport + Elevator seams into `AdminClient` and the helper boot, preserving the exact control flow and extending the refusal path to cover NullElevator. Your outcome is the admin client/helper running through `make_admin_transport()` + `make_elevator()` instead of hardcoded Windows pipe classes — with the "zero silent drops" refusal contract intact across OSes. This is the last sub-task in Worktree F; it lands after 3.2 + 3.4.
</role>

<outcome>
DONE means:
- `jarvis/admin/client.py`: `_ensure_pipe_client` (`:108`) becomes a transport-agnostic `_ensure_transport` using `make_admin_transport()`; `make_elevator()` is injected. `AdminClient.execute` (`:121`) keeps its exact control flow: destructive gate (`:135`) → cancel-token (`:142`) → ensure transport/secret (`:152`) → publish requested (`:167`) → roundtrip (`:172`) → completed/rejected event (`:174-183`). Only step 3 swaps `AdminPipeClient` for `make_admin_transport()`, and the `no_secret` refusal (`:154-164`) is EXTENDED to also cover `NullElevator` refusals with the same `AdminResponse(success=False, error_code=...)` shape.
- `jarvis/admin/helper.py`: constructs `make_admin_transport()` (not hardcoded `AdminPipeServer`) and serves the reused `_decode_request`→executor→`_encode_response` handler.
- `tests/unit/admin/ -v` green on all OS legs (client flow exercised with `fake_admin_transport.py` + `fake_elevator.py`). `import jarvis.admin.client, jarvis.admin.helper` clean on Linux/macOS. The Windows loopback regression `tests/integration/test_admin_ipc_loopback.py` stays green.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-3-admin.md` §3.6.
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-12 + AD-6 ("zero silent drops").
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-14 (NullElevator refusal default), HN-12 (don't weaken validation), HN-11 (no shell=True), HN-7 (no module-scope Windows import in client/helper).
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-10 + AP-12.
- Read `jarvis/admin/client.py:108-183` (the exact control flow to preserve) + `jarvis/admin/helper.py`.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. Same worktree as 3.1/3.2/3.4 (Worktree F): `crossplat/w3-transport` (after merging the 3.2 + 3.4 sub-branches into it). 3.6 wires them in last.
3. `pip install -e ".[dev]"`
4. `requesting-code-review` before merge.
</environment-setup>

<scope>
Modify: `jarvis/admin/client.py` (transport-agnostic ensure + inject elevator + extend the refusal path), `jarvis/admin/helper.py` (bind via `make_admin_transport()`). Depends on 3.1 + 3.2 + 3.4.
</scope>

<primary-path>
1. Replace `_ensure_pipe_client` with `_ensure_transport` using `make_admin_transport()`; inject `make_elevator()` into `AdminClient.__init__` (preserve the injectable seam).
2. Keep `execute`'s control flow identical; extend the `no_secret` refusal to also surface NullElevator refusals as `AdminResponse(success=False, ...)`.
3. Bind the helper via `make_admin_transport()`; serve the reused handler.
4. Run the unit suite with the fakes + the Windows loopback regression; confirm import-clean on POSIX.
</primary-path>

<fallback-paths>
- If injecting the elevator changes `AdminClient.__init__` arity and breaks callers, give it a defaulting `elevator: Elevator | None = None` that lazily builds `make_elevator()` — preserve backward-compatible construction. Failure condition: an existing caller breaks.
- If the helper's transport bind needs the address from the elevator, thread it through as a validated arg, never a shell string (HN-11). Failure condition: the address rides a shell string.
- You may invent the refusal-extension shape if the prescribed one fights the event flow, provided the AD-6 "zero silent drops" contract holds (every failure → typed AdminResponse, never a crash/silent drop).
</fallback-paths>

<acceptance>
pytest tests/unit/admin/ -v
python -c "import jarvis.admin.client, jarvis.admin.helper; print('admin import clean')"
pytest tests/integration/test_admin_ipc_loopback.py -v
python scripts/ci/check_import_clean.py
ruff check jarvis/admin/ && mypy jarvis/admin/client.py jarvis/admin/helper.py
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-14 — NullElevator refusal surfaces as a typed `AdminResponse(success=False, ...)`; never a silent drop.
- HN-12 / HN-11 — the helper still validates every op through the reused chain; no shell=True.
- HN-7 — client/helper import-clean on POSIX (Windows pipe code lazy inside transport).
- AD-6 — the exact control flow is preserved; every failure is a typed response, never a crash.
SOFT:
- Keep `AdminClient.__init__` backward-compatible (defaulting elevator).
</hard-rules>

<done-signal>
Final report (5 bullets):
- The transport-agnostic ensure + injected elevator + the extended refusal path.
- The helper bound via `make_admin_transport()`.
- Admin import-clean on POSIX + the Windows loopback regression green.
- Hard-rules honored (HN-14/HN-12/HN-7 quoted) + the code-review pass.
- Path-taken + any backward-compat default used.
</done-signal>
```

## check-w3

```prompt
ultrathink
<role>
You are a read-only phase auditor for Wave 3 (the security-sensitive admin/elevation wave). Your outcome is a PASS/FAIL verdict on whether the AdminTransport + Elevator seams meet EK-2/EK-3 + the ADR half of EK-6 and are safe to merge — with explicit confirmation that the HMAC core is unchanged, peer-cred is enforced, NullElevator is the default, and end-to-end elevation is correctly NOT CI-verified (deferred to Wave 4). You verify; you do not modify.
</role>

<outcome>
DONE means a verdict report stating, with command evidence, whether:
- The HMAC/envelope core is unchanged (`test_hmac_replay.py` green; HN-12).
- `make_admin_transport()` selects per OS; `ipc.py`/`client.py`/`helper.py` are import-clean on POSIX (no module-scope win32; HN-7).
- `UnixSocketTransport` enforces the peer-cred check + 0600/0700 perms; the real AF_UNIX loopback round-trips (HN-13).
- The Unix op vocabulary is fresh members of the same union with pattern-validated argv; a malicious payload is rejected; `shell=True` appears nowhere on the admin path (HN-11/HN-12/AP-7/AP-10).
- `NullElevator` is the default refusal; `make_elevator()` selects per OS; no end-to-end elevation test runs in CI (HN-14/AD-3).
- ADR-0020 supersedes ADR-0001; uniqueness holds (EK-6).
- The CI matrix is green.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-3-admin.md` (EK acceptance gate).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` EK-2/EK-3/EK-6 + AD-3/AD-12.
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` HN-11/HN-12/HN-13/HN-14/HN-7/HN-15.
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-7/AP-10/AP-12.
</required-reading>

<environment-setup>
1. `git switch feat/crossplatform-port` (with Wave-3 branches merged, or check each branch).
2. `pip install -e ".[dev]"`
Read-only. Run a `requesting-code-review`-style scan over the admin diff focused on the no-shell / validated-argv / peer-cred invariants.
</environment-setup>

<scope>
Modify: NOTHING. Grep the entire admin path for `shell=True` and for module-scope `win32*` imports; grep for any test that would trigger a real elevation prompt without `skip_ci`.
</scope>

<primary-path>
1. Run each acceptance command; capture output.
2. Grep `shell=True` across `jarvis/admin/`; confirm zero.
3. Confirm CI green + that interactive-auth tests are skip_ci.
4. Render the verdict, explicitly flagging end-to-end elevation as Wave-4-deferred (HN-6/AD-3).
</primary-path>

<fallback-paths>
- If a sub-task branch is not yet merged, audit it on its branch and note integration state.
</fallback-paths>

<acceptance>
pytest tests/unit/admin/ -v
pytest tests/integration/test_admin_unix_loopback.py tests/integration/test_admin_ipc_loopback.py -v
grep -rn "shell=True" jarvis/admin/ || echo "no shell=True on the admin path"
python scripts/ci/check_import_clean.py
python -c "from jarvis.admin.elevator import make_elevator, Elevator; from jarvis.admin.transport import make_admin_transport, AdminTransport; assert isinstance(make_elevator(), Elevator) and isinstance(make_admin_transport(), AdminTransport)"
pytest tests/unit/docs/test_adr_uniqueness.py -v
gh run list --workflow=ci.yml --branch feat/crossplatform-port --limit 1
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-12 — confirm the HMAC core is unchanged before declaring mergeable.
- HN-13 — confirm peer-cred is enforced.
- HN-14 — confirm NullElevator is the default refusal.
- HN-15 — do not declare mergeable unless the matrix is green.
SOFT:
- Be explicit that end-to-end elevation is Wave-4-deferred.
</hard-rules>

<done-signal>
Final report (5 bullets):
- HMAC core unchanged + transport seam + import-clean: PASS/FAIL.
- Peer-cred + loopback + op-vocab validation + no-shell: PASS/FAIL.
- NullElevator default + per-OS elevator selection: PASS/FAIL.
- ADR-0020 supersession + uniqueness + CI green: PASS/FAIL.
- Overall verdict + the Wave-4-deferred elevation note + path-taken; if FAIL, the exact blocker.
</done-signal>
```

## merge-w3

```prompt
ultrathink
<role>
You mechanically merge Wave 3's branches onto the integration branch, run the wave acceptance, and push only if green. Step-prescriptive — and extra-careful because this is the security-sensitive wave.
</role>

<outcome>
DONE means: Worktree F's transport branch (with its 3.2 + 3.4 sub-branches already folded in via 3.6) and Worktree G's op-vocab+ADR branch merge `--no-ff` onto `feat/crossplatform-port`; the wave acceptance passes (including the HMAC-replay regression and the no-`shell=True` scan); the CI matrix is green; the branch is pushed. Any conflict or red CI → STOP and hand off to the matching recovery prompt. Never resolve a conflict by weakening a security invariant.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-3-admin.md` §Parallelism (F: 3.1→3.2→3.4→3.6; G: 3.3+3.5).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` HN-11/HN-12/HN-13/HN-14/HN-15/HN-17.
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. `git switch feat/crossplatform-port`
3. `git fetch origin && git pull --ff-only origin feat/crossplatform-port`
4. `pip install -e ".[dev]"`
</environment-setup>

<scope>
Branches: `crossplat/w3-transport` (folds in `crossplat/w3-unix-socket` + `crossplat/w3-elevator` via 3.6), `crossplat/w3-op-vocab`. Merge commits + conflict resolutions only.
</scope>

<primary-path>
1. Confirm the F sub-branches are folded into `crossplat/w3-transport` (3.6 done); if not, fold them there first.
2. `git merge --no-ff crossplat/w3-op-vocab` → run acceptance.
3. `git merge --no-ff crossplat/w3-transport` → run acceptance.
4. Full wave acceptance incl. the HMAC-replay regression + the no-shell scan; if green, `git push origin feat/crossplatform-port`; watch CI.
</primary-path>

<fallback-paths>
- Conflict (likely in `schema.py`/`executor.py` if both waves touched op tables, or `client.py`) → STOP, hand off to `recovery-merge-conflict`. NEVER resolve by removing peer-cred (HN-13), weakening HMAC (HN-12), or adding `shell=True` (HN-11).
- Red CI after push → STOP, hand off to `recovery-red-ci`.
- Never force-push (HN-17).
</fallback-paths>

<acceptance>
pytest tests/unit/admin/test_hmac_replay.py -v
pytest tests/unit/admin/ -q
pytest tests/integration/test_admin_unix_loopback.py tests/integration/test_admin_ipc_loopback.py -v
grep -rn "shell=True" jarvis/admin/ || echo "no shell=True"
python scripts/ci/check_import_clean.py
pytest -m "not skip_ci" --junitxml=report.xml -q; python scripts/ci/assert_min_passed.py report.xml
gh run list --workflow=ci.yml --branch feat/crossplatform-port --limit 1
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-12/HN-13/HN-11/HN-14 — never resolve a conflict by weakening the HMAC core, dropping peer-cred, adding shell=True, or removing the NullElevator default.
- HN-15 — push only if green.
- HN-17 — no force-push, no `--no-verify`.
SOFT:
- `--no-ff` per unit.
</hard-rules>

<done-signal>
Final report (5 bullets):
- The merges + hashes (F sub-branches folded via 3.6).
- Wave acceptance incl. HMAC-replay + no-shell scan.
- CI matrix green status (run URL).
- Hard-rules honored (HN-12/HN-13/HN-15 quoted).
- Path-taken / any recovery handoff.
</done-signal>
```

---

# Wave 4 — Hardening + live sign-off

> Mostly serial and human-gated (per `WELLE-4-hardening.md` §Parallelism): one worktree, sequential 4.1 → 4.2 → (4.3 + 4.4) → 4.6; with 4.5 (JARVIS-20) as an independent side-track sharing the same hardware session. The hardware constraint (a real macOS box + a real Linux desktop) is the bottleneck — if a device is unavailable, record `unverified-on-real-desktop` honestly (AD-3), do not block the wave. NO new feature code lands here — only the verification harness, the sign-off log, and the doc truth-up. Depends on ALL of Waves 0-3 being merged.

## 4.1

```prompt
ultrathink
<role>
You build the live sign-off harness: a checklist of exactly the GUI/permission behaviors CI cannot reach, plus a thin operator-aide probe runner. Your outcome is a per-(feature × OS) manual checklist and a `signoff_probe.py` that constructs each seam via its factory and brackets the manual step — it does NOT try to automate a permission prompt (impossible).
</role>

<outcome>
DONE means:
- `docs/plans/cross-platform-mac-linux/LIVE-SIGNOFF-CHECKLIST.md` exists with ONE row per (feature × {macOS, Linux}) for the four GUI/permission behaviors CI cannot reach: AX/AT-SPI tree capture, Orb transparency, global-hotkey capture, elevation prompt. Each row: the precise manual step, the expected observation, a PASS/FAIL/N/A field. It covers the degrade paths too (revoke permission → onboarding message + pixel fallback; Wayland hotkey no-op; tray fallback).
- `scripts/crossplatform/signoff_probe.py` constructs each seam via its factory (`make_ui_tree_source`, `make_overlay_surface`, `make_hotkey_backend`, `make_admin_transport`/`make_elevator`), runs the live action on the matching OS, and prints what the operator should observe. `--list` prints the probe catalog on ANY OS without raising (acts only on the matching OS).
- `python scripts/crossplatform/signoff_probe.py --list` runs on any OS without raising; `ruff check scripts/crossplatform/` clean.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-4-hardening.md` §4.1 (the exact per-feature manual steps).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-3 (CI + one-time live sign-off) + AD-13 (detect-and-degrade) + AD-8 (Wayland no-op / macOS Input-Monitoring).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-6 ("Never claim a GUI/permission feature 'works' without a live sign-off. CI-green is NOT sufficient…") and HN-4 (the probe never raises off-OS).
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-9 (no real display in CI — the probe is an operator aide, not a CI test) + AP-2 (distinguish empty from forbidden).
- The six factory entry points from Waves 1-3 (read their signatures).
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. `git worktree add -b crossplat/w4-signoff ../sub-agents-outputs/crossplat-w4-signoff feat/crossplatform-port`
3. `pip install -e ".[dev]"`
</environment-setup>

<scope>
Create: `docs/plans/cross-platform-mac-linux/LIVE-SIGNOFF-CHECKLIST.md`, `scripts/crossplatform/signoff_probe.py` (+ `scripts/crossplatform/__init__.py` if needed). Worktree owns 4.1→4.2→4.3→4.4→4.6. No production-code edits.
</scope>

<primary-path>
1. Write the checklist: one row per (feature × OS) for the four behaviors + their degrade paths, each with step/expected/verdict-field.
2. Write `signoff_probe.py`: `--list` prints the catalog; each `--feature` flag constructs the matching factory + runs the live action + prints the expected observation; off-OS → print "N/A on this platform" and exit 0 (never raise).
3. `sys.stdout.reconfigure(encoding='utf-8')` at the top.
</primary-path>

<fallback-paths>
- If a factory needs runtime config the probe can't supply headless, have the probe construct it lazily and print the manual prerequisite instead of failing. Failure condition: `--list` raises on any OS.
- You may structure the checklist table however reads clearly, provided it has one row per (feature × {macOS, Linux}) for the four behaviors (and the degrade paths).
</fallback-paths>

<acceptance>
test -f docs/plans/cross-platform-mac-linux/LIVE-SIGNOFF-CHECKLIST.md && echo OK
python scripts/crossplatform/signoff_probe.py --list
ruff check scripts/crossplatform/
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-6 — the harness frames the live sign-off as the ONLY way to claim GUI/permission features work; CI-green is not enough.
- HN-4 / AP-9 — `signoff_probe.py --list` never raises off-OS; it is an operator aide, never a CI test; no real display required to list.
SOFT:
- Cover the degrade paths (revoke → fallback; Wayland no-op; tray) as explicit rows.
</hard-rules>

<done-signal>
Final report (5 bullets):
- The checklist row coverage (4 behaviors × 2 OSes + degrade paths).
- `signoff_probe.py --list` runs on the maintainer box without raising (output).
- The factories the probe constructs.
- Hard-rules honored (HN-6/HN-4 quoted).
- Path-taken.
</done-signal>
```

## 4.2

```prompt
ultrathink
<role>
You execute the live sign-off on a real macOS box and a real Linux desktop and record an honest, dated, device-attributed per-feature verdict. Your outcome is `SIGNOFF-LOG.md` where every GUI/permission row carries either `live-verified <date> on <device>` or an explicit `unverified-on-real-desktop` with the reason — honesty over a green-washed claim.
</role>

<outcome>
DONE means:
- `docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md` exists with a verdict line for every (feature × OS) GUI/permission row from the 4.1 checklist.
- Each verdict is either `<feature> (<OS>): live-verified <date> on <device>` or `<feature> (<OS>): unverified-on-real-desktop — <reason>`. No row is left blank.
- Any behavior that could not be reached (no Wayland box, no rented Mac) is recorded honestly as `unverified-on-real-desktop` with the reason — this is the AD-3 honesty contract, not a failure; the plan still ships.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-4-hardening.md` §4.2.
- `docs/plans/cross-platform-mac-linux/LIVE-SIGNOFF-CHECKLIST.md` (from 4.1 — the rows to fill).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-3 + EK-5.
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-6 (no "works" claim without a live sign-off) and HN-17 (English-only).
</required-reading>

<environment-setup>
1. Continue in the 4.1 worktree: `../sub-agents-outputs/crossplat-w4-signoff` on `crossplat/w4-signoff`.
2. On EACH real device available: `pip install -e ".[dev,desktop]"` (+ `.[desktop-macos]` on Mac; `apt install python3-pyatspi gir1.2-atspi-2.0` on Linux per AD-14), then run `scripts/crossplatform/signoff_probe.py` per the checklist.
3. This sub-task is HUMAN-GATED — it requires the operator at a real macOS box and a real Linux desktop.
</environment-setup>

<scope>
Create: `docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md`. No production-code edits.
</scope>

<primary-path>
1. On the macOS device: run the AX, Orb-transparency, hotkey-capture, and elevation-prompt probes per the checklist; record each verdict with date + device.
2. On the Linux desktop: run the AT-SPI, Orb (compositor + tray fallback), hotkey (X11 vs Wayland), and elevation (pkexec/sudo/null) probes; record each verdict.
3. For any unreachable behavior, record `unverified-on-real-desktop` + the reason.
4. Cross-check that every 4.1 row has a verdict line.
</primary-path>

<fallback-paths>
- If only one OS device is available, complete that OS and record the other's rows as `unverified-on-real-desktop — no <OS> device available` (AD-3 honesty). Failure condition: a row is left blank.
- If a rented cloud Mac lacks a GUI session for the Orb, record the Orb row as unverified with that reason but still verify the AX-tree/elevation headless-reachable parts. Failure condition: claiming Orb transparency without a real display.
</fallback-paths>

<acceptance>
test -f docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md && echo OK
grep -c "live-verified\|unverified-on-real-desktop" docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-6 — only a live sign-off (with date + device) earns a "works" claim; everything else is `unverified-on-real-desktop`.
- HN-17 — English-only.
SOFT:
- An honest `unverified` is a pass for the AD-3 contract, not a failure.
</hard-rules>

<done-signal>
Final report (5 bullets):
- Devices used (or which were unavailable).
- The per-feature verdicts (live-verified vs unverified, with dates/devices).
- Confirmation no row is blank (grep count ≥ row count).
- Hard-rules honored (HN-6/HN-17 quoted).
- Path-taken (which OSes were reachable).
</done-signal>
```

## 4.3

```prompt
ultrathink
<role>
You write the honest per-feature labels into the README capability matrices to retire the "Windows-only" claim. Your outcome is the repo README + the plan README reflecting the real cross-platform status — Terminal/App-launch as CI-verified, the GUI/permission features carrying the 4.2 verdict badge, Admin noted as never-CI-E2E.
</role>

<outcome>
DONE means:
- `README.md` (the "What runs where (platform capability matrix)" table at `README.md:64-79`): the rows that said Windows-only (the "—" cells for Linux/macOS at `:75-77`) now carry honest badges — Terminal + app-launch-by-name → `✅ CI-verified` (EK-4, no sign-off needed); UI-element-click, Orb, global-hotkey → the 4.2 verdict (`✅ live-verified <date>` or `🟡 unverified-on-real-desktop`); Admin/elevation → the 4.2 verdict + "never CI-E2E by design".
- The prose paragraph at `README.md:79` no longer claims those six are Windows-only — the new truth is "cross-platform behind a seam, with per-feature verification badges". A short "Verification status" subsection links to `SIGNOFF-LOG.md`.
- `docs/plans/cross-platform-mac-linux/README.md` (the TL;DR six-feature table at `:41-48`): the "CI-testable?" column matches the SIGNOFF-LOG.
- `grep "Global-hotkey wake, Orb overlay" README.md` no longer shows "— | — | ✅"; `grep "CI-verified\|live-verified\|unverified-on-real-desktop" README.md` matches in the capability-matrix section. `python -c "import jarvis"` still clean (docs-only; no `npm run build` needed).
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-4-hardening.md` §4.3 (the exact README lines).
- `docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md` (from 4.2 — the verdicts to transcribe).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` EK-4/EK-5/EK-6 + AD-3.
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-6 (honest badges; CI-green ≠ GUI works) and HN-17 (English-only).
- Read the current `README.md:64-79` + the plan `README.md:41-48` to see the exact present cells.
</required-reading>

<environment-setup>
1. Continue in the 4.1/4.2 worktree: `../sub-agents-outputs/crossplat-w4-signoff` on `crossplat/w4-signoff`.
2. `pip install -e ".[dev]"`
</environment-setup>

<scope>
Modify: `README.md` (the capability matrix + the prose + a Verification-status subsection), `docs/plans/cross-platform-mac-linux/README.md` (the TL;DR "CI-testable?" column). Docs only.
</scope>

<primary-path>
1. Transcribe the 4.2 verdicts into the README matrix rows (CI-verified for terminal/app-launch; the live verdict for AX/Orb/hotkey; never-CI-E2E for admin).
2. Rewrite the Windows-only prose; add the Verification-status subsection linking SIGNOFF-LOG.md.
3. Align the plan README's TL;DR "CI-testable?" column.
4. Run the grep acceptance.
</primary-path>

<fallback-paths>
- If the README line numbers have drifted, edit the rows by their content (find the capability table) and note the drift. Failure condition: the matrix can't be located.
- If a feature's 4.2 verdict is `unverified-on-real-desktop`, the badge MUST be `🟡 unverified-on-real-desktop` — never upgrade it to a green claim (HN-6). Failure condition: a badge over-claims relative to SIGNOFF-LOG.
</fallback-paths>

<acceptance>
grep -n "CI-verified\|live-verified\|unverified-on-real-desktop" README.md
grep -n "Global-hotkey wake, Orb overlay" README.md
python -c "import jarvis; print('import clean')"
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-6 — every GUI/permission badge matches its SIGNOFF-LOG verdict exactly; never over-claim.
- HN-17 — English-only.
SOFT:
- Keep the Verification-status subsection short; link, don't duplicate, SIGNOFF-LOG.
</hard-rules>

<done-signal>
Final report (5 bullets):
- The README matrix rows updated + the badges applied (matching 4.2).
- The Windows-only prose retired + the Verification-status link.
- The plan README TL;DR column aligned.
- Hard-rules honored (HN-6/HN-17 quoted).
- Path-taken + any line-number drift.
</done-signal>
```

## 4.4

```prompt
ultrathink
<role>
You update CLAUDE.md's cross-platform framing and supersession pointers so a future agent does not "fix" the migration's intentional decisions. Your outcome is a CLAUDE.md paragraph recording the six features as cross-platform behind the `jarvis/platform/` seam, the per-feature verification status, ADR-0020, and the AD-14 dependency reality (especially: do NOT pip-install pyatspi) — while keeping the €5-VPS base-install doctrine intact.
</role>

<outcome>
DONE means:
- `CLAUDE.md`: a paragraph (or small table) records that Terminal/Hotkey/Orb/UI-element-click/Admin are now cross-platform behind the `jarvis/platform/` seam, with per-feature verification status pointing at `SIGNOFF-LOG.md`, referencing ADR-0020 (supersedes ADR-0001) and the `jarvis/platform/` capability module (AD-5).
- The dependency-grouping reality is recorded (AD-14): `pynput`+`ptyprocess` in `[desktop]`, `pyobjc-*` in `[desktop-macos]`, `pyatspi` as a distro prerequisite — so a future agent does not "fix" the missing pyatspi pip dep.
- The doctrine is intact: the base €5-VPS install still ships none of these desktop extras; they remain opt-in and degrade gracefully. The "base install boots on a headless `python:3.11-slim`" claim is unchanged and still true (no new base dependency added).
- `grep "ADR-0020\|jarvis/platform\|SIGNOFF-LOG" CLAUDE.md` matches; `grep "pyatspi" CLAUDE.md` matches.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-4-hardening.md` §4.4.
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-5/AD-14 + EK-6.
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-8 (extras only), HN-9 ("Never put pyatspi in a pip extra"), HN-6 (verification badges), HN-17 (English-only).
- Read `CLAUDE.md` "Cloud-First Philosophy" + "Windows specifics" + the AP-table/pointers sections.
</required-reading>

<environment-setup>
1. Continue in the Wave-4 worktree: `../sub-agents-outputs/crossplat-w4-signoff` on `crossplat/w4-signoff`.
2. `pip install -e ".[dev]"`
</environment-setup>

<scope>
Modify: `CLAUDE.md` (the cross-platform framing + the dependency-reality note + the supersession pointer). Docs only.
</scope>

<primary-path>
1. Add the cross-platform-behind-the-seam paragraph + the per-feature verification pointer to SIGNOFF-LOG.md + the ADR-0020/`jarvis/platform/` references.
2. Add the AD-14 dependency-reality note, explicitly: do NOT pip-install pyatspi.
3. Confirm the doctrine's base-install claim is unchanged.
4. Run the grep acceptance.
</primary-path>

<fallback-paths>
- If CLAUDE.md's section structure has changed, place the paragraph in the most relevant existing section (Windows specifics / Optional power-user extras) and note the placement. Failure condition: the new framing can't be located by a future agent.
- You may phrase the framing however reads clearly, provided the pyatspi-is-apt-only guidance is explicit (HN-9) and the base-install doctrine is untouched.
</fallback-paths>

<acceptance>
grep -n "ADR-0020\|jarvis/platform\|SIGNOFF-LOG" CLAUDE.md
grep -n "pyatspi" CLAUDE.md
python -c "import jarvis; print('import clean')"
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-9 — the "do not pip-install pyatspi" guidance is recorded (defends AD-14).
- HN-8 — the extras-only / base-install-clean doctrine is reaffirmed, not weakened.
- HN-17 — English-only.
SOFT:
- Keep the doctrine's headless-VPS claim verbatim-true.
</hard-rules>

<done-signal>
Final report (5 bullets):
- The cross-platform framing paragraph + the SIGNOFF-LOG / ADR-0020 / jarvis-platform references.
- The AD-14 dependency-reality note (incl. pyatspi-is-apt-only).
- Confirmation the base-install doctrine is unchanged.
- Hard-rules honored (HN-9/HN-8/HN-17 quoted).
- Path-taken.
</done-signal>
```

## 4.5

```prompt
ultrathink
<role>
You run the JARVIS-20 cross-platform benchmark (authored elsewhere) on each available OS and record per-(scenario × OS) scores. Your outcome is `JARVIS-20-RESULTS.md` distinguishing `pass` / `degraded-as-designed` (a pass for the AD-6 contract) / `fail` (a crash or silent drop — a release blocker per AD-OE6). Zero `fail` rows are allowed at close-out.
</role>

<outcome>
DONE means:
- `docs/plans/cross-platform-mac-linux/JARVIS-20-RESULTS.md` exists with a row per (scenario × available OS), each scored `pass` / `degraded-as-designed` (with the reason, e.g. Wayland hotkey no-op, headless tray fallback) / `fail` (a crash or silent drop).
- Zero `fail` rows that represent a crash or silent drop (a `fail` is a release blocker per AD-OE6); every non-pass is explicitly `degraded-as-designed` with the reason.
- The results file references `JARVIS-20-CROSSPLATFORM.md` as its scenario source and cross-links the SIGNOFF-LOG.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-4-hardening.md` §4.5.
- `docs/plans/cross-platform-mac-linux/JARVIS-20-CROSSPLATFORM.md` (the 20 scenarios — authored by a separate agent; you EXECUTE, not author. If absent, coordinate — do not invent scenarios).
- `docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md` (cross-link target).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` AD-6 (graceful degrade) + AD-OE6 (zero silent drops — a `fail` is a blocker).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-4 (degrade, never raise — a crash is a fail) and HN-5 (never silently empty — a silent drop is a fail).
</required-reading>

<environment-setup>
1. Continue in / share the Wave-4 worktree on `crossplat/w4-signoff` (4.5 is the independent side-track sharing the same hardware session as 4.2).
2. On each available device: `pip install -e ".[dev,desktop]"` (+ the per-OS extras/prereqs as in 4.2).
3. Human-gated: needs the same real macOS + Linux devices as 4.2.
</environment-setup>

<scope>
Create: `docs/plans/cross-platform-mac-linux/JARVIS-20-RESULTS.md`. No production-code edits.
</scope>

<primary-path>
1. Read the 20 scenarios from `JARVIS-20-CROSSPLATFORM.md`.
2. Execute each on the maintainer Windows box (baseline), the macOS device, and the Linux desktop; score each (scenario × OS).
3. Classify every non-pass as `degraded-as-designed` (with reason) or `fail` (crash/silent drop). Any `fail` is a release blocker — flag it loudly for a fix before close-out.
4. Cross-link the SIGNOFF-LOG; reference the scenario source.
</primary-path>

<fallback-paths>
- If a device is unavailable, run the reachable OSes and mark the missing OS's rows as not-run with the reason (mirror 4.2's honesty). Failure condition: a row is fabricated as `pass` without running it.
- If `JARVIS-20-CROSSPLATFORM.md` is not yet authored, STOP and report the dependency — do NOT invent the scenarios (this wave executes, does not author them).
- If a scenario surfaces a `fail`, record it and escalate; do not relabel a crash as `degraded-as-designed` to green the table (HN-4/HN-5/AD-OE6).
</fallback-paths>

<acceptance>
test -f docs/plans/cross-platform-mac-linux/JARVIS-20-RESULTS.md && echo OK
grep -c "degraded-as-designed\|pass\|fail" docs/plans/cross-platform-mac-linux/JARVIS-20-RESULTS.md
grep -n "JARVIS-20-CROSSPLATFORM.md" docs/plans/cross-platform-mac-linux/JARVIS-20-RESULTS.md
</acceptance>

<hard-rules>
INVIOLABLE:
- AD-OE6 / HN-4 / HN-5 — a `fail` (crash or silent drop) is a release blocker; never relabel it as degraded-as-designed.
- HN-6 — GUI/permission scenario results align with the SIGNOFF-LOG verdicts.
SOFT:
- `degraded-as-designed` (with a reason) is a pass for the AD-6 contract.
</hard-rules>

<done-signal>
Final report (5 bullets):
- The scenarios run + the OSes covered.
- The score distribution (pass / degraded-as-designed / fail).
- Any `fail` rows flagged as blockers (or "zero fails").
- Hard-rules honored (AD-OE6/HN-4/HN-5 quoted).
- Path-taken + the scenario-source dependency status.
</done-signal>
```

## 4.6

```prompt
ultrathink
<role>
You close out the plan by confirming the full EK-1..EK-6 Definition of Done with an evidence link attached to each. Your outcome is the plan README's DoD section marked complete, each EK pointing at its proving artifact, and a final re-confirmation that CI is still green and the import gate still holds after all six ports merged.
</role>

<outcome>
DONE means:
- `docs/plans/cross-platform-mac-linux/README.md`'s Definition-of-Done section marks EK-1..EK-6 complete, each with an evidence pointer: EK-1 → the green `ci.yml` run; EK-2 → the six factories (`make_pty_backend`, app-launch resolver, `make_hotkey_backend`, `make_ui_tree_source`, `make_overlay_surface`, `make_admin_transport`/`make_elevator`); EK-3 → the `tests/fakes/` fakes list; EK-4 → the CI terminal real-PTY test; EK-5 → `SIGNOFF-LOG.md`; EK-6 → ADR-0020 + the README/CLAUDE.md diffs + the import-cleanliness gate.
- `pytest -m "not skip_ci" -q` green on all three CI legs (re-confirm EK-1 after the doc-only Wave-4 changes); `python scripts/ci/check_import_clean.py` exits 0 (EK-6 holds after all six ports merged).
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-4-hardening.md` §4.6.
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` EK-1..EK-6 (the full DoD).
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-15 (CI green) and HN-7 (import-cleanliness holds).
- The artifacts produced across all waves (the six factories, the fakes, SIGNOFF-LOG, ADR-0020).
</required-reading>

<environment-setup>
1. Continue in the Wave-4 worktree on `crossplat/w4-signoff` (after 4.2/4.3/4.4/4.5).
2. `pip install -e ".[dev]"`
</environment-setup>

<scope>
Modify: `docs/plans/cross-platform-mac-linux/README.md` (the DoD checklist + evidence links). Docs only.
</scope>

<primary-path>
1. Walk EK-1..EK-6; attach the proving artifact/command to each.
2. Re-run the import gate + the non-skip_ci suite to re-confirm EK-1/EK-6 after the doc changes.
3. Mark the DoD section complete with the evidence pointers.
</primary-path>

<fallback-paths>
- If any EK is not fully met (e.g. a feature is `unverified-on-real-desktop` per 4.2), mark it with the honest status + the reason rather than checking it falsely (HN-6/AD-3). Failure condition: an EK is checked without evidence.
- If the non-skip_ci suite drops below the min-passed floor after merges, STOP and hand off to `recovery-red-ci` — do not mark EK-1 complete. 
</fallback-paths>

<acceptance>
pytest -m "not skip_ci" --junitxml=report.xml -q; python scripts/ci/assert_min_passed.py report.xml
python scripts/ci/check_import_clean.py
grep -n "EK-1\|EK-2\|EK-3\|EK-4\|EK-5\|EK-6" docs/plans/cross-platform-mac-linux/README.md
gh run list --workflow=ci.yml --branch feat/crossplatform-port --limit 1
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-15 — EK-1 is only complete if the matrix is genuinely green.
- HN-7 — EK-6's import gate must still pass after all six ports merged.
- HN-6 — an EK tied to a GUI/permission feature reflects its honest SIGNOFF-LOG status.
SOFT:
- Each EK gets a concrete evidence pointer, not a bare check.
</hard-rules>

<done-signal>
Final report (5 bullets):
- EK-1..EK-6 status + the evidence pointer for each.
- The re-confirmed CI + import-gate results.
- Any EK left honestly partial (with the reason).
- Hard-rules honored (HN-15/HN-7/HN-6 quoted).
- Path-taken.
</done-signal>
```

## check-w4

```prompt
ultrathink
<role>
You are a read-only phase auditor for Wave 4 (the closing wave). Your outcome is a PASS/FAIL verdict on whether the live sign-off, the honest doc labels, the JARVIS-20 results, and the EK close-out are complete and truthful — and whether the plan is genuinely ready to declare done. You verify; you do not modify.
</role>

<outcome>
DONE means a verdict report stating, with command evidence, whether:
- `LIVE-SIGNOFF-CHECKLIST.md` + `SIGNOFF-LOG.md` exist; every (feature × OS) GUI/permission row has a `live-verified`/`unverified-on-real-desktop` verdict (EK-5).
- The README capability matrix + the plan TL;DR + CLAUDE.md carry honest badges matching the SIGNOFF-LOG; no badge over-claims (HN-6).
- `JARVIS-20-RESULTS.md` exists with zero crash/silent-drop `fail` rows (AD-OE6).
- The DoD section marks EK-1..EK-6 with evidence; CI is green; the import gate holds.
- pyatspi is still absent from pyproject; the base install is still clean.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-4-hardening.md` (EK acceptance gate).
- `docs/plans/cross-platform-mac-linux/_FROZEN-DECISIONS.md` EK-1..EK-6 + AD-3/AD-OE6.
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` HN-6/HN-9/HN-15/HN-7.
</required-reading>

<environment-setup>
1. `git switch feat/crossplatform-port` (with the Wave-4 branch merged, or check it).
2. `pip install -e ".[dev]"`
Read-only.
</environment-setup>

<scope>
Modify: NOTHING. Cross-check each README/CLAUDE.md badge against the SIGNOFF-LOG verdict; grep for any over-claim.
</scope>

<primary-path>
1. Run each acceptance command; capture output.
2. Cross-check badges vs SIGNOFF-LOG (no over-claim).
3. Confirm zero crash/silent-drop fails in JARVIS-20-RESULTS.
4. Render the verdict.
</primary-path>

<fallback-paths>
- If a device was unavailable and a feature is honestly `unverified-on-real-desktop`, that is a PASS for the AD-3 contract — not a FAIL — as long as the badge matches. Failure condition: a badge claims live-verified without a matching SIGNOFF-LOG line.
</fallback-paths>

<acceptance>
test -f docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md && test -f docs/plans/cross-platform-mac-linux/JARVIS-20-RESULTS.md && echo "artifacts ok"
grep -n "CI-verified\|live-verified\|unverified-on-real-desktop" README.md
grep -n "pyatspi" CLAUDE.md
python scripts/ci/check_import_clean.py
python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); ext=d['project']['optional-dependencies']; assert all('pyatspi' not in x for g in ext.values() for x in g); print('no pyatspi in pyproject')"
pytest -m "not skip_ci" --junitxml=report.xml -q; python scripts/ci/assert_min_passed.py report.xml
gh run list --workflow=ci.yml --branch feat/crossplatform-port --limit 1
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-6 — no badge over-claims; each matches its SIGNOFF-LOG verdict.
- HN-15 — CI green before declaring done.
- HN-7 / HN-9 — import gate holds; pyatspi absent from pyproject.
SOFT:
- An honest `unverified` is a PASS for AD-3.
</hard-rules>

<done-signal>
Final report (5 bullets):
- Sign-off artifacts + every GUI/permission row has a verdict (EK-5): PASS/FAIL.
- Badges match SIGNOFF-LOG (no over-claim): PASS/FAIL.
- JARVIS-20 zero crash/silent-drop fails: PASS/FAIL.
- DoD complete + CI green + import gate + pyatspi-absent: PASS/FAIL.
- Overall verdict + path-taken; if FAIL, the exact blocker.
</done-signal>
```

## merge-w4

```prompt
ultrathink
<role>
You mechanically merge Wave 4's branch onto the integration branch, run the (mostly doc) wave acceptance, push only if green, and then open the final PR from the integration branch to main. Step-prescriptive.
</role>

<outcome>
DONE means: `crossplat/w4-signoff` merges `--no-ff` onto `feat/crossplatform-port`; the wave acceptance passes (CI still green after the doc-only changes; import gate holds); the branch is pushed; and a PR from `feat/crossplatform-port` → `main` is opened with the EK-1..EK-6 evidence in the body. Any conflict or red CI → STOP and hand off to the matching recovery prompt.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/WELLE-4-hardening.md` §Parallelism + EK gate.
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` HN-15, HN-16, HN-17.
- `CLAUDE.md` (main branch protection: PR-required, linear history, required `sign` check).
</required-reading>

<environment-setup>
1. `pwsh scripts/preflight.ps1`
2. `git switch feat/crossplatform-port`
3. `git fetch origin && git pull --ff-only origin feat/crossplatform-port`
4. `pip install -e ".[dev]"`
</environment-setup>

<scope>
Branch: `crossplat/w4-signoff`. Merge commit + conflict resolutions only; then open the PR to main.
</scope>

<primary-path>
1. `git merge --no-ff crossplat/w4-signoff` → run acceptance.
2. If green, `git push origin feat/crossplatform-port`; watch CI.
3. Open the PR: `gh pr create --base main --head feat/crossplatform-port` with the EK-1..EK-6 evidence in the body and the per-feature SIGNOFF-LOG badges summarized. End the PR body with the required attribution line.
</primary-path>

<fallback-paths>
- Conflict → STOP, hand off to `recovery-merge-conflict`.
- Red CI after push → STOP, hand off to `recovery-red-ci`.
- If the required `sign` check only runs on tags (per main branch protection notes), surface that to the operator for the owner-bypass decision — do not force the merge yourself (HN-17).
</fallback-paths>

<acceptance>
python scripts/ci/check_import_clean.py
pytest -m "not skip_ci" --junitxml=report.xml -q; python scripts/ci/assert_min_passed.py report.xml
ruff check jarvis/ && ruff format --check jarvis/
git log --oneline --merges -1
gh run list --workflow=ci.yml --branch feat/crossplatform-port --limit 1
gh pr view --json url,state 2>/dev/null || echo "open the PR"
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-15 — push + PR only if the matrix is green.
- HN-17 — English-only PR body; never force-push; never `--no-verify`; never bypass signing yourself (surface to the operator).
SOFT:
- Summarize the honest per-feature badges in the PR body.
</hard-rules>

<done-signal>
Final report (5 bullets):
- The merge hash + the wave acceptance result.
- CI matrix green status (run URL).
- The PR URL + the EK evidence summarized in its body.
- Hard-rules honored (HN-15/HN-17 quoted).
- Path-taken / any owner-bypass surfaced to the operator.
</done-signal>
```

---

# Recovery prompts

> Recovery needs exact state, not creativity — these are step-prescriptive. Each diagnoses first, then applies the minimal safe fix, and NEVER resolves a problem by breaking an INVIOLABLE rule (no force-push, no `--no-verify`, no lowering the min-passed floor, no removing a regression guard or a security invariant).

## recovery-rate-limited

```prompt
ultrathink
<role>
You recover a coding-agent session that stalled or failed due to API/tool rate-limiting (429s, OAuth-concurrency saturation, throttled subprocess workers). Your outcome is the in-progress sub-task brought back to a clean, resumable state without losing committed work — by reducing concurrency and resuming, not by abandoning the branch.
</role>

<outcome>
DONE means: the cause of the rate-limit is identified (which provider/tool, concurrency vs quota); in-flight work is preserved (committed on the sub-task branch); concurrency is reduced to a safe level; and either the sub-task resumes cleanly OR a clear "resume here" handoff is recorded. No work is lost; no branch is force-reset.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` HN-17 (never force-push; never bypass hooks).
- The sub-task prompt that was running (its `<done-signal>` defines what "resume here" means).
- MEMORY: the mission mass-failure root cause was OAuth-concurrency saturation (fix = First-Output-Gate + max_concurrent 1) — relevant if the rate-limit is on the worker/sub-agent path.
</required-reading>

<environment-setup>
1. `git status` (in the affected worktree) — confirm whether uncommitted work exists.
2. `git stash list` and `git log --oneline -5` — locate the last good commit.
</environment-setup>

<scope>
Modify: only what is needed to reduce concurrency (e.g. a worker `max_concurrent` setting) + commit in-flight work. Do NOT edit unrelated files.
</scope>

<primary-path>
1. Diagnose: read the error — identify the provider/tool and whether it is a quota cap or concurrency saturation.
2. Preserve: `git add -A && git commit -m "wip: <sub-task> checkpoint before rate-limit recovery"` on the sub-task branch (NEVER on a shared branch directly; NEVER `git add .` blindly — review the diff first).
3. Reduce concurrency: if it is OAuth-concurrency (sub-agent/worker path), set the relevant `max_concurrent` to 1; if it is a per-provider quota, switch provider via ENV (e.g. `brain.primary`→an alternate) per the project's drift-safe config path (ENV override, not a raw toml edit).
4. Wait for the limit window to clear (do not hammer-retry in a tight loop), then re-run the sub-task's acceptance commands to confirm the resumable state.
5. Record a "resume here" note (which acceptance commands still need to pass).
</primary-path>

<fallback-paths>
- If the rate-limit is global and persistent, record the checkpoint commit + a handoff and STOP — let the operator decide when to resume. Failure condition: retries keep 429-ing.
- If switching provider, use the ENV-override path (drift-guard-safe), never a raw `jarvis.toml` edit that a parallel session could roll back.
- Never delete `jarvis.lock` manually (MEMORY: that breaks the lock-holder sweep contract).
</fallback-paths>

<acceptance>
git status --porcelain
git log --oneline -3
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-17 — no force-push; no `--no-verify`; commit in-flight work rather than discarding it.
- Never `git add .` blindly (review the diff); never manually delete `jarvis.lock`.
SOFT:
- Prefer ENV-override provider switch over a raw toml edit.
</hard-rules>

<done-signal>
Final report (5 bullets):
- The diagnosed cause (provider/tool, quota vs concurrency).
- The checkpoint commit hash (in-flight work preserved).
- The concurrency/provider mitigation applied.
- Hard-rules honored (HN-17 quoted).
- Resume-here: which acceptance commands still need to pass.
</done-signal>
```

## recovery-red-ci

```prompt
ultrathink
<role>
You recover a red CI matrix run. Your outcome is a correctly-diagnosed failure (which OS leg, which step, real failure vs mass-skip vs flake) and a minimal fix that turns the matrix genuinely green — never by lowering the min-passed floor, widening skip_ci to dodge a real failure, or weakening a regression guard.
</role>

<outcome>
DONE means: the red leg + failing step is identified from the run logs; the failure is classified (real test failure / import-cleanliness violation / min-passed-floor shortfall / mass-skip / flake); the minimal honest fix is applied on the appropriate branch; and the matrix is re-run green on all three legs. No floor lowered, no skip_ci widened to hide a real failure, no guard removed.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-15 (matrix green before merge), HN-16 ("Never let a mass-skip pass as green. The minimum-passed-count floor must hold"), HN-7 (import cleanliness), HN-2 (don't weaken a regression guard).
- `docs/plans/cross-platform-mac-linux/ANTI-PATTERNS.md` AP-6 (green-by-mass-skip) + AP-13 (module-scope import goes red on the other OS).
</required-reading>

<environment-setup>
1. `gh run list --workflow=ci.yml --branch <branch> --limit 3`
2. `gh run view <run-id> --log-failed` — read the failing step on each red leg.
3. `git switch <the branch whose PR is red>`; `pip install -e ".[dev]"`.
</environment-setup>

<scope>
Modify: only the file(s) causing the red. Do NOT touch `scripts/ci/assert_min_passed.py`'s FLOOR (except to bump it FORWARD if the green count legitimately rose) and do NOT add `skip_ci` to dodge a real failure.
</scope>

<primary-path>
1. Classify the failure from the logs:
   - **Import-cleanliness red** (Linux/macOS `import jarvis` fails / the AST gate flags a module-scope Windows import) → move the offending import inside the function/branch, lazy + guarded (HN-7/AP-13). Reproduce locally: `python scripts/ci/check_import_clean.py`.
   - **Min-passed-floor shortfall** (passed < FLOOR with zero failures) → this is a MASS-SKIP, not a pass: find the collection error / swallowed import that dropped the count; fix the root cause. NEVER lower the floor (HN-16/AP-6).
   - **Real test failure** → fix the code or the test honestly; do not reverse-patch the test to pass (HN-2).
   - **Flake** (passes locally, intermittent on the runner) → confirm it is truly non-deterministic (re-run); if a GUI/display test sneaked into CI, mark it `skip_ci` and move it to the live sign-off (AP-9) — but ONLY a genuinely-headless-impossible test, never a real failure.
2. Apply the minimal fix on the right branch.
3. Re-run the relevant acceptance locally, push, and watch the matrix.
</primary-path>

<fallback-paths>
- If the failure is on a single OS leg only (e.g. macos-latest), reproduce the platform-specific cause via the capability/probe path before editing; do not "fix" by branching inline on sys.platform (HN-3). Failure condition: the fix passes one leg but reds another.
- If the green count legitimately rose, bump FLOOR FORWARD (never down) and note it. Failure condition: lowering FLOOR to pass.
- If the root cause is genuinely unclear after log review, add targeted logging and re-run rather than guessing a fix (systematic-debugging).
</fallback-paths>

<acceptance>
python scripts/ci/check_import_clean.py
pytest -m "not skip_ci" --junitxml=report.xml -q; python scripts/ci/assert_min_passed.py report.xml
ruff check jarvis/ && mypy jarvis/platform/
gh run list --workflow=ci.yml --branch <branch> --limit 1
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-16 / AP-6 — never lower the min-passed floor; a shortfall is a mass-skip to fix at the root.
- HN-2 — never reverse-patch a test or remove a regression guard to green a run.
- HN-7 / AP-13 — fix an import-cleanliness red by making the import lazy, not by suppressing the gate.
- HN-15 — the matrix must be GENUINELY green on all three legs.
SOFT:
- Bump FLOOR forward only if the green count truly rose.
</hard-rules>

<done-signal>
Final report (5 bullets):
- The red leg + failing step + the classification.
- The root cause + the minimal fix applied (on which branch).
- Re-run result (all three legs green).
- Hard-rules honored (HN-16/HN-2/HN-7/HN-15 quoted).
- Path-taken.
</done-signal>
```

## recovery-merge-conflict

```prompt
ultrathink
<role>
You resolve a merge conflict that arose while merging a cross-platform sub-task branch onto the integration branch. Your outcome is a correct, semantically-sound resolution that preserves BOTH the Windows path (untouched, AD-7) and the new OS sibling — never a resolution that drops a regression guard, re-adds a forbidden dependency, or weakens a security invariant.
</role>

<outcome>
DONE means: the conflicted files are identified; each conflict is resolved by understanding BOTH sides (not blindly taking one); the merge completes; the wave's acceptance passes; and no INVIOLABLE rule was broken in the resolution (no pyatspi re-added, no peer-cred/HMAC weakened, no regression guard deleted, no Windows impl rewritten).
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` — inline-quote HN-1 (Windows impl untouched), HN-2 (don't delete a regression guard), HN-9 (no pyatspi in pyproject), HN-12/HN-13 (don't weaken HMAC/peer-cred), HN-17 (no force-push).
- The `_FROZEN-DECISIONS.md` hot-file ownership table (README §Hot-file ownership) — which wave owns which file; a file in two waves is a coordination point.
- The two sub-task prompts whose branches are colliding.
</required-reading>

<environment-setup>
1. `git status` — list the conflicted paths (`Unmerged paths`).
2. `git diff --name-only --diff-filter=U` — the exact conflict set.
3. For each conflicted file: `git log --oneline -3 <file>` on both branches to understand each side's intent.
</environment-setup>

<scope>
Modify: only the conflicted files, resolving the markers. Do NOT abort-and-restart unless instructed; do NOT take a wholesale `--theirs`/`--ours` without understanding the hunks.
</scope>

<primary-path>
1. For the COMMON conflict shapes in this migration:
   - **`pyproject.toml` extras** (Wave-1 `[desktop]` vs Wave-2 `[desktop-macos]`): keep BOTH groups; ensure `pynput`/`ptyprocess` in `[desktop]`, `pyobjc-*` in `[desktop-macos]`, and NO pyatspi anywhere (HN-9). Verify with tomllib.
   - **`jarvis/admin/schema.py` / `executor.py`** (Wave-3 op tables): keep the full superset union; the Windows ops are not narrowed; the Unix ops keep pattern-validated argv (HN-11/HN-12).
   - **A consumer call-site** (e.g. a vision tool touched by Wave 2's factory rewire): keep the factory call (`make_*`), not the old literal; preserve the existing DI seam.
   - **A conftest / test file**: keep BOTH the live-orb path AND the skip_ci on the abandoned framework; never delete a regression guard (HN-2).
2. Resolve each hunk by intent (both sides preserved where they are additive siblings).
3. `git add <resolved files>`; complete the merge (`git commit` with the default merge message, no `--no-verify`).
4. Reinstall extras if pyproject changed; run the wave acceptance.
</primary-path>

<fallback-paths>
- If a conflict is genuinely a true overlap (both waves edited the SAME logical line), sequence it: pick the semantically-correct combined result and note the coordination point for the hot-file table. Failure condition: the resolution silently drops one wave's change.
- If you cannot determine intent from the diffs, read the two sub-task prompts' `<scope>`/`<outcome>` to reconstruct it — do not guess.
- If the merge is too tangled to resolve safely, `git merge --abort` and hand back to the operator with the conflict analysis — never force a bad resolution.
</fallback-paths>

<acceptance>
git diff --name-only --diff-filter=U
python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); ext=d['project'].get('optional-dependencies',{}); assert all('pyatspi' not in x for g in ext.values() for x in g)"
python scripts/ci/check_import_clean.py
pytest -m "not skip_ci" --junitxml=report.xml -q; python scripts/ci/assert_min_passed.py report.xml
git log --oneline --merges -1
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-1 — never resolve by rewriting the Windows impl.
- HN-2 — never resolve by deleting a regression guard.
- HN-9 — never re-add pyatspi to pyproject during resolution.
- HN-12/HN-13 — never weaken HMAC/peer-cred during resolution.
- HN-17 — no force-push; complete the merge with hooks intact.
SOFT:
- Prefer additive both-sides resolution; sequence true overlaps.
</hard-rules>

<done-signal>
Final report (5 bullets):
- The conflicted files + each side's intent.
- How each conflict was resolved (both-sides-preserved vs sequenced).
- The wave acceptance result after resolution.
- Hard-rules honored (HN-1/HN-2/HN-9/HN-12/HN-17 quoted).
- Path-taken / any conflict handed back to the operator.
</done-signal>
```

## recovery-worktree-stuck

```prompt
ultrathink
<role>
You recover a stuck git-worktree situation (a worktree that won't create/remove, a stale editable-install pin to a deleted clone, a "works in tests but Jarvis behavior unchanged" restore-trap, or a worktree whose live `import jarvis` points at the wrong path). Your outcome is a clean, correctly-pinned worktree where `import jarvis` resolves to THIS clone and preflight passes — the four-layer restore-trap defused.
</role>

<outcome>
DONE means: the stuck condition is diagnosed (which of the four restore-trap layers: worktree / frontend build / RAM / editable-install pin); the worktree is either repaired or cleanly recreated without losing committed work; `pwsh scripts/preflight.ps1` exits 0; and `python -c "import jarvis; print(jarvis.__file__)"` points at the current worktree clone.
</outcome>

<required-reading>
- `docs/plans/cross-platform-mac-linux/HARD-NEGATIVES.md` HN-17 (no force-push; preserve work) + HN-15 (the matrix is the gate).
- `CLAUDE.md` — the four-layer restore trap (BUG-006 → -014 → -015): worktree + frontend build + RAM + editable-install pin to a deleted clone. Defense: `pwsh scripts/preflight.ps1` + `python -c "import jarvis; print(jarvis.__file__)"`.
- MEMORY: never manually delete `jarvis.lock`; `scripts/preflight.ps1` exit non-zero → fix before coding.
</required-reading>

<environment-setup>
1. `git worktree list` — see all worktrees + their branches + locked state.
2. `python -c "import jarvis; print(jarvis.__file__)"` — does it point at THIS worktree or a deleted/other clone?
3. `git status` in the affected worktree — confirm whether uncommitted work exists.
</environment-setup>

<scope>
Modify: nothing in `jarvis/` for the recovery itself — this is environment repair (worktree + editable-install pin). Commit any in-flight work first.
</scope>

<primary-path>
1. Preserve first: if uncommitted work exists, `git add -A && git commit -m "wip: checkpoint before worktree recovery"` (review the diff; never `git add .` blindly).
2. Diagnose the layer:
   - **Editable-install pin to a deleted/other clone** (the most common): `import jarvis` resolves to a path that is NOT this worktree → re-pin with `pip install -e . --no-deps` from THIS worktree root. Re-check the import path.
   - **Stale worktree registration** (`git worktree add` fails "already exists" / a worktree dir was deleted manually): `git worktree prune`, then re-add: `git worktree add -b <branch> ../sub-agents-outputs/<dir> feat/crossplatform-port`.
   - **Locked worktree**: `git worktree unlock <path>` if you own it; never force-remove one another session is using.
   - **Frontend-build / RAM layer** (UI behavior unchanged after edit): rebuild (`npm run build` in `jarvis/ui/web/frontend/`) + note an app restart is needed (pywebview holds the old bundle in RAM) — relevant only if the sub-task touched the frontend.
3. Run `pwsh scripts/preflight.ps1`; if non-zero, fix the reported issue before declaring done.
4. Confirm `import jarvis` points at this worktree.
</primary-path>

<fallback-paths>
- If a worktree cannot be removed because another live session holds it, leave it and create a fresh one with a distinct name rather than fighting the lock. Failure condition: removing a worktree another session is editing.
- If preflight still fails after re-pinning, read its exact error and address that single issue — do not blanket-reinstall everything. Failure condition: preflight non-zero with an unaddressed reason.
- Never manually delete `jarvis.lock` to "unstick" things (MEMORY) — it breaks the lock-holder sweep.
</fallback-paths>

<acceptance>
git worktree list
python -c "import jarvis; print(jarvis.__file__)"
pwsh scripts/preflight.ps1
git status --porcelain
</acceptance>

<hard-rules>
INVIOLABLE:
- HN-17 — preserve in-flight work (commit it); never force-push; never `git add .` blindly.
- Never manually delete `jarvis.lock`; never force-remove a worktree another session is using.
SOFT:
- Re-pin the editable install from THIS worktree before blaming the code (the restore-trap is usually the pin).
</hard-rules>

<done-signal>
Final report (5 bullets):
- The diagnosed restore-trap layer.
- The checkpoint commit (if work was preserved).
- The repair applied (re-pin / prune+re-add / unlock / rebuild).
- `import jarvis` now points at this worktree + preflight exit 0 (output).
- Hard-rules honored (HN-17 quoted) + path-taken.
</done-signal>
```

