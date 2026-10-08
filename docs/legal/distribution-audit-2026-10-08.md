# Distribution evidence: v2.9.0

Reviewed on 2026-10-08. This records inspection of published artifacts, not a
legal clearance or a statement about every platform. The accompanying code
changes affect future builds; they do not replace published release assets.

## Artifact identity and method

The [published v2.9.0 release](https://github.com/PersonalJarvis/PersonalJarvis/releases/tag/v2.9.0)
was published on 2026-10-05. Its `payload-commit.txt` and annotated tag identify
commit `722b4c46af37c04e55b6053bc025cd8bd2d3b79d`.

| Asset | Size in bytes | SHA-256 |
| --- | ---: | --- |
| `PersonalJarvis-Setup-x64.exe` | 247298481 | `09349f91c33a3ad92738be452940ad0fc6af97c7f04bf35dbe0f23b2b0ed6b07` |
| `personal-jarvis_2.9.0_amd64.deb` | 285597858 | `ca0fa3ee2b902bc4b4872247e65c49c30a0db0cc6e132af1efe9aab0841ed70f` |

Both hashes matched the release's `installers-SHA256SUMS.txt`. The Windows Inno
Setup archive was listed and extracted with an independent extractor. The
Linux Debian archive and its data tar were listed, with selected files
extracted. No installer, application, embedded Python module, or native payload
was executed. Version strings were read by statically parsing the embedded
PyInstaller CArchive/PYZ data.

Windows contained 9,383 payload files (809,880,353 extracted bytes). The Linux
data tar contained 7,771 file entries. Both distribute a frozen application,
rather than only a bootstrap that asks the user to install dependencies later.

## Confirmed components

The embedded Python archives identify the same versions on both platforms:

| Component | Version | Additional evidence |
| --- | --- | --- |
| PyAV | 17.1.0 | FFmpeg libraries, x264, and x265 occur in the native payload |
| sherpa-onnx | 1.13.8 | Native extension contains `espeak_TextToPhonemes`, `espeak_SetVoiceByName`, and `espeak_Initialize` |
| AsyncSSH | 2.24.1 | Version recovered from `asyncssh.version` |

Selected binary fingerprints:

| Binary | SHA-256 |
| --- | --- |
| Windows FFmpeg `avcodec` | `518e7467c753c24a0fa639eeb46291ccdc4d7d22acbc7e77ecbf83f1313b096d` |
| Windows sherpa native extension | `c7cbe8071492dde37f7c9d640fd882ff52f62073c5214741c74178502aa6c593` |
| Linux sherpa native extension | `eb9c3545b310ecd97ba0a2ff53fe39f8d8fb9efaea2dea0ca333ae9050bf2cd2` |

The FFmpeg binaries label themselves LGPL version 3 or later; the Windows
build configuration includes `--enable-libx264` and `--enable-libx265`. These
strings identify composition and upstream labeling, not the complete set of
permissions for every linked codec.

The [sherpa 1.13.8 build configuration](https://github.com/k2-fsa/sherpa-onnx/blob/v1.13.8/cmake/piper-phonemize.cmake)
incorporates Piper phonemization with static compilation. The
[upstream dependency discussion](https://github.com/k2-fsa/sherpa-onnx/issues/3731)
identifies its GPL eSpeak dependency. This requires assessment of the complete
native distribution; sherpa's top-level Apache license alone is insufficient.

## Missing material in the inspected packages

- The project's root `LICENSE` and `NOTICE` files were absent in both complete
  manifests. The Windows `personal_jarvis.egg-info/PKG-INFO` declares them as
  `License-File` entries, but the corresponding files are not present. Some
  other components carry Apache license text; the finding is the missing
  project files and their attribution chain.
- Neither manifest contains license files for PyAV, sherpa, eSpeak, FFmpeg,
  x264, x265, or AsyncSSH. No complete GNU GPL or LGPL text was found in the
  inspected notice files. Windows has 28 identified license/notice files and
  Linux has 24.
- The released frontend's `THIRD_PARTY_NOTICES.txt` contains Material Icon
  Theme, font summaries with a URL, and Hermes. Complete bundled-package
  notices and the complete OFL text are missing from this file.
- The inspected packages contain no corresponding dependency source trees.
  No corresponding-source offer or applicable commercial-exception statement
  was found in the Windows readable payload. The separately released project
  source archive was not audited for corresponding third-party build sources.

## Remediation and remaining verification

The packaging corrections include project and vendored license texts explicitly
instead of relying on editable-install metadata. Frontend notices are generated
from emitted production modules, worker modules, font assets, and copied
materials. The onboarding terms are packaged in both install layouts.

For the next release, inspect the built artifacts again. Retaining a license
text does not satisfy every source, linking, modification, or notice obligation.
Resolve the native components' terms against their exact versions and build
options, and provide the corresponding sources/build materials or other
deliverables required by the selected route.

The [PyAV wheel discussion](https://github.com/PyAV-Org/PyAV/issues/2270)
distinguishes its BSD source license from binary-wheel terms and describes
commercial exceptions. The applicable downstream exception documents were not
established by this audit. Preserve those grants if relying on them; otherwise
select and fulfill a documented license route. See
[FFmpeg's guidance](https://ffmpeg.org/legal.html).

This report does not conclude that the entire application must be relicensed.
Apache-2.0 and GPLv3 can be combined under the applicable GPLv3 conditions; see
the [Apache Software Foundation's explanation](https://www.apache.org/licenses/GPL-compatibility.html).
AsyncSSH's EPL terms also require their own redistribution review.

The macOS disk images, Linux AppImage, model downloads, private license grants,
and codec patent questions remain outside this artifact inspection. Changes to
project branding or user-selected assistant names are outside this remediation.
