# Cross-Platform Port — macOS + Linux for the Desktop Power-User Feature Set

> Binding master plan. The canonical Architecture Decisions (AD-1..AD-15), Pre-Conditions
> (PC-1..PC-7), wave structure, stakeholder matrix, and Definition of Done (EK-1..EK-6) are
> frozen in [`_FROZEN-DECISIONS.md`](_FROZEN-DECISIONS.md). On any conflict between this
> README and `_FROZEN-DECISIONS.md`, the frozen file wins. Output language: English (repo
> `CLAUDE.md` Output-Language Policy).

---

## North Star

Personal Jarvis runs with its **full desktop power-user feature set on Windows, macOS, and
Linux** — verified by a CI matrix plus one-time live sign-offs — even though the maintainer
owns only a Windows machine. Cloud-first doctrine ([`docs/PHILOSOPHY.md`](../../PHILOSOPHY.md))
makes macOS/Linux the *primary* target, not an afterthought; this plan retires the de-facto
"Windows-only desktop extras" reality and replaces it with an honest, per-feature
cross-platform contract.

---

## Why this exists

The cloud-first doctrine already declares macOS and Linux first-class runtimes, yet six
desktop power-user features ship Windows-only **in implementation**: UI-element-click,
app-launch-by-name, global-hotkey, the Orb overlay, the built-in terminal, and
admin/elevation. None of these are Windows-bound *in principle* — clicking a UI element,
launching an app by name, capturing a hotkey, drawing a translucent overlay, owning a PTY,
and elevating a privileged op all have clean macOS and Linux equivalents (AX/AT-SPI,
`open`/`xdg-open`, `pynput`, transparent surfaces, `ptyprocess`, `pkexec`/Authorization
Services). The real hurdle is **verification without Mac/Linux hardware**: a Windows-only
maintainer cannot run a true end-to-end test of an Orb transparency mask or an elevation
prompt. This plan resolves that hurdle by leading with a CI matrix (the highest-leverage,
currently-missing safety net), proving everything that headless CI *can* prove, and labelling
every GUI/permission feature with an honest `CI-verified` vs `live-verified <date>` badge.

---

## TL;DR — the six features

| Feature | What it is | Windows impl today | macOS target | Linux target | Effort | CI-testable? |
|---|---|---|---|---|---|---|
| **Terminal** | Built-in PTY-backed terminal view | `pywinpty` ConPTY (`jarvis/terminal/pty_manager.py`) | `ptyprocess.PtyProcess` (1:1 method mirror) | `ptyprocess.PtyProcess` | 🟢 | ✅ **Full** — real PTY on ubuntu/macos runners |
| **App-launch** | Launch an app by spoken name | `os.startfile` + App-Paths + `KNOWN_APPS` (`jarvis/plugins/tool/{open_app,app_resolver}.py`) | `open -a` + macOS `KNOWN_APPS` | `xdg-open` / direct exec + Linux `KNOWN_APPS` | 🟢 | ✅ Resolution logic (launch is a live check) |
| **Hotkey** | Global push-to-talk / wake hotkey | `global-hotkeys` (L/R-Alt + refcount, `jarvis/trigger/hotkey.py`) | `pynput` | `pynput` (X11); **Wayland = no-op + log** | 🟡 | 🟡 Logic yes; live capture needs sign-off |
| **UI-element-click** | Click a named/numbered UI element | UIA tree → `Observation`/`UIANode` (`jarvis/vision/uia_tree.py`) | `pyobjc` AX tree (`AXUIElement`) | `pyatspi` AT-SPI bus | 🔴 | 🟡 Role-normalization yes; tree capture needs sign-off |
| **Orb-overlay** | Translucent always-on butler orb | Tk color-key overlay + `SetSystemCursor` swap | Tk `-transparentcolor` (works on macOS) | best-effort transparent surface + tray fallback | 🔴 | 🟡 Headless construct yes; transparency needs sign-off |
| **Admin/elevation** | Run a privileged op (install/service/firewall) | SDDL-ACL named pipe + winget/sc/netsh/winreg/schtasks (`jarvis/admin/`) | Authorization Services / `osascript … with administrator privileges`; `UnixSocketTransport` | `pkexec`/polkit or `sudo`; `UnixSocketTransport` | 🔴 | ❌ **Never** end-to-end (interactive auth) |

