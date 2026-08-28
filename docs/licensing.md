# Licensing

## Current: Apache License 2.0

Personal Jarvis is licensed under the [Apache License 2.0](../LICENSE), with the
attribution notice in [`NOTICE`](../NOTICE). The switch was made on `main` on
2026-08-27 and ships with **version 2.0.0** — the first release under the new
terms. Everything merged from that day on is Apache 2.0.

Because a license change is a breaking change in SemVer terms, the next release
out of `main` is 2.0.0. There is no further 1.x release.

## Released 1.x stays MIT, permanently

Every release up to and including 1.6.0 was published under the MIT License and
remains MIT. That cannot be revoked: a copy obtained under MIT keeps those
rights forever, including the right to fork it. The text those releases shipped
with is still in the history — `git show v1.6.0:LICENSE`.

## Why Apache 2.0

Apache 2.0 keeps everything MIT gives you — use, modify, distribute, sell, no
copyleft, no source-sharing obligation — and closes three things MIT leaves
unsaid:

- **An explicit patent grant** (§3). Every contributor grants users a license to
  the patents their contribution needs, and that grant ends for anyone who sues
  over them. MIT says nothing about patents at all, which is the single most
  common reason a corporate legal review rejects an MIT dependency.
- **A trademark carve-out** (§6). The license covers the code and not the name
  or the logo, in writing. Ours already lives in
  [`TRADEMARK.md`](../TRADEMARK.md); Apache 2.0 makes it a license term.
- **Stated redistribution duties** (§4). Pass on the license, keep the notices,
  carry the `NOTICE` file, mark the files you changed. MIT only asks for the
  copyright line.

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

The bundled Silero VAD model (`jarvis/assets/vad/`) carries its own MIT license
from its own authors and keeps it — the switch does not touch it, and no
dependency's terms change either. Nothing is relicensed by being included here.

## Still to do at the 2.0 release

- [ ] `homebrew-tap/Formula/personal-jarvis-installer.rb` and
      `scoop-bucket/personal-jarvis-installer.json` — both are pinned to the
      v1.0.5 installer asset, which really is MIT. Change `license` in the same
      commit that bumps them to the 2.0 asset, not before.
- [ ] Repository social preview — `assets/brand/social-preview.png` is already
      re-rendered and reads "APACHE 2.0". GitHub does not take it from the
      repository: upload it under Settings -> General -> Social preview.
- [ ] The GitHub release notes for 2.0.0 — lead with the license change.
