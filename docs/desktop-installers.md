# Desktop installers

The public native release supports Windows x64 and Linux x86_64. macOS users
install the Python package or use the source/CLI installer. The macOS bundle
builder is kept for optional local diagnostics; the public release does not
build, qualify, sign, notarize, or publish a DMG.

## Public release assets

| Asset | Purpose |
| --- | --- |
| `PersonalJarvis-Setup-x64.exe` | Windows 10/11 x64 per-user installer |
| `PersonalJarvis-Linux-x86_64.AppImage` | Linux x86_64 native package |
| `personal-jarvis-src.tar.gz` | Source archive bound to the release commit |
| `installers-SHA256SUMS.txt` and `.cosign.sig` | Version-bound checksums and signed manifest |
| `release-qualification.json` | Commit, asset hashes, and native test evidence |

The signed CLI install scripts and their provenance are published alongside
these assets. They remain available on Windows, macOS, and Linux. The Python
package is published through the top-level release workflow. The exact gate,
account requirements, and outstanding acceptance checks are documented in
[the release pipeline](release-pipeline.md).

## Building native packages

The release workflow calls the same builders a maintainer can run locally:

```powershell
pwsh packaging/windows/build.ps1
```

```bash
./packaging/linux/build.sh
```

Both package the PyInstaller application and CLI. The Windows builder uses
Inno Setup for a per-user installation; the Linux builder produces an AppImage
on the oldest supported CI runner so its glibc requirement stays bounded.
`packaging/macos/build.sh` can still produce a local diagnostic bundle, but
its output is outside the release matrix and is never a required artifact.

Frozen installs use `packaging/pyinstaller_rthook_frozen.py` to direct settings
and data to a per-user directory outside the replaceable program files. An
explicit `JARVIS_CONFIG` or `JARVIS_DATA_DIR` override still wins. The Linux
AppImage also resolves its default memory store through that writable data
root instead of the read-only mount.

## Qualification and signing

A tag release requires a successful functional CI run for the exact commit,
an earlier public Windows and Linux native version for upgrade tests, native
installation and rollback proof for both targets, a verified Windows installer
signature, and the signed checksum manifest. Windows uses the approved
SignPath Foundation route by default; Azure Artifact Signing is an explicit
paid override. Linux integrity is bound to the signed manifest. Missing
credentials, missing assets, failed preservation checks, or mismatched hashes
block publication.

Manual branch runs may produce unsigned diagnostic artifacts. They are not
public release evidence. The workflow stages qualified assets before the
release coordinator publishes them, and never overwrites an already public
release. No Apple developer account or Apple signing credential is required.

## Updating an installed copy

The in-app updater selects a native package only for a supported frozen
Windows or Linux installation. It verifies the release manifest and asset
hash before executing an installer. An unsupported platform or install kind
must not receive an incompatible package. Managed/source installations keep
their separate update transaction; macOS follows that path in the public
release. A failed native upgrade restores the previous application, and its
health check must confirm the running version before reporting success.

User settings, conversations, and local credentials remain outside the
replaceable program directory. A binary rollback does not undo a destructive
data migration, so migrations require their own compatibility checks.