Concrete per-feature targets are frozen in AD-8..AD-12.

---

## Architecture Decisions (FROZEN — AD-1 .. AD-15)

| ID | Decision | Rationale |
|---|---|---|
| **AD-1** | **Dual-platform parallel.** Each feature wave delivers macOS and Linux simultaneously, not sequentially. | Doctrine treats both as primary; parallel avoids a second pass over every seam. |
| **AD-2** | **Port all six.** Terminal, App-launch, Hotkey, UI-element-click, Orb-overlay, Admin/elevation — all in scope. No descoping. | Operator chose full scope; the hard features (Orb, Admin) are explicitly included. |
| **AD-3** | **Verification = CI + one-time live sign-off.** Headless CI proves logic + real-PTY; GUI/permission behavior (Orb transparency, global hotkey capture, AX/AT-SPI tree, elevation prompt) gets one manual sign-off on a real/borrowed/rented device per feature. Docs carry an honest per-feature badge. | A Windows-only maintainer cannot run true GUI/permission E2E; CI-only would ship untested GUI paths silently. |
| **AD-4** | **CI matrix is Wave 0 (blocking).** GitHub Actions matrix on `ubuntu-latest` + `macos-latest` + `windows-latest` running `pip install -e ".[dev]"`, `ruff`, `mypy`, `pytest -m "not skip_ci"`, an **import-cleanliness gate** (`python -c "import jarvis"` on Linux/macOS proving no module-scope `pywin32`/`winreg`/`global_hotkeys` import), and a **minimum-passed-count floor**. There is **no functional CI today** — highest-leverage action in the plan. Copy the 3-OS matrix shape from `.github/workflows/cross-runner-hash.yml:76-86`. | Without CI there is no safety net for a cross-platform claim; the min-passed-floor stops a mass-skip regression from passing as green. |
| **AD-5** | **One shared `jarvis/platform/` capability module.** `detect_platform() -> "win32"\|"darwin"\|"linux"` + a cached, frozen `Capabilities` dataclass (`has_hotkey`, `has_ax_tree`, `has_overlay`, `has_pty`, `has_elevation`, `display_present`, `is_wayland`, `ax_permission_granted`, …). All six ports read from here; they do NOT each re-detect. The wizard renders one "what works on your box" snapshot. | Single detection authority prevents drift across six ports and gives onboarding one truth source. |
| **AD-6** | **Uniform seam pattern.** Every port = `Protocol` (in or alongside `jarvis/core/protocols.py`) + per-OS implementation + `sys.platform` factory + **graceful null-fallback**. No `sys.platform` branch ever raises; an unavailable capability logs a clear English message and degrades. | Enforces the "zero silent drops" contract (AD-OE6) and keeps all six ports structurally identical. |
| **AD-7** | **Permanent additive coexistence.** Windows implementations stay **untouched** (grandfathered; BUG-009/012/014/030 fixes live in them). macOS/Linux are added as siblings behind the seam. No rewrite of the Windows path. | The battle-tested Windows impls carry hard-won bug fixes; rewriting them to a "unified" impl re-opens closed bugs. |
| **AD-8** | **Hotkey: dual backend.** Windows keeps `global-hotkeys` (the L/R-Alt + refcount logic the bug fixes depend on). macOS/Linux use `pynput`. Wayland: global hotkey unavailable by OS design → no-op + log, lean on wake-word. macOS: detect "registered but zero events" → guide the user to grant Input-Monitoring/Accessibility. | `pynput` is the only cross-platform global-hotkey lib; Wayland forbids global capture by design — degrade, do not fake it. |
| **AD-9** | **Terminal: `ptyprocess` (Unix).** `ptyprocess.PtyProcess` mirrors all five `pywinpty` methods 1:1 (spawn/read/write/setwinsize/terminate/isalive/exitstatus) into the existing daemon-thread read-loop with no async rewrite. Normalize str↔bytes at the backend seam. `shells.py` gets a Unix branch (`$SHELL` → `/etc/shells` → `which bash/zsh/fish`). **Fully real-PTY CI-testable** on ubuntu/macos. | Method-for-method parity means the read-loop is untouched; terminal is the standout verification win — fully provable in CI. |
| **AD-10** | **UI-element-click: AX + AT-SPI.** macOS via `pyobjc` (`AXUIElement` tree → `Observation`/`UIANode`); Linux via `pyatspi` (AT-SPI bus). Both satisfy the `VisionSource` Protocol (`protocols.py:419`) at the 6 consumer sites. Native AX/AT-SPI roles are **normalized into the canonical UIA role vocabulary** (`pruning.py:51`, `_CLICKABLE_UIA_ROLES`) so model prompt + tests stay platform-agnostic. Degrades to the pixel-click path when the native tree is empty. | One canonical role vocabulary keeps the model prompt and tests OS-agnostic; the existing pixel path is the universal fallback. |
| **AD-11** | **Orb: `OverlaySurface` abstraction + 3-tier visual ladder.** `TkColorKeyOverlay` (Windows + macOS — `-transparentcolor` works on both) / best-effort transparent surface on Linux+compositor / `TrayOnlySurface` driving the already-cross-platform pystray tray (`jarvis/ui/tray.py`). The `SetSystemCursor` swap stays Windows-only (no OS equivalent; already a no-op off-Windows). NOTE: framework conflict (Tk in `jarvis/ui/orb/` vs PySide6 in `OS-Level/src/overlay/`) must be resolved in Wave 0. | A graceful 3-tier ladder guarantees *some* presence everywhere; the tray already works cross-platform as the floor. |
| **AD-12** | **Admin/elevation: full port behind `Elevator` + `AdminTransport` seams.** Reuse the transport-free HMAC/envelope/Pydantic-argv layer (`ipc.py:65-262`) as-is (preserve no-`shell=True`, pattern-validated argv). New per-OS op vocabulary (macOS `brew`/`launchctl`/protected-path via Authorization Services or `osascript`; Linux `apt`/`systemctl`/`ufw`/protected-path via `pkexec`/polkit or `sudo`). `UnixSocketTransport` (0700 socket in `$XDG_RUNTIME_DIR`, `SO_PEERCRED`/`LOCAL_PEERCRED`) replaces the Windows SDDL-ACL named pipe. `NullElevator` = headless/no-auth fallback. Never CI-testable end-to-end → AD-3 sign-off + heavy fake-transport unit tests. | Elevation is the highest-risk surface; the security core (HMAC + validated argv) is reused untouched, only the transport + op vocabulary change per OS. |
| **AD-13** | **Permission-UX is detect-and-degrade.** macOS AX/Input-Monitoring and Linux AT-SPI-bus: probe at first use; if missing, log an English onboarding message + fall back, never silently empty, never hard-block. | Avoids both the "said it worked, nothing happened" class and a hard-block that breaks the always-degrade contract. |
| **AD-14** | **Dependency grouping.** `pynput` + `ptyprocess` → `[desktop]` extras (no platform marker; `ptyprocess` marked `sys_platform != 'win32'`). `pyobjc-framework-{Quartz,ApplicationServices,Accessibility}` → new `[desktop-macos]`, `sys_platform == 'darwin'`. Linux `pyatspi` is **NOT on PyPI** (GObject-Introspection, distro-packaged: `apt install python3-pyatspi gir1.2-atspi-2.0`) → documented system prerequisite + a `capabilities.has_ax_tree` runtime probe, NOT a pip extra. Mirror `pyproject.toml:99-110`. | Keeps the base install GPU/OS-free per doctrine; `pyatspi` simply cannot be a pip dep, so it is a probed prerequisite. |
| **AD-15** | **`KNOWN_APPS` whitelist is platform-conditional.** Selected by `sys.platform` (Windows names today; `Safari`/`Terminal`/`firefox`/`nautilus`/`gnome-calculator` etc. per OS), keeping the anti-STT-hallucination gate intact with the PATH/URL/path escape hatches. | The whitelist exists to gate STT hallucinations; it must carry correct per-OS names without losing the escape hatches. |

