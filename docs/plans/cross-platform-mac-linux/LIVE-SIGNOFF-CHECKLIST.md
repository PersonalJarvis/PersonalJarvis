# Live Sign-Off Checklist — macOS + Linux GUI/Permission Behaviors

> Wave 4, sub-task **4.1**. Canonical decisions: [`_FROZEN-DECISIONS.md`](_FROZEN-DECISIONS.md)
> (AD-3 verification = CI + one-time live sign-off + honest per-feature labels;
> EK-5 sign-off notes). This checklist enumerates **exactly the AD-3
> GUI/permission behaviors that headless CI cannot reach** — one row per
> (feature × OS) for the six behaviors:
>
> 1. App identity and native privacy prompts
> 2. UI-element-click — a real AX (macOS) / AT-SPI (Linux) accessibility tree
> 3. Orb overlay — the *actual* transparency (or the tray fallback)
> 4. Hotkey *capture* — keys arriving from the OS (not just registration)
> 5. Multi-monitor Computer-Use targeting
> 6. Admin/elevation — the OS auth *prompt* and a privileged op completing
>
> Run it with the operator aide
> [`scripts/crossplatform/signoff_probe.py`](../../../scripts/crossplatform/signoff_probe.py),
> which brackets each manual step (`--feature ax|orb|hotkey|admin`) and prints
> what to observe. The probe **never** automates a permission prompt and **never**
> fakes a verdict. Record results in [`SIGNOFF-LOG.md`](SIGNOFF-LOG.md). Output
> language: English.

---

## How to use this checklist

1. On a real **macOS** device and a real **Linux** desktop, run the advertised
   full install (`pip install -e ".[full]"` for a developer checkout). On
   Linux additionally `apt install python3-pyatspi gir1.2-atspi-2.0` (AD-14:
   `pyatspi` is distro-packaged, **not** a pip extra — do not try to pip-install
   it).
2. For each row, run the listed `signoff_probe.py --feature <name>` command, grant
   the noted permission, perform the manual step, and watch for the **expected
   observation**.
3. Fill the **PASS / FAIL / N/A** field. A graceful, logged degrade on its target
   OS (Wayland hotkey no-op, headless tray fallback, pixel-click fallback,
   NullElevator refusal) is a **PASS** for the AD-6 contract — not a FAIL.
4. Copy the dated, device-attributed verdict into [`SIGNOFF-LOG.md`](SIGNOFF-LOG.md).

> **Honesty contract (AD-3):** if a device is unavailable (no rented Mac, no
> Wayland box), mark the row `N/A` here and record `unverified-on-real-desktop`
> in the log with the reason. That is the truthful outcome, not a failure.

Run every macOS row once on Intel and once on Apple Silicon. Record the CPU,
macOS version, display arrangement, and per-display scaling in the sign-off log.

## 0. App identity and macOS privacy grants

| # | Scenario | Manual step | Expected observation | PASS / FAIL / N/A |
|---|---|---|---|---|
| TCC-1 | Stable installed identity | Run the public install command, then inspect the launched process and `~/Applications/Personal Jarvis.app` | The installer launches through LaunchServices; status reports bundle ID `com.personal-jarvis.desktop`, `launched_as_bundle=true`, and `stable=true`. Terminal/Python never receives the grants | _____ |
| TCC-2 | Explicit first-run grants | In onboarding, press each Allow button in order: Microphone, Screen Recording, Accessibility, Input Monitoring, Input Control | Every dialog is initiated by that button; no dialog appears merely from installing, booting, or opening the page. Returning from System Settings refreshes status without manual reload | _____ |
| TCC-3 | Denial and recovery | Deny each permission once, then use Open Settings and grant it | The affected feature stays disabled with an honest status; text chat remains usable; recovery is entirely in-app and never edits the TCC database | _____ |
| TCC-4 | Revocation | Revoke each previously granted permission while Jarvis is running, then retry the affected feature | The next microphone, capture, hotkey, window, or input action fails closed before touching the protected API; restoring the grant recovers through the supported UI flow | _____ |
| TCC-5 | Identity persistence | Update/relaunch and enable login autostart | Manual launch, restart, updater relaunch, and LaunchAgent all re-enter through the same app bundle; grants do not migrate to Terminal/Python or unexpectedly reset | _____ |

