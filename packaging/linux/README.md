# Linux packaging — AppImage and Debian package

`packaging/linux/build.sh` turns this repository into two downloadable files:

| Artifact | Path | What it is |
|---|---|---|
| `PersonalJarvis-Linux-x86_64.AppImage` | `dist/installers/` | One executable file. No Python, no pip, no root. Download, `chmod +x`, run. |
| `personal-jarvis_<version>_amd64.deb` | `dist/installers/` | The same tree as a normal Debian package: `apt install ./personal-jarvis_*.deb` registers the app in the menu and puts `jarvis` on `PATH`. Built only when `dpkg-deb` is present. |

Both are produced from one PyInstaller `onedir` freeze and one AppDir, so a bug
can only exist in both or in neither.

## Building

```bash
# on a Linux x86_64 machine or an ubuntu-22.04 runner
python -m pip install -e ".[full]"      # or ".[desktop]" for a smaller build
python -m pip install pyinstaller
bash packaging/linux/build.sh
```

The script builds the web bundle first when `jarvis/ui/web/dist` is missing
(needs `npm`), freezes the app, assembles the AppDir, downloads a
**pinned** `appimagetool` (version and SHA-256 are in the script; a digest
mismatch aborts the build), and prints the artifact paths on the last lines.

Useful switches: `DRY_RUN=1` prints every command without running it,
`SKIP_PYINSTALLER=1` reuses an existing freeze in `FREEZE_DIR`, `BUILD_DEB=0`
skips the Debian package, `APPIMAGETOOL=/path/to/tool` uses a local build tool.

## The window question — read this before "fixing" it

**The AppImage has no native desktop window. It serves the interface over
loopback HTTP and opens it in the user's default browser.** That is a decision,
not a bug, and here is the whole reasoning:

* pywebview needs a *native* backend. On Linux that is GTK 3 + WebKit2GTK
  through PyGObject, or Qt through QtWebEngine.
* **The system's PyGObject cannot be used.** `python3-gi` is a compiled
  extension built against the distribution's own CPython. A PyInstaller bundle
  ships its own interpreter, so `import gi` inside the frozen app can never
  resolve to the system package no matter which system packages are installed.
  Telling users to `apt install gir1.2-webkit2-4.1` does not help this build.
* **Bundling GTK + WebKit2GTK is not a small change.** WebKitGTK is not one
  library: it needs its `WebKitNetworkProcess` / `WebKitWebProcess` helper
  binaries at the paths it was compiled for, plus GIO modules, GdkPixbuf
  loaders, GSettings schemas and typelibs. Freezing that stack portably is a
  project of its own, and it breaks in ways that only show up on other people's
  distributions - the exact failure mode this project treats as a defect.
* **Qt is not available either, today.** The `[desktop]` extra installs
  `pyside6-essentials`, which does *not* contain QtWebEngine; pywebview's Qt
  backend needs `QtWebEngineWidgets` from `pyside6-addons`. `jarvis.spec` also
  excludes PySide6 outright to keep the bundle small. Adding `pyside6-addons`
  and removing that exclusion is the realistic route to a native Linux window,
  at roughly +400 MB - a trade worth making deliberately, not by accident.

So the app degrades, and it really does degrade: `jarvis/ui/desktop_app.py`
catches pywebview's `WebViewException`, keeps the already-running backend
serving, and prints where the interface is. Verified in a container against a
real build:

```
Native desktop window unavailable: You must have either QT or GTK with Python
extensions installed in order to use pywebview.
Browser-UI fallback: UI stays reachable at http://127.0.0.1:47821
```

A double-clicked AppImage has no terminal to read that message in, so `AppRun`
closes the gap: with no arguments and a graphical session it waits for
`/api/health` to answer and then opens the address with `xdg-open` (falling
back through `gio`, `x-www-browser`, `sensible-browser`, and the common browser
binaries). It never opens a tab it has not confirmed first, and it stops
waiting as soon as the app exits. `JARVIS_APPIMAGE_NO_BROWSER=1` turns it off.

Whether to do this at all is decided at build time, not guessed: `build.sh`
inspects the freeze for PyGObject and writes
`usr/share/personal-jarvis/browser-ui` only when there is no window backend. A
future build that does bundle one stops opening the browser tab by itself.

**One rough edge, stated plainly:** the app's own fallback message suggests
installing `python3-gi` / `gir1.2-webkit2-4.1`. For a frozen build that advice
does not apply (see above). It lives in `jarvis/ui/desktop_app.py`
(`_degrade_to_browser_ui`), which is where a frozen-aware wording belongs.

## Using it as the CLI

`AppRun` passes every argument straight through, so the AppImage *is* the
command-line tool:

```bash
chmod +x PersonalJarvis-Linux-x86_64.AppImage
./PersonalJarvis-Linux-x86_64.AppImage --version
./PersonalJarvis-Linux-x86_64.AppImage serve       # headless API + browser UI
./PersonalJarvis-Linux-x86_64.AppImage --doctor
```

To type `jarvis` instead, link it once:

```bash
mkdir -p ~/.local/bin
ln -sf "$PWD/PersonalJarvis-Linux-x86_64.AppImage" ~/.local/bin/jarvis
```

The Debian package does this for you: it installs `/usr/bin/jarvis` (the
console entry) and `/usr/bin/personal-jarvis` (the launcher).

On a host without FUSE - containers, some hardened servers - run the AppImage
with `--appimage-extract-and-run` (or `APPIMAGE_EXTRACT_AND_RUN=1`). That is
also how the build machine runs `appimagetool` itself.

## Desktop registration

* **`.deb`** registers the app the normal way: a `.desktop` entry in
  `/usr/share/applications`, the icon in the hicolor theme, and the two
  commands on `PATH`. `apt remove personal-jarvis` takes all of it away again.
* **AppImage** carries its `.desktop` entry and icon inside itself, which is
  what AppImageLauncher and `appimaged` read when they offer to "integrate"
  the file. Without one of those tools an AppImage is just a file you run -
  that is how the format works, and Personal Jarvis does not fight it.

The app itself registers **nothing** when it is a frozen build:
`jarvis/setup/desktop_integration.py` and the writers in
`jarvis/ui/icon_utils.py` return a logged no-op under
`jarvis.core.frozen.is_frozen()`. They would otherwise write an entry whose
`Exec=` points at `sys.executable` - which, inside an AppImage, is a path in a
temporary mount that disappears the moment the app exits.

## Updates

The in-app updater replaces the running AppImage in place: it downloads the new
`PersonalJarvis-Linux-x86_64.AppImage` from the GitHub Release, verifies it
against `installers-SHA256SUMS.txt` from the same release, atomically replaces
the file `$APPIMAGE` points at, restores the executable bit and relaunches.
A `.deb` install updates through the package, not through the app.

## What has actually been tested

Proven in a `python:3.12-bookworm` container on 2026-08-25, against a real
156 MB AppImage built by this script:

* `appimagetool` digest check, AppDir assembly, `desktop-file-validate` passes
* `--version` through the packaged AppImage (`APPIMAGE_EXTRACT_AND_RUN=1`),
  through the extracted `AppRun`, and through `usr/bin/jarvis`
* `AppRun serve` boots the backend and `/api/health` answers in ~3 s
* the browser hand-off calls the opener with `http://127.0.0.1:47821`
* the Debian package's control file, symlinks and desktop entry

Not tested: a real FUSE-mounted run on a desktop distribution, and the app on a
machine that actually has a display. Both belong in the CI job and on a real
Linux desktop before release.