---

## Pre-Conditions Verified (Phase 2, with evidence)

The headline finding is **PC-1 🔴: there is no functional CI today** — only supply-chain
workflows. Everything else is green or a known, scoped yellow.

| # | Pre-condition | Verdict | Evidence |
|---|---|---|---|
| **PC-1** | Functional CI exists | 🔴 **NONE** | `.github/workflows/` has 5 files; none run pytest/ruff/mypy. 3-OS matrix precedent: `cross-runner-hash.yml:76-86` |
| **PC-2** | Nothing crashes on Mac/Linux today | 🟢 | All Windows deps lazy-imported + guarded: `app_resolver.py:24`, `hotkey.py:241`, `pty_manager.py:71`, `uia_tree.py:188` |
| **PC-3** | Clean abstraction seams exist | 🟢 | `VisionSource` Protocol `protocols.py:419`; `HotkeyTrigger`; `PtyManager`; `AdminClient` injectable transport `client.py:80` |
| **PC-4** | Port libs available | 🟢/🟡 | `pynput`, `ptyprocess`, `pyobjc-*` on PyPI; `pyatspi` distro-only (apt) |
| **PC-5** | Tests run cross-platform | 🟢 | `skip_ci` marker exists (`pyproject.toml:283`); conftests have no Windows assumption; overlay tests already headless (`tests/overlay/conftest.py:21` `QT_QPA_PLATFORM=offscreen`) |
| **PC-6** | Orb framework identity | 🟡 | CONFLICT: Tk (`jarvis/ui/orb/`) vs PySide6 (`OS-Level/src/overlay/`); stale `ui.orb` path in `conftest.py:5` — resolve in Wave 0 |
| **PC-7** | Admin ops are Windows-native | 🟡 | All 13 ops (`schema.py:175-209`) are winget/sc/netsh/winreg/schtasks → "port all" means a NEW Linux/macOS op vocabulary; elevation glue (`launcher.py`) is currently dormant (never auto-called) |

