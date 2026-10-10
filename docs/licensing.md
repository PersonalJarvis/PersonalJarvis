# Licensing

## Current: Apache License 2.0

Personal Jarvis project code is licensed under the
[Apache License 2.0](../LICENSE), with the attribution notice in
[`NOTICE`](../NOTICE), starting with version 2.0.0. Third-party code, fonts,
models, images, and native libraries retain their own licenses.

## Released 1.x stays MIT, permanently

Every release up to and including 1.6.0 was published under the MIT License and
remains MIT. That cannot be revoked: a copy obtained under MIT keeps those
rights forever, including the right to fork it. The text those releases shipped
with is still in the history — `git show v1.6.0:LICENSE`.

## Why Apache 2.0

Apache 2.0 permits use, modification, and distribution, including commercial
distribution, subject to its conditions. This describes the project code;
bundled dependencies can impose additional obligations.

- **An explicit patent grant** (§3). Every contributor grants users a license to
  applicable patent claims described in the license. Section 3 terminates
  that grant for the work if the recipient brings the specified patent
  infringement litigation. MIT contains no equivalent express patent grant.
- **A trademark carve-out** (§6). The license covers the code and not the name
  or the logo, in writing. Ours already lives in
  [`TRADEMARK.md`](../TRADEMARK.md); Apache 2.0 makes it a license term.
- **Stated redistribution duties** (§4). Pass on the license, keep the notices,
  carry the `NOTICE` file, mark the files you changed. MIT also requires
  preservation of its copyright and permission notice.

## What it means for you

- **Users:** nothing to do. The freedoms are the same, and you gain a patent
  license you did not have before.
- **Forks and redistributors:** anything you took under 1.x stays MIT. From
  2.0 on, ship `LICENSE` and `NOTICE` with your copies and mark the files you
  modified.
- **Contributors:** by opening a pull request you agree your work is licensed to
  the project under the Apache License 2.0, per §5 of the License.

## What does NOT need changing

A license change applies going forward. It does not make past statements wrong,
and it creates no duty to hunt them down:

- **Videos, talks, streams, and old posts that say "MIT".** They described the
  releases that existed when they were recorded, and for those releases the
  statement is still true. Leave them up. Adding a line to the description of
  the most-watched ones ("from 2.0 on: Apache 2.0") is courtesy, not a
  correction — taking them offline would look like something was wrong when
  nothing was.
- **Anything already downloaded, forked, or vendored.** It stays MIT, and no
  one has to be told.
- **Blog posts, press coverage, and third-party listings.** Not yours to fix.

What does have to be current is anything that states the license **as of
today**: this repository, package listings, app-store
descriptions, and pinned posts. Those are the checklist below.

## Third-party components

The warranty and liability disclaimers in an open-source license do not replace
its redistribution conditions. The project license also does not grant rights
in another party's trademarks. The project name and the assistant name chosen
by a user are separate labels; choosing an assistant name does not change any
bundled component's license.

### What builds from the corrected source include

The following describes the packaging corrections accompanying the linked
artifact audit. Previously published installers retain their original contents
until a separately verified release is published.

- The Python wheel carries the project and vendored component licenses in its
  distribution metadata. The desktop bundle also includes the `third_party/`
  license texts referenced by `NOTICE`.
- Each frontend build emits `THIRD_PARTY_NOTICES.txt` alongside its assets. It
  contains complete license texts for bundled packages and fonts, plus notices
  for copied source and artwork. These notices travel with the frontend in
  wheels and desktop bundles.
- The current onboarding terms are included in both wheel and desktop layouts.
  They describe the optional project-operated OAuth token broker as well as
  directly connected third-party services.

License files document permissions and conditions; they do not by themselves
prove that a particular native binary satisfies every condition. A source
checkout, a dependency installed by the user, and a frozen installer are
different distribution artifacts and must be assessed separately.

### Native libraries and models

