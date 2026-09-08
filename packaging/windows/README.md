# Windows packaging

Two packages come out of the same frozen bundle (`dist\Jarvis`, built by
`jarvis.spec`):

| Script | Output | Who installs it |
| --- | --- | --- |
| `build.ps1` | `dist\installers\PersonalJarvis-Setup-x64.exe` - per-user Inno Setup wizard | the website's download button, the in-app updater |
| `build-msix.ps1 -SkipPyInstaller` | `dist\installers\PersonalJarvis-x64.msix` - Microsoft Store package | Partner Center only (see below) |

Both run unchanged on a maintainer's machine and in
`.github/workflows/desktop-installers.yml`, so a green CI run means the
documented command works.

## Why there are two ways past SmartScreen

Windows SmartScreen warns on any downloaded executable it has no reputation
for. Reputation is earned per signing certificate over weeks of clean
installs; a fresh certificate - paid or free, OV or EV - starts at zero, and
an unsigned file starts at zero again with **every release**. Microsoft's own
guidance ([SmartScreen reputation](https://learn.microsoft.com/windows/apps/package-and-deploy/smartscreen-reputation))
names two remedies, and this repository uses both:

1. **Sign the setup** so reputation accrues on the certificate and carries
   over from release to release. The certificate comes from the
   [SignPath Foundation](https://signpath.org), which signs open-source
   projects for free; the publisher shown on the file is
   *SignPath Foundation*.
2. **Publish through the Microsoft Store.** The Store re-signs the package
   with Microsoft's certificate; a Store install never sees the SmartScreen
   prompt at all, and `winget install` can point at the Store listing.

## Code signing (SignPath Foundation)

The workflow's `windows` job uploads the unsigned setup as the artifact
`installer-windows-x64-unsigned`, submits it to SignPath by artifact id,
waits for the signed file, verifies the Authenticode signature with
`Get-AuthenticodeSignature`, and only then lets the file become
`installer-windows-x64` for the release. No private key ever exists on the
runner or in this repository. Without the secrets the job builds an unsigned
setup and prints a notice - a fork is not broken by not being SignPath's
project.

Secrets (Settings > Secrets and variables > Actions):

| Name | Kind | Value |
| --- | --- | --- |
| `SIGNPATH_API_TOKEN` | secret | API token of a SignPath user with the *Submitter* role on the project |
| `SIGNPATH_ORGANIZATION_ID` | secret | the organization id SignPath shows under Settings |
| `SIGNPATH_PROJECT_SLUG` | variable, optional | defaults to `PersonalJarvis` |
| `SIGNPATH_SIGNING_POLICY_SLUG` | variable, optional | defaults to `release-signing` |

SignPath-side configuration, once the Foundation has approved the project:

1. In the SignPath project, connect the **GitHub Actions trusted build
   system** to `PersonalJarvis/PersonalJarvis` (Trusted build systems >
   GitHub). This is what lets SignPath verify that a signing request really
   came from a workflow run on this repository, not from a laptop.
2. Add an **artifact configuration** for the setup. GitHub artifacts arrive
   as a zip, so the configuration wraps the executable:

   ```xml
   <artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
     <zip-file>
       <pe-file path="PersonalJarvis-Setup-x64.exe">
         <authenticode-sign />
       </pe-file>
     </zip-file>
   </artifact-configuration>
   ```

3. Create the signing policy `release-signing` with the Foundation's release
   certificate, **manual approval** on, and the origin restricted to tags of
   this repository. Every release signature is then a click by the approver
   named in the [code signing policy](https://personaljarvis.ai/docs/code-signing/).

The website's policy page and the installation guide say the project uses
SignPath Foundation; keep them in step with this file when anything here
changes.

## Microsoft Store package (MSIX)

`build-msix.ps1` stages `dist\Jarvis` unchanged, renders the Store tiles from
`assets\icons\jarvis-gigi-256.png`, fills `msix\AppxManifest.xml` and packs
the folder with `makeappx` from the Windows SDK (preinstalled on GitHub's
`windows-latest`). The package is **unsigned on purpose**: an unsigned MSIX
cannot be installed by double-click, and the Store signs it itself after
certification. It is therefore a workflow artifact (`msix-windows-x64`), not a
release asset, and the website never links to it.

The identity has to match what Partner Center reserved for the app name, or
the submission is rejected. Set these repository **variables** from
Partner Center > the app > Product management > Product identity:

| Variable | Manifest field |
| --- | --- |
| `MSSTORE_IDENTITY_NAME` | `Package/Identity/Name` |
| `MSSTORE_PUBLISHER` | `Package/Identity/Publisher` (`CN=...`) |
| `MSSTORE_PUBLISHER_DISPLAY_NAME` | `Package/Properties/PublisherDisplayName` |

Without them the script packs with placeholders and warns, so the packaging
path is exercised on every run while the Store safety net stays in place.

Submitting a release: download the `msix-windows-x64` artifact of the tag's
workflow run and upload it in Partner Center > the app > Packages. The
manifest declares `runFullTrust` (the app is a classic Win32 program), the
`microphone` device capability and `internetClient`; the Store lists those on
the product page.
