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

> Rewritten 2026-10-02 to the ask-when-needed behaviour (AP-35,
> [ADR-0037](../../adr/0037-macos-permissions-ask-when-needed.md)): a feature asks
> macOS at the moment the user first uses it, from that gesture; nothing is asked at
> launch, in onboarding or by a banner, and the app draws nothing around macOS's dialog (its only
> addition is one toast after a user-started use failed, UI reset 2026-10-02). The earlier rows (press each Allow button in
> onboarding, fail closed before the OS is asked) described the removed preflight wall;
> git history keeps them. None of these rows has been signed off on a physical Mac.
> The full row set with the exact dialogs to record is section 7 of
> [`docs/macos-permissions.md`](../../macos-permissions.md) (ids `M-*`).
>
> **Before each row** quit Jarvis, run `jarvis permissions reset <permission> --yes` (or
> `tccutil reset <Service> <bundle id>`) for the permission under test (Microphone,
> ScreenCapture, Accessibility plus PostEvent, ListenEvent, AppleEvents), start the app again, then check System Settings > Privacy &
> Security: an unchanged list means the reset did nothing. Test both installed apps (the
> `.dmg` app `ai.personaljarvis.desktop` and the managed app `com.personal-jarvis.desktop`)
> and the one you are not testing must keep its own state.

| # | Scenario | Manual step | Expected observation | PASS / FAIL / N/A |
|---|---|---|---|---|
| TCC-1 | Stable installed identity | Install through the public install command (managed app) or drag the `.dmg` app to Applications; start it from there; read `jarvis permissions status` | The app runs from the installed bundle; status reports its bundle ID (`com.personal-jarvis.desktop` managed, `ai.personaljarvis.desktop` for the `.dmg`), `launched_as_bundle=true`, `stable=true`. Terminal/Python never receives the grants. A terminal-launched dev run asks for no grant on its own: the toast offers **Ask macOS now**, a confirmation that names the app that started Jarvis | _____ |
| TCC-2 | First use asks, nothing at launch | With every permission reset: start the app and wait 30 s with the wake word off; then press the dictation button in the composer (microphone); ask Jarvis to look at the screen (Screen Recording); dictate into another app or run one computer-use click (Accessibility); save a new shortcut on Voice > Shortcuts (Input Monitoring); with Music running switch **Mute music while dictating** on (Automation) | **No dialog, toast, banner or wizard at launch or while the page is open.** Each protected action raises Apple's own dialog for exactly that permission, from that gesture, naming Personal Jarvis with the usage string where one exists; Jarvis shows its own toast only when the permission is blocked, never while an Apple dialog is up. Record the dialog text and whether the app was frontmost | _____ |
| TCC-3 | Denial and recovery | Answer **Don't Allow** (or leave a Screen Recording or Accessibility dialog unanswered) for each permission once, retry the feature, then use **Open System Settings** from the toast and switch it on | Only the affected feature degrades with an honest sentence (dictation: the microphone is off; capture refuses, **no wallpaper frame**; dictation paste stays on the clipboard as `clipboard_only`); typed chat stays usable; the retry does not raise a second dialog (a decision is not asked twice); the toast's button opens the right pane; after the switch the feature works without a restart for the microphone, and the observation for Screen Recording and Input Monitoring (works at once, or only after "Quit and reopen") is written down. Recovery never edits the TCC database | _____ |
| TCC-4 | Revocation | Revoke each previously granted permission in System Settings while Jarvis is running, then retry the affected feature | The next microphone stream ends within a few seconds (or after about 5 s of exact zeros shows one "denied or muted" notice), the next capture, shortcut, window or input action refuses honestly **without** acting on a stale grant, and restoring the grant recovers through the toast's button or System Settings. No crash | _____ |
| TCC-5 | Identity persistence | Update/relaunch and enable login autostart | Manual launch, restart, updater relaunch, and LaunchAgent all re-enter through the same app bundle; grants do not migrate to Terminal/Python or unexpectedly reset. Record the "Background Items Added" notice and the name Login Items shows | _____ |
| TCC-6 | Upgrader and boot rule | With every grant present, update or restart the app; then, with the wake word on and the microphone reset, start it again | An upgrader sees zero toasts and zero requests and every feature works at once. With the microphone reset and the wake word on, launch shows no dialog and opens no input stream and no toast (the one wake-word toast appears only for a DENIED microphone) | _____ |
| TCC-7 | An agent never answers a dialog | With an Apple permission dialog on screen, start a computer-use task | Nothing is dispatched while the consent window is frontmost; the mission ends `blocked_permission` with a Retry; the agent text says not to touch the dialog | _____ |

---

## 1. UI-element-click — real accessibility tree (`make_ui_tree_source`)

| # | Feature × OS | Probe command | Manual step | Expected observation | PASS / FAIL / N/A |
|---|---|---|---|---|---|
| AX-1 | UI-element-click (macOS) | `signoff_probe.py --feature ax` | Grant System Settings › Privacy & Security › Accessibility; bring a normal app (e.g. TextEdit) to the foreground | `make_ui_tree_source()` returns `AXTreeSource`; `observe()` yields **non-empty** `UIANode`s with canonical roles (`AXButton`→`Button`); a `click_element` by name lands on its bounds | _____ |
| AX-2 | UI-element-click (macOS) — degrade | `signoff_probe.py --feature ax` | **Revoke** the Accessibility grant, retry | AX reads degrade quietly (empty tree, no dialog, no card); the first input action asks for Accessibility (`TCC-2`) and, while it is off, Computer-Use returns one `[permission_needed:accessibility]` message and refuses to inject input. No pixel-click bypass, crash, or silent empty result | _____ |
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
| HK-1 | Hotkey (macOS) | `signoff_probe.py --feature hotkey` | Save a shortcut on Voice > Shortcuts, which asks for Input Monitoring, and allow it (Accessibility is not needed for the listen-only tap); press `ctrl+right_alt+j` | `QuartzHotkeyBackend` **captures** the combo; Jarvis enters LISTENING | _____ |
| HK-2 | Hotkey (macOS) — missing grant | `signoff_probe.py --feature hotkey` | Without Input Monitoring (never asked, or denied), press the combo | Nothing is asked at launch; the shortcut does nothing and, after a denied save, one toast offers **Open System Settings**; the buttons and voice keep working; a restart hint appears only when the preflight reads granted and typing then produced no events (AD-8); never an automatic restart; no crash | _____ |
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