---

## Stakeholder-Conflict Matrix (resolved)

| Axis | Decision | Why |
|---|---|---|
| Windows-tempo vs cross-platform | **Cross-platform, additively** (AD-7) | Doctrine makes Mac/Linux primary; keep Windows untouched to avoid re-opening closed bugs |
| Code-cleanliness vs fallback-safety | **Permanent coexistence** (AD-7) | Battle-tested Windows impls stay; unify only the seam, not the impls |
| Verification rigor vs no-hardware reality | **CI + one-time live sign-off + honest labels** (AD-3) | Full real-hardware E2E is impossible for a Windows-only maintainer; CI-only ships untested GUI paths |
| Feature-completeness vs risk | **Port all, sequenced easy→hard** (AD-2 + waves) | Operator chose full scope; sequencing puts the CI-provable wins first and the un-CI-able Admin last |
| Security vs convenience (Admin) | **Preserve the HMAC/validated-argv core; NullElevator default** (AD-12) | Elevation is the highest-risk surface; never weaken injection defenses to add convenience |

---

## Wave structure

Each wave links to its detailed brief (authored separately). Wave 0 is a hard prerequisite:
**nothing else may merge until the CI matrix is green.**

| Wave | Goal (one line) | Features | Parallelism | Brief |
|---|---|---|---|---|
| **0 — Foundation** (BLOCKING) | Shared `jarvis/platform/` capability module + the GitHub Actions Win+Mac+Linux pytest/ruff/mypy matrix (import-cleanliness gate + min-passed-floor); resolve the Orb-framework conflict (PC-6); clean the stale `ui.orb` conftest path. | Platform module + CI matrix | Single blocking wave | [`WELLE-0-foundation.md`](WELLE-0-foundation.md) |
| **1 — Easy / CI-provable** | Port the three logic-heavy, CI-provable features. | Terminal (Unix-PTY via `ptyprocess` + Unix shell discovery), App-launch (`open`/`xdg-open` + platform `KNOWN_APPS`), Hotkey (`pynput` dual-backend) | 3 parallel worktrees, all independent | [`WELLE-1-easy-ports.md`](WELLE-1-easy-ports.md) |
| **2 — Permission/GUI-heavy** | Port the two native-tree / surface features behind their seams. | UI-element-click (macOS AX + Linux AT-SPI behind `VisionSource`), Orb (`OverlaySurface` ladder: Mac transparent, Linux best-effort + tray fallback) | 2 parallel worktrees | [`WELLE-2-gui-permission.md`](WELLE-2-gui-permission.md) |
| **3 — Admin/elevation** | Port the security-sensitive elevation path. | `Elevator` + `AdminTransport` seams; per-OS op vocabulary; `UnixSocketTransport` + peer-cred; supersede ADR-0001 with a new ADR-0020; `NullElevator` headless fallback | Single sensitive wave | [`WELLE-3-admin.md`](WELLE-3-admin.md) |
| **4 — Hardening + sign-off** | The one-time live sign-off round + honest labelling + benchmark. | Live sign-off on real macOS + Linux for the AD-3 GUI/permission features; honest per-feature verification labels in docs; run the JARVIS-20 cross-platform benchmark | Final wave | [`WELLE-4-hardening.md`](WELLE-4-hardening.md) |

