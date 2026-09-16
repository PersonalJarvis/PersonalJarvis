"""The macOS app lives where Mac users look for apps (BUG-216).

A bundle kept only in ``~/Applications`` was installed and launchable, yet the
user could not see it among their apps: Finder's "Applications" item and
Launchpad show ``/Applications``. These tests pin that the installer prefers
``/Applications`` when the account may write there, moves an existing
per-user install over without touching its bytes (the signature, and with it
every TCC grant, survives a rename), and never loses the app on the way.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import jarvis.setup.macos_app_bundle as mab
from jarvis.setup.macos_app_bundle import (
    APP_DIR_NAME,
    ensure_macos_app_bundle,
    macos_applications_dir,
    remove_macos_app_bundle,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")


def _bundle(root: Path, marker: str = "signed-bytes") -> Path:
    bundle = root / APP_DIR_NAME
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "MacOS" / "PersonalJarvis").write_text(marker, encoding="utf-8")
    return bundle


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    system = tmp_path / "Applications"
    user = tmp_path / "home" / "Applications"
    system.mkdir()
    user.mkdir(parents=True)
    return system, user


@pytest.fixture
def read_only(roots: tuple[Path, Path]):
    system, _user = roots
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permission bits")
    system.chmod(0o555)
    yield system
    system.chmod(0o755)


def test_a_fresh_install_goes_to_the_system_applications_folder(roots) -> None:
    system, user = roots
    assert macos_applications_dir(system_dir=system, user_dir=user) == system


def test_a_standard_account_falls_back_to_its_own_applications_folder(roots, read_only) -> None:
    _system, user = roots
    assert macos_applications_dir(system_dir=read_only, user_dir=user) == user


def test_an_existing_install_is_found_where_it_is(roots, read_only) -> None:
    system, user = roots
    _bundle(user)
    assert macos_applications_dir(system_dir=system, user_dir=user) == user


def test_a_copy_in_the_system_folder_wins(roots) -> None:
    system, user = roots
    _bundle(user)
    _bundle(system)
    assert macos_applications_dir(system_dir=system, user_dir=user) == system


def test_a_per_user_install_is_moved_byte_for_byte(roots) -> None:
    system, user = roots
    _bundle(user, marker="exact-signature")

    moved = mab._promote_to_system_applications(system_dir=system, user_dir=user)

    assert moved == system / APP_DIR_NAME
    assert not (user / APP_DIR_NAME).exists()
    executable = moved / "Contents" / "MacOS" / "PersonalJarvis"
    assert executable.read_text(encoding="utf-8") == "exact-signature"


def test_an_existing_system_copy_is_never_overwritten(roots) -> None:
    system, user = roots
    _bundle(user, marker="user")
    _bundle(system, marker="system")

    assert mab._promote_to_system_applications(system_dir=system, user_dir=user) is None
    assert (user / APP_DIR_NAME).is_dir()
    assert (system / APP_DIR_NAME / "Contents" / "MacOS" / "PersonalJarvis").read_text(
        encoding="utf-8"
    ) == "system"


def test_no_move_without_write_access(roots, read_only) -> None:
    _system, user = roots
    _bundle(user)

    assert mab._promote_to_system_applications(system_dir=read_only, user_dir=user) is None
    assert (user / APP_DIR_NAME).is_dir()


def test_a_failed_move_keeps_the_working_app(roots, monkeypatch: pytest.MonkeyPatch) -> None:
    system, user = roots
    _bundle(user)

    def _refuse(self, target):
        raise OSError("Cross-device link")

    monkeypatch.setattr(Path, "rename", _refuse)

    assert mab._promote_to_system_applications(system_dir=system, user_dir=user) is None
    assert (user / APP_DIR_NAME).is_dir()


def test_nothing_to_move_is_a_no_op(roots) -> None:
    system, user = roots
    assert mab._promote_to_system_applications(system_dir=system, user_dir=user) is None


def test_uninstall_clears_both_locations(roots, monkeypatch: pytest.MonkeyPatch) -> None:
    system, user = roots
    _bundle(system)
    _bundle(user)
    monkeypatch.setattr(mab.sys, "platform", "darwin")
    monkeypatch.setattr(mab, "SYSTEM_APPLICATIONS_DIR", system)
    monkeypatch.setattr(mab, "user_applications_dir", lambda: user)

    assert remove_macos_app_bundle() is True
    assert not (system / APP_DIR_NAME).exists()
    assert not (user / APP_DIR_NAME).exists()


def test_the_running_app_is_followed_to_its_new_location(
    roots, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The process keeps the path it launched from after the move; the repair
    must hand back — and register — where the app is now."""
    system, user = roots
    _bundle(user)
    launched_from = user / APP_DIR_NAME
    monkeypatch.setattr(mab.sys, "platform", "darwin")
    monkeypatch.setattr(mab, "SYSTEM_APPLICATIONS_DIR", system)
    monkeypatch.setattr(mab, "user_applications_dir", lambda: user)
    monkeypatch.setattr(mab, "ensure_local_signing_identity", lambda **_kw: None)
    monkeypatch.setattr(
        mab, "_running_managed_bundle", lambda *, install_root, **_kw: launched_from
    )
    monkeypatch.setattr(
        mab,
        "_install_native_bundle",
        lambda *_a, **_kw: pytest.fail("a moved running app must never be rebuilt"),
    )
    registered: list[Path] = []
    monkeypatch.setattr(mab, "register_with_launch_services", registered.append)

    result = ensure_macos_app_bundle(install_dir=tmp_path / "install")

    assert result == system / APP_DIR_NAME
    assert registered == [system / APP_DIR_NAME]
    assert not launched_from.exists()
