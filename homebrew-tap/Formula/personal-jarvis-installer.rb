class PersonalJarvisInstaller < Formula
  desc "Supply-chain hardened installer for Personal Jarvis (cosign + offline Ed25519 + SLSA L3 + in-toto)"
  homepage "https://github.com/personal-jarvis/PersonalJarvis"
  license "MIT"

  # PINNED to the v0.5.1-supplychain-wave5-audit-fixes release.
  #
  # Wave-5 audit fixes (this bump): tag-binding, payload-commit pin,
  # content-anchor rename, dependabot. See
  # docs/supply-chain/wave5-audit-fixes-validation.md.
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
  # SHA-256 SOURCE OF TRUTH: read it from the published checksums.txt
  # AFTER the v0.5.1 release pipeline completes:
  #   curl -fsSL https://github.com/personal-jarvis/PersonalJarvis/releases/download/v0.5.1-supplychain-wave5-audit-fixes/checksums.txt \
  #     | awk '/install-verify\.sh$/ {print $1}'
  # That value MUST be pasted into `sha256` below before the tap is
  # committed to homebrew-jarvis. Until the release pipeline produces
  # checksums.txt the sha256 below is a placeholder zero-string and the
  # tap will refuse to install (Homebrew rejects all-zero sha256).
  url "https://github.com/personal-jarvis/PersonalJarvis/releases/download/v0.5.1-supplychain-wave5-audit-fixes/install-verify.sh"
  version "0.5.1-supplychain-wave5-audit-fixes"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"

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
    assert_match "EXPECTED_REPO=\"personal-jarvis/PersonalJarvis\"", contents
  end
end
