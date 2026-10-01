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
* a certificate without its password is refused before the long freeze;
* the REAL import branch (not just its DRY_RUN printout) runs the right
  ``security`` calls in the right order, reads the identity back from the
  certificate, and always restores the keychain search list and deletes the
  temporary keychain. That branch is cut out of the script and run against a
  stand-in ``security`` command, because a runner's keychain cannot be used on
  every host these tests run on. On a Mac runner the same test runs under the
  shell macOS ships (bash 3.2).
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
        "security list-keychains -d user -s",
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


# --- The real import branch, against a stand-in `security` command -----------

_FAKE_SECURITY = """#!/usr/bin/env bash
# Stand-in for macOS `security`: log every call, answer the two queries.
printf '%s\\n' "$*" >> "${FAKE_SECURITY_LOG}"
case "$1" in
  list-keychains)
    case " $* " in
      *" -s "*) ;;
      *) printf '    "/Users/runner/Library/Keychains/login.keychain-db"\\n'
         printf '    "/Library/Keychains/System.keychain"\\n' ;;
    esac ;;
  find-identity) cat "${FAKE_IDENTITIES_FILE}" ;;
  import) [ -z "${FAKE_IMPORT_FAILS:-}" ] || exit 1 ;;
esac
exit 0
"""

_FAKE_UUIDGEN = """#!/usr/bin/env bash
echo "11111111-2222-3333-4444-555555555555"
"""

_DEVELOPER_ID_IDENTITIES = (
    "  1) 0123456789ABCDEF0123456789ABCDEF01234567 "
    '"Developer ID Application: Example GmbH (ABCDE12345)"\n'
    "     1 valid identities found\n"
)
_APPLE_DEVELOPMENT_IDENTITIES = (
    '  1) 0123456789ABCDEF0123456789ABCDEF01234567 "Apple Development: Someone (ZZZZZ99999)"\n'
    "     1 valid identities found\n"
)


def _import_section() -> str:
    """The certificate-import part of build.sh, cut out by its own markers."""
    script = BUILD_SH.read_text(encoding="utf-8")
    start_marker = 'SIGNING_KEYCHAIN=""'
    end_marker = "import_signing_certificate\n\n# --- 1. Web bundle"
    assert start_marker in script and end_marker in script, (
        "build.sh was restructured: update the markers that cut out the certificate import"
    )
    return script[script.index(start_marker) : script.index(end_marker)]


