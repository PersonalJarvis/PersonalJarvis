# WELLE-4 — Hardening + live sign-off

> Canonical decisions: `_FROZEN-DECISIONS.md` (AD-3 verification = CI + one-time
> live sign-off + honest per-feature labels, EK-5 sign-off notes, EK-6 docs +
> capability matrix updated). This is the closing wave: it converts the
> CI-green-but-GUI-unverified state from Waves 1-3 into an honestly-labelled,
> live-verified release.

---

## Goal

Discharge the human-in-the-loop half of the verification bar. Waves 1-3 prove all
six ports green in CI, but four behaviors **cannot** be proven on a headless
runner (AD-3): a real AX/AT-SPI accessibility tree captured from a live app, the
Orb's actual transparency, global-hotkey *capture* (keys arriving from the OS),
and an elevation prompt. Wave 4 runs **one** sign-off pass on a real (or
borrowed/rented) macOS box and a real Linux desktop, records an honest
per-feature verdict (`live-verified <date> on <device>` vs
`unverified-on-real-desktop`) into the plan docs, the README capability matrix,
and CLAUDE.md, and runs the JARVIS-20 cross-platform benchmark (the scenario
file `JARVIS-20-CROSSPLATFORM.md` is authored by a separate agent; this wave
*executes* it and records results). No new feature code lands here — only the
verification harness, the sign-off log, and the doc truth-up. This wave gates
**EK-5** and the documentation half of **EK-6**.

---

## Sub-tasks

### 4.1 — Live sign-off checklist + per-feature verification harness

- **Create:** `docs/plans/cross-platform-mac-linux/LIVE-SIGNOFF-CHECKLIST.md`,
  `scripts/crossplatform/signoff_probe.py` (a guided, mostly-manual probe runner
  the operator runs on each real device).
- **Approach:**
  - The checklist enumerates exactly the AD-3 GUI/permission behaviors that CI
    cannot reach, one row per (feature × OS), each with: the precise manual step,
    the expected observation, and a `PASS`/`FAIL`/`N/A` field:
    - **UI-element-click (macOS):** grant Accessibility (System Settings ›
      Privacy & Security › Accessibility), run `signoff_probe.py --feature ax`,
      confirm the foreground app's `AXTreeSource` (`jarvis/vision/ax_tree.py`)
      returns non-empty `UIANode`s with canonical roles, then confirm a
      `click_element` by name lands. Verify the **degrade**: revoke the grant,
      confirm the English onboarding message fires and the loop falls back to the
      pixel path (AD-13).
    - **UI-element-click (Linux):** `apt install python3-pyatspi gir1.2-atspi-2.0`,
      ensure the AT-SPI bus is up, confirm `AtspiTreeSource`
      (`jarvis/vision/atspi_tree.py`) returns a non-empty tree; then confirm the
      bus-unavailable degrade message + pixel fallback.
    - **Orb (macOS):** launch the desktop app, confirm `TkColorKeyOverlay`
      (`jarvis/overlay/surface.py`) renders a *transparent* orb (no magenta box).
    - **Orb (Linux):** confirm `LinuxBestEffortOverlay` transparency on a
      compositor, and confirm the `TrayOnlySurface` fallback shows a state-colored
      tray icon on Wayland / non-compositing.
    - **Hotkey capture (macOS):** grant Input-Monitoring, confirm the `pynput`
      backend (`jarvis/trigger/backends/pynput.py`) actually receives the
      configured combo; confirm the "registered but zero events → grant
      Input-Monitoring" detection fires when the grant is missing (AD-8).
    - **Hotkey capture (Linux X11):** confirm capture; on Wayland confirm the
      logged no-op (AD-8).
    - **Admin/elevation (macOS):** confirm `MacAuthElevator` raises the
      Touch-ID/password sheet and a `brew`/`launchctl` op completes through the
      `UnixSocketTransport` peer-cred path; confirm `NullElevator` refusal on a
      box with no auth.
    - **Admin/elevation (Linux):** confirm `PolkitElevator` (pkexec) raises the
      polkit dialog and an `apt`/`systemctl`/`ufw` op completes; confirm the
      `SudoElevator` fallback and the `NullElevator` headless refusal.
  - `signoff_probe.py` is a thin operator aide — it constructs each seam via its
    factory (`make_ui_tree_source`, `make_overlay_surface`, `make_hotkey_backend`,
    `make_admin_transport`/`make_elevator`), runs the live action, and prints what
    the operator should observe. It does **not** attempt to automate the
    permission prompt (impossible) — it brackets the manual step.
- **Acceptance criteria:**
  - `test -f docs/plans/cross-platform-mac-linux/LIVE-SIGNOFF-CHECKLIST.md` exits 0 and the file has one row per (feature × {macOS, Linux}) for the four GUI/permission behaviors.
  - `python scripts/crossplatform/signoff_probe.py --list` runs on any OS and prints the probe catalog without raising (it only *acts* on the matching OS).
  - `ruff check scripts/crossplatform/` clean.

