# Licensing

## Today: MIT

Every release up to and including the 1.x line is licensed under the
[MIT License](../LICENSE). That does not change retroactively, and it cannot:
a copy obtained under MIT stays MIT forever, including the right to fork it.

## From version 2.0: Apache License 2.0

Starting with **2.0.0**, Personal Jarvis is licensed under the
[Apache License 2.0](../LICENSE-APACHE-2.0). The full text already sits in the
repository so the terms can be read before they apply — it is **not in force
yet**. The file that governs the code you have today is still `LICENSE`.

### Why

Apache 2.0 keeps everything MIT gives you — use, modify, distribute, sell, no
copyleft, no source-sharing obligation — and closes three things MIT leaves
unsaid:

- **An explicit patent grant** (§3). Every contributor grants users a license
  to the patents their contribution needs, and that grant ends for anyone who
  sues over them. MIT says nothing about patents at all, which is the single
  most common reason a corporate legal review rejects an MIT dependency.
- **A trademark carve-out** (§6). The license covers the code and not the name
  or the logo, in writing. Ours already lives in
  [`TRADEMARK.md`](../TRADEMARK.md); Apache 2.0 makes it a license term.
- **Stated redistribution duties** (§4). Pass on the license, keep the notices,
  mark the files you changed. MIT only asks for the copyright line.

### What it means for you

- **Users:** nothing to do. The freedoms are the same, in both directions in
  time.
- **Forks and redistributors:** anything you took under 1.x stays under MIT.
  From 2.0 on, keep `LICENSE` and any `NOTICE` file with your copies and mark
  the files you modified.
- **Contributors:** by opening a pull request you agree your work is licensed
  to the project under the MIT License for the 1.x line and under the Apache
  License 2.0 from 2.0 on. This is stated up front so the switch needs no
  chase-down of past authors later.

### Third-party components

The bundled Silero VAD model (`jarvis/assets/vad/`) carries its own MIT license
from its own authors and keeps it — the switch does not touch it, and no
dependency's terms change either.

## Checklist for the 2.0 release

Every place the license is named today, so nothing is left claiming "MIT" after
the switch:

- [ ] `LICENSE` — replace the MIT text with the contents of
      `LICENSE-APACHE-2.0`, then delete `LICENSE-APACHE-2.0`
- [ ] `NOTICE` — add one (optional under §4d, but the conventional place for
      the attribution line downstream copies must carry)
- [ ] `pyproject.toml` — `license = "Apache-2.0"`
- [ ] `README.md` — the badge, the "what it costs" paragraph, the License
      section, the footer line
- [ ] `CONTRIBUTING.md` — the badge and the contribution paragraph
- [ ] `.github/PULL_REQUEST_TEMPLATE.md` — the contribution note
- [ ] `jarvis.spec` — the `LegalCopyright` string in the Windows version
      resource
- [ ] `homebrew-tap/Formula/personal-jarvis-installer.rb` — `license "Apache-2.0"`
- [ ] `scoop-bucket/personal-jarvis-installer.json` — `"license": "Apache-2.0"`
- [ ] `CHANGELOG.md` — record it under 2.0.0
- [ ] this file — drop the "from 2.0" framing and state Apache 2.0 as current
