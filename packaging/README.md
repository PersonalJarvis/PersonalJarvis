# Native installer recipes

This directory turns the PyInstaller bundle (`jarvis.spec` → `dist/`) into a
double-click native installer for each OS. The full per-OS build runs in CI on
real runners — see [`.github/workflows/build-app.yml`](../.github/workflows/build-app.yml).

| OS | Recipe | Output | Tooling |
|---|---|---|---|
| Windows | [`windows/jarvis.iss`](windows/jarvis.iss) | `PersonalJarvis-Setup-<ver>.exe` | Inno Setup (`iscc`) |
| macOS | [`macos/build-dmg.sh`](macos/build-dmg.sh) | `PersonalJarvis-<ver>.dmg` | `hdiutil` (built-in) |
| Linux | [`linux/build-appimage.sh`](linux/build-appimage.sh) | `PersonalJarvis-<ver>-x86_64.AppImage` | `appimagetool` |

## Local build

```bash
# 1. Build the PyInstaller bundle first:
build.bat              # Windows  -> dist\Jarvis\
./build.sh             # macOS    -> dist/Jarvis.app   (BUNDLE step)
./build.sh             # Linux    -> dist/Jarvis/

# 2. Then the native installer:
iscc /DAppVersion=0.1.0 packaging\windows\jarvis.iss      # Windows
packaging/macos/build-dmg.sh 0.1.0                        # macOS
packaging/linux/build-appimage.sh 0.1.0                   # Linux
```

## Honesty / status

- **Windows**: live-buildable and live-testable on the maintainer's machine.
- **macOS / Linux**: the recipes follow standard conventions and build on the CI
  runners, but the resulting GUI/permission behaviour is **not yet live-verified
  on real macOS/Linux desktop hardware** (the maintainer has Windows only). Track
  the per-OS sign-off in
  [`docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md`](../docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md).
- The `.dmg` and `.AppImage` are **unsigned**. Gatekeeper (macOS) needs a
  right-click → Open the first time; signing + notarization (macOS) and a `.desktop`
  MIME registration (Linux) are future work.
- Native packaging of an app that spawns subprocess workers + git worktrees is
  inherently fiddly; expect the first CI runs to need iteration. The fully
  supported, working install path today remains `pip install -e .[full]` via
  [`install/installer.py`](../install/installer.py).