### 4.2 — Execute the sign-off + record the per-feature verdict log

- **Create:** `docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md` (the dated,
  device-attributed results of the 4.1 run).
- **Approach:**
  - The operator runs the 4.1 checklist on a real macOS device and a real Linux
    desktop (owned, borrowed, or a rented cloud Mac / a Linux desktop VM with a
    GPU-less compositor). Each row gets a verdict line:
    `AX tree (macOS): live-verified 2026-06-?? on Mac mini M4 (macOS 15.x)` or
    `Orb transparency (Linux/Wayland): unverified-on-real-desktop — tray fallback
    confirmed only`.
  - Any behavior that could not be reached (no Wayland box available, no rented
    Mac) is recorded honestly as `unverified-on-real-desktop` with the reason —
    this is the AD-3 honesty contract, not a failure. The plan still ships; the
    label tells the truth.
- **Acceptance criteria:**
  - `test -f docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md` exits 0.
  - Every (feature × OS) GUI/permission row from 4.1 has either a `live-verified <date> on <device>` or an `unverified-on-real-desktop` line — no row left blank (`grep -c "live-verified\|unverified-on-real-desktop" SIGNOFF-LOG.md` ≥ the row count).

### 4.3 — Write honest per-feature labels into the plan docs + README capability matrix

- **Modify:** `README.md` (the "What runs where (platform capability matrix)"
  table at `README.md:64-79`), `docs/plans/cross-platform-mac-linux/README.md`
  (the TL;DR six-feature table at `README.md:41-48`).
- **Approach:**
  - The README matrix currently says UI-element-click, app-launch-by-name,
    global-hotkey, Orb, ConPTY terminal, and UAC admin are Windows-only
    (`README.md:75-77` show "—" for Linux/macOS). After Waves 1-3 ship and 4.2
    records the verdicts, update those rows to reflect reality, carrying the
    honest badge:
    - Terminal, app-launch-by-name → `✅ CI-verified` for Linux/macOS (EK-4, no
      live sign-off needed — fully provable in CI).
    - UI-element-click, Orb, global-hotkey → the 4.2 verdict
      (`✅ live-verified <date>` or `🟡 unverified-on-real-desktop`).
    - Admin/elevation → the 4.2 verdict (and note "never CI-E2E by design").
  - Rewrite the prose paragraph at `README.md:79` so it no longer claims those
    six are Windows-only — the new truth is "cross-platform behind a seam, with
    per-feature verification badges". Update the cross-platform plan's TL;DR table
    "CI-testable?" column to match the SIGNOFF-LOG.
  - Add a short "Verification status" subsection linking to `SIGNOFF-LOG.md` so a
    reader can audit every claim.
- **Acceptance criteria:**
  - `grep -n "Global-hotkey wake, Orb overlay" README.md` no longer shows "— | — | ✅" (the Linux/macOS cells now carry a badge, not a dash).
  - `grep -n "CI-verified\|live-verified\|unverified-on-real-desktop" README.md` matches in the capability-matrix section.
  - `npm run build` is **not** required (docs only); `python -c "import jarvis"` still clean.

### 4.4 — Update CLAUDE.md cross-platform status + supersession pointers

- **Modify:** `CLAUDE.md` (the "Cloud-First Philosophy" framing + the Windows-
  specifics section + the AP table / pointers).
- **Approach:**
  - CLAUDE.md's "Windows specifics" and "Optional power-user extras" framing
    currently treats Terminal/Hotkey/Orb/UI-element-click/Admin as Windows-only
    power-user extras. Add a paragraph (or a small table) recording that these are
    now cross-platform behind the `jarvis/platform/` seam, with the per-feature
    verification status pointing at `SIGNOFF-LOG.md`, and reference ADR-0020
    (which supersedes ADR-0001) and the `jarvis/platform/` capability module
    (AD-5).
  - Note the new dependency-grouping reality (AD-14): `pynput`+`ptyprocess` in
    `[desktop]`, `pyobjc-*` in `[desktop-macos]`, `pyatspi` as a distro
    prerequisite — so a future agent does not "fix" the missing pyatspi pip dep.
  - Keep the doctrine intact: the base €5-VPS install still ships none of these
    desktop extras; they remain opt-in and degrade gracefully (the new labels just
    say *which* of them now work on Mac/Linux, not that they are required).
- **Acceptance criteria:**
  - `grep -n "ADR-0020\|jarvis/platform\|SIGNOFF-LOG" CLAUDE.md` matches.
  - `grep -n "pyatspi" CLAUDE.md` matches (the "do not pip-install pyatspi" guidance is recorded, defending AD-14).
  - The doctrine's "base install boots on a headless `python:3.11-slim`" claim is unchanged and still true (no new base dependency was added — all ports are extras-gated).

### 4.5 — Run the JARVIS-20 cross-platform benchmark + record results

