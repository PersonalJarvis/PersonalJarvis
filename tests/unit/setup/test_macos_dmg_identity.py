"""The downloadable .dmg app is its own installed app, with its own identity.

``jarvis.spec`` (PyInstaller) cannot import ``jarvis.core.branding``, so it
carries the .dmg bundle id as a literal. The permission port accepts that id as
an installed identity; if the two ever drift apart, every macOS feature of the
.dmg app fails closed again — with every permission granted and no button left
to fix it. These tests pin the literal, and the lookup that finds the .dmg app
for tools that have to bring it to the front.
"""

from __future__ import annotations

import plistlib
import re
from pathlib import Path

import pytest

import jarvis.setup.macos_app_bundle as mab
from jarvis.core.branding import MACOS_BUNDLE_ID, MACOS_DMG_BUNDLE_ID
from jarvis.platform.permissions import ACCEPTED_BUNDLE_IDS
from jarvis.setup.macos_app_bundle import APP_DIR_NAME, installed_macos_app_bundle_path

REPO_ROOT = Path(__file__).resolve().parents[3]
PYINSTALLER_SPEC = REPO_ROOT / "jarvis.spec"


def test_the_spec_names_the_bundle_id_the_app_accepts_as_its_own() -> None:
    spec = PYINSTALLER_SPEC.read_text(encoding="utf-8")

    match = re.search(r'^MACOS_BUNDLE_IDENTIFIER\s*=\s*"([^"]+)"', spec, re.MULTILINE)

    assert match is not None, "jarvis.spec must declare MACOS_BUNDLE_IDENTIFIER"
    assert match.group(1) == MACOS_DMG_BUNDLE_ID
    # The Info.plist and the BUNDLE call must both use that one literal.
    assert '"CFBundleIdentifier": MACOS_BUNDLE_IDENTIFIER' in spec
    assert "bundle_identifier=MACOS_BUNDLE_IDENTIFIER" in spec


def test_the_two_installed_identities_stay_distinct() -> None:
    """The managed installer tells the .dmg app apart by id and must keep doing so."""
    assert MACOS_DMG_BUNDLE_ID != MACOS_BUNDLE_ID
    assert ACCEPTED_BUNDLE_IDS == (MACOS_BUNDLE_ID, MACOS_DMG_BUNDLE_ID)


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    system = tmp_path / "Applications"
    user = tmp_path / "home" / "Applications"
    system.mkdir()
    user.mkdir(parents=True)
    monkeypatch.setattr(mab, "SYSTEM_APPLICATIONS_DIR", system)
    monkeypatch.setattr(mab, "user_applications_dir", lambda: user)
    return system, user


def _app(root: Path, bundle_id: str) -> Path:
    bundle = root / APP_DIR_NAME
    (bundle / "Contents").mkdir(parents=True)
    with (bundle / "Contents" / "Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleIdentifier": bundle_id}, stream)
    return bundle


def test_a_mac_with_only_the_downloaded_app_finds_it(roots) -> None:
    system, _user = roots
    dmg_app = _app(system, MACOS_DMG_BUNDLE_ID)

    assert installed_macos_app_bundle_path() == dmg_app


def test_the_managed_install_wins_when_both_are_present(roots) -> None:
    system, user = roots
    _app(system, MACOS_DMG_BUNDLE_ID)
    managed = _app(user, MACOS_BUNDLE_ID)

    assert installed_macos_app_bundle_path() == managed


def test_an_unrelated_app_with_our_name_is_never_returned(roots) -> None:
    system, user = roots
    _app(system, "org.example.imposter")

    path = installed_macos_app_bundle_path()

    assert path != system / APP_DIR_NAME
    assert path == user / APP_DIR_NAME
    assert not path.exists()


def test_without_any_install_the_managed_location_is_reported(roots) -> None:
    system, user = roots

    path = installed_macos_app_bundle_path()

    assert path.name == APP_DIR_NAME
    assert path.parent in {system, user}
    assert not path.exists()
