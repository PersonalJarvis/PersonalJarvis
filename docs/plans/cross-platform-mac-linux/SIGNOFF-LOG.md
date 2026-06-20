# Sign-Off Log — cross-platform GUI/permission verdicts

> Wave 4, sub-task **4.2**. Dated, device-attributed results of running
> [`LIVE-SIGNOFF-CHECKLIST.md`](LIVE-SIGNOFF-CHECKLIST.md) via
> [`scripts/crossplatform/signoff_probe.py`](../../../scripts/crossplatform/signoff_probe.py).
> Per the AD-3 honesty contract, every verdict tells the literal truth.

## Verdict vocabulary (read this first)

| Label | Meaning |
|---|---|
| `verified-on-windows <date>` | Provable on the Windows host present in this environment: factory selection on Windows, platform-independent logic, the real-PTY echo round-trip, the import-cleanliness gate. **Not** a claim about macOS/Linux behavior. |
| `verified-in-linux-container <date>` | Provable in a local `python:3.11-slim` Docker container present in this environment: base-install boot, module imports with the `[desktop]` extras absent, the import-cleanliness gate, and headless/logic test suites — on **real Linux**, not just configured CI. **Not** a claim about Linux **GUI/desktop** behavior (AX/AT-SPI tree, Orb, hotkey, elevation), which still need a real desktop session. |
| `CI-configured — first green run pending push` | The `ci.yml` matrix is *configured* to prove this on the ubuntu/macos runners (terminal real-PTY, app-launch resolution, the import-clean gate on Linux/macOS), but the workflow **has not run yet** — nothing is pushed. **Not** "CI-verified". |
| `unverified-on-real-desktop` | A macOS/Linux **live** GUI/permission behavior (AX/AT-SPI tree, Orb transparency, hotkey capture, elevation prompt). There is **no macOS/Linux hardware in this environment**, so it could not be observed. Run `signoff_probe.py` on a real device to fill it in. |
| `live-verified <date> on <device>` | Observed on a dated real device. **None recorded yet** — this environment is Windows-only. |

## Environment of record