Inspect the exact binaries in each release, including their transitive native
dependencies. In particular:

- `sherpa-onnx` has an Apache-2.0 top-level license, but some native builds
  include GPL-licensed eSpeak through `piper-phonemize`. Selecting sherpa rather
  than `piper1-gpl` does not establish that a distribution is free of GPL
  obligations. See the [upstream dependency discussion](https://github.com/k2-fsa/sherpa-onnx/issues/3731).
- PyAV's source license does not cover all bundled FFmpeg and codec binaries.
  Review the exact wheel's FFmpeg build options, external codecs, and any
  documented license exceptions. Preserve the notices and corresponding source
  or other materials required by the selected license route. See
  [FFmpeg's licensing guidance](https://ffmpeg.org/legal.html) and the
  [PyAV wheel discussion](https://github.com/PyAV-Org/PyAV/issues/2270).
- Downloaded voice and wake models have licenses separate from the engine that
  loads them. Record the model source, version, and applicable terms.

Keep an artifact-level inventory of component versions, file hashes, licenses,
upstream source/build references, and unresolved permissions. Claims of
commercial exceptions require the applicable grant, not only a package label
or a link to a vendor's sales page. Public source for this project alone is
not a substitute for the corresponding source of bundled third-party binaries.

The [v2.9.0 artifact inspection](legal/distribution-audit-2026-10-08.md)
records verified Windows and Linux package identities, observed components,
missing notices, and the limits of that inspection.

### Adapted source

The bundled Silero VAD model (`jarvis/assets/vad/`) carries its own MIT license
from its own authors and keeps it — the switch does not touch it, and no
dependency's terms change either. Nothing is relicensed by being included here.

The Agentic IDE thread work log contains portions adapted from
[T3 Code](https://github.com/pingdotgg/t3code) @ `e22c880`, MIT License,
Copyright (c) 2026 T3 Tools Inc. The full text ships in
[`third_party/t3code/LICENSE`](../third_party/t3code/LICENSE), and every adapted
file names its source in a header comment.

The Computers settings page (`jarvis/ui/web/frontend/src/views/computers/surface.tsx`
and `ComputerRow.tsx`) contains portions of the settings layout, environment row,
status dot and empty state adapted from T3 Code @ `12069ee` under the same MIT
license; both files name their source in a header comment. The connection features of the same page — several addresses per computer, the SSH-config and Tailscale suggestions, the on/off switch, automatic placement and GitHub sharing (`jarvis/computers/ssh_config.py`, `tailscale.py`, `placement.py`, `github_access.py`; `views/computers/RouteList.tsx`, `Placement.tsx`, `GithubAccess.tsx`, `machineKind.tsx`) — adapt further portions of T3 Code @ `12069ee`, each credited the same way.

The Agentic IDE explorer's coloured file-type icons
(`jarvis/ui/web/frontend/src/components/agentic/sidePanel/explorer/fileIconSprite.ts`
and `fileIcon.ts`) contain SVG symbols and file-name rules copied from
[@pierre/trees](https://www.npmjs.com/package/@pierre/trees) 1.0.0-beta.6,
Apache License 2.0, Copyright 2025 Pierre Computer Company, full text in
[`third_party/pierre-trees/LICENSE`](../third_party/pierre-trees/LICENSE), plus a
colour table adapted from T3 Code @ `31f9d83` under the MIT license above.

A few well-separated portions of NousResearch/hermes-agent (MIT, Copyright (c)
2025 Nous Research) are adapted into the agent code. Each adapted block names
its upstream file and commit in a header comment; the license text and the list
of adapted files live in [`third_party/hermes-agent/`](../third_party/hermes-agent/).

## External distribution metadata

Package-manager listings and release descriptions should identify the license
of the artifact they actually distribute. A listing pinned to an older MIT
release retains that description until its artifact is updated. External
listings and repository social-preview settings are not updated by changing
this file; verify them separately when publishing a release.
