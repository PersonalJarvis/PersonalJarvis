"""``packaging/macos/build.sh`` — the rehearsed (``DRY_RUN=1``) signing flow.

A GitHub runner starts with an empty keychain, so ``codesign --sign "Developer
ID Application: ..."`` can only work once the certificate secrets have been
imported. The workflow passed ``APPLE_CERTIFICATE_P12_BASE64`` to a script that
never read it, which is why every public .dmg was ad-hoc signed and
unnotarized: macOS then refuses the first launch and forgets every privacy
grant on every update. These tests rehearse the script on any host (DRY_RUN
prints each command instead of running it) in a throw-away copy of the layout,
and pin the three things that must stay true:

* without secrets the script behaves exactly as before (ad-hoc, no keychain);
* with the certificate secrets it imports them and signs, and no secret ever
  reaches the output;
* a certificate without its password is refused before the long freeze.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BUILD_SH = REPO_ROOT / "packaging" / "macos" / "build.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="a POSIX shell is needed to rehearse the macOS build script",
)

# Strings that must never appear in the rehearsal output.
_SECRETS = {
    "APPLE_CERTIFICATE_P12_BASE64": "TOPSECRET-P12-PAYLOAD",
    "APPLE_CERTIFICATE_PASSWORD": "TOPSECRET-P12-PASSWORD",
    "APPLE_APP_SPECIFIC_PASSWORD": "TOPSECRET-APP-PASSWORD",
}


@pytest.fixture
def layout(tmp_path: Path) -> Path:
    """A minimal copy of the repo layout the script resolves its paths from."""
    root = tmp_path / "repo"
    (root / "packaging" / "macos").mkdir(parents=True)
    shutil.copy2(BUILD_SH, root / "packaging" / "macos" / "build.sh")
    shutil.copy2(
        REPO_ROOT / "packaging" / "macos" / "entitlements.plist",
        root / "packaging" / "macos" / "entitlements.plist",
    )
    (root / "jarvis" / "ui" / "web" / "dist").mkdir(parents=True)
    (root / "jarvis" / "ui" / "web" / "dist" / "index.html").write_text("<html></html>")
    (root / "jarvis.toml.example").write_text("# defaults\n", encoding="utf-8")
    (root / "jarvis.toml").write_text("# defaults\n", encoding="utf-8")
    (root / "assets" / "icons").mkdir(parents=True)
    (root / "assets" / "icons" / "jarvis.icns").write_bytes(b"icns")
    return root


def _rehearse(layout: Path, **env: str) -> subprocess.CompletedProcess[str]:
    clean = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(layout),
        "DRY_RUN": "1",
        "SKIP_FRONTEND": "1",
        "PYTHON": sys.executable,
    }
    clean.update(env)
    return subprocess.run(
        ["bash", str(layout / "packaging" / "macos" / "build.sh")],
        capture_output=True,
        text=True,
        timeout=120,
        env=clean,
        check=False,
    )


def test_without_secrets_the_build_is_ad_hoc_and_touches_no_keychain(layout: Path) -> None:
    result = _rehearse(layout)

    assert result.returncode == 0, result.stderr
    assert "ad-hoc signing this build" in result.stdout
    assert "codesign --force --deep -s -" in result.stdout
    assert "keychain" not in result.stdout.lower()
    assert "notarytool" not in result.stdout


def test_the_certificate_secrets_are_imported_and_the_app_is_signed_with_it(
    layout: Path,
) -> None:
    result = _rehearse(
        layout,
        APPLE_ID="maintainer@example.com",
        APPLE_TEAM_ID="ABCDE12345",
        **_SECRETS,
    )

    assert result.returncode == 0, result.stderr
    out = result.stdout
    # The identity does not have to be given: it comes out of the certificate.
    assert "signing with Developer ID identity" in out
    assert "--options runtime" in out
    # Import, key access for codesign, and the search list — in that order.
    markers = [
        "security create-keychain",
        "security import",
        "security set-key-partition-list",
        "security list-keychains",
        "codesign --force --deep --timestamp --options runtime",
    ]
    positions = [out.index(marker) for marker in markers]
    assert positions == sorted(positions)
    assert "notarytool submit" in out
    assert "ad-hoc" not in out


def test_no_secret_ever_reaches_the_output(layout: Path) -> None:
    result = _rehearse(
        layout,
        APPLE_ID="maintainer@example.com",
        APPLE_TEAM_ID="ABCDE12345",
        **_SECRETS,
    )

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    for secret in _SECRETS.values():
        assert secret not in combined
    assert "<redacted>" in combined


def test_an_identity_that_is_given_wins_over_reading_one_from_the_certificate(
    layout: Path,
) -> None:
    result = _rehearse(
        layout,
        APPLE_SIGNING_IDENTITY="Developer ID Application: Example (ABCDE12345)",
        **_SECRETS,
    )

    assert result.returncode == 0, result.stderr
    assert "Developer ID Application: Example (ABCDE12345)" in result.stdout


def test_a_certificate_without_its_password_is_refused_before_the_freeze(layout: Path) -> None:
    result = _rehearse(layout, APPLE_CERTIFICATE_P12_BASE64="TOPSECRET-P12-PAYLOAD")

    assert result.returncode != 0
    assert "APPLE_CERTIFICATE_PASSWORD" in result.stderr
    assert "TOPSECRET-P12-PAYLOAD" not in result.stdout + result.stderr
    # Refused up front: the long PyInstaller step never started.
    assert "running PyInstaller" not in result.stdout


def test_an_identity_alone_keeps_working_on_a_mac_that_already_holds_it(layout: Path) -> None:
    """A maintainer's own Mac has the identity in its login keychain: no import."""
    result = _rehearse(
        layout,
        APPLE_SIGNING_IDENTITY="Developer ID Application: Example (ABCDE12345)",
    )

    assert result.returncode == 0, result.stderr
    assert "security create-keychain" not in result.stdout
    assert "signing with Developer ID identity" in result.stdout
