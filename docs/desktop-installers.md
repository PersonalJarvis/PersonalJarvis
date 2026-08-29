# Desktop installers

How Personal Jarvis becomes a one-click download, how each installer is built
and signed, and how an installed copy keeps itself up to date.

This is the maintainer's page. A user never reads it: they press one button on
the website, run a normal install wizard, and the app updates itself afterwards.

---

## 1. What a release publishes

Every `v*.*.*` tag produces these assets on the GitHub Release. The names are a
contract - the website's download button and the in-app updater both resolve
`https://github.com/PersonalJarvis/PersonalJarvis/releases/latest/download/<name>`:

| Asset                                  | Platform                                  |
| -------------------------------------- | ----------------------------------------- |
| `PersonalJarvis-Setup-x64.exe`         | Windows 10/11 x64, per-user Inno Setup    |
| `PersonalJarvis-macOS-arm64.dmg`       | Apple Silicon                             |
| `PersonalJarvis-macOS-x64.dmg`         | Intel Mac                                 |
| `PersonalJarvis-Linux-x86_64.AppImage` | Linux x86_64                              |
| `installers-SHA256SUMS.txt`            | what the updater verifies every download against |

The terminal one-liner and `pipx install personal-jarvis` stay supported. They
produce a *managed* or *dev* install, which updates through git - see
[section 6](#6-how-an-installed-copy-updates-itself).

---

## 2. The frozen bundle

Everything starts from one PyInstaller build:

```bash
pyinstaller jarvis.spec --noconfirm --clean
```

`onedir`, not `onefile`: no 3-5 second extraction on every launch, and each DLL
can be signed on its own.

It produces **two executables that share one payload**:

| Binary                    | Console? | What it is                                                       |
| ------------------------- | -------- | ---------------------------------------------------------------- |
| `PersonalJarvis` (`.exe`) | no       | the windowed app - Start Menu, Dock, `.desktop`                   |
| `jarvis` (`.exe`)         | yes      | the CLI: `jarvis serve`, `jarvis --version`, `jarvis missions list` |

Both run `jarvis/__main__.py`, so the CLI from a native install behaves exactly
like the pip console script.

> **Why the GUI binary is not called `Jarvis`.** `Jarvis` and `jarvis` are the
> same path on Windows (NTFS) and on a default macOS APFS volume, so one would
> silently overwrite the other. `PersonalJarvis` is the name
> `jarvis.core.branding` already uses for the Windows branded launcher and the
> macOS bundle executable.

Layout:

- Windows / Linux: `dist/Jarvis/` with both binaries plus `_internal/`
- macOS: `dist/Personal Jarvis.app`, CLI at `Contents/MacOS/jarvis`

### Where a frozen install keeps user data

`packaging/pyinstaller_rthook_frozen.py` runs before anything imports `jarvis`
and points the two documented overrides at the per-user application directory:

- `JARVIS_CONFIG` -> `%LOCALAPPDATA%\Jarvis\jarvis.toml` (`~/.jarvis/jarvis.toml`
  off Windows), seeded once from the copy inside the bundle
- `JARVIS_DATA_DIR` -> `<that directory>/data`

Without it, `jarvis.core.config` would anchor both to the bundle's own
directory, where an in-place upgrade overwrites the user's settings and an
uninstall deletes their memory. An explicitly exported `JARVIS_CONFIG` /
`JARVIS_DATA_DIR` still wins, so a maintainer can point a frozen build at a
scratch directory.

---

## 3. Building each installer

All three scripts do the same three things: build the frontend if
`jarvis/ui/web/dist` is missing, run PyInstaller, package the result into
`dist/installers/`. Each prints the artifact path and exits non-zero on failure.

### Windows

```powershell
pwsh packaging/windows/build.ps1
# -> dist/installers/PersonalJarvis-Setup-x64.exe
```

Needs Node 22+, Python 3.12 with `pip install -e ".[desktop,dev]"`, and Inno
Setup 6 (`winget install --id JRSoftware.InnoSetup -e`). The script finds
`ISCC.exe` on `PATH`, under `Program Files (x86)`, or in
`%LOCALAPPDATA%\Programs\Inno Setup 6`.

Two flags shorten the loop: `-SkipPyInstaller` repackages the existing
`dist/Jarvis` (seconds instead of ~12 minutes) and `-SkipFrontend` refuses to
run npm.

The wizard itself is `packaging/windows/PersonalJarvis.iss`:

- **per-user** (`PrivilegesRequired=lowest`, `{localappdata}\Programs\Personal
  Jarvis`) - no administrator prompt, exactly like Chrome. This is also what
  lets the in-app updater run the upgrade unattended.
- a **fixed `AppId` GUID** so every future setup upgrades in place instead of
  adding a second entry to "Installed apps"
- Start Menu shortcut; optional desktop shortcut (off by default)
- optional **"Add the `jarvis` command to PATH"** (on by default). Implemented
  in `[Code]` against `HKCU\Environment` and broadcast with `WM_SETTINGCHANGE`,
  so a newly opened terminal sees it without a sign-out. Adding is idempotent,
  and clearing the task on an upgrade removes the entry again.
- `CloseApplications=yes` so Restart Manager closes a running Personal Jarvis
  instead of failing on locked files
- LZMA2/max, `x64compatible` only, `MinVersion=10.0`

### macOS

```bash
./packaging/macos/build.sh
# -> dist/installers/PersonalJarvis-macOS-<arm64|x64>.dmg
```

Owned by `packaging/macos/`. It reads `APPLE_SIGNING_IDENTITY`, `APPLE_ID`,
`APPLE_TEAM_ID` and `APPLE_APP_SPECIFIC_PASSWORD`; with none of them set it
ad-hoc signs, skips notarization and prints a one-line notice. The `.app`'s
identity (bundle id, minimum system version, and the microphone / speech /
camera / Apple-Events usage strings macOS requires before it will let the
process touch those APIs) comes from the `BUNDLE` block in `jarvis.spec`.

### Linux

```bash
./packaging/linux/build.sh
# -> dist/installers/PersonalJarvis-Linux-x86_64.AppImage
```

Owned by `packaging/linux/`. Built on the oldest supported runner
(`ubuntu-22.04`) because an AppImage inherits the glibc of the machine that
built it.

---

## 4. Signing

Signing is optional everywhere. Without secrets the build still produces a
working installer and says, in one line, that it is unsigned.

| Platform | Mechanism                    | Secrets                                                                                                     |
| -------- | ---------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Windows  | Azure Trusted Signing        | `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_SIGNING_ENDPOINT`, `AZURE_CODE_SIGNING_ACCOUNT`, `AZURE_CERTIFICATE_PROFILE` |
| macOS    | Developer ID + notarization  | `APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_SPECIFIC_PASSWORD`                          |
| Linux    | none (AppImage is unsigned)  | -                                                                                                             |

Private key material only ever exists as a GitHub Actions secret (AP-29); no
signing step ever reads a file from the repository.

On Windows the workflow signs the **setup executable**. That is the file the
browser marks with Mark-of-the-Web, so it is the signature SmartScreen weighs;
files the installer then writes carry no MOTW of their own. Signing the inner
binaries as well (run PyInstaller, sign `dist/Jarvis`, then
`build.ps1 -SkipPyInstaller`) mainly helps against antivirus false positives on
PyInstaller bootloaders, and can be added the same way.

---

## 5. Releasing

`.github/workflows/desktop-installers.yml` runs on a `v*.*.*` tag push (or
manually via `workflow_dispatch`):

1. `windows`, `macos` (arm64 + x64) and `linux` each install Python 3.12 and
   Node 22, `pip install -e ".[desktop,dev]"`, run their OS build script and
   upload the artifact.
2. `release` downloads all of them, refuses to continue if any promised asset is
   missing, verifies the tag equals `jarvis.__version__`, writes
   `installers-SHA256SUMS.txt` (plain `sha256sum` format, flat file names),
   re-verifies it with `sha256sum -c`, and attaches everything to the Release.

`contents: write` is granted to that last job only.

The version check matters twice over: a tag that disagrees with
`jarvis.__version__` publishes installers nobody can trace back to a commit, and
the updater compares the release version against `jarvis.__version__` - so a
mismatch would offer an update that installs the same build forever.

---

## 6. How an installed copy updates itself

`GET /api/update/status` and `POST /api/update/apply` serve all install kinds
behind one "Update Now" button. `jarvis.core.frozen.is_frozen()` is the only
switch between them.

| Kind        | Detected by                          | Update path                                    |
| ----------- | ------------------------------------ | ---------------------------------------------- |
| **frozen**  | `sys.frozen` + `sys._MEIPASS`        | download the next installer asset, verify, install |
| **managed** | install marker + official `origin`   | `git fetch` the published tag, reinstall       |
| **dev**     | anything else                        | never updates                                  |

For a frozen install (`jarvis/core/installer_update.py`):

1. **Pick the asset.** OS plus CPU architecture map to exactly one name from the
   table in section 1. No mapping (Windows on ARM, Linux on aarch64) means the
   button never appears.
2. **Refuse to offer what cannot be installed.** `status` reports
   `update_available: true` only when the release actually carries both this
   platform's installer and `installers-SHA256SUMS.txt`. Status is fail-open:
   any network error reports "no update", never an error.
3. **Verify before executing.** `apply` downloads to a temp directory under a
   size cap, computes SHA-256 and compares it against the manifest **of the same
   release**. A mismatch, a missing entry, an oversized body or an unreachable
   manifest all delete the download and refuse. Apply is fail-closed.
4. **Hand over to the platform.**
   - Windows: runs the new `Setup.exe` detached with
     `/SILENT /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS /NORESTART`. Restart
     Manager closes the app, the files are replaced, the app comes back.
   - macOS: `hdiutil attach` the DMG, swap the running `.app` with two renames
     on one filesystem (the old bundle is put back if the second rename fails),
     `hdiutil detach`, relaunch with `open`.
   - Linux: copy the new AppImage next to the running one, `chmod +x`,
     `os.replace` onto `$APPIMAGE` (atomic), relaunch.

The response says `restart_required: false`, because the handover restarts the
app itself. A caller that restarts anyway is harmless - the single-instance lock
still holds - but the honest answer is "no restart needed from you".

Managed installs keep today's behaviour untouched: the frozen branch is entered
only when `is_frozen()` is true, and the frozen branch never runs git.

---

## 7. What an uninstall keeps

The Windows uninstaller removes the **program directory only**
(`%LOCALAPPDATA%\Programs\Personal Jarvis`) and its PATH entry, Start Menu
shortcut and uninstall registry key.

It deliberately keeps **`%LOCALAPPDATA%\Jarvis`** - `jarvis.toml`, memory,
skills, logs, the chat database. Uninstalling and reinstalling therefore returns
the user to their own configured app, not to a blank one. The uninstall
confirmation says so. To remove that too, delete the directory by hand or run
`jarvis --uninstall` before uninstalling.

API keys are not in that directory: they live in the OS credential store
(Windows Credential Manager / Keychain / Secret Service) and survive both.

---

## 8. Verifying a build by hand

```bash
# the CLI works from the bundle
dist/Jarvis/jarvis.exe --version

# the backend really boots (Ctrl-C to stop)
dist/Jarvis/jarvis.exe serve
curl http://127.0.0.1:<admin_api_port>/api/health

# a full install / upgrade / uninstall cycle, unattended
PersonalJarvis-Setup-x64.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="C:\pj-test" /LOG=install.log
C:\pj-test\jarvis.exe --version
PersonalJarvis-Setup-x64.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="C:\pj-test"   # upgrade in place
C:\pj-test\unins000.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

Use a **short** test directory. The deepest path in the bundle is about 111
characters; a real install prefix adds roughly 60, which fits inside Windows'
260-character limit with room to spare, but a deeply nested scratch directory
does not, and Inno then aborts with `MoveFile failed; code 3`.

Never run the app out of `dist/Jarvis` and then package that directory: the app
writes runtime state into it, which would ship the builder's own database inside
the installer. `build.ps1` removes such leftovers before packaging, and a clean
`--clean` build never has them.