---

## 1. UI-element-click — real accessibility tree (`make_ui_tree_source`)

| # | Feature × OS | Probe command | Manual step | Expected observation | PASS / FAIL / N/A |
|---|---|---|---|---|---|
| AX-1 | UI-element-click (macOS) | `signoff_probe.py --feature ax` | Grant System Settings › Privacy & Security › Accessibility; bring a normal app (e.g. TextEdit) to the foreground | `make_ui_tree_source()` returns `AXTreeSource`; `observe()` yields **non-empty** `UIANode`s with canonical roles (`AXButton`→`Button`); a `click_element` by name lands on its bounds | _____ |
| AX-2 | UI-element-click (macOS) — degrade | `signoff_probe.py --feature ax` | **Revoke** the Accessibility grant, retry | Tree is empty; one English permission message is returned and Computer-Use refuses to inject input. No pixel-click bypass, crash, or silent empty result | _____ |
| AX-3 | UI-element-click (Linux) | `signoff_probe.py --feature ax` | `apt install python3-pyatspi gir1.2-atspi-2.0`; ensure the AT-SPI bus is up; foreground a GTK app | `make_ui_tree_source()` returns `AtspiTreeSource`; `observe()` returns a **non-empty** tree normalized to canonical roles | _____ |
| AX-4 | UI-element-click (Linux) — degrade | `signoff_probe.py --feature ax` | Stop the AT-SPI bus / uninstall `pyatspi`, retry | `NullUITreeSource`; **one** English degrade line ("AT-SPI bus unavailable — install python3-pyatspi …"); pixel-click fallback still clicks. No crash | _____ |

## 2. Orb overlay — transparency / tray fallback (`make_overlay_surface`)

| # | Feature × OS | Probe command | Manual step | Expected observation | PASS / FAIL / N/A |
|---|---|---|---|---|---|
| ORB-1 | Orb (macOS) | `signoff_probe.py --feature orb` | Launch the desktop app; wake Jarvis | `TkColorKeyOverlay` renders a **transparent** orb (no opaque magenta/black backing box) that visibly changes to LISTENING then back to IDLE | _____ |
| ORB-2 | Orb (Linux, compositor) | `signoff_probe.py --feature orb` | On an X11 compositor, launch the desktop app | `LinuxBestEffortOverlay` renders a transparent orb cycling IDLE→LISTENING→THINKING→SPEAKING | _____ |
| ORB-3 | Orb (Linux, Wayland / headless) — degrade | `signoff_probe.py --feature orb` | On Wayland or with no compositor, launch the desktop app | Surface detects it cannot key out the transparent color, logs **one** English message, **falls through to `TrayOnlySurface`**; a **state-colored tray icon** shows the four states. Never an opaque magenta box, never a crash (AD-11) | _____ |

## 3. Hotkey capture — keys from the OS (`make_hotkey_backend`)

| # | Feature × OS | Probe command | Manual step | Expected observation | PASS / FAIL / N/A |
|---|---|---|---|---|---|
| HK-1 | Hotkey (macOS) | `signoff_probe.py --feature hotkey` | Grant Accessibility and Input Monitoring; press `ctrl+right_alt+j` | `PynputBackend` **captures** the combo; Jarvis enters LISTENING | _____ |
| HK-2 | Hotkey (macOS) — missing grant | `signoff_probe.py --feature hotkey` | Without Input-Monitoring, press the combo | The "registered but zero events → grant Input-Monitoring/Accessibility" detection fires (AD-8); no crash | _____ |
| HK-3 | Hotkey (Linux X11) | `signoff_probe.py --feature hotkey` | On an X11 session, press the combo | `PynputBackend` captures the press; Jarvis enters LISTENING | _____ |
| HK-4 | Hotkey (Linux Wayland) — degrade | `signoff_probe.py --feature hotkey` | On a Wayland session, press the combo, then say the wake word | `NoopBackend`; the combo does nothing but logs **once** "global hotkey unavailable on Wayland by OS design; lean on the wake word"; the wake word still summons Jarvis (AD-8). No crash, no spam | _____ |

