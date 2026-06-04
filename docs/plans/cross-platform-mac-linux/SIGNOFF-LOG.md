# Cross-Platform Sign-off Log

Honest, dated, device-attributed verification status for the cross-platform full
desktop app. **Never** upgrade a row to `live-verified` without a real device.

Legend:
- `live-verified` — exercised on real hardware of that OS; dated + who/what.
- `CI-configured` — a CI job is configured to build/exercise it on that OS's
  runner; the build runs on a real OS image, but interactive GUI/permission
  behaviour is **not** asserted.
- `unverified-on-real-desktop` — needs a real desktop of that OS to sign off.

The operator probe to fill these in lives at
[`scripts/crossplatform/signoff_probe.py`](../../../scripts/crossplatform/signoff_probe.py).

---

## Native installer / one-click app (Phase C — added 2026-06-02)

The build matrix is [`.github/workflows/build-app.yml`](../../../.github/workflows/build-app.yml);
the per-OS recipes are under [`packaging/`](../../../packaging/README.md).

| Target | Build (produces an artifact) | App launches + window shows | Mic / audio at runtime |
|---|---|---|---|
| Windows `.exe` (Inno Setup) | live-verified — maintainer Windows 11, 2026-06-02 (PyInstaller bundle + `installer.py` full install boot the desktop window) | live-verified — desktop window "Personal Jarvis" opened, 2026-06-02 | unverified (mic not exercised end-to-end) |
| macOS `.dmg` | CI-configured (macos-latest runner) — first green run pending push | unverified-on-real-desktop | unverified-on-real-desktop |
| Linux AppImage | CI-configured (ubuntu-latest runner) — first green run pending push | unverified-on-real-desktop | unverified-on-real-desktop |

**Caveats (honest):**
- The `.dmg` and `.AppImage` are **unsigned / un-notarized**. Gatekeeper on
  macOS requires a right-click → Open the first time.
- Native packaging of an app that spawns subprocess workers + git worktrees is
  inherently fiddly. The **first CI runs are expected to need iteration** — a
  green "Build App" run proves the artifact assembles, not that every runtime
  feature works inside the frozen bundle.
- The fully supported, working "all features" install path on every OS today is
  `pip install -e .[full]` via [`install/installer.py`](../../../install/installer.py)
  (clone + venv + pip). The native bundle is a convenience layer on top.

---

## Source-install full-app path (Phase B — 2026-06-02)

| Target | `installer.py` full install | `import jarvis` + companions | Desktop window |
|---|---|---|---|
| Windows | live-verified — 2026-06-02 (`.[full]` + board_backend/overlay/skillbook, `pip check` clean) | live-verified — 2026-06-02 | live-verified — 2026-06-02 |
| macOS | CI-configured (intended); unverified-on-real-desktop | CI-configured | unverified-on-real-desktop |
| Linux | CI-configured (intended); unverified-on-real-desktop | CI-configured | unverified-on-real-desktop |