---

## Definition of Done (EK-1 .. EK-6)

Each criterion below carries an **honest status** + an **evidence pointer** (Wave 4,
sub-task 4.6). Per the AD-3 honesty contract: this environment is **Windows-only**
and **nothing has been pushed**, so the CI matrix is *configured but has not run*
and all macOS/Linux **live** GUI/permission behavior is `unverified-on-real-desktop`.
That is the truthful close-out state, not a failure — the labels tell the truth.

- **EK-1** — CI matrix green on Windows + macOS + Linux for `ruff` + `mypy` + `pytest -m "not skip_ci"`, with the import-cleanliness gate and min-passed-floor enforced.
  - **Status: `CI-configured — first green run pending push`** (not yet "CI-verified"). The matrix is fully configured; no push has occurred so GitHub Actions has not run it.
  - **Evidence:** [`.github/workflows/ci.yml`](../../../.github/workflows/ci.yml) — 3-OS matrix (`ubuntu-latest` + `macos-latest` + `windows-latest`), BLOCKING import-cleanliness gate + min-passed floor (`scripts/ci/check_import_clean.py`, `scripts/ci/assert_min_passed.py`), `ruff`/`mypy` report-only against the pre-existing backlog. Locally on Windows: `check_import_clean.py` exits 0 (534 files scanned) and the targeted logic suites pass (e.g. 230 vision/admin tests green).
- **EK-2** — Each of the 6 features has a per-OS implementation behind its seam, selected by the shared `jarvis/platform/` factory, degrading to a logged no-op when the capability is absent.
  - **Status: ✅ verified-on-windows (factory selection) — the six factories exist and select correctly on this host.**
  - **Evidence (the six factories):** `make_pty_backend` (`jarvis/terminal/backend.py`), `resolve_app_launch_target` (`jarvis/plugins/tool/app_resolver.py`), `make_hotkey_backend` (`jarvis/trigger/backends/__init__.py`), `make_ui_tree_source` (`jarvis/vision/tree_factory.py`), `make_overlay_surface` (`jarvis/overlay/surface.py`), `make_admin_transport` + `make_elevator` (`jarvis/admin/transport.py`, `jarvis/admin/elevator.py`). All read `jarvis/platform/` (`detect_platform` + `Capabilities`). `signoff_probe.py` confirms each selects the Windows impl on this host.
- **EK-3** — Every new OS seam ships a `tests/fakes/` fake and unit tests; no `unittest.mock`.
  - **Status: ✅ verified-on-windows — fakes + tests present and green.**
  - **Evidence (the fakes):** `tests/fakes/fake_pty_backend.py`, `fake_hotkey_backend.py`, `fake_capabilities.py`, `fake_ax_api.py`, `fake_atspi.py`, `fake_overlay_surface.py`, `fake_admin_transport.py`, `fake_elevator.py`, `fake_global_hotkeys.py`. Unit tests under `tests/unit/vision/` (`test_tree_factory.py`, `test_role_map.py`, `test_ax_tree.py`, `test_atspi_tree.py`), `tests/unit/admin/` (`test_elevator.py`, `test_transport_seam.py`, `test_unix_socket_transport.py`, `test_schema_unix.py`), `tests/overlay/`, `tests/unit/trigger/`.
- **EK-4** — Terminal + App-launch resolution + Hotkey logic are CI-verified on the ubuntu+macos runners (terminal via a real PTY).
  - **Status: verified-on-windows (Windows real-PTY + resolution) · `CI-configured` for the Mac/Linux real-PTY (pending push).**
  - **Evidence:** `signoff_probe.py --feature terminal` ran a real PTY echo round-trip on `WinptyBackend` (no mojibake); `--feature applaunch` resolved calculator/terminal/code + routed a hallucinated name to a refusable target. The ubuntu/macos `ci.yml` legs run the real-PTY test; the first green run is pending the initial push.