- **Host:** Windows 11 Pro (the maintainer's only machine).
- **macOS hardware:** none available in this environment.
- **Linux desktop hardware:** none available in this environment (the €5 VPS is a
  *headless* Linux box — it can exercise degrade paths and capability probes, but
  not a GUI-present Linux session). The **headless / import / logic** layer WAS
  reached this pass via a local `python:3.11-slim` Docker container (see §0); the
  GUI layer was not.
- **CI matrix (`ci.yml`):** configured for `ubuntu-latest` + `macos-latest` +
  `windows-latest`, triggered on push to `main` / the migration branch and on PRs.
  **No push has occurred → the matrix has not run → no green run exists yet.**

---

## 0. Local Linux container pass — `verified-in-linux-container 2026-06-20`

Run on a fresh `python:3.11-slim` Docker container (the cloud-first base target),
source piped in via `tar` — the host working tree was never mounted or modified:

- **Base install** — `pip install .` (pyproject base deps, **no `[desktop]` extras**)
  boots cleanly; every dependency resolved via wheels (no build tools needed). ✅
- **Imports** — `jarvis.browser_voice.{session,route}`, `jarvis.telephony.audio`,
  `jarvis.audio.{player,capture}` all import with **sounddevice absent** (the H4
  lazy-import / `_PortAudioError` sentinel degrade to `sd = None`). ✅
- **B2 backend** — `tests/unit/browser_voice/` **13/13 passed** on Linux. ✅
- **Import-cleanliness gate** — PASS on Linux. ✅
- **Headless-import test** — `tests/unit/audio/test_headless_import.py` 6/6 passed. ✅
- **Not regressions** — the telephony failures are `ModuleNotFoundError: twilio`
  (the `[telephony]` extra) and the audio device-layer failures (WDM-KS /
  persistent-stream / device-rate) need real sounddevice (the `[desktop]` extra):
  i.e. correct cloud-first behaviour — the base install legitimately omits them.

**Scope boundary (AD-3 honesty):** this proves the **headless / import / logic**
layer on real Linux. It does **NOT** verify Linux **GUI/permission** behaviour — the
AX/AT-SPI tree, Orb transparency, hotkey capture, and pkexec/sudo elevation remain
`unverified-on-real-desktop` (no GUI Linux session here). macOS remains entirely
unverified (no macOS host; Docker on Windows cannot run macOS).

---

## 1. UI-element-click — real accessibility tree

| Row | Behavior | Verdict |
|---|---|---|
| AX-1 | AX tree returns non-empty UIANodes (macOS, Accessibility granted) | `unverified-on-real-desktop` — no macOS/Linux hardware in this environment (run `scripts/crossplatform/signoff_probe.py --feature ax` on a real device to fill this in) |
| AX-2 | AX degrade → onboarding message + pixel fallback (macOS, grant revoked) | `unverified-on-real-desktop` — no macOS/Linux hardware in this environment (run `scripts/crossplatform/signoff_probe.py --feature ax` on a real device to fill this in) |
| AX-3 | AT-SPI tree returns non-empty tree (Linux, bus up) | `unverified-on-real-desktop` — no macOS/Linux hardware in this environment (run `scripts/crossplatform/signoff_probe.py --feature ax` on a real device to fill this in) |
| AX-4 | AT-SPI degrade → bus-unavailable message + pixel fallback (Linux) | `unverified-on-real-desktop` — no macOS/Linux hardware in this environment (run `scripts/crossplatform/signoff_probe.py --feature ax` on a real device to fill this in) |
| — | `make_ui_tree_source()` selects `UIATreeSource` on Windows; role-normalization logic | `verified-on-windows 2026-05-30` — probe selected `UIATreeSource`; role-map normalization is unit-tested (`tests/unit/vision/test_role_map.py`, `test_tree_factory.py`) |

## 2. Orb overlay — transparency / tray fallback

| Row | Behavior | Verdict |
|---|---|---|
| ORB-1 | Transparent orb, no magenta box (macOS) | `unverified-on-real-desktop` — no macOS/Linux hardware in this environment (run `scripts/crossplatform/signoff_probe.py --feature orb` on a real device to fill this in) |
| ORB-2 | Best-effort transparent orb on an X11 compositor (Linux) | `unverified-on-real-desktop` — no macOS/Linux hardware in this environment (run `scripts/crossplatform/signoff_probe.py --feature orb` on a real device to fill this in) |
| ORB-3 | Wayland/headless → state-colored tray fallback (Linux) | `unverified-on-real-desktop` — no macOS/Linux hardware in this environment (run `scripts/crossplatform/signoff_probe.py --feature orb` on a real device to fill this in) |
| — | `make_overlay_surface()` selects `TkColorKeyOverlay` on Windows; state-mapping logic | `verified-on-windows 2026-05-30` — probe selected `TkColorKeyOverlay`; surface selection + state map are unit-tested (`tests/overlay/`) |

## 3. Hotkey capture — keys from the OS

| Row | Behavior | Verdict |
|---|---|---|
| HK-1 | pynput captures `ctrl+right_alt+j` (macOS, Input-Monitoring granted) | `unverified-on-real-desktop` — no macOS/Linux hardware in this environment (run `scripts/crossplatform/signoff_probe.py --feature hotkey` on a real device to fill this in) |
| HK-2 | "registered but zero events" hint fires (macOS, no grant) | `unverified-on-real-desktop` — no macOS/Linux hardware in this environment (run `scripts/crossplatform/signoff_probe.py --feature hotkey` on a real device to fill this in) |
| HK-3 | pynput captures the combo (Linux X11) | `unverified-on-real-desktop` — no macOS/Linux hardware in this environment (run `scripts/crossplatform/signoff_probe.py --feature hotkey` on a real device to fill this in) |
| HK-4 | Wayland no-op + single log + wake-word still works (Linux Wayland) | `unverified-on-real-desktop` — no macOS/Linux hardware in this environment (run `scripts/crossplatform/signoff_probe.py --feature hotkey` on a real device to fill this in) |
| — | `make_hotkey_backend()` selects `GlobalHotkeysBackend` on Windows | `verified-on-windows 2026-05-30` — probe selected `GlobalHotkeysBackend`; backend selection is unit-tested (`tests/unit/trigger/`, fake at `tests/fakes/fake_hotkey_backend.py`) |

## 4. Admin / elevation — auth prompt + privileged op

| Row | Behavior | Verdict |
|---|---|---|
| ADM-1 | Touch-ID/password sheet + brew/launchctl op via peer-cred socket (macOS) | `unverified-on-real-desktop` — no macOS/Linux hardware in this environment (run `scripts/crossplatform/signoff_probe.py --feature admin` on a real device to fill this in) |
| ADM-2 | NullElevator refusal on a no-auth macOS box | `unverified-on-real-desktop` — no macOS/Linux hardware in this environment (run `scripts/crossplatform/signoff_probe.py --feature admin` on a real device to fill this in) |
| ADM-3 | polkit dialog + apt/systemctl/ufw op (Linux pkexec) | `unverified-on-real-desktop` — no macOS/Linux hardware in this environment (run `scripts/crossplatform/signoff_probe.py --feature admin` on a real device to fill this in) |
| ADM-4 | SudoElevator fallback (Linux, sudo but no pkexec) | `unverified-on-real-desktop` — no macOS/Linux hardware in this environment (run `scripts/crossplatform/signoff_probe.py --feature admin` on a real device to fill this in) |
| ADM-5 | NullElevator refusal on a headless Linux VPS | `unverified-on-real-desktop` — no macOS/Linux hardware in this environment (run `scripts/crossplatform/signoff_probe.py --feature admin` on a real device to fill this in) |
| — | `make_elevator()`/`make_admin_transport()` select `UacElevator`/`NamedPipeTransport` on Windows; argv-validation + HMAC core | `verified-on-windows 2026-05-30` — probe selected `UacElevator` + `NamedPipeTransport`; the schema `extra="forbid"` + pattern-validated-argv + HMAC core are unit-tested against the fake transport (`tests/unit/admin/`, `tests/fakes/fake_admin_transport.py`, `tests/fakes/fake_elevator.py`) — this layer is **never CI-testable end-to-end** by design (interactive auth, AD-12) |

---

## Terminal + app-launch (CI-provable, EK-4 — not GUI/permission-gated)

These are not on the GUI/permission checklist; recorded here for completeness.

| Behavior | Verdict |
|---|---|
| Terminal — `make_pty_backend()` spawns the OS shell + echo round-trips with no mojibake | `verified-on-windows 2026-05-30` (probe ran a real PTY echo round-trip on `WinptyBackend`, `exitstatus` seam intact). macOS/Linux real-PTY: `CI-configured — first green run pending push` (the ubuntu/macos legs of `ci.yml` run the real-PTY test). |
| App-launch — `resolve_app_launch_target()` maps names + rejects a hallucinated name | `verified-on-windows 2026-05-30` (probe resolved calculator/terminal/code to executables and routed the nonsense name to a refusable target). macOS/Linux resolution: `CI-configured — first green run pending push`. The *actual* process launch on a real device is `unverified-on-real-desktop`. |
| Import-cleanliness gate (`python -c "import jarvis"`, no module-scope Windows-only import) | `verified-on-windows 2026-05-30` (`scripts/ci/check_import_clean.py` exits 0, 534 files scanned). On Linux/macOS: `CI-configured — first green run pending push` (the BLOCKING gate runs on every leg of `ci.yml`). |

---

## Summary

| Category | `live-verified` | `verified-on-windows` | `CI-configured` (pending push) | `unverified-on-real-desktop` |
|---|---|---|---|---|
| UI-element-click | 0 | 1 (Windows selection + role-map logic) | — | 4 |
| Orb | 0 | 1 (Windows selection + state-map) | — | 3 |
| Hotkey | 0 | 1 (Windows selection) | — | 4 |
| Admin | 0 | 1 (Windows selection + argv/HMAC core) | — | 5 |
| Terminal | 0 | 1 (Windows real-PTY round-trip) | 1 (Mac/Linux real-PTY) | — |
| App-launch | 0 | 1 (Windows resolution) | 1 (Mac/Linux resolution) | 1 (live launch) |

**Bottom line (AD-3 honesty):** zero macOS/Linux live GUI/permission rows are
`live-verified`, because this environment is Windows-only and nothing is pushed.
The CI matrix is *configured* but has not produced a green run. Everything
provable on Windows here is recorded as `verified-on-windows 2026-05-30`. To
close out EK-5 fully, an operator must run `signoff_probe.py` on a real macOS box
and a real Linux desktop and replace each `unverified-on-real-desktop` line with a
dated `live-verified … on <device>` verdict.