@pytest.fixture
def stand_in(tmp_path: Path):
    """A PATH whose `security` and `uuidgen` are logging stand-ins."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("security", _FAKE_SECURITY), ("uuidgen", _FAKE_UUIDGEN)):
        tool = bin_dir / name
        tool.write_text(body, encoding="utf-8")
        tool.chmod(0o755)
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "DRY_RUN=0\n"
        "log() { printf '[macos-build] %s\\n' \"$*\"; }\n"
        "die() { printf '[macos-build] ERROR: %s\\n' \"$*\" >&2; exit 1; }\n"
        + _import_section()
        + '\nimport_signing_certificate\necho "IDENTITY=${APPLE_SIGNING_IDENTITY:-}"\n',
        encoding="utf-8",
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return bin_dir, harness, scratch


def _run_import(stand_in, tmp_path: Path, identities: str, **env: str):
    bin_dir, harness, scratch = stand_in
    log_file = tmp_path / "security.log"
    log_file.write_text("", encoding="utf-8")
    identities_file = tmp_path / "identities.txt"
    identities_file.write_text(identities, encoding="utf-8")
    clean = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
        "RUNNER_TEMP": str(scratch),
        "FAKE_SECURITY_LOG": str(log_file),
        "FAKE_IDENTITIES_FILE": str(identities_file),
        "APPLE_CERTIFICATE_P12_BASE64": "VE9QU0VDUkVULVAxMi1QQVlMT0FE",  # TOPSECRET-P12-PAYLOAD
        "APPLE_CERTIFICATE_PASSWORD": "TOPSECRET-P12-PASSWORD",
    }
    clean.update(env)
    result = subprocess.run(
        ["bash", str(harness)],
        capture_output=True,
        text=True,
        timeout=60,
        env=clean,
        check=False,
    )
    calls = [line for line in log_file.read_text(encoding="utf-8").splitlines() if line]
    return result, calls


def test_the_real_import_runs_the_right_calls_in_order_and_cleans_up(
    stand_in, tmp_path: Path
) -> None:
    result, calls = _run_import(stand_in, tmp_path, _DEVELOPER_ID_IDENTITIES)

    assert result.returncode == 0, result.stderr
    assert [call.split()[0] for call in calls] == [
        "list-keychains",  # remember the search list BEFORE creating anything
        "delete-keychain",  # a leftover of an interrupted earlier run
        "create-keychain",
        "set-keychain-settings",
        "unlock-keychain",
        "import",
        "set-key-partition-list",
        "list-keychains",  # put ours first
        "find-identity",
        "list-keychains",  # on exit: restore the search list ...
        "delete-keychain",  # ... and drop the temporary keychain
    ]
    keychain = str(tmp_path / "scratch" / "jarvis-signing.keychain-db")
    originals = [
        "/Users/runner/Library/Keychains/login.keychain-db",
        "/Library/Keychains/System.keychain",
    ]
    assert calls[7] == f"list-keychains -d user -s {keychain} {' '.join(originals)}"
    assert calls[9] == f"list-keychains -d user -s {' '.join(originals)}"
    assert calls[-1] == f"delete-keychain {keychain}"
    assert "-T /usr/bin/codesign" in calls[5]


def test_the_real_import_reads_the_identity_back_from_the_certificate(
    stand_in, tmp_path: Path
) -> None:
    result, _calls = _run_import(stand_in, tmp_path, _DEVELOPER_ID_IDENTITIES)

    assert result.returncode == 0, result.stderr
    assert "IDENTITY=Developer ID Application: Example GmbH (ABCDE12345)" in result.stdout


def test_a_given_identity_is_not_looked_up_again(stand_in, tmp_path: Path) -> None:
    result, calls = _run_import(
        stand_in,
        tmp_path,
        _APPLE_DEVELOPMENT_IDENTITIES,
        APPLE_SIGNING_IDENTITY="Developer ID Application: Given (QQQQQ11111)",
    )

    assert result.returncode == 0, result.stderr
    assert "IDENTITY=Developer ID Application: Given (QQQQQ11111)" in result.stdout
    assert "find-identity" not in [call.split()[0] for call in calls]


def test_a_certificate_without_a_developer_id_identity_fails_and_still_cleans_up(
    stand_in, tmp_path: Path
) -> None:
    result, calls = _run_import(stand_in, tmp_path, _APPLE_DEVELOPMENT_IDENTITIES)

    assert result.returncode != 0
    assert "Developer ID Application" in result.stderr
    # The trap still ran: search list restored, temporary keychain dropped.
    assert calls[-2].startswith("list-keychains -d user -s /Users/runner/")
    assert calls[-1].startswith("delete-keychain ")


def test_the_real_import_never_prints_a_secret_and_leaves_no_certificate_behind(
    stand_in, tmp_path: Path
) -> None:
    result, _calls = _run_import(stand_in, tmp_path, _DEVELOPER_ID_IDENTITIES)

    assert result.returncode == 0, result.stderr
    for secret in ("TOPSECRET-P12-PAYLOAD", "TOPSECRET-P12-PASSWORD"):
        assert secret not in result.stdout + result.stderr
    assert list((tmp_path / "scratch").iterdir()) == []


def test_a_failed_import_leaves_neither_the_certificate_nor_the_keychain_behind(
    stand_in, tmp_path: Path
) -> None:
    """A wrong password aborts at `security import`; the trap must still clean up.

    On a runner the machine is thrown away; on a maintainer's Mac it is not, and
    the decoded certificate is the private key.
    """
    result, calls = _run_import(stand_in, tmp_path, _DEVELOPER_ID_IDENTITIES, FAKE_IMPORT_FAILS="1")

    assert result.returncode != 0
    assert list((tmp_path / "scratch").iterdir()) == []
    assert calls[-2].startswith("list-keychains -d user -s /Users/runner/")
    assert calls[-1].startswith("delete-keychain ")
    assert "find-identity" not in [call.split()[0] for call in calls]