- **EK-5** — The GUI/permission features (UI-element-click, Orb, Hotkey live capture) each carry a one-time live sign-off note (`live-verified <date> on <device>`) or an explicit `unverified-on-real-desktop` label.
  - **Status: ✅ satisfied via honest `unverified-on-real-desktop` labels — no `live-verified` rows yet (Windows-only environment).**
  - **Evidence:** [`SIGNOFF-LOG.md`](SIGNOFF-LOG.md) (every GUI/permission row labelled), [`LIVE-SIGNOFF-CHECKLIST.md`](LIVE-SIGNOFF-CHECKLIST.md) (the operator checklist), [`JARVIS-20-RESULTS.md`](JARVIS-20-RESULTS.md) (per-scenario verdicts, zero `fail`), and the operator aide [`scripts/crossplatform/signoff_probe.py`](../../../scripts/crossplatform/signoff_probe.py).
- **EK-6** — ADR-0001 superseded by a new ADR; CLAUDE.md + README capability matrix updated to reflect real cross-platform status; no module hard-imports a Windows-only package at module scope.
  - **Status: ✅ verified-on-windows.**
  - **Evidence:** [`docs/adr/0020-cross-platform-elevation.md`](../../adr/0020-cross-platform-elevation.md) supersedes ADR-0001; `README.md` capability matrix + "Verification status" subsection and `CLAUDE.md` "Cross-platform desktop features" table updated (both carry the honest badges + the AD-14 "do not pip-install pyatspi" guidance); import-cleanliness gate `scripts/ci/check_import_clean.py` exits 0 on Windows (no module-scope Windows-only import) and is BLOCKING on every `ci.yml` leg.

---

## Hot-file ownership

To let parallel coding-agents run without collision, each wave owns a disjoint file set.
A file appearing in two waves is a coordination point — sequence those edits, never let two
worktrees edit it simultaneously.

| Wave | Owns / touches | Notes |
|---|---|---|
| **0** | `jarvis/platform/` (**new** package: `detect.py`, `capabilities.py`, `probes.py`); `.github/workflows/ci.yml` (**new**); `tests/overlay/conftest.py` (clean stale `ui.orb` path, PC-6) | Blocking. The Orb-framework decision (PC-6) is recorded here; it constrains Wave 2. |
| **1** | `jarvis/terminal/pty_manager.py`, `jarvis/terminal/shells.py`, `jarvis/terminal/backend.py` (**new** Unix backend seam); `jarvis/plugins/tool/open_app.py`, `jarvis/plugins/tool/app_resolver.py`; `jarvis/trigger/hotkey.py` + per-OS hotkey backends (**new**) | 3 disjoint worktrees: terminal, app-launch, hotkey. No shared file. |
| **2** | `jarvis/vision/uia_tree.py`, `jarvis/vision/ax_tree.py` (**new** macOS), `jarvis/vision/atspi_tree.py` (**new** Linux), `jarvis/vision/tree_factory.py` (**new**); `jarvis/overlay/` (**new** `OverlaySurface` seam) + `OS-Level/src/overlay/` (per PC-6 outcome) | Vision factory + overlay surface are the two parallel tracks. `pruning.py` role vocab is read-only here (canonical, do not edit roles). |
| **3** | `jarvis/admin/transport.py` (**new** `AdminTransport` + `UnixSocketTransport`), `jarvis/admin/elevator.py` (**new** `Elevator`/`NullElevator`); `docs/adr/0020-*.md` (**new**, supersedes ADR-0001) | Reuses `jarvis/admin/ipc.py` and `schema.py` as-is; only the transport + op vocabulary are new. |
| **4** | Docs only: per-feature verification labels across `docs/` + this README's capability matrix; CLAUDE.md capability matrix; JARVIS-20 benchmark run artifacts | No production-code edits — labelling + benchmark + sign-off notes. |

---

## Verification strategy — the honest ladder

Verification climbs a five-rung ladder. Each rung proves strictly more than the last; the
top rung (real hardware) is reachable only via a one-time borrowed/rented sign-off. The
point of being explicit is that **a green CI run is not a license to claim a GUI feature
works on a real desktop** — only the matching live sign-off is.

1. **(a) Fakes + unit tests.** Every new seam ships a `tests/fakes/` fake (no `unittest.mock`, per EK-3) and unit tests for the platform-independent logic: shell discovery, `KNOWN_APPS` resolution, AX/AT-SPI → UIA role normalization, HMAC/argv validation, capability detection.
   *Proves:* logic correctness. *Cannot prove:* that the real OS API behaves as the fake assumes.

