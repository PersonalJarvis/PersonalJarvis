# Native release pipeline

Status: implementation and qualification in progress. Do not treat local unit
tests, unsigned diagnostic builds, or package reinstallation as evidence of a
signed, notarized, cross-platform release.

## Supported desktop packages

| Operating system | Processor | Public installer |
| --- | --- | --- |
| Windows 10/11 | x64 | `PersonalJarvis-Setup-x64.exe` |
| macOS | Apple Silicon | `PersonalJarvis-macOS-arm64.dmg` |
| macOS | Intel x64 | `PersonalJarvis-macOS-x64.dmg` |
| Linux | x86_64 | `PersonalJarvis-Linux-x86_64.AppImage` |

Windows ARM, Linux ARM, mobile devices and ChromeOS have no supported native
package in this matrix. Never substitute an installer for a different OS.
The diagnostic Debian package is not a supported automatic-update target.

End users open the Windows installer or drag the macOS app to Applications.
For Linux, enable execution in the AppImage's file properties and open it.
AppImage desktop integration depends on the distribution; a terminal command
is not the primary installation path. No downloader installs Python or Git.

The website is maintained in the separate `personaljarvis.github.io`
repository. Its download component resolves exact assets from one complete
stable release. Unknown processors open the manual chooser. In particular,
an Intel-looking macOS browser user agent does not prove an Intel processor.
JavaScript-disabled clients receive a release-page link for manual selection.

## Public build and release entry points

- `.github/workflows/release.yml` is the sole top-level tag release coordinator.
- `.github/workflows/desktop-installers.yml` coordinates native builds and
  qualification as a reusable workflow; manual branch runs produce diagnostic
  artifacts only.
- `packaging/windows/build.ps1`, `packaging/macos/build.sh` and
  `packaging/linux/build.sh` are the same builders used by CI.
- `.github/workflows/sign-installer.yml` retains the existing installer-script
  signatures and provenance as a reusable workflow.
- Python trusted publishing stays in the top-level `release.yml` workflow to
  preserve its registered PyPI identity. Qualification precedes publishing.
- `scripts/ci/prepare_native_release.py` checks required installer assets and
  qualification evidence before producing the release manifest.

The final workflow uploads into a draft and publishes only after all required
jobs, signatures, notarization and evidence checks succeed. A failed upload
leaves a draft, not a partially visible release. Published assets must never
be overwritten. Workflow success is required on the exact candidate commit;
an old green run does not qualify a new commit.

## Trust and updates

`installers-SHA256SUMS.txt` binds the release tag and installer hashes.
`installers-SHA256SUMS.txt.cosign.sig` is the base64-encoded Ed25519 signature
over the exact manifest bytes, using the existing Wave-2 key. The application
ships the public trust root and refuses missing, altered, wrong-key or
wrong-version manifests before executing an installer. A public key supplied
by the same download is not a trust root.

Previously distributed binaries cannot acquire this verification logic until
they themselves are upgraded. In particular, a legacy SHA-only updater does
not retroactively become a signature verifier because a release adds a
signature asset. Users requiring verification for that first transition need
the new signed installer through the manual download path; do not claim that
old Linux binaries already enforce the new trust contract.

The app checks for stable releases automatically. Selecting the update offer
first presents an install confirmation; cancelling sends no apply request.
The native installer owns shutdown and relaunch. The UI must not separately
restart the old app while the installer is replacing it.

User profiles, configuration, operating-system credential stores, conversations
and other data must stay outside the replaceable application bundle. Rollback
must retain the prior program until the candidate passes its startup check.
Data migrations must remain backward compatible or supply their own tested
transaction; replacing application binaries alone cannot undo destructive
database migrations. No such migration is introduced here.

## Accounts, secrets and cost

Checked against vendor documentation on 2026-09-26. A universal zero-cost claim
for trusted Windows signing plus Apple notarization would be incorrect.

| Item | Cost and prerequisite |
| --- | --- |
| Standard GitHub-hosted CI for this public repository | Runner usage is free; avoid larger paid runners and keep artifact retention bounded. Check storage allowances separately. |
| Ed25519 manifest and Linux package integrity | No certificate subscription. The existing signing key stays in GitHub Actions secrets. |
| Windows SignPath Foundation | Free for accepted open-source projects; requires application, policy setup and Foundation approval. Approval is not automatic. |
| Existing Azure Artifact Signing integration | Paid option: Basic is USD 9.99/account/month, with usage limits. Do not provision it under a strict zero-cost requirement. |
| macOS Developer ID and notarization | Apple Developer Program: USD 99/year, or an approved fee waiver for eligible nonprofits, educational institutions or government entities. Being open source alone does not grant a waiver. |

Sources: [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions),
[SignPath Foundation](https://signpath.org/),
[SignPath eligibility](https://signpath.org/terms.html),
[Azure pricing](https://learn.microsoft.com/en-us/azure/artifact-signing/how-to-change-sku),
[Apple membership](https://developer.apple.com/support/compare-memberships/),
[Apple fee waivers](https://developer.apple.com/help/account/membership/fee-waivers).

Keep private signing keys outside Git, including encrypted copies. Existing
`WAVE2_OFFLINE_KEY_B64` and `WAVE4_MLDSA65_KEY_B64` are Actions secrets. Apple
requires `APPLE_CERTIFICATE_P12_BASE64`, `APPLE_CERTIFICATE_PASSWORD`,
`APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_TEAM_ID` and
`APPLE_APP_SPECIFIC_PASSWORD`. Import the certificate into a temporary runner
keychain and remove it after the job. Azure, if explicitly chosen, uses the
existing `AZURE_*` secret fields in the workflow. Never print secret values.
Certificate and notarization setup are maintainer responsibilities, not
downloaders' setup steps.

No paid account is created by the repository scripts. If the strict zero-cost
condition cannot be met, publication must remain blocked instead of silently
falling back to unsigned or ad-hoc signed packages.

## Acceptance evidence still required

Each of the four native targets needs a fresh-profile installation and actual
previous-version-to-candidate upgrade, signature rejection, forced failed
upgrade with recovery of the previous working version, and preserved user
state. A successful process spawn alone is not a health check. Run the tests
on their native operating systems; local fakes do not prove Gatekeeper,
SmartScreen, keychain access or a Linux desktop environment.

Fresh installation with one arbitrary provider key and actual desktop use
remain separate acceptance requirements. Record only non-secret evidence.
Never label unsigned diagnostics, missing accounts or unexecuted CI as PASS.
The native preservation fixture covers an application chat store and the
credential-file fallback. It does not establish OS keychain behavior on a real
desktop; that remains a separate acceptance check.