- **Reference (authored elsewhere):** `docs/plans/cross-platform-mac-linux/JARVIS-20-CROSSPLATFORM.md`
  (the 20-scenario benchmark, written by a separate agent — this wave **runs** it,
  does not author it).
- **Create:** `docs/plans/cross-platform-mac-linux/JARVIS-20-RESULTS.md` (the
  per-OS scored results).
- **Approach:**
  - Execute the 20 scenarios on each available platform: the maintainer's Windows
    box (baseline), the macOS device, and the Linux desktop used in 4.2. Each
    scenario exercises one of the six ports end-to-end (e.g. "open the built-in
    terminal and run `echo` → assert output", "launch the default browser by
    name", "register the PTT hotkey and capture a press", "click a named button
    in the foreground app", "show the orb / tray", "run a no-op privileged op
    through the elevation prompt").
  - Record a score per (scenario × OS): `pass` / `degraded-as-designed` (e.g.
    Wayland hotkey no-op, headless tray fallback) / `fail`. A `degraded-as-designed`
    is a **pass** for the AD-6 contract (graceful degrade) — the results table
    distinguishes it from a `fail` (a crash or silent drop, which violates
    AD-OE6 and must be fixed before close-out).
  - Cross-link the results to the SIGNOFF-LOG so a reader sees both the manual
    sign-off and the scenario benchmark for each feature.
- **Acceptance criteria:**
  - `test -f docs/plans/cross-platform-mac-linux/JARVIS-20-RESULTS.md` exits 0 with a row per scenario per available OS.
  - Zero `fail` rows that represent a crash or silent drop (a `fail` here is a release blocker per AD-OE6); every non-pass is explicitly `degraded-as-designed` with the reason.
  - The results file references `JARVIS-20-CROSSPLATFORM.md` as its scenario source.

### 4.6 — Close-out: confirm the full EK gate

- **Modify:** `docs/plans/cross-platform-mac-linux/README.md` (mark the
  Definition-of-Done EK-1..EK-6 checklist complete with evidence links).
- **Approach:** Walk EK-1..EK-6 and attach the proving artifact to each:
  EK-1 → the green `ci.yml` run; EK-2 → the six factories
  (`make_pty_backend`, app-launch resolver, `make_hotkey_backend`,
  `make_ui_tree_source`, `make_overlay_surface`, `make_admin_transport`/
  `make_elevator`); EK-3 → the `tests/fakes/` fakes list; EK-4 → the CI terminal
  real-PTY test; EK-5 → `SIGNOFF-LOG.md`; EK-6 → ADR-0020 + the README/CLAUDE.md
  diffs + the import-cleanliness gate.
- **Acceptance criteria:**
  - `pytest -m "not skip_ci" -q` green on all three CI legs (re-confirm EK-1 after the doc-only Wave-4 changes).
  - `python scripts/ci/check_import_clean.py` exits 0 (EK-6 import-cleanliness still holds after all six ports merged).
  - The plan README's DoD section shows EK-1..EK-6 each checked with an evidence pointer.

---

## Parallelism

This wave is mostly **serial and human-gated** — there is little to parallelize
because the doc truth-up (4.3/4.4/4.6) depends on the sign-off results (4.2) which
depend on the harness (4.1) and on Waves 1-3 being merged. The natural split:

- **One worktree, sequential:** 4.1 (harness) → 4.2 (run the manual sign-off on
  real devices) → 4.3 + 4.4 (doc labels, can be done together once 4.2 is in) →
  4.6 (close-out).
- **Independent side-track:** 4.5 (run JARVIS-20) can proceed in parallel with
  4.3/4.4 once 4.2's devices are available — it shares the same hardware session
  but produces a separate results file.

The hardware constraint is the real bottleneck, not code coupling: 4.2 and 4.5
both need access to a real macOS box and a real Linux desktop. If only one is
available, record the missing one as `unverified-on-real-desktop` (AD-3 honesty)
rather than blocking the wave.

## EK acceptance gate

This wave satisfies **EK-5** (every GUI/permission feature — UI-element-click,
Orb, Hotkey live capture, Admin elevation — carries a `live-verified <date> on
<device>` note or an explicit `unverified-on-real-desktop` label in
`SIGNOFF-LOG.md`) and completes the documentation half of **EK-6** (CLAUDE.md +
README capability matrix updated to reflect real cross-platform status, ADR-0020
linked, import-cleanliness re-confirmed). It re-confirms **EK-1** (CI still green
after the doc changes) and produces the JARVIS-20 cross-platform results that
close out the plan's North Star.

## Dependencies on prior waves

**All of Waves 0, 1, 2, and 3 must be merged.** Wave 4 verifies what they built:
4.1's harness imports the six factories from Waves 1-3; 4.2's sign-off exercises
the AX/AT-SPI/Orb/Hotkey/Admin code from Waves 2-3; 4.3/4.4 document the final
cross-platform status; 4.5 benchmarks all six ports. This is the terminal wave —
nothing depends on it.
