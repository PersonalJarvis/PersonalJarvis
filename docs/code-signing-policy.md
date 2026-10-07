# Code signing policy

Free code signing provided by [SignPath.io](https://signpath.io), certificate by
[SignPath Foundation](https://signpath.org).

> **Status:** Personal Jarvis has applied to the SignPath Foundation open-source
> program. Until the application is approved and the first signed release ships,
> the Windows installer on the
> [Releases page](https://github.com/PersonalJarvis/PersonalJarvis/releases) is
> **unsigned**. Each release's workflow log states whether it was signed.

## What is signed

| Artifact | Platform | Signed by |
| --- | --- | --- |
| `PersonalJarvis-Setup-x64.exe` | Windows 10/11 x64 | SignPath Foundation certificate, via SignPath.io |

Only files built from this repository's source code by the
[`Desktop installers`](../.github/workflows/desktop-installers.yml) GitHub Actions
workflow are submitted for signing. The workflow runs on GitHub-hosted runners
for a release tag. SignPath.io verifies that each submitted file was produced by
that workflow before it signs anything. Third-party components bundled in the
installer keep their own publishers' signatures (or none). They are never signed
with this project's certificate.

The macOS disk images and the Linux AppImage are not covered by this policy.

## Team roles

| Role | Who |
| --- | --- |
| Committers and reviewers | [Members of the PersonalJarvis organization](https://github.com/orgs/PersonalJarvis/people) |
| Approvers | [Owners of the PersonalJarvis organization](https://github.com/orgs/PersonalJarvis/people?query=role%3Aowner) |

- **Committers** may change the source code in this repository.
- **Reviewers** review every pull request from people outside the team before it
  is merged.
- **Approvers** approve each signing request in SignPath by hand. No release is
  signed without that approval.

All team members use multi-factor authentication for GitHub and SignPath.

## How a release gets signed

1. A maintainer pushes a `v*.*.*` release tag.
2. The workflow builds the installer and uploads the unsigned file as a workflow
   artifact.
3. The workflow sends a signing request to SignPath.io, which checks the file's
   origin.
4. An approver reviews the request and approves it in SignPath.
5. The workflow downloads the signed installer, checks that its Authenticode
   signature is valid, timestamped and issued to SignPath Foundation, and only
   then publishes it.

## Privacy

This program will not transfer any information to other networked systems
unless specifically requested by the user or the person installing or operating
it. Personal Jarvis contacts a service only when you connect and use it, for
example a model provider, a speech provider or a plugin you configured. Those
services handle data under their own privacy policies. Details are in
[Privacy and Local Data](product/privacy-safety-and-support/privacy-and-local-data.md)
and the privacy section of [SECURITY.md](../SECURITY.md#privacy).

## Report a problem

If you believe a file signed with this project's certificate is malicious or
breaks this policy, report it privately as described in
[SECURITY.md](../SECURITY.md). You can also contact SignPath at
support@signpath.io.

## Maintainer setup

The signing path in the workflow stays dormant until these are set in the
repository settings:

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `SIGNPATH_API_TOKEN` | API token of a SignPath user with submitter rights |
| Variable | `SIGNPATH_ORGANIZATION_ID` | SignPath organization ID |
| Variable | `SIGNPATH_PROJECT_SLUG` | SignPath project slug |
| Variable | `SIGNPATH_SIGNING_POLICY_SLUG` | Signing policy slug, usually `release-signing` |
| Variable (optional) | `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG` | Artifact configuration slug; empty uses the project default |

The workflow uploads the installer as a ZIP workflow artifact, so the SignPath
artifact configuration wraps the setup file in a ZIP and pins its metadata:

```xml
<artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
  <zip-file>
    <pe-file path="PersonalJarvis-Setup-x64.exe" product-name="Personal Jarvis">
      <authenticode-sign />
    </pe-file>
  </zip-file>
</artifact-configuration>
```

Without these settings the workflow prints a notice and publishes an unsigned
installer. If both SignPath and Azure Trusted Signing are configured, SignPath
is used.