2. **(b) GitHub Actions ubuntu + macos + windows matrix** (Wave 0, AD-4). Runs `ruff`, `mypy`, `pytest -m "not skip_ci"`, the import-cleanliness gate, and the min-passed-floor on all three OSes.
   *Proves:* the package imports cleanly with no Windows-only module-scope import; logic tests pass on real Linux/macOS Python; **terminal works against a real PTY** on the runners.
   *Cannot prove:* anything needing a display, a permission grant, or an interactive prompt — runners are headless.

3. **(c) The project's own €5 Linux VPS as a real-Linux box.** Beyond the ephemeral runner, the project's own VPS is a persistent, real Linux environment for exercising the headless paths (terminal, app-launch resolution, capability probes, AT-SPI presence/absence detection) under a real desktop-less Linux.
   *Proves:* real-Linux behavior of the degrade paths and the `display_present`/`is_wayland`/`has_ax_tree` probes. *Cannot prove:* the GUI-present Linux path (no compositor on a headless VPS).

4. **(d) A macOS runner + one borrowed-Mac sign-off.** The `macos-latest` runner covers macOS logic + PTY; the one-time borrowed/rented Mac sign-off (Wave 4) covers the GUI/permission behavior the runner cannot: Orb transparency, global-hotkey live capture, the AX tree under a granted Accessibility permission, the elevation prompt.
   *Proves:* the real GUI/permission behavior, once, on a dated device. *Cannot prove:* ongoing regression safety — hence the honest label, not a "verified forever" claim.

5. **(e) Per-feature honesty labels** (EK-5). Every GUI/permission feature carries either `live-verified <date> on <device>` or an explicit `unverified-on-real-desktop` badge in the docs. No feature is allowed to imply a verification rung it did not reach.

### What each rung can and cannot prove, per feature

| Feature | Highest rung reachable | Honest verdict |
|---|---|---|
| **Terminal** | (b)/(c) real PTY in CI | **Fully CI-verified** — no live sign-off needed |
| **App-launch** | (b) resolution in CI; (d) actual launch | Resolution CI-verified; launch is a light live check |
| **Hotkey** | (a)/(b) logic in CI; (d) live capture | Logic CI-verified; **live capture needs sign-off** (Wayland is no-op by design) |
| **UI-element-click (AX/AT-SPI)** | (a)/(b) role normalization in CI; (d) live tree | Normalization CI-verified; **live tree + permission needs sign-off** |
| **Orb-overlay** | (a)/(b) headless construct + offscreen Qt; (d) live transparency | Construction CI-verified; **transparency mask needs sign-off** |
| **Admin/elevation** | (a) fake-transport unit tests only | **Never CI-testable end-to-end** (interactive auth) → sign-off + heavy fake-transport tests |

---

## Pointers

- [`_FROZEN-DECISIONS.md`](_FROZEN-DECISIONS.md) — canonical AD/PC/wave/EK source (this README must not contradict it).
- [`HARD-NEGATIVES.md`](HARD-NEGATIVES.md) — what this plan will *not* do (anti-scope).
- [`ANTI-PATTERNS.md`](ANTI-PATTERNS.md) — cross-platform-specific failure modes to avoid.
- [`EXECUTION-PLAYBOOK.md`](EXECUTION-PLAYBOOK.md) — operator manual: zero hand-typed git/PowerShell, paste-only prompts.
- [`PROMPTS.md`](PROMPTS.md) — the per-wave coding-agent prompt library.
- [`PHASE-TRACKER.html`](PHASE-TRACKER.html) — self-contained cockpit with copy-paste buttons + localStorage progress.
- Wave briefs: [`WELLE-0-foundation.md`](WELLE-0-foundation.md) · [`WELLE-1-easy-ports.md`](WELLE-1-easy-ports.md) · [`WELLE-2-gui-permission.md`](WELLE-2-gui-permission.md) · [`WELLE-3-admin.md`](WELLE-3-admin.md) · [`WELLE-4-hardening.md`](WELLE-4-hardening.md)
- [`CLAUDE.md`](../../../CLAUDE.md) — repo guidance + cloud-first doctrine summary + anti-pattern register.
- [`docs/PHILOSOPHY.md`](../../PHILOSOPHY.md) — the binding cloud-first doctrine this plan serves.
