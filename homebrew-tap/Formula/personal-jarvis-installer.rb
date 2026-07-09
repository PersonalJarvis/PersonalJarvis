class PersonalJarvisInstaller < Formula
  desc "Supply-chain hardened installer for Personal Jarvis (cosign + offline Ed25519 + SLSA L3 + in-toto)"
  homepage "https://github.com/PersonalJarvis/PersonalJarvis"
  license "MIT"

  # PINNED to the v1.0.5 release.
  #
  # When the next release lands, bump `url`, `version`, and `sha256` to
  # the new release. The url MUST point at a single release asset (not a
  # source tarball), because the installer is a single executable shell
  # script that we re-publish per release with cosign + offline + SLSA +
  # ML-DSA signatures alongside it. The Homebrew tap MUST never pull from
  # `master`/`HEAD` — that defeats the whole point of Wave 4+5 (the
  # package manager's signing chain is only meaningful if the pinned
  # artifact is immutable).
  #
  # SHA-256 SOURCE OF TRUTH: the pinned release's published checksums.txt:
  #   curl -fsSL https://github.com/PersonalJarvis/PersonalJarvis/releases/download/v1.0.5/checksums.txt \
  #     | awk '/install-verify\.sh$/ {print $1}'
  url "https://github.com/PersonalJarvis/PersonalJarvis/releases/download/v1.0.5/install-verify.sh"
  version "1.0.5"
  sha256 "a0dc47933dc1930288b177ebd63924bffd4b35521802c82841d838bcf67eeb09"

  def install
    # The downloaded file is a single executable shell script (not an
    # archive). Homebrew will have placed it in the cached path under its
    # `url`-derived basename; rename it to the canonical bin name during
    # install so the user invokes `personal-jarvis-installer`, not
    # `install-verify.sh`.
    bin.install "install-verify.sh" => "personal-jarvis-installer"
  end

  test do
    # The installer is a 12-stage verifier that fails closed; running it
    # bare would try to download cosign + the release bundle. For a `brew
    # test` smoke check we only assert that the script is present, is the
    # right verifier (not some random shell file), and has a recognisable
    # banner. This is the strongest meaningful test we can run without
    # network + signing material in the test sandbox.
    installer = "#{bin}/personal-jarvis-installer"
    assert_predicate Pathname.new(installer), :exist?
    assert_predicate Pathname.new(installer), :executable?
    contents = File.read(installer)
    assert_match "Personal Jarvis", contents
    assert_match "supply-chain", contents
    assert_match "EXPECTED_REPO=\"PersonalJarvis/PersonalJarvis\"", contents
  end
end