## 4. Multi-monitor Computer-Use — real display geometry

| # | Scenario | Manual step | Expected observation | PASS / FAIL / N/A |
|---|---|---|---|---|
| CU-M1 | Secondary display left/above primary | Place the target app on a display with negative X and/or Y, then click four corner targets | Screenshot, accessibility bounds, cursor landing, and click all use the same global point space; every corner lands within 2 points | _____ |
| CU-M2 | Mixed scaling / Retina | Use different resolutions and scaling on two displays; click a small control on each | Capture pixels map to macOS input points exactly once; no 2× Retina offset or primary-display clamp | _____ |
| CU-M3 | Window straddles displays | Split one window across two displays with its center on the smaller overlap | Capture selects the display with the largest window overlap, not merely the window center | _____ |
| CU-M4 | L-shaped dead gap | Arrange displays with an uncovered virtual-desktop gap and target a point inside it | Action is refused before button-down with an explicit virtual-desktop-gap error | _____ |
| CU-M5 | Display changes mid-step | Disconnect, rotate, or rearrange a display after observation but before action | Topology signature mismatch refuses the stale action and forces a fresh screenshot/geometry pass | _____ |
| CU-M6 | Coordinate-less scroll | Put the cursor on another display, target a scrollable window in the captured display, and request scrolling without coordinates | Jarvis grounds the scroll at the captured target/window, not at the stale cursor position | _____ |

## 5. Admin / elevation — auth prompt + privileged op (`make_elevator` + `make_admin_transport`)

| # | Feature × OS | Probe command | Manual step | Expected observation | PASS / FAIL / N/A |
|---|---|---|---|---|---|
| ADM-1 | Admin (macOS) | `signoff_probe.py --feature admin` | Trigger an authorized `brew`/`launchctl` op | `MacAuthElevator`; the **Touch-ID/password sheet** appears; on approval the op completes via an **argv list** (never a shell string) through the `UnixSocketTransport` peer-cred path | _____ |
| ADM-2 | Admin (macOS) — no auth | `signoff_probe.py --feature admin` | On a box with no auth mechanism, trigger a privileged op | `NullElevator` refusal: typed `AdminResponse(success=False, …)` with the English "no elevation mechanism available" message; never silently runs, never crashes | _____ |
| ADM-3 | Admin (Linux, polkit) | `signoff_probe.py --feature admin` | Trigger an authorized `apt`/`systemctl`/`ufw` op | `PolkitElevator` (pkexec); the **polkit dialog** appears; on approval the op completes through the validated-argv HMAC core | _____ |
| ADM-4 | Admin (Linux, sudo fallback) | `signoff_probe.py --feature admin` | On a box with `sudo` but no `pkexec`, trigger a privileged op | `SudoElevator` is selected and the op completes after the `sudo` prompt | _____ |
| ADM-5 | Admin (Linux, headless VPS) — degrade | `signoff_probe.py --feature admin` | On a €5 VPS (no pkexec, no sudo, no GUI), trigger a privileged op | `NullElevator` refusal with the English "install pkexec or run with sudo" message; never silently runs, never crashes (AD-12) | _____ |

---

## Coverage note

The original four behaviors map onto the JARVIS-20 sign-off-gated and graceful-degrade
scenarios (see [`JARVIS-20-CROSSPLATFORM.md`](JARVIS-20-CROSSPLATFORM.md)):

- UI-element-click → CP-10/CP-11 (live tree) + CP-12 (degrade)
- Orb → CP-13/CP-14 (transparency) + CP-15 (tray fallback)
- Hotkey → CP-7/CP-8 (capture) + CP-9 (Wayland no-op)
- Admin → CP-16 (prompt+install) + CP-18 (NullElevator refusal)

Terminal (CP-1..CP-3) and app-launch resolution (CP-4..CP-6) are **not** on this
checklist: they are fully CI-provable (EK-4) and need no live sign-off — only the
*actual* app launch (CP-4) is a light live check, noted in the log.
